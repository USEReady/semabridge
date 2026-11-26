"""Run the Phase 3.5 Databricks capability probes (explicit, user-started only).

Creates small synthetic metric views (named semabridge_probe_<token>_...) in an
EXISTING schema you name, queries them, compares the results with hand-computed
expectations, drops what it created, and records the evidence. No tables are
created and no customer data is read. Nothing runs without --execute.

    python scripts/run_databricks_probes.py --schema workspace.sb_tests              # offline: build + show SQL
    python scripts/run_databricks_probes.py --schema workspace.sb_tests --execute    # live run
    python scripts/run_databricks_probes.py --schema workspace.sb_tests --execute --json report.json

The connection comes from the global Databricks settings (.env); secrets are never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
os.environ.setdefault("SEMABRIDGE_ALLOW_LOCAL_DB", "true")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--schema", required=True, help="existing catalog.schema for the probe objects")
    ap.add_argument("--warehouse-id", help="SQL warehouse (default: configured, else auto-selected)")
    ap.add_argument("--execute", action="store_true", help="actually run on Databricks (otherwise offline only)")
    ap.add_argument("--json", type=Path, help="write the full report here")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    catalog, _, schema = args.schema.partition(".")
    if not catalog or not schema:
        ap.error("--schema must be catalog.schema")

    from semabridge.target_backends.databricks.probes import fixtures as F, pipeline

    if not args.execute:
        for fx in F.ALL:
            b = pipeline.build(fx, catalog=catalog, schema=schema, token="offline")
            print(f"\n## {fx.fid} ({fx.probe}): {fx.purpose}")
            if b.skipped_measures:
                print("   not placed by SemaBridge:", ", ".join(b.skipped_measures))
            for r in b.rendered:
                print(r.create_sql)
            for q in fx.queries:
                print("--", q.qid, ":", pipeline.query_sql(b, q, catalog, schema)[0])
        print("\n(offline only: nothing was sent to Databricks; add --execute to run)")
        return 0

    from semabridge.core.settings import get_settings
    from semabridge.target_backends.databricks.auth import DatabricksConnectionInfo
    from semabridge.target_backends.databricks.probes.executor import run_probes

    info = DatabricksConnectionInfo.from_config(get_settings().databricks, catalog=catalog, schema=schema)
    if args.warehouse_id:
        import dataclasses

        info = dataclasses.replace(info, warehouse_id=args.warehouse_id)
    report = run_probes(info, probe_catalog=catalog, probe_schema=schema, created_by="cli")
    print(f"run {report['run_id']} on DBSQL {report.get('dbsql_version')} (warehouse {report.get('warehouse_id')})")
    if report.get("preflight_error"):
        print("PREFLIGHT FAILED:", report["preflight_error"])
    for gate, g in report["gates"].items():
        print(f"  {gate:20s} {g['outcome']:13s} {g.get('reason', '')}")
        for fid, o in (g.get("fixtures") or {}).items():
            f = report["fixtures"].get(fid, {})
            print(f"      {fid:8s} {o}" + (f"  -- {f['error']}" if f.get("error") else ""))
            for qid, q in (f.get("queries") or {}).items():
                if q.get("outcome") not in ("proven", "observed"):
                    print(f"          {qid}: {q.get('outcome')} {q.get('error', '')} "
                          f"{q.get('mismatches') or ''} {q.get('missing') or ''} {q.get('extra') or ''}")
    for fid, o in report.get("observations", {}).items():
        print(f"  observation {fid}: {json.dumps({k: v.get('rows') for k, v in o.get('queries', {}).items()}, default=str)}")
    c = report["cleanup"]
    print(f"cleanup: dropped {len(c['dropped'])}, left untouched {len(c['left_untouched'])}, errors {len(c['errors'])}")
    for x in c["left_untouched"] + c["errors"]:
        print("  !", x)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"report written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
