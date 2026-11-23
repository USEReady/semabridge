"""BI-Truth Test Suite: Generate Q&A from certified metrics, approve, run against Cortex/agent, track accuracy."""

from __future__ import annotations

import datetime as _dt
import decimal as _dec
import json
import logging
import math
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from semabridge.reconciliation.golden_verifier import (
    _escape_dax_col,
    _escape_dax_table,
    _format_sql_identifier,
)
from semabridge.utils.null_sentinel import is_null_cast_sql

logger = logging.getLogger(__name__)

_CASES_KEY = "cases"  # hardcoding-ok: BI truth test suite dictionary key


class TruthCasesResult(list):
    """List of generated truth cases with structured addition and skip counts."""

    def __init__(
        self,
        items: Optional[List[Dict[str, Any]]] = None,
        added_count: int = 0,
        skipped_count: int = 0,
        skipped: Optional[List[Dict[str, Any]]] = None,
        view: Optional[str] = None,
    ):
        super().__init__(items or [])
        self.added_count = added_count
        self.skipped_count = skipped_count
        self.skipped = list(skipped or [])
        self.view = view
        self.semantic_view = view


def _suite_path() -> Path:
    p = Path.home() / ".semabridge" / "bi_truth_suite.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _sanitize_cell_value(val: Any) -> Any:
    """Recursively convert date, datetime, and Decimal values to JSON-serializable primitives."""
    if val is None:
        return None
    if isinstance(val, (_dt.date, _dt.datetime)) or (hasattr(val, "isoformat") and callable(getattr(val, "isoformat", None))):
        return val.isoformat()
    if isinstance(val, (uuid.UUID,)):
        return str(val)
    if isinstance(val, _dec.Decimal):
        if val.is_nan():
            return "NaN"
        if val.is_infinite():
            return "Infinity" if val > 0 else "-Infinity"
        if val % 1 == 0:
            return int(val)
        try:
            f = float(val)
            if _dec.Decimal(str(f)) == val:
                return f
        except Exception as ex:
            logger.warning("Decimal conversion fallback: %s", ex)
        return str(val)
    if isinstance(val, (list, tuple, set)):
        return [_sanitize_cell_value(x) for x in val]
    if isinstance(val, dict):
        return {str(k): _sanitize_cell_value(v) for k, v in val.items()}
    return val


def _row_to_case_dict(r: Any) -> Dict[str, Any]:
    exp_val = None
    if getattr(r, "expected_value_json", None):
        try:
            exp_val = json.loads(r.expected_value_json)
        except Exception:
            exp_val = r.expected_value_json
    exp_val = _sanitize_cell_value(exp_val)
    dims = []
    if getattr(r, "dimensions_json", None):
        try:
            dims = json.loads(r.dimensions_json)
        except Exception:
            dims = []
    return {
        "id": r.id,
        "project_id": r.project_id,
        "domain": r.domain,
        "metric_name": r.metric_name,
        "question": r.question,
        "expected_sql": r.expected_sql,
        "expected_dax": r.expected_dax,
        "expected_row_count": r.expected_row_count,
        "status": r.status,
        "created_at": r.created_at,
        "expected_value": exp_val,
        "power_bi_reference_value": getattr(r, "power_bi_reference_value", None),
        "certified_parity": getattr(r, "certified_parity", False) or False,
        "expected_evaluated_at": r.expected_evaluated_at,
        "evaluation_status": r.evaluation_status,
        "evaluation_note": r.evaluation_note,
        "dimensions": dims,
        "question_kind": getattr(r, "question_kind", "total") or "total",
    }


def _row_to_run_dict(r: Any) -> Dict[str, Any]:
    res_list = []
    if getattr(r, "results_json", None):
        try:
            res_list = json.loads(r.results_json)
        except Exception:
            res_list = []
    return {
        "id": r.id,
        "project_id": r.project_id,
        "total": r.total_cases,
        "passed": r.passed_cases,
        "accuracy_pct": r.accuracy_score,
        "results": res_list,
        "ran_at": r.run_at,
    }


def _load_suite() -> Dict[str, Any]:
    try:
        from semabridge.repository.orm.session_factory import db_manager
        from semabridge.repository.orm.ai_models import BITruthCaseRow, BITruthRunRow
        from semabridge.repository.orm.base import Base

        engine = db_manager.get_engine()
        Base.metadata.create_all(bind=engine)
        cases_dicts = []
        runs_dicts = []
        with db_manager.get_session() as s:
            case_rows = s.query(BITruthCaseRow).all()
            run_rows = s.query(BITruthRunRow).order_by(BITruthRunRow.run_at.desc()).all()
            cases_dicts = [_row_to_case_dict(r) for r in case_rows]
            runs_dicts = [_row_to_run_dict(r) for r in run_rows]

        if not cases_dicts:
            p = _suite_path()
            if p.exists():
                try:
                    legacy_data = json.loads(p.read_text(encoding="utf-8"))
                    legacy_cases = legacy_data.get(_CASES_KEY) or []
                    legacy_runs = legacy_data.get("runs") or []
                    if legacy_cases or legacy_runs:
                        _save_suite({_CASES_KEY: legacy_cases, "runs": legacy_runs})
                        with db_manager.get_session() as s2:
                            c_rows2 = s2.query(BITruthCaseRow).all()
                            r_rows2 = s2.query(BITruthRunRow).all()
                            cases_dicts = [_row_to_case_dict(r) for r in c_rows2]
                            runs_dicts = [_row_to_run_dict(r) for r in r_rows2]
                except Exception as ex:
                    logger.warning("Failed migrating legacy bi_truth_suite.json: %s", ex)

        return {
            _CASES_KEY: cases_dicts,
            "runs": runs_dicts,
        }
    except Exception as exc:
        logger.warning("DB load of bi_truth_suite failed, falling back to local file: %s", exc)
        p = _suite_path()
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                return {_CASES_KEY: list(data.get(_CASES_KEY) or []), "runs": list(data.get("runs") or [])}
            except Exception as ex:
                logger.warning("Reading local suite JSON failed: %s", ex)
        return {_CASES_KEY: [], "runs": []}


def _save_suite(data: Dict[str, Any]) -> None:
    cases_in = data.get(_CASES_KEY) or []
    runs_in = data.get("runs") or []
    try:
        from semabridge.repository.orm.session_factory import db_manager
        from semabridge.repository.orm.ai_models import BITruthCaseRow, BITruthRunRow
        from semabridge.repository.orm.base import Base

        engine = db_manager.get_engine()
        Base.metadata.create_all(bind=engine)

        with db_manager.get_session() as s:
            for c in cases_in:
                exp_json = None
                if c.get("expected_value") is not None:
                    exp_json = json.dumps(c["expected_value"], default=str)
                dims_json = json.dumps(c.get("dimensions") or [], default=str)
                exp_rc = 0 if c.get("expected_row_count") == 0 else int(c.get("expected_row_count") or 1)
                s.merge(
                    BITruthCaseRow(
                        id=str(c.get("id") or f"truth-{uuid.uuid4().hex[:8]}"),
                        project_id=c.get("project_id"),
                        domain=c.get("domain"),
                        metric_name=str(c.get("metric_name") or ""),
                        question=str(c.get("question") or ""),
                        expected_sql=str(c.get("expected_sql") or ""),
                        expected_dax=str(c.get("expected_dax") or ""),
                        expected_row_count=exp_rc,
                        status=str(c.get("status") or "draft"),
                        created_at=str(c.get("created_at") or datetime.now(timezone.utc).isoformat()),
                        expected_value_json=exp_json,
                        power_bi_reference_value=str(c.get("power_bi_reference_value")) if c.get("power_bi_reference_value") is not None else None,
                        certified_parity=bool(c.get("certified_parity", False)),
                        expected_evaluated_at=c.get("expected_evaluated_at"),
                        evaluation_status=c.get("evaluation_status"),
                        evaluation_note=c.get("evaluation_note"),
                        dimensions_json=dims_json,
                        question_kind=str(c.get("question_kind") or "total"),
                    )
                )

            for r in runs_in:
                res_json = json.dumps(r.get("results") or [], default=str)
                s.merge(
                    BITruthRunRow(
                        id=str(r.get("id") or f"trun-{uuid.uuid4().hex[:8]}"),
                        project_id=r.get("project_id"),
                        run_at=str(r.get("ran_at") or r.get("run_at") or datetime.now(timezone.utc).isoformat()),
                        total_cases=int(r.get("total", 0) or 0),
                        passed_cases=int(r.get("passed", 0) or 0),
                        failed_cases=int(r.get("total", 0) - r.get("passed", 0)),
                        accuracy_score=float(r.get("accuracy_pct", 0.0) or 0.0),
                        results_json=res_json,
                    )
                )
    except Exception as exc:
        logger.warning("DB save of bi_truth_suite failed, writing to fallback file: %s", exc)
        p = _suite_path()
        p.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _is_ratio_or_pct_metric(
    name: str,
    label: Optional[str] = None,
    formula: Optional[str] = None,
    agg_type: Optional[str] = None,
) -> bool:
    """Check if metric is a percentage, ratio, rate, or share using word boundaries and formula."""
    text = f"{name} {label or ''}".lower()
    has_ratio_keyword = bool(re.search(r"(?<![A-Za-z0-9])(ratio|rate|percentage|pct|share)(?![A-Za-z0-9])|%", text, re.I))
    has_divide = bool(formula and ("/" in formula or "DIVIDE" in formula.upper()))
    is_custom_agg = agg_type in ("NONE", "CUSTOM", None, "")

    has_amount = bool(re.search(r"(?<![A-Za-z0-9])amount(?![A-Za-z0-9])", text, re.I))
    if (has_amount or agg_type in ("SUM", "COUNT")) and not ("%" in text or has_divide or re.search(r"(?<![A-Za-z0-9])pct(?![A-Za-z0-9])", text, re.I)):
        return False

    return has_ratio_keyword or (is_custom_agg and has_divide)


def _is_window_metric(
    sql_expr: Optional[str],
    desc: Optional[str] = None,
    name: Optional[str] = None,
) -> Tuple[bool, List[str]]:
    """Detect if metric is a window metric and extract ALL required dimensions from PARTITION BY EXCLUDING."""
    expr = str(sql_expr or "")
    pm = re.search(r"PARTITION\s+BY\s+EXCLUDING\s*(?:\(([^)]+)\)|([A-Za-z0-9_.\",\s]+))", expr, re.I)
    if pm:
        raw = pm.group(1) or pm.group(2)
        dims = [d.strip().replace('"', '') for d in raw.split(",") if d.strip()]
        return True, dims
    cm = re.search(r"Must be queried with\s+([A-Za-z0-9_.\"]+)", str(desc or ""), re.I)
    if cm:
        dims = [d.strip().replace('"', '') for d in cm.group(1).split(",") if d.strip()]
        return True, dims
    if re.search(r"\bOVER\s*\(", expr, re.I):
        return True, []
    return False, []


def _is_unanswerable_metric(
    m: Any,
    m_sql: str,
    deploy_reason: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """Identify PRIVATE helpers and NULL placeholders to skip using deployed metadata."""
    m_name = getattr(m, "name", None) or (m.get("name") if isinstance(m, dict) else "") or ""
    is_hidden = getattr(m, "is_hidden", False) or (m.get("is_hidden") if isinstance(m, dict) else False)
    is_private = getattr(m, "is_private", False) or (m.get("is_private") if isinstance(m, dict) else False)
    reason_str = (
        deploy_reason
        or getattr(m, "deploy_reason", None)
        or (m.get("deploy_reason") if isinstance(m, dict) else "")
        or ""
    ).upper()

    if is_hidden or is_private or "PRIVATE" in reason_str:
        return True, "Marked PRIVATE in deployed semantic view"

    if m_name.upper().startswith("SUM_OF_"):
        return True, "Internal technical measure"

    expr = (m_sql or "").strip()
    if not expr or expr.upper() == "NULL" or "CAST(NULL AS" in expr.upper() or is_null_cast_sql(expr):
        return True, "Compiled as NULL placeholder during deployment"
    if "NULL" in reason_str or "UNTRANSLATABLE" in reason_str:
        return True, "Compiled as NULL placeholder during deployment"

    return False, None


def _format_expected_sql(
    view_name: str,
    metric_ident: str,
    dim_ident: Optional[Any] = None,
    is_top_n: bool = False,
    n: int = 5,
    platform: Optional[str] = None,
) -> str:
    """Format runnable query using real view, metric, and dimension identifiers."""
    if not view_name or not str(view_name).strip():
        raise ValueError("view_name is required to format expected SEMANTIC_VIEW SQL")
    clean_view = _format_sql_identifier(view_name)
    clean_metric = _format_sql_identifier(metric_ident)

    target_platform = (platform or "SNOWFLAKE").upper()
    if target_platform == "DATABRICKS":
        if dim_ident:
            if isinstance(dim_ident, (list, tuple)):
                dims_str = ", ".join(_format_sql_identifier(d) for d in dim_ident if d)
            else:
                dims_str = _format_sql_identifier(str(dim_ident))
            if dims_str:
                if is_top_n:
                    return f"SELECT {dims_str}, {clean_metric} FROM {clean_view} ORDER BY {clean_metric} DESC LIMIT {n};"
                return f"SELECT {dims_str}, {clean_metric} FROM {clean_view} GROUP BY {dims_str};"
        return f"SELECT {clean_metric} FROM {clean_view};"

    if dim_ident:
        if isinstance(dim_ident, (list, tuple)):
            dims_str = ", ".join(_format_sql_identifier(d) for d in dim_ident if d)
        else:
            dims_str = _format_sql_identifier(str(dim_ident))
        if dims_str:
            if is_top_n:
                return f"SELECT * FROM SEMANTIC_VIEW({clean_view} METRICS {clean_metric} DIMENSIONS {dims_str}) ORDER BY {clean_metric} DESC LIMIT {n};"
            return f"SELECT * FROM SEMANTIC_VIEW({clean_view} METRICS {clean_metric} DIMENSIONS {dims_str});"
    return f"SELECT * FROM SEMANTIC_VIEW({clean_view} METRICS {clean_metric});"


def _format_expected_dax(metric_name: str, dim_ident: Optional[Any] = None) -> str:
    """Format valid Power BI DAX query with proper 'Table'[Column] syntax or empty string."""
    if not dim_ident:
        return f'EVALUATE ROW("{metric_name}", [{metric_name}])'
    dim_list = [dim_ident] if isinstance(dim_ident, str) else list(dim_ident)
    cols_dax = []
    for d in dim_list:
        clean_dim = str(d).strip()
        if not clean_dim:
            continue
        if "." in clean_dim:
            tbl, col = clean_dim.split(".", 1)
            esc_tbl = _escape_dax_table(tbl.strip().strip("'\""))
            esc_col = _escape_dax_col(col.strip().strip("[]\"'"))
            cols_dax.append(f"'{esc_tbl}'[{esc_col}]")
        elif "[" in clean_dim and clean_dim.endswith("]"):
            tbl, col = clean_dim[:-1].split("[", 1)
            esc_tbl = _escape_dax_table(tbl.strip().strip("'\""))
            esc_col = _escape_dax_col(col.strip())
            cols_dax.append(f"'{esc_tbl}'[{esc_col}]")
        else:
            cols_dax.append(f"[{clean_dim}]")
    if cols_dax:
        cols_part = ", ".join(cols_dax)
        return f"EVALUATE SUMMARIZECOLUMNS({cols_part}, \"{metric_name}\", [{metric_name}])"
    return ""


def _evaluate_sql_against_snowflake(
    project_id: Optional[str],
    sql: str,
    owner_id: Optional[str] = None,
) -> Tuple[Optional[Any], Optional[str]]:
    """Execute a query once against Snowflake to fetch draft expected value with full result rows."""
    if not project_id or not sql:
        return None, None
    try:
        from semabridge.connectors.sql_guard import validate_read_only_sql
        is_safe, safety_err = validate_read_only_sql(sql)
        if not is_safe:
            return None, f"SQL safety check failed: {safety_err}"

        from semabridge.ai.project_connection import resolve_project_warehouse_config
        from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs

        sf = resolve_project_warehouse_config(project_id, "SNOWFLAKE", owner_id=owner_id)
        if sf is None:
            return None, "No warehouse configured"
        kw = get_snowflake_connect_kwargs(sf)
        if not (kw.get("account") and kw.get("user")):
            return None, "Incomplete connection credentials"
        import snowflake.connector

        conn = snowflake.connector.connect(**kw)
        try:
            cur = conn.cursor()
            try:
                cur.execute("ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = 15")
            except Exception as ex:
                logger.warning("Failed setting statement timeout: %s", ex)
            cur.execute(sql)
            cols = [d[0].upper() for d in cur.description] if cur.description else []
            rows = cur.fetchall()
            if not rows:
                return [], None
            res_rows = [
                {cols[i]: _sanitize_cell_value(val) for i, val in enumerate(row)}
                for row in rows
            ]
            return res_rows, None
        finally:
            conn.close()
    except Exception as exc:
        return None, str(exc)


def _evaluate_cases_in_background(cases: List[Dict[str, Any]], owner_id: Optional[str] = None) -> None:
    """Run one Snowflake connection per batch with query timeout in the background."""
    if not cases:
        return
    import threading

    def _worker():
        try:
            from semabridge.connectors.sql_guard import validate_read_only_sql
            from semabridge.ai.project_connection import resolve_project_warehouse_config
            from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs
            import snowflake.connector

            by_pid: Dict[Optional[str], List[Dict[str, Any]]] = {}
            for c in cases:
                pid = c.get("project_id")
                by_pid.setdefault(pid, []).append(c)

            valid_pids: Dict[str, Tuple[Any, Any, List[Dict[str, Any]]]] = {}
            for pid, pcases in by_pid.items():
                if not pid:
                    continue
                sf = resolve_project_warehouse_config(pid, "SNOWFLAKE", owner_id=owner_id)
                if not sf:
                    continue
                kw = get_snowflake_connect_kwargs(sf)
                if not (kw.get("account") and kw.get("user")):
                    continue
                valid_pids[pid] = (sf, kw, pcases)

            if not valid_pids:
                return

            now = datetime.now(timezone.utc).isoformat()
            state = _load_suite()
            cases_map = {c["id"]: c for c in state.get(_CASES_KEY) or []}
            updated = False

            for pid, (sf, kw, pcases) in valid_pids.items():

                conn = None
                try:
                    conn = snowflake.connector.connect(**kw)
                    cur = conn.cursor()
                    try:
                        cur.execute("ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = 15")
                    except Exception as ex:
                        logger.warning("Failed setting statement timeout: %s", ex)

                    for c in pcases:
                        cid = c.get("id")
                        sql = c.get("expected_sql")
                        if not sql or cid not in cases_map:
                            continue

                        is_safe, safety_err = validate_read_only_sql(sql)
                        if not is_safe:
                            cases_map[cid]["evaluation_note"] = f"SQL safety check failed: {safety_err}"
                            cases_map[cid]["evaluation_status"] = "evaluation_failed"
                            updated = True
                            continue

                        try:
                            cur.execute(sql)
                            cols = [d[0].upper() for d in cur.description] if cur.description else []
                            rows = cur.fetchall()
                            res_rows = [
                                {cols[i]: _sanitize_cell_value(val) for i, val in enumerate(row)}
                                for row in rows
                            ]
                            cases_map[cid]["expected_value"] = res_rows
                            cases_map[cid]["expected_evaluated_at"] = now
                            cases_map[cid]["evaluation_status"] = "draft_evaluated"
                            cases_map[cid]["evaluation_note"] = None
                            updated = True
                        except Exception as q_err:
                            cases_map[cid]["evaluation_note"] = str(q_err)
                            cases_map[cid]["evaluation_status"] = "evaluation_failed"
                            updated = True
                except Exception as conn_err:
                    logger.warning("Snowflake connection error in background evaluation for project %s: %s", pid, conn_err)
                finally:
                    if conn:
                        try:
                            conn.close()
                        except Exception as close_err:
                            logger.warning("Failed closing snowflake connection: %s", close_err)

            if updated:
                state[_CASES_KEY] = list(cases_map.values())
                _save_suite(state)
        except Exception as exc:
            logger.warning("Background Snowflake evaluation failed: %s", exc)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()


def _compare_truth_results(
    actual: Any,
    expected: Any,
    tolerance: float = 1e-3,
) -> Tuple[bool, Optional[str]]:
    """Compare generated query result against expected value within tolerance."""
    if expected is None or actual is None:
        return False, "No data returned or recorded for comparison"

    if isinstance(expected, (int, float, str)) and not isinstance(expected, (list, dict)):
        if isinstance(actual, list) and len(actual) == 1 and isinstance(actual[0], dict):
            vals = list(actual[0].values())
            actual_val = vals[0] if len(vals) == 1 else next((v for v in vals if isinstance(v, (int, float))), vals[-1])
        else:
            actual_val = actual

        try:
            ef = float(expected)
            af = float(actual_val)
            delta = abs(ef - af)
            max_tol = tolerance * max(1.0, abs(ef))
            if delta <= max_tol:
                return True, None
            return False, f"Value mismatch: expected {ef}, got {af} (delta {delta} > {max_tol})"
        except (ValueError, TypeError):
            if str(expected).strip().casefold() == str(actual_val).strip().casefold():
                return True, None
            return False, f"Value mismatch: expected '{expected}', got '{actual_val}'"

    if isinstance(expected, list):
        if not isinstance(actual, list):
            return False, f"Expected {len(expected)} rows, but got non-list result"
        if len(expected) == 0 and len(actual) == 0:
            return False, "No rows returned or compared"
        if len(actual) != len(expected):
            return False, f"Row count mismatch: expected {len(expected)} rows, got {len(actual)} rows"

        def _row_matches(exp_r: Any, act_r: Any) -> bool:
            if isinstance(exp_r, dict) and isinstance(act_r, dict):
                exp_ci = {str(k).upper(): v for k, v in exp_r.items()}
                act_ci = {str(k).upper(): v for k, v in act_r.items()}
                common_keys = set(exp_ci.keys()) & set(act_ci.keys())
                keys_to_check = common_keys if common_keys else None
                if keys_to_check:
                    for k in keys_to_check:
                        ev = exp_ci[k]
                        av = act_ci[k]
                        try:
                            ef = float(ev)
                            af = float(av)
                            delta = abs(ef - af)
                            max_tol = tolerance * max(1.0, abs(ef))
                            if delta > max_tol:
                                return False
                        except (ValueError, TypeError):
                            if str(ev).strip().casefold() != str(av).strip().casefold():
                                return False
                    return True
                else:
                    for ev, av in zip(exp_r.values(), act_r.values()):
                        try:
                            ef = float(ev)
                            af = float(av)
                            delta = abs(ef - af)
                            max_tol = tolerance * max(1.0, abs(ef))
                            if delta > max_tol:
                                return False
                        except (ValueError, TypeError):
                            if str(ev).strip().casefold() != str(av).strip().casefold():
                                return False
                    return True
            else:
                try:
                    ef = float(exp_r)
                    af = float(act_r)
                    delta = abs(ef - af)
                    max_tol = tolerance * max(1.0, abs(ef))
                    return delta <= max_tol
                except (ValueError, TypeError):
                    return str(exp_r).strip().casefold() == str(act_r).strip().casefold()

        # Positional matching first
        positional_all_match = True
        for exp_r, act_r in zip(expected, actual):
            if not _row_matches(exp_r, act_r):
                positional_all_match = False
                break
        if positional_all_match:
            return True, None

        # Order-insensitive bipartite matching
        matched_actual_indices: Set[int] = set()
        for exp_r in expected:
            matched = False
            for j, act_r in enumerate(actual):
                if j not in matched_actual_indices and _row_matches(exp_r, act_r):
                    matched_actual_indices.add(j)
                    matched = True
                    break
            if not matched:
                return False, "Row mismatch: expected row not found in generated results"

        return True, None

    return False, f"Unsupported expected_value type: {type(expected).__name__}"


def generate_truth_cases(
    *,
    project_id: Optional[str] = None,
    domain: Optional[str] = None,
    limit: int = 20,
    replace_drafts: bool = False,
    owner_id: Optional[str] = None,
    view: Optional[str] = None,
) -> TruthCasesResult:
    """Build draft BI-truth Q&A cases with runnable SEMANTIC_VIEW SQL, reachable dimensions, and answer execution."""
    from semabridge.ai.verified_queries_service import list_verified_queries_for_project
    from semabridge.reconciliation.certification_service import get_latest_certifications
    from semabridge.ontology.store import ontology_store
    from semabridge.repository.model_repository import ModelRepository

    state = _load_suite()
    raw_pid = (project_id or "").strip()
    pid = raw_pid
    if raw_pid:
        try:
            from semabridge.api.services.project_shared import resolve_canonical_project_id
            c = resolve_canonical_project_id(raw_pid)
            if c:
                pid = c
        except Exception as ex:
            logger.warning("Failed resolving canonical project id for %s: %s", raw_pid, ex)
    now = datetime.now(timezone.utc).isoformat()

    resolved_view = None
    if pid:
        repo = ModelRepository()
        if view:
            resolved_view = repo.validate_project_view(pid, view)
        else:
            deployments = repo.list_project_deployments(pid, include_unmanaged=True)
            latest = next((d for d in deployments if d.get("is_latest")), None)
            resolved_view = latest["semantic_view"] if latest else None

    if replace_drafts and pid:
        try:
            from semabridge.repository.orm.session_factory import db_manager
            from semabridge.repository.orm.ai_models import BITruthCaseRow
            with db_manager.get_session() as s:
                draft_rows = s.query(BITruthCaseRow).filter(BITruthCaseRow.status == "draft").all()
                to_del = [r.id for r in draft_rows if _matches_pid(r.project_id, pid)]
                if to_del:
                    s.query(BITruthCaseRow).filter(BITruthCaseRow.id.in_(to_del)).delete(synchronize_session=False)
        except Exception as ex:
            logger.warning("Failed clearing drafts from DB: %s", ex)
        state[_CASES_KEY] = [
            c for c in state.get(_CASES_KEY) or []
            if not (_matches_pid(c.get("project_id"), pid) and str(c.get("status") or "").lower() == "draft")
        ]

    # Deduplication map: (metric_ident, sorted_dim_tuple, question_kind) -> status
    existing_keys_by_status: Dict[Tuple[str, Tuple[str, ...], str], str] = {}
    for c in state.get(_CASES_KEY) or []:
        if _matches_pid(c.get("project_id"), pid):
            m_key = str(c.get("metric_name") or "").strip().casefold()
            c_dims = c.get("dimensions") or []
            if isinstance(c_dims, str):
                try:
                    c_dims = json.loads(c_dims)
                except Exception:
                    c_dims = [c_dims]
            d_key = tuple(sorted(str(d).strip().casefold() for d in c_dims))
            q_k = str(c.get("question_kind") or "total").strip().casefold()
            existing_keys_by_status[(m_key, d_key, q_k)] = str(c.get("status") or "").lower()

    certs = get_latest_certifications(project_id=project_id, domain=domain)
    vqs = list_verified_queries_for_project(project_id=project_id) if project_id else []

    created: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    current_run_keys: Set[Tuple[str, Tuple[str, ...], str]] = set()

    for c in certs:
        if str(c.get("status") or "").upper() != "CERTIFIED":
            continue
        mname = c.get("metric_name") or "metric"
        dims = c.get("dimensions") or []
        cand_key = (mname.casefold(), tuple(sorted(str(d).casefold() for d in dims)), "total" if not dims else "slice")
        if existing_keys_by_status.get(cand_key) in ("approved", "rejected") or cand_key in current_run_keys:
            continue
        current_run_keys.add(cand_key)
        existing_keys_by_status[cand_key] = "draft"
        q = f"What is {mname}" + (f" by {', '.join(dims)}" if dims else "") + "?"
        created.append({
            "id": f"truth-{uuid.uuid4().hex[:8]}",
            "project_id": project_id or c.get("project_id"),
            "domain": domain or c.get("domain"),
            "metric_name": mname,
            "question": q,
            "expected_sql": c.get("sql_query") or "",
            "expected_dax": "",
            "expected_row_count": c.get("sql_row_count") or 0,
            "status": "draft",
            "created_at": now,
            "dimensions": list(dims),
            "question_kind": "total" if not dims else "slice",
        })

    for v in vqs:
        q = (v.get("question") or "").strip()
        mname = v.get("metric_name") or ""
        cand_key = (mname.casefold(), (), "total")
        if not q or existing_keys_by_status.get(cand_key) in ("approved", "rejected") or cand_key in current_run_keys:
            continue
        current_run_keys.add(cand_key)
        existing_keys_by_status[cand_key] = "draft"
        created.append({
            "id": f"truth-{uuid.uuid4().hex[:8]}",
            "project_id": project_id or v.get("project_id"),
            "domain": domain or v.get("domain"),
            "metric_name": mname,
            "question": q,
            "expected_sql": v.get("sql") or "",
            "expected_dax": "",
            "expected_row_count": 0,
            "status": "draft",
            "created_at": now,
            "dimensions": [],
            "question_kind": "total",
        })

    target_domains = []
    if pid:
        target_domains.extend(
            d for d in ontology_store.domains_for(pid)
            if not domain or d.domain.casefold() == domain.casefold() or (d.id or "").casefold() == domain.casefold()
        )
        if not target_domains and raw_pid != pid:
            target_domains.extend(
                d for d in ontology_store.domains_for(raw_pid)
                if not domain or d.domain.casefold() == domain.casefold() or (d.id or "").casefold() == domain.casefold()
            )
    elif domain:
        d = ontology_store.get_domain(domain)
        if d:
            target_domains.append(d)
    else:
        target_domains = list(ontology_store.get_federated_ontology().domains.values())

    name_counts: Dict[str, int] = {}
    for dom in target_domains:
        for nm in {(m.name or m.logical_name or "").casefold() for m in getattr(dom, "metrics", []) or []}:
            name_counts[nm] = name_counts.get(nm, 0) + 1

    for dom in target_domains:
        if len(created) >= limit:
            break
        d_name = dom.domain
        view_name = resolved_view or getattr(dom, "semantic_view_name", None)
        if not view_name and pid:
            try:
                dep_info = ModelRepository().get_project_deployment_info(pid)
                if dep_info:
                    view_name = dep_info.get("semantic_view") or dep_info.get("view_name")
            except Exception as ex:
                logger.debug("Failed getting project deployment info for %s: %s", pid, ex)

        if not view_name:
            skipped.append({"metric_name": f"Domain:{d_name}", "reason": "No deployed semantic view found for domain"})
            continue

        active_rels = [
            r for r in getattr(dom, "relationships", []) or []
            if getattr(r, "is_active", True) is not False
        ]
        rel_key_cols: Set[str] = set()
        for r in active_rels:
            fa = getattr(r, "from_attribute", None)
            ta = getattr(r, "to_attribute", None)
            fc = getattr(r, "from_concept", None)
            tc = getattr(r, "to_concept", None)
            if fa:
                rel_key_cols.add(f"{fc}.{fa}".casefold() if fc else fa.casefold())
                rel_key_cols.add(fa.casefold())
            if ta:
                rel_key_cols.add(f"{tc}.{ta}".casefold() if tc else ta.casefold())
                rel_key_cols.add(ta.casefold())

        def _metric_importance(m_obj: Any) -> tuple:
            is_bi = bool(getattr(m_obj, "dax_expression", None) or getattr(m_obj, "bi_description", None) or getattr(m_obj, "maps_to", None))
            name = (getattr(m_obj, "name", None) or getattr(m_obj, "logical_name", None) or "").casefold()
            label = (getattr(m_obj, "label", None) or "").casefold()
            return (0 if is_bi else 1, label or name)

        all_dom_metrics = sorted(getattr(dom, "metrics", []) or [], key=_metric_importance)

        for m in all_dom_metrics:
            if len(created) >= limit:
                break
            m_name = m.name or m.logical_name
            if not m_name:
                continue
            m_sql = m.sql_expression or ""
            is_unans, unans_reason = _is_unanswerable_metric(m, m_sql, getattr(m, "deploy_reason", None))
            if is_unans:
                skipped.append({"metric_name": m_name, "reason": unans_reason})
                continue

            metric_ident = m.logical_name or m.name
            m_display = m.label or (m_name.replace("_", " ").title() if "_" in m_name else m_name)
            in_dom = f" in {d_name}" if name_counts.get(m_name.casefold(), 0) > 1 else ""
            is_ratio = _is_ratio_or_pct_metric(m_name, m.label, m_sql, m.aggregation_type)
            is_window, req_dims = _is_window_metric(m_sql, m.description, m_name)

            if is_window and not req_dims:
                skipped.append({"metric_name": m_name, "reason": "Window metric missing required dimension from PARTITION BY EXCLUDING"})
                continue

            start_concept = m.concept
            reachable_concepts = set()
            from semabridge.ai.nl_query_engine import NLQueryEngine
            if start_concept:
                reachable_concepts.add(start_concept)
                for concept in getattr(dom, "concepts", []) or []:
                    c_name = concept.name
                    if c_name != start_concept and NLQueryEngine._reaches_via_many_to_one(start_concept, c_name, active_rels):
                        reachable_concepts.add(c_name)
            else:
                reachable_concepts = {c.name for c in getattr(dom, "concepts", []) or []}

            business_dims: List[str] = []
            date_dims: List[str] = []
            other_dims: List[str] = []

            for concept in getattr(dom, "concepts", []) or []:
                if concept.name not in reachable_concepts:
                    continue
                c_name = concept.name
                for attr in getattr(concept, "attributes", []) or []:
                    a_name = attr.name
                    if (
                        getattr(attr, "is_key", False)
                        or getattr(attr, "is_primary_key", False)
                        or (a_name.casefold() in rel_key_cols)
                        or (f"{c_name}.{a_name}".casefold() in rel_key_cols)
                    ):
                        continue
                    tbl = getattr(concept, "logical_name", None) or NLQueryEngine._to_logical_id(concept.name)
                    col = getattr(attr, "logical_name", None) or NLQueryEngine._to_dimension_name(attr.name)
                    dim_ref = f"{tbl}.{col}"
                    dtype = str(getattr(attr, "data_type", "")).lower()
                    is_date = getattr(attr, "is_date", False) or getattr(attr, "is_temporal", False) or dtype in ("date", "datetime", "timestamp")
                    if is_date:
                        date_dims.append(dim_ref)
                    else:
                        is_num = bool(re.search(r"int|dec|float|num|double", dtype, re.I))
                        if not is_num:
                            business_dims.append(dim_ref)
                        else:
                            other_dims.append(dim_ref)

            candidate_dims = business_dims + other_dims
            metric_candidates: List[Dict[str, Any]] = []

            if not is_window:
                q_total = f"What is the overall {m_display}{in_dom}?" if is_ratio else f"What is the total {m_display}{in_dom}?"
                metric_candidates.append({
                    "question": q_total,
                    "expected_sql": _format_expected_sql(view_name, metric_ident),
                    "expected_dax": _format_expected_dax(m_name),
                    "expected_row_count": 1,
                    "dimensions": [],
                    "question_kind": "total",
                })

            if is_window:
                dim_disp = ", ".join(d.split(".")[-1].replace("_", " ").title() if "_" in d else d for d in req_dims)
                q_slice = f"What is {m_display} by {dim_disp}{in_dom}?"
                metric_candidates.append({
                    "question": q_slice,
                    "expected_sql": _format_expected_sql(view_name, metric_ident, req_dims),
                    "expected_dax": _format_expected_dax(m_name, req_dims),
                    "expected_row_count": 0,
                    "dimensions": list(req_dims),
                    "question_kind": "window",
                })
            else:
                if candidate_dims:
                    dim1 = candidate_dims[0]
                    dim1_disp = dim1.split(".")[-1].replace("_", " ").title() if "_" in dim1 else dim1
                    q1 = f"What is {m_display} by {dim1_disp}{in_dom}?"
                    metric_candidates.append({
                        "question": q1,
                        "expected_sql": _format_expected_sql(view_name, metric_ident, dim1),
                        "expected_dax": _format_expected_dax(m_name, dim1),
                        "expected_row_count": 0,
                        "dimensions": [dim1],
                        "question_kind": "slice",
                    })
                if len(candidate_dims) > 1:
                    dim2 = candidate_dims[1]
                    dim2_disp = dim2.split(".")[-1].replace("_", " ").title() if "_" in dim2 else dim2
                    q2 = f"What is {m_display} by {dim2_disp}{in_dom}?"
                    metric_candidates.append({
                        "question": q2,
                        "expected_sql": _format_expected_sql(view_name, metric_ident, dim2),
                        "expected_dax": _format_expected_dax(m_name, dim2),
                        "expected_row_count": 0,
                        "dimensions": [dim2],
                        "question_kind": "slice",
                    })
                if date_dims:
                    d_dim = date_dims[0]
                    d_disp = d_dim.split(".")[-1].replace("_", " ").title() if "_" in d_dim else d_dim
                    q_date = f"What is {m_display} by {d_disp}{in_dom}?"
                    metric_candidates.append({
                        "question": q_date,
                        "expected_sql": _format_expected_sql(view_name, metric_ident, d_dim),
                        "expected_dax": _format_expected_dax(m_name, d_dim),
                        "expected_row_count": 0,
                        "dimensions": [d_dim],
                        "question_kind": "slice",
                    })
                if not is_ratio and candidate_dims:
                    top_dim = candidate_dims[0]
                    top_disp = top_dim.split(".")[-1].replace("_", " ").title() if "_" in top_dim else top_dim
                    q_top = f"What are the top 5 {top_disp} by {m_display}{in_dom}?"
                    metric_candidates.append({
                        "question": q_top,
                        "expected_sql": _format_expected_sql(view_name, metric_ident, top_dim, is_top_n=True, n=5),
                        "expected_dax": "",
                        "expected_row_count": 5,
                        "dimensions": [top_dim],
                        "question_kind": "top_n",
                    })

            for cand in metric_candidates:
                if len(created) >= limit:
                    break
                cand_dims = cand.get("dimensions") or []
                cand_kind = cand.get("question_kind") or "total"
                cand_key = (metric_ident.casefold(), tuple(sorted(str(d).casefold() for d in cand_dims)), cand_kind.casefold())
                if existing_keys_by_status.get(cand_key) in ("approved", "rejected") or cand_key in current_run_keys:
                    continue
                current_run_keys.add(cand_key)
                existing_keys_by_status[cand_key] = "draft"
                created.append({
                    "id": f"truth-{uuid.uuid4().hex[:8]}",
                    "project_id": project_id or getattr(dom, "source_project_id", None) or pid,
                    "domain": d_name,
                    "metric_name": m_name,
                    "question": cand["question"],
                    "expected_sql": cand["expected_sql"],
                    "expected_dax": cand["expected_dax"],
                    "expected_row_count": cand["expected_row_count"],
                    "status": "draft",
                    "created_at": now,
                    "dimensions": list(cand_dims),
                    "question_kind": cand_kind,
                })

    if not created and not target_domains and pid:
        from semabridge.api.routers.reconciliation_router import get_project_reconciliation_metrics
        try:
            recon_res = get_project_reconciliation_metrics(pid)
            recon_metrics = recon_res.get("metrics") or []
        except Exception as exc:
            logger.warning("Failed getting project reconciliation metrics for %s: %s", pid, exc)
            recon_metrics = []

        for m in recon_metrics:
            if len(created) >= limit:
                break
            m_name = m.get("name") or m.get("logical_name")
            if not m_name:
                continue
            m_sql = m.get("sql_expression") or ""
            is_unans, unans_reason = _is_unanswerable_metric(m, m_sql, m.get("deploy_reason"))
            if is_unans:
                skipped.append({"metric_name": m_name, "reason": unans_reason})
                continue

            target_view = m.get("target_table_or_view")
            if not target_view and pid:
                try:
                    dep_info = ModelRepository().get_project_deployment_info(pid)
                    if dep_info:
                        target_view = dep_info.get("semantic_view") or dep_info.get("view_name")
                except Exception as ex:
                    logger.warning("Failed getting project deployment info for %s: %s", pid, ex)
            if not target_view:
                target_view = m.get("domain")
            if not target_view:
                skipped.append({"metric_name": m_name, "reason": "No deployed semantic view found for project"})
                continue

            metric_ident = m.get("logical_name") or m_name
            m_display = m.get("label") or (m_name.replace("_", " ").title() if "_" in m_name else m_name)
            is_ratio = _is_ratio_or_pct_metric(m_name, m.get("label"), m_sql, m.get("aggregation_type"))
            is_window, req_dims = _is_window_metric(m_sql, m.get("description"), m_name)

            if is_window and not req_dims:
                skipped.append({"metric_name": m_name, "reason": "Window metric missing required dimension from PARTITION BY EXCLUDING"})
                continue

            raw_dims = [d for d in (m.get("dimensions") or []) if d]

            cand_qs: List[Dict[str, Any]] = []
            if not is_window:
                q_total = f"What is the overall {m_display}?" if is_ratio else f"What is the total {m_display}?"
                cand_qs.append({
                    "question": q_total,
                    "expected_sql": _format_expected_sql(target_view, metric_ident),
                    "expected_dax": _format_expected_dax(m_name),
                    "expected_row_count": 1,
                    "dimensions": [],
                    "question_kind": "total",
                })
            if is_window:
                dim_disp = ", ".join(d.split(".")[-1].replace("_", " ").title() if "_" in d else d for d in req_dims)
                cand_qs.append({
                    "question": f"What is {m_display} by {dim_disp}?",
                    "expected_sql": _format_expected_sql(target_view, metric_ident, req_dims),
                    "expected_dax": _format_expected_dax(m_name, req_dims),
                    "expected_row_count": 0,
                    "dimensions": list(req_dims),
                    "question_kind": "window",
                })
            else:
                if raw_dims:
                    d1 = raw_dims[0]
                    d1_disp = d1.split(".")[-1].replace("_", " ").title() if "_" in d1 else d1
                    cand_qs.append({
                        "question": f"What is {m_display} by {d1_disp}?",
                        "expected_sql": _format_expected_sql(target_view, metric_ident, d1),
                        "expected_dax": _format_expected_dax(m_name, d1),
                        "expected_row_count": 0,
                        "dimensions": [d1],
                        "question_kind": "slice",
                    })
                if len(raw_dims) > 1:
                    d2 = raw_dims[1]
                    d2_disp = d2.split(".")[-1].replace("_", " ").title() if "_" in d2 else d2
                    cand_qs.append({
                        "question": f"What is {m_display} by {d2_disp}?",
                        "expected_sql": _format_expected_sql(target_view, metric_ident, d2),
                        "expected_dax": _format_expected_dax(m_name, d2),
                        "expected_row_count": 0,
                        "dimensions": [d2],
                        "question_kind": "slice",
                    })

            for cand in cand_qs:
                if len(created) >= limit:
                    break
                cand_dims = cand.get("dimensions") or []
                cand_kind = cand.get("question_kind") or "total"
                cand_key = (metric_ident.casefold(), tuple(sorted(str(d).casefold() for d in cand_dims)), cand_kind.casefold())
                if existing_keys_by_status.get(cand_key) in ("approved", "rejected") or cand_key in current_run_keys:
                    continue
                current_run_keys.add(cand_key)
                existing_keys_by_status[cand_key] = "draft"
                created.append({
                    "id": f"truth-{uuid.uuid4().hex[:8]}",
                    "project_id": pid,
                    "domain": m.get("domain") or pid,
                    "metric_name": m_name,
                    "question": cand["question"],
                    "expected_sql": cand["expected_sql"],
                    "expected_dax": cand["expected_dax"],
                    "expected_row_count": cand["expected_row_count"],
                    "status": "draft",
                    "created_at": now,
                    "dimensions": list(cand_dims),
                    "question_kind": cand_kind,
                })

    existing_for_pid = [c for c in state.get(_CASES_KEY, []) if _matches_pid(c.get("project_id"), pid)]
    if not created and not existing_for_pid and pid:
        raise ValueError(
            f"Prerequisite missing: project '{pid}' has no certified metrics, verified queries, or deployed semantic models. "
            f"Please sync and deploy the project model first to generate BI-Truth test questions."
        )

    if created:
        created = [_sanitize_cell_value(c) for c in created]
        state[_CASES_KEY] = created + (state.get(_CASES_KEY) or [])
        _save_suite(state)
        _evaluate_cases_in_background(created, owner_id=owner_id)

    return TruthCasesResult(
        items=created,
        added_count=len(created),
        skipped_count=len(skipped),
        skipped=[_sanitize_cell_value(s) for s in skipped],
        view=resolved_view,
    )


def clear_truth_cases(project_id: Optional[str] = None) -> int:
    """Clear truth test cases and past run results for a specific project or all projects."""
    state = _load_suite()
    orig_len = len(state.get(_CASES_KEY) or [])
    pid = (project_id or "").strip()

    try:
        from semabridge.repository.orm.session_factory import db_manager
        from semabridge.repository.orm.ai_models import BITruthCaseRow, BITruthRunRow
        with db_manager.get_session() as s:
            if pid:
                all_cases = s.query(BITruthCaseRow).all()
                to_delete = [c.id for c in all_cases if _matches_pid(c.project_id, pid)]
                if to_delete:
                    s.query(BITruthCaseRow).filter(BITruthCaseRow.id.in_(to_delete)).delete(synchronize_session=False)
                all_runs = s.query(BITruthRunRow).all()
                runs_to_delete = [r.id for r in all_runs if _matches_pid(r.project_id, pid)]
                if runs_to_delete:
                    s.query(BITruthRunRow).filter(BITruthRunRow.id.in_(runs_to_delete)).delete(synchronize_session=False)
            else:
                s.query(BITruthCaseRow).delete()
                s.query(BITruthRunRow).delete()
    except Exception as exc:
        logger.warning("DB delete during clear_truth_cases failed: %s", exc)

    if pid:
        state[_CASES_KEY] = [c for c in state.get(_CASES_KEY) or [] if not _matches_pid(c.get("project_id"), pid)]
        state["runs"] = [r for r in state.get("runs") or [] if not _matches_pid(r.get("project_id"), pid)]
    else:
        state[_CASES_KEY] = []
        state["runs"] = []

    p = _suite_path()
    if p.exists():
        try:
            p.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
        except Exception as ex:
            logger.warning("Writing local suite JSON failed: %s", ex)
    return orig_len - len(state.get(_CASES_KEY) or [])


def add_manual_truth_case(
    *,
    project_id: Optional[str] = None,
    domain: Optional[str] = None,
    question: str,
    metric_name: Optional[str] = None,
    expected_sql: Optional[str] = None,
    expected_dax: Optional[str] = None,
    status: str = "draft",
    owner_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Manually add a single truth question with expected SQL or DAX."""
    q_clean = (question or "").strip()
    if not q_clean:
        raise ValueError("Question cannot be empty.")
    state = _load_suite()
    cid = f"truth-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()
    new_case = {
        "id": cid,
        "project_id": project_id,
        "domain": domain or project_id,
        "metric_name": (metric_name or "").strip() or "Custom",
        "question": q_clean,
        "expected_sql": (expected_sql or "").strip(),
        "expected_dax": (expected_dax or "").strip(),
        "expected_row_count": 0,
        "status": status,
        "created_at": now,
        "source": "manual",
    }
    state[_CASES_KEY] = [new_case] + (state.get(_CASES_KEY) or [])
    _save_suite(state)
    return new_case


def import_truth_cases_from_content(
    *,
    content: str,
    filename: str = "upload.csv",
    project_id: Optional[str] = None,
    domain: Optional[str] = None,
    status: str = "draft",
    owner_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Parse uploaded document content (CSV, TSV, JSON, or text Q&A) into truth test cases."""
    import csv
    import io
    import json
    import re

    raw = content.strip()
    if not raw:
        return []

    parsed_pairs: List[Dict[str, str]] = []

    # 1. Try JSON
    if raw.startswith(("[", "{")):
        try:
            data = json.loads(raw)
            items = data if isinstance(data, list) else data.get(_CASES_KEY) or data.get("questions") or data.get("tests") or []
            for item in items:
                if isinstance(item, dict):
                    q = item.get("question") or item.get("prompt") or item.get("q") or ""
                    sql = item.get("expected_sql") or item.get("sql") or item.get("answer") or item.get("expected") or ""
                    m = item.get("metric_name") or item.get("metric") or item.get("measure") or ""
                    dax = item.get("expected_dax") or item.get("dax") or ""
                    if q.strip():
                        parsed_pairs.append({
                            "question": q.strip(),
                            "expected_sql": str(sql).strip(),
                            "expected_dax": str(dax).strip(),
                            "metric_name": str(m).strip(),
                        })
        except Exception as ex:
            logger.warning("Parsing golden SQL JSON failed: %s", ex)

    # 2. Try CSV / TSV if JSON didn't yield items
    if not parsed_pairs and ("," in raw or "\t" in raw or "\n" in raw):
        delimiter = "\t" if "\t" in raw and "," not in raw else ","
        try:
            reader = csv.reader(io.StringIO(raw), delimiter=delimiter)
            rows = [r for r in reader if any(cell.strip() for cell in r)]
            if rows:
                headers = [c.strip().lower().replace(" ", "_") for c in rows[0]]
                q_idx = -1
                sql_idx = -1
                metric_idx = -1
                dax_idx = -1

                for idx, h in enumerate(headers):
                    if h in ("question", "prompt", "q", "user_question", "query_text", "business_question"):
                        q_idx = idx
                    elif h in ("expected_sql", "sql", "expected_query", "query", "expected", "sql_query", "expected_answer", "answer"):
                        sql_idx = idx
                    elif h in ("metric", "metric_name", "measure", "domain", "kpi"):
                        metric_idx = idx
                    elif h in ("dax", "expected_dax", "dax_expression"):
                        dax_idx = idx

                if q_idx >= 0:
                    for row in rows[1:]:
                        if q_idx < len(row) and row[q_idx].strip():
                            q = row[q_idx].strip()
                            sql = row[sql_idx].strip() if sql_idx >= 0 and sql_idx < len(row) else ""
                            m = row[metric_idx].strip() if metric_idx >= 0 and metric_idx < len(row) else ""
                            dax = row[dax_idx].strip() if dax_idx >= 0 and dax_idx < len(row) else ""
                            parsed_pairs.append({
                                "question": q,
                                "expected_sql": sql,
                                "expected_dax": dax,
                                "metric_name": m,
                            })
                elif len(rows[0]) >= 2:
                    # Positional fallback: Col 0 = question, Col 1 = SQL / Answer
                    start = 1 if any(w in headers[0] for w in ("q", "question", "prompt")) else 0
                    for row in rows[start:]:
                        if len(row) >= 1 and row[0].strip():
                            q = row[0].strip()
                            sql = row[1].strip() if len(row) >= 2 else ""
                            parsed_pairs.append({
                                "question": q,
                                "expected_sql": sql,
                                "expected_dax": "",
                                "metric_name": "",
                            })
        except Exception as ex:
            logger.warning("Parsing golden SQL CSV failed: %s", ex)

    # 3. Try Q: / A: Text or Markdown patterns
    if not parsed_pairs:
        pattern = re.compile(r"(?:Q|Question)\s*:\s*(.+?)\s*(?:(?:A|Answer|SQL|Expected SQL)\s*:\s*(.+?))?(?=(?:(?:Q|Question)\s*:)|$)", re.IGNORECASE | re.DOTALL)
        for match in pattern.finditer(raw):
            q, ans = match.groups()
            if q and q.strip():
                parsed_pairs.append({
                    "question": q.strip(),
                    "expected_sql": (ans or "").strip(),
                    "expected_dax": "",
                    "metric_name": "",
                })

    if not parsed_pairs:
        # Final fallback: treat each non-empty line as a question
        for line in raw.splitlines():
            clean = line.strip().lstrip("0123456789.-*•) ")
            if len(clean) > 5 and ("?" in clean or any(clean.lower().startswith(w) for w in ("what", "how", "show", "get", "total", "count", "list"))):
                parsed_pairs.append({
                    "question": clean,
                    "expected_sql": "",
                    "expected_dax": "",
                    "metric_name": "",
                })

    if not parsed_pairs:
        return []

    state = _load_suite()
    now = datetime.now(timezone.utc).isoformat()
    created: List[Dict[str, Any]] = []

    for item in parsed_pairs:
        cid = f"truth-{uuid.uuid4().hex[:8]}"
        created.append({
            "id": cid,
            "project_id": project_id,
            "domain": domain or project_id,
            "metric_name": item.get("metric_name") or "Imported",
            "question": item["question"],
            "expected_sql": item.get("expected_sql") or "",
            "expected_dax": item.get("expected_dax") or "",
            "expected_row_count": 0,
            "status": status,
            "created_at": now,
            "source": f"doc:{filename}",
        })

    existing_qs = {(c.get("project_id"), c.get("question")) for c in state.get(_CASES_KEY) or []}
    added = [c for c in created if (c.get("project_id"), c.get("question")) not in existing_qs]
    state[_CASES_KEY] = added + (state.get(_CASES_KEY) or [])
    _save_suite(state)
    return added


def generate_golden_questions_from_pbix_dir(
    *,
    pbix_dir: Optional[str] = None,
    limit: int = 50,
    project_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Scan and generate golden Q&A cases routed through generate_truth_cases."""
    if not project_id:
        return []
    res = generate_truth_cases(project_id=project_id, limit=limit)
    return list(res)


def _case_scope(scope: Any, case: Dict[str, Any]) -> Any:
    """Per-case copy of a project scope pointed at the case's own semantic view.

    Multi-model projects have one view per model: a case generated from a
    domain uses that domain's view; otherwise the question is routed to the
    view owning its metric (no view when ambiguous).
    """
    import dataclasses

    from semabridge.ai.project_connection import route_scope_to_view

    views = getattr(scope, "domain_views", None) or {}
    if not dataclasses.is_dataclass(scope) or getattr(scope, "explicit_view", False) or len(set(views.values())) < 2:
        return scope
    cs = dataclasses.replace(scope, candidate_views=[], routing_matches={})
    dom = case.get("domain")
    if dom and dom in views:
        from semabridge.ai.project_connection import _restrict_to
        from semabridge.ontology.store import ontology_store

        cs.semantic_view = views[dom]
        cs.answered_by_domain = dom
        _restrict_to(cs, list(ontology_store.domains_for(cs.project_id)), [dom])
        return cs
    return route_scope_to_view(case.get("question") or "", cs)


def _matches_pid(case_pid: Optional[str], target_pid: Optional[str]) -> bool:
    """Check if case project_id matches target project_id flexibly."""
    if not target_pid:
        return True
    if not case_pid:
        return False
    c_clean = str(case_pid).removeprefix("proj-").strip().casefold()
    t_clean = str(target_pid).removeprefix("proj-").strip().casefold()
    return c_clean == t_clean


def list_truth_cases(project_id: Optional[str] = None) -> List[Dict[str, Any]]:
    cases = _load_suite()[_CASES_KEY]
    if project_id:
        cases = [c for c in cases if _matches_pid(c.get("project_id"), project_id)]
    return cases


def _is_eligible_for_certification(c: Dict[str, Any]) -> bool:
    """A case becomes 'certified' only when it matches a Power BI reference value AND a reviewer approves it."""
    has_pbi_ref = (
        c.get("power_bi_reference_value") is not None
        or c.get("expected_dax_value") is not None
        or c.get("certified_parity") is True
    )
    exp_val = c.get("expected_value")
    if not has_pbi_ref:
        return False
    if c.get("certified_parity") is True:
        return True
    if exp_val is not None:
        ref_val = (
            c.get("power_bi_reference_value")
            if c.get("power_bi_reference_value") is not None
            else c.get("expected_dax_value")
        )
        try:
            return math.isclose(float(ref_val), float(exp_val), rel_tol=1e-4, abs_tol=1e-4)
        except (ValueError, TypeError):
            return str(ref_val).strip() == str(exp_val).strip()
    return False


def _promote_truth_case_to_verified_query(case: Dict[str, Any]) -> None:
    """Auto-promote certified truth case to verified query via save_verified_query."""
    try:
        from semabridge.ai.verified_queries_service import (
            record_query_execution,
            save_verified_query,
        )
        pid = str(case.get("project_id") or "").strip()
        q = str(case.get("question") or "").strip()
        sql = str(case.get("expected_sql") or "").strip()
        v_name = case.get("view") or case.get("semantic_view")
        if pid and q and sql:
            record_query_execution(
                sql=sql,
                project_id=pid,
                success=True,
                row_count=case.get("expected_row_count") or 1,
            )
            save_verified_query(
                project_id=pid,
                question=q,
                sql=sql,
                confirmed=True,
                user_id="bi_truth_certifier",
                view_name=v_name,
                domain=case.get("domain"),
            )
    except Exception as exc:
        logger.warning("Could not auto-promote certified truth case %s to verified query: %s", case.get("id"), exc)


def build_engine_instructions_for_view(
    view_name: Optional[str] = None,
    metrics: Optional[List[Any]] = None,
    concepts: Optional[List[Any]] = None,
    data_max_date: Optional[str] = None,
) -> str:
    """Build engine instructions per view from model metadata (governed path, no today's date, period metrics)."""
    instructions: List[str] = [
        "Always query using SEMANTIC_VIEW with governed METRICS and DIMENSIONS; never query base/physical or enriched tables directly.",
        "Never use invented business rules or status checks (e.g. checking if a date is NULL) - use the governed metrics provided.",
        "Never filter or anchor on the current date (CURRENT_DATE, GETDATE, NOW) because historical model data does not extend to the current date.",
    ]
    if data_max_date:
        instructions.append(f"Model data ends at {data_max_date}; for period comparisons, evaluate relative to the latest data period.")
    else:
        instructions.append("Model data ends at historical anchor points; for period comparisons, evaluate relative to the latest data period.")

    m_list = metrics or []
    period_metrics: List[str] = []
    for m in m_list:
        if (
            getattr(m, "is_period_metric", False)
            or getattr(m, "time_intelligence", False)
            or getattr(m, "time_shift_tags", None)
            or getattr(m, "comparison_type", None)
        ):
            m_name = getattr(m, "name", None) or getattr(m, "unique_name", None) or ""
            if m_name:
                period_metrics.append(m_name)

    if period_metrics:
        instructions.append(f"Use governed period metrics ({', '.join(period_metrics[:5])}) for period and historical comparisons.")

    snapshot_dims: List[str] = []
    for m in m_list:
        semi_dim = getattr(m, "non_additive_dimension", None)
        if semi_dim:
            snapshot_dims.append(f"{getattr(m, 'name', '')} by {semi_dim}")
    if snapshot_dims:
        instructions.append(f"Semi-additive metrics ({', '.join(snapshot_dims[:3])}) must not be summed over time; evaluate them at the end of the period.")

    return "\n".join(f"- {inst}" for inst in instructions)


def set_truth_case_status(case_id: str, status: str, question: Optional[str] = None) -> Optional[Dict[str, Any]]:
    state = _load_suite()
    for c in state[_CASES_KEY]:
        if c["id"] == case_id:
            if status == "certified" and not _is_eligible_for_certification(c):
                raise ValueError(
                    f"Cannot certify case '{case_id}': certification requires matching a Power BI reference value and reviewer approval."
                )
            c["status"] = status
            if question is not None and question.strip():
                c["question"] = question.strip()
            c["updated_at"] = datetime.now(timezone.utc).isoformat()
            if status == "certified":
                _promote_truth_case_to_verified_query(c)
            _save_suite(state)
            return c
    return None


def set_batch_truth_case_status(
    *,
    status: str,
    project_id: Optional[str] = None,
    case_ids: Optional[List[str]] = None,
) -> int:
    """Atomically update status for multiple truth cases in one write."""
    state = _load_suite()
    count = 0
    now = datetime.now(timezone.utc).isoformat()
    cid_set = set(case_ids) if case_ids else None

    for c in state.get(_CASES_KEY) or []:
        if project_id and not _matches_pid(c.get("project_id"), project_id):
            continue
        if cid_set is not None and c.get("id") not in cid_set:
            continue
        if status == "certified" and not _is_eligible_for_certification(c):
            raise ValueError(
                f"Cannot certify case '{c.get('id')}': certification requires matching a Power BI reference value and reviewer approval."
            )
        c["status"] = status
        c["updated_at"] = now
        if status == "certified":
            _promote_truth_case_to_verified_query(c)
        count += 1

    if count > 0:
        _save_suite(state)
    return count


def run_truth_suite(
    *,
    project_id: Optional[str] = None,
    owner_id: Optional[str] = None,
    max_cases: int = 50,
    view: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute approved BI-truth questions against Cortex / NL engine and record accuracy."""
    from concurrent.futures import ThreadPoolExecutor
    from semabridge.ai.nl_query_engine import NLQueryEngine
    from semabridge.ai.project_connection import resolve_project_scope, resolve_project_warehouse_config
    from semabridge.ontology.store import ontology_store

    resolved_view = None
    if project_id:
        from semabridge.repository.model_repository import ModelRepository
        repo = ModelRepository()
        if view:
            resolved_view = repo.validate_project_view(project_id, view)
        else:
            deployments = repo.list_project_deployments(project_id, include_unmanaged=True)
            latest = next((d for d in deployments if d.get("is_latest")), None)
            resolved_view = latest["semantic_view"] if latest else None

    state = _load_suite()
    approved = [
        c for c in state.get(_CASES_KEY) or []
        if c.get("status") == "approved" and _matches_pid(c.get("project_id"), project_id)
    ]
    if max_cases and len(approved) > max_cases:
        approved = approved[:max_cases]

    engine = NLQueryEngine(ontology_store.search_index, ontology_store.get_federated_ontology())

    # Pre-resolve project scopes and configs once to avoid repetitive DB lookups
    scopes_cache: Dict[str, Any] = {}
    sf_cache: Dict[str, Any] = {}

    def _get_scope(pid: Optional[str]):
        p = pid or ""
        if p not in scopes_cache:
            scopes_cache[p] = resolve_project_scope(
                p,
                explicit_view=resolved_view if (not pid or pid == project_id) else None,
            )
        return scopes_cache[p]

    def _get_sf(pid: Optional[str]):
        p = pid or ""
        if p not in sf_cache:
            try:
                sf_cache[p] = resolve_project_warehouse_config(p, "SNOWFLAKE", owner_id=owner_id)
            except Exception:
                sf_cache[p] = None
        return sf_cache[p]

    # Open ONE Snowflake connection for the run with 15s timeout
    target_pid = project_id or (approved[0].get("project_id") if approved else None)
    sf_conn = None
    sf_cur = None
    if target_pid:
        try:
            sf_cfg = _get_sf(target_pid)
            if sf_cfg:
                from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs
                kw = get_snowflake_connect_kwargs(sf_cfg)
                if kw.get("account") and kw.get("user"):
                    import snowflake.connector
                    sf_conn = snowflake.connector.connect(**kw)
                    sf_cur = sf_conn.cursor()
                    try:
                        sf_cur.execute("ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = 15")
                    except Exception as ex:
                        logger.warning("Failed setting statement timeout: %s", ex)
        except Exception as conn_err:
            logger.warning("Failed opening single Snowflake connection for truth run: %s", conn_err)

    results: List[Dict[str, Any]] = []

    try:
        for c in approved:
            pid = c.get("project_id") or project_id
            scope = _case_scope(_get_scope(pid), c)
            sf = _get_sf(pid)
            generated_sql = ""
            mode = "local"
            err = None

            try:
                # 1. Governed query path first: questions matching governed metrics generate SELECT ... FROM SEMANTIC_VIEW(...)
                plan = engine.resolve_query(
                    c["question"],
                    dialect="SNOWFLAKE",
                    semantic_view=getattr(scope, "semantic_view", None),
                    allowed_domains=getattr(scope, "allowed_domains", []),
                )
                if plan.answerable and plan.generated_sql:
                    generated_sql = (plan.generated_sql or "").strip()
                    mode = "semantic_view"
                elif not plan.answerable and not getattr(plan, "plan_checks_passed", True):
                    # Plan check failed (fan-out, window metric missing partition dim, semi-additive, NULL metric)
                    err = plan.explanation
                    mode = "semantic_view_refused"
                else:
                    # 2. Free-form Cortex SQL only as a fallback, labelled "free-form SQL" and never certified
                    if sf is not None and getattr(sf, "private_key", None) and getattr(scope, "semantic_view", None):
                        try:
                            from semabridge.ai.cortex_analyst_client import call_cortex_analyst
                            resp = call_cortex_analyst(prompt=c["question"], semantic_view=scope.semantic_view, config=sf)
                            generated_sql = (resp.get("generated_sql") or "").strip()
                            mode = "free-form SQL"
                        except Exception:
                            generated_sql = (plan.generated_sql or "").strip()
                            mode = "local_fallback"
                    else:
                        generated_sql = (plan.generated_sql or "").strip()
                        mode = "local_fallback"
            except Exception as exc:
                err = str(exc)

            if generated_sql:
                from semabridge.connectors.sql_guard import validate_read_only_sql
                is_safe, safety_err = validate_read_only_sql(generated_sql)
                if not is_safe:
                    err = f"SQL safety check failed: {safety_err}"
                    generated_sql = ""

            actual_val = None
            if generated_sql and sf_cur is not None:
                try:
                    sf_cur.execute(generated_sql)
                    cols = [d[0].upper() for d in sf_cur.description] if sf_cur.description else []
                    rows = sf_cur.fetchall()
                    actual_val = [{cols[i]: _sanitize_cell_value(v) for i, v in enumerate(row)} for row in rows]
                    matches, mismatch_err = _compare_truth_results(actual_val, c.get("expected_value"))
                    ok = matches
                    if not ok and mismatch_err:
                        err = mismatch_err
                    # Result checks: check for breakdown exceeding expected total
                    if ok and actual_val and c.get("expected_value"):
                        from semabridge.api.routers.ai_router import check_query_results
                        exp_v = c.get("expected_value")
                        card_tot = None
                        if isinstance(exp_v, (int, float)):
                            card_tot = float(exp_v)
                        elif isinstance(exp_v, list) and len(exp_v) == 1 and isinstance(exp_v[0], dict):
                            vals = [v for v in exp_v[0].values() if isinstance(v, (int, float))]
                            if vals:
                                card_tot = float(vals[0])
                        row_vals = [[r.get(col) for col in cols] for r in actual_val]
                        res_warns, is_valid = check_query_results(cols, row_vals, card_total=card_tot)
                        if not is_valid:
                            ok = False
                            err = "; ".join(res_warns)
                except Exception as q_err:
                    ok = False
                    err = str(q_err)
            else:
                ok = False
                if not err:
                    err = "Query not executed against warehouse"

            if ok and generated_sql and pid:
                try:
                    from semabridge.ai.verified_queries_service import record_query_execution
                    target_view = getattr(scope, "semantic_view", None) or c.get("view_name") or resolved_view
                    record_query_execution(
                        project_id=pid,
                        sql=generated_sql,
                        success=True,
                        execution_type="bi_truth",
                        view_name=target_view,
                        target="SNOWFLAKE",
                    )
                except Exception as save_err:
                    logger.warning("Recording query execution for case %s skipped: %s", c.get("id"), save_err)

            results.append({
                "case_id": c["id"],
                "question": c["question"],
                "metric_name": c.get("metric_name") or "",
                "expected_sql": c.get("expected_sql") or "",
                "expected_value": c.get("expected_value"),
                "actual_value": actual_val,
                "passed": ok,
                "mode": mode,
                "generated_sql": generated_sql,
                "error": err,
            })
    finally:
        if sf_conn:
            try:
                sf_conn.close()
            except Exception as ex:
                logger.warning("Closing Snowflake connection failed: %s", ex)

    passed = sum(1 for r in results if r["passed"])
    total = len(approved)
    accuracy = round((100.0 * passed / total), 2) if total else 0.0
    run_rec = {
        "id": f"trun-{uuid.uuid4().hex[:8]}",
        "project_id": project_id,
        "view": resolved_view,
        "semantic_view": resolved_view,
        "total": total,
        "passed": passed,
        "accuracy_pct": accuracy,
        "results": results,
        "ran_at": datetime.now(timezone.utc).isoformat(),
    }
    state["runs"] = ([run_rec] + (state.get("runs") or []))[:50]
    _save_suite(state)
    return run_rec


def list_truth_runs(project_id: Optional[str] = None) -> List[Dict[str, Any]]:
    runs = _load_suite()["runs"]
    if project_id:
        runs = [r for r in runs if _matches_pid(r.get("project_id"), project_id)]
    return runs

