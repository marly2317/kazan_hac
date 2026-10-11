"""Что обычно указывают в реальных СТЕ сверх КТРУ — статистика по выгрузкам портала (офлайн).

В выгрузках (лист 1: ``ID СТЕ | Наименование | … | Характеристики``) у СТЕ нет кода КТРУ,
а характеристики — одной строкой «Название Значение | Название Значение». Поэтому:

1. СТЕ привязывается к семейству по наименованию — лексическим поиском (BM25, без векторов и
   LLM), и **только уверенно**: название семейства целиком есть в наименовании СТЕ и поиск
   однозначен. Сомнительные СТЕ пропускаются — лучше меньше статистики, чем чужая.
2. Строка характеристик разбирается на пары по словарю названий (лист 2): название — самый
   длинный словарный префикс сегмента.
3. По семейству считается доля СТЕ, где указана каждая характеристика, и частые значения. Не
   берутся характеристики, которые есть в КТРУ семейства (их и так заполняем), упаковка,
   бренд/модель/артикул и пустые значения («(не задано)», «0.00000»).

    python -m apps.ste.kb_build.ste_stats ../materials/Одежда.xlsx ../materials/Мебель.xlsx …

Результат — ``kb/ste_extra_stats.json``: family_id → {ste: N, chars: [{name, share, values}]}.
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from apps.ste.domain.extra_filter import same_name
from apps.ste.domain.values import fold_text

STATS_FILE = "ste_extra_stats.json"
#: Характеристику показываем, если она есть хотя бы в этой доле СТЕ семейства…
MIN_SHARE = 0.2
#: …и хотя бы у стольких СТЕ, и у семейства хотя бы столько привязанных СТЕ.
MIN_COUNT = 3
MIN_STE = 5
TOP_CHARS = 8
TOP_VALUES = 5
#: Не свойства товара для карточки: упаковка, учётные поля, вес «0.00000».
_SKIP_NAME = re.compile(
    r"упаковк|бренд|производител|модел|артикул|серия|страна|штрих|код|гарант|срок|"
    r"торгов|марка|поставщ|ндс|цена|наименован",
    re.IGNORECASE,
)
_EMPTY_VALUE = re.compile(r"^\(?не задано\)?|^0(?:[.,]0+)?(?:\s|$)", re.IGNORECASE)


def split_characteristics(raw: str, names: list[str]) -> list[tuple[str, str]]:
    """«Цвет синий | Тип сумки Сумка» → [("Цвет", "синий"), ("Тип сумки", "Сумка")].

    Название — самый длинный словарный префикс сегмента (``names`` — длинные раньше коротких).
    Словарь знает не все составные названия: «Тип сумки», «Количество внешних карманов» в нём
    только как «Тип», «Количество». Поэтому название продлевается строчными словами до первого
    слова с заглавной буквы, цифры или скобки — с них в выгрузке начинается значение. Если
    такого слова нет («Цвет темно-синий»), остаётся словарное название.
    """
    pairs = []
    for segment in (part.strip() for part in (raw or "").split("|")):
        if not segment:
            continue
        folded = fold_text(segment)
        for name in names:
            key = fold_text(name)
            if not (folded.startswith(key + " ") and len(segment) > len(name) + 1):
                continue
            words = segment[len(name) :].split()
            start = next(
                (
                    i
                    for i, word in enumerate(words)
                    if word[:1].isupper() or word[:1].isdigit() or word[:1] in '(«"'
                ),
                None,
            )
            lowercase_lead = start and all(word.isalpha() or "-" in word for word in words[:start])
            if lowercase_lead:
                name = f"{name} {' '.join(words[:start])}"
                words = words[start:]
            pairs.append((name, " ".join(words)))
            break
    return pairs


_ZEROS = re.compile(r"(\d+)[.,]0+\b")
_PARTS = re.compile(r",(?!\d)")


def _tidy(value: str) -> str:
    """«3.00000 шт» → «3 шт»: так значения пишет выгрузка портала."""
    return _ZEROS.sub(r"\1", value).strip()


def _read_rows(path: Path):
    import openpyxl

    workbook = openpyxl.load_workbook(path, read_only=True)
    sheet = workbook.worksheets[0]
    for row in sheet.iter_rows(min_row=2, values_only=True):
        if len(row) > 5 and row[1] and row[5]:
            yield str(row[1]), str(row[5])


def _value_key(value: str) -> str:
    return fold_text(value).strip(" .,;")


def build_stats(files: list[Path], kb, retriever, names: list[str], limit: int = 0) -> dict:
    """family_id → статистика; ``retriever.search(text, k, vectors=False)`` — только BM25."""
    names = sorted({name for name in names if name}, key=len, reverse=True)
    ste_count: Counter[str] = Counter()
    char_count: dict[str, Counter[str]] = defaultdict(Counter)
    values: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    shown_value: dict[tuple[str, str, str], str] = {}
    seen = 0
    for path in files:
        for title, raw in _read_rows(path):
            seen += 1
            if limit and seen > limit:
                break
            family = _confident_family(title, kb, retriever)
            if family is None:
                continue
            ste_count[family.id] += 1
            used: set[str] = set()
            for name, value in split_characteristics(raw, names):
                if _SKIP_NAME.search(name) or _EMPTY_VALUE.match(value.strip()):
                    continue
                if name in used or any(
                    same_name(name, char.name) for char in family.chars.values()
                ):
                    continue
                used.add(name)
                char_count[family.id][name] += 1
                for part in _PARTS.split(value):
                    part = _tidy(part)
                    if part and not _EMPTY_VALUE.match(part):
                        key = _value_key(part)
                        values[(family.id, name)][key] += 1
                        shown_value.setdefault((family.id, name, key), part)
    stats = {}
    for family_id, total in ste_count.items():
        if total < MIN_STE:
            continue
        chars = []
        for name, count in char_count[family_id].most_common():
            share = count / total
            if count < MIN_COUNT or share < MIN_SHARE:
                continue
            top = [
                shown_value[(family_id, name, key)]
                for key, _ in values[(family_id, name)].most_common(TOP_VALUES)
            ]
            chars.append({"name": name, "share": round(share, 2), "values": top})
            if len(chars) >= TOP_CHARS:
                break
        if chars:
            stats[family_id] = {"ste": total, "chars": chars}
    return stats


def _confident_family(title: str, kb, retriever):
    """Семейство СТЕ по наименованию — только когда в этом нет сомнений."""
    hits = retriever.search(title, k=3, vectors=False)
    if not hits:
        return None
    family = kb.families.get(hits[0].family_id)
    if family is None or family.role != "family":
        return None
    title_stems = {fold_text(word)[:5] for word in re.findall(r"[а-яёa-z]+", title.casefold())}
    name_stems = {
        fold_text(word)[:5]
        for word in re.findall(r"[а-яёa-z]+", family.name.casefold())
        if len(word) >= 3
    }
    return family if name_stems and name_stems <= title_stems else None


def run(files: list[Path], kb_dir: Path, limit: int = 0) -> tuple[dict, float]:
    from apps.ste.application.retrieval import Retriever
    from apps.ste.kb_build.build import load

    started = time.perf_counter()
    kb = load(kb_dir)
    retriever = Retriever(kb)  # только BM25: быстро и без модели
    names = [item["name"] for item in json.loads((kb_dir / "dict_char_names.json").read_text())]
    stats = build_stats(files, kb, retriever, names, limit)
    (kb_dir / STATS_FILE).write_text(json.dumps(stats, ensure_ascii=False, indent=1))
    return stats, time.perf_counter() - started


if __name__ == "__main__":
    from apps.ste import config

    paths = [Path(arg) for arg in sys.argv[1:] if not arg.startswith("--")]
    result, seconds = run(paths, config.KB_DIR)
    covered = sum(item["ste"] for item in result.values())
    print(f"семейств со статистикой: {len(result)}, СТЕ привязано: {covered}, {seconds:.0f} с")
