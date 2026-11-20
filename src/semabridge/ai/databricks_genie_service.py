"""Databricks AI/BI Genie Space specification generator, deployer, and SQL execution helpers.

Builds a Databricks AI/BI Genie Space specification from a project's ontology,
deployed Unity Catalog Metric Views, and verified queries. When ``deploy=False``
it returns a preview specification with zero network calls; when ``deploy=True``
it publishes the Genie Space via ``POST https://{host}/api/2.0/genie/spaces``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import urllib.parse
from typing import Any, Dict, List, Optional

import requests

from semabridge.api.services.databricks_connection_service import (
    DatabricksAPIError,
    DatabricksRestClient,
)
from semabridge.utils.tls import requests_verify

logger = logging.getLogger("semabridge.ai.databricks_genie_service")

_CHECKSUM_MARKER_RE = re.compile(r"\[semabridge-checksum:([0-9a-fA-F]{8,64})\]")


class _RequestsMethodSession:
    """Thin session adapter for ``DatabricksRestClient`` that dispatches HTTP calls
    to ``requests.<method>`` while preserving ``DatabricksRestClient``'s TLS
    verification, timeouts, 429/5xx exponential backoff retries, 401/403
    classification, and secret redaction.
    """

    def __init__(
        self,
        requests_mod: Any = requests,
        *,
        verify_override: Any = None,
        strip_default_accept: bool = False,
    ) -> None:
        self._requests = requests_mod
        self._verify_override = verify_override
        self._strip_default_accept = strip_default_accept

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
        verify: Any = None,
    ) -> Any:
        eff_verify = self._verify_override if self._verify_override is not None else verify
        eff_headers = dict(headers or {})
        if self._strip_default_accept and eff_headers.get("Accept") == "application/json":
            eff_headers.pop("Accept", None)

        verb = method.upper()
        fn = getattr(self._requests, verb.lower(), None)
        if verb == "GET" and callable(fn):
            return fn(url, headers=eff_headers, params=params, timeout=timeout, verify=eff_verify)
        if verb in ("POST", "PATCH", "PUT") and callable(fn):
            kwargs: Dict[str, Any] = {
                "headers": eff_headers,
                "timeout": timeout,
                "verify": eff_verify,
            }
            if params is not None:
                kwargs["params"] = params
            if json is not None:
                kwargs["json"] = json
            return fn(url, **kwargs)
        return self._requests.request(
            verb,
            url,
            headers=eff_headers,
            params=params,
            json=json,
            timeout=timeout,
            verify=eff_verify,
        )


def create_databricks_rest_client(
    host: str,
    bearer_token: str,
    *,
    timeout: float = 15.0,
    max_retries: int = 3,
    verify_override: Any = None,
    requests_mod: Any = requests,
    strip_default_accept: bool = False,
) -> DatabricksRestClient:
    """Instantiate a unified ``DatabricksRestClient`` with TLS, retries, and redaction."""
    session = _RequestsMethodSession(
        requests_mod,
        verify_override=verify_override,
        strip_default_accept=strip_default_accept,
    )
    return DatabricksRestClient(
        host,
        bearer_token,
        timeout=timeout,
        max_retries=max_retries,
        session=session,
    )


def extract_genie_checksum(text: Optional[str]) -> Optional[str]:
    """Extract the ``[semabridge-checksum:<sha12>]`` marker from a string, if present."""
    match = _CHECKSUM_MARKER_RE.search(str(text or ""))
    return match.group(1).lower() if match else None


def _strip_checksum_marker(text: Optional[str]) -> str:
    return _CHECKSUM_MARKER_RE.sub("", str(text or "")).strip()


def compute_genie_spec_checksum(spec: Dict[str, Any]) -> str:
    """Compute a deterministic 12-char SHA-256 checksum over the canonical Genie spec."""
    canonical = {
        "title": spec.get("title") or "",
        "description": _strip_checksum_marker(spec.get("description")),
        "table_identifiers": spec.get("table_identifiers") or [],
        "sample_questions": spec.get("sample_questions") or [],
        "instructions": spec.get("instructions") or [],
        "sql_snippets": spec.get("sql_snippets") or [],
        "benchmarks": spec.get("benchmarks") or [],
    }
    raw = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _embed_checksum_marker(description: str, checksum: str) -> str:
    base = _strip_checksum_marker(description)
    marker = f"[semabridge-checksum:{checksum}]"
    return f"{base} {marker}".strip() if base else marker


def _normalize_serialized_json(raw: Any) -> str:
    if isinstance(raw, dict):
        return json.dumps(raw, sort_keys=True, separators=(",", ":"))
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
        return json.dumps(parsed, sort_keys=True, separators=(",", ":"))
    except ValueError:
        return text


def _has_manual_edit_conflict(
    remote_space: Dict[str, Any],
    spec: Dict[str, Any],
    expected_payload: Dict[str, Any],
) -> bool:
    """Return True if the remote Databricks Genie Space diverges from SemaBridge's checksum."""
    expected_checksum = str(spec.get("semabridge_checksum") or "").strip().lower()
    if not expected_checksum:
        return False

    remote_desc = str(remote_space.get("description") or "")
    remote_serialized = remote_space.get("serialized_space")
    remote_checksum = extract_genie_checksum(remote_desc) or extract_genie_checksum(
        str(remote_serialized or "")
    )
    if remote_checksum != expected_checksum:
        return True

    if remote_serialized:
        norm_remote = _normalize_serialized_json(remote_serialized)
        norm_expected = _normalize_serialized_json(expected_payload.get("serialized_space"))
        if norm_remote and norm_expected and norm_remote != norm_expected:
            return True

    return False


def normalize_databricks_host(host: Optional[str]) -> str:
    """Normalize a Databricks workspace host to bare hostname (no scheme or trailing slash)."""
    h = str(host or "").strip().rstrip("/")
    if h.startswith("https://"):
        h = h[len("https://"):]
    elif h.startswith("http://"):
        h = h[len("http://"):]
    return h.rstrip("/")


def _secret_value(val: Any) -> str:
    if val is None:
        return ""
    if hasattr(val, "get_secret_value"):
        return str(val.get_secret_value() or "").strip()
    return str(val).strip()


def resolve_databricks_token(
    db_cfg: Any,
    *,
    host: Optional[str] = None,
    verify: Any = None,
) -> str:
    """Resolve a bearer token from ``db_cfg`` across PAT, OAuth, and Service Principal modes."""
    if db_cfg is None:
        return ""

    norm_host = normalize_databricks_host(host or getattr(db_cfg, "host", ""))
    auth_type = str(getattr(db_cfg, "auth_type", "") or "pat").strip().lower()
    tls_verify = requests_verify() if verify is None else verify

    pat_token = _secret_value(getattr(db_cfg, "token", None))
    oauth_token = _secret_value(getattr(db_cfg, "access_token", None))
    client_id = str(getattr(db_cfg, "client_id", "") or "").strip()
    client_secret = _secret_value(getattr(db_cfg, "client_secret", None))

    def _exchange_sp_token() -> str:
        if not (norm_host and client_id and client_secret):
            return ""
        resp = requests.post(
            f"https://{norm_host}/oidc/v1/token",
            data={"grant_type": "client_credentials", "scope": "all-apis"},
            auth=(client_id, client_secret),
            timeout=15,
            verify=tls_verify,
        )
        if resp.status_code == 200:
            return str((resp.json() or {}).get("access_token") or "").strip()
        logger.warning(
            "Databricks OIDC token exchange failed with HTTP %s: %s",
            resp.status_code,
            resp.text[:200],
        )
        return ""

    # 1. Explicit Service Principal / OAuth M2M
    if auth_type in ("service_principal", "sp", "oauth_m2m", "client_credentials"):
        sp_token = _exchange_sp_token()
        if sp_token:
            return sp_token
        if oauth_token:
            return oauth_token
        if pat_token:
            return pat_token

    # 2. OAuth / Interactive U2M
    elif auth_type in ("oauth", "oauth_u2m", "interactive"):
        if oauth_token:
            return oauth_token
        if pat_token:
            return pat_token

    # 3. PAT (or unspecified default)
    else:
        if pat_token:
            return pat_token
        if oauth_token:
            return oauth_token
        if client_id and client_secret:
            sp_token = _exchange_sp_token()
            if sp_token:
                return sp_token

    # 4. Fallback to emitter's token resolver (e.g., interactive MSAL refresh via CredentialManager)
    try:
        from semabridge.connectors.factory import make_target_emitter

        emitter = make_target_emitter("databricks", db_cfg)
        resolved = emitter._resolve_token()
        if resolved:
            return str(resolved).strip()
    except Exception as exc:
        logger.debug("Databricks emitter token fallback failed: %s", exc)

    return ""


def resolve_databricks_warehouse_id(db_cfg: Any) -> str:
    """Return configured ``warehouse_id`` or auto-discover one from the Databricks workspace."""
    if db_cfg is None:
        return ""
    configured = str(getattr(db_cfg, "warehouse_id", "") or "").strip()
    if configured:
        return configured
    from semabridge.core.engine.deployment.databricks import _auto_discover_warehouse_id

    return str(_auto_discover_warehouse_id(db_cfg) or "").strip()


def execute_databricks_sql(
    db_cfg: Any,
    sql: str,
    *,
    wait_timeout: str = "15s",
    timeout: int = 20,
) -> Dict[str, Any]:
    """Execute a SQL statement against Databricks SQL Warehouse Statements API.

    Uses ``requests_verify()``, resolves PAT/OAuth/SP tokens, and auto-discovers
    ``warehouse_id`` when not explicitly configured.
    """
    host = normalize_databricks_host(getattr(db_cfg, "host", "")) if db_cfg else ""
    if not host:
        return {"success": False, "not_configured": True, "error": "missing Databricks host."}

    verify = requests_verify()
    token = resolve_databricks_token(db_cfg, host=host, verify=verify)
    if not token:
        return {"success": False, "not_configured": True, "error": "missing Databricks token."}

    warehouse_id = resolve_databricks_warehouse_id(db_cfg)
    if not warehouse_id:
        return {
            "success": False,
            "not_configured": True,
            "error": "missing Databricks SQL warehouse (none configured or auto-discovered).",
        }

    client = create_databricks_rest_client(
        host,
        token,
        timeout=timeout,
        verify_override=verify,
        requests_mod=requests,
    )
    try:
        data = client._request(
            "POST",
            "/api/2.0/sql/statements",
            json_body={"statement": sql, "warehouse_id": warehouse_id, "wait_timeout": wait_timeout},
            purpose="Databricks SQL Statements API",
        )
    except DatabricksAPIError as exc:
        logger.warning("Databricks Statements API error: %s", exc.message)
        err_msg = (
            f"Databricks API returned HTTP {exc.status_code}: {exc.message}"
            if exc.status_code
            else exc.message
        )
        return {
            "success": False,
            "error": err_msg,
            "warehouse_id": warehouse_id,
        }

    state = (data.get("status") or {}).get("state")
    if state and state not in ("SUCCEEDED",):
        err = ((data.get("status") or {}).get("error") or {}).get("message") or f"statement state {state}"
        return {"success": False, "error": err, "warehouse_id": warehouse_id}

    cols = [c.get("name") for c in (data.get("manifest") or {}).get("schema", {}).get("columns", [])]
    rows = (data.get("result") or {}).get("data_array", []) or []
    return {
        "success": True,
        "columns": cols,
        "rows": rows,
        "row_count": len(rows),
        "warehouse_id": warehouse_id,
    }


def _clean_uc_identifier(raw: Optional[str]) -> str:
    """Normalize a 3-part Unity Catalog identifier by removing surrounding quotes/backticks."""
    text = str(raw or "").strip()
    if not text:
        return ""
    parts = [p.strip().strip("`").strip('"') for p in text.split(".") if p.strip()]
    return ".".join(p for p in parts if p)


def _quote_uc_identifier(raw: str) -> str:
    parts = [p.strip().strip("`").strip('"') for p in str(raw or "").split(".") if p.strip()]
    return ".".join(f"`{p}`" for p in parts)


def _quote_db_ident(name: str) -> str:
    clean = str(name or "").strip().strip("`").strip('"')
    return f"`{clean}`"


def build_genie_space_spec(
    project_id: str,
    *,
    space_title: Optional[str] = None,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a Databricks AI/BI Genie Space specification without any network calls."""
    pid = str(project_id or "").strip()
    if not pid:
        raise ValueError("project_id is required to generate a Genie Space specification.")

    from semabridge.ai.project_connection import resolve_project_scope
    from semabridge.ai.verified_queries_service import list_verified_queries_for_project
    from semabridge.ontology.store import ontology_store

    scope = resolve_project_scope(pid)
    domains = list(ontology_store.domains_for(pid))

    # 1. Collect deployed Unity Catalog metric view identifiers
    table_identifiers: List[str] = []
    seen_tables: set[str] = set()

    def _add_table(raw_name: Optional[str]) -> None:
        cleaned = _clean_uc_identifier(raw_name)
        if cleaned and cleaned.casefold() not in seen_tables:
            seen_tables.add(cleaned.casefold())
            table_identifiers.append(cleaned)

    if scope.semantic_view:
        _add_table(scope.semantic_view)
    for view_name in (scope.domain_views or {}).values():
        _add_table(view_name)
    for dom in domains:
        _add_table(getattr(dom, "semantic_view_name", None))
        for m in getattr(dom, "metrics", None) or []:
            _add_table(getattr(m, "semantic_view_name", None))

    # 2. Collect verified queries from DB
    verified_queries: List[Dict[str, Any]] = []
    try:
        verified_queries = list_verified_queries_for_project(pid)
    except Exception as exc:
        logger.warning("Could not load verified queries for project %s: %s", pid, exc)

    # 3. Collect sample questions, instructions, and SQL snippets
    sample_questions: List[str] = []
    seen_questions: set[str] = set()

    def _add_question(q: Optional[str]) -> None:
        text = str(q or "").strip()
        if text and text.casefold() not in seen_questions:
            seen_questions.add(text.casefold())
            sample_questions.append(text)

    sql_snippets: List[Dict[str, Any]] = []
    for vq in verified_queries:
        q = str(vq.get("question") or "").strip()
        sql = str(vq.get("sql") or "").strip()
        if q:
            _add_question(q)
        if q and sql:
            sql_snippets.append(
                {
                    "id": vq.get("id") or f"vq-{len(sql_snippets) + 1}",
                    "name": vq.get("name") or q[:64],
                    "question": q,
                    "sql": sql,
                    "source": "verified_query",
                }
            )

    instructions: List[str] = [
        "Always query the governed Unity Catalog Metric Views listed in this Genie Space using MEASURE(`metric_name`) for metrics.",
        "Do not bypass metric views to aggregate raw fact tables unless explicitly instructed.",
    ]

    all_metrics = []
    all_dim_names: List[str] = []
    seen_dims: set[str] = set()

    for dom in domains:
        for concept in getattr(dom, "concepts", None) or []:
            for attr in getattr(concept, "attributes", None) or []:
                if getattr(attr, "is_hidden", False):
                    continue
                dtype = str(getattr(attr, "data_type", "") or "").upper()
                if re.search(r"\b(INT|BIGINT|SMALLINT|TINYINT|DECIMAL|NUMERIC|FLOAT|DOUBLE|REAL|NUMBER)\b", dtype):
                    continue
                aname = str(getattr(attr, "label", None) or getattr(attr, "name", "") or "").strip()
                if aname and aname.casefold() not in seen_dims and not aname.lower().endswith("_id"):
                    seen_dims.add(aname.casefold())
                    all_dim_names.append(aname)

        for pol in getattr(dom, "policies", None) or []:
            expr = str(getattr(pol, "filter_expression", "") or "").strip()
            pname = str(getattr(pol, "name", "") or "Policy").strip()
            if expr:
                instructions.append(f"Policy '{pname}': always apply filter {expr}.")

        for m in getattr(dom, "metrics", None) or []:
            if getattr(m, "is_hidden", False):
                continue
            all_metrics.append((dom, m))

    # Add metric definitions to instructions and generate sample questions + canonical MEASURE() snippets
    primary_view = table_identifiers[0] if table_identifiers else ""
    for idx, (dom, m) in enumerate(all_metrics[:15]):
        m_label = str(getattr(m, "label", None) or getattr(m, "name", "") or "").strip()
        m_ident = str(getattr(m, "logical_name", None) or getattr(m, "name", "") or "").strip()
        if not m_label or not m_ident:
            continue
        m_desc = str(getattr(m, "description", "") or "").strip()
        m_agg = str(getattr(m, "aggregation_type", "") or "").strip()
        if m_desc:
            instructions.append(f"Metric '{m_label}' ({m_ident}): {m_desc}")
        elif m_agg:
            instructions.append(f"Metric '{m_label}' ({m_ident}): {m_agg} aggregation.")

        dim = all_dim_names[idx % len(all_dim_names)] if all_dim_names else None
        q = f"What is {m_label} by {dim}?" if dim else f"What is total {m_label}?"
        if len(sample_questions) < 10:
            _add_question(q)

        view_for_metric = _clean_uc_identifier(
            getattr(m, "semantic_view_name", None)
            or getattr(dom, "semantic_view_name", None)
            or primary_view
        )
        if view_for_metric and len(sql_snippets) < 15:
            q_view = _quote_uc_identifier(view_for_metric)
            q_met = _quote_db_ident(m_ident)
            if dim:
                q_dim = _quote_db_ident(dim)
                snippet_sql = (
                    f"SELECT {q_dim}, MEASURE({q_met}) AS {q_met} "
                    f"FROM {q_view} GROUP BY {q_dim} ORDER BY {q_met} DESC LIMIT 100"
                )
            else:
                snippet_sql = f"SELECT MEASURE({q_met}) AS {q_met} FROM {q_view} LIMIT 100"
            sql_snippets.append(
                {
                    "id": f"metric-{idx + 1}",
                    "name": m_label,
                    "question": q,
                    "sql": snippet_sql,
                    "source": "ontology_metric",
                }
            )

    # 4. Collect benchmarks from BI Truth cases/runs and verified queries
    benchmarks: List[Dict[str, Any]] = []
    seen_benchmarks: set[str] = set()

    def _add_benchmark(
        question: Optional[str],
        sql: Optional[str],
        expected_answer: Optional[str],
        source: str,
    ) -> None:
        q_text = str(question or "").strip()
        sql_text = str(sql or "").strip()
        if not (q_text and sql_text):
            return
        key = q_text.casefold()
        if key in seen_benchmarks:
            return
        seen_benchmarks.add(key)
        ans = str(expected_answer).strip() if expected_answer is not None and str(expected_answer).strip() else None
        benchmarks.append(
            {
                "question": q_text,
                "sql": sql_text,
                "expected_answer": ans,
                "source": source,
            }
        )

    try:
        from semabridge.ai.bi_truth_service import list_truth_cases, list_truth_runs

        truth_cases = list_truth_cases(pid) or []
        truth_runs = list_truth_runs(pid) or []
        run_results_by_case: Dict[str, Dict[str, Any]] = {}
        run_results_by_question: Dict[str, Dict[str, Any]] = {}
        if truth_runs:
            latest_run = truth_runs[0]
            for res in latest_run.get("results") or []:
                if not isinstance(res, dict):
                    continue
                cid = str(res.get("case_id") or res.get("id") or "").strip()
                rq = str(res.get("question") or "").strip().casefold()
                if cid and cid not in run_results_by_case:
                    run_results_by_case[cid] = res
                if rq and rq not in run_results_by_question:
                    run_results_by_question[rq] = res

        for tc in truth_cases:
            if not isinstance(tc, dict):
                continue
            if str(tc.get("status") or "").strip().lower() == "rejected":
                continue
            q_text = str(tc.get("question") or "").strip()
            cid = str(tc.get("id") or "").strip()
            run_hit = run_results_by_case.get(cid) or run_results_by_question.get(q_text.casefold()) or {}
            sql_text = str(tc.get("expected_sql") or run_hit.get("generated_sql") or "").strip()
            exp_ans = tc.get("expected_answer")
            if exp_ans is None and run_hit:
                if run_hit.get("expected_answer") is not None:
                    exp_ans = run_hit.get("expected_answer")
                elif run_hit.get("sample_rows"):
                    exp_ans = json.dumps(run_hit["sample_rows"][:3])
                elif run_hit.get("rows"):
                    exp_ans = json.dumps(run_hit["rows"][:3])
            _add_benchmark(q_text, sql_text, exp_ans, "bi_truth")
    except Exception as exc:
        logger.warning("Could not load BI Truth benchmarks for project %s: %s", pid, exc)

    for vq in verified_queries:
        _add_benchmark(
            vq.get("question"),
            vq.get("sql"),
            vq.get("expected_answer"),
            "verified_query",
        )

    model_label = scope.model_name or (domains[0].domain if domains else pid)
    resolved_title = str(space_title or "").strip() or f"{model_label} — Genie Space"
    base_desc = str(description or "").strip() or (
        f"SemaBridge governed Databricks AI/BI Genie Space for project '{pid}' "
        f"({len(table_identifiers)} metric view(s), {len(all_metrics)} metric(s), "
        f"{len(verified_queries)} verified quer{'y' if len(verified_queries) == 1 else 'ies'})."
    )

    spec: Dict[str, Any] = {
        "title": resolved_title,
        "description": base_desc,
        "project_id": pid,
        "table_identifiers": table_identifiers,
        "sample_questions": sample_questions[:10],
        "instructions": instructions,
        "sql_snippets": sql_snippets,
        "benchmarks": benchmarks,
        "verified_queries": [
            {
                "id": vq.get("id"),
                "name": vq.get("name"),
                "question": vq.get("question"),
                "sql": vq.get("sql"),
            }
            for vq in verified_queries
        ],
    }
    checksum = compute_genie_spec_checksum(spec)
    spec["semabridge_checksum"] = checksum
    spec["description"] = _embed_checksum_marker(base_desc, checksum)
    return spec


def _build_genie_api_payload(spec: Dict[str, Any], warehouse_id: str) -> Dict[str, Any]:
    serialized_space: Dict[str, Any] = {
        "version": 1,
        "data_sources": {
            "tables": [{"identifier": t} for t in spec.get("table_identifiers") or []],
        },
        "instructions": {
            "text_instructions": [
                {"id": f"inst-{i + 1}", "content": [line]}
                for i, line in enumerate(spec.get("instructions") or [])
            ],
            "example_question_sqls": [
                {
                    "id": str(s.get("id") or f"sql-{i + 1}"),
                    "question": [s["question"]],
                    "sql": [s["sql"]],
                }
                for i, s in enumerate(spec.get("sql_snippets") or [])
                if s.get("question") and s.get("sql")
            ],
        },
        "config": {
            "sample_questions": [
                {"id": f"sq-{i + 1}", "question": [q]}
                for i, q in enumerate(spec.get("sample_questions") or [])
            ],
        },
    }
    benchmarks = spec.get("benchmarks") or []
    if benchmarks:
        serialized_space["benchmarks"] = {
            "questions": [
                {
                    "id": f"bm-{i + 1}",
                    "question": [b["question"]],
                    "answer": [
                        {
                            "format": "SQL",
                            "content": [b["sql"]],
                        }
                    ],
                }
                for i, b in enumerate(benchmarks)
                if b.get("question") and b.get("sql")
            ]
        }
    return {
        "title": spec["title"],
        "description": spec["description"],
        "warehouse_id": warehouse_id,
        "table_identifiers": spec.get("table_identifiers") or [],
        "serialized_space": json.dumps(serialized_space),
    }


def generate_genie_space(
    project_id: str,
    *,
    space_title: Optional[str] = None,
    description: Optional[str] = None,
    deploy: bool = False,
    owner_id: Optional[str] = None,
    space_id: Optional[str] = None,
    force_overwrite: bool = False,
) -> Dict[str, Any]:
    """Generate a Databricks AI/BI Genie Space spec and optionally deploy/update it.

    When ``deploy=False``, returns ``{"status": "preview", "spec": ...}`` with
    zero network calls. When ``deploy=True``, publishes via ``DatabricksRestClient``
    (``POST /api/2.0/genie/spaces`` for new spaces or ``PATCH /api/2.0/genie/spaces/{space_id}``
    for existing spaces with a manual-edit guard unless ``force_overwrite=True``).
    """
    spec = build_genie_space_spec(
        project_id,
        space_title=space_title,
        description=description,
    )
    target_space_id = str(space_id or "").strip() or None
    if not deploy:
        return {
            "status": "preview",
            "space_id": target_space_id,
            "space_url": None,
            "spec": spec,
        }

    from semabridge.ai.project_connection import resolve_project_warehouse_config

    db_cfg = resolve_project_warehouse_config(project_id, "DATABRICKS", owner_id=owner_id)
    if db_cfg is None:
        raise ValueError("Databricks connection is not configured for this project.")

    host = normalize_databricks_host(getattr(db_cfg, "host", ""))
    if not host:
        raise ValueError("Databricks workspace host is missing.")

    verify = requests_verify()
    token = resolve_databricks_token(db_cfg, host=host, verify=verify)
    if not token:
        raise ValueError("Databricks authentication token could not be resolved.")

    warehouse_id = resolve_databricks_warehouse_id(db_cfg)
    if not warehouse_id:
        raise ValueError(
            "No Databricks SQL Warehouse ID is configured and none could be auto-discovered."
        )

    payload = _build_genie_api_payload(spec, warehouse_id)
    client = create_databricks_rest_client(
        host,
        token,
        timeout=30,
        verify_override=verify,
        requests_mod=requests,
    )

    try:
        if target_space_id:
            encoded_id = urllib.parse.quote(target_space_id, safe="")
            if not force_overwrite:
                remote_space = client._request(
                    "GET",
                    f"/api/2.0/genie/spaces/{encoded_id}",
                    purpose="Databricks Genie Space manual-edit check",
                )
                if _has_manual_edit_conflict(remote_space, spec, payload):
                    return {
                        "status": "conflict_manual_edit",
                        "space_id": target_space_id,
                        "message": (
                            "Genie space was edited manually in Databricks. "
                            "Pass force_overwrite=true to overwrite."
                        ),
                        "spec": spec,
                    }
            data = client._request(
                "PATCH",
                f"/api/2.0/genie/spaces/{encoded_id}",
                json_body=payload,
                purpose="Databricks Genie Space update",
            )
        else:
            data = client._request(
                "POST",
                "/api/2.0/genie/spaces",
                json_body=payload,
                purpose="Databricks Genie Space create",
            )
    except DatabricksAPIError as exc:
        raise RuntimeError(
            f"Databricks Genie Space API returned HTTP {exc.status_code}: {exc.message}"
            if exc.status_code
            else f"Databricks Genie Space API error: {exc.message}"
        ) from exc

    resolved_space_id = str(data.get("space_id") or data.get("id") or target_space_id or "").strip()
    space_url = str(data.get("space_url") or "").strip() or (
        f"https://{host}/genie/rooms/{resolved_space_id}"
        if resolved_space_id
        else f"https://{host}/genie"
    )
    return {
        "status": "deployed",
        "space_id": resolved_space_id,
        "space_url": space_url,
        "warehouse_id": warehouse_id,
        "spec": spec,
    }


def run_genie_evaluation(
    project_id: str,
    space_id: str,
    *,
    owner_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run benchmark evaluation for a deployed Databricks AI/BI Genie Space."""
    pid = str(project_id or "").strip()
    sid = str(space_id or "").strip()
    if not pid:
        raise ValueError("project_id is required to run a Genie Space evaluation.")
    if not sid:
        raise ValueError("space_id is required to run a Genie Space evaluation.")

    from semabridge.ai.project_connection import resolve_project_warehouse_config

    db_cfg = resolve_project_warehouse_config(pid, "DATABRICKS", owner_id=owner_id)
    if db_cfg is None:
        raise ValueError("Databricks connection is not configured for this project.")

    host = normalize_databricks_host(getattr(db_cfg, "host", ""))
    if not host:
        raise ValueError("Databricks workspace host is missing.")

    verify = requests_verify()
    token = resolve_databricks_token(db_cfg, host=host, verify=verify)
    if not token:
        raise ValueError("Databricks authentication token could not be resolved.")

    spec = build_genie_space_spec(pid)
    benchmarks = spec.get("benchmarks") or []

    client = create_databricks_rest_client(
        host,
        token,
        timeout=30,
        verify_override=verify,
        requests_mod=requests,
    )
    encoded_id = urllib.parse.quote(sid, safe="")
    try:
        data = client._request(
            "POST",
            f"/api/2.0/genie/spaces/{encoded_id}/benchmarks/eval",
            json_body={"benchmarks": benchmarks},
            purpose="Databricks Genie Space benchmark evaluation",
        )
    except DatabricksAPIError as exc:
        raise RuntimeError(
            f"Databricks Genie Evaluation API returned HTTP {exc.status_code}: {exc.message}"
            if exc.status_code
            else f"Databricks Genie Evaluation API error: {exc.message}"
        ) from exc

    eval_run_id = str(data.get("eval_run_id") or data.get("run_id") or data.get("id") or "").strip() or None
    raw_results = data.get("results") if isinstance(data.get("results"), list) else (
        data.get("benchmark_results") if isinstance(data.get("benchmark_results"), list) else []
    )
    results = list(raw_results)

    if data.get("total_benchmarks") is not None:
        total_benchmarks = int(data["total_benchmarks"])
    elif results:
        total_benchmarks = len(results)
    else:
        total_benchmarks = len(benchmarks)

    if data.get("passed") is not None:
        passed = int(data["passed"])
    else:
        passed = sum(
            1
            for r in results
            if isinstance(r, dict)
            and (
                r.get("passed") is True
                or str(r.get("status") or "").strip().upper() in ("PASSED", "PASS", "SUCCEEDED", "CORRECT")
            )
        )

    if data.get("score_pct") is not None:
        score_pct = round(float(data["score_pct"]), 2)
    elif data.get("accuracy") is not None:
        acc = float(data["accuracy"])
        score_pct = round(acc * 100.0 if acc <= 1.0 else acc, 2)
    elif total_benchmarks > 0:
        score_pct = round((passed / total_benchmarks) * 100.0, 2)
    else:
        score_pct = 0.0

    status = str(data.get("status") or "completed").strip().lower()
    return {
        "status": status,
        "space_id": sid,
        "eval_run_id": eval_run_id,
        "total_benchmarks": total_benchmarks,
        "passed": passed,
        "score_pct": score_pct,
        "results": results,
    }
