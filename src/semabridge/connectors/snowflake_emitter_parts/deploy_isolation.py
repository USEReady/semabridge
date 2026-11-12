"""Per-item failure isolation for Snowflake semantic-view deployment.

Product rule enforced here: anything Snowflake cannot accept becomes an
EXPECTED FAILURE isolated to that one item (metric / dimension / fact /
relationship / table) with a specific reason -- it never fails the whole
view, is never silently dropped, and is never reported as converted.

Three deploy-time mechanisms share this module:

1. ``static_check_and_isolate`` -- the INFORMATION_SCHEMA pre-deploy check.
   Every unresolved reference is attributed to its owning item, which is
   nulled (metric) or removed (dimension / fact / relationship / table).
2. ``execute_with_isolation`` -- bounded CREATE remediation: a Snowflake
   error is mapped to the offending item(s), those are isolated with the
   Snowflake error as the reason, and the CREATE is retried. Unmappable
   errors fail with a clear message; retries are capped.
3. ``run_cortex_safety_validation`` -- post-create checks never fail a run
   whose view is already live: failures become isolations + redeploy, and
   anything that can't be fixed becomes a "deployed with warnings" note.

Every isolation is recorded in the run's DropLedger (stage DDL_DEPLOYMENT,
which is the stage engine/deployment/snowflake.py merges into the run).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from semabridge.core.drop_ledger import DropStage, classify_drop_reason
from semabridge.core.exceptions import ConnectorError
from semabridge.utils.logger import get_logger
from semabridge.connectors.snowflake_emitter_parts.semantic_view_items import (
    ItemOutcome,
    SemanticViewDDL,
    isolate_item,
    map_error_to_items,
    norm_ident,
    unquote_ident,
)

logger = get_logger(__name__)

MAX_CREATE_REMEDIATION_ATTEMPTS = 25

_ENTITY_KIND = {"metric": "metric", "dimension": "dimension", "fact": "fact",
                "relationship": "relationship", "table": "table"}


def short_error(exc: Any, limit: int = 300) -> str:
    return " ".join(str(exc).split())[:limit]


def _is_semantic_view_sql(sql: str) -> bool:
    return bool(re.search(r"\bSEMANTIC\s+VIEW\b", sql or "", re.IGNORECASE)) and bool(
        re.search(r"\bCREATE\b", sql or "", re.IGNORECASE)
    )


class DeployIsolationMixin:
    """Mixed into SnowflakeEmitter (needs ``drop_ledger``, ``config``,
    ``connection_manager``, ``semantic_view_builder``, ``_dropped_metrics``)."""

    # ------------------------------------------------------------------
    # Ledger plumbing
    # ------------------------------------------------------------------
    def _record_item_outcomes(self, outcomes: List[ItemOutcome], origin: str) -> None:
        for o in outcomes:
            reason = o.reason
            by_design = classify_drop_reason(reason, o.detail)
            if o.entity_kind == "metric":
                ledger_reason = reason if reason.lower().startswith("placeholder:") else f"placeholder: {reason}"
                detail = (o.detail or "") + (" | " if o.detail else "") + (
                    f"Emitted as CAST(NULL AS DOUBLE) by {origin}; the reason is in the metric's COMMENT."
                )
                try:
                    self._dropped_metrics.append({"metric": o.name, "reason": reason})
                except AttributeError:
                    self._dropped_metrics = [{"metric": o.name, "reason": reason}]
            else:
                ledger_reason = reason
                detail = (o.detail or "") + (" | " if o.detail else "") + f"Removed from the semantic view by {origin}."
            name = o.name if o.entity_kind in ("metric", "relationship", "table") or not o.alias else f"{o.alias}.{o.name}"
            self.drop_ledger.record(
                _ENTITY_KIND.get(o.entity_kind, o.entity_kind), name, DropStage.DDL_DEPLOYMENT,
                ledger_reason[:500], dataset=o.alias, detail=detail[:800], by_design=by_design,
            )
            logger.warning("[isolation:%s] %s %s '%s': %s", origin, o.action, o.entity_kind, name, reason)

    def _add_deploy_warning(self, message: str) -> None:
        warnings = getattr(self, "deployment_warnings", None)
        if warnings is None:
            self.deployment_warnings = warnings = []
        warnings.append(message)
        logger.warning("Deployed with warning: %s", message)
        self.drop_ledger.record(
            "view", "semantic view", DropStage.DDL_DEPLOYMENT,
            f"deployed with warnings: {message[:400]}", by_design=False,
        )

    # ------------------------------------------------------------------
    # 1. Static pre-deploy check (INFORMATION_SCHEMA)
    # ------------------------------------------------------------------
    def _fetch_information_schema_columns(self, cur: Any) -> Optional[Dict[str, Set[str]]]:
        table_columns: Dict[str, Set[str]] = {}
        target_db = str(self.config.database or "").strip().upper()
        target_schema = str(self.config.schema_name or "").strip().upper()
        try:
            if target_db and target_schema:
                cur.execute(
                    "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE UPPER(TABLE_CATALOG) = %s AND UPPER(TABLE_SCHEMA) = %s",
                    (target_db, target_schema),
                )
            elif target_schema:
                cur.execute(
                    "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE UPPER(TABLE_SCHEMA) = %s",
                    (target_schema,),
                )
            else:
                cur.execute(
                    "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_CATALOG = CURRENT_DATABASE()"
                )
            for s, t, c in cur.fetchall():
                table_columns.setdefault(str(t).upper(), set()).add(str(c).upper())
                table_columns.setdefault(f"{str(s).upper()}.{str(t).upper()}", set()).add(str(c).upper())
            return table_columns
        except Exception:
            try:
                cur.execute(
                    "SELECT TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = %s",
                    (target_schema or "PUBLIC",),
                )
                for t, c in cur.fetchall():
                    table_columns.setdefault(str(t).upper(), set()).add(str(c).upper())
                return table_columns
            except Exception as inner_e:
                logger.warning("Could not query INFORMATION_SCHEMA.COLUMNS for static pre-deploy check: %s", inner_e)
                return None

    @staticmethod
    def _static_findings(view: SemanticViewDDL, table_columns: Dict[str, Set[str]]) -> List[Tuple[Any, str]]:
        """Return [(item, reason)] for every item with an unresolved reference."""
        findings: List[Tuple[Any, str]] = []
        tables = view.table_aliases()
        phys_of: Dict[str, str] = {}
        for alias, t in tables.items():
            phys = str(t.physical or "")
            if phys.startswith("("):
                continue
            segs = re.findall(r'"(?:[^"]|"")+"|[^.]+', phys)
            phys_of[alias] = norm_ident(segs[-1]) if segs else alias

        def cols_of(alias: str) -> Set[str]:
            return table_columns.get(phys_of.get(alias.upper(), ""), set())

        for alias, t in tables.items():
            valid = cols_of(alias)
            if not valid:
                continue
            for key in [t.pk_cols] + t.unique_keys:
                missing = [c for c in key if c.upper() not in valid]
                if missing:
                    findings.append((t, f"key column(s) {missing} of table '{alias}' do not exist in physical table '{phys_of[alias]}'"))
                    break

        for r in view.items("RELATIONSHIPS"):
            for side_alias, side_cols, side in ((r.from_alias, r.from_cols, "from"), (r.to_alias, r.to_cols, "to")):
                valid = cols_of(side_alias or "")
                missing = [c for c in side_cols if valid and c.upper() not in valid]
                if missing:
                    findings.append((r, f"relationship '{r.name}' ({side}) column(s) {missing} do not exist in physical table '{phys_of.get(str(side_alias).upper(), side_alias)}'"))
                    break

        declared_dims = {(str(d.alias or "").upper(), d.name.upper()) for d in view.items("DIMENSIONS")}
        declared_facts = {(str(f.alias or "").upper(), f.name.upper()) for f in view.items("FACTS")}
        declared_metrics = {
            (str(m.alias or "").upper(), m.name.upper())
            for m in view.items("METRICS")
            if not m.is_null_placeholder
        }
        metric_names = {n for _, n in declared_metrics}

        ref_re = re.compile(r'(?<![\w$"])"?([A-Za-z_][A-Za-z0-9_$]*)"?\s*\.\s*"((?:[^"]|"")+)"')

        def strip_literals(expr: str) -> str:
            return re.sub(r"'(?:''|[^'])*'", "''", expr or "")

        reach: Dict[str, Set[str]] = {}
        for r in view.items("RELATIONSHIPS"):
            reach.setdefault(str(r.from_alias).upper(), set()).add(str(r.to_alias).upper())

        def reachable(src: str) -> Set[str]:
            seen = {src}
            stack = [src]
            while stack:
                cur = stack.pop()
                for nxt in reach.get(cur, ()):
                    if nxt not in seen:
                        seen.add(nxt)
                        stack.append(nxt)
            return seen

        for clause in ("FACTS", "DIMENSIONS", "METRICS"):
            for it in view.items(clause):
                if clause == "METRICS" and it.is_null_placeholder:
                    continue
                expr = strip_literals(it.expr)
                # PARTITION BY EXCLUDING / NON ADDITIVE BY must name declared dimensions.
                if clause == "METRICS":
                    for nab_alias, nab_col in ref_re.findall(it.non_additive):
                        if (nab_alias.upper(), unquote_ident(f'"{nab_col}"').upper()) not in declared_dims:
                            findings.append((it, f"not applicable: NON ADDITIVE BY references '{nab_alias}.{nab_col}', which is not a declared dimension (semi-additive metric cannot be expressed without it)"))
                            break
                    else:
                        excl_bad = None
                        for excl in re.findall(r"PARTITION\s+BY\s+EXCLUDING\s+([^)]*?)(?:\s+ORDER\s+BY\b|\))", expr, re.IGNORECASE | re.DOTALL):
                            for a, c in ref_re.findall(excl):
                                if (a.upper(), c.upper()) not in declared_dims:
                                    excl_bad = f"{a}.{c}"
                        if excl_bad:
                            findings.append((it, f"not applicable: PARTITION BY EXCLUDING references '{excl_bad}', which is not a declared dimension"))
                        else:
                            agg_tables: Set[str] = set()
                            for agg in re.finditer(r"\b(?:SUM|AVG|COUNT|MIN|MAX|MEDIAN)\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)", expr, re.IGNORECASE):
                                for a, _c in ref_re.findall(agg.group(1)):
                                    if a.upper() in tables and a.upper() != str(it.alias or "").upper():
                                        agg_tables.add(a.upper())
                            if agg_tables and it.alias:
                                unreachable = sorted(agg_tables - reachable(str(it.alias).upper()))
                                if unreachable:
                                    findings.append((it, (
                                        f"not applicable: aggregates columns of {unreachable}, which are not reachable "
                                        f"(many -> one) from its table '{it.alias}' (Snowflake 010219 granularity rule)"
                                    )))
                                    continue
                    if findings and findings[-1][0] is it:
                        continue
                bad_ref = None
                for m_ref in ref_re.finditer(expr):
                    a, c = m_ref.group(1), m_ref.group(2)
                    a_u, c_u = a.upper(), c.replace('""', '"').upper()
                    if a_u not in tables:
                        continue
                    is_self = (a_u, c_u) == (str(it.alias or "").upper(), it.name.upper())
                    if ((a_u, c_u) in declared_facts or (a_u, c_u) in declared_dims) and not is_self:
                        continue
                    if clause == "METRICS" and not is_self and ((a_u, c_u) in declared_metrics or c_u in metric_names):
                        continue
                    valid = cols_of(a_u)
                    if valid and c_u not in valid:
                        bad_ref = f"{a}.{c}"
                        break
                if bad_ref:
                    findings.append((it, f"referenced column '{bad_ref}' does not exist in physical table '{phys_of.get(bad_ref.split('.')[0].upper())}'"))
        return findings

    def _validate_ddl_against_information_schema(self, cur: Any, ddls: List[str]) -> List[str]:
        """Static pre-deploy check: every column reference must exist in
        INFORMATION_SCHEMA.COLUMNS. Unlike the previous all-or-nothing
        version, an unresolved reference is isolated to its owning item
        (metric -> NULL + COMMENT, others removed) and ledgered; the deploy
        then proceeds with everything that IS resolvable. Returns the
        (possibly edited) DDL list."""
        table_columns = self._fetch_information_schema_columns(cur)
        if table_columns is None:
            return list(ddls)
        out: List[str] = []
        total = 0
        for ddl in ddls:
            if not ddl or not _is_semantic_view_sql(ddl):
                out.append(ddl)
                continue
            view = SemanticViewDDL(ddl)
            if not view.parsed:
                out.append(ddl)
                continue
            seen: Set[Tuple[str, str, str]] = set()
            for _ in range(5):  # cascades can expose new findings
                findings = self._static_findings(view, table_columns)
                findings = [(it, r) for it, r in findings if (it.clause, str(it.alias or "").upper(), it.name.upper()) not in seen]
                if not findings:
                    break
                for it, reason in findings:
                    outcomes = isolate_item(view, it, reason, "static pre-deploy check (INFORMATION_SCHEMA.COLUMNS)", seen)
                    total += len(outcomes)
                    self._record_item_outcomes(outcomes, "static pre-deploy check")
            out.append(view.render())
        if total:
            logger.warning("Static pre-deploy check isolated %d item(s); deploying the rest.", total)
        else:
            logger.info("Static pre-deploy check PASSED: all referenced columns exist in INFORMATION_SCHEMA.COLUMNS")
        return out

    # ------------------------------------------------------------------
    # 2. Bounded CREATE remediation
    # ------------------------------------------------------------------
    def _fallback_sanitizer_remediation(self, sql: str, exc: Exception) -> Optional[str]:
        """Legacy invalid-identifier remediation (synthetic anchors like
        MAX_DATE and non-semantic statements). Returns new SQL or None."""
        m = re.search(r"invalid identifier\s+'([^']+)'", str(exc), re.IGNORECASE)
        if not m:
            return None
        sanitizer = getattr(getattr(self, "semantic_view_builder", None), "sanitizer", None)
        if sanitizer is None:
            return None
        invalid_id = m.group(1)
        try:
            fixed, changed, nulled = sanitizer.remediate_invalid_identifier(sql, invalid_id)
        except Exception as rem_exc:  # noqa: BLE001
            logger.warning("Sanitizer remediation raised for '%s': %s", invalid_id, rem_exc)
            return None
        if not changed:
            return None
        if nulled:
            self._record_ddl_deployment_drops(invalid_id, list(nulled), sql, exc)
        return fixed

    def _execute_with_isolation(self, cur: Any, sql: str, context: str) -> str:
        """Execute *sql*; on failure isolate the offending item(s) and retry,
        at most MAX_CREATE_REMEDIATION_ATTEMPTS times. Returns the SQL that
        finally succeeded. Raises ConnectorError (with every isolation
        already ledgered) when an error can't be attributed to an item."""
        current = sql
        is_sv = _is_semantic_view_sql(sql)
        n_items = sum(1 for _ in SemanticViewDDL(sql).all_items()) if is_sv else 1
        cap = max(1, min(MAX_CREATE_REMEDIATION_ATTEMPTS, n_items + 1))
        last_exc: Optional[Exception] = None
        for attempt in range(cap + 1):
            try:
                self.connection_manager._execute_sql(cur, current, context=context if attempt == 0 else f"{context} pass {attempt}")
                if attempt:
                    logger.info("%s succeeded after %d isolation retr%s", context, attempt, "y" if attempt == 1 else "ies")
                return current
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt >= cap:
                    break
                err = short_error(exc)
                new_sql: Optional[str] = None
                if is_sv:
                    view = SemanticViewDDL(current)
                    targets = map_error_to_items(view, str(exc))
                    if targets:
                        reason = f"rejected by Snowflake: {err}"
                        outcomes: List[ItemOutcome] = []
                        seen: Set[Tuple[str, str, str]] = set()
                        for it in targets:
                            outcomes += isolate_item(view, it, reason, f"Snowflake error on CREATE (attempt {attempt + 1})", seen)
                        if outcomes:
                            self._record_item_outcomes(outcomes, "CREATE remediation")
                            new_sql = view.render()
                if new_sql is None or new_sql == current:
                    new_sql = self._fallback_sanitizer_remediation(current, exc)
                if new_sql is None:
                    if is_sv:
                        raise ConnectorError(
                            "Snowflake rejected the semantic view and the error could not be attributed "
                            f"to a single metric, dimension, fact, relationship or table, so it could not be "
                            f"isolated: {err}"
                        ) from exc
                    raise exc
                current = new_sql
        raise ConnectorError(
            f"Snowflake kept rejecting the semantic view after {cap} isolation attempt(s) "
            f"(retry cap reached); last error: {short_error(last_exc)}"
        ) from last_exc

    # ------------------------------------------------------------------
    # 3. Post-create Cortex Analyst checks -- never fail a live view
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_semantic_view_elements(ddl: str) -> Tuple[List[str], List[str]]:
        """Queryable dimension and metric identifiers of a semantic view.

        Handles every metric syntax (PRIVATE / NON ADDITIVE BY / derived
        ``"NAME" AS``). PRIVATE items and NULL placeholders are excluded:
        Snowflake forbids querying PRIVATE items directly, and a NULL
        placeholder has nothing to compile."""
        view = SemanticViewDDL(ddl or "")
        dims = [d.ident for d in view.items("DIMENSIONS") if d.name and not d.private.startswith("PRIVATE")]
        mets = [
            m.ident for m in view.items("METRICS")
            if m.name and not m.private.startswith("PRIVATE") and not m.is_null_placeholder
        ]
        return dims, mets

    @staticmethod
    def _null_metric_in_ddl(ddl: str, metric_ident: str, reason: str) -> str:
        """Replace one metric (any syntax) by ``CAST(NULL AS DOUBLE)`` with
        the reason in ``COMMENT = '...'`` (escaped; never a ``/* */``
        block). Returns *ddl* unchanged if the metric is not found."""
        view = SemanticViewDDL(ddl or "")
        if not view.parsed:
            return ddl
        alias, _, name = metric_ident.rpartition(".")
        item = view.find("METRICS", name, alias or None) or view.find("METRICS", name)
        if item is None:
            return ddl
        view.null_metric(item, f"rejected by Cortex validation: {reason}")
        return view.render()

    def _run_cortex_safety_validation(
        self,
        cursor: Any,
        qualified_view_name: str,
        ddl_sql: str,
        path_type: str,
    ) -> str:
        """Post-create Cortex Analyst safety checks on a view that is ALREADY
        live. Failing dimensions are removed and failing metrics nulled (each
        ledgered), then the view is redeployed through the same bounded
        isolation loop. Nothing here raises: a check that can't be repaired
        becomes a "deployed with warnings" note instead of failing the run."""
        view_name = qualified_view_name.split(".")[-1].strip().strip('"')
        fq_view = f'"{self.config.database}"."{self.config.schema_name}"."{view_name}"'
        warnings: List[str] = []

        dim_identifiers, metric_identifiers = self._extract_semantic_view_elements(ddl_sql)
        logger.info(
            "[%s] Starting Cortex Analyst safety check for %s (%d dimensions, %d metrics)",
            path_type, fq_view, len(dim_identifiers), len(metric_identifiers),
        )

        failed_dims: List[Tuple[str, str]] = []
        for dim in dim_identifiers:
            check_sql = f"SELECT * FROM SEMANTIC_VIEW({fq_view} DIMENSIONS {dim}) LIMIT 0"
            try:
                self.connection_manager._execute_sql(cursor, check_sql, context=f"cortex_safety:dim:{dim}")
            except Exception as exc:  # noqa: BLE001
                failed_dims.append((dim, short_error(exc)))

        failed_metrics: List[Tuple[str, str]] = []
        for metric in metric_identifiers:
            check_sql = f"SELECT * FROM SEMANTIC_VIEW({fq_view} METRICS {metric}) LIMIT 0"
            try:
                self.connection_manager._execute_sql(cursor, check_sql, context=f"cortex_safety:metric:{metric}", expected_failure=True)
            except Exception as exc:  # noqa: BLE001
                err_msg = str(exc)
                if "010277" in err_msg or "Required dimensions" in err_msg:
                    m = re.search(r"Required dimensions\s*\[(.*?)\]", err_msg)
                    if m:
                        dims_clause = ", ".join(d.strip() for d in m.group(1).split(","))
                        retry_sql = f"SELECT * FROM SEMANTIC_VIEW({fq_view} DIMENSIONS {dims_clause} METRICS {metric}) LIMIT 0"
                        try:
                            self.connection_manager._execute_sql(
                                cursor, retry_sql, context=f"cortex_safety:metric_with_dims:{metric}", expected_failure=True
                            )
                            continue
                        except Exception as retry_exc:  # noqa: BLE001
                            failed_metrics.append((metric, short_error(retry_exc)))
                            continue
                failed_metrics.append((metric, short_error(exc)))

        remediated_metric_names: List[str] = []
        dropped_dim_names: List[str] = []
        if failed_dims or failed_metrics:
            view = SemanticViewDDL(ddl_sql)
            seen: Set[Tuple[str, str, str]] = set()
            outcomes: List[ItemOutcome] = []
            for ident, err in failed_dims:
                alias, _, name = ident.rpartition(".")
                it = view.find("DIMENSIONS", name, alias or None)
                if it is not None:
                    outcomes += isolate_item(view, it, f"rejected by Cortex validation: {err}", "post-create dimension compile check", seen)
            for ident, err in failed_metrics:
                alias, _, name = ident.rpartition(".")
                it = view.find("METRICS", name, alias or None) or view.find("METRICS", name)
                if it is not None:
                    outcomes += isolate_item(view, it, f"rejected by Cortex validation: {err}", "post-create metric compile check", seen)
            if outcomes:
                self._record_item_outcomes(outcomes, "Cortex Analyst validation")
                remediated_metric_names = [o.name for o in outcomes if o.entity_kind == "metric"]
                dropped_dim_names = [o.name for o in outcomes if o.entity_kind == "dimension"]
                new_ddl = view.render()
                try:
                    ddl_sql = self._execute_with_isolation(cursor, new_ddl, f"cortex_safety:remediation_deploy:{view_name}")
                except Exception as exc:  # noqa: BLE001
                    warnings.append(
                        "remediation redeploy failed; the previously created view (including the items that failed "
                        f"Cortex checks) is still live: {short_error(exc)}"
                    )
                else:
                    for m_ident, _ in failed_metrics:
                        try:
                            self.connection_manager._execute_sql(
                                cursor, f"SELECT * FROM SEMANTIC_VIEW({fq_view} METRICS {m_ident}) LIMIT 0",
                                context=f"cortex_safety:verify_remediated:{m_ident}",
                            )
                        except Exception as exc:  # noqa: BLE001
                            warnings.append(f"re-verification of remediated metric {m_ident} failed: {short_error(exc)}")

        yaml_verified = False
        try:
            self.connection_manager._execute_sql(
                cursor, f"SELECT SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW('{fq_view}')",
                context=f"cortex_safety:read_yaml:{view_name}",
            )
            yaml_row = cursor.fetchone()
            if not yaml_row or not yaml_row[0]:
                warnings.append(f"could not read Cortex YAML from '{fq_view}'")
            else:
                schema_target = f'"{self.config.database}"."{self.config.schema_name}"'
                self.connection_manager._execute_sql(
                    cursor, "CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML(%s, %s, TRUE)",
                    params=(schema_target, yaml_row[0]), context=f"cortex_safety:verify_yaml:{view_name}",
                )
                yaml_verified = True
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Cortex Analyst verify-only YAML validation did not pass: {short_error(exc)}")

        for w in warnings:
            self._add_deploy_warning(f"{fq_view}: {w}")

        summary = {
            "view_name": view_name,
            "qualified_name": fq_view,
            "yaml_verified": yaml_verified,
            "status": "deployed_with_warnings" if warnings else "passed",
            "warnings": warnings,
            "dimensions_checked": len(dim_identifiers),
            "dimensions_passed": len(dim_identifiers) - len(failed_dims),
            "metrics_checked": len(metric_identifiers),
            "metrics_passed": len(metric_identifiers) - len(failed_metrics),
            "remediated_metrics": remediated_metric_names,
            "dropped_dimensions": dropped_dim_names,
        }
        self.cortex_validation_results.append(summary)
        logger.info(
            "[%s] Cortex Analyst safety check for %s: %s (%d/%d dims, %d/%d metrics passed, %d warning(s))",
            path_type, fq_view, summary["status"], summary["dimensions_passed"], len(dim_identifiers),
            summary["metrics_passed"], len(metric_identifiers), len(warnings),
        )
        return ddl_sql
