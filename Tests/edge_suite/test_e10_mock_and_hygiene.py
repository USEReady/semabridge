"""Production code must not ship demo data or let the client force it; drift never invents events."""
import pathlib
import re
import types

import pytest

ROOT = pathlib.Path(__file__).resolve()
while not (ROOT / "src" / "semabridge").exists() and ROOT.parent != ROOT:
    ROOT = ROOT.parent
SRC = ROOT / "src" / "semabridge"
FRONT = ROOT / "frontend" / "src"
PROD_PY = [p for p in SRC.rglob("*.py") if "/demo/" not in p.as_posix() and "fixtures" not in p.as_posix()]

DEMO_TOKENS = ["VanArsdel", "SALESFACT", "COMP_ANALYSIS", "proj-comp", "proj-sales", "drift-comp-",
               "REGIONS_ACTIVE_BOTH_FY", "Sarah Jenkins", "Executive Marketing"]


@pytest.mark.parametrize("token", DEMO_TOKENS)
def test_demo_tokens_are_not_in_production_python(token):
    hits = [str(p.relative_to(ROOT)) for p in PROD_PY if token in p.read_text(encoding="utf-8", errors="ignore")]
    assert not hits, f"'{token}' found in: {hits}"


FRONT_RULES = [
    (r"mock_mode\s*:\s*true", "frontend forces mock mode"),
    (r"SEMABRIDGE_WORKSPACE", "hard-coded target label"),
    (r"seed-mock", "UI calls the mock seeding endpoint"),
    (r">\s*MOCK\s*<|'MOCK'|\"MOCK\"", "mock badge visible to clients"),
    (r"drift-comp-", "UI knows demo event ids"),
    (r"'TOTAL_UNITS'|\"TOTAL_UNITS\"", "demo metric fallback"),
    (r"proj-comp", "demo project default"),
]


@pytest.mark.parametrize("pattern,why", FRONT_RULES, ids=[w for _, w in FRONT_RULES])
def test_frontend_has_no_mock_knowledge(pattern, why):
    if not FRONT.exists():
        pytest.skip("frontend not present")
    hits = [str(p.relative_to(ROOT)) for p in FRONT.rglob("*.js*")
            if ".test." not in p.name and re.search(pattern, p.read_text(encoding="utf-8", errors="ignore"))]
    assert not hits, f"{why}: {hits}"


ALLOW_WORDS = ["case", "cases", "role", "origin", "state", "months", "years"]


@pytest.mark.parametrize("word", ALLOW_WORDS)
def test_hardcoding_scanner_does_not_whitelist_model_words(word):
    scanner = ROOT / "ontology_tests" / "t_no_hardcoding.py"
    if not scanner.exists():
        scanner = ROOT / "Tests" / "ontology_tests" / "t_no_hardcoding.py"
    text = scanner.read_text(encoding="utf-8")
    assert not re.search(rf"[\"']{word}[\"']", text), f"'{word}' is allow-listed in {scanner.name}"


# ---- drift: Snowflake unavailable must never create events (real or mock) ----

@pytest.fixture
def drift_env(monkeypatch):
    import semabridge.governance.drift_service as ds
    import semabridge.ai.project_connection as pc
    import semabridge.connectors.snowflake_connection as sc
    import snowflake.connector
    from semabridge.ontology.store import ontology_store

    state = {"events": [], "schedules": []}
    dom = types.SimpleNamespace(domain="D", id="D", semantic_view_name="DB.S.V", source_project_id="proj-x",
                                source_model_id=None,
                                metrics=[types.SimpleNamespace(name="M", sql_expression="SUM(T.X)", dax_expression="")])
    monkeypatch.setattr(ontology_store, "domains_for", lambda pid, *a, **k: [dom])
    monkeypatch.setattr(ontology_store, "get_federated_ontology", lambda *a, **k: types.SimpleNamespace(domains={"D": dom}))
    monkeypatch.setattr(pc, "resolve_project_warehouse_config", lambda *a, **k: {"account": "a"})
    monkeypatch.setattr(sc, "get_snowflake_connect_kwargs", lambda cfg: {"account": "a", "user": "u"})

    def _down(**kw):
        raise RuntimeError("250001: could not connect")

    monkeypatch.setattr(snowflake.connector, "connect", _down)
    monkeypatch.setattr(ds, "_load_state", lambda: {"events": list(state["events"]), "schedules": []})
    monkeypatch.setattr(ds, "_save_state", lambda d: state.update(events=list(d.get("events") or [])))
    return ds, state, monkeypatch


@pytest.mark.parametrize("mock_flag", ["", "1", "true"])
def test_snowflake_unavailable_creates_no_events(drift_env, mock_flag):
    ds, state, mp = drift_env
    if mock_flag:
        mp.setenv("SEMABRIDGE_MOCK_DRIFT", mock_flag)
    else:
        mp.delenv("SEMABRIDGE_MOCK_DRIFT", raising=False)
    r = ds.run_drift_check(project_id="proj-x")
    assert r["status"] == "NOT_CHECKED" and r["not_checked_reason"] == "SNOWFLAKE_UNAVAILABLE"
    assert r["events"] == [] and state["events"] == [], "drift invented an event while Snowflake was unavailable"


@pytest.mark.parametrize("env", [{}, {"SEMABRIDGE_DEMO_MODE": "true"}])
def test_demo_events_never_appear_without_the_mock_flag(drift_env, env):
    ds, state, mp = drift_env
    mp.delenv("SEMABRIDGE_MOCK_DRIFT", raising=False)
    for k, v in env.items():
        mp.setenv(k, v)
    assert ds.get_default_mock_drift_events("proj-hr") == []
