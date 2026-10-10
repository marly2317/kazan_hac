"""Converting between ОКЕИ units that measure the same thing.

The task asks for values and units to be "приведены к унифицированному виду", and КТРУ decides
what that form is: it states each characteristic's permitted values in one specific unit. A
supplier writes «500 г» for a hammer head whose mass КТРУ expresses in kilograms, and that is
not an error — it is the same mass written the way people write it. Refusing it as a wrong unit
is almost as bad as accepting an out-of-range value: both tell the user something false.

Only exact, definitional factors are listed. Nothing here is approximate, and nothing crosses a
dimension: grams never become millimetres, and a unit this table does not know converts to
nothing rather than to a guess. ``Decimal`` throughout, because the comparison downstream is
against КТРУ's own bounds and a float would put values on the wrong side of them.
"""

from __future__ import annotations

from decimal import Decimal

#: ОКЕИ unit name → ``(dimension, how many base units one of these is)``.
#:
#: Names are exactly as the reference spells them, because that is what the parser stores and
#: what :func:`apps.ktru.infrastructure.units.canonical_unit` maps onto. The base unit of each
#: dimension is the one with factor 1, chosen as the smallest so every factor is an integer.
_UNITS: dict[str, tuple[str, Decimal]] = {
    # --- length, base: millimetre ---
    "Миллиметр": ("length", Decimal(1)),
    "Сантиметр": ("length", Decimal(10)),
    "Дециметр": ("length", Decimal(100)),
    "Метр": ("length", Decimal(1000)),
    "Километр": ("length", Decimal(1_000_000)),
    # --- mass, base: gram ---
    "Грамм": ("mass", Decimal(1)),
    "Килограмм": ("mass", Decimal(1000)),
    "Центнер": ("mass", Decimal(100_000)),
    "Тонна": ("mass", Decimal(1_000_000)),
    "Тонна метрическая": ("mass", Decimal(1_000_000)),
    # --- volume, base: millilitre. ОКЕИ 111 names the millilitre and the cubic centimetre
    # together, and they are the same volume by definition.
    "Кубический сантиметр; миллилитр": ("volume", Decimal(1)),
    "Литр; кубический дециметр": ("volume", Decimal(1000)),
    "Литр": ("volume", Decimal(1000)),
    "Кубический метр": ("volume", Decimal(1_000_000)),
    # --- power, base: watt ---
    "Ватт": ("power", Decimal(1)),
    "Киловатт": ("power", Decimal(1000)),
    "Мегаватт; тысяча киловатт": ("power", Decimal(1_000_000)),
    # --- time, base: second ---
    "Секунда": ("time", Decimal(1)),
    "Минута": ("time", Decimal(60)),
    "Час": ("time", Decimal(3600)),
    "Сутки": ("time", Decimal(86_400)),
    # --- area, base: square millimetre ---
    "Квадратный миллиметр": ("area", Decimal(1)),
    "Квадратный сантиметр": ("area", Decimal(100)),
    "Квадратный метр": ("area", Decimal(1_000_000)),
    # --- electric charge and current ---
    "Миллиампер-час": ("charge", Decimal(1)),
    "Ампер-час": ("charge", Decimal(1000)),
    "Миллиампер": ("current", Decimal(1)),
    "Ампер": ("current", Decimal(1000)),
    # --- frequency ---
    "Герц": ("frequency", Decimal(1)),
    "Килогерц": ("frequency", Decimal(1000)),
    "Мегагерц": ("frequency", Decimal(1_000_000)),
}


def dimension_of(unit: str) -> str | None:
    """What kind of quantity a unit measures, or ``None`` if the table does not know it."""
    entry = _UNITS.get(unit)
    return entry[0] if entry else None


def are_commensurable(left: str, right: str) -> bool:
    """Whether two units measure the same kind of quantity, so a conversion exists."""
    first, second = dimension_of(left), dimension_of(right)
    return first is not None and first == second


def convert(value: Decimal, source: str, target: str) -> Decimal | None:
    """``value`` expressed in ``target``, or ``None`` when no exact conversion exists.

    ``None`` rather than the value unchanged: the caller has to be able to tell "the same
    quantity, written differently" apart from "a unit that does not belong to this
    characteristic at all", because the first is unified silently and the second is an error
    reported to the user.
    """
    if source == target:
        return value
    if not are_commensurable(source, target):
        return None
    scaled = value * _UNITS[source][1] / _UNITS[target][1]
    # `normalize` drops the trailing zeros that division introduces, so 500 г becomes `0.5`
    # rather than `0.500`. The `+ 0` restores a plain form for integers, which `normalize`
    # would otherwise render in exponent notation (`2000` → `2E+3`).
    return scaled.normalize() + Decimal(0)
