"""Замер качества на тест-кейсах (``tests/ste/cases.jsonl``).

    cd backend
    python -m apps.ste.eval --retrieval            # только поиск: recall@1/5/10/30, без LLM
    python -m apps.ste.eval --no-llm               # весь пайплайн правилами (деградация)
    python -m apps.ste.eval --ids t01,t02          # весь пайплайн с LLM (ответы кэшируются)
    python -m apps.ste.eval --limit 10 --verbose

Каждый прогон с LLM стоит до двух запросов на кейс; ответы кэшируются на диске, поэтому
повторный прогон тех же кейсов бесплатен.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

CASES = Path(__file__).resolve().parent.parent.parent / "tests" / "ste" / "cases.jsonl"


def load_cases(path: Path = CASES) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def build_pipeline(use_llm: bool):
    from apps.ste.application.pipeline import Deps, Pipeline
    from apps.ste.infrastructure.session_store import MemorySessionStore
    from apps.ste.runtime import corrector, knowledge_base, llm_client, retriever

    return Pipeline(
        Deps(
            kb=knowledge_base(),
            corrector=corrector(),
            retriever=retriever(),
            llm=llm_client() if use_llm else None,
            store=MemorySessionStore(),
        )
    )


def _family_ok(case: dict, family_id: str | None) -> bool:
    expected = case.get("expected_family")
    acceptable = set(case.get("acceptable_families") or [])
    if expected is None:
        return family_id is None
    return family_id == expected or family_id in acceptable


def eval_retrieval(cases: list[dict], verbose: bool) -> None:
    from apps.ste.runtime import retriever

    search = retriever()
    ranks: list[int | None] = []
    latencies = []
    for case in cases:
        if not case.get("expected_family"):
            continue
        start = time.perf_counter()
        hits = search.search(case["text"], k=30)
        latencies.append((time.perf_counter() - start) * 1000)
        ids = [hit.family_id for hit in hits]
        targets = {case["expected_family"], *(case.get("acceptable_families") or [])}
        rank = next((index + 1 for index, fid in enumerate(ids) if fid in targets), None)
        ranks.append(rank)
        if verbose and (rank is None or rank > 1):
            top = hits[0].name if hits else "—"
            expected = case.get("expected_family_name")
            print(f"  [{case['id']}] rank={rank} «{case['text']}» → «{top}»; ждали «{expected}»")
    total = len(ranks)
    for k in (1, 3, 5, 10, 30):
        hit = sum(1 for rank in ranks if rank is not None and rank <= k)
        print(f"recall@{k:<2} {hit}/{total} = {hit / total:.0%}")
    print(f"latency median {statistics.median(latencies):.0f} ms, max {max(latencies):.0f} ms")


def eval_pipeline(cases: list[dict], use_llm: bool, verbose: bool) -> int:
    pipeline = build_pipeline(use_llm)
    stats = {
        "family": 0,
        "leaf": 0,
        "leaf_total": 0,
        "stage": 0,
        "stage_total": 0,
        "signals": 0,
        "signals_total": 0,
        "absent_ok": 0,
        "absent_total": 0,
        "n": 0,
    }
    latencies = []
    wrong_confident = 0
    for case in cases:
        start = time.perf_counter()
        result = pipeline.analyze(case["text"])
        latencies.append((time.perf_counter() - start) * 1000)
        stats["n"] += 1
        family_id = (result.get("family") or {}).get("id")
        if result["stage"] == "not_found":
            family_id = None
        fam_ok = _family_ok(case, family_id)
        stats["family"] += fam_ok
        if case.get("expected_family") is None:
            stats["absent_total"] += 1
            stats["absent_ok"] += result["stage"] == "not_found"
        if case.get("expected_leaf"):
            stats["leaf_total"] += 1
            stats["leaf"] += result.get("final_code") == case["expected_leaf"]
        if result.get("final_code") and not fam_ok:
            wrong_confident += 1
        if case.get("expected_stage"):
            stats["stage_total"] += 1
            stats["stage"] += result["stage"] == case["expected_stage"]
        expected_signals = set(case.get("expected_signals") or []) - {"TYPO"}
        if expected_signals:
            got = {signal["type"] for signal in result["signals"]}
            stats["signals_total"] += len(expected_signals)
            stats["signals"] += len(expected_signals & got)
        if verbose:
            mark = "✓" if fam_ok else "✗"
            family_name = (result.get("family") or {}).get("name")
            signal_types = [signal["type"] for signal in result["signals"]]
            print(
                f"{mark} [{case['id']}] {result['stage']:<10} «{case['text'][:70]}»\n"
                f"    семейство: {family_name} | ждали: {case.get('expected_family_name')}\n"
                f"    код: {result.get('final_code')} | ждали: {case.get('expected_leaf')} | "
                f"сигналы: {signal_types} | {result['timings_ms']}"
            )
    n = stats["n"]
    print(f"\nсемейство верно      {stats['family']}/{n} = {stats['family'] / max(n, 1):.0%}")
    if stats["leaf_total"]:
        print(f"конечный код верен   {stats['leaf']}/{stats['leaf_total']}")
    if stats["stage_total"]:
        print(f"стадия верна         {stats['stage']}/{stats['stage_total']}")
    if stats["signals_total"]:
        print(f"ожидаемые сигналы    {stats['signals']}/{stats['signals_total']}")
    if stats["absent_total"]:
        print(f"честное «не нашли»   {stats['absent_ok']}/{stats['absent_total']}")
    print(f"уверенно неверный код: {wrong_confident}")
    print(f"время: медиана {statistics.median(latencies):.0f} мс, максимум {max(latencies):.0f} мс")
    return 1 if wrong_confident else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retrieval", action="store_true")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--ids", default="")
    parser.add_argument("--variant", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    cases = load_cases()
    if args.ids:
        wanted = set(args.ids.split(","))
        cases = [case for case in cases if case["id"] in wanted]
    if args.variant:
        cases = [case for case in cases if case.get("variant") in set(args.variant.split(","))]
    if args.limit:
        cases = cases[: args.limit]
    if args.retrieval:
        eval_retrieval(cases, args.verbose)
        return 0
    return eval_pipeline(cases, use_llm=not args.no_llm, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
