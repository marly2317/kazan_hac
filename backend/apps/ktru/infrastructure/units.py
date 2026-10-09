"""Mapping what people write for a unit onto the ОКЕИ name КТРУ uses.

The task asks for values *and units* to be "приведены к унифицированному виду". Users write
``кг``, ``KG``, ``килограмм``, ``Кг.`` — КТРУ says ``Килограмм`` (ОКЕИ 166). This is not
spell-checking: ``кг`` is not a misspelling of anything, it is an abbreviation, and an edit
distance will never connect the two.

The table is seeded from the ОКЕИ names that appear in the imported reference, so it stays
correct as the reference changes, and is extended with the abbreviations people actually type.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

from django.core.cache import cache

from apps.ktru.infrastructure.models import PermittedValue

logger = logging.getLogger(__name__)

CACHE_KEY = "ktru:units:v1"
CACHE_TTL = 60 * 60 * 24

#: Written form → the ОКЕИ name to normalise it to. Latin look-alikes are deliberate: `mm`
#: and `кг` typed on an English layout are routine in supplier spreadsheets.
ALIASES: dict[str, str] = {
    "мм": "Миллиметр",
    "mm": "Миллиметр",
    "миллиметр": "Миллиметр",
    "см": "Сантиметр",
    "cm": "Сантиметр",
    "сантиметр": "Сантиметр",
    "м": "Метр",
    "m": "Метр",
    "метр": "Метр",
    "г": "Грамм",
    "гр": "Грамм",
    "g": "Грамм",
    "грамм": "Грамм",
    "кг": "Килограмм",
    "kg": "Килограмм",
    "килограмм": "Килограмм",
    "т": "Тонна",
    "тонна": "Тонна",
    "л": "Литр",
    "l": "Литр",
    "литр": "Литр",
    "мл": "Кубический сантиметр; миллилитр",
    "ml": "Кубический сантиметр; миллилитр",
    "миллилитр": "Кубический сантиметр; миллилитр",
    "шт": "Штука",
    "штук": "Штука",
    "штука": "Штука",
    "pcs": "Штука",
    "вт": "Ватт",
    "w": "Ватт",
    "ватт": "Ватт",
    "квт": "Киловатт",
    "kw": "Киловатт",
    "в": "Вольт",
    "v": "Вольт",
    "вольт": "Вольт",
    "а": "Ампер",
    "ампер": "Ампер",
    "ач": "Ампер-час",
    "мач": "Миллиампер-час",
    "гц": "Герц",
    "hz": "Герц",
    "мин": "Минута",
    "ч": "Час",
    "час": "Час",
    "%": "Процент",
    "процент": "Процент",
    "градус": "Градус Цельсия",
    "°c": "Градус Цельсия",
}

_TRAILING_DOT = re.compile(r"\.+$")


def canonical_unit(written: str) -> str | None:
    """The ОКЕИ name for what the user wrote, or ``None`` when it is not a unit we know.

    ``None`` rather than the input: the caller has to be able to say "this is not a unit" and
    report it, which is one of the "несуществующие значения" the task asks to be caught.
    """
    if not written:
        return None
    key = _TRAILING_DOT.sub("", written.strip().casefold())
    if not key:
        return None

    alias = ALIASES.get(key)
    if alias:
        return alias

    # An exact ОКЕИ name the user already typed correctly, in whatever case.
    return _known_unit_names().get(key)


def _known_unit_names() -> dict[str, str]:
    """``casefolded name -> ОКЕИ name``, taken from the imported reference."""
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached

    names: dict[str, str] = {}
    rows: Iterable[tuple[str]] = (
        PermittedValue.objects.exclude(unit_name="")
        .values_list("unit_name", flat=True)
        .distinct()
        .iterator(chunk_size=1000)
    )
    for name in rows:
        names[name.casefold()] = name

    logger.info("ОКЕИ units known to the reference: %s", len(names))
    cache.set(CACHE_KEY, names, CACHE_TTL)
    return names


def invalidate() -> None:
    cache.delete(CACHE_KEY)
