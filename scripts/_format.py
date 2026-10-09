"""Печать ответа /api/v1/autofill/analyze/ в читаемом виде. Используется scripts/try.sh."""

import json
import sys

d = json.load(sys.stdin)

if d["corrected"] != d["original"]:
    print(f"  исправлено : {d['corrected']!r}")
print(
    f"  состояние  : {d['state']}   "
    f"кандидатов: {d['total_candidates']}   код: {d['resolved_code']}"
)

for c in d["characteristics"]:
    if c["raw_unit"]:
        unit = f"  [{c['raw_unit']} → {c['unit']}]"
    elif c["unit"]:
        unit = f"  {c['unit']}"
    else:
        unit = ""
    print(f"     • {c['name']}: {c['value']}{unit}")

for i in d["issues"]:
    print(f"     ! [{i['kind']}] {i['message']}")
    if i["suggestions"]:
        print(f"         варианты: {', '.join(i['suggestions'][:5])}")

for s in d["steps"]:
    if s["eliminated"]:
        print(f"     → {s['label']}: {s['before']} → {s['after']} (−{s['eliminated']})")

for c in d["candidates"][:3]:
    print(f"     = {c['code']}  {c['name'][:60]}")

for n in d["next_steps"]:
    options = ", ".join(n["options"][:4])
    print(f"     ? уточнить «{n['characteristic']}» ({n['splits_into']} вар.): {options}")
