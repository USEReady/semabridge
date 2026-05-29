#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from semabridge.converter.tmdl_parser import TMDLParser  # noqa: E402

SIMPLE_PATTERN = re.compile(r"^(SUM|AVERAGE|MIN|MAX|COUNT|DISTINCTCOUNT|COUNTROWS)\s*\(", re.IGNORECASE)
ITERATOR_PATTERN = re.compile(r"\b(SUMX|AVERAGEX|MINX|MAXX|COUNTX|FILTER|RANKX|ADDCOLUMNS|TOPN)\s*\(", re.IGNORECASE)
MEASURE_REF_PATTERN = re.compile(r"(?<!['\w])\[([^\]]+)\]")
TABLE_COLUMN_PATTERN = re.compile(r"'([^']+)'\[([^\]]+)\]")


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


def _classify_measure(expr: str) -> str:
    clean = " ".join((expr or "").split()).strip()
    upper = clean.upper()
    if not clean:
        return "empty"
    if ITERATOR_PATTERN.search(clean):
        return "iterator"
    if upper.startswith("DIVIDE("):
        return "divide"
    if upper.startswith("CALCULATE("):
        return "calculate"
    if SIMPLE_PATTERN.match(clean):
        return "simple"
    return "complex"


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose measure extraction and cross-table dependencies in TMDL exports.")
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

    measures_by_table: dict[str, list[dict[str, str]]] = {}
    measure_owner_index: dict[str, list[str]] = defaultdict(list)

    for rel_path, content in tmdl_files.items():
        table_name = TMDLParser.derive_table_name(rel_path, content)
        parsed = TMDLParser.parse_table_file(content)
        measures: list[dict[str, str]] = []
        for raw_measure in parsed["measures"]:
            name = str(raw_measure.get("name") or "").strip()
            expr = str(raw_measure.get("expression") or "").strip()
            measures.append({"name": name, "expression": expr})
            if name:
                measure_owner_index[name.casefold()].append(table_name)
        measures_by_table[table_name] = measures

    print("EXTRACTED MEASURES")
    for table_name in sorted(measures_by_table, key=str.casefold):
        table_measures = measures_by_table[table_name]
        print(f"  {table_name}")
        if not table_measures:
            print("    <none>")
            continue
        for measure in table_measures:
            print(f"    {measure['name']} = {measure['expression']}")

    print("MEASURE COMPLEXITY")
    for table_name in sorted(measures_by_table, key=str.casefold):
        for measure in measures_by_table[table_name]:
            expr = measure["expression"]
            print(f"  {table_name}.{measure['name']} -> {_classify_measure(expr)}")

    print("CROSS-TABLE REFERENCES")
    cross_refs: list[str] = []
    for table_name in sorted(measures_by_table, key=str.casefold):
        for measure in measures_by_table[table_name]:
            expr = measure["expression"]
            if not expr:
                continue
            measure_refs = [ref for ref in MEASURE_REF_PATTERN.findall(expr)]
            table_refs = TABLE_COLUMN_PATTERN.findall(expr)
            for ref in measure_refs:
                if any(expr_fragment in ref for expr_fragment in ("SUM(", "CALCULATE(", "DIVIDE(")):
                    continue
                owners = measure_owner_index.get(ref.casefold(), [])
                if owners and table_name not in owners:
                    cross_refs.append(f"  {table_name}.{measure['name']} -> measure [{ref}] owned by {', '.join(sorted(set(owners), key=str.casefold))}")
            for ref_table, ref_column in table_refs:
                if ref_table.casefold() != table_name.casefold():
                    cross_refs.append(f"  {table_name}.{measure['name']} -> column {ref_table}[{ref_column}]")
    print("  <none>" if not cross_refs else "\n".join(sorted(set(cross_refs), key=str.casefold)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
