# Ontology & AI test scripts

Run from the **repo root** with the API running (`SEMABRIDGE_API` defaults to `http://127.0.0.1:8000`; pass `--base http://127.0.0.1:8001` or `8002` when running against another port).

> Tip: start the API with `SEMABRIDGE_ONTOLOGY_DIR=<temp folder>` while running O01-O07 if you do not want test domains touching `Config/ontologies`.  
> For full fix architecture notes, see [`src/semabridge/ontology/README.md`](../src/semabridge/ontology/README.md).

| Script | What it checks | Needs |
|---|---|---|
| `o01_loader_root_cause.py` | Verifies `_load_osi_model` snapshot & import correctness (offline code check) | repo only |
| `o02_projects_and_osi_files.py` | Project IDs vs `output/inferred/<id>/osi_inferred.json`; `/from-osi` by project ID | API |
| `o03_generate_from_osi_file.py` | Domain built from OSI JSON (concepts, attributes, metrics, relationships) and YAML round-trip | API + OSI file |
| `o04_upload_rules.py` | Ingest YAML accepts valid ontologies and rejects `model.yaml`, typos, duplicates, bad refs, `>1MB`, unsafe IDs, bad YAML | API |
| `o05_collisions.py` | Same metric across two domains -> collision handling & cleanup | API |
| `o06_validate_save_rename.py` | Validate (no save), save, edit, rename keeps ID, delete | API |
| `o07_project_attach_preview.py` | Attach to project, unknown domain rejected, preview unmatched bindings, graph, knowledge-graph SQL | API + project |
| `o08_ai_studio.py` | Synonym search, NL -> SQL (expected metric, `GROUP BY`, quoted aliases, `SEMANTIC_VIEW(...)`), honest Snowflake error handling without silent simulation | API (+ Snowflake) |
| `o09_nl_query_trace.py` | Single-question NL-to-SQL trace diagnostics (DAX in SQL, over-matching, missing dimensions, duplicate domains, Snowflake config) | API (+ Snowflake with `--execute`) |
| `o10_multi_model_validation.py` | 139-assertion regression suite across all 8 domains (`Adventure Works DW 2020`, `Arrow Chart`, `Artificial Intelligence Sample`, `Human Resources`, `Market Intelligence`, `Sales Analytics`, `Sales & Returns`, `Store Sales 1`) + cross-domain isolation & negative tests | API |
| `o11_benchmark_3_models.py` | 56 plain-business AI Studio benchmark questions across Human Resources (20), Sales & Returns (19), and Arrow Chart Financials (17) | API |
| `t_no_hardcoding.py` | AST guard ensuring zero business terms or model-specific identifiers are hardcoded in Python product modules | repo only |

## Quick Run Commands

```powershell
# Full O01-O10 suite against local server on port 8001
.venv\Scripts\python.exe ontology_tests\run_all.py --base http://127.0.0.1:8001 `
    --project proj-artificial-intelligence-sample `
    --questions ontology_tests\fixtures\questions_example.json

# Multi-model regression suite (139 checks)
.venv\Scripts\python.exe ontology_tests\o10_multi_model_validation.py --base http://127.0.0.1:8001

# 56-question 3-model plain-business benchmark
.venv\Scripts\python.exe ontology_tests\o11_benchmark_3_models.py --base http://127.0.0.1:8001

# AST zero-hardcoding check
.venv\Scripts\python.exe ontology_tests\t_no_hardcoding.py
```

Outputs go to `ontology_tests/out/` (gitignored).

## Findings & Fix Status (Verified on `ontology_validations`)

| # | Finding | Status |
|---|---|---|
| 1 | `export_router._load_osi_model` failed on broken imports, wrong `get_snapshot` signature, and raw dict assumptions, turning errors into HTTP 404 | **Fixed** (`export_router.py`) |
| 2 | `from-osi` with an `osi_model` payload returned 400 (`OSIModel(**raw)` strict mode); renamed domains duplicated instead of updating by `source_project_id` | **Fixed** (`domain_ontology_router.py`) |
| 3 | Multiple BI relationships between identical tables (role-playing / inactive keys) collapsed into one relationship | **Fixed** (`federated_loader.py` `_rkey`) |
| 4 | Raw BI DAX formulas leaked into `sql_expression` and `NONE`/`CUSTOM` ratio metrics were coerced to `SUM` | **Fixed** (`federated_loader.py` + AST fallback) |
| 5 | NL query SQL: `project_id` ignored, `FROM <domain>_METRICS_VIEW` instead of `SEMANTIC_VIEW(...)`, `GROUP BY` dimensions dropped or pulled from wrong domain | **Fixed** (`ai_router.py`, `nl_query_engine.py`, `semantic_search.py`) |
| 6 | `/api/ai/execute-query` returned fabricated simulated rows when Snowflake was unreachable | **Fixed** (`allow_simulated: bool = False` in `ai_router.py`) |
| 7 | Scalar `%`, Top-N `<entity> by <metric>`, `by X and Y`, rate-vs-amount ranking, and time-shift period specificity edge cases | **Fixed** (`nl_query_engine.py`, `semantic_search.py`) |
| 8 | Snowflake Semantic View validation rejected `PARTITION BY EXCLUDING` and LOO `CALCULATE(..., ALL(Fact))` expressions; keypair auth required manual toggle | **Fixed** (`metrics_clause_builder.py`, `dax_ast_parser.py`, `snowflake_connection.py`) |

## Verified Results Summary

- `t_no_hardcoding.py`: **`0 hard-coded literal(s) in 12 file(s)`** across `1473` model names
- `pytest Tests/test_ontology_emitter.py Tests/test_ontology_fixes.py Tests/test_ontology_graph.py Tests/test_window_exclusions_generic.py -v`: **`25 passed`**
- `o10_multi_model_validation.py`: **`139 pass, 0 fail, 0 warn`**
- `o11_benchmark_3_models.py`: All 56 queries routed cleanly to their model's governed `SEMANTIC_VIEW(...)` (`SV=True`); synonym/non-modeled gaps report explicit warnings (`HR` `Seps`/`TO %`/`Empcount` aliases, `Sales & Returns` colloquial synonyms, and `Arrow Chart` physical columns `Profit`/`Units Sold`/`COGS`/`Discounts` not declared as DAX metrics).

## Extraction coverage audit (read-only)

`ontology_extraction_coverage.py` compares PBIX (optional) -> SML -> OSI (optional) -> ontology YAML field by field and classifies gaps without modifying files (`samples/ai_model_coverage/` holds a reference run).
