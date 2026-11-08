"""Builder for Snowflake semantic-view METRICS (MEASURES) clause."""

from __future__ import annotations
from semabridge.utils.naming import to_alias, sanitize_column

import re
from typing import Any, Dict, List, Set, Tuple, Optional

from semabridge.utils.logger import get_logger
from semabridge.utils.identifiers import IdentifierSanitizer
from semabridge.connectors.ddl_helpers import (
    deduplicate_metrics_lines,
    deduplicate_metrics_lines_osi,
    extract_expr_key,
    extract_expr_key_osi,
)
from semabridge.connectors.snowflake_metric_sql import normalize_snowflake_metric_sql
from semabridge.connectors.synonym_clause import element_description, synonyms_clause
from semabridge.core.drop_ledger import DropLedger, DropStage
from semabridge.utils.null_sentinel import is_null_cast_sql

NULL_PLACEHOLDER_SQL = "CAST(NULL AS DOUBLE)"
# Drop-ledger reason for a metric emitted as a NULL placeholder because its
# DAX could not be translated; the original cause goes in the record detail.
PLACEHOLDER_NOT_TRANSLATABLE = "placeholder: not translatable"
from semabridge.connectors.type_safety_validator import (
    build_dataset_col_types,
    detect_date_numeric_type_mismatch,
    detect_nested_aggregate,
)
from semabridge.converter.time_intelligence_shapes import (
    ADVISORY_CATEGORY_ENRICHMENT_COLUMN_UNVERIFIABLE,
    enrichment_flag_column_if_referenced,
    metrics_with_time_intelligence_shapes,
)

logger = get_logger(__name__)


class MetricsClauseBuilder:
    def _ensure_window_helper_metrics(
        self,
        valid_metrics: List[Any],
        dataset_aliases: Dict[str, str],
        dataset_col_lookup: Dict[str, Set[str]],
        metric_name_set: Set[str],
        metric_to_alias: Dict[str, str],
        fact_aliases: Set[str],
        alias_by_raw: Dict[str, str],
        dataset_by_name: Dict[str, Any],
    ) -> None:
        """Synthesize and register base additive helper metrics (_SUM, _CNT, _NUM, _DEN)
        needed by decomposed window metrics so that Snowflake and static validators find
        all window arguments declared as valid metric references."""
        from semabridge.sml.models import SMLMetric, AggregationType
        
        m_sql_map = {}
        for m in valid_metrics:
            sql = getattr(m, "sql_expression", None)
            if not sql:
                continue
            for attr in ("label", "unique_name", "name"):
                val = getattr(m, attr, None)
                if val:
                    m_sql_map[val] = sql

        def _split_top_level_div(sql: str):
            depth, in_q = 0, False
            for i, ch in enumerate(sql):
                if ch in ("'", '"'):
                    in_q = not in_q
                elif not in_q:
                    if ch == '(': depth += 1
                    elif ch == ')': depth -= 1
                    elif ch == '/' and depth == 0:
                        num_part, den_part = sql[:i].strip(), sql[i+1:].strip()
                        m_null = re.match(r'^\s*NULLIF\s*\((.*),\s*0\s*\)\s*$', den_part, re.I)
                        if m_null:
                            den_part = m_null.group(1).strip()
                        return num_part, den_part
            return None

        # 1. Scan metrics to find any decomposed window metrics
        new_helpers = []
        for m in list(valid_metrics):
            expr = getattr(m, "sql_expression", None) or ""
            if not expr and getattr(m, "expression", None):
                dax = getattr(m, "expression", "")
                if any(k in dax.upper() for k in ["ALL(", "ALL (", "ALLEXCEPT("]):
                    from semabridge.converter.dax_ast_parser import try_ast_translate
                    t_alias = dataset_aliases.get(m.dataset)
                    if not t_alias or t_alias not in fact_aliases:
                        dax_tbls = re.findall(r"(?:'([^']+)'|\b([a-zA-Z0-9_]+))\s*\[", dax)
                        matched_t = None
                        for q_t, b_t in dax_tbls:
                            cand_t = q_t or b_t
                            for ds_k, al_k in dataset_aliases.items():
                                if ds_k.casefold() == cand_t.casefold() and (not fact_aliases or al_k in fact_aliases):
                                    matched_t = al_k
                                    break
                            if matched_t:
                                break
                        t_alias = matched_t or next(iter(fact_aliases), t_alias or "FACT")
                    try:
                        cand = try_ast_translate(dax, t_alias, None, measure_sql_map=m_sql_map, table_aliases=dataset_aliases)
                        if cand and ("OVER (" in cand.upper() or "OVER(" in cand.upper()):
                            expr = cand
                            m.sql_expression = cand
                    except Exception:
                        pass
            if not expr:
                continue
            if "OVER (" in expr.upper() or "OVER(" in expr.upper():
                for match in re.finditer(
                    r'(?:SUM|AVG|COUNT|MIN|MAX)\s*\(\s*([A-Za-z0-9_]+)\."([A-Za-z0-9_]+)"(?:::FLOAT)?\s*\)\s*OVER\s*\(PARTITION\s+BY(?:\s+EXCLUDING)?',
                    expr,
                    re.IGNORECASE,
                ):
                    t_alias = match.group(1).upper()
                    ref_name = match.group(2).upper()
                    if ref_name not in metric_name_set and not any(h[0] == ref_name for h in new_helpers):
                        ds_name = None
                        for d, a in dataset_aliases.items():
                            if a.upper() == t_alias:
                                ds_name = d
                                break
                        if not ds_name:
                            ds_name = alias_by_raw.get(t_alias, t_alias)
                        
                        helper_sql = None
                        is_count = False

                        # Column-based helper (_SUM / _CNT)
                        if ref_name.endswith("_SUM") or ref_name.endswith("_CNT"):
                            is_sum = ref_name.endswith("_SUM")
                            is_count = not is_sum
                            col_stem = ref_name[:-4]
                            known = dataset_col_lookup.get(ds_name, set())
                            matched_col = None
                            for c in known:
                                if c.upper() == col_stem or re.sub(r'[^A-Z0-9]', '', c.upper()) == re.sub(r'[^A-Z0-9]', '', col_stem):
                                    matched_col = c
                                    break
                            if not matched_col:
                                matched_col = col_stem
                            helper_sql = f'SUM({t_alias}."{matched_col}")' if is_sum else f'COUNT({t_alias}."{matched_col}")'

                        # Measure-based helper (_NUM / _DEN)
                        elif ref_name.endswith("_NUM") or ref_name.endswith("_DEN"):
                            is_num = ref_name.endswith("_NUM")
                            m_stem = ref_name[:-4]
                            base_m = None
                            for vm in valid_metrics:
                                vm_al = self.identifier_sanitizer.to_alias(vm.unique_name).upper()
                                if vm_al == m_stem or re.sub(r'[^A-Z0-9]', '', vm_al) == re.sub(r'[^A-Z0-9]', '', m_stem):
                                    base_m = vm
                                    break
                            
                            if base_m and getattr(base_m, "expression", None):
                                try:
                                    from semabridge.converter.dax_ast_parser import DaxAstParser, DaxSqlRenderer
                                    b_ast = DaxAstParser().parse(base_m.expression)
                                    n_node, d_node = None, None
                                    if hasattr(b_ast, "func") and b_ast.func.upper() == "DIVIDE" and len(b_ast.args) >= 2:
                                        n_arg, d_arg = b_ast.args[0], b_ast.args[1]
                                        if hasattr(n_arg, "func") and n_arg.func.upper() in ("ROUND", "ROUNDDOWN", "ROUNDUP"):
                                            inner = n_arg.args[0] if n_arg.args else None
                                            if hasattr(inner, "op") and inner.op == "*" and hasattr(inner.left, "func") and inner.left.func.upper() == "DIVIDE":
                                                n_arg, d_arg = inner.left.args[0], inner.left.args[1]
                                        n_node, d_node = n_arg, d_arg
                                    
                                    target_node = n_node if is_num else d_node
                                    if target_node is not None:
                                        renderer = DaxSqlRenderer(table_alias=t_alias, measure_sql_map=m_sql_map, table_aliases=dataset_aliases)
                                        rendered = renderer.render(target_node)
                                        if rendered and rendered.count("(") == rendered.count(")"):
                                            helper_sql = rendered
                                except Exception:
                                    pass

                            if not helper_sql and base_m:
                                b_sql = getattr(base_m, "sql_expression", None)
                                if b_sql:
                                    split_res = _split_top_level_div(b_sql)
                                    if split_res:
                                        target_sql = split_res[0] if is_num else split_res[1]
                                        if target_sql.count("(") == target_sql.count(")"):
                                            helper_sql = target_sql

                            if not helper_sql:
                                if is_count:
                                    helper_sql = f'COUNT(*)'

                        if helper_sql:
                            new_helpers.append((ref_name, t_alias, ds_name, helper_sql, is_count))

        for ref_name, t_alias, ds_name, helper_sql, is_count in new_helpers:
            helper_m = SMLMetric(
                unique_name=ref_name,
                label=ref_name,
                dataset=ds_name,
                expression="",
                sql_expression=helper_sql,
                aggregation=AggregationType.COUNT if is_count else AggregationType.SUM,
                sync_enabled=True,
                access_modifier="private_access",
            )
            valid_metrics.insert(0, helper_m)
            metric_name_set.add(ref_name)
            metric_to_alias[ref_name] = t_alias

    """Handles construction and validation of the METRICS clause."""

    def __init__(
        self,
        identifier_sanitizer: Any,
        schema_manager: Any,
        sanitizer: Any,
        translator: Any,
        config: Any,
        dup_name_repo: Any = None,
        drop_ledger: Optional[DropLedger] = None,
        enriched_view_mapping: Optional[Dict[str, str]] = None,
        no_populated_date_link_datasets: Optional[Set[str]] = None,
        failed_enrichment_datasets: Optional[Set[str]] = None,
        live_schema_metadata: Optional[Dict[str, Any]] = None,
    ):
        self.identifier_sanitizer = identifier_sanitizer
        self.schema_manager = schema_manager
        self.sanitizer = sanitizer
        self.translator = translator
        self.config = config
        self.dup_name_repo = dup_name_repo
        self.drop_ledger: DropLedger = drop_ledger if drop_ledger is not None else DropLedger()
        self.enriched_view_mapping: Dict[str, str] = (
            enriched_view_mapping if enriched_view_mapping is not None else {}
        )
        self.no_populated_date_link_datasets: Set[str] = (
            no_populated_date_link_datasets if no_populated_date_link_datasets is not None else set()
        )
        self.failed_enrichment_datasets: Set[str] = (
            failed_enrichment_datasets if failed_enrichment_datasets is not None else set()
        )
        self.live_schema_metadata: Dict[str, Any] = (
            live_schema_metadata if live_schema_metadata is not None else {}
        )

    def build_for_sml(
        self,
        sml: Any,
        dataset_aliases: Dict[str, str],
        dataset_by_name: Dict[str, Any],
        dataset_col_lookup: Dict[str, Set[str]],
        alias_by_raw: Dict[str, str],
        all_physical_col_names: Set[str],
        emittable_metric_name_set: Set[str]
    ) -> List[str]:
        """Build METRICS clause for SML model."""
        return self._build_metrics(
            sml, dataset_aliases, dataset_by_name, dataset_col_lookup, 
            alias_by_raw, all_physical_col_names, emittable_metric_name_set, is_osi=False
        )

    def build_for_osi(
        self,
        osi: Any,
        dataset_aliases: Dict[str, str],
        dataset_by_name: Dict[str, Any],
        dataset_col_lookup: Dict[str, Set[str]],
        alias_by_raw: Dict[str, str],
        all_physical_col_names: Set[str],
        emittable_metric_name_set: Set[str]
    ) -> List[str]:
        """Build METRICS clause for OSI model."""
        return self._build_metrics(
            osi, dataset_aliases, dataset_by_name, dataset_col_lookup, 
            alias_by_raw, all_physical_col_names, emittable_metric_name_set, is_osi=True
        )

    def _build_metrics(
        self,
        model: Any,
        dataset_aliases: Dict[str, str],
        dataset_by_name: Dict[str, Any],
        dataset_col_lookup: Dict[str, Set[str]],
        alias_by_raw: Dict[str, str],
        all_physical_col_names: Set[str],
        emittable_metric_name_set: Set[str],
        is_osi: bool
    ) -> List[str]:
        self.dataset_col_lookup = dataset_col_lookup
        self.dataset_by_name = dataset_by_name
        self._current_model = model
        metrics_lines = []
        used_metric_names: Set[str] = set()
        skipped_metric_names: Set[str] = set()
        expected_metrics: List[Tuple[str, str, str]] = []

        # Every metric flows through unconditionally -- identifier_sanitizer's
        # general character-replacement table (sanitize_alias/sanitize_column)
        # already turns '$' into 'DOL', the same way it turns '@' into 'AT' and
        # '#' into 'NUM' for every other metric name. A metric named e.g.
        # "Sales $" needs no special-case handling here to become SALES_DOL --
        # dropping it outright used to bypass that path for this one character.
        valid_metrics = list(model.metrics)
        metric_name_set = {self.identifier_sanitizer.to_alias(m.unique_name) for m in valid_metrics}

        # Build metric name to table alias mapping dynamically as metrics are emitted
        metric_to_alias: Dict[str, str] = {}

        # Pre-seed every metric's PROVISIONAL alias (its own dataset's alias)
        # before the main loop below processes metrics in plain source
        # order. Without this, a metric appearing earlier in the model that
        # references a LATER metric by name (e.g. a selector-table metric
        # referencing [Total Units YTD]) would have its bare reference
        # qualified against a map that doesn't have that later metric in it
        # yet -- _qualify_bare_metric_references (translator.py) simply has
        # no substitution rule for that name, so the token is left
        # completely unqualified in the emitted SQL. Snowflake then rejects
        # the WHOLE DDL statement with "invalid identifier", and the
        # deploy-time remediation sweep (semantic_ddl_sanitizer.py) nulls
        # out every metric whose line happens to contain that same bare
        # token -- including other, perfectly valid metrics that were never
        # actually broken (a real incident: one bad selector-table metric
        # collaterally nulled three unrelated, correctly-computing ones).
        #
        # The real, final alias -- computed per metric below via
        # _resolve_metric_emission_alias, which can differ from the
        # metric's own dataset for a handful of ambiguous-anchor cases --
        # overwrites this provisional guess once that metric is actually
        # processed (see the assignment a few dozen lines down), so any
        # metric processed AFTER a correction still sees the accurate
        # value. Only a metric BOTH processed earlier AND referencing one
        # of those rare divergent cases would see a provisional-but-wrong
        # alias -- still a real, existing table alias (safe, diagnosable if
        # wrong) rather than a bare, always-broken reference.
        for _seed_metric in valid_metrics:
            _provisional_alias = dataset_aliases.get(_seed_metric.dataset)
            if _provisional_alias:
                metric_to_alias.setdefault(
                    self.identifier_sanitizer.to_alias(_seed_metric.unique_name),
                    _provisional_alias,
                )

        # Build set of fact-table aliases so metric prefix resolution prefers them
        fact_aliases: Set[str] = {
            alias
            for ds_name, alias in dataset_aliases.items()
            if getattr(dataset_by_name.get(ds_name), "is_fact", False)
        }
        if not fact_aliases:
            # Table with most outgoing relationships (many-side of relationships)
            from_counts: Dict[str, int] = {}
            for rel in getattr(model, "relationships", []) or []:
                from_ds = getattr(rel, "from_dataset", None)
                if from_ds:
                    from_counts[from_ds] = from_counts.get(from_ds, 0) + 1
            if from_counts:
                max_from = max(from_counts, key=from_counts.get)
                if max_from in dataset_aliases:
                    fact_aliases.add(dataset_aliases[max_from])

        measure_dax_map: Dict[str, str] = {}
        for m in valid_metrics:
            dax = getattr(m, "expression", None)
            if dax:
                measure_dax_map[m.unique_name] = dax
                measure_dax_map[self.identifier_sanitizer.to_alias(m.unique_name).upper()] = dax
                if getattr(m, "label", None):
                    measure_dax_map[m.label] = dax
                    measure_dax_map[self.identifier_sanitizer.to_alias(m.label).upper()] = dax

        self._measure_dax_map = measure_dax_map
        self._dataset_col_lookup = dataset_col_lookup
        self._dataset_aliases = dataset_aliases
        self._relationships = getattr(model, "relationships", None)
        self._ensure_window_helper_metrics(
            valid_metrics,
            dataset_aliases,
            dataset_col_lookup,
            metric_name_set,
            metric_to_alias,
            fact_aliases,
            alias_by_raw,
            dataset_by_name,
        )

        # Declared column types (from the model's own SML/OSI Column
        # objects) — the type-safety backstop below uses this to catch a
        # date-producing expression combined with an integer/number column
        # before it ever reaches Snowflake's DDL. See
        # type_safety_validator.py's module docstring for the incident this
        # closes.
        dataset_col_types = build_dataset_col_types(getattr(model, "datasets", None))
        if hasattr(self.translator, "column_data_types"):
            self.translator.column_data_types = dataset_col_types
        else:
            setattr(self.translator, "column_data_types", dataset_col_types)

        # Resolved ONCE for the whole model (not per metric -- see
        # time_intelligence_shapes.py's own note on why) so the DDL-
        # emission schema-validation failure handler below can recognize
        # "this unknown column is exactly the enrichment flag column this
        # metric's own resolved shape predicts" instead of hard-failing
        # every enrichment-created column dry-run can't see.
        metric_shapes = metrics_with_time_intelligence_shapes(valid_metrics)
        # Build relationship reachability graph to predict cross-table reference failures
        relationship_graph: Dict[str, Set[str]] = {}
        directed_reach: Dict[str, Set[str]] = {}
        for rel in getattr(model, "relationships", []) or []:
            from_a = dataset_aliases.get(getattr(rel, "from_dataset", None))
            to_a = dataset_aliases.get(getattr(rel, "to_dataset", None))
            if not getattr(rel, "is_active", True):
                from_cols_raw = getattr(rel, "from_columns", None) or []
                fk_col_raw = from_cols_raw[0] if from_cols_raw else ""
                if fk_col_raw:
                    to_a = to_alias(rel.to_dataset, fk_col_raw)
            if from_a and to_a:
                relationship_graph.setdefault(from_a, set()).add(to_a)
                relationship_graph.setdefault(to_a, set()).add(from_a)
                directed_reach.setdefault(from_a, set()).add(to_a)

        metric_base_totals: Dict[str, int] = {}
        for m in valid_metrics:
            base = self.identifier_sanitizer.to_alias(m.unique_name)
            metric_base_totals[base] = metric_base_totals.get(base, 0) + 1
            
        metric_base_seen: Dict[str, int] = {}
        metric_signature_seen: Dict[str, int] = {}
        metric_namespace = self._duplicate_namespace_key(model.unique_name or model.label)
        model_name = model.unique_name or model.label

        # Priority 0: prefetch OpenAI translations in batches (up to 20),
        # so complex DAX gets one network round-trip per chunk instead of per metric.
        try:
            self.translator.prefetch_openai_metric_translations(
                metrics=valid_metrics,
                table_alias=next(iter(fact_aliases), "FACT"),
                dataset_col_lookup=dataset_col_lookup,
            )
        except Exception as exc:
            logger.warning("OpenAI metric prefetch skipped: %s", exc)

        non_additive_metric_names = {
            self.identifier_sanitizer.sanitize_column(m.unique_name).upper()
            for m in valid_metrics
            if getattr(m, "non_additive_dimension", None)
        }

        for metric in valid_metrics:
            raw_name = metric.unique_name
            sanitized_name = self.identifier_sanitizer.to_alias(raw_name)
            logger.info(f"🔍 METRIC SANITIZATION: raw='{raw_name}' → sanitized='{sanitized_name}'")

        non_additive_metric_names = {
            self.identifier_sanitizer.sanitize_column(m.unique_name).upper()
            for m in valid_metrics
            if getattr(m, "non_additive_dimension", None)
        }

        for metric in valid_metrics:
            if metric.unique_name in skipped_metric_names:
                continue

            from semabridge.converter.dax_ast_parser import is_by_design_excluded
            if is_by_design_excluded(getattr(metric, "sync_failure_reason", None)):
                reason_str = str(metric.sync_failure_reason or "").lower()
                if "compile-time constant" in reason_str or "no column/measure/table reference" in reason_str:
                    expr_str = str(getattr(metric, "expression", "") or "").strip()
                    if expr_str in ('""', "''", ""):
                        self.drop_ledger.record(
                            "m" + "etric", metric.unique_name, DropStage.DDL_EMISSION,
                            metric.sync_failure_reason or "By-design excluded from metric translation.",
                            dataset=getattr(metric, "dataset", None),
                            by_design=True,
                        )
                        continue

            ds_fold = str(getattr(metric, "dataset", "") or "").casefold()
            is_ti = (
                (metric.unique_name in metric_shapes)
                or any(token in metric.unique_name.upper() for token in ["_YTD", "_SPLY", "_R12M", "_R12MS", "_YOY", "_MTD", "_QTD"])
                or any(
                    k in str(getattr(metric, "expression", "") or "").upper()
                    for k in ["TOTALYTD", "SAMEPERIODLASTYEAR", "DATESYTD", "DATEADD", "PARALLELPERIOD", "MONTHINDEX", "RUNNING_YEAR", "COL_DATE.DATE_DATE", "_YTD", "_SPLY", "_R12M"]
                )
            )

            alias = dataset_aliases.get(metric.dataset)
            if not alias and fact_aliases:
                alias = sorted(fact_aliases)[0]
            if not alias and dataset_aliases:
                alias = sorted(dataset_aliases.values())[0]
            if not alias:
                # No home table in this view to hold even a NULL placeholder.
                self.drop_ledger.record(
                    "metric", metric.unique_name, DropStage.DDL_EMISSION,
                    f"Metric's dataset '{metric.dataset}' has no TABLES alias in "
                    "this semantic view, so the metric has no table to anchor "
                    "against.",
                    dataset=metric.dataset,
                )
                continue

            metric_base_alias = self.identifier_sanitizer.to_alias(metric.unique_name)
            metric_seen_idx = metric_base_seen.get(metric_base_alias, 0) + 1
            metric_base_seen[metric_base_alias] = metric_seen_idx

            metric_alias_seed = (metric_base_alias if metric_base_totals.get(metric_base_alias, 0) == 1
                                 else f"{metric_base_alias}_{metric_seen_idx}")

            if metric_base_totals.get(metric_base_alias, 0) > 1:
                metric_signature_seed = self._build_duplicate_signature_seed(
                    metric.unique_name,
                    getattr(metric, "sql_expression", None) or metric.expression,
                    None,
                    metric.aggregation.value if metric.aggregation else None
                )
                sig_idx = metric_signature_seen.get(metric_signature_seed, 0) + 1
                metric_signature_seen[metric_signature_seed] = sig_idx
                metric_signature = f"{metric_signature_seed}::occ{sig_idx}"
                metric_alias_seed = self._resolve_persistent_duplicate_name(
                    "metric", metric_namespace,
                    self.identifier_sanitizer.to_alias(metric.dataset),
                    metric_base_alias, metric.unique_name, metric_signature, metric_alias_seed
                )

            metric_name = self._resolve_unique_metric_alias(metric_alias_seed, used_metric_names, metric.unique_name)
            expected_metrics.append((alias, metric_name, metric.unique_name))

            # A metric whose DAX can't become valid metric SQL (for any
            # reason) is emitted as a NULL placeholder on its home table —
            # never dropped, so it can't fail the sync or cascade-drop the
            # metrics that reference it. By-design exclusions stay excluded.
            failure: Optional[Tuple[str, Optional[str]]] = None
            expr: Optional[str] = None
            if is_ti and ds_fold in self.no_populated_date_link_datasets:
                failure = ("no populated date link on fact table", None)
            else:
                ledger_mark = len(self.drop_ledger.records)
                expr = self._generate_metric_expression(
                    metric, metric_name, alias, dataset_by_name, dataset_aliases,
                    dataset_col_lookup, alias_by_raw, metric_name_set,
                    all_physical_col_names, emittable_metric_name_set, skipped_metric_names, model_name, is_osi,
                    fact_aliases=fact_aliases,
                    metric_to_alias=metric_to_alias,
                    dataset_col_types=dataset_col_types,
                    metric_shapes=metric_shapes,
                )
                if expr:
                    expr = normalize_snowflake_metric_sql(expr)
                if not expr:
                    failure = self._take_translation_failure(metric, ledger_mark)
                    if failure is None:
                        reason = getattr(metric, "sync_failure_reason", None) or "not applicable: not translatable"
                        failure = (reason, None)
                elif not self._is_scalar_metric_sql(expr):
                    failure = (
                        "Translated SQL contains a non-scalar construct (SELECT/JOIN/CTE), "
                        "which Snowflake's semantic-view METRICS clause does not support.",
                        expr[:200],
                    )
                else:
                    reason = (
                        self._detect_untranslated_dax_syntax(expr)
                        or detect_date_numeric_type_mismatch(expr, dataset_aliases, dataset_col_types)
                        or detect_nested_aggregate(expr)
                    )
                    if reason:
                        failure = (reason, expr[:200])

            metric_entity_alias = self._resolve_metric_emission_alias(
                alias, expr, dataset_aliases, fact_aliases,
                dax_expression=getattr(metric, "expression", None),
                measure_dax_map=measure_dax_map,
                directed_reach=directed_reach,
            )
            if failure is None:
                valid_aliases_set = set(dataset_aliases.values())
                ref_aliases = self._extract_referenced_table_aliases(expr, valid_aliases_set)
                unreachable_aliases = [
                    ra for ra in ref_aliases
                    if not self._is_reachable_alias(relationship_graph, metric_entity_alias, ra)
                ]
                if unreachable_aliases:
                    failure = (
                        f"Unsupported (disconnected polymorphic selector): metric references table alias(es) {', '.join(sorted(unreachable_aliases))} that are not "
                        f"reachable from its anchor table entity '{metric_entity_alias}' via declared relationships.",
                        expr[:200],
                    )
                    metric_entity_alias = alias

            # Requirement 4: If the enriched view cannot be created, dependent metrics -> NULL with the reason; other metrics still deploy.
            if failure is None:
                failed_enriched_map = getattr(self, "failed_enriched_view_datasets", {}) or {}
                failed_enriched_set = {s.casefold() for s in (getattr(self, "failed_enrichment_datasets", set()) or set())}
                if isinstance(failed_enriched_map, dict):
                    failed_enriched_set.update(k.casefold() for k in failed_enriched_map)
                m_ds_key = str(getattr(metric, "dataset", "") or "").casefold()
                if m_ds_key in failed_enriched_set:
                    dax = getattr(metric, "expression", "") or ""
                    from semabridge.converter.scalar_dim_lookup import match_scalar_dim_lookup_pattern
                    if (
                        match_scalar_dim_lookup_pattern(dax)
                        or re.search(r'\b_?CURRENT_[A-Za-z0-9_]+\b', expr, re.IGNORECASE)
                        or "MAX_DATE" in expr
                        or any(s in expr for s in ["IS_YTD", "IS_MTD", "IS_QTD", "IS_SPLY"])
                    ):
                        err_reason = failed_enriched_map.get(m_ds_key, "Enriched view creation failed")
                        failure = (f"Enriched view creation failed for '{metric.dataset}': {err_reason}", expr[:200])

            if failure is not None:
                reason, detail = failure
                logger.warning(
                    "Emitting metric '%s' on '%s' as NULL placeholder (not translatable): %s",
                    metric.unique_name, alias, reason,
                )
                self.drop_ledger.record(
                    "metric", metric.unique_name, DropStage.DAX_TRANSLATION,
                    PLACEHOLDER_NOT_TRANSLATABLE,
                    dataset=getattr(metric, "dataset", None),
                    detail=f"{reason}" + (f" | {detail}" if detail else ""),
                )
                expr = NULL_PLACEHOLDER_SQL
                skipped_metric_names.discard(metric.unique_name)

            safe_metric_name = self.identifier_sanitizer.sanitize_column(metric_name)
            if metric_name != safe_metric_name:
                logger.info(
                    "METRIC DDL FIX: '%s' -> '%s'",
                    metric_name,
                    safe_metric_name,
                )
            syns_to_emit = list(getattr(metric, "synonyms", []) or [])
            if "CAST(NULL AS DOUBLE)" in expr or not getattr(metric, "sync_enabled", True):
                syns_to_emit = []

            non_additive_clause = ""
            non_add_dim = getattr(metric, "non_additive_dimension", None)
            if not non_add_dim and expr != NULL_PLACEHOLDER_SQL and getattr(metric, "expression", None):
                try:
                    from semabridge.converter.dax_ast_parser import DaxAstParser, DaxSqlRenderer, FunctionCallNode, ColumnRefNode, IdentifierNode
                    ast = DaxAstParser().parse(metric.expression)
                    renderer = DaxSqlRenderer()
                    if isinstance(ast, FunctionCallNode) and ast.func.upper() == "CALCULATE" and len(ast.args) > 1:
                        for arg in ast.args[1:]:
                            extracted = renderer._extract_closing_balance_dimension(arg)
                            if extracted:
                                non_add_dim = extracted
                                break
                    elif isinstance(ast, FunctionCallNode) and ast.func.upper() == "IF" and len(ast.args) > 1:
                        cond_node = ast.args[0]
                        if isinstance(cond_node, FunctionCallNode) and cond_node.func.upper() == "HASONEVALUE" and cond_node.args:
                            carg = cond_node.args[0]
                            if isinstance(carg, ColumnRefNode):
                                non_add_dim = (carg.table, carg.column)
                            elif isinstance(carg, IdentifierNode):
                                non_add_dim = (None, carg.name)
                except Exception:
                    pass

            if non_add_dim and expr != NULL_PLACEHOLDER_SQL:
                dim_table, dim_col = non_add_dim
                dim_alias = None
                if dim_table:
                    for ds, a in dataset_aliases.items():
                        if ds.casefold() == dim_table.casefold():
                            dim_alias = a
                            break
                if not dim_alias:
                    dim_alias = metric_entity_alias

                dim_alias_upper = dim_alias.upper()
                emitted_list = getattr(self, "emitted_dimensions", {}).get(dim_alias_upper, [])
                safe_dim_col = self.identifier_sanitizer.sanitize_column(dim_col)

                matched_dim_col = None
                if emitted_list:
                    # 1. Exact match
                    for name in emitted_list:
                        if name == safe_dim_col:
                            matched_dim_col = name
                            break
                    # 2. Case-insensitive match
                    if not matched_dim_col:
                        for name in emitted_list:
                            if name.upper() == safe_dim_col.upper():
                                matched_dim_col = name
                                break
                    # 3. Alphanumeric match (ignoring underscores/spaces)
                    # e.g., 'PeriodNumber' or 'PERIODNUMBER' matches 'PERIOD_NUMBER'
                    if not matched_dim_col:
                        norm_target = re.sub(r"[^A-Za-z0-9]", "", str(dim_col or "")).upper()
                        for name in emitted_list:
                            if re.sub(r"[^A-Za-z0-9]", "", str(name or "")).upper() == norm_target:
                                matched_dim_col = name
                                break
                    # 4. Resolve via SchemaManager against physical dataset schema
                    if not matched_dim_col and hasattr(self, "schema_manager"):
                        dim_ds = dataset_by_name.get(dim_table) if dim_table else None
                        if dim_ds:
                            try:
                                resolved_phys = self.schema_manager._resolve_physical_column_name(
                                    dim_ds, dim_col, model=getattr(self, "_current_model", None)
                                )
                                if resolved_phys:
                                    for name in emitted_list:
                                        if name.upper() == resolved_phys.upper():
                                            matched_dim_col = name
                                            break
                            except Exception:
                                pass

                if matched_dim_col:
                    non_additive_clause = f' NON ADDITIVE BY ( {dim_alias}."{matched_dim_col}" ASC NULLS LAST )'
                elif not emitted_list:
                    # Standalone/mock execution without emitted_dimensions map
                    non_additive_clause = f' NON ADDITIVE BY ( {dim_alias}."{safe_dim_col}" ASC NULLS LAST )'
                else:
                    logger.warning(
                        "Dropping NON ADDITIVE BY for metric '%s': '%s.%s' is not an emitted dimension in DIMENSIONS",
                        metric.unique_name, dim_alias, dim_col,
                    )
                    self.drop_ledger.record(
                        "metric", metric.unique_name, DropStage.DDL_EMISSION,
                        f"NON ADDITIVE BY dimension '{dim_alias}.{dim_col}' is not a declared dimension in the semantic view.",
                        dataset=getattr(metric, "dataset", None),
                    )

            if expr != NULL_PLACEHOLDER_SQL and "PARTITION BY EXCLUDING" in expr.upper():
                expr = self._align_partition_by_excluding(expr)

            if expr != NULL_PLACEHOLDER_SQL:
                from semabridge.converter.scalar_dim_lookup import match_scalar_dim_lookup_pattern
                pat = match_scalar_dim_lookup_pattern(getattr(metric, "expression", "") or "")
                if pat:
                    fact_ds = dataset_by_name.get(metric.dataset)
                    fact_cols = {c.upper() for c in (dataset_col_lookup.get(metric.dataset, set()) or set())}
                    if fact_ds:
                        fact_cols.update(c.unique_name.upper() for c in getattr(fact_ds, "columns", []) or [])
                    c_clean = pat.dim_col.upper()
                    if c_clean in fact_cols and alias:
                        expr = re.sub(rf'\b[A-Za-z0-9_]+\."?{re.escape(c_clean)}"?', f'{alias}."{c_clean}"', expr, flags=re.IGNORECASE)

            comment_clause = ""
            if expr == NULL_PLACEHOLDER_SQL or "CAST(NULL AS DOUBLE)" in expr:
                reason_text = ""
                if failure is not None:
                    reason_text = failure[0]
                elif getattr(metric, "sync_failure_reason", None):
                    reason_text = str(metric.sync_failure_reason)
                if not reason_text:
                    reason_text = "not applicable: not translatable"
                
                reason_clean = re.sub(r"^BY_DESIGN_EXCLUDED:\s*", "", reason_text, flags=re.I).strip()
                if "Snowflake's semantic-view METRICS clause does not support window functions" in reason_clean:
                    reason_clean = "not applicable: needs window metric"
                elif "could not be translated to SQL at DDL-emission time" in reason_clean:
                    dax_upper = str(getattr(metric, "expression", "") or "").upper()
                    if "ALLSELECTED" in dax_upper:
                        reason_clean = "not applicable: depends on report selection (ALLSELECTED)"
                    elif "ISINSCOPE" in dax_upper:
                        reason_clean = "not applicable: depends on hierarchy scope (ISINSCOPE)"
                    elif "SELECTEDVALUE" in dax_upper:
                        reason_clean = "not applicable: depends on slicer selection (SELECTEDVALUE)"
                    elif "RANKX" in dax_upper:
                        reason_clean = "not applicable: rank function not supported in view (RANKX)"
                    elif "USERELATIONSHIP" in dax_upper:
                        reason_clean = "not applicable: dynamic relationship (USERELATIONSHIP)"
                    else:
                        reason_clean = "not applicable: DAX expression unsupported"
                safe_comment = self._clean_comment(reason_clean)
                comment_clause = f" COMMENT = '{safe_comment}'"

            elif "PARTITION BY" in expr.upper():
                if "PARTITION BY EXCLUDING" in expr.upper():
                    m_excl = re.search(r"PARTITION\s+BY\s+EXCLUDING\s+(.*?)(?:\)|$)", expr, re.I | re.S)
                    excl_text = m_excl.group(1).strip().replace('"', "") if m_excl else ""
                    safe_comment = self._clean_comment(f"Window metric over {metric_entity_alias}.{safe_metric_name}. Must be queried with {excl_text}.")
                else:
                    m_part = re.search(r"PARTITION\s+BY\s+(.*?)(?:\)|$)", expr, re.I | re.S)
                    part_text = m_part.group(1).strip().replace('"', "") if m_part else ""
                    safe_comment = self._clean_comment(f"Window metric over {part_text}" if part_text else "Window metric")
                comment_clause = f" COMMENT = '{safe_comment}'"

            elif any(f"IS_CURRENT_{u}" in expr.upper() for u in ("MONTH", "YEAR", "QUARTER", "DAY")):
                for u in ("MONTH", "YEAR", "QUARTER", "DAY"):
                    if f"IS_CURRENT_{u}" in expr.upper():
                        unit_str = u.lower()
                        safe_comment = self._clean_comment(f"latest {unit_str} vs previous {unit_str} only")
                        comment_clause = f" COMMENT = '{safe_comment}'"
                        break

            is_priv = (
                getattr(metric, "access_modifier", None) == "private_access"
                or getattr(metric, "is_private", False)
                or safe_metric_name.endswith(("_SUM", "_CNT", "_NUM", "_DEN"))
            )
            priv_prefix = "PRIVATE " if is_priv else ""

            is_referencing_non_additive = False
            if not non_additive_clause and non_additive_metric_names:
                for na_name in non_additive_metric_names:
                    if re.search(r'\b' + re.escape(na_name) + r'\b', expr, re.IGNORECASE):
                        is_referencing_non_additive = True
                        break

            lhs_target = f'"{safe_metric_name}"' if is_referencing_non_additive else f'{metric_entity_alias}."{safe_metric_name}"'
            metrics_lines.append(
                f'  {priv_prefix}{lhs_target}{non_additive_clause} AS {expr}'
                f'{synonyms_clause(syns_to_emit, None if comment_clause else element_description(metric))}{comment_clause}'
            )
            metric_to_alias[self.identifier_sanitizer.to_alias(metric.unique_name)] = metric_entity_alias
            emittable_metric_name_set.add(self.identifier_sanitizer.to_alias(metric.unique_name))

        # Map every DDL-visible metric name back to its original unique_name +
        # dataset, so drops detected below (line removed between before/after
        # snapshots of metrics_lines) can be recorded against the same
        # identity used everywhere else in this file, not the sanitized DDL
        # alias. `expected_metrics` covers every metric that reached alias
        # resolution, including ones later added by the OSI fallback loop.
        metric_by_unique_name = {m.unique_name: m for m in valid_metrics}
        ddl_name_to_unique: Dict[str, str] = {}
        ddl_name_to_dataset: Dict[str, Optional[str]] = {}
        for _alias, _metric_name, _orig_name in expected_metrics:
            _safe = self.identifier_sanitizer.sanitize_column(_metric_name)
            ddl_name_to_unique[_safe] = _orig_name
            _owner = metric_by_unique_name.get(_orig_name)
            ddl_name_to_dataset[_safe] = getattr(_owner, "dataset", None) if _owner else None

        # Pruning and Fallbacks...
        self._null_propagated = {}
        pre_prune_lines = list(metrics_lines)
        metrics_lines = self._prune_unresolved_metric_lines(metrics_lines, metric_name_set)
        self._record_removed_metric_lines(
            pre_prune_lines, metrics_lines, ddl_name_to_unique, ddl_name_to_dataset,
            "Metric's expression referenced another metric's name that was "
            "never itself emitted (dropped earlier, or removed in this same "
            "cascading pruning pass) — Snowflake's METRICS clause cannot "
            "reference an undefined metric.",
        )

        # Apply recursive metric reference inlining and fail-closed validation pass
        pre_prune_lines = list(metrics_lines)
        metrics_lines = self._prune_unresolved_metric_lines(metrics_lines, emittable_metric_name_set)
        self._record_removed_metric_lines(
            pre_prune_lines, metrics_lines, ddl_name_to_unique, ddl_name_to_dataset,
            "Metric references an unmapped, dropped, or invalid metric and failed closed.",
        )

        for ddl_name, ref_name in sorted(self._null_propagated.items()):
            self.drop_ledger.record(
                "metric", ddl_name_to_unique.get(ddl_name, ddl_name), DropStage.DAX_TRANSLATION,
                PLACEHOLDER_NOT_TRANSLATABLE,
                dataset=ddl_name_to_dataset.get(ddl_name),
                detail=f"depends on placeholder metric {ref_name}",
            )

        # OSI Fallback Loop
        if is_osi:
            metrics_lines = self._apply_osi_fallbacks(
                metrics_lines, expected_metrics, model, dataset_aliases, dataset_col_lookup,
                alias_by_raw, metric_name_set, all_physical_col_names, emittable_metric_name_set,
                skipped_metric_names
            )
            pre_dedup_lines = list(metrics_lines)
            metrics_lines = deduplicate_metrics_lines_osi(metrics_lines)
        else:
            pre_dedup_lines = list(metrics_lines)
            metrics_lines = deduplicate_metrics_lines(metrics_lines)
        self._record_removed_metric_lines(
            pre_dedup_lines, metrics_lines, ddl_name_to_unique, ddl_name_to_dataset,
            "Duplicate metric definition (same table alias + metric name as "
            "an earlier-defined line) — the earlier line was kept and this "
            "later duplicate was dropped.",
        )

        final_metrics_lines = []
        for i, line in enumerate(metrics_lines):
            comma = "," if i < len(metrics_lines) - 1 else ""
            final_metrics_lines.append(f"{line}{comma}")

        return final_metrics_lines

    def _take_translation_failure(self, metric: Any, ledger_mark: int) -> Optional[Tuple[str, Optional[str]]]:
        """Why this metric produced no SQL, taken back out of the drop ledger.

        _generate_metric_expression records a drop for every metric it gives
        up on; those records are replaced by one placeholder record. Returns
        None for a by-design exclusion (left in the ledger, metric stays out).
        """
        records = self.drop_ledger.records
        mine = [r for r in records[ledger_mark:] if r.entity_name == metric.unique_name]
        if any(getattr(r, "by_design", False) for r in mine):
            return None
        for record in mine:
            records.remove(record)
        reason = "; ".join(r.reason for r in mine) or "DAX translation produced no SQL"
        detail = next((r.detail for r in mine if r.detail), None)
        return reason, detail

    @staticmethod
    def _names_in_lines(lines: List[str]) -> Set[str]:
        """Extract the declared metric name (the quoted part before ``AS``)
        from each METRICS-clause line — used to diff a before/after pass of
        ``metrics_lines`` and detect which metrics a pruning/dedup step
        silently removed."""
        names: Set[str] = set()
        for line in lines:
            m = re.search(r'\."([^"]+)"(?:\s+NON\s+ADDITIVE\s+BY\s*\([^)]+\))?\s+AS\s+', line)
            if m:
                names.add(m.group(1))
        return names

    def _record_removed_metric_lines(
        self,
        before: List[str],
        after: List[str],
        ddl_name_to_unique: Dict[str, str],
        ddl_name_to_dataset: Dict[str, Optional[str]],
        reason: str,
    ) -> None:
        """Record a DropLedger entry for every metric name present in
        ``before`` but missing from ``after`` — the generic hook that lets
        any current or future line-removal step (pruning, dedup, ...) stay
        reconciliation-safe without each one re-implementing its own
        bookkeeping."""
        removed = self._names_in_lines(before) - self._names_in_lines(after)
        for name in removed:
            self.drop_ledger.record(
                "metric", ddl_name_to_unique.get(name, name), DropStage.DDL_EMISSION,
                reason, dataset=ddl_name_to_dataset.get(name),
            )

    @staticmethod
    def _is_scalar_metric_sql(expr: str) -> bool:
        if not expr or "{" in expr or "}" in expr:
            return False
        upper = f" {expr.upper()} "
        if "PARTITION BY EXCLUDING" in upper:
            forbidden = (
                " SELECT ",
                " FROM ",
                " JOIN ",
                " WITH ",
                " UNION ",
                ";",
            )
        else:
            forbidden = (
                " SELECT ",
                " FROM ",
                " JOIN ",
                " WITH ",
                " UNION ",
                " OVER ",          # window functions not allowed in METRICS clause
                " PARTITION BY ",  # redundant but explicit
                ";",
            )
        return not any(token in upper for token in forbidden)

    # Matches a bare DAX-only function call (FILTER(, CALCULATE(, etc.) that
    # must never survive into "translated" SQL -- every translation tier
    # (the AST renderer, the regex-based rule translator, the LLM fallback)
    # is expected to either fully resolve one of these into real SQL or
    # decline the whole metric (return None, recorded to drop_ledger), never
    # leave the call itself embedded in what it reports as success. This is
    # the last line of defense before such a fragment reaches Snowflake's
    # own DDL compiler as invalid SQL (e.g. "unexpected 'FILTER'") instead
    # of being cleanly dropped like every other unsupported DAX pattern --
    # _is_scalar_metric_sql above only catches SELECT/JOIN/CTE shapes, and
    # neither of the checks below it is looking for raw DAX syntax either.
    _UNTRANSLATED_DAX_TOKEN_RE = re.compile(
        r"\b(CALCULATE|FILTER|TOPN|RANKX|ALLEXCEPT|ALL|SELECTEDVALUE|SUMX|"
        r"AVERAGEX|MINX|MAXX|COUNTX|EARLIER|USERELATIONSHIP|CROSSFILTER)\s*\(",
        re.IGNORECASE,
    )

    @classmethod
    def _detect_untranslated_dax_syntax(cls, expr: str) -> Optional[str]:
        if not expr:
            return None
        match = cls._UNTRANSLATED_DAX_TOKEN_RE.search(expr)
        if not match:
            return None
        return (
            f"Translated SQL still contains an untranslated DAX construct "
            f"('{match.group(1).upper()}(...)') that no translation tier "
            f"should ever leave embedded. Skipping rather than emitting "
            f"invalid SQL that would fail at Snowflake DDL compile time."
        )

    def _precomputed_column_name(self, source_dataset: str, source_column: str) -> str:
        return self.identifier_sanitizer.sanitize_column(f"{source_dataset}_{source_column}")

    def _get_effective_dataset_cols(
        self,
        dataset_name: str,
        dataset_col_lookup: Dict[str, Set[str]],
    ) -> Set[str]:
        cols = set(dataset_col_lookup.get(dataset_name, set()))
        if self.translator and getattr(self.translator, "anchor_flag_map", None):
            mapped_source = (
                (self.enriched_view_mapping or {}).get(dataset_name)
                or (self.enriched_view_mapping or {}).get(str(dataset_name or "").casefold())
                or ""
            )
            if mapped_source and ("_ENRICHED" in mapped_source.upper()):
                ds_flags = self.translator.anchor_flag_map.get(str(dataset_name or "").casefold()) or {}
                for flag_val in ds_flags.values():
                    if isinstance(flag_val, str) and flag_val:
                        cols.add(flag_val)
        return cols

    def _dataset_has_column(
        self,
        dataset_name: str,
        column_name: str,
        dataset_col_lookup: Dict[str, Set[str]],
    ) -> bool:
        wanted = self.identifier_sanitizer.sanitize_column(column_name).upper()
        cols = self._get_effective_dataset_cols(dataset_name, dataset_col_lookup)
        return wanted in {str(c).upper() for c in cols}

    def _rewrite_cross_dataset_sql_refs_to_precomputed(
        self,
        sql_expr: str,
        active_dataset: str,
        active_alias: str,
        dataset_aliases: Dict[str, str],
        dataset_col_lookup: Dict[str, Set[str]],
    ) -> str:
        """Replace reachable cross-dataset refs with enriched-view columns.

        Snowflake semantic-view metrics cannot freely reference unrelated
        entities from a metric anchored on a fact table. When the enriched view
        already projects SOURCE_COLUMN as SOURCE_COLUMN into the metric dataset,
        this rewrite keeps the metric scalar and single-entity.
        """
        if not sql_expr or not active_dataset or not active_alias:
            return sql_expr

        rewritten = sql_expr
        for source_dataset, source_alias in (dataset_aliases or {}).items():
            if str(source_dataset).casefold() == str(active_dataset).casefold():
                continue
            tokens = {
                self.identifier_sanitizer.to_alias(source_dataset),
                source_alias,
                source_dataset,
            }
            for token in sorted({t for t in tokens if t}, key=len, reverse=True):
                quoted_pattern = re.compile(rf'\b{re.escape(token)}\."([^"]+)"', re.IGNORECASE)

                def replace_quoted(match: re.Match[str]) -> str:
                    source_col = match.group(1)
                    precomputed_col = self._precomputed_column_name(source_dataset, source_col)
                    if self._dataset_has_column(active_dataset, precomputed_col, dataset_col_lookup):
                        return f'{active_alias}."{precomputed_col}"'
                    return match.group(0)

                rewritten = quoted_pattern.sub(replace_quoted, rewritten)

                bare_pattern = re.compile(rf'\b{re.escape(token)}\.([A-Za-z_][A-Za-z0-9_]*)', re.IGNORECASE)

                def replace_bare(match: re.Match[str]) -> str:
                    source_col = match.group(1)
                    precomputed_col = self._precomputed_column_name(source_dataset, source_col)
                    if self._dataset_has_column(active_dataset, precomputed_col, dataset_col_lookup):
                        return f'{active_alias}."{precomputed_col}"'
                    return match.group(0)

                rewritten = bare_pattern.sub(replace_bare, rewritten)
        return rewritten

    def _rewrite_cross_dataset_dax_refs_to_precomputed(
        self,
        dax_expr: str,
        active_dataset: str,
        dataset_col_lookup: Dict[str, Set[str]],
    ) -> str:
        if not dax_expr or not active_dataset:
            return dax_expr

        dataset_names = sorted(dataset_col_lookup.keys(), key=len, reverse=True)

        def replace_ref(match: re.Match[str]) -> str:
            source_dataset = match.group(1).strip().strip("'\"")
            source_column = match.group(2).strip()
            resolved_source = next(
                (ds for ds in dataset_names if ds.casefold() == source_dataset.casefold()),
                source_dataset,
            )
            if resolved_source.casefold() == str(active_dataset).casefold():
                return match.group(0)
            precomputed_col = self._precomputed_column_name(resolved_source, source_column)
            if not self._dataset_has_column(active_dataset, precomputed_col, dataset_col_lookup):
                return match.group(0)
            return f"'{active_dataset}'[{precomputed_col}]"

        pattern = re.compile(r"[\'\"]?([^\'\"\[\]\(\),]+)[\'\"]?\s*\[\s*([^\]]+?)\s*\]")
        return pattern.sub(replace_ref, dax_expr)

    def _remap_virtual_measures_table_refs(
        self,
        sql_expr: str,
        measures_alias: str,
        dataset_col_lookup: Dict[str, Set[str]],
        dataset_aliases: Dict[str, str],
        fact_aliases: Optional[Set[str]] = None,
    ) -> Optional[str]:
        """Remap references from a virtual MEASURES table alias to real owning tables.

        Returns ``None`` if at least one referenced column cannot be mapped safely.
        """
        if not sql_expr:
            return sql_expr

        physical_datasets = [
            ds for ds in dataset_col_lookup.keys()
            if not self._is_virtual_measures_table(ds, dataset_col_lookup)
        ]
        # Prefer fact datasets first when resolving ambiguous ownership.
        if fact_aliases:
            physical_datasets.sort(
                key=lambda ds: 0 if dataset_aliases.get(ds) in fact_aliases else 1
            )

        pattern = re.compile(
            rf'(?P<alias>{re.escape(measures_alias)})\s*\.\s*(?P<col>"[^"]+"|[A-Za-z_][A-Za-z0-9_$]*)'
        )

        unresolved = False

        def _replace(match: re.Match) -> str:
            nonlocal unresolved
            raw_col = match.group("col")
            col_name = raw_col.strip('"')

            owners: list[str] = []
            for ds in physical_datasets:
                cols = set(dataset_col_lookup.get(ds, set()))
                if self.translator and getattr(self.translator, "anchor_flag_map", None):
                    ds_flags = self.translator.anchor_flag_map.get(str(ds or "").casefold()) or {}
                    for flag_val in ds_flags.values():
                        if isinstance(flag_val, str) and flag_val:
                            cols.add(flag_val)
                resolved_col = self.translator._resolve_column_name_for_dataset(cols, col_name)
                if resolved_col:
                    owners.append(ds)

            if not owners:
                unresolved = True
                return match.group(0)

            chosen_dataset = owners[0]
            chosen_alias = dataset_aliases.get(chosen_dataset)
            if not chosen_alias:
                unresolved = True
                return match.group(0)

            chosen_dataset_cols = set(dataset_col_lookup.get(chosen_dataset, set()))
            if self.translator and getattr(self.translator, "anchor_flag_map", None):
                ds_flags = self.translator.anchor_flag_map.get(str(chosen_dataset or "").casefold()) or {}
                for flag_val in ds_flags.values():
                    if isinstance(flag_val, str) and flag_val:
                        chosen_dataset_cols.add(flag_val)

            chosen_col = (
                self.translator._resolve_column_name_for_dataset(
                    chosen_dataset_cols,
                    col_name,
                )
                or col_name
            )
            return f'{chosen_alias}."{chosen_col}"'

        rewritten = pattern.sub(_replace, sql_expr)

        if unresolved:
            logger.warning(
                "Could not resolve all virtual measures-table references for alias '%s'.",
                measures_alias,
            )
            return None

        return rewritten

    def _is_virtual_measures_table(self, dataset_name: Optional[str], dataset_col_lookup: Dict[str, Set[str]]) -> bool:
        """Detect virtual measures tables (containers with no physical data columns)."""
        if not dataset_name:
            return False
        ds = getattr(self, "dataset_by_name", {}).get(dataset_name)
        if ds is not None and getattr(ds, "is_measure_table", False):
            return True
        cols = dataset_col_lookup.get(dataset_name, set())
        if not cols:
            return True
        non_id_cols = {c for c in cols if c.upper() not in ("ID", "NAME", "DESCRIPTION", "_ID")}
        return len(non_id_cols) == 0

    def _find_physical_owner_for_column(self, col_name: str, dataset_col_lookup: Dict[str, Set[str]]) -> Optional[str]:
        for ds, cols in dataset_col_lookup.items():
            if self._is_virtual_measures_table(ds, dataset_col_lookup):
                continue
            effective_cols = set(cols)
            if self.translator and getattr(self.translator, "anchor_flag_map", None):
                ds_flags = self.translator.anchor_flag_map.get(str(ds or "").casefold()) or {}
                for flag_val in ds_flags.values():
                    if isinstance(flag_val, str) and flag_val:
                        effective_cols.add(flag_val)
            resolved = self.translator._resolve_column_name_for_dataset(effective_cols, col_name)
            if resolved:
                return ds
        return None

    def _generate_metric_expression(
        self,
        metric: Any,
        metric_name: str,
        alias: str,
        dataset_by_name: Dict[str, Any],
        dataset_aliases: Dict[str, str],
        dataset_col_lookup: Dict[str, Set[str]],
        alias_by_raw: Dict[str, str],
        metric_name_set: Set[str],
        all_physical_col_names: Set[str],
        emittable_metric_name_set: Set[str],
        skipped_metric_names: Set[str],
        model_name: str,
        is_osi: bool,
        fact_aliases: Optional[Set[str]] = None,
        metric_to_alias: Optional[Dict[str, str]] = None,
        dataset_col_types: Optional[Dict[str, Dict[str, str]]] = None,
        metric_shapes: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        # A by-design-excluded metric (constant expression / string-producing
        # root, classified earlier in the pipeline — see dax_ast_parser.py's
        # dax_has_zero_data_dependencies/dax_root_is_string_producing) was
        # never a translation candidate to begin with. Without this check,
        # every fallback below (LLM, then basic DAX patterns) gets an
        # independent shot at "translating" its raw DAX expression, and a
        # naive literal-conversion fallback can succeed at turning something
        # like a DAX empty-string literal into a valid — but meaningless —
        # SQL empty-string literal, silently undoing the earlier exclusion.
        from semabridge.converter.dax_ast_parser import is_by_design_excluded
        if getattr(metric, "sync_enabled", True) is False or is_by_design_excluded(getattr(metric, "sync_failure_reason", None)):
            logger.debug(
                "Metric '%s': not applicable / disabled — emitting NULL placeholder in DDL.",
                metric.unique_name,
            )
            self.drop_ledger.record(
                "metric", metric.unique_name, DropStage.DAX_TRANSLATION,
                getattr(metric, "sync_failure_reason", None) or "Metric translation disabled (not applicable).",
                dataset=getattr(metric, "dataset", None),
                by_design=False,
            )
            return None

        if (metric.source_column and metric.aggregation and
            (not getattr(metric, "sql_expression", None) or self._should_use_direct_metric_aggregation(metric))):
            
            col_name = (self.identifier_sanitizer.sanitize_column(metric.source_column) if is_osi 
                        else self.schema_manager._resolve_physical_column_name(dataset_by_name.get(metric.dataset), metric.source_column))
            agg = metric.aggregation.value.upper()
            
            # Check for physical owner
            owners = [ds for ds, cols in dataset_col_lookup.items() 
                      if self.translator._resolve_column_name_for_dataset(cols, col_name)]
            
            if owners:
                preferred_owner = None
                if metric.dataset in owners and not self._is_virtual_measures_table(metric.dataset, dataset_col_lookup):
                    preferred_owner = metric.dataset
                else:
                    # Filter out virtual measures tables from owners
                    non_virtual_owners = [o for o in owners if not self._is_virtual_measures_table(o, dataset_col_lookup)]
                    if len(non_virtual_owners) == 1:
                        preferred_owner = non_virtual_owners[0]
                    elif len(non_virtual_owners) > 1:
                        # Prefer fact table if available
                        fact_owners = [o for o in non_virtual_owners if dataset_aliases.get(o) in (fact_aliases or set())]
                        preferred_owner = fact_owners[0] if fact_owners else non_virtual_owners[0]
                
                if preferred_owner:
                    owner_alias = dataset_aliases.get(preferred_owner)
                    owner_col = self.translator._resolve_column_name_for_dataset(dataset_col_lookup.get(preferred_owner, set()), col_name) or col_name
                    if owner_alias:
                        if agg == "COUNT_DISTINCT":
                            return f'COUNT(DISTINCT {self.sanitizer.format_physical_column_ref(owner_alias, owner_col, model_name=model_name)})'
                        elif agg == "NONE":
                            return f'{self.sanitizer.format_physical_column_ref(owner_alias, owner_col, model_name=model_name)}'
                        else:
                            return f'{agg}({self.sanitizer.format_physical_column_ref(owner_alias, owner_col, model_name=model_name)})'
            else:
                # Column not found in live schema metadata — trust the source model's
                # dataset assignment and emit directly against the metric's own alias.
                # This avoids falling through to sql_expression which may reference
                # a non-existent column on a dimension/lookup table (e.g. MEASURES table).
                if alias and col_name:
                    # Skip metrics bound to virtual Power BI measures tables — they have
                    # no real data columns in Snowflake and will always fail.
                    if not sql_expr and self._is_virtual_measures_table(metric.dataset, dataset_col_lookup):
                        logger.warning(
                            "Metric '%s': dataset '%s' appears to be a virtual measures table "
                            "with no physical columns. Skipping to prevent DDL failure.",
                            metric.unique_name, metric.dataset
                        )
                        self.drop_ledger.record(
                            "metric", metric.unique_name, DropStage.DDL_EMISSION,
                            f"Dataset '{metric.dataset}' is a virtual Power BI measures table "
                            "with no physical columns in Snowflake.",
                            dataset=metric.dataset,
                        )
                        return None
                    logger.debug(
                        "Metric '%s': column '%s' not found in schema metadata; "
                        "emitting against declared dataset alias '%s'",
                        metric.unique_name, col_name, alias
                    )
                    if agg == "COUNT_DISTINCT":
                        return f'COUNT(DISTINCT {self.sanitizer.format_physical_column_ref(alias, col_name, model_name=model_name)})'
                    elif agg == "NONE":
                        return f'{self.sanitizer.format_physical_column_ref(alias, col_name, model_name=model_name)}'
                    else:
                        return f'{agg}({self.sanitizer.format_physical_column_ref(alias, col_name, model_name=model_name)})'

        # SQL Expression path
        sql_expr = getattr(metric, "sql_expression", None)
        dax_expr = getattr(metric, "expression", None)

        if getattr(metric, "is_placeholder", False):
            return sql_expr or "CAST(NULL AS DOUBLE)"

        if sql_expr and is_null_cast_sql(sql_expr):
            # A prior stage (or a previous run's persisted translation) already
            # gave up on this metric and stored the NULL-cast placeholder as
            # its sql_expression. Treat it exactly like "no sql_expression" —
            # fall through to a DAX-expression retry if one exists (that path
            # records its own success/failure), rather than validating and
            # emitting the placeholder as if it were real SQL. Only record
            # here when there's no DAX fallback to attempt, so this doesn't
            # double up with the DAX-expression branch's own record below.
            if not dax_expr:
                logger.warning(
                    "Metric '%s': stored sql_expression is a NULL-cast placeholder "
                    "from an earlier translation attempt that declined to translate, "
                    "and no DAX expression exists to retry. Skipping rather than "
                    "emitting it as if valid.",
                    metric.unique_name,
                )
                self.drop_ledger.record(
                    "metric", metric.unique_name, DropStage.DAX_TRANSLATION,
                    "A prior translation stage stored a NULL-cast placeholder instead of "
                    "real SQL for this metric's expression, and no DAX expression exists "
                    "to retry — treating as untranslated rather than emitting a dead "
                    "metric silently.",
                    dataset=getattr(metric, "dataset", None),
                )
                return None
            logger.warning(
                "Metric '%s': stored sql_expression is a NULL-cast placeholder from "
                "an earlier translation attempt that declined to translate. Falling "
                "back to its DAX expression instead of emitting the placeholder as "
                "if valid.",
                metric.unique_name,
            )
            sql_expr = None

        if not sql_expr and self._is_virtual_measures_table(metric.dataset, dataset_col_lookup):
            logger.warning(f"Metric '{metric.unique_name}': virtual measures table, trying DAX fallback")
            # Fall through to DAX expression path
            dax_expr = getattr(metric, "expression", None)
            if dax_expr:
                # Continue to DAX translation
                sql_expr = None
            else:
                self.drop_ledger.record(
                    "metric", metric.unique_name, DropStage.DDL_EMISSION,
                    f"Dataset '{metric.dataset}' is a virtual Power BI measures table with no "
                    "physical columns, and the metric has no DAX expression to fall back to.",
                    dataset=metric.dataset,
                )
                return None
        
        # If we have SQL expression, use it directly
        if sql_expr:
            # Guard: Snowflake's METRICS clause only accepts scalar expressions.
            # Subqueries (SELECT …) and CTEs (WITH …) are illegal and cause
            # "unexpected 'SELECT'" DDL compilation errors.  This can happen when
            # an older translation stored a subquery-based expression, or if a new
            # translation path regresses.  Skip the metric rather than emitting
            # invalid DDL; the caller will surface a sync_failure_reason.
            if "SELECT" in sql_expr.upper():
                logger.warning(
                    "Metric '%s': sql_expression contains a subquery (SELECT) which is "
                    "not allowed in Snowflake METRICS clause. Skipping to prevent DDL "
                    "failure. Expression (first 120 chars): %s",
                    metric.unique_name,
                    sql_expr[:120],
                )
                self.drop_ledger.record(
                    "metric", metric.unique_name, DropStage.DDL_EMISSION,
                    "SQL expression contains a subquery (SELECT), which Snowflake's "
                    "semantic-view METRICS clause does not support.",
                    dataset=getattr(metric, "dataset", None), detail=sql_expr[:200],
                )
                return None

            UNTRANSLATED_DAX_PATTERN = re.compile(
                r"\b(CALCULATE|FILTER|ALL|EARLIER|VALUES|USERELATIONSHIP|ALLEXCEPT|CROSSFILTER|SUMX|AVERAGEX|COUNTX|MINX|MAXX)\b",
                re.IGNORECASE,
            )
            if UNTRANSLATED_DAX_PATTERN.search(sql_expr):
                logger.warning(
                    "Metric '%s': sql_expression contains untranslated DAX constructs (%s). "
                    "Skipping to prevent DDL compilation failure.",
                    metric.unique_name,
                    sql_expr[:120],
                )
                self.drop_ledger.record(
                    "metric", metric.unique_name, DropStage.DDL_EMISSION,
                    "SQL expression contains untranslated DAX functions, which Snowflake's "
                    "semantic-view METRICS clause does not support.",
                    dataset=getattr(metric, "dataset", None), detail=sql_expr[:200],
                )
                return None
            # If the metric's dataset is a virtual measures table, try to remap
            # column references to the actual fact table that owns those columns.
            if not sql_expr and self._is_virtual_measures_table(metric.dataset, dataset_col_lookup):
                sql_expr = self._remap_virtual_measures_table_refs(
                    sql_expr, alias, dataset_col_lookup, dataset_aliases, fact_aliases=fact_aliases
                )
                if sql_expr is None:
                    logger.warning(
                        "Metric '%s': could not remap virtual measures table references. Skipping.",
                        metric.unique_name
                    )
                    self.drop_ledger.record(
                        "metric", metric.unique_name, DropStage.DDL_EMISSION,
                        "Could not resolve all virtual measures-table column references to a "
                        "real fact-table owner.",
                        dataset=getattr(metric, "dataset", None),
                    )
                    return None
            expr = sql_expr
            if expr:
                expr = re.sub(r'(>|<|=|\+|-|\*|/)\s*(?:["\']_["\']|(?<!\w)_(?!\w))', r'\1 0', expr)
            
            # ✅ NEW: Qualify cross-table references FIRST (before normalization)
            expr = self.translator._auto_qualify_cross_table_refs(expr, dataset_aliases)
            expr = self._rewrite_cross_dataset_sql_refs_to_precomputed(
                expr,
                metric.dataset,
                alias,
                dataset_aliases,
                dataset_col_lookup,
            )
            
            # Then normalize and validate
            expr = self.translator._normalize_metric_column_references(
                expr, metric.unique_name, dataset_col_lookup, dataset_aliases, 
                metric_names=metric_name_set, preferred_table_alias=alias,
                metric_to_alias=metric_to_alias
            )
            is_valid, reason = self.translator._validate_metric_column_references(
                expr,
                metric.unique_name,
                dataset_col_lookup,
                dataset_aliases,
                metric_names=metric_name_set,
            )
            if not is_valid:
                # Before treating this as a genuine failure: is the
                # "unknown" column actually the enrichment flag column
                # this metric's own resolved time-intelligence shape
                # predicts (e.g. IS_YTD)? dry-run's dataset_col_lookup is
                # always built from the model's declared (pre-enrichment)
                # columns only -- it can never see a column
                # _create_enriched_view only creates at real-deploy time,
                # so a translation this codebase's own enrichment
                # mechanism already knows how to satisfy must not be
                # reported as a hard DDL-emission failure. See
                # time_intelligence_shapes.py's enrichment_flag_column_
                # if_referenced.
                window_col_match = re.search(r'\b(?:MAX|SUM|AVG|MIN)\s*\(\s*[A-Za-z0-9_]+\."?(_?ALL_[A-Za-z0-9_]+)"?\s*\)', expr, re.IGNORECASE)
                if window_col_match:
                    has_enriched_target = bool(
                        self.enriched_view_mapping
                        and (
                            self.enriched_view_mapping.get(metric.dataset)
                            or self.enriched_view_mapping.get(str(metric.dataset or "").casefold())
                            or self.enriched_view_mapping.get(str(metric.dataset or "").upper())
                        )
                        and str(metric.dataset).casefold() not in {s.casefold() for s in (self.failed_enrichment_datasets or set())}
                    )
                    if has_enriched_target:
                        return expr

                scalar_anchor_match = re.search(r'\b_?CURRENT_[A-Za-z0-9_]+\b', expr, re.IGNORECASE)
                if scalar_anchor_match:
                    has_enriched_target = bool(
                        self.enriched_view_mapping
                        and (
                            self.enriched_view_mapping.get(metric.dataset)
                            or self.enriched_view_mapping.get(str(metric.dataset or "").casefold())
                            or self.enriched_view_mapping.get(str(metric.dataset or "").upper())
                        )
                        and str(metric.dataset).casefold() not in {s.casefold() for s in (self.failed_enrichment_datasets or set())}
                    )
                    if has_enriched_target:
                        return expr

                shape = (metric_shapes or {}).get(metric.unique_name)
                predicted_flag_col = enrichment_flag_column_if_referenced(shape, expr)
                if predicted_flag_col:
                    has_enriched_target = bool(
                        self.enriched_view_mapping
                        and (
                            self.enriched_view_mapping.get(metric.dataset)
                            or self.enriched_view_mapping.get(str(metric.dataset or "").casefold())
                            or self.enriched_view_mapping.get(str(metric.dataset or "").upper())
                        )
                        and str(metric.dataset).casefold() not in {s.casefold() for s in (self.failed_enrichment_datasets or set())}
                    )
                    if has_enriched_target and self.live_schema_metadata:
                        enriched_vname = (
                            self.enriched_view_mapping.get(metric.dataset)
                            or self.enriched_view_mapping.get(str(metric.dataset or "").casefold())
                            or self.enriched_view_mapping.get(str(metric.dataset or "").upper())
                        )
                        live_v_cols = self.live_schema_metadata.get(str(enriched_vname).upper(), set())
                        if live_v_cols and predicted_flag_col.upper() not in {c.upper() for c in live_v_cols}:
                            has_enriched_target = False

                    if has_enriched_target:
                        honest_note = (
                            f"Cannot verify in dry-run — resolved by live enrichment at deploy time "
                            f"(references '{predicted_flag_col}', a flag column "
                            f"_create_enriched_view creates on '{metric.dataset}' for this metric's "
                            f"time-intelligence shape; dry-run has no live connection to confirm it "
                            f"directly)."
                        )
                        if hasattr(metric, "advisory_notes") and honest_note not in metric.advisory_notes:
                            metric.advisory_notes = list(metric.advisory_notes) + [honest_note]
                            metric.advisory_categories = list(metric.advisory_categories) + [
                                ADVISORY_CATEGORY_ENRICHMENT_COLUMN_UNVERIFIABLE
                            ]
                        logger.info(
                            "Metric '%s': column '%s' not in dry-run's declared schema, but matches "
                            "this metric's predicted enrichment flag column — emitting as unverified "
                            "rather than dropping.",
                            metric.unique_name, predicted_flag_col,
                        )
                        return expr
                    else:
                        self.drop_ledger.record(
                            "metric", metric.unique_name, DropStage.DDL_EMISSION,
                            f"Metric references enrichment flag column '{predicted_flag_col}' but dataset '{metric.dataset}' is not pointing to an enriched view in TABLES clause",
                            dataset=getattr(metric, "dataset", None), detail=expr[:200] if expr else None,
                        )
                        return None
                logger.warning(
                    "Metric '%s': skipping invalid SQL expression after normalization: %s",
                    metric.unique_name,
                    reason or "unknown reference error",
                )
                self.drop_ledger.record(
                    "metric", metric.unique_name, DropStage.DDL_EMISSION,
                    f"SQL expression references a column that could not be resolved after "
                    f"normalization: {reason or 'unknown reference error'}",
                    dataset=getattr(metric, "dataset", None), detail=expr[:200] if expr else None,
                )
                return None
            return expr
        
        # If we have DAX expression, try to translate it
        if dax_expr:
            active_dataset = metric.dataset
            active_alias = alias
            
            if not sql_expr and self._is_virtual_measures_table(metric.dataset, dataset_col_lookup):
                cols_in_dax = re.findall(r"\[([^\]]+)\]", dax_expr)
                for c in cols_in_dax:
                    sanitized_c = self.identifier_sanitizer.sanitize_column(c)
                    owner = self._find_physical_owner_for_column(sanitized_c, dataset_col_lookup)
                    if owner:
                        active_dataset = owner
                        active_alias = dataset_aliases.get(owner) or alias
                        break

            rewritten_dax_expr = self._rewrite_cross_dataset_dax_refs_to_precomputed(
                dax_expr,
                active_dataset,
                dataset_col_lookup,
            )
            
            class MetricWrapper:
                def __init__(self, orig, dataset, expression):
                    self._orig = orig
                    self.dataset = dataset
                    self.expression = expression
                def __getattr__(self, name):
                    return getattr(self._orig, name)
                    
            wrapped_metric = MetricWrapper(metric, active_dataset, rewritten_dax_expr)

            # Try LLM fallback first when configured (OpenAI is preferred inside
            # the translator). This keeps complex Fabric DAX from being dropped
            # before provider-backed translation gets a chance.
            translated = self.translator._try_llm_metric_fallback_expression(
                metric=wrapped_metric,
                metric_name=metric_name,
                table_alias=active_alias,
                alias_by_raw=alias_by_raw,
                dataset_col_lookup=dataset_col_lookup,
                dataset_aliases=dataset_aliases,
                metric_name_set=metric_name_set,
                all_physical_col_names=all_physical_col_names,
                emittable_metric_name_set=emittable_metric_name_set,
                skipped_metric_names=skipped_metric_names,
                metric_to_alias=metric_to_alias,
                dataset_col_types=dataset_col_types,
            )
            if translated:
                # ✅ NEW: Qualify cross-table references in translated SQL
                translated = self.translator._auto_qualify_cross_table_refs(translated, dataset_aliases)
                translated = self._rewrite_cross_dataset_sql_refs_to_precomputed(
                    translated,
                    active_dataset,
                    active_alias,
                    dataset_aliases,
                    dataset_col_lookup,
                )
                return translated

            # Try basic DAX translation first (COUNTROWS, COUNTBLANK, etc.)
            translated = self.translator._try_basic_dax_metric_fallback_expression(
                wrapped_metric, active_alias, dataset_col_lookup, model=None, dataset_by_name=dataset_by_name
            )
            if translated:
                translated = self._rewrite_cross_dataset_sql_refs_to_precomputed(
                    translated,
                    active_dataset,
                    active_alias,
                    dataset_aliases,
                    dataset_col_lookup,
                )
                return translated

            # Translation failed — skip this metric rather than emitting invalid SQL
            logger.warning(
                "Could not translate metric '%s' with expression '%s'. "
                "Skipping metric to prevent DDL compilation failure.",
                metric.unique_name, dax_expr[:100]
            )
            dax_upper = str(dax_expr or "").upper()
            if "ALLSELECTED" in dax_upper:
                dax_reason = "not applicable: depends on report selection (ALLSELECTED)"
            elif "ISINSCOPE" in dax_upper:
                dax_reason = "not applicable: depends on hierarchy scope (ISINSCOPE)"
            elif "SELECTEDVALUE" in dax_upper:
                dax_reason = "not applicable: depends on slicer selection (SELECTEDVALUE)"
            elif "RANKX" in dax_upper:
                dax_reason = "not applicable: rank function not supported in view (RANKX)"
            elif "USERELATIONSHIP" in dax_upper:
                dax_reason = "not applicable: dynamic relationship (USERELATIONSHIP)"
            else:
                dax_reason = "not applicable: DAX expression unsupported"
            self.drop_ledger.record(
                "metric", metric.unique_name, DropStage.DAX_TRANSLATION,
                dax_reason,
                dataset=getattr(metric, "dataset", None), detail=dax_expr[:200] if dax_expr else None,
            )
            return None

        self.drop_ledger.record(
            "metric", metric.unique_name, DropStage.DDL_EMISSION,
            "Metric has no source_column+aggregation, no sql_expression, and no DAX "
            "expression to translate — nothing to emit.",
            dataset=getattr(metric, "dataset", None),
        )
        return None

    def _apply_osi_fallbacks(
        self, metrics_lines: List[str], expected_metrics: List[Any], osi: Any, 
        dataset_aliases: Dict[str, str], dataset_col_lookup: Dict[str, Set[str]],
        alias_by_raw: Dict[str, str], metric_name_set: Set[str], 
        all_physical_col_names: Set[str], emittable_metric_name_set: Set[str],
        skipped_metric_names: Set[str]
    ) -> List[str]:
        """OSI-specific metric fallbacks - generate from OSI structure."""
        metrics_list = getattr(osi, "metrics", None) or getattr(osi, "measures", [])
        # Exact-name membership, not substring: `metric_name in line` would
        # false-positive whenever a sibling metric's disambiguated name
        # ("X_2") contains this metric's base name ("X") as a substring,
        # wrongly concluding X already has a line and skipping its fallback
        # attempt (X still has a DropLedger record from the primary attempt
        # in _generate_metric_expression, but is denied a chance to recover).
        already_emitted = self._names_in_lines(metrics_lines)
        for alias, metric_name, original_name in expected_metrics:
            if metric_name not in already_emitted:
                # Find the OSI measure
                for measure in metrics_list:
                    if measure.unique_name == original_name:
                        # Generate SQL from measure's source column
                        if measure.source_column and measure.aggregation:
                            agg_val = measure.aggregation.value if hasattr(measure.aggregation, "value") else str(measure.aggregation)
                            agg_val = agg_val.upper()
                            sql = f'{agg_val}({alias}."{measure.source_column}")'
                            metrics_lines.append(f'  {alias}."{metric_name}" AS {sql}')
                            already_emitted.add(metric_name)
                        break
        return metrics_lines


    def _should_use_direct_metric_aggregation(self, metric: Any) -> bool:
        if not getattr(metric, "source_column", None) or not getattr(metric, "aggregation", None):
            return False
        complexity_tier = getattr(metric, "complexity_tier", 1) or 1
        if complexity_tier > 1:
            return False
        expr = (getattr(metric, "expression", None) or "").strip().upper()
        if not expr:
            return True
        simple_patterns = ("SUM(", "COUNT(", "DISTINCTCOUNT(", "AVERAGE(", "MIN(", "MAX(")
        return expr.startswith(simple_patterns)

    def _build_duplicate_signature_seed(self, source_name: str, source_expression: Optional[str], data_type: Optional[str], aggregation: Optional[str] = None) -> str:
        return "|".join([source_name or "", source_expression or "", data_type or "", aggregation or ""])

    def _duplicate_namespace_key(self, model_name: Optional[str] = None) -> str:
        parts: List[str] = [
            str(self.identifier_sanitizer.to_alias(self.config.database or "DB")),
            str(self.identifier_sanitizer.to_alias(self.config.schema_name or "SCHEMA")),
        ]
        if model_name:
            alias = self.identifier_sanitizer.to_alias(model_name)
            if alias:
                parts.append(str(alias))
        return str(".".join(parts))

    def _resolve_persistent_duplicate_name(self, scope_type: str, namespace_key: str, dataset_key: str, normalized_base: str, source_name: str, source_signature: str, preferred_name: str) -> str:
        if not self.dup_name_repo:
            return preferred_name
        try:
            assigned = self.dup_name_repo.get_or_create_assigned_name(
                scope_type=scope_type, namespace_key=namespace_key, dataset_key=dataset_key, 
                normalized_base=normalized_base, source_name=source_name, source_signature=source_signature, 
                preferred_name=preferred_name
            )
            return str(assigned) if assigned is not None else preferred_name
        except Exception as exc:
            logger.warning("Duplicate mapping failed for %s: %s", source_name, exc)
            return preferred_name

    def _resolve_unique_metric_alias(self, base_alias: str, used_aliases: Set[str], original_metric_name: str) -> str:
        if base_alias not in used_aliases:
            used_aliases.add(base_alias)
            return base_alias
        idx = 2
        while True:
            candidate = f"{base_alias}_{idx}"
            if candidate not in used_aliases:
                used_aliases.add(candidate)
                return candidate
            idx += 1

    @staticmethod
    def _is_reachable_alias(graph: Dict[str, Set[str]], start_alias: str, target_alias: str) -> bool:
        if start_alias == target_alias:
            return True
        if not graph:
            return True
        visited = {start_alias}
        queue = [start_alias]
        while queue:
            curr = queue.pop(0)
            for neighbor in graph.get(curr, set()):
                if neighbor == target_alias:
                    return True
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        return False

    @staticmethod
    def _reachable_from(src: str, directed_reach: Optional[Dict[str, Set[str]]]) -> Set[str]:
        if not directed_reach:
            return {src}
        seen = {src}
        st = [src]
        while st:
            curr = st.pop()
            for neighbor in directed_reach.get(curr, ()):
                if neighbor not in seen:
                    seen.add(neighbor)
                    st.append(neighbor)
        return seen

    def _resolve_metric_emission_alias(
        self,
        default_alias: str,
        metric_sql: str,
        dataset_aliases: Dict[str, str],
        fact_aliases: Optional[Set[str]] = None,
        dax_expression: Optional[str] = None,
        measure_dax_map: Optional[Dict[str, str]] = None,
        directed_reach: Optional[Dict[str, Set[str]]] = None,
    ) -> str:
        valid_aliases = set(dataset_aliases.values())
        if metric_sql and "OVER" in metric_sql.upper():
            # For window metrics, Snowflake rule 010263 requires the window metric to be placed
            # on the exact same table as the inner metric or aggregate inside its OVER clause,
            # never on an excluded dimension in PARTITION BY EXCLUDING.
            win_matches = re.findall(
                r'(?:SUM|AVG|COUNT|MIN|MAX)\s*\(\s*\(?\s*([A-Za-z0-9_]+)\."?[A-Za-z0-9_]+"?',
                metric_sql,
                re.IGNORECASE,
            )
            for tbl_cand in win_matches:
                for a in valid_aliases:
                    if a.upper() == tbl_cand.upper():
                        return a

        # DAX lineage: extract tables aggregated by the DAX expression
        if dax_expression:
            from semabridge.converter.dax_lineage import resolve_dax_lineage_tables
            lineage_tables = resolve_dax_lineage_tables(
                dax_expression,
                measure_dax_map=measure_dax_map,
                default_home_table=default_alias,
            )
            if lineage_tables:
                matched_aliases: Set[str] = set()
                for lt in lineage_tables:
                    clean_lt = lt.strip("'").strip('"')
                    for ds_name, al in dataset_aliases.items():
                        if ds_name.casefold() == clean_lt.casefold():
                            matched_aliases.add(al)
                    for val_al in valid_aliases:
                        if val_al.casefold() == clean_lt.casefold():
                            matched_aliases.add(val_al)

                if matched_aliases:
                    if directed_reach:
                        valid_reach_cands = {
                            al for al in matched_aliases
                            if matched_aliases.issubset(self._reachable_from(al, directed_reach))
                        }
                        if valid_reach_cands:
                            matched_aliases = valid_reach_cands
                    if len(matched_aliases) == 1:
                        return next(iter(matched_aliases))
                    if fact_aliases:
                        fact_matches = matched_aliases & fact_aliases
                        if len(fact_matches) == 1:
                            return next(iter(fact_matches))
                        if fact_matches:
                            return sorted(fact_matches)[0]
                    return sorted(matched_aliases)[0]

        referenced_aliases = self._extract_referenced_table_aliases(metric_sql, valid_aliases)
        if directed_reach and referenced_aliases:
            valid_cands = {
                al for al in valid_aliases
                if referenced_aliases.issubset(self._reachable_from(al, directed_reach))
            }
            if valid_cands:
                if default_alias in valid_cands:
                    return default_alias
                if fact_aliases:
                    fact_cands = valid_cands & fact_aliases
                    if len(fact_cands) == 1:
                        return next(iter(fact_cands))
                    if fact_cands:
                        return sorted(fact_cands)[0]
                ref_cands = valid_cands & referenced_aliases
                if len(ref_cands) == 1:
                    return next(iter(ref_cands))
                if ref_cands:
                    return sorted(ref_cands)[0]
                return sorted(valid_cands)[0]

        if len(referenced_aliases) == 1:
            return next(iter(referenced_aliases))
        if len(referenced_aliases) > 1 and fact_aliases:
            # Prefer fact table aliases over dimension aliases to avoid
            # "invalid identifier 'DIM_TABLE.COLUMN'" errors in Snowflake
            fact_refs = referenced_aliases & fact_aliases
            if len(fact_refs) == 1:
                return next(iter(fact_refs))
            if fact_refs:
                return sorted(fact_refs)[0]
        # Virtual measures-table metrics cannot safely anchor to MEASURES when
        # expression spans multiple entities. Prefer a concrete referenced alias.
        if referenced_aliases and self._is_virtual_measures_alias(default_alias):
            fact_like_refs = sorted(a for a in referenced_aliases if "FACT" in a.upper())
            if fact_like_refs:
                return fact_like_refs[0]
            non_virtual_refs = sorted(a for a in referenced_aliases if not self._is_virtual_measures_alias(a))
            if non_virtual_refs:
                return non_virtual_refs[0]
        # If no table aliases found in expression (e.g. pure metric-reference expression)
        # and the default alias is a virtual measures container, inspect DAX or fact aliases.
        if not referenced_aliases and self._is_virtual_measures_alias(default_alias):
            if dax_expression:
                for ds_name, al in dataset_aliases.items():
                    if not self._is_virtual_measures_alias(al) and (
                        re.search(rf"\b{re.escape(ds_name)}\b", dax_expression, re.IGNORECASE)
                        or re.search(rf"\b{re.escape(al)}\b", dax_expression, re.IGNORECASE)
                    ):
                        return al
            if fact_aliases:
                non_virtual_facts = [a for a in fact_aliases if not self._is_virtual_measures_alias(a)]
                if non_virtual_facts:
                    return sorted(non_virtual_facts)[0]
        if not referenced_aliases and fact_aliases and default_alias not in fact_aliases:
            if len(fact_aliases) == 1:
                return next(iter(fact_aliases))
        return default_alias

    def _is_virtual_measures_alias(self, alias: str) -> bool:
        if not alias:
            return False
        col_lookup = getattr(self, "dataset_col_lookup", {})
        cols = col_lookup.get(alias)
        if cols is None:
            for k, v in col_lookup.items():
                if k.upper() == alias.upper():
                    cols = v
                    break
        if not cols:
            return True
        non_id_cols = {c for c in cols if c.upper() not in ("ID", "NAME", "DESCRIPTION", "_ID")}
        return len(non_id_cols) == 0

    def _extract_referenced_table_aliases(self, metric_sql: str, valid_aliases: Set[str]) -> Set[str]:
        if not metric_sql: return set()
        # Exclude dimension columns inside PARTITION BY EXCLUDING clauses
        clean_sql = re.sub(r'\bPARTITION\s+BY\s+EXCLUDING\s+[^)]+\)', '', metric_sql, flags=re.IGNORECASE)
        referenced = set()
        patterns = [r'(\w+)\."([^"]+)"', r'(\w+)\.([A-Za-z_][A-Za-z0-9_$]*)']
        for pattern in patterns:
            for alias, _ in re.findall(pattern, clean_sql):
                if alias in valid_aliases:
                    referenced.add(alias)
        return referenced

    @staticmethod
    def _escape_comment(text: str) -> str:
        """Escape single quotes in COMMENT literals exactly once (never produce '''' sequences)."""
        clean = str(text or "")
        while "''" in clean:
            clean = clean.replace("''", "'")
        return clean.replace("'", "''")

    @classmethod
    def _clean_comment(cls, text: str) -> str:
        s = " ".join(str(text or "").split())
        while "''" in s:
            s = s.replace("''", "'")
        s = s.replace('"', '')
        if "could not be translated to SQL at DDL-emission time" in s:
            s = "not applicable: DAX expression unsupported"
        if "available columns" in s.lower():
            s = s.split("available columns")[0].strip().rstrip(":;,-")
        # Remove quoted identifiers in comment (e.g. T."COL" -> T.COL) to keep comments clean
        s = re.sub(r'([A-Za-z0-9_]+)\."([A-Za-z0-9_]+)"', r'\1.\2', s)
        s = s.replace('"', '')
        if len(s) > 280:
            # Cut at word boundary without ellipsis cutting mid-word
            s = s[:270].rsplit(' ', 1)[0]
        return cls._escape_comment(s)

    def _align_partition_by_excluding(self, metric_sql: str) -> str:
        """Align PARTITION BY EXCLUDING dimension tokens with exact declared DIMENSIONS.
        
        Snowflake requires all items in PARTITION BY EXCLUDING to be declared dimensions
        in the semantic view. If a column was deduplicated (e.g. PRODUCTS.PRODUCTSEQ ->
        PRODUCTS.PRODUCTS_PRODUCTSEQ), this maps it to the emitted dimension name and ensures
        it is properly quoted. Undeclared items are dropped.
        """
        if not metric_sql or "PARTITION BY EXCLUDING" not in metric_sql.upper():
            return metric_sql

        emitted_map = getattr(self, "emitted_dim_by_phys", {})
        emitted_dims = getattr(self, "emitted_dimensions", {})
        if not emitted_map and not emitted_dims:
            return metric_sql

        def _replace_excluding(match: re.Match) -> str:
            raw_clause = match.group(1)
            tokens = [t.strip() for t in raw_clause.split(",") if t.strip()]
            aligned_tokens = []
            for tok in tokens:
                m = re.match(r'^(?:([A-Za-z0-9_]+)\.)?"?([^"]+)"?$', tok)
                if not m:
                    aligned_tokens.append(tok)
                    continue
                t_alias = (m.group(1) or "").upper()
                c_name = m.group(2).strip()
                cu = c_name.upper()

                # Rule 1: PARTITION BY EXCLUDING must use the declared dimension of the DIMENSION TABLE
                # (<DIM_TABLE>.<DIM_COL>), never an enriched copy on the fact (<FACT_TABLE>_<COL>).
                resolved_tbl = None
                resolved_dim = None

                # 1. Enriched copy check: <OTHER_TABLE>_<COL>
                for o_tbl, dims in emitted_dims.items():
                    if o_tbl.upper() != t_alias:
                        prefix = f"{o_tbl.upper()}_"
                        if cu.startswith(prefix):
                            suffix = cu[len(prefix):]
                            matched = emitted_map.get((o_tbl.upper(), suffix))
                            if not matched:
                                norm_s = re.sub(r"[^A-Za-z0-9]", "", suffix)
                                for d in dims:
                                    if re.sub(r"[^A-Za-z0-9]", "", d.upper()) == norm_s:
                                        matched = d
                                        break
                            if matched:
                                resolved_tbl = o_tbl.upper()
                                resolved_dim = matched
                                break

                # 2. Check if c_name exists directly on another dimension table
                if not resolved_dim:
                    norm_c = re.sub(r"[^A-Za-z0-9]", "", cu)
                    for o_tbl, dims in emitted_dims.items():
                        if o_tbl.upper() != t_alias:
                            norm_o = re.sub(r"[^A-Za-z0-9]", "", o_tbl.upper())
                            if norm_c == norm_o:
                                for d in dims:
                                    if re.sub(r"[^A-Za-z0-9]", "", d.upper()) == norm_c:
                                        resolved_tbl = o_tbl.upper()
                                        resolved_dim = d
                                        break
                                if not resolved_dim and dims:
                                    resolved_tbl = o_tbl.upper()
                                    resolved_dim = dims[0]
                                if resolved_dim:
                                    break

                if resolved_tbl and resolved_dim:
                    t_alias = resolved_tbl
                    matched_dim = resolved_dim
                else:
                    if not t_alias:
                        for tbl, dims in emitted_dims.items():
                            if c_name.upper() in [d.upper() for d in dims]:
                                t_alias = tbl
                                break

                    matched_dim = None
                    if t_alias:
                        matched_dim = emitted_map.get((t_alias, c_name.upper()))
                        if not matched_dim and t_alias in emitted_dims:
                            norm_c = re.sub(r"[^A-Za-z0-9]", "", c_name.upper())
                            for d in emitted_dims[t_alias]:
                                if re.sub(r"[^A-Za-z0-9]", "", d.upper()) == norm_c:
                                    matched_dim = d
                                    break

                if matched_dim and t_alias:
                    ref = f'{t_alias}."{matched_dim}"'
                    if ref not in aligned_tokens:
                        aligned_tokens.append(ref)
                elif not emitted_dims:
                    aligned_tokens.append(tok)
                else:
                    logger.warning(
                        "Dropping undeclared dimension '%s' from PARTITION BY EXCLUDING (not in DIMENSIONS)",
                        tok
                    )

            if not aligned_tokens:
                return match.group(0)
            aligned_tokens.sort()
            return f"PARTITION BY EXCLUDING {', '.join(aligned_tokens)}"

        return re.sub(r"\bPARTITION\s+BY\s+EXCLUDING\s+([^)]+)", _replace_excluding, metric_sql, flags=re.IGNORECASE)

    def _prune_unresolved_metric_lines(self, metrics_lines: List[str], metric_name_set: Set[str]) -> List[str]:
        if not metrics_lines or not metric_name_set:
            return metrics_lines
        current = list(metrics_lines)
        inlined_by_metric: Dict[str, Set[str]] = {}
        max_passes = len(metrics_lines) + 5
        pass_count = 0
        while pass_count < max_passes:
            pass_count += 1
            defined: Set[str] = set()
            window_metrics: Set[str] = set()
            parsed = []
            expr_by_name = {}
            comment_by_name: Dict[str, str] = {}
            for line in current:
                m = re.search(r'^\s*(PRIVATE\s+)?([A-Z_][A-Z0-9_]*)\."([^\"]+)"(\s+NON\s+ADDITIVE\s+BY\s*\([^)]+\))?\s+AS\s+(.+?)\s*$', line.strip().rstrip(','))
                if not m:
                    parsed.append((line, "", None, None, "", "", "", ""))
                    continue
                priv, alias, name, non_additive_clause, expr = m.groups()
                priv = priv or ""
                non_additive_clause = non_additive_clause or ""
                expr, synonym_suffix = self._split_outer_synonyms_clause(expr)

                comment_text = ""
                m_comm = re.search(r"\s+COMMENT\s*=\s*'((?:''|[^'])*)'\s*$", expr, re.IGNORECASE)
                if m_comm:
                    comment_text = m_comm.group(1)
                    while "''" in comment_text:
                        comment_text = comment_text.replace("''", "'")
                    expr = expr[:m_comm.start()].strip()
                elif "/*" in expr:
                    m_blk = re.search(r'/\*\s*(.*?)\s*\*/', expr)
                    if m_blk:
                        comment_text = m_blk.group(1).strip()

                defined.add(name)
                expr_by_name[name] = expr
                if comment_text:
                    comment_by_name[name] = comment_text
                if re.search(r'\bOVER\b', expr, flags=re.IGNORECASE):
                    window_metrics.add(name)
                parsed.append((line, priv, alias, name, non_additive_clause, expr, synonym_suffix, comment_text))

            removed = rewritten = False
            next_lines = []
            for line, priv, alias, name, non_additive_clause, expr, synonym_suffix, comment_text in parsed:
                if not name:
                    next_lines.append(line)
                    continue

                refs = set(re.findall(r'(?:[A-Za-z0-9_]+\.)?"([A-Za-z0-9_]+)"', expr))
                metric_refs = [r for r in refs if r in metric_name_set and r != name]

                # 1. Fail-closed: a reference to an undefined metric emits NULL placeholder;
                # a reference to a NULL placeholder makes this metric a NULL placeholder too (NULL propagates).
                unresolved_or_invalid = False
                null_ref = None
                for r in metric_refs:
                    if r not in defined:
                        unresolved_or_invalid = True
                        break
                    r_expr = expr_by_name.get(r)
                    if not r_expr or "CAST(NULL AS" in r_expr.upper() or r_expr.strip().upper() == "NULL":
                        null_ref = null_ref or r

                if unresolved_or_invalid:
                    missing_ref = next((r for r in metric_refs if r not in defined), "unknown")
                    reason = f"depends on [{missing_ref}]: missing/unresolved dependency"
                    safe_comm = self._clean_comment(reason)
                    self.__dict__.setdefault("_null_propagated", {})[name] = "missing/unresolved dependency"
                    next_lines.append(f'  {priv}{alias}."{name}" AS {NULL_PLACEHOLDER_SQL} COMMENT = \'{safe_comm}\'')
                    comment_by_name[name] = reason
                    rewritten = True
                    continue

                if null_ref and expr.strip().upper() != NULL_PLACEHOLDER_SQL:
                    self.__dict__.setdefault("_null_propagated", {})[name] = null_ref
                    ref_reason = comment_by_name.get(null_ref)
                    if not ref_reason:
                        m_blk = re.search(r'/\*\s*(.*?)\s*\*/', expr_by_name.get(null_ref, ""))
                        ref_reason = m_blk.group(1).strip() if m_blk else "not translatable"
                    reason = f"depends on [{null_ref}]: {ref_reason}"
                    safe_comm = self._clean_comment(reason)
                    next_lines.append(f'  {priv}{alias}."{name}" AS {NULL_PLACEHOLDER_SQL} COMMENT = \'{safe_comm}\'')
                    comment_by_name[name] = reason
                    rewritten = True
                    continue

                if expr.strip().upper() == NULL_PLACEHOLDER_SQL or "CAST(NULL AS DOUBLE)" in expr.upper():
                    if not comment_text:
                        comment_text = "not applicable: not translatable"
                    safe_comm = self._clean_comment(comment_text)
                    next_lines.append(f'  {priv}{alias}."{name}" AS {NULL_PLACEHOLDER_SQL} COMMENT = \'{safe_comm}\'')
                    continue

                # 2. Inline valid referenced metrics that haven't been inlined into this line yet
                if "OVER (" in expr.upper() or "OVER(" in expr.upper():
                    next_lines.append(line)
                    continue
                already_inlined = inlined_by_metric.setdefault(name, set())
                uninlined_metric_refs = [r for r in metric_refs if r not in already_inlined]

                if uninlined_metric_refs:
                    logger.info("PRUNER MATCH: metric '%s' inlining metrics: %s", name, uninlined_metric_refs)
                    expanded_expr = expr
                    substituted = False
                    for ref_name in sorted(set(uninlined_metric_refs)):
                        if getattr(self, "_dataset_col_lookup", None):
                            clean_ref = self.identifier_sanitizer.sanitize_column(ref_name).upper()
                            is_clash = any(
                                any(self.identifier_sanitizer.sanitize_column(c).upper() == clean_ref for c in cols)
                                for cols in self._dataset_col_lookup.values()
                            )
                            if is_clash:
                                already_inlined.add(ref_name)
                                continue

                        ref_expr = expr_by_name.get(ref_name)
                        if ref_expr:
                            clean_ref_expr = re.sub(
                                r"\s+WITH\s+SYNONYMS\s*=\s*\((?:[^()']|'(?:''|[^'])*')*\)",
                                "",
                                ref_expr,
                                flags=re.IGNORECASE
                            )
                            pattern = rf'(?:[A-Z0-9_]+\.)?"{re.escape(ref_name)}"'
                            expanded_expr = re.sub(pattern, f'({clean_ref_expr.strip()})', expanded_expr)
                            already_inlined.add(ref_name)
                            substituted = True

                    if substituted:
                        if "PARTITION BY EXCLUDING" in expanded_expr.upper():
                            expanded_expr = self._align_partition_by_excluding(expanded_expr)
                        next_lines.append(f'  {priv}{alias}."{name}"{non_additive_clause} AS {expanded_expr}{synonym_suffix}')
                        rewritten = True
                        continue

                next_lines.append(line)

            if not removed and not rewritten:
                break
            current = next_lines

        if hasattr(self.translator, "_normalize_string_boolean_comparisons"):
            current = [self.translator._normalize_string_boolean_comparisons(l) for l in current]

        # Collect window metric dimensions
        window_dims_by_metric: Dict[str, Set[str]] = {}
        for l in current:
            m = re.search(r'^\s*(?:PRIVATE\s+)?([A-Z_][A-Z0-9_]*)\."([^"]+)"(?:\s+NON\s+ADDITIVE\s+BY\s*\([^)]+\))?\s+AS\s+(.+?)\s*$', l.strip().rstrip(','))
            if m:
                _, m_name, m_expr = m.group(1), m.group(2), m.group(3)
                if "PARTITION BY EXCLUDING" in m_expr.upper():
                    m_excl = re.search(r"PARTITION\s+BY\s+EXCLUDING\s+(.*?)(?:\)|$)", m_expr, re.I | re.S)
                    if m_excl:
                        tokens = [t.strip().replace('"', '') for t in m_excl.group(1).split(",") if t.strip()]
                        window_dims_by_metric[m_name.upper()] = set(tokens)

        # Collect defined metric names for Error 010220 validation
        defined_metric_names: Set[str] = set()
        for l in current:
            m_scan = re.search(r'^\s*(?:PRIVATE\s+)?([A-Z_][A-Z0-9_]*)\."([^"]+)"', l.strip())
            if m_scan:
                defined_metric_names.add(m_scan.group(2).upper())

        # Final validation pass: validate final expressions after all inlining/rewrites
        final_validated = []
        for l in current:
            m = re.search(r'^\s*(PRIVATE\s+)?([A-Z_][A-Z0-9_]*)\."([^"]+)"(\s+NON\s+ADDITIVE\s+BY\s*\([^)]+\))?\s+AS\s+(.+?)\s*$', l.strip().rstrip(','))
            if not m:
                final_validated.append(l)
                continue
            priv, alias, m_name, non_add, m_expr = m.groups()
            priv = priv or ""
            m_expr, syn = self._split_outer_synonyms_clause(m_expr)
            if not alias or not m_name or m_expr.strip().upper() == NULL_PLACEHOLDER_SQL:
                final_validated.append(l)
                continue
            if "PARTITION BY EXCLUDING" in m_expr.upper():
                m_expr = self._align_partition_by_excluding(m_expr)
                m_excl = re.search(r"PARTITION\s+BY\s+EXCLUDING\s+(.*?)(?:\)|$)", m_expr, re.I | re.S)
                if m_excl:
                    excl_text = m_excl.group(1).strip().replace('"', "")
                    m_comm = re.search(r"\s+COMMENT\s*=\s*'((?:''|[^'])*)'\s*$", m_expr, re.IGNORECASE)
                    if m_comm and "Window metric over" in m_comm.group(1):
                        new_comm = f"Window metric over {alias}.{m_name}. Must be queried with {excl_text}."
                        m_expr = m_expr[:m_comm.start()] + f" COMMENT = '{self._clean_comment(new_comm)}'"

            # Propagate window metric requirements (T6)
            req_dims: Set[str] = set()
            for w_name, w_dims in window_dims_by_metric.items():
                if w_name != m_name.upper() and re.search(rf'\b"?{re.escape(w_name)}"?\b', m_expr, re.IGNORECASE):
                    req_dims |= w_dims
            if req_dims:
                sorted_dims = ", ".join(sorted(req_dims))
                req_text = f"Must be queried with {sorted_dims}."
                m_comm = re.search(r"\s+COMMENT\s*=\s*'((?:''|[^'])*)'\s*$", m_expr, re.IGNORECASE)
                if m_comm:
                    curr_comm = m_comm.group(1).replace("''", "'")
                    missing_req = [d for d in req_dims if d.upper() not in curr_comm.upper()]
                    if missing_req:
                        new_comm = f"{curr_comm.rstrip('.')} {req_text}"
                        m_expr = m_expr[:m_comm.start()] + f" COMMENT = '{self._clean_comment(new_comm)}'"
                else:
                    m_expr = m_expr + f" COMMENT = '{self._clean_comment(req_text)}'"

            m_expr = self._rewrite_foreign_table_columns_in_aggregates(
                m_expr=m_expr,
                alias=alias,
                relationships=getattr(self, "_relationships", None),
                dataset_aliases=getattr(self, "_dataset_aliases", None),
            )

            nested_reason = detect_nested_aggregate(m_expr)
            if nested_reason:
                logger.warning("Final metric '%s' rejected by nested aggregate check: %s", m_name, nested_reason)
                safe_comm = self._clean_comment(f"not applicable: {nested_reason}")
                final_validated.append(f'  {priv}{alias}."{m_name}" AS {NULL_PLACEHOLDER_SQL} COMMENT = \'{safe_comm}\'')
                continue

            dax_expr = getattr(self, "_measure_dax_map", {}).get(m_name.upper())
            predeploy_reason = self._run_predeploy_checks(
                m_name=m_name,
                alias=alias,
                m_expr=m_expr,
                dax_expr=dax_expr,
                dataset_col_lookup=getattr(self, "_dataset_col_lookup", None),
                dataset_aliases=getattr(self, "_dataset_aliases", None),
                relationships=getattr(self, "_relationships", None),
                defined_metric_names=defined_metric_names,
                non_add_clause=non_add,
            )
            if predeploy_reason:
                logger.warning("Final metric '%s' rejected by pre-deploy check: %s", m_name, predeploy_reason)
                safe_comm = self._clean_comment(f"not applicable: {predeploy_reason}")
                final_validated.append(f'  {priv}{alias}."' + m_name + f'" AS {NULL_PLACEHOLDER_SQL} COMMENT = \'{safe_comm}\'')
                continue
            final_validated.append(f'  {priv}{alias}."{m_name}"{non_add or ""} AS {m_expr}{syn}')
        return final_validated

    def _parse_metric_line(self, line: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], str, str]:
        m = re.search(r'^\s*(PRIVATE\s+)?([A-Z_][A-Z0-9_]*)\."([^"]+)"(\s+NON\s+ADDITIVE\s+BY\s*\([^)]+\))?\s+AS\s+(.+?)\s*$', line.strip().rstrip(','))
        if not m:
            return None, None, None, None, "", ""
        priv, alias, name, non_add, expr = m.groups()
        expr, syn = self._split_outer_synonyms_clause(expr)
        return priv or "", alias, name, non_add or "", expr, syn

    def _split_outer_synonyms_clause(self, expr: str) -> Tuple[str, str]:
        marker = " WITH SYNONYMS = ("
        idx = str(expr or "").upper().rfind(marker)
        if idx < 0:
            return expr, ""
        return expr[:idx].rstrip(), expr[idx:]

    def _rewrite_foreign_table_columns_in_aggregates(
        self,
        m_expr: str,
        alias: str,
        relationships: Optional[Iterable[Any]],
        dataset_aliases: Optional[Dict[str, str]],
    ) -> str:
        if not relationships or not m_expr:
            return m_expr

        # Build FK map for alias: (parent_alias_upper, pk_col_upper) -> fk_col_name
        fk_map: Dict[Tuple[str, str], str] = {}
        for rel in relationships:
            from_ds = getattr(rel, "from_dataset", "") or getattr(rel, "from_table", "") or ""
            to_ds = getattr(rel, "to_dataset", "") or getattr(rel, "to_table", "") or ""
            from_al = (dataset_aliases.get(from_ds) or to_alias(from_ds) or from_ds).upper() if dataset_aliases else from_ds.upper()
            to_al = (dataset_aliases.get(to_ds) or to_alias(to_ds) or to_ds).upper() if dataset_aliases else to_ds.upper()

            if from_al == alias.upper() and to_al != alias.upper():
                from_cols = getattr(rel, "from_columns", None) or getattr(rel, "from_column", None) or []
                to_cols = getattr(rel, "to_columns", None) or getattr(rel, "to_column", None) or []
                if isinstance(from_cols, str):
                    from_cols = [from_cols]
                if isinstance(to_cols, str):
                    to_cols = [to_cols]
                for fk, pk in zip(from_cols, to_cols):
                    if fk and pk:
                        fk_sanitized = sanitize_column(str(fk))
                        pk_upper = str(pk).strip('"').upper()
                        fk_map[(to_al, pk_upper)] = fk_sanitized
                        fk_map[(to_al, sanitize_column(str(pk)).upper())] = fk_sanitized

        if not fk_map:
            return m_expr

        # Find aggregate functions and rewrite parent_alias.pk inside them
        agg_pattern = re.compile(r'\b(SUM|AVG|AVERAGE|COUNT|MIN|MAX)\s*\(', re.IGNORECASE)
        pos = 0
        result = []
        while pos < len(m_expr):
            m = agg_pattern.search(m_expr, pos)
            if not m:
                result.append(m_expr[pos:])
                break

            result.append(m_expr[pos:m.end()])
            depth = 1
            curr = m.end()
            while curr < len(m_expr) and depth > 0:
                if m_expr[curr] == '(':
                    depth += 1
                elif m_expr[curr] == ')':
                    depth -= 1
                curr += 1

            agg_body = m_expr[m.end():curr - 1]
            def _replace_col(col_match):
                tbl = col_match.group(1)
                col = col_match.group(2)
                key = (tbl.upper(), col.upper())
                if key in fk_map:
                    fk = fk_map[key]
                    return f'{alias}."{fk}"'
                return col_match.group(0)

            rewritten_body = re.sub(
                r'([A-Za-z_][A-Za-z0-9_]*)\."?([A-Za-z_][A-Za-z0-9_]*)"?',
                _replace_col,
                agg_body
            )
            result.append(rewritten_body)
            if depth == 0:
                result.append(')')
            pos = curr

        return "".join(result)

    def _rewrite_clashing_columns_in_aggregates(
        self,
        m_expr: str,
        defined_metric_names: Set[str],
        dataset_col_lookup: Optional[Dict[str, Set[str]]],
        dataset_aliases: Optional[Dict[str, str]],
    ) -> str:
        """When a metric name equals a physical column of its table, in a Snowflake
        semantic view T.NAME inside an aggregate resolves to the metric (Snowflake 010220).
        This rewrites T.NAME inside any aggregate function (SUM, AVG, MIN, MAX, COUNT)
        to T._COL_NAME so it targets the declared PRIVATE fact column.
        """
        if not m_expr or not defined_metric_names:
            return m_expr

        # Build clashing set: (table_alias_upper, col_upper)
        clashing: Set[Tuple[str, str]] = set()
        if dataset_col_lookup:
            for ds, cols in dataset_col_lookup.items():
                al = (dataset_aliases.get(ds) or to_alias(ds) or ds).upper() if dataset_aliases else to_alias(ds).upper()
                for c in cols:
                    c_clean = sanitize_column(c).upper()
                    c_orig = str(c).strip().upper()
                    if c_clean in defined_metric_names:
                        clashing.add((al, c_clean))
                    if c_orig in defined_metric_names:
                        clashing.add((al, c_orig))

        if not clashing:
            return m_expr

        agg_pattern = re.compile(r'\b(SUM|AVG|AVERAGE|COUNT|MIN|MAX)\s*\(', re.IGNORECASE)
        pos = 0
        result = []
        while pos < len(m_expr):
            m = agg_pattern.search(m_expr, pos)
            if not m:
                result.append(m_expr[pos:])
                break
            result.append(m_expr[pos:m.end()])
            depth = 1
            curr = m.end()
            while curr < len(m_expr) and depth > 0:
                if m_expr[curr] == '(':
                    depth += 1
                elif m_expr[curr] == ')':
                    depth -= 1
                curr += 1
            agg_inner = m_expr[m.end():curr - 1]
            for tbl, col in clashing:
                pat = rf'\b({re.escape(tbl)})\.([\"\']?{re.escape(col)}[\"\']?)(?!\w)'
                def _rep(match):
                    q = '"' if '"' in match.group(2) else ''
                    c_name = match.group(2).replace('"', '').replace("'", '')
                    return f'{match.group(1)}.{q}_COL_{c_name}{q}'
                agg_inner = re.sub(pat, _rep, agg_inner, flags=re.IGNORECASE)
            result.append(agg_inner)
            if curr <= len(m_expr) and depth == 0:
                result.append(')')
            pos = curr
        return ''.join(result)

    def _run_predeploy_checks(
        self,
        m_name: str,
        alias: str,
        m_expr: str,
        dax_expr: Optional[str],
        dataset_col_lookup: Optional[Dict[str, Set[str]]],
        dataset_aliases: Optional[Dict[str, str]],
        relationships: Optional[Iterable[Any]],
        defined_metric_names: Optional[Set[str]] = None,
        non_add_clause: Optional[str] = None,
    ) -> Optional[str]:
        """Pre-deploy validation checks (a)-(g) for every metric.
        Returns failure reason string or None if valid.
        """
        expr_upper = m_expr.strip().upper()
        clean_expr = re.sub(r"\s+COMMENT\s*=\s*'((?:''|[^'])*)'\s*$", "", expr_upper).strip()
        if clean_expr in (NULL_PLACEHOLDER_SQL, "CAST(NULL AS DOUBLE)", "NULL"):
            return None

        # Check (g): no CAST(NULL) inside a larger expression (composite null)
        if "CAST(NULL" in clean_expr:
            return "CAST(NULL) detected inside larger expression (composite null)"

        # Check (a): every TABLE.COLUMN exists in that table (live schema)
        if dataset_col_lookup:
            alias_to_cols: Dict[str, Set[str]] = {}
            for ds, cols in dataset_col_lookup.items():
                al = (dataset_aliases.get(ds) or ds).upper() if dataset_aliases else ds.upper()
                alias_to_cols[al] = {c.upper() for c in cols}
                alias_to_cols[ds.upper()] = {c.upper() for c in cols}

            col_refs = re.findall(r'([A-Za-z_][A-Za-z0-9_]*)\."?([A-Za-z_][A-Za-z0-9_]*)"?', m_expr)
            for tbl_ref, col_ref in col_refs:
                tbl_u = tbl_ref.upper()
                col_u = col_ref.upper()
                if tbl_u in alias_to_cols:
                    if (col_u.startswith("IS_") or col_u.startswith("__SB_") or 
                        col_u.startswith("_CURRENT_") or col_u.startswith("_ALL_") or 
                        col_u.startswith("_GLOBAL_") or col_u == "ONE_ROW_KEY"):
                        continue
                    known_cols = alias_to_cols[tbl_u]
                    clean_col_u = re.sub(r'[^A-Z0-9]', '', col_u)
                    if col_u.startswith("_COL_"):
                        base_c = col_u[5:]
                        clean_base = re.sub(r'[^A-Z0-9]', '', base_c)
                        if any(c == base_c or re.sub(r'[^A-Z0-9]', '', c) == clean_base for c in known_cols):
                            continue
                    matched = any(c == col_u or re.sub(r'[^A-Z0-9]', '', c) == clean_col_u for c in known_cols)
                    if not matched:
                        if defined_metric_names and col_u in defined_metric_names:
                            continue
                        return f"TABLE.COLUMN '{tbl_ref}.{col_ref}' does not exist in schema"

        # Snowflake error 010220 safety:
        # (1) aggregate on table alias cannot reference cross-table columns
        # (2) aggregate cannot wrap another defined metric (aggregating a metric)
        # (3) cannot mix raw aggregates with metric references
        def _is_phys_col(t_name: str, c_name: str) -> bool:
            if not dataset_col_lookup:
                return False
            c_check = c_name[5:] if c_name.upper().startswith("_COL_") else c_name
            clean_check = re.sub(r'[^A-Z0-9]', '', c_check.upper())
            for ds_name, cols in dataset_col_lookup.items():
                al = (dataset_aliases.get(ds_name) or ds_name).upper() if dataset_aliases else ds_name.upper()
                if al == t_name.upper() or ds_name.upper() == t_name.upper():
                    return c_check.upper() in {str(c).upper() for c in cols} or any(re.sub(r'[^A-Z0-9]', '', str(c).upper()) == clean_check for c in cols)
            return False

        agg_pattern = re.compile(r'\b(SUM|AVG|AVERAGE|COUNT|MIN|MAX)\s*\(', re.IGNORECASE)
        has_aggregate = bool(agg_pattern.search(m_expr))
        if has_aggregate:
            raw_agg_spans = []
            all_spans = []
            for agg_m in agg_pattern.finditer(m_expr):
                start = agg_m.end()
                depth = 1
                curr = start
                while curr < len(m_expr) and depth > 0:
                    if m_expr[curr] == '(':
                        depth += 1
                    elif m_expr[curr] == ')':
                        depth -= 1
                    curr += 1

                over_m = re.match(r'^\s*OVER\s*\(', m_expr[curr:], re.IGNORECASE)
                is_window_agg = bool(over_m)
                if is_window_agg:
                    over_start = curr + over_m.end()
                    over_depth = 1
                    over_curr = over_start
                    while over_curr < len(m_expr) and over_depth > 0:
                        if m_expr[over_curr] == '(':
                            over_depth += 1
                        elif m_expr[over_curr] == ')':
                            over_depth -= 1
                        over_curr += 1
                    all_spans.append((agg_m.start(), over_curr))
                else:
                    raw_agg_spans.append((agg_m.start(), curr))
                    all_spans.append((agg_m.start(), curr))

                agg_body = m_expr[start:curr-1] if depth == 0 else m_expr[start:]
                for col_m in re.finditer(r'([A-Za-z_][A-Za-z0-9_]*)\."?([A-Za-z_][A-Za-z0-9_]*)"?', agg_body):
                    tbl_ref = col_m.group(1).upper()
                    col_ref = col_m.group(2)
                    if not is_window_agg and defined_metric_names and col_ref.upper() in defined_metric_names and col_ref.upper() != m_name.upper():
                        return f"BY_DESIGN_EXCLUDED: metric '{m_name}' cannot wrap aggregate around existing metric '{col_ref}' (Snowflake error 010220)"
                    if _is_phys_col(tbl_ref, col_ref):
                        continue

            # Check (3): cannot mix raw aggregates with metric references outside aggregates
            if raw_agg_spans and defined_metric_names:
                outside_expr = m_expr
                for s, e in reversed(all_spans):
                    outside_expr = outside_expr[:s] + " " + outside_expr[e:]
                for col_m in re.finditer(r'([A-Za-z_][A-Za-z0-9_]*)\."?([A-Za-z_][A-Za-z0-9_]*)"?', outside_expr):
                    tbl_ref = col_m.group(1).upper()
                    col_ref = col_m.group(2)
                    if _is_phys_col(tbl_ref, col_ref):
                        continue
                    if col_ref.upper() in defined_metric_names and col_ref.upper() != m_name.upper():
                        return f"BY_DESIGN_EXCLUDED: metric '{m_name}' cannot mix raw aggregate with metric reference '{col_ref}' (Snowflake error 010220)"

        # Check (d): no SUM/COUNT/AVG of a column that belongs to a related 'one'-side table (fan-out)
        # Never for COUNT(DISTINCT)/MIN/MAX, the table's own columns, or measure-only tables.
        for agg_fn in ("SUM", "AVG", "AVERAGE", "COUNT"):
            for m in re.finditer(rf'\b{agg_fn}\s*\(\s*(?!DISTINCT\b)\s*([A-Za-z_][A-Za-z0-9_]*)\."?([A-Za-z_][A-Za-z0-9_]*)"?\s*\)', m_expr, re.IGNORECASE):
                t_ref = m.group(1).upper()
                c_ref = m.group(2).upper()
                # 1. Skip if measure-only table
                cur_model = getattr(self, "_current_model", None)
                t_ds_obj = (getattr(self, "dataset_by_name", {}) or {}).get(t_ref)
                if not t_ds_obj and cur_model and dataset_aliases:
                    t_ds_obj = next((d for d in getattr(cur_model, "datasets", []) or [] if (dataset_aliases.get(getattr(d, "unique_name", None)) or getattr(d, "unique_name", "")).upper() == t_ref), None)
                if t_ds_obj and getattr(t_ds_obj, "is_measure_table", False):
                    continue
                if dataset_col_lookup and t_ref in dataset_col_lookup and len(dataset_col_lookup[t_ref]) == 0:
                    continue

                # 2. If this column is the fact table's own physical column, it is not a copied dimension column
                if _is_phys_col(t_ref, c_ref):
                    continue

                # 3. Only fire for columns belonging to a related 'one'-side table reached through a relationship
                if relationships:
                    for rel in relationships:
                        from_ds = str(getattr(rel, "from_dataset", "") or getattr(rel, "from_table", "")).upper()
                        to_ds = str(getattr(rel, "to_dataset", "") or getattr(rel, "to_table", "")).upper()
                        from_al = (dataset_aliases.get(from_ds) or from_ds).upper() if dataset_aliases else from_ds
                        to_al = (dataset_aliases.get(to_ds) or to_ds).upper() if dataset_aliases else to_ds
                        if t_ref in (from_al, from_ds) and to_al != t_ref:
                            to_clean = re.sub(r'[^A-Z0-9]', '', to_al)
                            is_dim_prefix = c_ref.startswith(f"{to_al}_") or (bool(to_clean) and c_ref.startswith(f"{to_clean}_"))
                            c_bare = c_ref
                            if c_ref.startswith(f"{to_al}_"):
                                c_bare = c_ref[len(to_al)+1:]
                            elif to_clean and c_ref.startswith(f"{to_clean}_"):
                                c_bare = c_ref[len(to_clean)+1:]

                            if dataset_col_lookup:
                                # Check if c_bare is in to_ds columns and NOT in t_ref columns
                                to_cols = {str(c).upper() for c in dataset_col_lookup.get(to_ds, set())}
                                t_cols = {str(c).upper() for c in dataset_col_lookup.get(from_ds, set())}
                                clean_bare = re.sub(r'[^A-Z0-9]', '', c_bare)
                                in_to = any(c == c_bare or re.sub(r'[^A-Z0-9]', '', c) == clean_bare for c in to_cols)
                                in_t = any(c == c_bare or re.sub(r'[^A-Z0-9]', '', c) == clean_bare for c in t_cols)
                                if in_to and not in_t:
                                    return f"BY_DESIGN_EXCLUDED: aggregating dimension column with {agg_fn} on fact table causes fan-out duplication (measure should aggregate on dimension table directly)"
                                elif not to_cols and is_dim_prefix:
                                    return f"BY_DESIGN_EXCLUDED: aggregating dimension column with {agg_fn} on fact table causes fan-out duplication (measure should aggregate on dimension table directly)"
                            else:
                                if is_dim_prefix:
                                    return f"BY_DESIGN_EXCLUDED: aggregating dimension column with {agg_fn} on fact table causes fan-out duplication (measure should aggregate on dimension table directly)"

        # DAX-based checks if dax_expr is available
        if dax_expr:
            dax_upper = dax_expr.upper()
            dax_ast = None
            try:
                from semabridge.converter.dax_ast_parser import (
                    DaxAstParser,
                    DaxSqlRenderer,
                    DaxNode,
                    FunctionCallNode,
                    ColumnRefNode,
                    IdentifierNode,
                    BinaryOpNode,
                    UnaryOpNode,
                    LiteralNode,
                    VarNode,
                )
                dax_ast = DaxAstParser().parse(dax_expr)
            except Exception:
                dax_ast = None

            def _walk_ast(node: Optional[Any]):
                if not node:
                    return
                yield node
                if isinstance(node, FunctionCallNode):
                    for arg in node.args:
                        yield from _walk_ast(arg)
                elif isinstance(node, BinaryOpNode):
                    yield from _walk_ast(node.left)
                    yield from _walk_ast(node.right)
                elif isinstance(node, UnaryOpNode):
                    yield from _walk_ast(node.operand)
                elif isinstance(node, VarNode):
                    for _, expr in getattr(node, "variables", []):
                        yield from _walk_ast(expr)
                    yield from _walk_ast(getattr(node, "body", None))

            # Check (e): iterators over VALUES/DISTINCT are not unrolled (iteration grain)
            if dax_ast is not None:
                for n in _walk_ast(dax_ast):
                    if isinstance(n, FunctionCallNode) and n.func.upper() in (
                        "SUMX", "AVERAGEX", "MINX", "MAXX", "COUNTX", "COUNTAX", "PRODUCTX", "GEOMEANX"
                    ):
                        if n.args:
                            iter_target = n.args[0]
                            if isinstance(iter_target, FunctionCallNode) and iter_target.func.upper() in (
                                "VALUES", "DISTINCT", "SUMMARIZE", "SUMMARIZECOLUMNS"
                            ):
                                if "OVER" not in expr_upper and "GROUP BY" not in expr_upper:
                                    return "BY_DESIGN_EXCLUDED: aggregation over groups requires multi-level grouping not supported in a single semantic metric"
                            if isinstance(iter_target, FunctionCallNode) and iter_target.func.upper() in (
                                "ALL", "ALLNOBLANKROW", "REMOVEFILTERS"
                            ):
                                if iter_target.args and isinstance(iter_target.args[0], ColumnRefNode) and n.func.upper() not in ("MINX", "MAXX"):
                                    return "BY_DESIGN_EXCLUDED: aggregation over groups requires multi-level grouping not supported in a single semantic metric"
            else:
                for it_fn in ("SUMX", "AVERAGEX", "MINX", "MAXX", "COUNTX", "COUNTAX", "PRODUCTX", "GEOMEANX"):
                    if it_fn in dax_upper:
                        m_it = re.search(rf'\b{it_fn}\s*\(\s*(VALUES|DISTINCT|SUMMARIZE|SUMMARIZECOLUMNS)\s*\(', dax_upper)
                        if m_it and "OVER" not in expr_upper and "GROUP BY" not in expr_upper:
                            return "BY_DESIGN_EXCLUDED: aggregation over groups requires multi-level grouping not supported in a single semantic metric"

            # Check (b): every DAX filter condition appears in the SQL
            if dax_ast is not None:
                renderer = DaxSqlRenderer()
                has_row_filter = False
                closing_balance_required = False
                time_shift_required = False

                for n in _walk_ast(dax_ast):
                    if isinstance(n, FunctionCallNode):
                        fn_u = n.func.upper()
                        if fn_u in ("CALCULATE", "CALCULATETABLE") and len(n.args) > 1:
                            for filter_arg in n.args[1:]:
                                cb_dim = renderer._extract_closing_balance_dimension(filter_arg)
                                if cb_dim:
                                    closing_balance_required = True
                                    continue
                                if isinstance(filter_arg, FunctionCallNode):
                                    afn = filter_arg.func.upper()
                                    if afn in ("LASTNONBLANK", "CLOSINGBALANCEMONTH", "CLOSINGBALANCEQUARTER", "CLOSINGBALANCEYEAR"):
                                        closing_balance_required = True
                                        continue
                                    if afn in ("USERELATIONSHIP", "REMOVEFILTERS"):
                                        continue
                                    if afn in ("ALL", "ALLNOBLANKROW", "ALLSELECTED", "ALLEXCEPT"):
                                        continue
                                    if afn in ("DATEADD", "SAMEPERIODLASTYEAR", "PARALLELPERIOD", "DATESYTD", "DATESMTD", "DATESQTD", "PREVIOUSYEAR", "PREVIOUSMONTH", "PREVIOUSQUARTER", "PREVIOUSDAY"):
                                        time_shift_required = True
                                        continue
                                has_row_filter = True
                        elif fn_u in ("FILTER", "KEEPFILTERS"):
                            cb_dim = renderer._extract_closing_balance_dimension(n)
                            if cb_dim:
                                closing_balance_required = True
                            elif fn_u == "FILTER" and len(n.args) >= 2:
                                has_row_filter = True
                            elif fn_u == "KEEPFILTERS":
                                has_row_filter = True

                has_non_additive = bool(non_add_clause and "NON ADDITIVE BY" in non_add_clause.upper()) or "NON ADDITIVE BY" in expr_upper

                if closing_balance_required and not has_non_additive and non_add_clause is not None:
                    return "DAX closing-balance filter lost in SQL translation (missing NON ADDITIVE BY)"

                if time_shift_required:
                    has_time_shift_sql = any(k in expr_upper for k in ("IS_YTD", "IS_MTD", "IS_QTD", "IS_SPLY", "__SB_", "PARTITION BY", "DATEADD", "CASE", "WHEN", "IFF", "WHERE"))
                    if not has_time_shift_sql:
                        return "DAX time-intelligence filter lost in SQL translation"

                if has_row_filter:
                    has_sql_cond = any(k in expr_upper for k in ("CASE", "WHEN", "IFF", "WHERE", "IS_YTD", "IS_MTD", "IS_QTD", "IS_SPLY", "__SB_"))
                    if not has_sql_cond and not has_non_additive and "PARTITION BY EXCLUDING" not in expr_upper:
                        return "DAX filter condition lost in SQL translation (bare aggregate with no CASE/condition)"
            else:
                has_dax_filter = False
                is_closing_balance_filter = False
                if "FILTER(" in dax_upper or "KEEPFILTERS(" in dax_upper:
                    if (
                        re.search(r'FILTER\s*\(\s*ALL\s*\([^)]+\)\s*,\s*[^=]+=\s*MAX\s*\(', dax_upper)
                        or "LASTNONBLANK(" in dax_upper
                        or "CLOSINGBALANCEMONTH(" in dax_upper
                        or "CLOSINGBALANCEQUARTER(" in dax_upper
                        or "CLOSINGBALANCEYEAR(" in dax_upper
                    ):
                        is_closing_balance_filter = True
                    else:
                        has_dax_filter = True
                elif "CALCULATE(" in dax_upper:
                    calc_args = [a.strip() for a in dax_upper.split("CALCULATE(", 1)[1].split(",")] if "CALCULATE(" in dax_upper else []
                    if len(calc_args) > 1 and not all("USERELATIONSHIP" in a or ("ALL(" in a and "[" not in a) for a in calc_args[1:]):
                        has_dax_filter = True

                has_preserved_clause = (
                    bool(non_add_clause and "NON ADDITIVE BY" in non_add_clause.upper())
                    or "NON ADDITIVE BY" in expr_upper
                    or "PARTITION BY EXCLUDING" in expr_upper
                    or is_closing_balance_filter
                )

                if has_dax_filter and not has_preserved_clause:
                    is_bare_agg = bool(re.match(r'^\s*(?:SUM|AVG|MIN|MAX|COUNT)\s*\([^)]*\)\s*(?:::FLOAT)?\s*$', m_expr, re.IGNORECASE))
                    has_sql_cond = any(k in expr_upper for k in ("CASE", "WHEN", "IFF", "WHERE", "IS_YTD", "IS_MTD", "IS_QTD", "__SB_", "PARTITION BY EXCLUDING", "NON ADDITIVE BY"))
                    if is_bare_agg and not has_sql_cond:
                        return "DAX filter condition lost in SQL translation (bare aggregate with no CASE/condition)"

            # Check (c): SQL tables == DAX lineage tables
            dax_filter_tables: Set[str] = set()
            if dax_ast is not None:
                for n in _walk_ast(dax_ast):
                    if isinstance(n, FunctionCallNode) and n.func.upper() in ("FILTER", "SUMX", "AVERAGEX", "MINX", "MAXX", "COUNTAX", "COUNTROWS") and n.args:
                        t_arg = n.args[0]
                        if isinstance(t_arg, IdentifierNode):
                            dax_filter_tables.add(t_arg.name)
                        elif isinstance(t_arg, ColumnRefNode):
                            dax_filter_tables.add(t_arg.table)
            else:
                dax_filter_tables = set(re.findall(r"(?:FILTER|SUMX|AVERAGEX|MINX|MAXX|COUNTAX|COUNTROWS)\s*\(\s*['\"]?([A-Za-z0-9_ ]+)['\"]?", dax_expr, re.IGNORECASE))

            if dax_filter_tables and dataset_aliases:
                sql_check_text = m_expr + (" " + non_add_clause if non_add_clause else "")
                sql_table_aliases = {m.upper() for m in re.findall(r'([A-Za-z_][A-Za-z0-9_]*)\."?[A-Za-z_][A-Za-z0-9_]*"?', sql_check_text)}
                for ft in dax_filter_tables:
                    ft_alias = (dataset_aliases.get(ft) or to_alias(ft)).upper()
                    if ft_alias not in sql_table_aliases and sql_table_aliases:
                        has_helper_or_flag = any(k in expr_upper for k in ("IS_", "__SB_", "_LAG_", "_CURRENT_"))
                        is_related = False
                        if relationships:
                            for rel in relationships:
                                from_ds = str(getattr(rel, "from_dataset", "") or getattr(rel, "from_table", "")).upper()
                                to_ds = str(getattr(rel, "to_dataset", "") or getattr(rel, "to_table", "")).upper()
                                from_al = (dataset_aliases.get(from_ds) or from_ds).upper()
                                to_al = (dataset_aliases.get(to_ds) or to_ds).upper()
                                if (ft_alias in (from_al, to_al)) and any(sql_al in (from_al, to_al) for sql_al in sql_table_aliases):
                                    is_related = True
                                    break
                        if not (has_helper_or_flag or is_related):
                            return f"SQL tables do not match DAX lineage tables (expected '{ft_alias}' in SQL)"

            # Check (f): outer numeric literals preserved
            if dax_ast is not None:
                curr = dax_ast
                while isinstance(curr, VarNode):
                    curr = curr.body
                if isinstance(curr, BinaryOpNode):
                    if curr.op == "*" and isinstance(curr.right, LiteralNode) and isinstance(curr.right.value, (int, float)):
                        val = float(curr.right.value)
                        matches = re.findall(r'\*\s*\(?\s*([0-9]+(?:\.[0-9]+)?)\s*\)?', m_expr)
                        if not any(abs(float(m) - val) < 1e-4 for m in matches if m):
                            return f"outer numeric literal (* {curr.right.value}) from DAX expression was lost in SQL translation"
                    elif curr.op == "-" and isinstance(curr.left, LiteralNode) and isinstance(curr.left.value, (int, float)):
                        val = float(curr.left.value)
                        matches = re.findall(r'\b([0-9]+(?:\.[0-9]+)?)\s*\)?\s*-\s*', m_expr)
                        if not any(abs(float(m) - val) < 1e-4 for m in matches if m):
                            return f"outer numeric literal ({curr.left.value} -) from DAX expression was lost in SQL translation"
            else:
                m_mult = re.search(r'\*\s*([0-9]+(?:\.[0-9]+)?)\s*$', dax_expr.strip())
                if m_mult:
                    num = m_mult.group(1)
                    if not re.search(rf'\*\s*{re.escape(num)}\b', m_expr):
                        return f"outer numeric literal (* {num}) from DAX expression was lost in SQL translation"
                m_sub = re.search(r'^\s*([0-9]+(?:\.[0-9]+)?)\s*-\s*', dax_expr.strip())
                if m_sub:
                    num = m_sub.group(1)
                    if not re.search(rf'\b{re.escape(num)}\s*-\s*', m_expr):
                        return f"outer numeric literal ({num} -) from DAX expression was lost in SQL translation"

        return None
