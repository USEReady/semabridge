"""BI-Truth Test Suite: Generate Q&A from certified metrics, approve, run against Cortex/agent, track accuracy."""

from __future__ import annotations

import concurrent.futures
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _suite_path() -> Path:
    p = Path.home() / ".semabridge" / "bi_truth_suite.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load_suite() -> Dict[str, Any]:
    p = _suite_path()
    if not p.exists():
        return {"cases": [], "runs": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {"cases": list(data.get("cases") or []), "runs": list(data.get("runs") or [])}
    except Exception:
        return {"cases": [], "runs": []}


def _save_suite(data: Dict[str, Any]) -> None:
    _suite_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


def generate_truth_cases(
    *,
    project_id: Optional[str] = None,
    domain: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Build draft BI-truth Q&A cases from certified metrics and verified queries."""
    from semabridge.ai.verified_queries_service import list_verified_queries_for_project
    from semabridge.reconciliation.certification_service import get_latest_certifications

    certs = get_latest_certifications(project_id=project_id, domain=domain)
    vqs = list_verified_queries_for_project(project_id=project_id, domain=domain) if project_id else []
    now = datetime.now(timezone.utc).isoformat()

    created: List[Dict[str, Any]] = []
    for c in certs:
        if str(c.get("status") or "").upper() != "CERTIFIED":
            continue
        mname = c.get("metric_name") or "metric"
        dims = c.get("dimensions") or []
        q = f"What is {mname}" + (f" by {', '.join(dims)}" if dims else "") + "?"
        created.append({
            "id": f"truth-{uuid.uuid4().hex[:8]}",
            "project_id": project_id or c.get("project_id"),
            "domain": domain or c.get("domain"),
            "metric_name": mname,
            "question": q,
            "expected_sql": c.get("sql_query") or "",
            "expected_row_count": c.get("sql_row_count") or 0,
            "status": "draft",
            "created_at": now,
        })

    for v in vqs:
        q = (v.get("question") or "").strip()
        if not q:
            continue
        created.append({
            "id": f"truth-{uuid.uuid4().hex[:8]}",
            "project_id": project_id or v.get("project_id"),
            "domain": domain or v.get("domain"),
            "metric_name": v.get("metric_name") or "",
            "question": q,
            "expected_sql": v.get("sql") or "",
            "expected_row_count": 0,
            "status": "draft",
            "created_at": now,
        })

    # If no certified metrics or verified queries exist yet, bootstrap draft test cases from domain metrics
    if not created and (project_id or domain):
        from semabridge.ontology.store import ontology_store
        fed = ontology_store.get_federated_ontology()
        pid_clean = str(project_id or "").strip().casefold()
        dom_clean = str(domain or "").strip().casefold()
        target_domains = []
        for d in fed.domains.values():
            sp = str(getattr(d, "source_project_id", "") or "").strip().casefold()
            did = str(getattr(d, "id", "") or "").strip().casefold()
            dname = str(getattr(d, "domain", "") or "").strip().casefold()
            matched = False
            if pid_clean and (
                pid_clean in (sp, did, dname)
                or (sp and (sp == pid_clean or sp.replace("proj-", "") == pid_clean.replace("proj-", "")))
            ):
                matched = True
            if dom_clean and dom_clean in (did, dname):
                matched = True
            if matched and d not in target_domains:
                target_domains.append(d)

        for d in target_domains:
            for m in d.metrics:
                mname = m.label or m.name
                q = f"What is {mname}?"
                created.append({
                    "id": f"truth-{uuid.uuid4().hex[:8]}",
                    "project_id": project_id or getattr(d, "source_project_id", None),
                    "domain": d.domain,
                    "metric_name": m.name,
                    "question": q,
                    "expected_sql": m.sql_expression or "",
                    "expected_row_count": 0,
                    "status": "draft",
                    "created_at": now,
                })

    if created:
        state = _load_suite()
        existing_qs = {(c.get("project_id"), c.get("question")) for c in state["cases"]}
        added = [c for c in created if (c.get("project_id"), c.get("question")) not in existing_qs]
        state["cases"] = added + state["cases"]
        _save_suite(state)
        return added
    return []


def generate_golden_questions_from_pbix_dir(
    *,
    pbix_dir: str = "examples/powerbi",
    limit: int = 50,
    project_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Scan .pbix reports under ``pbix_dir`` and generate up to ``limit`` golden Q&A cases."""
    from semabridge.connectors.local_pbix_connector import LocalPBIXConnector

    root = Path(pbix_dir)
    if not root.is_absolute():
        # Resolve relative to repo root if needed
        candidate = Path.cwd() / root
        if not candidate.exists():
            candidate = Path(__file__).resolve().parents[3] / root
        root = candidate
    if not root.exists():
        return []

    pbix_files = sorted(root.glob("*.pbix"))
    now = datetime.now(timezone.utc).isoformat()
    candidates: List[Dict[str, Any]] = []

    for pbix in pbix_files:
        if len(candidates) >= limit * 2:
            break
        report_name = pbix.stem.replace("_", " ").strip()
        try:
            discovered = LocalPBIXConnector({"pbix_path": str(pbix)}).discover()
        except Exception:
            continue
        raw_tmsl = (discovered or {}).get("raw_tmsl") or {}
        tables = (raw_tmsl.get("model") or {}).get("tables") or (discovered or {}).get("tables") or []
        top_measures = (discovered or {}).get("measures") or []
        dims: List[tuple[str, str]] = []
        measures: List[tuple[str, str, str]] = []

        for tbl in tables:
            tname = str(tbl.get("name") or tbl.get("table_name") or "").strip()
            if not tname or tname.startswith(("DateTableTemplate_", "LocalDateTable_")):
                continue
            for col in tbl.get("columns") or []:
                cname = str(col.get("name") or col.get("column_name") or "").strip()
                if not cname or cname.startswith(("RowNumber", "_")) or col.get("isHidden") or col.get("is_hidden"):
                    continue
                dtype = str(col.get("dataType") or col.get("data_type") or "").lower()
                if dtype in ("string", "datetime", "date") or any(k in cname.lower() for k in ("category", "region", "country", "state", "segment", "year", "month", "brand", "department", "manufacturer")):
                    dims.append((tname, cname))
            for m in tbl.get("measures") or []:
                mname = str(m.get("name") or "").strip()
                mexpr = m.get("expression")
                if isinstance(mexpr, list):
                    mexpr = " ".join(str(x) for x in mexpr)
                if mname and not m.get("isHidden"):
                    measures.append((tname, mname, str(mexpr or "").strip()))

        if not measures and top_measures:
            for m in top_measures:
                mname = str(m.get("name") or m.get("measure_name") or "").strip()
                tname = str(m.get("table") or m.get("table_name") or "Model").strip()
                mexpr = str(m.get("expression") or m.get("dax_expression") or "").strip()
                if mname and not m.get("isHidden") and not m.get("is_hidden"):
                    measures.append((tname, mname, mexpr))

        for tname, mname, mexpr in measures:
            if dims:
                dtbl, dcol = dims[len(candidates) % len(dims)]
                q = f"What is {mname} by {dcol} in {report_name}?"
                dax = f"EVALUATE SUMMARIZECOLUMNS('{dtbl}'[{dcol}], \"{mname}\", [{mname}])"
                sql = f"SELECT {dcol}, {mname} FROM {tname} GROUP BY {dcol}"
            else:
                q = f"What is {mname} in {report_name}?"
                dax = f"EVALUATE ROW(\"{mname}\", [{mname}])"
                sql = f"SELECT {mname} FROM {tname}"
            candidates.append({
                "id": f"truth-{uuid.uuid4().hex[:8]}",
                "project_id": project_id,
                "domain": report_name,
                "source_report": pbix.name,
                "metric_name": mname,
                "dax_expression": mexpr,
                "question": q,
                "expected_dax": dax,
                "expected_sql": sql,
                "expected_row_count": 0,
                "status": "draft",
                "created_at": now,
            })

    # Spread across reports round-robin / capped at limit
    selected = candidates[:limit]
    if selected:
        state = _load_suite()
        existing_qs = {(c.get("project_id"), c.get("question")) for c in state["cases"]}
        added = [c for c in selected if (c.get("project_id"), c.get("question")) not in existing_qs]
        state["cases"] = added + state["cases"]
        _save_suite(state)
        return added
    return []


def _match_pid(c_pid: Any, target_pid: Optional[str]) -> bool:
    if not target_pid:
        return True
    c_str = str(c_pid or "").strip().casefold()
    t_str = str(target_pid or "").strip().casefold()
    if not c_str or not t_str:
        return False
    if c_str == t_str:
        return True
    c_norm = c_str.replace("proj-", "").replace("-", "_").replace(" ", "_")
    t_norm = t_str.replace("proj-", "").replace("-", "_").replace(" ", "_")
    return c_norm == t_norm or c_norm.startswith(t_norm) or t_norm.startswith(c_norm)


def list_truth_cases(project_id: Optional[str] = None) -> List[Dict[str, Any]]:
    cases = _load_suite()["cases"]
    if project_id:
        cases = [c for c in cases if _match_pid(c.get("project_id"), project_id)]
    return cases


def set_truth_case_status(case_id: str, status: str, question: Optional[str] = None) -> Optional[Dict[str, Any]]:
    state = _load_suite()
    for c in state["cases"]:
        if c["id"] == case_id:
            c["status"] = status
            if question is not None and question.strip():
                c["question"] = question.strip()
            c["updated_at"] = datetime.now(timezone.utc).isoformat()
            _save_suite(state)
            return c
    return None


def run_truth_suite(
    *,
    project_id: Optional[str] = None,
    owner_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute all approved BI-truth questions against Cortex / NL engine and record accuracy."""
    from semabridge.ai.nl_query_engine import NLQueryEngine
    from semabridge.ai.project_connection import resolve_project_scope, resolve_project_warehouse_config
    from semabridge.ontology.store import ontology_store

    state = _load_suite()
    approved = [
        c for c in state["cases"]
        if c.get("status") == "approved" and (not project_id or _match_pid(c.get("project_id"), project_id))
    ]
    engine = NLQueryEngine(ontology_store.search_index, ontology_store.get_federated_ontology())

    def _eval_case(c: Dict[str, Any]) -> Dict[str, Any]:
        pid = c.get("project_id") or project_id
        scope = resolve_project_scope(pid)
        generated_sql = ""
        mode = "local"
        err = None
        try:
            sf = resolve_project_warehouse_config(pid, "SNOWFLAKE", owner_id=owner_id)
            if sf is not None and getattr(sf, "private_key", None) and scope.semantic_view:
                from semabridge.ai.cortex_analyst_client import call_cortex_analyst

                resp = call_cortex_analyst(prompt=c["question"], semantic_view=scope.semantic_view, config=sf)
                generated_sql = (resp.get("generated_sql") or "").strip()
                mode = "cortex"
            else:
                plan = engine.resolve_query(c["question"], dialect="SNOWFLAKE", semantic_view=scope.semantic_view, allowed_domains=scope.allowed_domains)
                generated_sql = (plan.generated_sql or "").strip()
        except Exception as exc:
            err = str(exc)

        ok = bool(generated_sql) and not err
        return {
            "case_id": c["id"],
            "question": c["question"],
            "passed": ok,
            "mode": mode,
            "generated_sql": generated_sql,
            "error": err,
        }

    results: List[Dict[str, Any]] = []
    if approved:
        max_workers = min(len(approved), 6)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            results = list(pool.map(_eval_case, approved))

    passed = sum(1 for r in results if r.get("passed"))
    total = len(approved)
    accuracy = round((100.0 * passed / total), 2) if total else 0.0
    run_rec = {
        "id": f"trun-{uuid.uuid4().hex[:8]}",
        "project_id": project_id,
        "total": total,
        "passed": passed,
        "accuracy_pct": accuracy,
        "results": results,
        "ran_at": datetime.now(timezone.utc).isoformat(),
    }
    state["runs"] = ([run_rec] + state["runs"])[:50]
    _save_suite(state)
    return run_rec


def list_truth_runs(project_id: Optional[str] = None) -> List[Dict[str, Any]]:
    runs = _load_suite()["runs"]
    if project_id:
        runs = [r for r in runs if _match_pid(r.get("project_id"), project_id)]
    return runs

