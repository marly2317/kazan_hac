"""Унификация дополнительных характеристик (архитектура §4.7).

Дополнительные — то, что пользователь указал, но в схеме семейства КТРУ нет («с подсветкой»,
«кейс в комплекте», «18 В» у шуруповёрта). Новые характеристики КТРУ разрешает
(``noNewFeatures = false`` у всех позиций в охвате), но писать их нужно единообразно: название —
по словарю типовых характеристик портала (лист 2 выгрузок), единица — по ОКЕИ.
"""

from __future__ import annotations

import difflib
import re

from apps.ste.domain import units as U
from apps.ste.domain.values import fold_text

#: Насколько название пользователя должно совпасть со словарным, чтобы заменить его.
MATCH = 0.86


def _key(name: str) -> str:
    return re.sub(r"[^0-9a-zа-я ]+", " ", fold_text(name)).strip()


class ExtraUnifier:
    def __init__(self, dictionary: list[dict]) -> None:
        # Словарь отсортирован по частоте: при совпадении ключей остаётся самое частое написание.
        self._names: dict[str, dict] = {}
        for item in dictionary:
            name = str(item.get("name") or "").strip(" :;.")
            if name:
                self._names.setdefault(_key(name), {**item, "name": name})
        self._keys = list(self._names)

    def unify(self, item: dict) -> dict:
        """``{name, value, unit, quote}`` → то же, с унифицированными названием и единицей."""
        raw_name = str(item.get("name") or "").strip()
        result = dict(item)
        result["matched_from"] = None
        key = _key(raw_name)
        known = self._names.get(key)
        if known is None and key:
            close = difflib.get_close_matches(key, self._keys, n=1, cutoff=MATCH)
            known = self._names.get(close[0]) if close else None
        if known is not None:
            result["name"] = known["name"]
            if _key(known["name"]) != key:
                result["matched_from"] = raw_name
        elif raw_name:
            result["name"] = raw_name[:1].upper() + raw_name[1:]
        unit = U.parse_unit(item.get("unit")) if item.get("unit") else None
        if unit is not None:
            result["unit"] = unit.name
        return result

    def unify_all(self, items: list[dict]) -> list[dict]:
        return [self.unify(item) for item in items if item.get("name") or item.get("value")]
