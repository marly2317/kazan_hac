#!/usr/bin/env python3
"""Замер качества подбора КТРУ на фиксированном наборе описаний.

Зачем это нужно отдельным файлом: «работает хорошо» — не ответ на вопрос жюри «насколько
точно?», а каждая правка эвристики может чинить один случай и ломать два других, чего без
замера не видно. Здесь набор описаний с ожидаемым результатом, и одна команда печатает цифры.

    python3 scripts/eval.py --base-url http://localhost:8099 \
        --email demo@example.com --password demo-password-2026

Что считается:

* **точность@1** — наименование лидирующей позиции совпало с ожидаемым;
* **точность@5** — ожидаемое наименование есть среди первых пяти;
* **воздержание** — для описаний, которых в КТРУ нет, сервис ответил `not_found`. Это
  отдельная метрика, потому что уверенно неверный код хуже честного «не нашли», и система,
  оптимизированная только по точности, научится угадывать;
* **время ответа** — медиана и 95-й процентиль.

Сравнение идёт по **наименованию**, а не по коду: КТРУ даёт разным позициям одинаковые имена
(в справочнике три позиции «Куртка зимняя из кожи»), и требовать конкретный код из трёх
одинаковых значило бы штрафовать за угадывание, а не за понимание.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.cookiejar import CookieJar


@dataclass(frozen=True)
class Case:
    description: str
    #: Ожидаемое наименование позиции КТРУ, либо ``None`` — ожидается воздержание.
    expect: str | None
    note: str = ""


#: Набор намеренно смешанный: чистые описания, описания с опечатками, с единицами, которые
#: нужно привести, и товары, которых в справочнике нет. Набор только из удобных случаев
#: показывает 100% и не значит ничего.
CASES: list[Case] = [
    # --- одежда ---
    Case("куртка зимняя из кожи женская 48 размер", "Куртка зимняя из кожи"),
    Case("куртка зимняя с верхом из меха", "Куртка зимняя с верхом из меха"),
    # Обобщённого «Костюм» в справочнике нет — есть «Костюм спортивный», «Костюм
    # изолирующий», «Костюм хирургический». Ожидание сформулировано по тому, что в КТРУ
    # действительно есть: набор, проверяющий несуществующие позиции, меряет не сервис.
    Case("костюм спортивный мужской", "Костюм спортивный"),
    Case("халат медицинский женский", "Халат"),
    Case(
        "перчатки трикотажные",
        "Перчатки трикотажные для защиты от внешних воздействий",
        "обобщённых «Перчатки» в КТРУ нет",
    ),
    # --- мебель ---
    Case("стол письменный ДСП", "Стол письменный"),
    Case("шкаф для одежды металлический", "Шкаф для одежды металлический"),
    Case("стеллаж офисный металлический", "Стеллаж офисный"),
    Case("кровать детская", "Кровать детская"),
    Case("стул на металлическом каркасе", "Стул на металлическом каркасе"),
    # --- инструменты ---
    Case("молоток слесарный 500 г", "Молоток"),
    Case("пила ручная по дереву", "Пила ручная"),
    Case("дрель ручная механическая", "Дрель ручная механическая"),
    Case("ключ гаечный разводной", "Ключ гаечный разводной"),
    Case("шуруповерт аккумуляторный", "Шуруповерт"),
    Case("топор плотницкий", "Топор"),
    Case("стамеска по дереву 20 мм", "Стамеска"),
    # --- опечатки, раскладка, формы слов ---
    Case("кострукция сварная стол письменный", "Стол письменный", "пропущена буква"),
    Case("малоток слесарный", "Молоток", "опечатка в корне"),
    Case("шкаф для одеждыы металлический", "Шкаф для одежды металлический", "удвоение"),
    # --- единицы, которые нужно привести ---
    Case("молоток масса головки 0.5 кг", "Молоток", "кг против граммов в КТРУ"),
    Case("стол письменный высота 75 см", "Стол письменный", "см против миллиметров"),
    # --- товаров нет в справочнике: ожидается воздержание ---
    Case("отвертка крестовая PH2 длина 150 мм", None, "в КТРУ нет отвёрток"),
    Case("перфоратор аккумуляторный SDS-plus", None, "в КТРУ нет перфораторов"),
    Case("квадрокоптер с камерой 4К", None, "не из категорий хакатона"),
    Case("абвгдеёжзий", None, "бессмыслица"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8099")
    parser.add_argument("--email", default="demo@example.com")
    parser.add_argument("--password", default="demo-password-2026")
    parser.add_argument("--verbose", action="store_true", help="печатать каждый случай")
    options = parser.parse_args()

    client = Client(options.base_url)
    try:
        client.sign_in(options.email, options.password)
    except Exception as error:  # noqa: BLE001 — это утилита, стек тут бесполезен
        print(f"Не удалось войти: {error}", file=sys.stderr)
        print("Стенд поднят? SEED_DEMO_USER=1?", file=sys.stderr)
        return 2

    hits_at_1 = hits_at_5 = 0
    expected_total = 0
    abstained_right = abstention_total = 0
    false_answers: list[tuple[Case, str]] = []
    misses: list[tuple[Case, str]] = []
    timings: list[float] = []

    for case in CASES:
        started = time.perf_counter()
        result = client.analyze(case.description)
        timings.append(time.perf_counter() - started)

        names = [candidate["name"] for candidate in result["candidates"]]
        state = result["state"]
        top = names[0] if names else "—"

        if case.expect is None:
            abstention_total += 1
            if state == "not_found" or not names:
                abstained_right += 1
                verdict = "✓ воздержался"
            else:
                false_answers.append((case, top))
                verdict = f"✗ ответил «{top}»"
        else:
            expected_total += 1
            # Воздержание на известном товаре — промах: ответ был и не был дан.
            answered = state != "not_found"
            if answered and top == case.expect:
                hits_at_1 += 1
                hits_at_5 += 1
                verdict = "✓"
            elif answered and case.expect in names[:5]:
                hits_at_5 += 1
                misses.append((case, top))
                verdict = f"~ в топ-5, лидер «{top}»"
            else:
                misses.append((case, top if answered else "воздержался"))
                verdict = f"✗ «{top}»" if answered else "✗ воздержался"

        if options.verbose:
            print(f"  {verdict:34} {case.description}")

    print()
    print("=" * 78)
    print(f"Случаев: {len(CASES)}  (с ожидаемым кодом {expected_total}, "
          f"на воздержание {abstention_total})")
    print("-" * 78)
    _ratio("Точность@1", hits_at_1, expected_total)
    _ratio("Точность@5", hits_at_5, expected_total)
    _ratio("Верное воздержание", abstained_right, abstention_total)
    print("-" * 78)
    print(f"Время ответа: медиана {statistics.median(timings) * 1000:.0f} мс, "
          f"максимум {max(timings) * 1000:.0f} мс")
    print("=" * 78)

    if false_answers:
        print("\nУверенно неверные ответы (самая дорогая ошибка):")
        for case, got in false_answers:
            print(f"  «{case.description}» → «{got}»   [{case.note}]")

    if misses:
        print("\nПромахи:")
        for case, got in misses:
            note = f"   [{case.note}]" if case.note else ""
            print(f"  «{case.description}»\n      ожидалось «{case.expect}», получено «{got}»{note}")

    # Ненулевой код возврата, если сервис уверенно отвечает там, где ответа нет: это
    # регрессия, которую нельзя пропустить молча.
    return 1 if false_answers else 0


def _ratio(label: str, hits: int, total: int) -> None:
    share = f"{hits / total:.0%}" if total else "—"
    print(f"{label:22} {hits:3} / {total:<3}  {share}")


class Client:
    """Минимальный клиент с cookie-сессией и CSRF — как у браузера."""

    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar())
        )

    def sign_in(self, email: str, password: str) -> None:
        self._opener.open(f"{self._base}/api/v1/auth/csrf/").read()
        token = self._cookie("csrftoken")
        payload = json.dumps({"email": email, "password": password}).encode()
        request = urllib.request.Request(
            f"{self._base}/api/v1/auth/login/",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-CSRFToken": token,
                # Django проверяет Referer на небезопасных запросах.
                "Referer": f"{self._base}/",
            },
        )
        self._opener.open(request).read()

    def analyze(self, description: str) -> dict:
        query = urllib.parse.urlencode({"description": description})
        with self._opener.open(f"{self._base}/api/v1/autofill/analyze/?{query}") as response:
            return json.loads(response.read())

    def _cookie(self, name: str) -> str:
        # CookieJar прячется внутри обработчика; достаём его явно, чтобы не зависеть от
        # порядка обработчиков в opener.
        jar = None
        for handler in self._opener.handlers:
            jar = getattr(handler, "cookiejar", None)
            if jar is not None:
                break
        for cookie in jar or []:
            if cookie.name == name:
                return cookie.value
        return ""


if __name__ == "__main__":
    raise SystemExit(main())
