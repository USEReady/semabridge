"""
Production-readiness checks for the issues seen in the UI review (Nov 20).

Put this file in Tests/ and run:
    .venv\Scripts\python -m pytest Tests/test_production_readiness.py -q -o addopts="" -o log_cli=false

Each test states required behaviour. A failure is a finding: fix the code, not the test.
Sections:
  A. Every API path the frontend calls exists in the backend (no 404s such as /api/bi-truth/suite)
  B. No fake data: mock parity can never certify or register a verified query; no dummy UI defaults
  C. Platform-aware: Databricks projects are never checked/queried on Snowflake
  D. Cross-Platform preview never puts DAX in SQL targets or SQL in DAX targets
  E. Drift UI shows honest statuses (baseline captured, superseded hidden)
  F. Ask AI picks the engine from the project's platform and never executes refused plans
  G. Loading performance: no per-project request loops, one shared project list
"""
import inspect
import os
import pathlib
import re
import types

import pytest

os.environ.setdefault("SEMABRIDGE_ALLOW_LOCAL_DB", "true")

ROOT = pathlib.Path(__file__).resolve()
while not (ROOT / "src" / "semabridge").exists() and ROOT.parent != ROOT:
    ROOT = ROOT.parent
FRONT = ROOT / "frontend" / "src"


def _front_files():
    if not FRONT.exists():
        pytest.skip("frontend not present")
    return [p for p in FRONT.rglob("*.js*") if ".test." not in p.name and "node_modules" not in p.parts]


def _read(p):
    return p.read_text(encoding="utf-8", errors="ignore")


# =============================================================================
# A. Frontend API paths exist in the backend
# =============================================================================

def _backend_route_patterns():
    from semabridge.api.main import app

    paths = set(app.openapi().get("paths", {}))
    for r in getattr(app, "routes", []):
        if getattr(r, "path", None):
            paths.add(r.path)
    return sorted(re.sub(r"\{[^}]+\}", "X", p.rstrip("/")) for p in paths if p.startswith("/api"))


def _frontend_api_paths():
    found = {}
    for p in _front_files():
        for m in re.finditer(r"[`'\"](/api/[^`'\"\s?#]+)", _read(p)):
            raw = m.group(1)
            raw = re.sub(r"(?<!/)\$\{[^}]*\}", "", raw)          # appended query string
            path = re.sub(r"\$\{[^}]*\}", "X", raw).rstrip("/")  # path parameter
            found.setdefault(path, set()).add(str(p.relative_to(ROOT)))
    return found


FRONT_PATHS = _frontend_api_paths() if FRONT.exists() else {}


@pytest.mark.parametrize("path", sorted(FRONT_PATHS), ids=sorted(FRONT_PATHS))
def test_frontend_api_path_exists_in_backend(path):
    backend = _backend_route_patterns()
    rx = re.compile("^" + re.escape(path).replace("X", "[^/]+") + "$")
    exact = any(rx.match(b) for b in backend)
    prefix = any(b.startswith(path + "/") for b in backend)  # base URL extended in code
    assert exact or prefix, f"{path} is called by {sorted(FRONT_PATHS[path])} but no backend route matches (404)"


# =============================================================================
# B. No fake data
# =============================================================================

@pytest.fixture
def certify_env(monkeypatch):
    import semabridge.api.routers.reconciliation_router as rr
    import semabridge.ai.verified_queries_service as vqs

    saved = {"cert": [], "vq": []}
    monkeypatch.setattr(rr, "_validate_certify_request", lambda *a, **k: None)
    monkeypatch.setattr(rr, "save_certification_record", lambda **kw: saved["cert"].append(kw) or kw)
    monkeypatch.setattr(vqs, "save_verified_query_record", lambda **kw: saved["vq"].append(kw) or kw)
    return rr, saved


def _cert_req(rr, **kw):
    base = dict(project_id="proj-x", domain="D", metric_name="CASE_COUNT", view_metric="CASES.CASE_COUNT",
                metric_sql_expression="COUNT(*)", target_table_or_view="DB.S.V", dimensions=["CASES.STATUS"],
                register_verified_query=True)
    base.update(kw)
    return rr.CertifyMetricRequest(**base)


@pytest.mark.parametrize("flag", ["1", "true"])
def test_mock_parity_can_never_certify(certify_env, monkeypatch, flag):
    rr, saved = certify_env
    monkeypatch.setenv("SEMABRIDGE_MOCK_PARITY", flag)
    req = _cert_req(rr)
    try:
        out = rr.certify_metric_end_to_end(req, types.SimpleNamespace(state=types.SimpleNamespace(user_id=None)))
    except Exception:
        return  # refusing to certify on mock data is acceptable
    statuses = [c.get("status") for c in saved["cert"]]
    assert "CERTIFIED" not in statuses, "a mock parity result was persisted as CERTIFIED"
    assert not saved["vq"] and not out.get("verified_query_registered"), "mock parity registered a verified query"


@pytest.mark.parametrize("flag", ["1", "true"])
def test_mock_parity_with_power_bi_rows_still_cannot_certify(certify_env, monkeypatch, flag):
    rr, saved = certify_env
    monkeypatch.setenv("SEMABRIDGE_MOCK_PARITY", flag)
    req = _cert_req(rr, dax_results=[{"CASE_COUNT": 100, "CASES.STATUS": "Open"}])
    try:
        rr.certify_metric_end_to_end(req, types.SimpleNamespace(state=types.SimpleNamespace(user_id=None)))
    except Exception:
        return
    assert "CERTIFIED" not in [c.get("status") for c in saved["cert"]], "mock Snowflake rows certified against real Power BI rows"


DUMMY_UI = [
    (r"Segment (Alpha|Beta|Gamma|Delta|Epsilon)", "fake parity slices"),
    (r"useState\(\s*['\"]SELECT 1 AS OK['\"]\s*\)", "placeholder SQL as default state"),
    (r"Sarah Jenkins|VanArsdel|Executive Marketing", "demo names in UI"),
]


@pytest.mark.parametrize("pattern,why", DUMMY_UI, ids=[w for _, w in DUMMY_UI])
def test_no_dummy_data_in_frontend(pattern, why):
    hits = [str(p.relative_to(ROOT)) for p in _front_files() if re.search(pattern, _read(p))]
    assert not hits, f"{why}: {hits}"


def test_mock_parity_generator_not_reachable_without_flag(monkeypatch):
    from semabridge.reconciliation import mock_parity

    monkeypatch.delenv("SEMABRIDGE_MOCK_PARITY", raising=False)
    monkeypatch.delenv("SEMABRIDGE_MOCK_DRIFT", raising=False)
    assert mock_parity.is_mock_parity_enabled() is False


# =============================================================================
# C. Platform-aware: Databricks projects are never handled as Snowflake
# =============================================================================

def test_certification_does_not_hardcode_snowflake():
    import semabridge.api.routers.reconciliation_router as rr

    src = inspect.getsource(rr.certify_metric_end_to_end)
    assert not re.search(r"target\s*=\s*[\"']SNOWFLAKE[\"']", src), \
        "certify_metric_end_to_end hard-codes target='SNOWFLAKE' (Databricks projects certified/registered on Snowflake)"


@pytest.fixture
def databricks_project(monkeypatch):
    import semabridge.ai.project_connection as pc
    import semabridge.connectors.snowflake_connection as sc
    import semabridge.governance.drift_service as ds
    import snowflake.connector
    from semabridge.ontology.store import ontology_store

    calls = {"snowflake": 0}
    dom = types.SimpleNamespace(domain="D", id="D", semantic_view_name="semabridge.public.Model_metric_view",
                                source_project_id="proj-dbx", source_model_id=None,
                                metrics=[types.SimpleNamespace(name="M", sql_expression="SUM(T.X)", dax_expression="")])
    meta = {"id": "proj-dbx", "target": {"type": "databricks", "platform": "DATABRICKS", "connector_type": "DATABRICKS",
                                          "catalog": "semabridge", "schema": "public"},
            "target_platform": "databricks", "targets": [{"type": "databricks"}]}
    monkeypatch.setattr(pc, "_project_meta", lambda pid: meta)
    monkeypatch.setattr(ontology_store, "domains_for", lambda pid, *a, **k: [dom])
    monkeypatch.setattr(ontology_store, "get_federated_ontology", lambda *a, **k: types.SimpleNamespace(domains={"D": dom}))
    monkeypatch.setattr(pc, "resolve_project_warehouse_config", lambda *a, **k: {"account": "a", "host": "h"})
    monkeypatch.setattr(sc, "get_snowflake_connect_kwargs", lambda cfg: {"account": "a", "user": "u"})

    def _connect(**kw):
        calls["snowflake"] += 1
        raise RuntimeError("Snowflake must not be used for a Databricks project")

    monkeypatch.setattr(snowflake.connector, "connect", _connect)
    state = {"events": [], "schedules": []}
    monkeypatch.setattr(ds, "_load_state", lambda: {"events": list(state["events"]), "schedules": []})
    monkeypatch.setattr(ds, "_save_state", lambda d: state.update(events=list(d.get("events") or [])))
    monkeypatch.delenv("SEMABRIDGE_MOCK_DRIFT", raising=False)
    return ds, calls


def test_drift_check_for_databricks_project_never_uses_snowflake(databricks_project):
    ds, calls = databricks_project
    r = ds.run_drift_check(project_id="proj-dbx")
    assert calls["snowflake"] == 0, "drift check connected to Snowflake for a Databricks project"
    assert r.get("not_checked_reason") != "SNOWFLAKE_UNAVAILABLE", "Databricks project reported as 'Snowflake unavailable'"


DOUBLE_QUAL = re.compile(r'(\b[\w$]+\.[\w$]+)\.(?:")?\1\b', re.I)


@pytest.mark.parametrize("view", ['semabridge.public."semabridge.public.X_metric_view"', 'DB.S."DB.S.V"'])
def test_double_qualification_pattern_detects_the_bug(view):
    assert DOUBLE_QUAL.search(view)


def test_view_names_are_never_double_qualified_in_stored_domains():
    from semabridge.ontology.store import ontology_store

    bad = [d.semantic_view_name for d in ontology_store.get_federated_ontology().domains.values()
           if getattr(d, "semantic_view_name", None)
           and DOUBLE_QUAL.search(d.semantic_view_name)]
    assert not bad, f"double-qualified semantic view names stored: {bad}"


# =============================================================================
# D. Cross-Platform preview: no DAX in SQL targets, no SQL in DAX targets
# =============================================================================

DAX_TOKENS = re.compile(r"\b(SELECTEDVALUE|CALCULATE|SUMX|AVERAGEX|COUNTROWS|DIVIDE|RELATED|TOTALYTD|SAMEPERIODLASTYEAR)\s*\(|'[^']+'\[[^\]]+\]")


@pytest.fixture
def cross_env(monkeypatch):
    from semabridge.ontology.store import ontology_store

    metrics = [
        types.SimpleNamespace(name="ONLY_DAX", sql_expression=None, dax_expression="SELECTEDVALUE('Adj'[Value], 0)", description=""),
        types.SimpleNamespace(name="ONLY_SQL", sql_expression="SUM(T.X)", dax_expression=None, description=""),
        types.SimpleNamespace(name="BOTH", sql_expression="COUNT(*)", dax_expression="COUNTROWS('Cases')", description=""),
    ]
    dom = types.SimpleNamespace(domain="D", source_project_id="proj-x", metrics=metrics)
    monkeypatch.setattr(ontology_store, "get_federated_ontology", lambda *a, **k: types.SimpleNamespace(domains={"D": dom}))
    from semabridge.sync.cross_platform import preview_cross_platform_definitions

    return preview_cross_platform_definitions(project_id="proj-x")["previews"]


@pytest.mark.parametrize("target", ["snowflake", "databricks"])
def test_sql_targets_never_contain_dax(cross_env, target):
    d = cross_env[target]["definition"]
    assert not DAX_TOKENS.search(d), f"{target} definition contains DAX:\n{d}"


def test_untranslatable_metric_is_reported_not_emitted(cross_env):
    sf = cross_env["snowflake"]
    assert any("ONLY_DAX" in g for g in sf["gaps"])
    assert "ONLY_DAX AS" not in sf["definition"], "metric without SQL emitted into the Snowflake definition"


def test_fabric_never_contains_sql_fallback(cross_env):
    d = cross_env["fabric"]["definition"]
    assert "measure 'ONLY_SQL' = SUM(T.X)" not in d, "Fabric/TMDL definition contains SQL instead of DAX"


# =============================================================================
# E. Drift UI statuses
# =============================================================================

def _page(name):
    p = FRONT / "pages" / name
    if not p.exists():
        pytest.skip(f"{name} not present")
    return _read(p)


def test_drift_page_handles_baseline_captured():
    assert "BASELINE_CAPTURED" in _page("DriftMonitoringPage.jsx"), \
        "Drift page never handles status BASELINE_CAPTURED (shows 'In Sync' after a first check)"


def test_drift_page_handles_not_checked_status_from_backend():
    src = _page("DriftMonitoringPage.jsx")
    assert "NOT_CHECKED" in src and "not_checked_reason" in src, "Drift page does not show the backend's not-checked reason"


def test_drift_page_hides_superseded_events_by_default():
    assert re.search(r"superseded", _page("DriftMonitoringPage.jsx"), re.I), "superseded events are not handled in the Drift page"


def test_drift_empty_state_is_not_always_in_sync():
    src = _page("DriftMonitoringPage.jsx")
    m = re.search(r"All metrics in sync with Snowflake", src)
    assert not m, "hard-coded 'All metrics in sync with Snowflake' empty state (wrong for Databricks and for not-checked)"


# =============================================================================
# F. Ask AI
# =============================================================================

def _ask_src():
    files = [p for p in _front_files() if p.name in ("AskAiPage.jsx", "AnswerCard.jsx")]
    if not files:
        pytest.skip("Ask AI files not present")
    return "\n".join(_read(p) for p in files)


def test_ask_ai_engine_depends_on_project_platform():
    src = _ask_src()
    assert re.search(r"platform|target_type|targetPlatform|connector_type", src, re.I), \
        "Ask AI 'Run on' choice does not look at the project's platform (Cortex offered for Databricks projects)"


def test_ask_ai_never_executes_refused_plans():
    src = _ask_src()
    for m in re.finditer(r"(?:authFetch|fetch|post|request)\w*\(\s*[`'\"][^`'\"]*execute-query", src):
        window = src[max(0, m.start() - 1500): m.start()]
        assert re.search(r"refus|failed_check|blocked|!\s*\w*[sS]ql\b|\bsql\s*&&|if\s*\(\s*!\s*\w*sql", window), \
            "execute-query is called without checking for a refused plan / empty SQL (400 'SQL query cannot be empty')"


# =============================================================================
# G. Loading performance
# =============================================================================

def test_no_per_project_snapshot_request_loop():
    offenders = []
    for p in _front_files():
        src = _read(p)
        for m in re.finditer(r"/api/graph/\$\{[^}]+\}/snapshots", src):
            window = src[max(0, m.start() - 600): m.start()]
            if re.search(r"\.(map|forEach)\(|for\s*\(|Promise\.all", window):
                offenders.append(str(p.relative_to(ROOT)))
    assert not offenders, f"one snapshots request per project in a loop: {sorted(set(offenders))}"


def test_project_list_is_loaded_through_one_shared_source():
    users = sorted({str(p.relative_to(ROOT)) for p in _front_files() if re.search(r"/api/projects\?limit=", _read(p))})
    assert len(users) <= 1, f"project list fetched directly in {len(users)} files (use one shared hook): {users}"


def test_no_aggressive_global_polling():
    """Components mounted on every page (contexts, layouts, status bar) must not poll faster than
    every 10 s; page-local polling while a run is active is fine."""
    offenders = []
    glob_files = [p for p in _front_files()
                  if "context" in p.parts or "layouts" in p.parts or p.name in ("StatusBar.jsx", "useSyncStatus.js")]
    for p in glob_files:
        for m in re.finditer(r"setInterval\([^,]+,\s*(\d+)\s*\)", _read(p)):
            if int(m.group(1)) < 10000:
                offenders.append(f"{p.relative_to(ROOT)} ({m.group(1)} ms)")
    assert not offenders, f"polling faster than every 10 s: {offenders}"
