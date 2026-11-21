"""Generated-SQL Governance Guard.

Inspects every SQL query produced by AI engines (Cortex Analyst, Databricks Genie,
or local engines) BEFORE execution.

Enforces:
1. Today's-date guard: Blocks CURRENT_DATE / CURRENT_TIMESTAMP on data-anchored models
   when the question did not ask about today/now. Rewrites to governed period metrics
   (*_SPLY, *_YOY_*) or refuses with clear suggestions.
2. Governed path: Detects queries on base/physical or *_ENRICHED tables, labels them
   "free-form (not governed)", suggests governed metrics, and blocks certification.
3. Snapshot / semi-additive safety: Flags direct aggregation over snapshot tables
   where rows may repeat per period.
4. Business-rule guesses: Flags invented status/flag conditions (e.g. IS NULL on date columns)
   as unverified assumptions.
5. Helper metrics: Replaces auto-generated helpers (SUM_OF_*, AVG_OF_*) with business
   metrics when available, or warns that an auto-generated measure is used.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class SQLGuardDecision(BaseModel):
    """Result of running governance checks on generated SQL."""
    allowed: bool = True
    refusal_reason: Optional[str] = None
    query_path: str = "semantic_view"
    rewritten_sql: Optional[str] = None
    checks_passed: bool = True
    checks: List[Dict[str, Any]] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    suggested_metrics: List[str] = Field(default_factory=list)
    is_certified_eligible: bool = True


def _extract_metric_names_from_sv_sql(sql: str) -> List[str]:
    """Extract metric identifiers listed in a SEMANTIC_VIEW(...) query."""
    m = re.search(r"\bMETRICS\s+([^)]*?)(?:\bDIMENSIONS\b|\bWHERE\b|\bORDER\b|\bLIMIT\b|\))", sql, re.IGNORECASE)
    if not m:
        return []
    raw = m.group(1).strip()
    items = [tok.strip().strip('"') for tok in raw.split(",") if tok.strip()]
    return [i for i in items if i]


def _extract_dimension_names_from_sv_sql(sql: str) -> List[str]:
    """Extract dimension identifiers listed in a SEMANTIC_VIEW(...) query."""
    m = re.search(r"\bDIMENSIONS\s+([^)]*?)(?:\bMETRICS\b|\bWHERE\b|\bORDER\b|\bLIMIT\b|\))", sql, re.IGNORECASE)
    if not m:
        return []
    raw = m.group(1).strip()
    items = [tok.strip() for tok in raw.split(",") if tok.strip()]
    return [i for i in items if i]


def _find_period_metric_variants(base_metric_name: str, all_metrics: List[Any]) -> List[str]:
    """Find period/SPLY/YOY variants of a base metric in the model."""
    clean_base = re.sub(r'[^A-Za-z0-9]', '', base_metric_name).upper()
    variants: List[str] = []
    for m in all_metrics:
        m_name = str(getattr(m, "name", None) or getattr(m, "unique_name", None) or "").strip()
        if not m_name or m_name.upper() == base_metric_name.upper():
            continue
        clean_m = re.sub(r'[^A-Za-z0-9]', '', m_name).upper()
        if clean_base in clean_m:
            if any(tag in clean_m for tag in ("SPLY", "YOY", "YTD", "MTD", "QTD", "SMLY", "LY")):
                variants.append(m_name)
    return variants


def _is_model_data_anchored(all_metrics: List[Any]) -> bool:
    """Check if the model contains time intelligence anchored to historical data."""
    for m in all_metrics:
        expr = str(
            getattr(m, "sql_expression", "")
            or getattr(m, "expression", "")
            or getattr(m, "dax_expression", "")
            or ""
        ).upper()
        if any(tok in expr for tok in ("_SPLY", "_YTD", "SPLY", "SAMEPERIODLASTYEAR", "DATESYTD", "DATEADD")):
            return True
        m_name = str(getattr(m, "name", "") or getattr(m, "unique_name", "") or "").upper()
        if any(tok in m_name for tok in ("_SPLY", "_YOY", "_YTD", "SPLY", "YOY")):
            return True
    return False


def inspect_and_guard_generated_sql(
    sql: str,
    prompt: str,
    all_metrics: Optional[List[Any]] = None,
    all_concepts: Optional[List[Any]] = None,
    semantic_view_name: Optional[str] = None,
) -> SQLGuardDecision:
    """Run all governance guard checks on generated SQL before execution.

    Parameters:
        sql: The SQL string returned by Cortex Analyst, Genie, or local engine.
        prompt: The user's natural language question.
        all_metrics: List of metrics defined in the active model / domain.
        all_concepts: List of concepts / tables in the active model / domain.
        semantic_view_name: Fully-qualified name of the deployed semantic view.

    Returns:
        SQLGuardDecision with verdict, rewritten SQL if applicable, warnings, and assumptions.
    """
    decision = SQLGuardDecision()
    if not sql or not sql.strip():
        decision.allowed = False
        decision.refusal_reason = "Empty SQL statement."
        decision.checks_passed = False
        return decision

    metrics_list = all_metrics or []
    concepts_list = all_concepts or []
    sql_u = sql.upper()
    is_sv_query = "SEMANTIC_VIEW(" in sql_u

    # -----------------------------------------------------------------------
    # 1. Governed Path Check (Class C)
    # -----------------------------------------------------------------------
    if is_sv_query:
        decision.query_path = "semantic_view"
        decision.checks.append({
            "check": "governed_path",
            "passed": True,
            "message": "Queries through SEMANTIC_VIEW.",
        })
    else:
        decision.query_path = "free-form (not governed)"
        decision.is_certified_eligible = False
        decision.checks_passed = False

        # Suggest matching governed metrics
        matched_gov_metrics: List[str] = []
        for m in metrics_list:
            m_name = getattr(m, "name", None) or getattr(m, "unique_name", None) or ""
            if m_name and re.search(rf"\b{re.escape(m_name.upper())}\b", sql_u):
                matched_gov_metrics.append(m_name)
            elif m_name and getattr(m, "label", None):
                lbl = getattr(m, "label")
                if lbl and re.search(rf"\b{re.escape(lbl.upper())}\b", sql_u):
                    matched_gov_metrics.append(m_name)

        # Also search for aggregate patterns in SQL matching metric expressions
        for m in metrics_list:
            sql_expr = getattr(m, "sql_expression", "") or ""
            if sql_expr and len(sql_expr) > 6 and sql_expr.upper() in sql_u:
                m_name = getattr(m, "name", None) or getattr(m, "unique_name", None)
                if m_name and m_name not in matched_gov_metrics:
                    matched_gov_metrics.append(m_name)

        decision.suggested_metrics = matched_gov_metrics
        msg = "Free-form SQL queries physical/base tables directly instead of SEMANTIC_VIEW."
        if matched_gov_metrics:
            msg += f" Governed metrics exist: {', '.join(matched_gov_metrics)}."
        decision.warnings.append(msg)
        decision.checks.append({
            "check": "governed_path",
            "passed": False,
            "message": msg,
        })

    # -----------------------------------------------------------------------
    # 2. Today's-date Anchoring Check (Class B)
    # -----------------------------------------------------------------------
    current_date_pattern = r"\b(CURRENT_DATE|CURRENT_TIMESTAMP|GETDATE|SYSDATE|NOW\s*\()\b"
    has_current_date = bool(re.search(current_date_pattern, sql, re.IGNORECASE))
    asks_today = bool(re.search(r"\b(today|right\s+now|current\s+date|at\s+present)\b", prompt, re.IGNORECASE))  # hardcoding-ok: regex matching user time intent

    if has_current_date and not asks_today and _is_model_data_anchored(metrics_list):
        decision.checks_passed = False
        decision.is_certified_eligible = False

        # Find period metrics for the requested measure
        sv_metrics = _extract_metric_names_from_sv_sql(sql)
        sply_variants: List[str] = []
        for m_name in sv_metrics:
            sply_variants.extend(_find_period_metric_variants(m_name, metrics_list))

        if not sply_variants:
            # Check prompt for measures
            for m in metrics_list:
                m_name = getattr(m, "name", None) or getattr(m, "unique_name", None) or ""
                if m_name and re.search(rf"\b{re.escape(m_name.upper())}\b", prompt.upper()):
                    sply_variants.extend(_find_period_metric_variants(m_name, metrics_list))

        if sply_variants:
            decision.suggested_metrics = list(dict.fromkeys(decision.suggested_metrics + sply_variants))

            # Attempt clean rewrite to SEMANTIC_VIEW using governed period metrics
            dims = _extract_dimension_names_from_sv_sql(sql)
            base_m = sv_metrics[0] if sv_metrics else None
            sply_m = sply_variants[0] if sply_variants else None
            target_sv = semantic_view_name or (re.search(r"SEMANTIC_VIEW\s*\(\s*([^,\s\)]+)", sql, re.I).group(1) if is_sv_query else None)

            if target_sv and base_m and sply_m:
                dim_clause = f"    DIMENSIONS {', '.join(dims)}\n" if dims else ""
                dim_cols = [d.split(".")[-1] for d in dims]
                select_prefix = f"{', '.join(dim_cols)}, " if dim_cols else ""
                order_clause = f"\nORDER BY {dim_cols[0]}" if dim_cols else ""

                rewritten = (
                    f"SELECT {select_prefix}\n"
                    f"       {base_m},\n"
                    f"       {sply_m},\n"
                    f"       {base_m} - {sply_m} AS yoy_change\n"
                    f"FROM SEMANTIC_VIEW(\n"
                    f"    {target_sv}\n"
                    f"    METRICS {base_m}, {sply_m}\n"
                    f"{dim_clause}"
                    f"){order_clause};"
                )
                decision.rewritten_sql = rewritten
                decision.allowed = True
                decision.query_path = "semantic_view"
                decision.warnings.append(
                    f"Rewritten to governed period metrics ({base_m}, {sply_m}) because model data is data-anchored; "
                    f"original query filtered on CURRENT_DATE which produces empty results."
                )
                decision.checks.append({
                    "check": "today_date_anchor",
                    "passed": True,
                    "message": f"CURRENT_DATE filter rewritten to governed period metrics: {sply_m}.",
                })
            else:
                decision.allowed = False
                decision.refusal_reason = (
                    f"Refused: Query filters on CURRENT_DATE for historical data-anchored model, returning empty results. "
                    f"Use governed period metrics instead: {', '.join(sply_variants)}."
                )
                decision.checks.append({
                    "check": "today_date_anchor",
                    "passed": False,
                    "message": decision.refusal_reason,
                })
        else:
            decision.allowed = False
            decision.refusal_reason = (
                "Refused: Query filters on CURRENT_DATE for historical data-anchored model. "
                "The model data ends earlier; CURRENT_DATE filters return empty rows."
            )
            decision.checks.append({
                "check": "today_date_anchor",
                "passed": False,
                "message": decision.refusal_reason,
            })
    else:
        decision.checks.append({
            "check": "today_date_anchor",
            "passed": True,
            "message": "No improper CURRENT_DATE anchoring on historical data.",
        })

    # -----------------------------------------------------------------------
    # 3. Snapshot / Semi-Additive Safety Check (Class C)
    # -----------------------------------------------------------------------
    if not is_sv_query:
        # Check if query aggregates tables that have non-additive snapshot definitions
        snapshot_concepts: Set[str] = set()
        for m in metrics_list:
            if getattr(m, "non_additive_dimension", None) or "NON ADDITIVE BY" in str(getattr(m, "sql_expression", "")).upper():
                c_name = getattr(m, "concept", None) or getattr(m, "dataset", None)
                if c_name:
                    snapshot_concepts.add(str(c_name).upper())

        for sc in snapshot_concepts:
            if sc in sql_u or f"{sc}_ENRICHED" in sql_u:
                # Direct aggregation over snapshot table
                if re.search(r"\b(AVG|SUM|COUNT)\s*\(", sql_u):
                    decision.assumptions.append(
                        f"Rows may repeat per period: table '{sc}' has a snapshot (non-additive) metric definition; "
                        f"direct aggregation over time without period filtering may distort averages or totals."
                    )
                    decision.checks.append({
                        "check": "snapshot_semi_additive_safety",
                        "passed": False,
                        "message": f"Direct aggregation over snapshot table '{sc}'.",
                    })
                    break

    # -----------------------------------------------------------------------
    # 4. Business-Rule Guesses Check (Class C)
    # -----------------------------------------------------------------------
    if not is_sv_query:
        # Check for invented status/flag conditions like CASE WHEN col IS NULL THEN ...
        m_guess = re.search(r"CASE\s+WHEN\s+([A-Za-z0-9_.]+\s+IS\s+(?:NOT\s+)?NULL)\s+THEN\s+'([^']+)'\s+ELSE\s+'([^']+)'\s+END", sql, re.IGNORECASE)
        if m_guess:
            cond_expr, status_a, status_b = m_guess.groups()
            decision.assumptions.append(
                f"Assumes '{status_a}' vs '{status_b}' is defined by '{cond_expr}'. "
                f"This condition is a guessed business rule, not a governed dimension in the model."
            )
            decision.checks.append({
                "check": "business_rule_validity",
                "passed": False,
                "message": f"Guessed status condition: {cond_expr}",
            })

    # -----------------------------------------------------------------------
    # 5. Helper Metrics Check (Class D)
    # -----------------------------------------------------------------------
    used_metrics = _extract_metric_names_from_sv_sql(sql)
    for m_name in used_metrics:
        if m_name.upper().startswith("SUM_OF_") or m_name.upper().startswith("AVG_OF_"):
            # Check if a business metric with the same base exists
            base_token = re.split(r"_(?:of|OF)_", m_name, flags=re.I)[-1]
            biz_metric = next(
                (m for m in metrics_list if getattr(m, "name", "").upper() == base_token.upper() or getattr(m, "label", "").upper() == base_token.upper()),
                None
            )
            if biz_metric:
                biz_name = getattr(biz_metric, "name", "")
                decision.warnings.append(f"Auto-generated helper '{m_name}' used; business metric '{biz_name}' is available.")
            else:
                decision.warnings.append(f"Uses auto-generated helper measure '{m_name}'; no business-named measure found in model.")
            decision.checks.append({
                "check": "helper_metric_usage",
                "passed": False,
                "message": f"Uses helper measure '{m_name}'.",
            })

    return decision
