"""Metric view repair for the Databricks publisher.

Rewrites a metric view when Databricks cannot take it as generated:
structure-only stubs for missing source tables (joins pruned at any depth),
stripping a measure Databricks rejected, and the view comment (SemaBridge
marker + business description). Diagnostics are recorded for the run report,
never written into the comment.

Split out of databricks_publisher.py unchanged (a mixin of
DatabricksPublisher).
"""
from __future__ import annotations

import re
from typing import Any, Optional

import yaml

from semabridge.sml.models import SMLModel
from semabridge.utils.logger import get_logger

logger = get_logger("semabridge.connectors.databricks_publisher")


class DatabricksViewRepairMixin:
    """Methods of DatabricksPublisher that rewrite / repair metric view SQL."""

    _COMMENT_MARKER_SEPARATOR = " — "

    def _metric_view_comment(self, model_label: str, view_label: str, description: Any) -> str:
        marker = f"Semabridge: {model_label} - {view_label}"
        text = re.sub(r"\s+", " ", str(description or "")).strip()
        if not text:
            return marker
        return f"{marker}{self._COMMENT_MARKER_SEPARATOR}{text}"[:1000]

    @classmethod
    def _comment_view_hint(cls, comment_text: str) -> str:
        """The <view> part of a "Semabridge: <model> - <view>[ — description]" comment."""
        marker = str(comment_text or "").split(cls._COMMENT_MARKER_SEPARATOR, 1)[0]
        return marker.rsplit(" - ", 1)[-1].strip() if " - " in marker else ""

    def _record_omitted_measure(self, name: str, reason: str) -> None:
        """A measure Databricks rejected and SemaBridge left out of the view."""
        omitted = getattr(self, "_omitted_measures", None)
        if omitted is None:
            self._omitted_measures = omitted = []
        if not any(o.get("name") == name for o in omitted):
            omitted.append({"name": str(name), "reason": str(reason)})

    def _record_null_projection(self, name: str, item_type: str, reason: str) -> None:
        """Ledger a NULL projection (column or metric source) with its reason
        -- the item is reported as NOT converted, never as live."""
        details = getattr(self, "_null_projection_details", None)
        if details is None:
            self._null_projection_details = details = []
        if any(d.get("name") == name and d.get("item_type") == item_type for d in details):
            return
        details.append({"name": name, "item_type": item_type, "reason": f"projected as NULL: {reason}"})

    def _extract_unresolved_column_name(self, error_text: str) -> str:
        """Extract unresolved column identifier from Databricks error text."""
        text = str(error_text or "")
        match = re.search(
            r"name\s+`([^`]+)`\s+cannot\s+be\s+resolved",
            text,
            re.IGNORECASE,
        )
        if not match:
            return ""
        return self._sanitize_identifier(match.group(1)).lower()

    def _remove_failing_metric_measure(
        self,
        view_sql: str,
        error_text: str,
    ) -> str | None:
        """Remove metric-view entries referencing an unresolved identifier.

        This primarily removes failing measures, and also drops dimensions that
        directly reference the unresolved identifier so degraded retries can
        proceed when the parser failure is dimension-driven.
        """
        unresolved_col = self._extract_unresolved_column_name(error_text)
        if not unresolved_col:
            return None

        metric_sql_match = re.search(
            r"(?is)^(CREATE\s+OR\s+REPLACE\s+VIEW\s+`[^`]+`\.`[^`]+`\.`[^`]+`\s+WITH\s+METRICS\s+LANGUAGE\s+YAML\s+AS\s+\$\$)\s*(.*?)\s*\$\$\s*$",
            str(view_sql or ""),
        )
        if not metric_sql_match:
            return None

        prefix = metric_sql_match.group(1)
        yaml_body = metric_sql_match.group(2)

        try:
            payload = yaml.safe_load(yaml_body) or {}
        except yaml.YAMLError:
            return None

        measures = payload.get("measures")
        unresolved_pattern = re.compile(rf"\b{re.escape(unresolved_col)}\b", re.IGNORECASE)

        removed_measure_names: list[str] = []
        if isinstance(measures, list) and measures:
            retained_measures: list[dict[str, Any]] = []
            for measure in measures:
                if not isinstance(measure, dict):
                    retained_measures.append(measure)
                    continue
                expr = str(measure.get("expr") or "")
                expr_norm = expr.replace("`", "").replace('"', "")
                if unresolved_pattern.search(expr_norm):
                    removed_measure_names.append(str(measure.get("name") or "unnamed_measure"))
                    continue
                retained_measures.append(measure)
            payload["measures"] = retained_measures

        dimensions = payload.get("dimensions")
        removed_dimension_names: list[str] = []
        if isinstance(dimensions, list) and dimensions:
            retained_dimensions: list[dict[str, Any]] = []
            for dim in dimensions:
                if not isinstance(dim, dict):
                    retained_dimensions.append(dim)
                    continue
                expr = str(dim.get("expr") or "")
                expr_norm = expr.replace("`", "").replace('"', "")
                if unresolved_pattern.search(expr_norm):
                    removed_dimension_names.append(str(dim.get("name") or "unnamed_dimension"))
                    continue
                retained_dimensions.append(dim)
            payload["dimensions"] = retained_dimensions

        if not removed_measure_names and not removed_dimension_names:
            return None

        try:
            repaired_yaml = yaml.safe_dump(payload, sort_keys=False).strip()
        except yaml.YAMLError:
            return None

        if removed_measure_names:
            logger.warning(
                "Metric-view graceful degradation: removed %d measure(s) referencing unresolved column '%s': %s",
                len(removed_measure_names),
                unresolved_col,
                ", ".join(removed_measure_names),
            )
        if removed_dimension_names:
            logger.warning(
                "Metric-view graceful degradation: removed %d dimension(s) referencing unresolved column '%s': %s",
                len(removed_dimension_names),
                unresolved_col,
                ", ".join(removed_dimension_names),
            )

        return f"{prefix}\n{repaired_yaml}\n$$"

    def _strip_rejected_metric_measure(
        self,
        view_sql: str,
        error_text: str,
    ) -> tuple[str | None, str]:
        """Strip a single Databricks-rejected measure from metric-view YAML and record why in ``comment:``.

        Handles both errors that explicitly name a measure (e.g. ``measure 'Bad Measure'``,
        ``INVALID_METRIC_VIEW_MEASURE ... `bad_measure` ``) and ``UNRESOLVED_COLUMN`` errors
        matching a measure expression.

        Returns:
            ``(repaired_sql, omitted_name)`` or ``(None, "")`` if no offending measure could
            be identified or stripping would leave zero measures.
        """
        metric_sql_match = re.search(
            r"(?is)^(CREATE\s+OR\s+REPLACE\s+VIEW\s+`[^`]+`\.`[^`]+`\.`[^`]+`\s+WITH\s+METRICS\s+LANGUAGE\s+YAML\s+AS\s+\$\$)\s*(.*?)\s*\$\$\s*$",
            str(view_sql or ""),
        )
        if not metric_sql_match:
            return None, ""

        prefix = metric_sql_match.group(1)
        yaml_body = metric_sql_match.group(2)
        try:
            payload = yaml.safe_load(yaml_body) or {}
        except yaml.YAMLError:
            return None, ""

        measures = payload.get("measures")
        if not isinstance(measures, list) or len(measures) <= 1:
            # Never strip the last remaining measure via named-measure stripping;
            # fall back to unresolved-column handler if applicable.
            if "UNRESOLVED_COLUMN" in str(error_text or "").upper():
                repaired = self._remove_failing_metric_measure(view_sql, error_text)
                if repaired and repaired != view_sql:
                    return repaired, self._extract_unresolved_column_name(error_text) or "unresolved_column"
            return None, ""

        err = str(error_text or "")
        target_index: Optional[int] = None
        omitted_name = ""

        # 1. Explicit "measure '<name>'" / "measure `<name>`" / "measure <name>" pattern
        explicit_patterns = [
            r"(?i)\bmeasure\s+[`'\"]?([^`'\"\n,:;()]+)[`'\"]?",
            r"(?i)\bmetric\s+[`'\"]?([^`'\"\n,:;()]+)[`'\"]?",
        ]
        candidate_names: list[str] = []
        for pat in explicit_patterns:
            for m in re.finditer(pat, err):
                cand = m.group(1).strip()
                if cand:
                    candidate_names.append(cand)

        if candidate_names:
            for idx, measure in enumerate(measures):
                if not isinstance(measure, dict):
                    continue
                m_name = str(measure.get("name") or "").strip()
                if not m_name:
                    continue
                m_lower = m_name.lower()
                for cand in candidate_names:
                    c_lower = cand.lower()
                    if c_lower == m_lower or c_lower.startswith(m_lower + " ") or m_lower == c_lower.split()[0]:
                        target_index = idx
                        omitted_name = m_name
                        break
                if target_index is not None:
                    break

        # 2. Match any measure name present in the error text (longest-first to avoid partial matches)
        if target_index is None:
            indexed_names = [
                (idx, str(m.get("name") or "").strip())
                for idx, m in enumerate(measures)
                if isinstance(m, dict) and str(m.get("name") or "").strip()
            ]
            indexed_names.sort(key=lambda item: len(item[1]), reverse=True)
            for idx, m_name in indexed_names:
                pat = re.compile(
                    rf"(?<![A-Za-z0-9_])(?:[`'\"])?{re.escape(m_name)}(?:[`'\"])?(?![A-Za-z0-9_])",
                    re.IGNORECASE,
                )
                if pat.search(err):
                    target_index = idx
                    omitted_name = m_name
                    break

        # 3. Fallback: UNRESOLVED_COLUMN in measure expr
        if target_index is None and "UNRESOLVED_COLUMN" in err.upper():
            unresolved_col = self._extract_unresolved_column_name(err)
            if unresolved_col:
                unresolved_pat = re.compile(rf"\b{re.escape(unresolved_col)}\b", re.IGNORECASE)
                for idx, measure in enumerate(measures):
                    if not isinstance(measure, dict):
                        continue
                    expr_norm = str(measure.get("expr") or "").replace("`", "").replace('"', "")
                    if unresolved_pat.search(expr_norm):
                        target_index = idx
                        omitted_name = str(measure.get("name") or unresolved_col)
                        break

        if target_index is None:
            return None, ""

        retained_measures = [m for idx, m in enumerate(measures) if idx != target_index]
        if not retained_measures:
            return None, ""

        payload["measures"] = retained_measures
        # Diagnostics belong in the run report, not in the view comment (the
        # comment carries business meaning that users and Genie read).
        short_reason = " ".join(err.split())[:300]
        self._record_omitted_measure(omitted_name, short_reason)

        try:
            repaired_yaml = yaml.safe_dump(payload, sort_keys=False).strip()
        except yaml.YAMLError:
            return None, ""

        logger.warning(
            "Per-measure deploy retry: stripped rejected measure '%s' from metric view YAML. Reason: %s",
            omitted_name,
            short_reason,
        )
        return f"{prefix}\n{repaired_yaml}\n$$", omitted_name

    def _rewrite_metric_view_sql_for_missing_sources(
        self,
        view_sql: str,
        sml_model: SMLModel,
        missing_tables: list[str],
    ) -> Optional[str]:
        """Rewrite a Metric View DDL to use an inline SQL schema stub when physical
        source/join tables do not exist yet in Databricks Unity Catalog.

        Zero ``CREATE TABLE`` statements are issued: Databricks Metric Views v1.1
        natively support ``source: "SELECT CAST(NULL AS ...) WHERE 1=0"``. Any
        dimensions/measures that referenced missing joined tables are flattened
        into the inline SQL projection so ``DESCRIBE``, ``SELECT * LIMIT 0``, and
        ``SELECT MEASURE(...) LIMIT 0`` all succeed cleanly.
        """
        match = re.match(r"^(.*?AS\s+\$\$\n)(.*?)(\n\$\$.*)$", view_sql, re.DOTALL)
        if not match:
            return None

        prefix, yaml_body, suffix = match.group(1), match.group(2), match.group(3)
        try:
            payload = yaml.safe_load(yaml_body)
        except yaml.YAMLError:
            return None
        if not isinstance(payload, dict):
            return None

        def _norm_tbl(ref: str) -> str:
            return str(ref or "").strip().replace("`", "").replace('"', "").lower()

        missing_norm = {_norm_tbl(t) for t in missing_tables if t}
        root_source = str(payload.get("source") or "").strip()
        root_missing = _norm_tbl(root_source) in missing_norm

        # Build SML column-name -> Spark SQL type lookup (exact, sanitized, and 4-char prefixed)
        col_type_map: dict[str, str] = {}
        for ds in (sml_model.datasets or []):
            ds_prefix = self._sanitize_identifier(ds.unique_name)[:4].lower()
            for col in (ds.columns or []):
                spark_t = self._sml_dtype_to_spark_sql(getattr(col, "data_type", None))
                for raw_key in (
                    str(col.unique_name or "").strip(),
                    self._sanitize_identifier(col.unique_name or ""),
                    str(getattr(col, "source_column", "") or "").strip(),
                ):
                    if not raw_key:
                        continue
                    lk = raw_key.lower()
                    col_type_map.setdefault(lk, spark_t)
                    if ds_prefix:
                        col_type_map.setdefault(f"{ds_prefix}_{lk}", spark_t)

        # Partition joins at every depth: a join whose backing table does not
        # exist is dropped together with the joins nested under it. Every
        # alias path (e.g. ("product", "category")) is recorded as kept or
        # removed so references through a removed join can be rewritten.
        raw_joins = payload.get("joins")
        removed_join_aliases: set[str] = set()
        alias_paths: dict[tuple[str, ...], bool] = {}  # path -> removed?

        def _mark_removed(joins: Any, parent: tuple[str, ...]) -> None:
            for j in joins or []:
                if isinstance(j, dict) and str(j.get("name") or "").strip():
                    path = parent + (str(j.get("name")).strip().lower(),)
                    alias_paths[path] = True
                    _mark_removed(j.get("joins"), path)

        def _prune(joins: Any, parent: tuple[str, ...]) -> list[dict[str, Any]]:
            kept: list[dict[str, Any]] = []
            for j in joins or []:
                if not isinstance(j, dict):
                    continue
                j_name = str(j.get("name") or "").strip().lower()
                path = parent + (j_name,) if j_name else parent
                if _norm_tbl(str(j.get("source") or "").strip()) in missing_norm:
                    if j_name:
                        if not parent:
                            removed_join_aliases.add(j_name)
                        alias_paths[path] = True
                        _mark_removed(j.get("joins"), path)
                    continue
                if j_name:
                    alias_paths[path] = False
                if isinstance(j.get("joins"), list):
                    children = _prune(j.get("joins"), path)
                    j = dict(j)
                    if children:
                        j["joins"] = children
                    else:
                        j.pop("joins", None)
                kept.append(j)
            return kept

        kept_joins: list[dict[str, Any]] = _prune(raw_joins, ()) if isinstance(raw_joins, list) else []

        # Collect numeric-context columns from measure expressions so sum()/avg() never get STRING
        measures = payload.get("measures") if isinstance(payload.get("measures"), list) else []
        numeric_measure_cols: set[str] = set()
        for m in measures:
            if not isinstance(m, dict):
                continue
            m_expr = str(m.get("expr") or "")
            for num_match in re.finditer(
                r"\b(?:sum|avg|min|max)\s*\(\s*(?:distinct\s+)?`?([A-Za-z_][A-Za-z0-9_]*)`?\s*\)",
                m_expr,
                flags=re.IGNORECASE,
            ):
                numeric_measure_cols.add(num_match.group(1).lower())

        def _infer_col_type(col_name: str, fallback: str = "STRING") -> str:
            lk = col_name.lower()
            if lk in col_type_map:
                t = col_type_map[lk]
                if lk in numeric_measure_cols and t not in {"BIGINT", "DOUBLE"}:
                    return "DOUBLE"
                return t
            if lk in numeric_measure_cols:
                return "DOUBLE"
            return fallback

        projected_cols: dict[str, tuple[str, str]] = {}

        def _project(col_name: str, fallback: str = "STRING") -> None:
            safe = str(col_name or "").strip().strip("`")
            if not safe:
                return
            lk = safe.lower()
            dtype = _infer_col_type(safe, fallback=fallback)
            if lk not in projected_cols:
                projected_cols[lk] = (safe, dtype)
            elif dtype in {"DOUBLE", "BIGINT"} and projected_cols[lk][1] == "STRING":
                projected_cols[lk] = (safe, dtype)

        # Rewrite dimensions and collect alias -> flattened column mappings
        dimensions = payload.get("dimensions") if isinstance(payload.get("dimensions"), list) else []
        alias_col_replacements: dict[str, str] = {}
        alias_expr_re = re.compile(r"^`?([A-Za-z_][A-Za-z0-9_]*)`?\.`?([A-Za-z0-9_ ]+)`?$")
        simple_col_re = re.compile(r"^`?([A-Za-z_][A-Za-z0-9_]*)`?$")

        for dim in dimensions:
            if not isinstance(dim, dict):
                continue
            dim_name = str(dim.get("name") or "").strip()
            dim_expr = str(dim.get("expr") or "").strip()
            am = alias_expr_re.match(dim_expr)
            if am and am.group(1).lower() in removed_join_aliases:
                alias_key = f"{am.group(1).lower()}.{am.group(2).strip().lower()}"
                flat_col = self._sanitize_identifier(dim_name) or self._sanitize_identifier(am.group(2))
                alias_col_replacements[alias_key] = flat_col
                if root_missing:
                    dim["expr"] = f"`{flat_col}`"
                    _project(flat_col)
                else:
                    dim["expr"] = f"CAST(NULL AS {_infer_col_type(flat_col)})"
            elif root_missing:
                sm = simple_col_re.match(dim_expr)
                if sm:
                    _project(sm.group(1))
                elif dim_name:
                    flat_col = self._sanitize_identifier(dim_name)
                    if flat_col:
                        dim["expr"] = f"`{flat_col}`"
                        _project(flat_col)

        # Any remaining reference that goes THROUGH a removed join, at any
        # depth (product.product, product.category.channel), becomes one flat
        # stub column (root missing) or a typed NULL (root kept). Left as is,
        # Databricks would read it as a field of a plain column and fail with
        # INVALID_EXTRACT_BASE_FIELD_TYPE.
        dotted_ref_re = re.compile(
            r"(?<![A-Za-z0-9_`.])`?([A-Za-z_][A-Za-z0-9_]*)`?((?:\.`?[A-Za-z_][A-Za-z0-9_ ]*?`?)+)(?![A-Za-z0-9_(])"
        )

        def _rewrite_removed_refs(expr: str) -> str:
            if not any(alias_paths.values()):
                return expr

            def _sub(match: "re.Match[str]") -> str:
                head = match.group(1)
                tail = [seg.strip("`").strip() for seg in match.group(2).split(".") if seg.strip("`").strip()]
                segments = [head] + tail
                path: tuple[str, ...] = ()
                for seg in segments[:-1]:
                    path = path + (seg.lower(),)
                    if path not in alias_paths:
                        return match.group(0)  # not a join path (e.g. a literal 1.5)
                    if alias_paths[path]:
                        flat = self._sanitize_identifier("_".join(segments)).lower()
                        if root_missing:
                            _project(flat)
                            return f"`{flat}`"
                        return f"CAST(NULL AS {_infer_col_type(segments[-1])})"
                return match.group(0)

            # Leave quoted string literals untouched.
            parts = re.split(r"('(?:''|[^'])*')", expr)
            return "".join(part if i % 2 else dotted_ref_re.sub(_sub, part) for i, part in enumerate(parts))

        for dim in dimensions:
            if isinstance(dim, dict) and dim.get("expr"):
                dim["expr"] = _rewrite_removed_refs(str(dim.get("expr")))

        # Rewrite measure expressions referencing removed join aliases and project referenced columns
        for m in measures:
            if not isinstance(m, dict):
                continue
            m_expr = str(m.get("expr") or "")
            if alias_col_replacements:
                for alias_key, flat_col in alias_col_replacements.items():
                    alias_part, col_part = alias_key.split(".", 1)
                    pattern = re.compile(
                        rf"`?{re.escape(alias_part)}`?\.`?{re.escape(col_part)}`?",
                        flags=re.IGNORECASE,
                    )
                    replacement = f"`{flat_col}`" if root_missing else f"CAST(NULL AS {_infer_col_type(flat_col)})"
                    m_expr = pattern.sub(replacement, m_expr)
            m_expr = _rewrite_removed_refs(m_expr)
            m["expr"] = m_expr

            if root_missing:
                # Strip single-quoted string literals before scanning for column identifiers
                expr_no_strings = re.sub(r"'(?:''|[^'])*'", " ", m_expr)
                for tok_match in re.finditer(r"`([A-Za-z_][A-Za-z0-9_]*)`|\b([A-Za-z_][A-Za-z0-9_]*)\b", expr_no_strings):
                    tok = tok_match.group(1) or tok_match.group(2)
                    if not tok:
                        continue
                    if tok.lower() in self._STUB_SQL_KEYWORDS:
                        continue
                    # Skip function calls (identifier immediately followed by '(')
                    end_pos = tok_match.end()
                    if end_pos < len(expr_no_strings) and expr_no_strings[end_pos:].lstrip().startswith("("):
                        continue
                    _project(tok, fallback="DOUBLE" if tok.lower() in numeric_measure_cols else "STRING")

        # Ensure join keys for any kept joins are projected on the stub root
        if root_missing and kept_joins:
            for j in kept_joins:
                for u_col in (j.get("using") or []):
                    if isinstance(u_col, str) and u_col.strip():
                        _project(u_col.strip())

        if root_missing:
            if not projected_cols:
                projected_cols["_semabridge_stub_key"] = ("_semabridge_stub_key", "BIGINT")
            select_items = [
                f"CAST(NULL AS {dtype}) AS `{col}`"
                for col, dtype in projected_cols.values()
            ]
            payload["source"] = f"SELECT {', '.join(select_items)} WHERE 1=0"

        if kept_joins:
            payload["joins"] = kept_joins
        else:
            payload.pop("joins", None)

        # The structure-only state is reported on the run (views_stubbed) and
        # tagged by the deploy summary; the business comment is left alone.

        try:
            repaired_yaml = yaml.safe_dump(payload, sort_keys=False, width=4096).strip()
        except yaml.YAMLError:
            return None

        return f"{prefix}{repaired_yaml}{suffix}"
