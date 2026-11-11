"""O10 - Multi-model validation & regression suite for Ontology + AI Query Engine.

Validates across all local domains / projects against a running SemaBridge API:
  1. Metadata quality: no DAX in sql_expression, aggregation preserved, no fake synonyms.
  2. Open Issue 1 (Metric precision): singular vs comparison intent, ambiguity warning.
  3. Open Issue 2 (Semantic View routing): BI-backed domains use SEMANTIC_VIEW(...) even without project_id.
  4. Open Issue 3 (Count / Rate / MoM intent): 'How many cases by product?' -> Case Count + Product.
  5. Cross-model validation: Adventure Works DW 2020, Arrow Chart Financial Dataset, Artificial Intelligence Sample.
  6. Cross-domain isolation & negative tests: unmatched metrics produce low confidence and zero phantom joins.

Usage:
    uv run python ontology_tests/o10_multi_model_validation.py --base http://127.0.0.1:8001
"""
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent))
from common import Api, Report, base_args, body_text  # noqa: E402

DAX_TOKENS = re.compile(
    r"\b(CALCULATE|CALCULATETABLE|COUNTROWS|DIVIDE|SUMX|AVERAGEX|SELECTEDVALUE|ALLEXCEPT|ALLSELECTED|"
    r"DATEADD|SAMEPERIODLASTYEAR|DATESYTD|TOTALYTD|RELATED|USERELATIONSHIP|HASONEVALUE)\s*\(|"
    r"\bVAR\s+\w+\s*=|\bRETURN\b|'[^']+'\[[^\]]+\]",
    re.I,
)


def nm(x):
    return re.sub(r"[^0-9a-z%]", "", str(x).casefold())


p = base_args(__doc__)
a = p.parse_args()
api = Api(a.base, a.token)
rep = Report("O10 Multi-Model & Regression Validation")
results = {"metadata": [], "queries": []}

# ------------------------------------------------------------------ 1. Metadata Audit Across All Local Domains
doms_resp = api.get("/api/ontology/domains")
if not rep.check(doms_resp.ok, "GET /api/ontology/domains returns 200", body_text(doms_resp)[:200]):
    sys.exit(rep.done())

domains = doms_resp.json().get("domains", [])
for d in domains:
    d_name = d["domain"]
    g = api.get("/api/ontology/domains/" + quote(d_name, safe=""))
    if not rep.check(g.ok, f"[{d_name}] domain detail readable"):
        continue
    parsed = g.json().get("parsed", {})
    metrics = parsed.get("metrics", [])

    dax_leaks = [m["name"] for m in metrics if m.get("sql_expression") and DAX_TOKENS.search(m["sql_expression"])]
    rep.check(
        not dax_leaks,
        f"[{d_name}] 0 metrics have raw DAX leaked into sql_expression",
        f"DAX found in: {dax_leaks[:5]}",
    )

    # Check for legacy artificial title-casing (e.g. 'Cases Mompct', 'Forecast Pct')
    bad_syns = []
    for m in metrics:
        for s in m.get("synonyms") or []:
            if s.endswith(" Pct") or "Mompct" in s:
                bad_syns.append(f"{m['name']}:{s}")
    rep.check(
        not bad_syns,
        f"[{d_name}] 0 synthetic title-case synonyms (Pct/Mompct)",
        f"Found: {bad_syns[:5]}",
    )

    results["metadata"].append({
        "domain": d_name,
        "source_project_id": parsed.get("source_project_id"),
        "metrics_count": len(metrics),
        "concepts_count": len(parsed.get("concepts", [])),
        "sql_populated": sum(1 for m in metrics if m.get("sql_expression")),
        "dax_leaks": dax_leaks,
    })

# ------------------------------------------------------------------ 2. Golden Query Matrix Across Models
CASES = [
    # --- Artificial Intelligence Sample (with & without project_id) ---
    {
        "label": "Open Issue 1: singular total revenue",
        "prompt": "What's the total revenue?",
        "project_id": "proj-ai",
        "expect_metrics_exact": ["Revenue Won"],
        "expect_sv": "ARTIFICIAL_INTELLIGENCE_SAMPLE_SEMANTIC",
        "expect_warning": True,
    },
    {
        "label": "Open Issue 1b: comparison won and open revenue",
        "prompt": "Compare revenue won and revenue open",
        "project_id": "proj-ai",
        "expect_metrics_all": ["Revenue Won", "Revenue Open"],
        "expect_sv": "ARTIFICIAL_INTELLIGENCE_SAMPLE_SEMANTIC",
    },
    {
        "label": "Open Issue 2: global query without project_id still uses SEMANTIC_VIEW",
        "prompt": "What's the total revenue?",
        "project_id": None,
        "expect_metrics_exact": ["Revenue Won"],
        "expect_sv": "ARTIFICIAL_INTELLIGENCE_SAMPLE_SEMANTIC",
    },
    {
        "label": "Open Issue 3: how many cases are there by product?",
        "prompt": "How many cases are there by product?",
        "project_id": "proj-ai",
        "expect_metrics_exact": ["Case Count"],
        "expect_dims": ["Product"],
        "forbid_dims": ["ProductKey", "ProductSeq"],
        "expect_sv": "ARTIFICIAL_INTELLIGENCE_SAMPLE_SEMANTIC",
    },
    {
        "label": "Open Issue 3b: how many cases by product?",
        "prompt": "How many cases by product?",
        "project_id": "proj-ai",
        "expect_metrics_exact": ["Case Count"],
        "expect_dims": ["Product"],
        "expect_sv": "ARTIFICIAL_INTELLIGENCE_SAMPLE_SEMANTIC",
    },
    {
        "label": "Q6: which product has the most cases?",
        "prompt": "Which product has the most cases?",
        "project_id": "proj-ai",
        "expect_metrics_exact": ["Case Count"],
        "expect_dims": ["Product"],
        "expect_sv": "ARTIFICIAL_INTELLIGENCE_SAMPLE_SEMANTIC",
    },
    {
        "label": "Q4: escalation rate by subject",
        "prompt": "What is the escalation rate by subject?",
        "project_id": "proj-ai",
        "expect_metrics_exact": ["Escalations"],
        "expect_dims": ["Subject"],
        "expect_sv": "ARTIFICIAL_INTELLIGENCE_SAMPLE_SEMANTIC",
    },
    {
        "label": "Q8: month-over-month change in cases",
        "prompt": "What is the month-over-month change in cases?",
        "project_id": "proj-ai",
        "expect_metrics_exact": ["Cases MoM%"],
        "expect_sv": "ARTIFICIAL_INTELLIGENCE_SAMPLE_SEMANTIC",
    },
    {
        "label": "Q9: percentage of cases each product has",
        "prompt": "What percentage of cases does each product have?",
        "project_id": "proj-ai",
        "expect_metrics_all": ["Cases % by Product"],
        "expect_dims": ["Product"],
        "expect_sv": "ARTIFICIAL_INTELLIGENCE_SAMPLE_SEMANTIC",
    },
    {
        "label": "Q16: opportunities won and lost",
        "prompt": "How many opportunities were won and lost?",
        "project_id": "proj-ai",
        "expect_metrics_all": ["Count of Won", "Count of Lost"],
        "expect_sv": "ARTIFICIAL_INTELLIGENCE_SAMPLE_SEMANTIC",
    },
    # --- Adventure Works DW 2020 ---
    {
        "label": "AW: sales amount by product",
        "prompt": "Show sales amount by product",
        "project_id": "proj-adventure-works-dw-2020",
        "expect_metrics_any": ["Sales - Sum Of Sales Amount", "Sales - Sum of Sales Amount"],
        "expect_dims": ["Product"],
        "expect_sv": "ADVENTURE_WORKS_DW_2020_SEMANTIC",
    },
    {
        "label": "AW: order quantity by customer",
        "prompt": "What is the order quantity by customer?",
        "project_id": "proj-adventure-works-dw-2020",
        "expect_metrics_any": ["Sales - Sum Of Order Quantity", "Sales - Sum of Order Quantity"],
        "expect_dims": ["Customer"],
        "expect_sv": "ADVENTURE_WORKS_DW_2020_SEMANTIC",
    },
    # --- Arrow Chart Financial Dataset ---
    {
        "label": "Arrow: sales by country",
        "prompt": "What are total sales by country?",
        "project_id": "proj-arrow-chart-microsot-financial-dataset",
        "expect_metrics_exact": ["Sales"],
        "expect_dims": ["Country"],
        "expect_sv": "ARROW_CHART__MICROSOT_FINANCIAL_DATASET_SEMANTIC",
    },
    {
        "label": "Arrow: YoY percentage sales by segment",
        "prompt": "Show YoY percentage sales by segment",
        "project_id": "proj-arrow-chart-microsot-financial-dataset",
        "expect_metrics_any": ["Yoy Pct Sales", "Yoy Sales"],
        "expect_dims": ["Segment"],
        "expect_sv": "ARROW_CHART__MICROSOT_FINANCIAL_DATASET_SEMANTIC",
    },
    # --- Human Resources Sample PBIX ---
    {
        "label": "HR: new hires by region",
        "prompt": "Show new hires by region",
        "project_id": "proj-human-resources-sample",
        "expect_metrics_exact": ["New Hires"],
        "expect_dims": ["Region"],
        "expect_sv": "HUMAN_RESOURCES_SAMPLE_PBIX_SEMANTIC",
    },
    {
        "label": "HR: avg age by gender",
        "prompt": "What is the avg age by gender?",
        "project_id": "proj-human-resources-sample",
        "expect_metrics_exact": ["Avg Age"],
        "expect_dims": ["Gender"],
        "expect_sv": "HUMAN_RESOURCES_SAMPLE_PBIX_SEMANTIC",
    },
    # --- Sales & Returns Sample v201912 ---
    {
        "label": "Sales & Returns: net sales by category",
        "prompt": "Show net sales by category",
        "project_id": "proj-sales-and-returns",
        "expect_metrics_exact": ["Net Sales"],
        "expect_dims": ["Category"],
        "expect_sv": "SALES__RETURNS_SAMPLE_V201912_SEMANTIC",
    },
    {
        "label": "Sales & Returns: units sold by product",
        "prompt": "What are units sold by product?",
        "project_id": "proj-sales-and-returns",
        "expect_metrics_exact": ["Units Sold"],
        "expect_dims": ["Product"],
        "expect_sv": "SALES__RETURNS_SAMPLE_V201912_SEMANTIC",
    },
    # --- Store Sales 1 ---
    {
        "label": "Store Sales: gross margin this year by district",
        "prompt": "Show gross margin this year by district",
        "project_id": "proj-store-sales",
        "expect_metrics_exact": ["Gross Margin This Year"],
        "expect_dims": ["District"],
        "expect_sv": "STORE_SALES_1_SEMANTIC",
    },
    {
        "label": "Store Sales: regular sales dollars by territory",
        "prompt": "Show regular sales dollars by territory",
        "project_id": "proj-store-sales",
        "expect_metrics_exact": ["Regular Sales Dollars"],
        "expect_dims": ["Territory"],
        "expect_sv": "STORE_SALES_1_SEMANTIC",
    },
    # --- Negative / Cross-Domain Isolation ---
    {
        "label": "Cross-domain isolation (Adventure Works): gross margin & ARR by Customer in 2024",
        "prompt": "What is the gross margin and ARR by Customer in 2024?",
        "project_id": "proj-adventure-works-dw-2020",
        "expect_no_metrics": True,
        "max_confidence": 0.35,
        "forbid_sql_tokens": ["Accounts", "Contacts", "Postal Code", "ANALYTICS", "STORE_SALES"],
        "expect_sql_tokens": ["ADVENTURE_WORKS_DW_2020_SEMANTIC DIMENSIONS Customer", "YEAR("],
    },
    {
        "label": "Negative test: unmatched unique sales",
        "prompt": "what's the unique sales?",
        "project_id": "proj-ai",
        "expect_no_metrics": True,
        "max_confidence": 0.35,
        "expect_warning": True,
    },
]

for tc in CASES:
    label = tc["label"]
    payload = {"prompt": tc["prompt"], "target": "SNOWFLAKE"}
    if tc.get("project_id") is not None:
        payload["project_id"] = tc["project_id"]

    r = api.post("/api/ai/nl-query", payload)
    if not rep.check(r.ok, f"[{label}] /api/ai/nl-query HTTP 200", body_text(r)[:200]):
        continue

    plan = r.json()
    sql = plan.get("generated_sql", "")
    body = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    got_m = plan.get("detected_metrics", [])
    got_d = plan.get("detected_dimensions", [])
    conf = plan.get("confidence_score", 0.0)
    warns = plan.get("warnings", [])

    if "expect_metrics_exact" in tc:
        exp = tc["expect_metrics_exact"]
        rep.check(
            [nm(x) for x in got_m] == [nm(x) for x in exp],
            f"[{label}] exact metrics == {exp}",
            f"got {got_m}",
        )
    if "expect_metrics_all" in tc:
        for exp in tc["expect_metrics_all"]:
            rep.check(
                any(nm(exp) == nm(g) for g in got_m),
                f"[{label}] metric '{exp}' detected",
                f"got {got_m}",
            )
    if "expect_metrics_any" in tc:
        opts = {nm(x) for x in tc["expect_metrics_any"]}
        rep.check(
            any(nm(g) in opts for g in got_m),
            f"[{label}] metric in {tc['expect_metrics_any']}",
            f"got {got_m}",
        )
    if tc.get("expect_no_metrics"):
        rep.check(len(got_m) == 0, f"[{label}] 0 metrics detected", f"got {got_m}")
    if "max_confidence" in tc:
        rep.check(conf <= tc["max_confidence"], f"[{label}] confidence <= {tc['max_confidence']}", f"got {conf}")
    if tc.get("expect_warning"):
        rep.check(len(warns) > 0, f"[{label}] emits explanatory/ambiguity warning", f"warnings={warns}")

    for dim in tc.get("expect_dims", []):
        rep.check(
            any(nm(dim) in nm(d) for d in got_d),
            f"[{label}] dimension '{dim}' detected",
            f"got {got_d}",
        )
    for fdim in tc.get("forbid_dims", []):
        rep.check(
            not any(nm(fdim) == nm(d) for d in got_d),
            f"[{label}] dimension '{fdim}' not picked instead of natural column",
            f"got {got_d}",
        )

    if "expect_sv" in tc:
        sv = tc["expect_sv"]
        rep.check(
            f"SEMANTIC_VIEW({sv}" in body,
            f"[{label}] SQL routes to SEMANTIC_VIEW({sv} ...)",
            f"SQL body:\n{body}",
        )
        rep.check(
            not re.search(r"\bFROM\s+(ANALYTICS|Opportunities|Cases|Sales|financials)\b", body, re.I),
            f"[{label}] SQL does not fall back to raw physical table",
            f"SQL body:\n{body}",
        )

    for tok in tc.get("expect_sql_tokens", []):
        rep.check(tok in body, f"[{label}] SQL contains '{tok}'", f"SQL body:\n{body}")
    for tok in tc.get("forbid_sql_tokens", []):
        rep.check(tok not in body, f"[{label}] SQL forbids '{tok}'", f"SQL body:\n{body}")

    results["queries"].append({
        "label": label,
        "prompt": tc["prompt"],
        "project_id": tc.get("project_id"),
        "detected_metrics": got_m,
        "detected_dimensions": got_d,
        "confidence": conf,
        "warnings": warns,
        "sql_body": body,
    })

out = Path("ontology_tests/out")
out.mkdir(parents=True, exist_ok=True)
(out / "o10_multi_model_validation.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
print(f"  report saved: {out / 'o10_multi_model_validation.json'}")
sys.exit(rep.done())
