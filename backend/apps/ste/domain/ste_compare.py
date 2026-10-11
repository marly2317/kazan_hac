"""Сравнение карточки с практикой портала: КТРУ ↔ то, что мы извлекли ↔ что указывают в СТЕ.

Статистика реальных СТЕ (``kb_build.ste_stats``) говорит, какие характеристики сверх КТРУ
обычно указывают у этого типа товара. Сама по себе это сводка; полезна она в сравнении:

* **типичная характеристика СТЕ** получает статус:
  ``filled`` — мы её уже извлекли из текста (дополнительная характеристика, значение показано);
  ``ktru`` — это на самом деле характеристика КТРУ под другим названием (её заполняем там);
  ``suggested`` — в тексте о ней ничего нет, но в СТЕ её обычно указывают — предложить;
* **наша дополнительная характеристика** получает статус:
  ``typical`` — так указывают и в реальных СТЕ (доля показана);
  ``rare`` — в СТЕ этого типа её почти не указывают: проверить, нужна ли она в карточке.

Значения не подставляются: решение остаётся за пользователем. Модуль чистый.
"""

from __future__ import annotations

from apps.ste.domain.extra_filter import _GENERIC_NAME_STEMS, same_name
from apps.ste.domain.values import fold_text

SUGGESTED = "suggested"
FILLED = "filled"
IN_KTRU = "ktru"
TYPICAL = "typical"
RARE = "rare"


def _generic(name: str) -> bool:
    words = {fold_text(word)[:5] for word in name.split()}
    return bool(words) and words <= _GENERIC_NAME_STEMS


def _value_text(value) -> str:
    if isinstance(value, list | tuple):
        return ", ".join(str(part) for part in value)
    return "" if value is None else str(value)


def compare_with_ste(
    typical: list[dict],
    total_ste: int,
    ktru_chars: dict[str, str],
    extra: list[dict],
) -> dict | None:
    """``typical`` — [{name, share, values}] из статистики; ``ktru_chars`` — ключ → название
    характеристики семейства; ``extra`` — наши дополнительные характеристики [{name, value, unit}].
    """
    if not typical:
        return None
    rows = []
    for item in typical:
        name = item["name"]
        if _generic(name):
            # «Тип: дивана прямой» — выгрузка склеила название КТРУ «Тип дивана» со значением;
            # однословное «Тип»/«Вид» не говорит, что заполнять.
            continue
        ours = next((e for e in extra if same_name(str(e.get("name") or ""), name)), None)
        ktru = next((n for n in ktru_chars.values() if same_name(name, n)), None)
        row = {
            "name": name,
            "share": item.get("share", 0),
            "values": list(item.get("values") or []),
            "status": SUGGESTED,
            "value": None,
            "ktru_name": None,
        }
        if ours is not None:
            row["status"] = FILLED
            row["value"] = " ".join(
                part for part in (_value_text(ours.get("value")), ours.get("unit") or "") if part
            )
        elif ktru is not None:
            row["status"] = IN_KTRU
            row["ktru_name"] = ktru
        rows.append(row)

    own = []
    for item in extra:
        name = str(item.get("name") or "")
        match = next((t for t in typical if same_name(name, t["name"])), None)
        own.append(
            {
                "name": name,
                "value": _value_text(item.get("value")),
                "status": TYPICAL if match else RARE,
                "share": match.get("share") if match else None,
            }
        )
    return {
        "ste": total_ste,
        "typical": rows,
        "ours": own,
        "summary": {
            FILLED: sum(row["status"] == FILLED for row in rows),
            SUGGESTED: sum(row["status"] == SUGGESTED for row in rows),
            RARE: sum(item["status"] == RARE for item in own),
        },
    }
