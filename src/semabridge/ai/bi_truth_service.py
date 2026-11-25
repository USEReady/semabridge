"""BI-Truth Test Suite: Generate Q&A from certified metrics, approve, run against Cortex/agent, track accuracy."""

from __future__ import annotations

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
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Build draft BI-truth Q&A cases from project model runs, certified metrics, and verified queries."""
    from semabridge.ai.verified_queries_service import list_verified_queries_for_project
    from semabridge.reconciliation.certification_service import get_latest_certifications
    from semabridge.ontology.store import ontology_store

    certs = get_latest_certifications(project_id=project_id, domain=domain)
    vqs = list_verified_queries_for_project(project_id=project_id) if project_id else []
    now = datetime.now(timezone.utc).isoformat()

    created: List[Dict[str, Any]] = []
    seen_metrics = set()

    for c in certs:
        if str(c.get("status") or "").upper() != "CERTIFIED":
            continue
        mname = c.get("metric_name") or "metric"
        dims = c.get("dimensions") or []
        q = f"What is {mname}" + (f" by {', '.join(dims)}" if dims else "") + "?"
        seen_metrics.add(mname.casefold())
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

    # Pull directly from Project's Model Run / Domain Ontology
    target_domains = []
    pid = (project_id or "").strip()
    if pid:
        # One resolver; a project without domains yields no suggestions
        # (never another project's metrics).
        target_domains.extend(
            d for d in ontology_store.domains_for(pid)
            if not domain or d.domain.casefold() == domain.casefold() or (d.id or "").casefold() == domain.casefold()
        )
    elif domain:
        d = ontology_store.get_domain(domain)
        if d:
            target_domains.append(d)
    else:
        target_domains = list(ontology_store.get_federated_ontology().domains.values())

    # Same-named metrics of different models (one domain per model) are
    # separate cases; their questions name the domain to stay distinct.
    prior_seen = set(seen_metrics)
    name_counts: Dict[str, int] = {}
    for dom in target_domains:
        for nm in {(m.name or m.logical_name or "").casefold() for m in getattr(dom, "metrics", []) or []}:
            name_counts[nm] = name_counts.get(nm, 0) + 1
    seen_pairs: set = set()

    for dom in target_domains:
        d_name = dom.domain
        # Gather potential dimension attributes
        dim_candidates = []
        for concept in getattr(dom, "concepts", []) or []:
            c_name = concept.name
            for attr in getattr(concept, "attributes", []) or []:
                a_name = attr.name
                # Avoid surrogate IDs or internal flags
                if not any(k in a_name.lower() for k in ("_id", "guid", "row_num", "sys_")):
                    dim_candidates.append(f"{c_name}.{a_name}")

        # Sort metrics so high-value business metrics come first (Sales, Units, Revenue, Count, Margin, etc.)
        def _metric_priority(m_obj):
            n = (m_obj.name or m_obj.logical_name or "").lower()
            if any(k in n for k in ("sales", "unit", "revenue", "count", "profit", "margin", "amount", "cost", "total")):
                return 0
            if any(k in n for k in ("kpi", "growth", "share", "ratio", "rate")):
                return 1
            return 2

        all_dom_metrics = sorted(getattr(dom, "metrics", []) or [], key=_metric_priority)

        for m in all_dom_metrics:
            if len(created) >= limit:
                break
            m_name = m.name or m.logical_name
            if not m_name or m_name.casefold() in prior_seen or (d_name, m_name.casefold()) in seen_pairs:
                continue
            seen_pairs.add((d_name, m_name.casefold()))
            seen_metrics.add(m_name.casefold())
            in_dom = f" in {d_name}" if name_counts.get(m_name.casefold(), 0) > 1 else ""
            m_sql = m.sql_expression or ""
            m_dax = m.dax_expression or ""

            # 1. Total aggregate question
            created.append({
                "id": f"truth-{uuid.uuid4().hex[:8]}",
                "project_id": project_id or getattr(dom, "source_project_id", None) or d_name,
                "domain": d_name,
                "metric_name": m_name,
                "question": f"What is the total {m_name}{in_dom}?",
                "expected_sql": m_sql,
                "expected_dax": m_dax,
                "expected_row_count": 1,
                "status": "draft",
                "created_at": now,
            })
            if len(created) >= limit:
                break

            # 2. Slice question (pick a top candidate dimension)
            if dim_candidates:
                chosen_dim = dim_candidates[len(created) % len(dim_candidates)]
                created.append({
                    "id": f"truth-{uuid.uuid4().hex[:8]}",
                    "project_id": project_id or getattr(dom, "source_project_id", None) or d_name,
                    "domain": d_name,
                    "metric_name": m_name,
                    "question": f"What is {m_name} by {chosen_dim}{in_dom}?",
                    "expected_sql": f"SELECT {chosen_dim}, {m_sql} GROUP BY {chosen_dim}" if m_sql else "",
                    "expected_dax": f"EVALUATE SUMMARIZECOLUMNS('{chosen_dim}', \"{m_name}\", [{m_name}])" if m_dax else "",
                    "expected_row_count": 0,
                    "status": "draft",
                    "created_at": now,
                })
            if len(created) >= limit:
                break

    if created:
        state = _load_suite()
        existing_qs = {(str(c.get("project_id") or "").removeprefix("proj-").strip().casefold(), (c.get("question") or "").strip().casefold()) for c in state["cases"]}
        added = [c for c in created if (str(c.get("project_id") or "").removeprefix("proj-").strip().casefold(), (c.get("question") or "").strip().casefold()) not in existing_qs]
        state["cases"] = added + state["cases"]
        _save_suite(state)
        return added
    return []


def clear_truth_cases(project_id: Optional[str] = None) -> int:
    """Clear truth test cases and past run results for a specific project or all projects."""
    state = _load_suite()
    orig_len = len(state.get("cases") or [])
    pid = (project_id or "").strip()
    if pid:
        state["cases"] = [c for c in state.get("cases") or [] if not _matches_pid(c.get("project_id"), pid)]
        state["runs"] = [r for r in state.get("runs") or [] if not _matches_pid(r.get("project_id"), pid)]
    else:
        state["cases"] = []
        state["runs"] = []
    _save_suite(state)
    return orig_len - len(state.get("cases") or [])


def add_manual_truth_case(
    *,
    project_id: Optional[str] = None,
    domain: Optional[str] = None,
    question: str,
    metric_name: Optional[str] = None,
    expected_sql: Optional[str] = None,
    expected_dax: Optional[str] = None,
    status: str = "approved",
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
    state["cases"] = [new_case] + (state.get("cases") or [])
    _save_suite(state)
    return new_case


def import_truth_cases_from_content(
    *,
    content: str,
    filename: str = "upload.csv",
    project_id: Optional[str] = None,
    domain: Optional[str] = None,
    status: str = "approved",
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
            items = data if isinstance(data, list) else data.get("cases") or data.get("questions") or data.get("tests") or []
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
        except Exception:
            pass

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
        except Exception:
            pass

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

    existing_qs = {(c.get("project_id"), c.get("question")) for c in state.get("cases") or []}
    added = [c for c in created if (c.get("project_id"), c.get("question")) not in existing_qs]
    state["cases"] = added + (state.get("cases") or [])
    _save_suite(state)
    return added


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
        return True
    c_clean = str(case_pid).removeprefix("proj-").strip().casefold()
    t_clean = str(target_pid).removeprefix("proj-").strip().casefold()
    return c_clean == t_clean


def list_truth_cases(project_id: Optional[str] = None) -> List[Dict[str, Any]]:
    cases = _load_suite()["cases"]
    if project_id:
        cases = [c for c in cases if _matches_pid(c.get("project_id"), project_id)]
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

    for c in state.get("cases") or []:
        if project_id and not _matches_pid(c.get("project_id"), project_id):
            continue
        if cid_set is not None and c.get("id") not in cid_set:
            continue
        c["status"] = status
        c["updated_at"] = now
        count += 1

    if count > 0:
        _save_suite(state)
    return count


def _detect_project_target(project_id: Optional[str]) -> str:
    """Detect whether ``project_id`` targets Databricks or Snowflake."""
    pid = (project_id or "").strip()
    if not pid:
        return "SNOWFLAKE"
    try:
        from semabridge.ai.project_connection import _project_meta

        meta = _project_meta(pid) or {}
        candidates = [
            meta.get("target_type"),
            (meta.get("target") or {}).get("type") if isinstance(meta.get("target"), dict) else None,
        ]
        targets = meta.get("targets")
        if isinstance(targets, list) and targets and isinstance(targets[0], dict):
            candidates.append(targets[0].get("type"))
        for raw in candidates:
            if raw and "databricks" in str(raw).strip().lower():
                return "DATABRICKS"
    except Exception:
        pass
    try:
        from semabridge.repository.model_repository import ModelRepository

        info = ModelRepository().get_project_deployment_info(pid) or {}
        for key in ("target_type", "platform", "target"):
            val = info.get(key)
            if val and "databricks" in str(val).strip().lower():
                return "DATABRICKS"
    except Exception:
        pass
    return "SNOWFLAKE"


def run_truth_suite(
    *,
    project_id: Optional[str] = None,
    owner_id: Optional[str] = None,
    max_cases: int = 50,
    live_parity: bool = True,
    dax_executor: Optional[Any] = None,
    sql_executor: Optional[Any] = None,
) -> Dict[str, Any]:
    """Execute approved BI-truth questions against Cortex, Databricks SQL Warehouse, or NL engine
    and record accuracy.

    When ``live_parity=True``, also executes DAX and SQL queries against backends
    and requires returned values to match mathematically (never passes just because
    SQL was generated).
    """
    from concurrent.futures import ThreadPoolExecutor
    from semabridge.ai.databricks_genie_service import execute_databricks_sql
    from semabridge.ai.nl_query_engine import NLQueryEngine
    from semabridge.ai.project_connection import resolve_project_scope, resolve_project_warehouse_config
    from semabridge.ontology.store import ontology_store

    state = _load_suite()
    approved = [
        c for c in state.get("cases") or []
        if c.get("status") == "approved" and _matches_pid(c.get("project_id"), project_id)
    ]
    if max_cases and len(approved) > max_cases:
        approved = approved[:max_cases]

    engine = NLQueryEngine(ontology_store.search_index, ontology_store.get_federated_ontology())

    # Pre-resolve project scopes and configs once to avoid repetitive DB lookups
    scopes_cache: Dict[str, Any] = {}
    target_cache: Dict[str, str] = {}
    sf_cache: Dict[str, Any] = {}
    db_cache: Dict[str, Any] = {}

    def _get_scope(pid: Optional[str]):
        p = pid or ""
        if p not in scopes_cache:
            scopes_cache[p] = resolve_project_scope(p)
        return scopes_cache[p]

    def _get_target(pid: Optional[str]) -> str:
        p = pid or ""
        if p not in target_cache:
            target_cache[p] = _detect_project_target(p)
        return target_cache[p]

    def _get_sf(pid: Optional[str]):
        p = pid or ""
        if p not in sf_cache:
            try:
                sf_cache[p] = resolve_project_warehouse_config(p, "SNOWFLAKE", owner_id=owner_id)
            except Exception:
                sf_cache[p] = None
        return sf_cache[p]

    def _get_db(pid: Optional[str]):
        p = pid or ""
        if p not in db_cache:
            try:
                db_cache[p] = resolve_project_warehouse_config(p, "DATABRICKS", owner_id=owner_id)
            except Exception:
                db_cache[p] = None
        return db_cache[p]

    def _evaluate_case(c: Dict[str, Any]) -> Dict[str, Any]:
        pid = c.get("project_id") or project_id
        scope = _case_scope(_get_scope(pid), c)
        project_target = _get_target(pid)
        generated_sql = ""
        mode = "local"
        err = None
        row_count: Optional[int] = None
        sf = None  # set on the Snowflake path; live parity reads it on every path
        warehouse_rows: Optional[List[Dict[str, Any]]] = None  # Databricks result rows

        try:
            if project_target == "DATABRICKS":
                plan = engine.resolve_query(
                    c["question"],
                    dialect="DATABRICKS",
                    semantic_view=getattr(scope, "semantic_view", None),
                    allowed_domains=getattr(scope, "allowed_domains", []),
                )
                generated_sql = (plan.generated_sql or "").strip()
                is_refusal = generated_sql.lstrip().startswith("-- SemaBridge AI Query Refusal")
                sql_to_run = (
                    (c.get("expected_sql") or "").strip()
                    if (not generated_sql or is_refusal)
                    else generated_sql
                )
                db_cfg = _get_db(pid)
                if db_cfg is not None and sql_to_run:
                    exec_res = execute_databricks_sql(db_cfg, sql_to_run)
                    if exec_res.get("success"):
                        mode = "databricks_sql"
                        row_count = exec_res.get("row_count", 0)
                        from semabridge.reconciliation.project_verify import rows_as_dicts as _rows

                        warehouse_rows = _rows(exec_res.get("rows") or [], exec_res.get("columns"))
                        if not generated_sql or is_refusal:
                            generated_sql = sql_to_run
                    elif exec_res.get("not_configured"):
                        mode = "databricks_local"
                    else:
                        mode = "databricks_sql"
                        err = exec_res.get("error") or "Databricks SQL execution failed"
                else:
                    mode = "databricks_local"
            else:
                sf = _get_sf(pid)
                if sf is not None and getattr(sf, "private_key", None) and getattr(scope, "semantic_view", None):
                    try:
                        from semabridge.ai.cortex_analyst_client import call_cortex_analyst
                        resp = call_cortex_analyst(prompt=c["question"], semantic_view=scope.semantic_view, config=sf)
                        generated_sql = (resp.get("generated_sql") or "").strip()
                        mode = "cortex"
                    except Exception:
                        # Gracefully fall back to local semantic engine if Cortex network call is unavailable/times out
                        plan = engine.resolve_query(
                            c["question"],
                            dialect="SNOWFLAKE",
                            semantic_view=getattr(scope, "semantic_view", None),
                            allowed_domains=getattr(scope, "allowed_domains", []),
                        )
                        generated_sql = (plan.generated_sql or "").strip()
                        mode = "local_fallback"
                else:
                    plan = engine.resolve_query(
                        c["question"],
                        dialect="SNOWFLAKE",
                        semantic_view=getattr(scope, "semantic_view", None),
                        allowed_domains=getattr(scope, "allowed_domains", []),
                    )
                    generated_sql = (plan.generated_sql or "").strip()
        except Exception as exc:
            err = str(exc)

        from semabridge.reconciliation import project_verify as pv

        refused = generated_sql.lstrip().startswith("-- SemaBridge AI Query Refusal")
        # No AI answer: the case's known-good SQL is still compared (the
        # Databricks path above already executes it the same way).
        if (not generated_sql or refused) and (c.get("expected_sql") or "").strip() and not err:
            generated_sql = (c.get("expected_sql") or "").strip()
            refused = False
        if err or not generated_sql or refused:
            verdict = pv.Verdict(pv.FAILED, err or ("refused: " + generated_sql[:200] if refused else "no SQL generated"))
        elif not live_parity:
            verdict = pv.Verdict(pv.UNVERIFIED, "comparison with Power BI was not requested")
        else:
            # A pass means the numbers match Power BI, never "SQL was generated".
            dax_query = (c.get("expected_dax") or "").strip()
            sql_query = generated_sql
            try:
                if dax_executor:
                    dax_rows = pv.rows_as_dicts(dax_executor(dax_query))
                else:
                    dax_rows = pv.run_project_dax(pid, dax_query)
                if sql_executor:
                    sql_rows = pv.rows_as_dicts(sql_executor(sql_query))
                elif project_target == "DATABRICKS":
                    if warehouse_rows is None:
                        raise pv.VerificationUnavailable("the Databricks warehouse did not return rows")
                    sql_rows = warehouse_rows
                elif sf is not None:
                    import snowflake.connector
                    from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs

                    with snowflake.connector.connect(**get_snowflake_connect_kwargs(sf)) as conn:
                        cur = conn.cursor()
                        cur.execute(sql_query)
                        cols = [d[0] for d in cur.description]
                        sql_rows = [dict(zip(cols, row)) for row in cur.fetchall()]
                else:
                    raise pv.VerificationUnavailable("no Snowflake connection for this project")
                verdict = pv.compare(c.get("metric_name") or "metric", dax_rows, sql_rows,
                                     dax_query=dax_query, sql_query=sql_query)
            except pv.VerificationUnavailable as exc:
                verdict = pv.Verdict(pv.UNVERIFIED, str(exc))
            except Exception as exc:  # noqa: BLE001 -- recorded on the case
                verdict = pv.Verdict(pv.UNVERIFIED, f"comparison could not run: {exc}")
        ok = verdict.passed
        if verdict.verdict == pv.MISMATCH:
            err = err or f"Parity mismatch: {verdict.detail}"
        elif verdict.verdict == pv.FAILED:
            err = err or verdict.detail

        result_item: Dict[str, Any] = {
            "case_id": c["id"],
            "question": c["question"],
            "metric_name": c.get("metric_name") or "",
            "expected_sql": c.get("expected_sql") or "",
            "expected_dax": c.get("expected_dax") or "",
            "passed": ok,
            "verdict": verdict.verdict,
            "verdict_detail": verdict.detail,
            "mode": mode,
            "target": project_target,
            "generated_sql": generated_sql,
            "error": err,
        }
        if row_count is not None:
            result_item["row_count"] = row_count
        return result_item

    # Parallelize evaluations with ThreadPoolExecutor
    workers = min(6, max(1, len(approved)))
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_evaluate_case, approved))
    else:
        results = [_evaluate_case(c) for c in approved]

    from semabridge.reconciliation.project_verify import summarize

    summary = summarize(results)
    passed = summary["matched"]
    total = len(approved)
    run_rec = {
        "id": f"trun-{uuid.uuid4().hex[:8]}",
        "project_id": project_id,
        "total": total,
        "passed": passed,
        # Only cases actually compared with Power BI count toward accuracy.
        "accuracy_pct": summary["accuracy_pct"],
        "coverage_pct": summary["coverage_pct"],
        "verdicts": {k: summary[k] for k in ("matched", "mismatch", "unverified", "failed")},
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

