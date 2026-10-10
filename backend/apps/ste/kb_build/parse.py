"""Потоковый разбор выгрузки КТРУ (архитектура §3.1).

Читает zip-архивы напрямую, не распаковывая: в каждом архиве один XML на 10–200 МБ. Берутся
только файлы ``*_all_actual_*`` (``all_not-actual`` организаторы просят игнорировать) и только
позиции со статусом ``ACTIVE`` из охвата по префиксам ОКПД2.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree


@dataclass
class RawValue:
    text: str | None = None
    okei_code: str | None = None
    okei_name: str | None = None
    min_op: str | None = None
    minimum: float | None = None
    max_op: str | None = None
    maximum: float | None = None
    concrete: float | None = None


@dataclass
class RawChar:
    code: str
    name: str
    type: int
    kind: int
    required: bool
    actual: bool
    values: list[RawValue] = field(default_factory=list)


@dataclass
class RawPosition:
    code: str
    version: int
    name: str
    okpd2: str
    okpd2_name: str
    status: str
    is_template: bool
    parent: str | None
    no_new_features: bool
    description: str | None
    chars: list[RawChar] = field(default_factory=list)


def _number(text: str | None) -> float | None:
    if text is None or not text.strip():
        return None
    try:
        return float(text.strip().replace(",", "."))
    except ValueError:
        return None


def _bool(text: str | None) -> bool:
    return (text or "").strip().lower() == "true"


def _parse_value(element) -> RawValue:
    value = RawValue()
    value.text = element.findtext("{*}qualityDescription")
    okei = element.find("{*}OKEI")
    if okei is not None:
        value.okei_code = okei.findtext("{*}code")
        value.okei_name = okei.findtext("{*}name")
    value_range = element.find("{*}rangeSet/{*}valueRange")
    if value_range is not None:
        value.min_op = value_range.findtext("{*}minMathNotation")
        value.minimum = _number(value_range.findtext("{*}min"))
        value.max_op = value_range.findtext("{*}maxMathNotation")
        value.maximum = _number(value_range.findtext("{*}max"))
    concrete = element.findtext("{*}valueSet/{*}concreteValue")
    if concrete is not None:
        value.concrete = _number(concrete)
    return value


def _parse_position(data) -> RawPosition:
    description = None
    for node in data.iterfind(".//{*}descriptionValue"):
        if node.text and node.text.strip():
            description = node.text.strip()
            break
    position = RawPosition(
        code=data.findtext("{*}code") or "",
        version=int(data.findtext("{*}version") or 0),
        name=(data.findtext("{*}name") or "").strip(),
        okpd2=data.findtext("{*}OKPD2/{*}code") or "",
        okpd2_name=(data.findtext("{*}OKPD2/{*}name") or "").strip(),
        status=data.findtext("{*}status") or "",
        is_template=_bool(data.findtext("{*}isTemplate")),
        parent=data.findtext("{*}parentPositionInfo/{*}code"),
        no_new_features=_bool(data.findtext("{*}noNewFeatures")),
        description=description,
    )
    for element in data.iterfind("{*}characteristics/{*}characteristic"):
        char = RawChar(
            code=element.findtext("{*}code") or "",
            name=(element.findtext("{*}name") or "").strip(),
            type=int(element.findtext("{*}type") or 1),
            kind=int(element.findtext("{*}kind") or 2),
            required=_bool(element.findtext("{*}isRequired")),
            actual=(element.findtext("{*}actual") or "true").strip().lower() != "false",
        )
        char.values = [_parse_value(item) for item in element.iterfind("{*}values/{*}value")]
        position.chars.append(char)
    return position


def _xml_streams(source: Path) -> Iterator[tuple[str, io.IOBase]]:
    """Все ``*_all_actual_*.xml`` из каталога: из zip-архивов и лежащие открыто."""
    paths = sorted(source.rglob("*")) if source.is_dir() else [source]
    for path in paths:
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    if "_all_actual_" in name and name.lower().endswith(".xml"):
                        with archive.open(name) as stream:
                            yield name, stream
        elif path.suffix.lower() == ".xml" and "_all_actual_" in path.name:
            with path.open("rb") as stream:
                yield path.name, stream


def in_scope(okpd2: str, scope: Iterable[str]) -> bool:
    return any(okpd2.startswith(prefix) for prefix in scope)


def iter_positions(source: Path, scope: Iterable[str]) -> Iterator[RawPosition]:
    """Активные позиции из охвата. Код уникален; при повторе побеждает старшая версия."""
    scope = tuple(scope)
    for _name, stream in _xml_streams(source):
        for _event, element in etree.iterparse(stream, events=("end",), tag="{*}position"):
            data = element.find("{*}data")
            if data is not None:
                status = data.findtext("{*}status")
                okpd2 = data.findtext("{*}OKPD2/{*}code") or ""
                if status == "ACTIVE" and in_scope(okpd2, scope):
                    yield _parse_position(data)
            element.clear()
            while element.getprevious() is not None:
                del element.getparent()[0]


def load_positions(source: Path, scope: Iterable[str]) -> dict[str, RawPosition]:
    positions: dict[str, RawPosition] = {}
    for position in iter_positions(source, scope):
        known = positions.get(position.code)
        if known is None or position.version >= known.version:
            positions[position.code] = position
    return positions
