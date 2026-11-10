"""O11 - 56-question benchmark across Human Resources, Sales & Returns, and Arrow Chart (Financials).

Runs every question from the benchmark list against /api/ai/nl-query scoped by project_id,
records detected metrics, detected dimensions, confidence, warnings, and generated SQL,
and evaluates query routing, metric/dimension matching, and negative-case handling.

Usage:
    uv run python ontology_tests/o11_benchmark_3_models.py --base http://127.0.0.1:8002
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import Api, base_args, body_text  # noqa: E402

HR_QUESTIONS = [
    ("HR-01", "How many employees do we have?", "Simple", "Empcount / Actives"),
    ("HR-02", "How many active employees are there?", "Simple", "Actives"),
    ("HR-03", "How many separations did we have?", "Simple", "Seps"),
    ("HR-04", "How many new hires this year?", "Simple", "New Hires"),
    ("HR-05", "What is the average age of employees?", "Simple", "Avg Age"),
    ("HR-06", "What is the average tenure in months?", "Simple", "Avg Tenure Months"),
    ("HR-07", "Show active employees by business unit.", "By dim", "Actives + BU"),
    ("HR-08", "New hires by region.", "By dim", "New Hires + Region"),
    ("HR-09", "Separations by separation reason.", "By dim", "Seps + SeparationType/Reason"),
    ("HR-10", "Headcount by gender and ethnicity.", "By dim", "Empcount + Gender, Ethnicity"),
    ("HR-11", "Average tenure by age group.", "By dim", "Avg Tenure + AgeGroup"),
    ("HR-12", "Bad hires by pay type.", "By dim", "Sum Of Badhires + PayType"),
    ("HR-13", "How many new hires compared to the same period last year?", "Time", "New Hires vs New Hires Sply"),
    ("HR-14", "What is the year-over-year change in separations?", "Time", "Seps YoY"),
    ("HR-15", "What is the turnover rate by VP?", "Time", "TO % + VP"),
    ("HR-16", "Show headcount by month.", "Time", "Empcount/Actives + Month (not summed)"),
    ("HR-17", "How many people left the company?", "Synonym", "Seps (synonym gap expected)"),
    ("HR-18", "What's our attrition rate?", "Synonym", "TO % (synonym gap expected)"),
    ("HR-19", "Total staff by department?", "Synonym", "Empcount + BU (synonym gap expected)"),
    ("HR-20", "What is the average salary by department?", "Not in model", "NONE / warning"),
]

SR_QUESTIONS = [
    ("SR-01", "What are the total net sales?", "Simple", "Net Sales"),
    ("SR-02", "How many units were sold?", "Simple", "Units Sold"),
    ("SR-03", "What is the total returns amount?", "Simple", "Returns"),
    ("SR-04", "What is the return rate?", "Simple", "% Return Rate"),
    ("SR-05", "Net sales by product category.", "By dim", "Net Sales + Category"),
    ("SR-06", "Units sold by store.", "By dim", "Units Sold + Store"),
    ("SR-07", "Returns by store type.", "By dim", "Returns + Store Type"),
    ("SR-08", "Return rate by product segment.", "By dim", "% Return Rate + Segment"),
    ("SR-09", "Net sales by customer gender.", "By dim", "Net Sales + Gender"),
    ("SR-10", "Units returned by issue.", "By dim", "Units Returned + Issue"),
    ("SR-11", "Net sales this month vs previous month.", "Time", "Net Sales vs Net Sales PM"),
    ("SR-12", "What is the net sales variance %?", "Time", "Net Sales Variance %"),
    ("SR-13", "Show net sales for the last 2 months.", "Time", "Last 2 Months Net Sales"),
    ("SR-14", "Returns by month.", "Time", "Returns + Month"),
    ("SR-15", "Which products had the highest return rate?", "Time", "% Return Rate + Product"),
    ("SR-16", "How much did we sell?", "Synonym", "Net Sales (synonym gap)"),
    ("SR-17", "How many items were sent back?", "Synonym", "Units Returned (synonym gap)"),
    ("SR-18", "Top 3 stores by sales.", "Synonym", "Top N / Sales + Store + LIMIT 3"),
    ("SR-19", "What is the profit margin by product?", "Not in model", "NONE / warning"),
]

AC_QUESTIONS = [
    ("AC-01", "What are total sales?", "Simple", "Sales"),
    ("AC-02", "What is the total profit?", "Simple", "Profit"),
    ("AC-03", "How many units were sold?", "Simple", "Units Sold"),
    ("AC-04", "Sales by country.", "By dim", "Sales + Country"),
    ("AC-05", "Profit by segment.", "By dim", "Profit + Segment"),
    ("AC-06", "Sales by product and discount band.", "By dim", "Sales + Product, Discount Band"),
    ("AC-07", "COGS by country.", "By dim", "COGS + Country"),
    ("AC-08", "What are the year-to-date sales?", "Time", "Ytd Sales"),
    ("AC-09", "Sales vs previous year.", "Time", "Sales + Py Sales"),
    ("AC-10", "Year-over-year sales growth %.", "Time", "Yoy Pct Sales"),
    ("AC-11", "Quarter-over-quarter sales change.", "Time", "Qoq Sales / Qoq Pct Sales"),
    ("AC-12", "Month-to-date sales vs previous month.", "Time", "Mtd Sales + Pm Sales"),
    ("AC-13", "Moving annual total sales.", "Time", "Mat Sales"),
    ("AC-14", "What's our revenue by country?", "Synonym", "Sales (synonym gap)"),
    ("AC-15", "Total discounts given by segment?", "Synonym", "Discounts + Segment"),
    ("AC-16", "How are sales trending this year?", "Synonym", "Sales / Ytd Sales + note"),
    ("AC-17", "What is the customer churn rate?", "Not in model", "NONE / warning"),
]

MODELS = [
    ("Human Resources", "proj-human-resources-sample", "HUMAN_RESOURCES_SAMPLE_PBIX_SEMANTIC", HR_QUESTIONS),
    ("Sales & Returns", "proj-sales-and-returns", "SALES__RETURNS_SAMPLE_V201912_SEMANTIC", SR_QUESTIONS),
    ("Arrow Chart (Financials)", "proj-arrow-chart-microsot-financial-dataset", "ARROW_CHART__MICROSOT_FINANCIAL_DATASET_SEMANTIC", AC_QUESTIONS),
]

p = base_args(__doc__)
a = p.parse_args()
api = Api(a.base, a.token)

all_results = {}
for model_title, pid, expected_sv, qlist in MODELS:
    print(f"\n==================== {model_title} ({pid}) ====================")
    model_rows = []
    for qid, qtext, cat, expect_note in qlist:
        r = api.post("/api/ai/nl-query", {"prompt": qtext, "target": "SNOWFLAKE", "project_id": pid})
        if not r.ok:
            print(f"  [{qid}] HTTP {r.status_code}: {body_text(r)[:120]}")
            model_rows.append({"id": qid, "error": r.status_code})
            continue
        plan = r.json()
        sql = plan.get("generated_sql", "")
        body = " ".join(l.strip() for l in sql.splitlines() if l.strip() and not l.strip().startswith("--"))
        uses_sv = f"SEMANTIC_VIEW({expected_sv}" in body
        metrics = plan.get("detected_metrics", [])
        dims = plan.get("detected_dimensions", [])
        conf = plan.get("confidence_score", 0.0)
        warns = plan.get("warnings", [])
        print(
            f"  [{qid:5s}] ({cat:12s}) Q: {qtext}\n"
            f"          -> Metrics={metrics} | Dims={dims} | Conf={conf} | SV={uses_sv}\n"
            f"          -> Expected: {expect_note}"
            + (f"\n          -> Warnings: {warns}" if warns else "")
        )
        model_rows.append({
            "id": qid,
            "category": cat,
            "question": qtext,
            "expected": expect_note,
            "detected_metrics": metrics,
            "detected_dimensions": dims,
            "confidence": conf,
            "uses_expected_sv": uses_sv,
            "warnings": warns,
            "sql": sql,
        })
    all_results[model_title] = model_rows

out = Path("ontology_tests/out")
out.mkdir(parents=True, exist_ok=True)
(out / "o11_benchmark_3_models.json").write_text(json.dumps(all_results, indent=2), encoding="utf-8")
print(f"\nSaved full benchmark results to {out / 'o11_benchmark_3_models.json'}")

