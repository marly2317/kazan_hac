"""Streaming parser for the КТРУ XML dumps.

The dumps are ~10 MB each and there are ~180 of them, so the whole set does not fit in memory
as a tree. ``iterparse`` plus clearing each element after use keeps the footprint flat however
many shards are fed in.

Only ``nsiKTRUNew_all_actual_*.xml`` is parsed; the organisers' own note says the
``not-actual`` dumps are to be ignored, and positions whose status is not ``ACTIVE`` are
skipped — matching a user onto a withdrawn code would hand them one they cannot use.
"""

from __future__ import annotations

import logging
import zipfile
from collections.abc import Iterator
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET

from apps.ktru.domain.entities import Characteristic, PermittedValue, Position, Unit
from apps.ktru.domain.enums import ACTIVE_STATUS, CharacteristicType, ValueKind

logger = logging.getLogger(__name__)

NS = {"o": "http://zakupki.gov.ru/oos/types/1"}

#: Only these files inside a shard archive carry current data.
ACTUAL_FILE_MARKER = "_all_actual_"

#: `greater`/`less` mean the bound itself is excluded; the `orEqual` forms include it. Getting
#: this backwards would accept a value that КТРУ rejects, which is the error the task scores.
_EXCLUSIVE = {"greater", "less"}


def _decimal(raw: str | None) -> Decimal | None:
    if raw is None or not raw.strip():
        return None
    try:
        return Decimal(raw.strip())
    except InvalidOperation:
        logger.debug("Unparsable number in КТРУ dump: %r", raw)
        return None


def _unit(element: ET.Element | None) -> Unit | None:
    if element is None:
        return None
    okei = element.find("o:OKEI", NS)
    if okei is None:
        return None
    code = (okei.findtext("o:code", "", NS) or "").strip()
    name = (okei.findtext("o:name", "", NS) or "").strip()
    return Unit(code=code, name=name) if name else None


def _permitted_values(characteristic: ET.Element) -> list[PermittedValue]:
    values: list[PermittedValue] = []
    container = characteristic.find("o:values", NS)
    if container is None:
        return values

    for value in container.findall("o:value", NS):
        unit = _unit(value)

        text = (value.findtext("o:qualityDescription", "", NS) or "").strip()
        if text:
            values.append(PermittedValue(kind=ValueKind.TEXT, text=text, unit=unit))
            continue

        exact = _decimal(value.findtext("o:valueSet/o:concreteValue", None, NS))
        if exact is not None:
            values.append(PermittedValue(kind=ValueKind.EXACT, exact=exact, unit=unit))
            continue

        for value_range in value.findall("o:rangeSet/o:valueRange", NS):
            minimum = _decimal(value_range.findtext("o:min", None, NS))
            maximum = _decimal(value_range.findtext("o:max", None, NS))
            if minimum is None and maximum is None:
                continue
            values.append(
                PermittedValue(
                    kind=ValueKind.RANGE,
                    minimum=minimum,
                    maximum=maximum,
                    min_inclusive=(value_range.findtext("o:minMathNotation", "", NS))
                    not in _EXCLUSIVE,
                    max_inclusive=(value_range.findtext("o:maxMathNotation", "", NS))
                    not in _EXCLUSIVE,
                    unit=unit,
                )
            )
    return values


def _characteristics(data: ET.Element) -> list[Characteristic]:
    container = data.find("o:characteristics", NS)
    if container is None:
        return []

    result: list[Characteristic] = []
    for element in container.findall("o:characteristic", NS):
        # A characteristic flagged `actual=false` is history the position no longer uses.
        if (element.findtext("o:actual", "true", NS) or "").strip().lower() == "false":
            continue
        name = (element.findtext("o:name", "", NS) or "").strip()
        raw_type = (element.findtext("o:type", "", NS) or "").strip()
        if not name or raw_type not in {t.value for t in CharacteristicType}:
            continue
        result.append(
            Characteristic(
                code=(element.findtext("o:code", "", NS) or "").strip(),
                name=name,
                type=CharacteristicType(raw_type),
                is_required=(element.findtext("o:isRequired", "false", NS) or "").strip().lower()
                == "true",
                values=_permitted_values(element),
            )
        )
    return result


def _position_from(element: ET.Element) -> Position | None:
    """Build a position from one ``<oos:position>``, or ``None`` if it should be skipped."""
    data = element.find("o:data", NS)
    if data is None:
        return None
    if (data.findtext("o:status", "", NS) or "").strip() != ACTIVE_STATUS:
        return None

    code = (data.findtext("o:code", "", NS) or "").strip()
    name = (data.findtext("o:name", "", NS) or "").strip()
    if not code or not name:
        return None

    okpd2 = data.find("o:OKPD2", NS)
    return Position(
        code=code,
        name=name,
        okpd2_code=(okpd2.findtext("o:code", "", NS) or "").strip() if okpd2 is not None else "",
        okpd2_name=(okpd2.findtext("o:name", "", NS) or "").strip() if okpd2 is not None else "",
        characteristics=_characteristics(data),
    )


def _stream(source) -> Iterator[Position]:
    """Walk one XML stream, yielding active positions and freeing each element after use.

    The ``element.clear()`` is not an optimisation: without it ``iterparse`` retains every
    position it has seen and the process grows to the size of the whole dump.
    """
    for _event, element in ET.iterparse(source, events=("end",)):
        if not element.tag.endswith("}position"):
            continue
        position = _position_from(element)
        if position is not None:
            yield position
        element.clear()


def parse_positions(source: Path | str) -> Iterator[Position]:
    """Yield every active position in one XML file."""
    yield from _stream(str(source))


def parse_archive(archive: Path) -> Iterator[Position]:
    """Yield the active positions from every ``_all_actual_`` member of a shard archive.

    Read straight out of the zip: expanding ~180 shards to disk first would cost gigabytes of
    temporary space for data that is read once.
    """
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.namelist():
            if not member.endswith(".xml") or ACTUAL_FILE_MARKER not in member:
                continue
            with bundle.open(member) as stream:
                yield from _stream(stream)
