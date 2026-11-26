"""Show the Databricks metric-view plan (Phase 3.6) for a .pbix, offline.

Runs the same conversion as Step 6 (Databricks rules) and the planner, with
no Databricks connection (tables show as "unbound"), and writes a readable
Markdown report (and optionally the raw JSON plan).

    python scripts/show_databricks_plan.py "path/to/model.pbix" --out plan.md
    python scripts/show_databricks_plan.py model.pbix --strict --capability one_to_many_joins --json plan.json

Needs SEMABRIDGE_ALLOW_LOCAL_DB=true (as the tests do).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "Tests" / "golden"))
os.environ.setdefault("SEMABRIDGE_ALLOW_LOCAL_DB", "true")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pbix", type=Path)
    ap.add_argument("--strict", action="store_true", help="leave out CALCULATE-flagged measures")
    ap.add_argument("--capability", action="append", default=[], help="enable a probed capability (repeatable)")
    ap.add_argument("--exclude", action="append", default=[], help="untick a measure (repeatable)")
    ap.add_argument("--out", type=Path, help="write the Markdown report here (default: print)")
    ap.add_argument("--json", type=Path, help="also write the raw plan JSON here")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles: print the report's · and → safely
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from test_snowflake_golden import _pbix_to_osi  # same extraction as the golden tests / engine
    from semabridge.converter.osi_to_sml import OSIToSMLConverter
    from semabridge.target_backends.databricks.planner import PlannerSettings, plan_metric_views
    from semabridge.target_backends.databricks.planner.report import render
    from semabridge.target_rules import DATABRICKS_RULES

    osi, rows = _pbix_to_osi(args.pbix)
    sml = OSIToSMLConverter(rules=DATABRICKS_RULES).from_osi(osi, row_counts=rows)
    settings = PlannerSettings(calculate_mode="strict" if args.strict else "approximate",
                               capabilities=frozenset(args.capability), excluded_measures=tuple(args.exclude))
    plan = plan_metric_views(sml, None, settings).as_dict()
    text = render(plan, args.pbix.stem)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    if args.json:
        args.json.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
