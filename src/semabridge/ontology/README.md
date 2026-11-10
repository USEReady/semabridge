# SemaBridge Ontology, AI Query Engine & Validation Fixes (`ontology_validations`)

This directory implements SemaBridge's federated domain ontology loader, persistence store, graph builder, policy/lineage models, and execution glue.  
On branch `ontology_validations`, it also collects all multi-model Ontology, AI Studio NL-to-SQL, Snowflake Semantic View, and DAX-to-SQL fixes verified locally across 8 enterprise models.

---

## Summary of Fixes Included

### 1. Ontology Generation, OSI Loading & Governance Features
- **`src/semabridge/api/routers/export_router.py` (`_load_osi_model`)**
  - Fixed broken imports (`ModelRepository` from `semabridge.repository.model_repository`, removed missing `get_db_session`), snapshot lookup signature (`repo.get_head(project_id)` / `repo.get_snapshot(snapshot_id)`), and JSON/dict deserialisation so `/from-osi` and Knowledge Graph previews succeed instead of raising hidden 404s.
- **`src/semabridge/api/routers/domain_ontology_router.py`**
  - Switched `/from-osi` payload validation to `OSIModel.model_validate_json(...)` so enum strings and date fields in JSON payloads deserialise cleanly without HTTP 400 errors.
  - Added lookup fallback by `source_project_id` so re-syncing a renamed domain updates it in place rather than creating duplicate YAML files.
- **`src/semabridge/ontology/federated_loader.py` & `src/semabridge/ontology/models.py` & `src/semabridge/ontology/store.py`**
  - **SQL vs DAX separation:** Keeps compiled SQL in `sql_expression` and original BI DAX in `dax_expression`; falls back to `try_ast_translate(...)` when a snapshot lacks pre-compiled SQL so raw DAX never leaks into ontology SQL expressions.
  - **Aggregation preservation:** Stopped coercing `NONE`/`CUSTOM` ratio and percentage metrics to `SUM`.
  - **Domain-agnostic metadata propagation:** Inherits natural BI `original_name`/`label`/`synonyms` from source metadata without injecting hardcoded business words.
  - **Role-playing relationship keys:** Disambiguates multiple relationships between identical tables via composite join-expression + relationship unique-name keys (`_rkey`).
  - **Governance support (merged with `integrate/relationship-direction-fixes`):** Supports `DomainPolicy`, `RelationshipType`, `filter_expression`, `derives_from` inference (`_auto_infer_metric_dependencies`), expression expansion, and `metric partial definition` collision reporting.

### 2. AI Studio NL-to-SQL Routing & Metric/Dimension Resolution
- **`src/semabridge/api/routers/ai_router.py`**
  - Honors `project_id` scoping (`allowed_domains`) to prevent cross-domain metric/dimension bleeding (e.g. Adventure Works vs Artificial Intelligence Sample vs Store Sales).
  - Automatically resolves the deployed Snowflake `SEMANTIC_VIEW(...)` name from project snapshots (`view_name` / `get_target_deployment_name`) and forwards `semantic_view` to `NLQueryEngine`.
  - Added `allow_simulated: bool = False` on `ExecuteQueryRequest` so unreachable Snowflake connections return honest failures rather than fabricated preview rows by default.
- **`src/semabridge/ai/nl_query_engine.py`**
  - **Generic intent classification & re-ranking:** Separates pure rate/percentage intent (`asks_for_rate_or_pct`) from time-intelligence shifts (`asks_for_time_intel`) so scalar percentage queries do not falsely promote `MoM%` / `SPLY` metrics, and `rate`/`pctof` metrics are penalized when not asked for (`SR-03` picks `Returns` over `Total Return Rate`).
  - **Specific period-shift boosting:** Boosts only time metrics matching the shift tokens present in the query (so `"Sales vs previous year"` selects `['Py Sales', 'Sales']` instead of adding `Sales Pm` or `Pq Sales`).
  - **Subject-token gating:** Filters modifier-only false positives (e.g. `"average salary"` does not match `Avg Age`; missing metrics emit helpful `"Did you mean..."` warnings with low confidence).
  - **Singular vs multi-metric intent:** Computes `has_multi_intent` on the metric clause (`metric_search_query`) rather than the full prompt so `"by product and discount band"` does not multiply selected metrics.
  - **Domain-scoped dimension resolution & Top-N ranking:** Queries `self.ontology.domains` directly (avoiding first-wins federation shadowing on shared names like `Product`, `Customer`, `Date`), supports comma/`and`-separated multi-dimension `by X and Y` clauses (`HR-10`, `AC-06`), plural/stem matching, compound noun phrases (`product category` -> `Category`), and `top N <entity> by <metric>` (`SR-18` groups by `Store` with `LIMIT 3`).
- **`src/semabridge/ai/semantic_search.py`**
  - Indexes `metric.concept` in metric search text, weights exact/token-subset bonuses by token coverage, normalizes time-shift phrases (`year-to-date` -> `ytd`, `previous year` -> `py`, `same period last year` -> `sply`, `moving annual total` -> `mat`, etc.) before camelCase splitting, adds space-joined bigram matching (`q_bigrams`), and scopes candidate fallback count to `domain_filter`.

### 3. Snowflake Semantic View & DAX Translation Fixes
- **`src/semabridge/connectors/metrics_clause_builder.py` & `Tests/test_window_exclusions_generic.py`**
  - Strips `PARTITION BY EXCLUDING (...)` before physical column schema verification and allows declared semantic dimensions and table-prefixed dimension identifiers.
- **`src/semabridge/converter/dax_ast_parser.py` & `src/semabridge/converter/sml_to_osi.py`**
  - Handles Leave-One-Out (`CALCULATE(..., ALL(FactTable))`) expression substitution using precomputed totals inside `AVERAGEX`/`COUNTROWS`/`SUM` contexts.
- **`src/semabridge/connectors/snowflake_connection.py`**
  - Auto-detects `keypair` auth when `password` is empty and `private_key` is configured.

---

## Validation & Regression Checks

Run from `semabridge/`:

1. **Zero-hardcoding AST guard** (`0 hard-coded literal(s) in 12 file(s)` across 1,473 model identifiers):
   ```powershell
   .venv\Scripts\python.exe ontology_tests\t_no_hardcoding.py
   ```
2. **Unit & ontology regression tests** (`25 passed`):
   ```powershell
   .venv\Scripts\pytest.exe Tests/test_ontology_emitter.py Tests/test_ontology_fixes.py Tests/test_ontology_graph.py Tests/test_window_exclusions_generic.py -v
   ```
3. **Multi-model HTTP validation suite** (`139 pass, 0 fail, 0 warn` across all 8 domains with API running):
   ```powershell
   .venv\Scripts\python.exe ontology_tests\o10_multi_model_validation.py --base http://127.0.0.1:8001
   ```
4. **56-question 3-model plain-business benchmark** (HR, Sales & Returns, Arrow Chart Financials):
   ```powershell
   .venv\Scripts\python.exe ontology_tests\o11_benchmark_3_models.py --base http://127.0.0.1:8001
   ```

See [ontology_tests/README.md](../../ontology_tests/README.md) for script-by-script details (`o01` through `o11` and `t_no_hardcoding.py`).
