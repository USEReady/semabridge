#!/usr/bin/env python3
"""Batch-translate complex DAX samples and save a deployment audit.

This script is intended for analysis, not production deployment. It translates
each DAX expression for both Databricks and Snowflake targets, saves the raw
SQL results, and writes a summary report so the translated outputs can be
reviewed for deployability.

Outputs:
  - JSON audit report with per-target translation results
  - Markdown summary
  - One .sql file per metric per target

Example:
  python Scripts/audit_tier6_dax.py
  python Scripts/audit_tier6_dax.py --output-dir output/dax_audit
  python Scripts/audit_tier6_dax.py --targets databricks snowflake
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from semabridge.converter.common_dax_translator import CommonDAXTranslator, SQLDialect
from semabridge.converter.dax_ast_parser import DaxAstParser, DaxSqlRenderer
from semabridge.converter.dax_rule_translator import rule_based_translation


@dataclass(frozen=True)
class DAXCase:
    name: str
    dax: str


DAX_CASES: list[DAXCase] = [
    DAXCase(
        "Multi-Column Dynamic Ranking",
        """EVALUATE
ADDCOLUMNS(
    SUMMARIZECOLUMNS(
        'Product'[Category],
        'Date'[Year],
        \"Total Sales\", [Total Sales],
        \"Profit Margin\", [Profit Margin]
    ),
    \"Sales Rank\",
    RANKX(
        ALL('Product'[Category]),
        [Total Sales],
        ,
        DESC,
        DENSE
    )
)
ORDER BY 'Date'[Year], [Sales Rank]""",
    ),
    DAXCase(
        "Prior State with EARLIER",
        """Current Type = 
CALCULATE(
    MAX(Changes[new_value]),
    FILTER(Changes, Changes[member_id] = EARLIER(Changes[member_id])),
    FILTER(Changes, Changes[change_type] = \"Type\"),
    FILTER(Changes, Changes[start_date] = CALCULATE(
        MAX(Changes[start_date]),
        FILTER(CALCULATETABLE(Changes,
               FILTER(Changes, Changes[member_id] = EARLIEST(Changes[member_id])),
               FILTER(Changes, Changes[change_type] = \"Type\")),
               Changes[start_date] < EARLIEST(Changes[start_date])
        )
    ))
)""",
    ),
    DAXCase(
        "SUMMARIZECOLUMNS with Filters",
        """EVALUATE
SUMMARIZECOLUMNS(
    'Customer'[Segment],
    'Date'[Year],
    FILTER(VALUES('Customer'[Segment]), [Segment] IN { \"Premium\", \"Enterprise\" }),
    FILTER('Date', [Year] >= 2020),
    \"Total Revenue\", SUM('Sales'[Revenue]),
    \"Avg Order Value\", DIVIDE([Total Revenue], [Order Count])
)
ORDER BY 'Date'[Year] DESC""",
    ),
    DAXCase("Total Sales", "SUM(Sales[SalesAmount])"),
    DAXCase("Total Cost", "SUM(Sales[CostAmount])"),
    DAXCase("Profit", "[Total Sales] - [Total Cost]"),
    DAXCase("Profit Margin %", "DIVIDE([Profit], [Total Sales])"),
    DAXCase("Order Count", "DISTINCTCOUNT(Sales[OrderID])"),
    DAXCase("Customer Count", "DISTINCTCOUNT(Sales[CustomerID])"),
    DAXCase("Average Order Value", "DIVIDE([Total Sales], [Order Count])"),
    DAXCase("Sales YTD", "TOTALYTD([Total Sales], 'Date'[Date])"),
    DAXCase("Sales MTD", "TOTALMTD([Total Sales], 'Date'[Date])"),
    DAXCase("Sales QTD", "TOTALQTD([Total Sales], 'Date'[Date])"),
    DAXCase("Sales LY", "CALCULATE([Total Sales], SAMEPERIODLASTYEAR('Date'[Date]))"),
    DAXCase("YoY Growth %", "DIVIDE([Total Sales] - [Sales LY], [Sales LY])"),
    DAXCase("Sales PM", "CALCULATE([Total Sales], DATEADD('Date'[Date], -1, MONTH))"),
    DAXCase("Rolling 3M Sales", "CALCULATE([Total Sales], DATESINPERIOD('Date'[Date], MAX('Date'[Date]), -3, MONTH))"),
    DAXCase("Rolling 12M Sales", "CALCULATE([Total Sales], DATESINPERIOD('Date'[Date], MAX('Date'[Date]), -12, MONTH))"),
    DAXCase("Sales All Products", "CALCULATE([Total Sales], ALL(Products))"),
    DAXCase("Sales Contribution %", "DIVIDE([Total Sales], [Sales All Products])"),
    DAXCase("Sales Ignore Date", "CALCULATE([Total Sales], REMOVEFILTERS('Date'))"),
    DAXCase("Sales by Country", "CALCULATE([Total Sales], ALLEXCEPT(Customers, Customers[Country]))"),
    DAXCase("Tech Sales", "CALCULATE([Total Sales], KEEPFILTERS(Products[Category] = \"Technology\"))"),
    DAXCase("Product Rank", "RANKX(ALL(Products[ProductName]), [Total Sales], , DESC)"),
    DAXCase("Top 10 Product Sales", "CALCULATE([Total Sales], TOPN(10, Products, [Total Sales], DESC))"),
    DAXCase("Bottom 5 Product Sales", "CALCULATE([Total Sales], TOPN(5, Products, [Total Sales], ASC))"),
    DAXCase("Running Total", "CALCULATE([Total Sales], FILTER(ALL('Date'), 'Date'[Date] <= MAX('Date'[Date])))"),
    DAXCase(
        "Cumulative Product Sales",
        "CALCULATE([Total Sales], FILTER(ALL(Products), Products[ProductName] <= MAX(Products[ProductName])))",
    ),
    DAXCase(
        "Pareto %",
        "VAR TotalSalesAll = CALCULATE([Total Sales], ALL(Products)) VAR ProductSales = ADDCOLUMNS(ALL(Products), \"Sales\", [Total Sales]) VAR CumSales = SUMX(FILTER(ProductSales, [Sales] >= EARLIER([Sales])), [Sales]) RETURN DIVIDE(CumSales, TotalSalesAll)",
    ),
    DAXCase("Sales Variance", "[Total Sales] - [Sales Target]"),
    DAXCase("Sales Achievement %", "DIVIDE([Total Sales], [Sales Target])"),
    DAXCase("Sales Status", "IF([Total Sales] >= [Sales Target], \"Achieved\", \"Not Achieved\")"),
    DAXCase("Avg Sales per Customer", "DIVIDE([Total Sales], [Customer Count])"),
    DAXCase("High Value Sales", "CALCULATE([Total Sales], FILTER(Customers, [Total Sales] > 50000))"),
    DAXCase("Sales Selected Year", "CALCULATE([Total Sales], ALL('Date'), VALUES('Date'[Year]))"),
    DAXCase("Max Sales Day", "MAXX(VALUES('Date'[Date]), [Total Sales])"),
    DAXCase("Min Sales Day", "MINX(VALUES('Date'[Date]), [Total Sales])"),
    DAXCase("Avg Monthly Sales", "AVERAGEX(VALUES('Date'[Month]), [Total Sales])"),
    DAXCase("Sales No Discount", "CALCULATE([Total Sales], Sales[Discount] = 0)"),
    DAXCase("Discounted Sales", "CALCULATE([Total Sales], Sales[Discount] > 0)"),
    DAXCase("Discount Impact %", "DIVIDE([Discounted Sales], [Total Sales])"),
    DAXCase("First Purchase Date", "MIN(Sales[OrderDate])"),
    DAXCase("Last Purchase Date", "MAX(Sales[OrderDate])"),
    DAXCase("Inactive Customers", "CALCULATE(DISTINCTCOUNT(Customers[CustomerID]), FILTER(Customers, ISBLANK([Total Sales])))"),
    DAXCase("Sales by Ship Date", "CALCULATE([Total Sales], USERELATIONSHIP(Sales[ShipDate], 'Date'[Date]))"),
    DAXCase(
        "Dynamic Sales",
        "SWITCH(SELECTEDVALUE(Metrics[Metric]), \"Sales\", [Total Sales], \"Profit\", [Profit], \"Quantity\", SUM(Sales[Quantity]))",
    ),
    DAXCase("Sales by Region", "CALCULATE([Total Sales], TREATAS(VALUES(Region[Region]), Customers[Region]))"),
    DAXCase("Sales BI Direction", "CALCULATE([Total Sales], CROSSFILTER(Sales[CustomerID], Customers[CustomerID], BOTH))"),
    DAXCase("Last Sales Value", "LASTNONBLANK('Date'[Date], [Total Sales])"),
    DAXCase("Diff from Avg %", "DIVIDE([Total Sales] - AVERAGE([Total Sales]), AVERAGE([Total Sales]))"),
    DAXCase("Sales Index", "DIVIDE([Total Sales], CALCULATE([Total Sales], ALL('Date')))"),
    DAXCase("Sales Per Day", "DIVIDE([Total Sales], DISTINCTCOUNT('Date'[Date]))"),
    DAXCase("Sales Color", "IF([Total Sales] >= [Sales Target], \"Green\", \"Red\")"),
]


TARGETS = {
    "databricks": SQLDialect.DATABRICKS,
    "snowflake": SQLDialect.SNOWFLAKE,
}


def _sanitize_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return cleaned.strip("._-") or "metric"


def _extract_schema_context(dax: str) -> dict[str, list[str]]:
    table_columns: dict[str, set[str]] = {}

    for quoted_table, bare_table, column in re.findall(r"(?i)(?:'([^']+)'|([A-Za-z_][\w]*))\s*\[([^\]]+)\]", dax):
        table_name = quoted_table or bare_table
        if not table_name:
            continue
        table_columns.setdefault(table_name, set()).add(column)

    for table in re.findall(
        r"(?i)\b(?:ALL|ALLEXCEPT|ADDCOLUMNS|CALCULATETABLE|FILTER|KEEPFILTERS|REMOVEFILTERS|TOPN|TREATAS|VALUES|SUMMARIZECOLUMNS|SUMMARIZE|CROSSFILTER|USERELATIONSHIP)\s*\(\s*'([^']+)'",
        dax,
    ):
        table_columns.setdefault(table, set())

    for table in re.findall(
        r"(?i)\b(?:ALL|ALLEXCEPT|ADDCOLUMNS|CALCULATETABLE|FILTER|KEEPFILTERS|REMOVEFILTERS|TOPN|TREATAS|VALUES|SUMMARIZECOLUMNS|SUMMARIZE|CROSSFILTER|USERELATIONSHIP)\s*\(\s*([A-Za-z_][\w]*)",
        dax,
    ):
        table_columns.setdefault(table, set())

    return {table: sorted(cols) for table, cols in table_columns.items()}


def _classify_dax(dax: str) -> list[str]:
    upper = dax.upper()
    tags: list[str] = []
    if upper.lstrip().startswith("EVALUATE"):
        tags.append("query")
    if any(token in upper for token in ["TOTALYTD", "TOTALMTD", "TOTALQTD", "SAMEPERIODLASTYEAR", "DATEADD", "DATESINPERIOD"]):
        tags.append("time-intelligence")
    if any(token in upper for token in ["RANKX", "MAXX", "MINX", "AVERAGEX", "SUMX", "TOPN"]):
        tags.append("iterator/ranking")
    if any(token in upper for token in ["EARLIER", "EARLIEST", "CALCULATETABLE", "ADDCOLUMNS", "SUMMARIZECOLUMNS"]):
        tags.append("context-transition/virtual-table")
    if any(token in upper for token in ["ALL(", "ALLEXCEPT", "REMOVEFILTERS", "KEEPFILTERS", "TREATAS", "CROSSFILTER", "USERELATIONSHIP"]):
        tags.append("filter-context")
    if any(token in upper for token in ["VAR ", "RETURN", "SWITCH", "SELECTEDVALUE", "LASTNONBLANK"]):
        tags.append("branching/scalar")
    return tags


def _build_translator(dialect: SQLDialect, provider_order: list[str] | None) -> CommonDAXTranslator:
    return CommonDAXTranslator(
        dialect=dialect,
        provider_order=provider_order,
        cache_enabled=False,
        fallback_to_placeholder=False,
        placeholder_sql="CAST(NULL AS DOUBLE)",
        raw_response_dir=ROOT / "output" / "debug" / "llm_dax_raw",
    )


def _normalize_deterministic_sql(sql: str, dialect_name: str) -> str:
    sql = str(sql or "").strip()
    if dialect_name == "databricks":
        sql = re.sub(r'"([^"\\]+)"', r"`\1`", sql)
    return sql


def _is_deployable_sql(sql: str) -> bool:
    text = str(sql or "").strip()
    if not text:
        return False
    upper = text.upper()
    if any(token in upper for token in ("[", "]", "MEASURE_VALUE", "CAST(NULL AS DOUBLE)", "CAST(NULL AS FLOAT)")):
        return False
    if upper.startswith(("SELECT ", "WITH ")):
        return False
    return True


def _deterministic_translate(case: DAXCase, dialect_name: str) -> str | None:
    dax = case.dax.strip()
    if dax.upper().lstrip().startswith("EVALUATE"):
        return None
    parser = DaxAstParser()
    renderer = DaxSqlRenderer(table_alias="fact", date_alias="date")
    try:
        ast = parser.parse(dax)
        sql = renderer.render(ast)
        if not sql:
            return None
        return _normalize_deterministic_sql(sql, dialect_name)
    except Exception:
        return None


def _translate_case(
    *,
    case: DAXCase,
    dialect_name: str,
    translator: CommonDAXTranslator,
    allow_llm: bool,
) -> dict[str, Any]:
    schema_context = _extract_schema_context(case.dax)
    rule_sql = rule_based_translation(case.dax, table_alias="fact", metric_name=case.name, dialect=dialect_name)
    if rule_sql and _is_deployable_sql(rule_sql):
        return {
            "metric_name": case.name,
            "dialect": dialect_name,
            "tags": _classify_dax(case.dax),
            "schema_context": schema_context,
            "translation_source": "rules",
            "translation": {
                "sql": rule_sql,
                "is_valid": True,
                "provider": "deterministic-rules",
                "model": "rule-engine",
                "cached": False,
                "fallback_used": False,
                "error": "",
                "attempted_providers": [],
            },
            "deployable": True,
        }

    deterministic_sql = _deterministic_translate(case, dialect_name)
    if deterministic_sql and _is_deployable_sql(deterministic_sql):
        return {
            "metric_name": case.name,
            "dialect": dialect_name,
            "tags": _classify_dax(case.dax),
            "schema_context": schema_context,
            "translation_source": "ast",
            "translation": {
                "sql": deterministic_sql,
                "is_valid": True,
                "provider": "deterministic",
                "model": "local-rules",
                "cached": False,
                "fallback_used": False,
                "error": "",
                "attempted_providers": [],
            },
            "deployable": True,
        }

    if not allow_llm:
        return {
            "metric_name": case.name,
            "dialect": dialect_name,
            "tags": _classify_dax(case.dax),
            "schema_context": schema_context,
            "translation_source": "unsupported",
            "translation": {
                "sql": "",
                "is_valid": False,
                "provider": "",
                "model": "",
                "cached": False,
                "fallback_used": False,
                "error": "No deterministic rule matched",
                "attempted_providers": [],
            },
            "deployable": False,
        }

    result = translator.translate(
        dax=case.dax,
        metric_name=case.name,
        dataset_name="Tier6 Audit",
        table_alias="fact",
        schema_context=schema_context,
    )
    return {
        "metric_name": case.name,
        "dialect": dialect_name,
        "tags": _classify_dax(case.dax),
        "schema_context": schema_context,
        "translation_source": "llm",
        "translation": {
            "sql": result.sql or "",
            "is_valid": bool(result.is_valid),
            "provider": result.provider,
            "model": result.model,
            "cached": bool(result.cached),
            "fallback_used": bool(result.fallback_used),
            "error": result.error,
            "attempted_providers": list(result.attempted_providers),
        },
        "deployable": bool(result.is_valid and result.sql and not result.fallback_used),
    }


def _render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Tier 6 DAX Translation Audit")
    lines.append("")
    lines.append(f"- Generated: {report['generated_at']}")
    lines.append(f"- Cases: {report['summary']['case_count']}")
    lines.append(f"- Targets: {', '.join(report['summary']['targets'])}")
    lines.append("")
    lines.append("## Summary")
    for target, stats in report["summary"]["by_target"].items():
        lines.append(
            f"- {target}: translated={stats['translated']} deployable={stats['deployable']} failed={stats['failed']} fallback={stats['fallback']}"
        )
    lines.append("")
    lines.append("## Results")
    for item in report["results"]:
        lines.append(f"### {item['metric_name']} ({item['dialect']})")
        if item["tags"]:
            lines.append(f"- Tags: {', '.join(item['tags'])}")
        lines.append(f"- Deployable: {item['deployable']}")
        translation = item["translation"]
        lines.append(f"- Valid: {translation['is_valid']}")
        lines.append(f"- Fallback used: {translation['fallback_used']}")
        if translation["error"]:
            lines.append(f"- Error: {translation['error']}")
        lines.append("")
        lines.append("```sql")
        lines.append(translation["sql"] or "-- no sql generated")
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Translate complex DAX samples and save an audit report.")
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "output" / "dax_audit"),
        help="Directory where JSON/Markdown/SQL artifacts will be saved.",
    )
    parser.add_argument(
        "--targets",
        nargs="+",
        default=["databricks", "snowflake"],
        choices=sorted(TARGETS.keys()),
        help="Targets to translate for.",
    )
    parser.add_argument(
        "--provider-order",
        default="",
        help="Comma-separated provider order override, e.g. groq,gemini,deepseek.",
    )
    parser.add_argument(
        "--input-file",
        default="",
        help="Optional JSON file with [{\"name\":..., \"dax\":...}] entries. Defaults to built-in cases.",
    )
    parser.add_argument(
        "--allow-llm",
        action="store_true",
        help="Allow LLM fallback after deterministic rules fail.",
    )
    args = parser.parse_args()

    provider_order = [item.strip() for item in args.provider_order.split(",") if item.strip()] or None
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.input_file:
        raw = json.loads(Path(args.input_file).read_text(encoding="utf-8"))
        cases = [DAXCase(name=str(item["name"]), dax=str(item["dax"])) for item in raw]
    else:
        cases = DAX_CASES

    translators = {
        target: _build_translator(TARGETS[target], provider_order)
        for target in args.targets
    } if args.allow_llm else {}

    results: list[dict[str, Any]] = []
    for case in cases:
        for target in args.targets:
            result = _translate_case(
                case=case,
                dialect_name=target,
                translator=translators.get(target),
                allow_llm=args.allow_llm,
            )
            results.append(result)

    summary_by_target: dict[str, dict[str, int]] = {}
    for target in args.targets:
        items = [item for item in results if item["dialect"] == target]
        summary_by_target[target] = {
            "translated": sum(1 for item in items if item["translation"]["is_valid"]),
            "deployable": sum(1 for item in items if item["deployable"]),
            "failed": sum(1 for item in items if not item["translation"]["is_valid"]),
            "fallback": sum(1 for item in items if item["translation"]["fallback_used"]),
        }

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "case_count": len(cases),
        "results": results,
        "summary": {
            "case_count": len(cases),
            "targets": args.targets,
            "by_target": summary_by_target,
        },
    }

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"tier6_dax_audit_{ts}.json"
    md_path = output_dir / f"tier6_dax_audit_{ts}.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(report), encoding="utf-8")

    sql_root = output_dir / f"sql_{ts}"
    sql_root.mkdir(parents=True, exist_ok=True)
    for item in results:
        target_dir = sql_root / item["dialect"]
        target_dir.mkdir(parents=True, exist_ok=True)
        file_name = _sanitize_filename(item["metric_name"])
        sql_file = target_dir / f"{file_name}.sql"
        translation = item["translation"]
        content_lines = [
            f"-- Metric: {item['metric_name']}",
            f"-- Target: {item['dialect']}",
            f"-- Deployable: {item['deployable']}",
            f"-- Valid: {translation['is_valid']}",
            f"-- Fallback used: {translation['fallback_used']}",
        ]
        if translation["error"]:
            content_lines.append(f"-- Error: {translation['error']}")
        content_lines.append(translation["sql"] or "-- no sql generated")
        sql_file.write_text("\n".join(content_lines) + "\n", encoding="utf-8")

    print(f"Saved audit report to: {json_path}")
    print(f"Saved markdown summary to: {md_path}")
    print(f"Saved SQL artifacts under: {sql_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())