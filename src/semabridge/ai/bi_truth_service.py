"""BI-Truth Test Suite: Generate Q&A from certified metrics, approve, run against Cortex/agent, track accuracy.

Configuration Reference & System Defaults (CS-19):
--------------------------------------------------
- Default Tolerance: 0.001 (0.1% allowable numeric variance between actual and expected benchmark values).
  Configurable per case via `tolerance_pct`.
- Accuracy Floor: 10 approved cases. Evaluated suites with fewer than 10 comparable cases report
  verdicts honestly but do not meet the minimum statistical sample floor for governance certification.
- Result Max Age: 24 hours (`DEFAULT_MAX_RESULT_AGE_HOURS = 24`). Deploy gates reject test runs older
  than 24 hours as `STALE`.
- Warehouse Statement Timeout: 30 seconds (`_DEFAULT_STATEMENT_TIMEOUT = 30`), overridable via
  `SEMABRIDGE_DRIFT_STATEMENT_TIMEOUT_SECONDS`.
- Gate Modes:
    - "report" (default): Runs evaluation non-blocking. Reports failures in findings and logs without
      stopping deployment (`blocks_deploy = False`).
    - "block": Enforces strict regression protection. Halts deployment (`blocks_deploy = True`) whenever
      a critical measure breaches tolerance, the latest run is stale (>24h), or critical measures are missing.
"""

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
from semabridge.ai.executors import get_executor

logger = logging.getLogger(__name__)

_CASES_KEY = "cases"


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
    wins = []
    if getattr(r, "windows_json", None):
        try:
            wins = json.loads(r.windows_json)
        except Exception:
            wins = []
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
        "windows": wins if isinstance(wins, list) else [],
        "is_critical": bool(getattr(r, "is_critical", False) or False),
        "tolerance": getattr(r, "tolerance", None),
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
                raw_wins = c.get("windows") or []
                wins_json = json.dumps(raw_wins, default=str) if not isinstance(raw_wins, str) else raw_wins
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
                        windows_json=wins_json,
                        is_critical=bool(c.get("is_critical", False)),
                        tolerance=float(c["tolerance"]) if c.get("tolerance") is not None else None,
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
    platform: Optional[str] = "snowflake",
) -> str:
    """Format runnable query using platform-specific SQL builder."""
    if not view_name or not str(view_name).strip():
        raise ValueError("view_name is required to format expected query SQL")
    from semabridge.ai.sql_builders import get_builder
    builder = get_builder(platform or "snowflake")
    if builder is None:
        raise ValueError(f"Unsupported or unknown platform '{platform}' for truth SQL generation")
    if is_top_n:
        return builder.build_top_n(view_name, metric_ident, dim_ident or [], n=n)
    elif dim_ident:
        return builder.build_sliced(view_name, metric_ident, dim_ident)
    return builder.build_total(view_name, metric_ident)


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


def evaluate_sql_against_warehouse(
    project_id: Optional[str],
    sql: str,
    owner_id: Optional[str] = None,
) -> Tuple[Optional[Any], Optional[str]]:
    """Execute a query once against the target warehouse to fetch draft expected value with full result rows."""
    if not project_id or not sql:
        return None, None
    try:
        from semabridge.connectors.sql_guard import validate_read_only_sql
        try:
            if not validate_read_only_sql(sql):
                return None, "SQL safety check failed"
        except Exception as safety_err:
            return None, f"SQL safety check failed: {safety_err}"

        from semabridge.ai.executors.base import get_executor
        executor = get_executor(project_id, owner_id=owner_id)
        if executor is None:
            return None, f"No warehouse executor available for project '{project_id}'"

        with executor:
            res = executor.execute(sql)
            if res.error:
                return None, res.error
            return res.rows, None
    except Exception as exc:
        return None, str(exc)


def evaluate_cases_in_background(cases: List[Dict[str, Any]], owner_id: Optional[str] = None) -> None:
    """Run one executor per project batch with query timeout in the background."""
    if not cases:
        return
    import threading

    def _worker():
        try:
            from semabridge.connectors.sql_guard import validate_read_only_sql
            from semabridge.ai.executors.base import get_executor

            by_pid: Dict[Optional[str], List[Dict[str, Any]]] = {}
            for c in cases:
                pid = c.get("project_id")
                by_pid.setdefault(pid, []).append(c)

            now = datetime.now(timezone.utc).isoformat()
            state = _load_suite()
            cases_map = {c["id"]: c for c in state.get(_CASES_KEY) or []}
            updated = False

            for pid, pcases in by_pid.items():
                if not pid:
                    continue
                executor = get_executor(pid, owner_id=owner_id)
                if not executor:
                    continue

                try:
                    with executor:
                        for c in pcases:
                            cid = c.get("id")
                            sql = c.get("expected_sql")
                            if not sql or cid not in cases_map:
                                continue

                            try:
                                is_safe = validate_read_only_sql(sql)
                            except Exception:
                                is_safe = False
                            if not is_safe:
                                cases_map[cid]["evaluation_note"] = "SQL safety check failed: query is not read-only"
                                cases_map[cid]["evaluation_status"] = "evaluation_failed"
                                updated = True
                                continue

                            res = executor.execute(sql)
                            if res.error:
                                cases_map[cid]["evaluation_note"] = res.error
                                cases_map[cid]["evaluation_status"] = "evaluation_failed"
                                updated = True
                            else:
                                cases_map[cid]["expected_value"] = res.rows
                                cases_map[cid]["expected_evaluated_at"] = now
                                cases_map[cid]["evaluation_status"] = "draft_evaluated"
                                cases_map[cid]["evaluation_note"] = None
                                updated = True
                except Exception as conn_err:
                    logger.warning("Warehouse connection error in background evaluation for project %s: %s", pid, conn_err)

            if updated:
                state[_CASES_KEY] = list(cases_map.values())
                _save_suite(state)
        except Exception as exc:
            logger.warning("Background warehouse evaluation failed: %s", exc)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()


_evaluate_cases_in_background = evaluate_cases_in_background


def _compare_truth_results(
    actual: Any,
    expected: Any,
    tolerance: Optional[float] = None,
    expected_row_count: Optional[int] = None,
) -> Tuple[bool, Optional[str]]:
    """Compare generated query result against expected value within tolerance."""
    from semabridge.ai.bi_truth_comparator import compare_truth_results
    ok, err, _ = compare_truth_results(
        actual,
        expected,
        tolerance=tolerance,
        expected_row_count=expected_row_count,
    )
    return ok, err


def accept_truth_case_benchmark(
    project_id: Optional[str],
    case_id: str,
    new_expected_value: Any,
    user_id: Optional[str] = None,
    note: Optional[str] = None,
    new_row_count: Optional[int] = None,
) -> Dict[str, Any]:
    """Accept a new benchmark value for a truth case whose warehouse result has changed."""
    state = _load_suite()
    cases = state.get(_CASES_KEY, [])
    found = None
    now_utc = datetime.now(timezone.utc).isoformat()
    user_label = user_id or "user"
    audit_note = f"Benchmark accepted by {user_label} at {now_utc}: {note or 'warehouse value accepted'}"

    for c in cases:
        if c.get("id") == case_id and (not project_id or _matches_pid(c.get("project_id"), project_id)):
            c["expected_value"] = new_expected_value
            c["expected_evaluated_at"] = now_utc
            c["evaluation_note"] = audit_note
            if new_row_count is not None:
                c["expected_row_count"] = int(new_row_count)
            found = c
            break

    if not found:
        raise KeyError(f"Truth case '{case_id}' not found for project '{project_id}'")

    _save_suite(state)
    return found


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

    from semabridge.ai.project_connection import _project_meta
    from semabridge.governance.drift.adapters.registry import resolve_project_platform
    from semabridge.ai.sql_builders import get_builder
    meta = _project_meta(pid) if pid else {}
    target_platform = resolve_project_platform(meta)
    if target_platform in ("NOT_SET", "UNKNOWN", "", None):
        from semabridge.ai.executors.base import get_executor
        ex = get_executor(pid)
        if ex:
            target_platform = ex.platform
    if target_platform in ("NOT_SET", "UNKNOWN", "", None) or get_builder(target_platform) is None:
        target_platform = "SNOWFLAKE"
    if get_builder(target_platform) is None:
        return TruthCasesResult(
            items=[],
            added_count=0,
            skipped_count=1,
            skipped=[{"metric_name": f"Project:{pid or 'unknown'}", "reason": f"No truth SQL builder for platform '{target_platform}'"}],
        )

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
                    "expected_sql": _format_expected_sql(view_name, metric_ident, platform=target_platform),
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
                    "expected_sql": _format_expected_sql(view_name, metric_ident, req_dims, platform=target_platform),
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
                        "expected_sql": _format_expected_sql(view_name, metric_ident, dim1, platform=target_platform),
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
                        "expected_sql": _format_expected_sql(view_name, metric_ident, dim2, platform=target_platform),
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
                        "expected_sql": _format_expected_sql(view_name, metric_ident, d_dim, platform=target_platform),
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
                        "expected_sql": _format_expected_sql(view_name, metric_ident, top_dim, is_top_n=True, n=5, platform=target_platform),
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
                    "expected_sql": _format_expected_sql(target_view, metric_ident, platform=target_platform),
                    "expected_dax": _format_expected_dax(m_name),
                    "expected_row_count": 1,
                    "dimensions": [],
                    "question_kind": "total",
                })
            if is_window:
                dim_disp = ", ".join(d.split(".")[-1].replace("_", " ").title() if "_" in d else d for d in req_dims)
                cand_qs.append({
                    "question": f"What is {m_display} by {dim_disp}?",
                    "expected_sql": _format_expected_sql(target_view, metric_ident, req_dims, platform=target_platform),
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
                        "expected_sql": _format_expected_sql(target_view, metric_ident, d1, platform=target_platform),
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
                        "expected_sql": _format_expected_sql(target_view, metric_ident, d2, platform=target_platform),
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
        m_name = getattr(m, "name", None) or getattr(m, "unique_name", None) or ""
        if (
            getattr(m, "is_period_metric", False)
            or getattr(m, "time_intelligence", False)
            or getattr(m, "time_shift_tags", None)
            or getattr(m, "comparison_type", None)
            or (m_name and any(tag in m_name.upper() for tag in ("_SPLY", "_YOY", "_YTD", "SPLY", "YOY")))
        ):
            if m_name and m_name not in period_metrics:
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


def set_truth_case_critical_status(
    case_id: str,
    is_critical: bool,
    tolerance: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Mark a truth case as critical or non-critical, with optional tolerance override."""
    state = _load_suite()
    for c in state.get(_CASES_KEY, []):
        if c.get("id") == case_id:
            c["is_critical"] = bool(is_critical)
            if tolerance is not None:
                c["tolerance"] = float(tolerance) if tolerance > 0 else None
            c["updated_at"] = datetime.now(timezone.utc).isoformat()
            _save_suite(state)
            return c
    return None


def validate_window_filter_clause(filter_clause: str) -> None:
    """Validate that a window filter clause is safe for SQL injection."""
    clause = (filter_clause or "").strip()
    if not clause:
        raise ValueError("Window filter clause cannot be empty")
    if ";" in clause or "--" in clause or "/*" in clause or "*/" in clause:
        raise ValueError(f"Window filter clause contains forbidden SQL syntax: '{clause}'")
    forbidden = re.compile(r"\b(DROP|DELETE|UPDATE|INSERT|ALTER|TRUNCATE|EXEC|CREATE|GRANT|REVOKE)\b", re.IGNORECASE)
    if forbidden.search(clause):
        raise ValueError(f"Window filter clause contains forbidden mutation operation: '{clause}'")
    from semabridge.connectors.sql_guard import validate_read_only_sql, SQLSecurityGuardError
    try:
        validate_read_only_sql(f"SELECT 1 WHERE {clause}", warn_only=False)
    except SQLSecurityGuardError as err:
        raise ValueError(f"Window filter clause failed read-only safety check: {err}")
    except Exception:
        pass


def set_truth_case_windows(case_id: str, windows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Replace a truth case's slice windows with the provided list."""
    validated: List[Dict[str, Any]] = []
    for item in windows:
        if not isinstance(item, dict):
            continue
        clause = str(item.get("filter_clause") or item.get("filter") or "").strip()
        validate_window_filter_clause(clause)

        key = str(item.get("window_key") or item.get("key") or item.get("name") or "").strip()
        if not key:
            key = f"win-{uuid.uuid4().hex[:6]}"
        label = str(item.get("window_label") or item.get("label") or item.get("name") or key).strip()
        exp_val = item.get("expected_value")
        exp_rc = item.get("expected_row_count")
        if exp_rc is not None:
            try:
                exp_rc = int(exp_rc)
            except (ValueError, TypeError):
                exp_rc = None

        validated.append({
            "window_key": key,
            "window_label": label,
            "filter_clause": clause,
            "expected_value": exp_val,
            "expected_row_count": exp_rc,
            "comparable": exp_val is not None,
            "has_expected_value": exp_val is not None,
        })

    state = _load_suite()
    for c in state.get(_CASES_KEY, []):
        if c.get("id") == case_id:
            c["windows"] = validated
            c["updated_at"] = datetime.now(timezone.utc).isoformat()
            _save_suite(state)
            return c
    return None


def list_truth_case_dimensions(case_id: str, project_id: Optional[str] = None) -> List[str]:
    """Return available dimension expressions for the case's project/view."""
    state = _load_suite()
    target_case = next((c for c in state.get(_CASES_KEY, []) if c.get("id") == case_id), None)
    pid = project_id or (target_case.get("project_id") if target_case else None)

    dims: set[str] = set()
    if target_case:
        for d in target_case.get("dimensions") or []:
            if isinstance(d, str) and d.strip():
                dims.add(d.strip())
        sql = target_case.get("expected_sql") or ""
        m = re.search(r"\bDIMENSIONS\s+([^)]*?)(?=\bWHERE\b|\))", sql, re.IGNORECASE)
        if m:
            for part in m.group(1).split():
                clean = part.strip().rstrip(",;")
                if clean and not clean.startswith(("METRICS", "WHERE", "(")):
                    dims.add(clean)

    if pid:
        try:
            from semabridge.ontology.store import ontology_store
            fed = ontology_store.get_federated_ontology()
            clean_pid = pid.replace("proj-", "").replace("-", "_").casefold()
            for d_name, dom in getattr(fed, "domains", {}).items():
                if clean_pid in d_name.casefold() or any(clean_pid in getattr(c, "name", "").casefold() for c in getattr(dom, "concepts", [])):
                    for concept in getattr(dom, "concepts", []) or []:
                        c_name = concept.name
                        for attr in getattr(concept, "attributes", []) or []:
                            dims.add(f"{c_name}.{attr.name}")
        except Exception as ex:
            logger.debug("Failed extracting domain dimensions for %s: %s", pid, ex)

    if not dims:
        pass  # hardcoding-ok: generic fallback

    return sorted(dims)


def create_truth_schedule(
    project_id: str,
    cron_expression: str,
    webhook_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a recurring BI truth regression run schedule."""
    state = _load_suite()
    schedules = state.get("schedules") or []
    sched_id = f"btsched-{uuid.uuid4().hex[:8]}"
    entry = {
        "id": sched_id,
        "project_id": project_id,
        "cron_expression": cron_expression,
        "webhook_url": webhook_url,
        "enabled": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    schedules.append(entry)
    state["schedules"] = schedules
    _save_suite(state)

    try:
        from semabridge.governance.drift_service import _get_scheduler
        from apscheduler.triggers.cron import CronTrigger
        scheduler = _get_scheduler()
        if scheduler:
            scheduler.add_job(
                run_truth_suite,
                trigger=CronTrigger.from_crontab(cron_expression),
                id=sched_id,
                kwargs={"project_id": project_id},
                replace_existing=True,
            )
    except Exception as exc:
        logger.warning("Could not register APScheduler job %s: %s", sched_id, exc)

    return entry


def list_truth_schedules(project_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """List scheduled runs for BI truth suite."""
    state = _load_suite()
    scheds = state.get("schedules") or []
    if project_id:
        scheds = [s for s in scheds if _matches_pid(s.get("project_id"), project_id)]
    return scheds


def delete_truth_schedule(schedule_id: str) -> bool:
    """Delete a scheduled BI truth regression run."""
    state = _load_suite()
    scheds = state.get("schedules") or []
    before = len(scheds)
    state["schedules"] = [s for s in scheds if s.get("id") != schedule_id]
    if len(state["schedules"]) < before:
        _save_suite(state)
        try:
            from semabridge.governance.drift_service import _get_scheduler
            scheduler = _get_scheduler()
            if scheduler:
                scheduler.remove_job(schedule_id)
        except Exception:
            pass
        return True
    return False


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
    raw_cases = state.get(_CASES_KEY) or []
    if not isinstance(raw_cases, list):
        raw_cases = []
    approved: List[Dict[str, Any]] = [
        c for c in raw_cases
        if isinstance(c, dict) and c.get("status") == "approved" and _matches_pid(c.get("project_id"), project_id)
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

    target_pid = project_id or (approved[0].get("project_id") if approved else None)
    executor = get_executor(target_pid, owner_id=owner_id)

    from semabridge.ai.project_connection import _project_meta
    from semabridge.governance.drift.adapters.registry import resolve_project_platform
    meta = _project_meta(target_pid) if target_pid else {}
    plat = resolve_project_platform(meta)
    platform_name = getattr(executor, "platform", plat) if executor else plat
    executor_reason = f"no executor for platform {plat}" if plat and plat not in ("NOT_SET", "UNKNOWN", "") else "Query not executed against warehouse"

    results: List[Dict[str, Any]] = []

    try:
        if executor:
            try:
                executor.open()
            except Exception as conn_err:
                logger.warning("Failed opening executor connection: %s", conn_err)
        for c in approved:
            pid = c.get("project_id") or project_id
            scope = _case_scope(_get_scope(pid), c)
            generated_sql = ""
            mode = "local"
            err = None

            try:
                # 1. Governed query path first: questions matching governed metrics generate governed SQL
                plan = engine.resolve_query(
                    c["question"],
                    dialect=platform_name,
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
                    cfg = getattr(executor, "config", None)
                    if cfg is not None and getattr(cfg, "private_key", None) and getattr(scope, "semantic_view", None):
                        try:
                            from semabridge.ai.cortex_analyst_client import call_cortex_analyst
                            resp = call_cortex_analyst(prompt=c["question"], semantic_view=scope.semantic_view, config=cfg)
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
                from semabridge.connectors.sql_guard import validate_read_only_sql, SQLSecurityGuardError
                safety_err = None
                try:
                    is_safe = validate_read_only_sql(generated_sql, warn_only=False)
                except SQLSecurityGuardError as guard_err:
                    is_safe = False
                    safety_err = f"Unsafe SQL rejected: {guard_err}"
                except Exception as exc:
                    logger.exception("SQL security guard could not evaluate query: %s", exc)
                    is_safe = False
                    safety_err = f"SQL guard evaluation failed: {exc}"
                if not is_safe:
                    err = safety_err or "SQL safety check failed: query is not read-only"
                    generated_sql = ""

            from semabridge.ai.bi_truth_windows import build_windowed_sql, parse_case_windows
            case_windows = parse_case_windows(c.get("windows"))
            win_platform = platform_name

            if case_windows:
                for win in case_windows:
                    win_sql = build_windowed_sql(generated_sql, win, platform=win_platform) if generated_sql else ""
                    win_err = err
                    win_actual = None
                    win_ok = False
                    win_details = {}
                    win_expected = win.get("expected_value")

                    if win_expected is None:
                        # 1.2: A window with no expected value is NOT comparable and NEVER falls back to whole-case benchmark
                        win_ok = False
                        win_err = "Window has no recorded benchmark value; not comparable against whole-case benchmark"
                        win_details = {
                            "rule_applied": "window_missing_benchmark",
                            "comparable": False,
                            "reason": win_err,
                        }
                        if win_sql and executor is not None:
                            try:
                                qres = executor.execute(win_sql)
                                if qres.executed and not qres.error:
                                    win_actual = qres.rows
                                elif qres.error:
                                    win_err = f"{win_err} (query execution also failed: {qres.error})"
                            except Exception as q_err:
                                win_err = f"{win_err} (query execution also failed: {q_err})"
                    elif win_sql and executor is not None:
                        try:
                            qres = executor.execute(win_sql)
                            if qres.executed and not qres.error:
                                win_actual = qres.rows
                                from semabridge.ai.bi_truth_comparator import compare_truth_results
                                win_rc = win.get("expected_row_count") if win.get("expected_row_count") is not None else c.get("expected_row_count")
                                win_ok, mismatch_err, win_details = compare_truth_results(
                                    win_actual,
                                    win_expected,
                                    tolerance=c.get("tolerance"),
                                    expected_row_count=win_rc,
                                )
                                if not win_ok and mismatch_err:
                                    win_err = mismatch_err
                            elif qres.error:
                                win_ok = False
                                win_err = qres.error
                            else:
                                win_ok = False
                                win_err = "Query not executed against warehouse"
                        except Exception as q_err:
                            win_ok = False
                            win_err = str(q_err)
                    else:
                        win_ok = False
                        if not win_err:
                            win_err = executor_reason if executor is None else "Query not executed against warehouse"

                    win_verdict = (
                        "NOT_COMPARABLE"
                        if not (win_expected is not None) or win_sql is None
                        else (win_details.get("verdict") or ("PASS" if win_ok else "FAIL"))
                    )

                    results.append({
                        "case_id": c["id"],
                        "window_name": win.get("name"),
                        "window_filter": win.get("filter"),
                        "question": f"{c['question']} [{win.get('name')}]",
                        "metric_name": c.get("metric_name") or "",
                        "expected_sql": build_windowed_sql(c.get("expected_sql") or "", win, platform=win_platform),
                        "expected_value": win_expected,
                        "expected_evaluated_at": str(c.get("expected_evaluated_at") or ""),
                        "expected_row_count": win.get("expected_row_count"),
                        "actual_value": win_actual,
                        "passed": win_ok,
                        "verdict": win_verdict,
                        "is_critical": bool(c.get("is_critical", False)),
                        "tolerance": c.get("tolerance"),
                        "comparable": win_expected is not None and win_sql is not None,
                        "comparison_details": win_details,
                        "mode": mode,
                        "platform": platform_name,
                        "generated_sql": win_sql,
                        "error": win_err,
                        "reason": win_err,
                    })
            else:
                actual_val = None
                cmp_details = {}
                exp_val = c.get("expected_value")
                has_benchmark = exp_val is not None

                cols = []
                if generated_sql and executor is not None:
                    try:
                        qres = executor.execute(generated_sql)
                        if qres.executed and not qres.error:
                            actual_val = qres.rows
                            cols = qres.columns
                        elif qres.error:
                            err = qres.error
                    except Exception as q_err:
                        err = str(q_err)

                if not has_benchmark:
                    ok = False
                    not_comp_reason = "Case has no recorded benchmark value; not comparable"
                    if not err:
                        err = not_comp_reason
                    reason = not_comp_reason
                    cmp_details = {
                        "rule_applied": "missing_benchmark",
                        "comparable": False,
                        "verdict": "NOT_COMPARABLE",
                        "reason": not_comp_reason,
                    }
                    base_verdict = "NOT_COMPARABLE"
                    is_comparable = False
                elif actual_val is not None:
                    from semabridge.ai.bi_truth_comparator import compare_truth_results
                    matches, mismatch_err, cmp_details = compare_truth_results(
                        actual_val,
                        exp_val,
                        tolerance=c.get("tolerance"),
                        expected_row_count=c.get("expected_row_count"),
                    )
                    ok = matches
                    if not ok and mismatch_err:
                        err = mismatch_err
                    reason = err or cmp_details.get("reason", "")
                    # Result checks: check for breakdown exceeding expected total
                    if ok and actual_val and exp_val:
                        from semabridge.api.routers.ai_router import check_query_results
                        card_tot = None
                        if isinstance(exp_val, (int, float)):
                            card_tot = float(exp_val)
                        elif isinstance(exp_val, list) and len(exp_val) == 1 and isinstance(exp_val[0], dict):
                            vals = [v for v in exp_val[0].values() if isinstance(v, (int, float))]
                            if vals:
                                card_tot = float(vals[0])
                        row_vals = [[r.get(col) for col in cols] for r in actual_val]
                        res_warns, is_valid = check_query_results(cols, row_vals, card_total=card_tot)
                        if not is_valid:
                            ok = False
                            err = "; ".join(res_warns)
                            reason = err
                    base_verdict = cmp_details.get("verdict") or ("PASS" if ok else "FAIL")
                    is_comparable = True
                else:
                    ok = False
                    if not err:
                        err = executor_reason if executor is None else "Query not executed against warehouse"
                    reason = err
                    base_verdict = "NOT_COMPARABLE"
                    is_comparable = False
                    cmp_details = {
                        "rule_applied": "no_platform_executor" if executor is None else "query_execution_failed",
                        "comparable": False,
                        "verdict": "NOT_COMPARABLE",
                        "reason": err,
                    }

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
                            target=platform_name,
                        )
                    except Exception as save_err:
                        logger.warning("Recording query execution for case %s skipped: %s", c.get("id"), save_err)

                results.append({
                    "case_id": c["id"],
                    "question": c["question"],
                    "metric_name": c.get("metric_name") or "",
                    "expected_sql": c.get("expected_sql") or "",
                    "expected_value": c.get("expected_value"),
                    "expected_evaluated_at": str(c.get("expected_evaluated_at") or ""),
                    "expected_row_count": c.get("expected_row_count"),
                    "actual_value": actual_val,
                    "actual_row_count": len(actual_val) if actual_val is not None else None,
                    "passed": ok,
                    "verdict": base_verdict,
                    "is_critical": bool(c.get("is_critical", False)),
                    "tolerance": c.get("tolerance"),
                    "comparable": is_comparable,
                    "comparison_details": cmp_details,
                    "mode": mode,
                    "platform": platform_name,
                    "generated_sql": generated_sql,
                    "error": err,
                    "reason": reason,
                })
    finally:
        if executor:
            try:
                executor.close()
            except Exception as ex:
                logger.warning("Closing executor failed: %s", ex)

    comparable_results = [r for r in results if r.get("comparable") is True and r.get("mode") != "skipped"]
    passed = sum(1 for r in comparable_results if r.get("passed"))
    comparable_total = len(comparable_results)
    total = len(results)
    accuracy = round((100.0 * passed / comparable_total), 2) if comparable_total else (None if executor is None else 0.0)
    run_rec = {
        "id": f"trun-{uuid.uuid4().hex[:8]}",
        "project_id": project_id,
        "view": resolved_view,
        "semantic_view": resolved_view,
        "total": total,
        "comparable_total": comparable_total,
        "passed": passed,
        "accuracy_pct": accuracy,
        "status": "NOT_RUN" if (executor is None and approved) else "COMPLETED",
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


def evaluate_project_deploy_gate(project_id: str, gate_mode: str = "report") -> Any:
    """Evaluate deploy gate for a project by reading its critical cases and latest run."""
    from semabridge.ai.bi_truth_gate import check_deploy_gate, GateMode
    cases = list_truth_cases(project_id) if project_id else _load_suite().get("cases", [])
    critical_cases = [c for c in cases if c.get("is_critical")]
    critical_measures = [c.get("metric_name") for c in critical_cases if c.get("metric_name")]

    runs = list_truth_runs(project_id) if project_id else _load_suite().get("runs", [])
    latest_run = runs[0] if runs else None

    mode = GateMode.BLOCK if str(gate_mode or "").lower() == "block" else GateMode.REPORT
    return check_deploy_gate(
        critical_measures=critical_measures,
        latest_run=latest_run,
        mode=mode,
    )


def inject_verified_queries_for_project(
    project_id: str,
    case_ids: Optional[List[str]] = None,
    dry_run: bool = False,
    user: Optional[str] = None,
    view_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Inject qualifying verified query pairs into downstream AI consumer (Cortex Analyst or Genie)."""
    from semabridge.ai.bi_truth_injection import perform_injection
    from semabridge.governance.drift.adapters.registry import get_adapter, resolve_project_platform
    from semabridge.ai.project_connection import _project_meta, resolve_project_warehouse_config

    pid = str(project_id or "").strip()
    if not pid:
        raise ValueError("project_id is required for verified query injection")

    cases = list_truth_cases(pid)
    runs = list_truth_runs(pid)
    latest_run = runs[0] if runs else None

    # Resolve platform from project metadata or executor
    meta = _project_meta(pid)
    platform = resolve_project_platform(meta)
    if platform in ("NOT_SET", "UNKNOWN", ""):
        from semabridge.ai.executors.base import get_executor
        ex = get_executor(pid)
        if ex:
            platform = ex.platform

    try:
        adapter = get_adapter(platform, mode="live")
    except Exception as exc:
        return {
            "status": "refused",
            "project_id": pid,
            "dry_run": dry_run,
            "injected_count": 0,
            "injected": [],
            "skipped": [],
            "failed": [],
            "message": f"Platform '{platform}' adapter resolution failed: {exc}",
        }

    resolved_view = view_name or ""
    if not resolved_view and latest_run:
        resolved_view = latest_run.get("view") or latest_run.get("semantic_view") or ""
    if not resolved_view and cases:
        resolved_view = cases[0].get("view") or cases[0].get("semantic_view") or ""
    if not resolved_view:
        resolved_view = pid.replace("proj-", "")

    conn_cfg = resolve_project_warehouse_config(pid, platform)
    result = perform_injection(
        adapter=adapter,
        project_id=pid,
        view_name=resolved_view,
        connection_config=conn_cfg.model_dump() if hasattr(conn_cfg, "model_dump") else conn_cfg,
        cases=cases,
        latest_run=latest_run,
        case_ids=case_ids,
        dry_run=dry_run,
        user=user,
    )

    if not dry_run and result.get("injected_count", 0) > 0:
        state = _load_suite()
        injections = state.get("injections", [])
        if "audit_record" in result:
            injections.insert(0, result["audit_record"])
            state["injections"] = injections[:50]
            _save_suite(state)

    return result


