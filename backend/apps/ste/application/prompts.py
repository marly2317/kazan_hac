"""Промпты LLM (архитектура §7): выбор семейства, разложение по схеме, одна характеристика,
метаданные семейств.

Только сборка сообщений — без вызовов и без разбора ответов. Схему ответа сюда не вписываем:
в режиме ``prompt`` клиент сам дописывает её к последнему сообщению пользователя, поэтому
системное сообщение зависит лишь от каталога/семейства и кэшируется провайдером.
"""

from __future__ import annotations

import math
import re

from apps.ste.domain.kb import Family, FamilyChar
from apps.ste.domain.values import fold_text

Messages = list[dict[str, str]]

#: Сколько допустимых значений качественной характеристики перечислять без фильтра.
ENUM_LIMIT = 40
#: Длинный список: значения, найденные в тексте (не больше стольких), + первые значения списка.
ENUM_RELEVANT_MAX = 30
#: Метаданные семейства (§3.7): обрезка описания и «видов».
META_DESCRIPTION_LIMIT = 300
META_KIND1_LIMIT = 30
META_CHARS_LIMIT = 25

#: Сколько первых букв слова считаются основой — грубый, но устойчивый к словоформам признак.
_STEM = 5
_WORD = re.compile(r"[0-9a-zа-яё]+", re.IGNORECASE)


def _stem(word: str) -> str:
    word = word.casefold().replace("ё", "е")
    return word[:_STEM] if len(word) > _STEM else word


def _quoted(text: str) -> str:
    return "«" + text.strip() + "»"


# --- LLM-1: выбор семейства (§4.1, §7.1) ---------------------------------------------------

FAMILY_SELECT_ROLE = (
    "Ты эксперт по каталогу КТРУ (одежда, обувь, сумки, мебель, инструменты). По описанию товара "
    "от поставщика выбери подходящее семейство КТРУ строго из списка ниже."
)

FAMILY_SELECT_RULES = """\
Правила:
- Выбирай только id из списков выше (вида F001).
- Учитывай разговорные названия, синонимы, сокращения и опечатки: «болгарка» и «УШМ» — шлифмашина.
- Конкретное семейство предпочтительнее общей позиции; общую — только если конкретных нет.
- Верни 1–3 кандидата по убыванию score — уверенности от 0 до 1 — и reason: одно короткое \
предложение со ссылкой на слова из текста.
- score ≥ 0.75 — тип товара назван явно; 0.4–0.75 — похоже, но есть сомнения; \
кандидатов ниже 0.4 не указывай.
- Если товар не относится ни к одному семейству (другая категория, бессмысленный текст) — \
candidates пустой, not_found = true, not_found_reason — коротко почему."""


def family_select_messages(text: str, card_lines: list[str], generic_lines: list[str]) -> Messages:
    """LLM-1. Длинный список карточек — в начале системного сообщения: одинаковый префикс
    кэшируется провайдером, а правила стоят ближе к вопросу и лучше соблюдаются."""
    parts = [FAMILY_SELECT_ROLE, "", "СПИСОК СЕМЕЙСТВ:", *card_lines]
    if generic_lines:
        parts += ["", "ОБЩИЕ ПОЗИЦИИ (запасной вариант, если конкретного семейства нет):"]
        parts += generic_lines
    parts += ["", FAMILY_SELECT_RULES]
    return [
        {"role": "system", "content": "\n".join(parts)},
        {"role": "user", "content": f"Описание товара: {_quoted(text)}"},
    ]


# --- таблица характеристик для LLM-2 (§3.6, §7.2) ------------------------------------------


def char_ids(family: Family) -> dict[str, str]:
    """Короткие id ``c01…cNN`` → ключ характеристики, в порядке ``family.chars``.

    Кириллица в ключах JSON-схемы — лишний риск для модели и для провайдеров; id стабильны,
    пока стабилен порядок характеристик в базе знаний.
    """
    width = max(2, len(str(len(family.chars))))
    return {f"c{index:0{width}d}": key for index, key in enumerate(family.chars, start=1)}


def is_multi(char: FamilyChar) -> bool:
    """Выбор нескольких значений (kind=3) хотя бы у одного листа."""
    return 3 in char.kinds


def relevant_values(
    values: list[str], text: str, limit: int = ENUM_LIMIT
) -> tuple[list[str], bool]:
    """Допустимые значения для промпта: все, если их не больше ``limit``; иначе — найденные в
    тексте (до :data:`ENUM_RELEVANT_MAX`) плюс первые по списку. Второе — список урезан.

    Длинные списки — это размеры («29,5»…«48») и дюймы («1 1/2»): нужное значение почти всегда
    написано в тексте дословно, а сотня соседних только раздувает промпт.
    """
    if len(values) <= limit:
        return list(values), False
    folded_text = fold_text(text)
    text_stems = {_stem(word) for word in _WORD.findall(folded_text) if len(word) >= 3}
    matched: list[str] = []
    for value in values:
        folded = fold_text(value)
        if not folded:
            continue
        # Число/дробь — только целиком: «44» не должно найтись внутри «44,5».
        pattern = r"(?<![\w/.,])" + re.escape(folded) + r"(?![\w/]|[.,]\d)"
        if re.search(pattern, folded_text):
            matched.append(value)
            continue
        stems = [_stem(word) for word in _WORD.findall(folded) if len(word) >= 3]
        stems = [stem for stem in stems if not stem.isdigit()]
        if stems and sum(stem in text_stems for stem in stems) >= max(1, math.ceil(len(stems) / 2)):
            matched.append(value)
    matched = matched[:ENUM_RELEVANT_MAX]
    chosen = set(matched)
    head = [value for value in values if value not in chosen][: max(0, limit - len(matched))]
    return matched + head, True


def char_line(cid: str, char: FamilyChar, text: str) -> str:
    """«c01 — Вид — текст — — обязательная — допустимые: A; B; C»."""
    required = "обязательная" if char.required else "необязательная"
    if char.is_numeric:
        unit = char.unit
        # Пустая колонка единицы — «— —», как в §7.2: безразмерные «Рост», «Размер».
        unit_text = f" {unit.name} " if unit else " "
        return f"{cid} — {char.name} — число —{unit_text}— {required}"
    kind = "список (несколько значений)" if is_multi(char) else "текст"
    values = char.text_values()
    if not values:
        allowed = "допустимые: любое значение"
    else:
        shown, truncated = relevant_values(values, text)
        allowed = "допустимые"
        if truncated:
            allowed += (
                f" (показано {len(shown)} из {len(values)}; другое значение из текста "
                "копируй дословно)"
            )
        allowed += ": " + "; ".join(shown)
    return f"{cid} — {char.name} — {kind} — — {required} — {allowed}"


EXTRACT_RULES = """\
Правила:
- Просмотри КАЖДУЮ характеристику таблицы. В characteristics включи те, о которых говорит текст, \
со status:
  «указано» — значение прямо следует из текста, в том числе через очевидный синоним \
(«болгарка» → «Углошлифовальная машина», «аккумуляторный» → «От аккумулятора», \
«сетевой» → «От сети»);
  «противоречие» — в тексте значение, которого нет среди допустимых, или текст сам себе \
противоречит.
  Характеристики, о которых текст молчит, не включай: они считаются «не указано».
- Текстовые значения — дословно из списка допустимых. Подходящего нет — status «противоречие», \
value — значение как в тексте.
- Тип «список» — value массивом допустимых значений.
- Числа — value числом как в тексте (0,84 → 0.84), unit — единица как в тексте («кВт», «мм»), \
без перевода; единицы нет — null. Для чисел status всегда «указано», даже если число или единица \
кажутся неверными («толщина 25 кг», «длина 5 м» при допустимых 60–250 см): единицы, диапазоны и \
ошибки проверяет код, а не ты.
- quote — точная подстрока текста, из которой взято значение.
- Одна характеристика указана в тексте с разными значениями — включи её несколько раз.
- Всё остальное существенное о товаре, чего нет в таблице, — в extra: name, value, unit, quote. \
В extra — только объективные проверяемые свойства самого товара (материал, цвет, принт, фасон, \
комплектация, назначение). Не включай оценки, эмоции и рекламу («афигенный», «классный», \
«лучший», «качественный», «стильный») и не заводи общих полей вроде «Описание», «Комментарий», \
«Особенности», «Качество». Из «красивый принт пони» в extra — «Принт: пони».
- brand — производитель, model — модель или артикул; нет в тексте — null. В extra их не дублируй.
- Ничего не выдумывай и не дополняй значениями по умолчанию."""


def _family_header(family: Family) -> str:
    okpd2 = f"{family.okpd2} {family.okpd2_name}".strip()
    return f"Семейство: {family.name}" + (f" (ОКПД2 {okpd2})." if okpd2 else ".")


def extract_messages(text: str, family: Family, extra_hints: list[str] | None = None) -> Messages:
    """LLM-2: разложить текст по характеристикам семейства (§4.3, §7.2).

    ``extra_hints`` — как в реальных СТЕ этого типа называют характеристики сверх КТРУ: если
    текст о них говорит, extra получает эти названия, а не придуманные моделью.
    """
    ids = char_ids(family)
    lines = [char_line(cid, family.chars[key], text) for cid, key in ids.items()]
    hints = []
    if extra_hints:
        hints = [
            "",
            "Для extra: в реальных СТЕ этого типа обычно указывают "
            + ", ".join(f"«{name}»" for name in extra_hints)
            + ". Если текст о них говорит — называй их так. Если не говорит — не добавляй.",
        ]
    system = "\n".join(
        [
            "Ты заполняешь характеристики товара по справочнику КТРУ.",
            _family_header(family),
            "Характеристики (id — название — тип — единица — обязательность — допустимые "
            "значения):",
            *lines,
            *hints,
            "",
            EXTRACT_RULES,
        ]
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Описание товара: {_quoted(text)}"},
    ]


EXTRACT_ONE_RULES = """\
Правила:
- status «указано» — ответ даёт значение, в том числе через очевидный синоним; «противоречие» — \
такого значения нет среди допустимых; «не указано» — значения в ответе нет («не знаю», «любой»).
- Текстовое значение — дословно из списка допустимых; подходящего нет — value как в ответе.
- Тип «список» — value массивом допустимых значений.
- Число — value числом как в ответе, unit — единица как в ответе; единицы нет — null.
- quote — точная подстрока ответа. Ничего не выдумывай."""


def extract_one_messages(text: str, family: Family, char_key: str) -> Messages:
    """Мини-LLM-2: свободный ответ пользователя на вопрос об одной характеристике (§4.8)."""
    char = family.chars[char_key]
    system = "\n".join(
        [
            "Ты заполняешь одну характеристику товара по справочнику КТРУ.",
            _family_header(family),
            "Характеристика (название — тип — единица — обязательность — допустимые значения):",
            char_line("c01", char, text).split(" — ", 1)[1],
            "",
            EXTRACT_ONE_RULES,
        ]
    )
    user = f"Вопрос: «Уточните: {char.name}». Ответ пользователя: {_quoted(text)}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# --- метаданные семейств (офлайн, §3.7) ----------------------------------------------------

META_RULES = """\
Ты составляешь справочные метаданные семейств каталога КТРУ для поиска по запросам поставщиков.
Для каждого семейства верни:
- synonyms — 5–15 названий ЭТОГО ТИПА ТОВАРА, как его называют поставщики и покупатели: \
разговорные, профессиональные, сокращения, частые варианты написания (для «Шлифмашина»: \
болгарка, ушм, углошлифовалка, шлифовальная машинка). Только названия типа товара: без брендов, \
моделей, артикулов, размеров, цветов и материалов; не повторяй название семейства. \
Узкий тип товара — меньше синонимов, но не выдумывай.
- description — 1–2 предложения по-русски: что это за товар и для чего.
- keywords — до 8 ключевых слов: назначение, область применения, отличительные признаки.
synonyms и keywords — в нижнем регистре."""


def family_meta_block(family: Family) -> str:
    """Описание одного семейства для промпта метаданных."""
    lines = [f"[{family.short_id}] {family.name}"]
    okpd2 = f"{family.okpd2} {family.okpd2_name}".strip()
    if okpd2:
        lines.append(f"  ОКПД2: {okpd2}")
    description = re.sub(r"\s+", " ", family.description or "").strip()
    if description:
        if len(description) > META_DESCRIPTION_LIMIT:
            description = description[: META_DESCRIPTION_LIMIT - 1].rstrip() + "…"
        lines.append(f"  описание: {description}")
    kinds = family.kind1_values(META_KIND1_LIMIT)
    if kinds:
        lines.append("  виды: " + "; ".join(kinds))
    names = [char.name for char in family.chars.values()][:META_CHARS_LIMIT]
    if names:
        lines.append("  характеристики: " + "; ".join(names))
    return "\n".join(lines)


def family_meta_messages(families: list[Family]) -> Messages:
    blocks = "\n\n".join(family_meta_block(family) for family in families)
    ids = ", ".join(family.short_id for family in families)
    user = (
        f"Семейства:\n\n{blocks}\n\n"
        f"Верни объект: ключ — id семейства ({ids}), значение — synonyms, description, keywords."
    )
    return [{"role": "system", "content": META_RULES}, {"role": "user", "content": user}]
