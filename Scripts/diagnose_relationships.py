#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from semabridge.converter.tmdl_parser import TMDLParser  # noqa: E402
from semabridge.converter.tmdl_to_osi import analyze_tmdl_tables  # noqa: E402


def _load_tmdl_files(root: Path) -> dict[str, str]:
    tmdl_files: dict[str, str] = {}
    for file_path in root.rglob("*.tmdl"):
        try:
            rel_path = file_path.relative_to(root).as_posix()
        except ValueError:
            continue
        if "definition/tables/" not in rel_path:
            continue
        tmdl_files[rel_path] = file_path.read_text(encoding="utf-8", errors="ignore")
    return tmdl_files


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose TMDL relationship filtering decisions.")
    parser.add_argument(
        "root",
        nargs="?",
        default=str(ROOT),
        help="Workspace root or extracted Fabric export root containing definition/tables/*.tmdl",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.exists() or not root.is_dir():
        print(f"ERROR: root directory not found: {root}", file=sys.stderr)
        return 2

    tmdl_files = _load_tmdl_files(root)
    if not tmdl_files:
        print("No TMDL table files found under definition/tables.", file=sys.stderr)
        return 1

    table_records = analyze_tmdl_tables(tmdl_files)
    deployed_tables = {str(record["name"]).casefold() for record in table_records if record["include"]}

    kept: list[str] = []
    dropped: list[str] = []
    date_variations: list[str] = []

    for rel_path, content in tmdl_files.items():
        for rel in TMDLParser.parse_relationships(content):
            name = str(rel.get("name") or rel.get("relationship_id") or "<unnamed>")
            from_table = str(rel.get("fromTable") or "")
            to_table = str(rel.get("toTable") or "")
            join_behavior = str(rel.get("joinOnDateBehavior") or "").strip().casefold()
            if join_behavior == "datepartonly":
                date_variations.append(f"{name}: {from_table} -> {to_table}")
                continue

            if from_table.casefold() in deployed_tables and to_table.casefold() in deployed_tables:
                kept.append(f"{name}: {from_table}.{rel.get('fromColumn')} -> {to_table}.{rel.get('toColumn')}")
            else:
                reasons = []
                if from_table.casefold() not in deployed_tables:
                    reasons.append(f"missing from-table '{from_table}'")
                if to_table.casefold() not in deployed_tables:
                    reasons.append(f"missing to-table '{to_table}'")
                dropped.append(f"{name}: {from_table}.{rel.get('fromColumn')} -> {to_table}.{rel.get('toColumn')} | {', '.join(reasons)}")

    print("KEPT RELATIONSHIPS")
    print("  <none>" if not kept else "  " + "\n  ".join(sorted(kept)))
    print("DROPPED RELATIONSHIPS")
    print("  <none>" if not dropped else "  " + "\n  ".join(sorted(dropped)))
    print("DATE VARIATION RELATIONSHIPS")
    print("  <none>" if not date_variations else "  " + "\n  ".join(sorted(date_variations)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
