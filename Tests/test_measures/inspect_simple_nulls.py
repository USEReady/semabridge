import csv

with open(r"measure_results\measure_inventory.csv", encoding="utf-8") as f:
    r = list(csv.DictReader(f))

simple = [x for x in r if x.get("simple_but_null") == "yes"]
print(f"Total simple_but_null: {len(simple)}")
for s in simple:
    print(f"[{s['model']}] {s['measure']} | Outcome: {s['outcome']} | Reason: {s['reason']}")
    print(f"   DAX: {s['expression'][:80]}")
    print(f"   Stage: {s['stage']}")
    print(f"   Detail: {s['detail'][:100]}")
    print(f"   Translator: status={s.get('translator_isolated')}, tier={s.get('translator_tier')}, out={s.get('translator_output')[:100]}")
    print("-" * 60)
