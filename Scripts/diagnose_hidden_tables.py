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


def _print_section(title: str, records: list[dict[str, object]]) -> None:
    print(title)
    if not records:
        print("  <none>")
        return
    for record in sorted(records, key=lambda item: str(item["name"]).casefold()):
        print(
            f"  {record['name']} | hidden={record['is_hidden']} | system={record['is_system']} | "
            f"relationships={record['relationship_count']} | measures={record['measure_count']}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose hidden table classification in TMDL exports.")
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

    records = analyze_tmdl_tables(tmdl_files)
    visible = [record for record in records if record["include"] and not record["is_hidden"]]
    hidden = [record for record in records if record["is_hidden"]]
    system = [record for record in records if record["is_system"]]
    hidden_fact = [record for record in records if record["is_hidden_fact"]]

    _print_section("VISIBLE TABLES", visible)
    _print_section("HIDDEN TABLES", hidden)
    _print_section("SYSTEM TABLES", system)
    _print_section("HIDDEN FACT TABLES", hidden_fact)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
