#!/usr/bin/env python3
"""
Translate DAX to Databricks SQL using LLMDAXTranslator.

Examples:
    python translate_dax.py "SUM('Sales'[Amount])" --table_alias sales --dataset_name "Sales Model"
    python translate_dax.py --stdin --table_alias corporate_dsi_aggregate --dataset_name "Corporate DSI Aggregate" --metric_name Corporate_IOH
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure the local src/ package is importable when running from repo root.
ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from semabridge.converter.llm_dax_translator import get_llm_translator


def _read_dax_from_stdin() -> str:
    if sys.stdin.isatty():
        return ""
    return sys.stdin.read().strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Translate DAX to Databricks SQL")
    parser.add_argument("dax", nargs="?", help="DAX expression to translate")
    parser.add_argument("--stdin", action="store_true", help="Read the DAX expression from stdin")
    parser.add_argument("--table_alias", default="fact", help="Root table alias")
    parser.add_argument("--dataset_name", default="Unknown", help="Dataset name")
    parser.add_argument("--metric_name", default="", help="Metric name")
    parser.add_argument(
        "--schema_context",
        default="",
        help="Optional JSON string with schema context, e.g. '{\"sales\": [\"amount\", \"region\"]}'",
    )
    args = parser.parse_args()

    dax = args.dax or ""
    if args.stdin:
        dax = _read_dax_from_stdin()

    if not dax:
        print("Provide DAX as an argument or pipe it via stdin.", file=sys.stderr)
        return 2

    schema_context = None
    if args.schema_context:
        import json

        schema_context = json.loads(args.schema_context)

    translator = get_llm_translator()
    result = translator.translate(
        dax=dax,
        table_alias=args.table_alias,
        dataset_name=args.dataset_name,
        metric_name=args.metric_name,
        schema_context=schema_context,
    )

    if result.is_valid and result.sql:
        print(result.sql)
        return 0

    print(f"FAILED: {result.error or result.reasoning}", file=sys.stderr)
    if result.sql:
        print(result.sql, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
