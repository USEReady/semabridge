# Governance tests (linter, blast radius, reconciliation)

Run from the **repo root** with the API running (`$env:SEMABRIDGE_API`, default http://127.0.0.1:8000).
Tests create `TEST ...` domains with random names and delete them. Use a throwaway ontology folder
(`SEMABRIDGE_ONTOLOGY_DIR`) for the API while testing.

| Script | Checks | Needs |
|---|---|---|
| g01_linter_rules.py | Every lint rule fires when it should and only then; options on/off; cycles vs role-playing; counts; determinism (E1-E15) | API |
| g02_blast_radius.py | Column/table/metric impact, word-boundary and case matching, table scoping, transitive impact, severities, input validation, totals (B1-B15) | API |
| g03_recon_verify.py | Verifier with crafted DAX/SQL rows: tolerances, missing/extra rows, NULL/NaN, wrong value column, empty input, duplicate keys, key types, text numbers, multi-key (V1-V21) | API |
| g04_recon_query_generation.py | Generated DAX/SQL: quoting, table qualifiers, escaping, semantic-view syntax, filters (Q1-Q8) | API |
| g05_fake_data_scan.py | Hard-coded result rows, outcome switches, demo defaults, random values in the governance UI/code (F1-F5) | repo only |
| g06_recon_real.py | REAL reconciliation: SEMANTIC_VIEW results from Snowflake vs Power BI values you export; independent check + verifier | API + Snowflake + expected values |

```powershell
uv run python Tests/governance_tests/run_all.py
uv run python Tests/governance_tests/g06_recon_real.py --view SEMABRIDGE.ARTIFICIAL_INTELLIGENCE.ARTIFICIAL_INTELLIGENCE_SAMPLE_SEMANTIC `
    --expected Tests/governance_tests/fixtures/my_values.json --project proj-artificial-intelligence

# Local CI/CD Governance Quality Gate (lints real Config/ontologies files, fails on empty store or failed certifications)
uv run python Tests/governance_tests/g09_cicd_gate.py --gate-only --direct --ontologies-dir Config/ontologies --max-errors 0 --steward-level warning --check-certifications
# Optional: scope gate to changed project/ontology files only
uv run python Tests/governance_tests/g09_cicd_gate.py --gate-only --direct --ontologies-dir Config/ontologies --changed --max-errors 0 --steward-level warning --check-certifications
```

For g06, copy `fixtures/expected_values_example.json` and fill `rows` with values exported from Power BI
(table visual -> Export data, or DAX Studio). One entry per metric (+ dimensions).

## Results on feat/cortex-verified-queries (a0e3d34)

| Script | Pass | Fail | Real bugs found |
|---|---|---|---|
| g01 linter | 22 | 3 | binding-only metric flagged as empty (E5); 'X Y' vs 'X_Y' not a collision (E8); losing duplicate in 2nd domain never linted (E9) |
| g02 blast radius | 28 | 5 | no transitive impact (B8 x2); 'amt' hits column 'amt%' (B2); target 'amt%' finds nothing (B15); unknown change_type accepted (B11) |
| g03 verifier | 20 | 7 | **values under another column name -> CERTIFIED (V8, this is what the UI sends)**; 0 rows -> CERTIFIED (V9); NaN == 0 (V10); duplicate keys keep last row (V11); 2024 vs 2024.0 different keys (V12); '1,234.5' -> NULL == NULL certified (V14); negative tolerance accepted (V20) |
| g04 query generation | 7 | 7 | SQL alias with spaces unquoted; table qualifier dropped; unqualified dimension becomes 'X'[X]; no escaping of ] or quotes; expression pasted on a semantic view instead of SEMANTIC_VIEW(...); SQL filter text reused as DAX |
| g05 fake data | 8 | 3 | UI sends 12 hard-coded rows to /verify; 'certified'/'drift' switch decides the outcome; demo defaults (Gross Margin, FACT_FINANCIALS, Customer_Tier) |

Because of V8, the UI reconciliation reports CERTIFIED for the "drift" scenario too: its rows use the key
`metric_value`, the verifier looks values up by the metric name, finds none on either side, and treats
"nothing vs nothing" as a match.
