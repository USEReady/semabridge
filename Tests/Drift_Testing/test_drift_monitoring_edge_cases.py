"""
Drift Monitoring: behaviour and edge cases.

Drop into Tests/ of the SemaBridge repo. Runs fully offline: the ontology store,
Snowflake connection, blast-radius simulator, event storage and webhook are
replaced by in-memory fakes, so every test is deterministic.

Contract under test (governance.drift_service.run_drift_check):
  result["status"]               "IN_SYNC" | "DRIFT_DETECTED" | "NOT_CHECKED" | "PARTIAL"
  result["not_checked_reason"]   None | "NO_ONTOLOGY" | "SNOWFLAKE_UNAVAILABLE" |
                                 "NO_DEPLOYED_VIEW" | "NO_COMPARABLE_METRICS"
  result["checked_metrics"]      metrics actually compared against a live view
  result["not_checked"]          [{"domain", "view", "reason"}] per domain/view not compared
  result["skipped_placeholders"] metrics deployed as an intentional NULL placeholder
  result["drift_detected"], result["events"]
Event drift_type: "MISSING_IN_SNOWFLAKE" | "EXPRESSION_MISMATCH" | "NULL_IN_SNOWFLAKE" |
                  "UNMANAGED_IN_SNOWFLAKE"

Rule behind every status test: no comparison means no clean status.

Tests marked "CURRENT GAP" describe required behaviour the code does not have yet;
they fail until the gap is fixed. Do not mark them xfail to make the suite green.
"""
import re
import types
from pathlib import Path

import pytest

import semabridge.governance.drift_service as ds


# =============================================================================
# Fakes
# =============================================================================

def metric(name, sql, dax=""):
    return types.SimpleNamespace(name=name, sql_expression=sql, dax_expression=dax)


def domain(name, view, metrics, project="proj-test", model=None, did=None):
    return types.SimpleNamespace(
        domain=name, id=did or f"id-{name}", semantic_view_name=view,
        source_project_id=project, source_model_id=model, metrics=list(metrics),
    )


class FakeCursor:
    def __init__(self, views, fail_views):
        self.views, self.fail_views = views, fail_views
        self.description = None
        self._rows = []
        self.described = []

    def execute(self, sql):
        m = re.match(r"\s*DESCRIBE SEMANTIC VIEW\s+(.+)$", sql, re.I)
        assert m, f"unexpected SQL: {sql}"
        view = m.group(1).strip()
        self.described.append(view)
        if view in self.fail_views:
            raise RuntimeError(f"002003: Semantic view '{view}' does not exist or not authorized")
        self.description = [("object_kind",), ("object_name",), ("property",), ("property_value",)]
        self._rows = [("METRIC", n, "EXPRESSION", e) for n, e in self.views.get(view, {}).items()]

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def close(self):
        pass


class Env:
    """Wires all fakes into drift_service for one test."""

    def __init__(self, monkeypatch):
        self.mp = monkeypatch
        self.domains = []
        self.views = {}          # view -> {METRIC_NAME: expression}
        self.fail_views = set()
        self.connect_fails = False
        self.no_warehouse = False
        self.supply_baseline = True
        self.state = {"events": [], "schedules": [], "baselines": {}}
        self.webhooks = []
        self.cursor = FakeCursor(self.views, self.fail_views)
        self.real_send_webhook = ds._send_webhook

        from semabridge.ontology.store import ontology_store
        import semabridge.ai.project_connection as pc
        import semabridge.connectors.snowflake_connection as sc
        import semabridge.governance.blast_radius as br
        import snowflake.connector

        monkeypatch.setattr(ontology_store, "domains_for",
                            lambda pid, *a, **k: [d for d in self.domains
                                                  if _pkey(d.source_project_id) == _pkey(pid)])
        monkeypatch.setattr(ontology_store, "get_federated_ontology",
                            lambda *a, **k: types.SimpleNamespace(
                                domains={d.domain: d for d in self.domains}))
        monkeypatch.setattr(pc, "resolve_project_warehouse_config",
                            lambda *a, **k: None if self.no_warehouse else {"account": "acct"})
        monkeypatch.setattr(sc, "get_snowflake_connect_kwargs",
                            lambda cfg: {"account": "acct", "user": "u"})

        def _connect(**kw):
            if self.connect_fails:
                raise RuntimeError("250001: Could not connect to Snowflake backend")
            return FakeConn(self.cursor)

        monkeypatch.setattr(snowflake.connector, "connect", _connect)

        class _Sim:
            def __init__(self, *a, **k):
                pass

            def simulate(self, **k):
                return types.SimpleNamespace(affected_metrics=[], affected_bi_reports=[])

        monkeypatch.setattr(br, "BlastRadiusSimulator", _Sim)
        monkeypatch.setattr(ds, "_load_state", lambda: {
            "events": [dict(e) for e in self.state["events"]],
            "schedules": list(self.state["schedules"]),
            "baselines": dict(self.state.get("baselines", {})),
        })
        monkeypatch.setattr(ds, "_save_state", lambda data: self.state.update(
            events=[dict(e) for e in data.get("events") or []],
            baselines=dict(data.get("baselines") or {}),
        ))
        monkeypatch.setattr(ds, "_update_event_row", lambda ev: False)
        monkeypatch.setattr(ds, "_send_webhook", lambda url, payload: self.webhooks.append((url, payload)))
        monkeypatch.delenv("SEMABRIDGE_MOCK_DRIFT", raising=False)

    def check(self, project="proj-test", **kw):
        if getattr(self, "supply_baseline", True):
            for dom in self.domains:
                sv = getattr(dom, "semantic_view_name", None)
                if not sv:
                    continue
                v_key = ds._view_key(sv)
                pid = getattr(dom, "source_project_id", project)
                p_key = _pkey(pid)
                p_bases = self.state.setdefault("baselines", {}).setdefault(p_key, {})
                if v_key not in p_bases:
                    base_metrics = {
                        str(m.name).upper(): str(m.sql_expression or m.dax_expression or "").strip()
                        for m in (getattr(dom, "metrics", []) or [])
                    }
                    if base_metrics:
                        p_bases[v_key] = {
                            "project_id": pid,
                            "view_name": sv,
                            "semantic_view": sv,
                            "metrics": base_metrics,
                            "deployed_at": "2026-01-01T00:00:00Z",
                        }
        return ds.run_drift_check(project_id=project, **kw)

    def drift_types(self, result):
        return sorted((e["metric_name"], e["drift_type"]) for e in result["events"])


def _pkey(v):
    return str(v or "").strip().lower().removeprefix("proj-")


@pytest.fixture
def env(monkeypatch):
    return Env(monkeypatch)


def one_view(env, sf, onto, view="DB.S.V1", project="proj-test"):
    """sf/onto: {METRIC: expression}. Ontology names are the deployed names."""
    env.views[view] = dict(sf)
    env.domains.append(domain("D1", view, [metric(n, e) for n, e in onto.items()], project=project))
    if getattr(env, "supply_baseline", True):
        p_key = _pkey(project)
        v_key = ds._view_key(view)
        base_metrics = {str(k).upper(): str(v).strip() for k, v in onto.items()}
        env.state.setdefault("baselines", {}).setdefault(p_key, {})[v_key] = {
            "project_id": project,
            "view_name": view,
            "semantic_view": view,
            "metrics": base_metrics,
            "deployed_at": "2026-01-01T00:00:00Z",
        }


# =============================================================================
# 1. Trust: never "in sync" without a comparison
# =============================================================================

class TestStatusTrust:

    def test_clean_when_every_metric_matches(self, env):
        one_view(env, {"A": "SUM(T.X)", "B": "COUNT(*)"}, {"A": "SUM(T.X)", "B": "COUNT(*)"})
        r = env.check()
        assert r["checked_metrics"] == 2
        assert r["drift_detected"] == 0
        assert r["status"] == "IN_SYNC"                                      # CURRENT GAP: no status field

    def test_project_without_ontology_domain_is_not_checked(self, env):
        r = env.check(project="proj-has-no-domain")
        assert r["checked_metrics"] == 0
        assert r["status"] == "NOT_CHECKED"                                  # CURRENT GAP
        assert r["not_checked_reason"] == "NO_ONTOLOGY"

    def test_snowflake_connection_failure_is_not_checked(self, env):
        one_view(env, {"A": "SUM(T.X)"}, {"A": "SUM(T.Y)"})
        env.connect_fails = True
        r = env.check()
        assert r["snowflake_connected"] is False
        assert r["status"] == "NOT_CHECKED"                                  # CURRENT GAP
        assert r["not_checked_reason"] == "SNOWFLAKE_UNAVAILABLE"

    def test_no_warehouse_configured_is_not_checked(self, env):
        one_view(env, {"A": "SUM(T.X)"}, {"A": "SUM(T.X)"})
        env.no_warehouse = True
        r = env.check()
        assert r["status"] == "NOT_CHECKED"                                  # CURRENT GAP
        assert r["not_checked_reason"] == "SNOWFLAKE_UNAVAILABLE"

    def test_describe_failure_marks_that_view_not_checked(self, env):
        one_view(env, {"A": "SUM(T.X)"}, {"A": "SUM(T.X)"}, view="DB.S.GOOD")
        env.views["DB.S.BROKEN"] = {}
        env.fail_views.add("DB.S.BROKEN")
        env.domains.append(domain("D2", "DB.S.BROKEN", [metric("B", "SUM(T.Z)")]))
        r = env.check()
        assert r["checked_metrics"] == 1                                     # only the good view's metric
        assert r["status"] == "PARTIAL"                                      # CURRENT GAP
        assert {"domain": "D2", "view": "DB.S.BROKEN"}.items() <= r["not_checked"][0].items()

    def test_domain_without_deployed_view_is_not_checked(self, env):
        env.domains.append(domain("D1", None, [metric("A", "SUM(T.X)")]))
        r = env.check()
        assert r["checked_metrics"] == 0                                     # CURRENT GAP: counted today
        assert r["status"] == "NOT_CHECKED"
        assert r["not_checked_reason"] == "NO_DEPLOYED_VIEW"

    def test_domain_with_no_metrics_is_not_checked(self, env):
        env.views["DB.S.V1"] = {"A": "SUM(T.X)"}
        env.domains.append(domain("D1", "DB.S.V1", []))
        r = env.check()
        assert r["status"] == "NOT_CHECKED"                                  # CURRENT GAP
        assert r["not_checked_reason"] == "NO_COMPARABLE_METRICS"

    def test_checked_count_only_counts_real_comparisons(self, env):
        one_view(env, {"A": "SUM(T.X)"}, {"A": "SUM(T.X)"})
        env.connect_fails = True
        assert env.check()["checked_metrics"] == 0                           # CURRENT GAP: counts 1 today


# =============================================================================
# 2. NULL handling: only real placeholders are skipped
# =============================================================================

class TestNullHandling:

    @pytest.mark.parametrize("old,new", [
        ("SUM(T.A) / NULLIF(SUM(T.B), 0)", "SUM(T.A) / NULLIF(SUM(T.C), 0)"),
        ("COUNT(CASE WHEN T.D IS NULL THEN 1 END)", "COUNT(CASE WHEN T.D IS NOT NULL THEN 1 END)"),
        ("SUM(CASE WHEN T.S = 'Won' THEN T.V ELSE NULL END)", "SUM(CASE WHEN T.S = 'Lost' THEN T.V ELSE NULL END)"),
        ("COALESCE(SUM(T.X), 0)", "COALESCE(SUM(T.X), NULL)"),
    ], ids=["nullif", "is_null", "else_null", "coalesce_null"])
    def test_real_change_in_expression_containing_null_is_detected(self, env, old, new):
        one_view(env, {"M": new}, {"M": old})
        r = env.check()
        assert ("M", "EXPRESSION_MISMATCH") in env.drift_types(r)           # CURRENT GAP: skipped today

    @pytest.mark.parametrize("placeholder", ["NULL", "CAST(NULL AS DOUBLE)", "cast( null as number )",
                                             "NULL::FLOAT"])
    def test_intentional_null_placeholder_with_no_source_sql_is_not_drift(self, env, placeholder):
        one_view(env, {"M": placeholder}, {"M": ""})
        r = env.check()
        assert r["drift_detected"] == 0
        assert r.get("skipped_placeholders", []) == ["M"]                    # CURRENT GAP: not reported

    def test_metric_with_sql_deployed_as_null_is_drift(self, env):
        one_view(env, {"M": "CAST(NULL AS DOUBLE)"}, {"M": "SUM(T.X)"})
        r = env.check()
        assert ("M", "NULL_IN_SNOWFLAKE") in env.drift_types(r)              # CURRENT GAP: silently skipped


# =============================================================================
# 3. Expression comparison: formatting ignored, meaning never ignored
# =============================================================================

class TestComparison:

    @pytest.mark.parametrize("onto,sf", [
        ("SUM(T.X)", 'SUM("T"."X")'),
        ("SUM(T.X)", "sum( t.x )"),
        ("SUM(T.X)", "SUM(T.X) -- deployed by SemaBridge"),
        ("SUM(T.X)", "SUM(T.X) /* note */"),
        ("SUM(T.X)\n", "  SUM(T.X)"),
    ], ids=["quotes", "case_whitespace", "line_comment", "block_comment", "newlines"])
    def test_formatting_only_differences_are_not_drift(self, env, onto, sf):
        # CURRENT GAP (case_whitespace): spaces inside parentheses "( x )" vs "(x)" give a false drift
        one_view(env, {"M": sf}, {"M": onto})
        assert env.check()["drift_detected"] == 0

    @pytest.mark.parametrize("onto,sf", [
        ("COUNT(DISTINCT ACCOUNTS.REGION)", "COUNT(DISTINCT CONTACTS.REGION)"),
        ("SUM(T.X::FLOAT) / SUM(T.Y)", "SUM(T.X::INT) / SUM(T.Y)"),
        ("MAX(CAST(T.A AS NUMBER))", "MAX(CAST(T.A AS VARCHAR))"),
        ("SUM(CASE WHEN T.S = 'Won' THEN T.V END)", "SUM(CASE WHEN T.S = 'won' THEN T.V END)"),
        ("SUM(T.PRODUCT_COST)", "SUM(T.COST)"),
    ], ids=["different_table", "float_to_int", "cast_type", "literal_case", "prefix_stripping"])
    def test_meaningful_changes_hidden_by_normalisation_are_detected(self, env, onto, sf):
        one_view(env, {"M": sf}, {"M": onto})
        assert ("M", "EXPRESSION_MISMATCH") in env.drift_types(env.check())  # CURRENT GAP: normalised away

    @pytest.mark.parametrize("onto,sf", [
        ("SUM(T.X) * 0.75", "SUM(T.X) * 0.7"),
        ("SUM(T.X)", "SUM(T.X) * 1.18"),
        ("SUM(T.X)", "AVG(T.X)"),
        ("COUNT(T.ID)", "COUNT(DISTINCT T.ID)"),
        ("SUM(CASE WHEN T.S = 'Open' THEN T.V END)", "SUM(T.V)"),
    ], ids=["constant", "multiplier", "aggregation", "distinct", "filter_removed"])
    def test_obvious_changes_are_detected(self, env, onto, sf):
        one_view(env, {"M": sf}, {"M": onto})
        assert ("M", "EXPRESSION_MISMATCH") in env.drift_types(env.check())

    def test_no_model_specific_table_names_in_comparison_code(self):
        src = Path(ds.__file__).read_text(encoding="utf-8")
        start = src.index("def _normalize_metric_expr")
        body = src[start:src.index("\ndef ", start + 10)]
        for name in ("salesfact", "sentiment", "geo", "vanarsdel"):
            assert name not in body.lower(), f"model-specific name '{name}' in _normalize_metric_expr"


# =============================================================================
# 4. Missing, unmanaged and name matching
# =============================================================================

class TestPresence:

    def test_metric_missing_in_snowflake(self, env):
        one_view(env, {"A": "SUM(T.X)"}, {"A": "SUM(T.X)", "B": "SUM(T.Y)"})
        assert ("B", "MISSING_IN_SNOWFLAKE") in env.drift_types(env.check())

    def test_metric_without_sql_missing_in_snowflake_is_not_drift(self, env):
        one_view(env, {"A": "SUM(T.X)"}, {"A": "SUM(T.X)", "B": ""})
        assert env.check()["drift_detected"] == 0

    def test_metric_only_in_snowflake_is_reported_as_unmanaged(self, env):
        one_view(env, {"A": "SUM(T.X)", "HOTFIX": "SUM(T.Z)"}, {"A": "SUM(T.X)"})
        assert ("HOTFIX", "UNMANAGED_IN_SNOWFLAKE") in env.drift_types(env.check())  # CURRENT GAP

    def test_ontology_label_with_spaces_matches_deployed_identifier(self, env):
        env.views["DB.S.V1"] = {"TOTAL_REVENUE": "SUM(T.R)"}
        env.domains.append(domain("D1", "DB.S.V1", [metric("Total Revenue", "SUM(T.R)")]))
        r = env.check()
        assert r["drift_detected"] == 0, env.drift_types(r)                 # CURRENT GAP: false MISSING

    def test_metric_name_case_differences_match(self, env):
        one_view(env, {"TOTAL_REVENUE": "SUM(T.R)"}, {"total_revenue": "SUM(T.R)"})
        assert env.check()["drift_detected"] == 0


# =============================================================================
# 5. Multi-model and multi-domain
# =============================================================================

class TestMultiModel:

    def test_same_metric_name_in_two_models_compared_to_own_view(self, env):
        env.views["DB.S.V_HR"] = {"ACTIVES": "COUNT(E.ID)"}
        env.views["DB.S.V_EHH"] = {"ACTIVES": "COUNT(DISTINCT E.ID)"}
        env.domains += [
            domain("HR", "DB.S.V_HR", [metric("ACTIVES", "COUNT(E.ID)")], model="hr"),
            domain("EHH", "DB.S.V_EHH", [metric("ACTIVES", "COUNT(DISTINCT E.ID)")], model="ehh"),
        ]
        r = env.check()
        assert r["drift_detected"] == 0
        assert r["checked_metrics"] == 2

    def test_drift_in_one_model_is_attributed_to_that_model(self, env):
        env.views["DB.S.V_HR"] = {"ACTIVES": "COUNT(E.ID) * 2"}
        env.views["DB.S.V_EHH"] = {"ACTIVES": "COUNT(DISTINCT E.ID)"}
        env.domains += [
            domain("HR", "DB.S.V_HR", [metric("ACTIVES", "COUNT(E.ID)")], model="hr"),
            domain("EHH", "DB.S.V_EHH", [metric("ACTIVES", "COUNT(DISTINCT E.ID)")], model="ehh"),
        ]
        r = env.check()
        assert [(e["domain"], e["semantic_view"]) for e in r["events"]] == [("HR", "DB.S.V_HR")]

    def test_shared_view_is_described_once(self, env):
        env.views["DB.S.V1"] = {"A": "SUM(T.X)", "B": "SUM(T.Y)"}
        env.domains += [domain("D1", "DB.S.V1", [metric("A", "SUM(T.X)")]),
                        domain("D2", "DB.S.V1", [metric("B", "SUM(T.Y)")])]
        env.check()
        assert env.cursor.described.count("DB.S.V1") == 1

    def test_other_projects_domains_are_never_checked(self, env):
        one_view(env, {"A": "SUM(T.X)"}, {"A": "SUM(T.X)"}, project="proj-a")
        env.views["DB.S.VB"] = {"B": "SUM(T.Y) * 9"}
        env.domains.append(domain("DB", "DB.S.VB", [metric("B", "SUM(T.Y)")], project="proj-b"))
        r = env.check(project="proj-a")
        assert r["checked_metrics"] == 1 and r["drift_detected"] == 0


# =============================================================================
# 6. Event lifecycle
# =============================================================================

class TestEvents:

    def _drifted(self, env, onto="SUM(T.X)", sf="SUM(T.X) * 2"):
        env.views.clear()
        env.domains.clear()
        one_view(env, {"M": sf}, {"M": onto})
        return env.check()

    def test_event_has_everything_a_reviewer_needs(self, env):
        ev = self._drifted(env)["events"][0]
        for key in ("id", "project_id", "domain", "semantic_view", "metric_name", "drift_type",
                    "bi_definition", "snowflake_definition", "proposed_snowflake_definition",
                    "status", "detected_at"):
            assert ev.get(key) not in (None, ""), key
        assert ev["status"] == "pending"

    def test_same_drift_twice_creates_one_event(self, env):
        self._drifted(env)
        self._drifted(env)
        assert len([e for e in env.state["events"] if e["metric_name"] == "M"]) == 1

    def test_approved_drift_with_same_definition_is_not_raised_again(self, env):
        ev = self._drifted(env)["events"][0]
        ds.resolve_drift_event(ev["id"], "approve", "ok")
        self._drifted(env)
        assert [e["status"] for e in env.state["events"]] == ["approved"]

    def test_ignored_drift_is_not_raised_again(self, env):
        ev = self._drifted(env)["events"][0]
        ds.resolve_drift_event(ev["id"], "ignore", "accepted variance")
        self._drifted(env)
        assert [e["status"] for e in env.state["events"]] == ["ignored"]

    def test_new_change_after_approval_raises_new_pending_event(self, env):
        ev = self._drifted(env)["events"][0]
        ds.resolve_drift_event(ev["id"], "approve", "ok")
        self._drifted(env, onto="SUM(T.X) + 1")
        assert any(e["status"] == "pending" for e in env.state["events"])

    def test_resolving_unknown_event_returns_none(self, env):
        assert ds.resolve_drift_event("drift-does-not-exist", "approve") is None

    def test_history_cap_is_per_project(self, env):
        env.state["events"] = [
            {"id": f"a{i}", "project_id": "proj-a", "metric_name": f"M{i}", "status": "ignored",
             "bi_definition": "x", "semantic_view": "V", "model_id": None, "domain": "D"}
            for i in range(250)
        ]
        one_view(env, {"M": "SUM(T.X) * 2"}, {"M": "SUM(T.X)"}, project="proj-b")
        env.check(project="proj-b")
        assert any(e["project_id"] == "proj-b" for e in env.state["events"])  # CURRENT GAP: global cap

    def test_list_events_project_match_is_exact(self, env):
        env.state["events"] = [
            {"id": "1", "project_id": "proj-ai", "metric_name": "A", "status": "pending"},
            {"id": "2", "project_id": "proj-ai-sample", "metric_name": "B", "status": "pending"},
            {"id": "3", "project_id": "ai", "metric_name": "C", "status": "pending"},
        ]
        got = sorted(e["id"] for e in ds.list_drift_events("proj-ai"))
        assert got == ["1", "3"]

    def test_clear_history_only_clears_that_project(self, env):
        env.state["events"] = [
            {"id": "1", "project_id": "proj-a", "metric_name": "A", "status": "pending"},
            {"id": "2", "project_id": "proj-b", "metric_name": "B", "status": "pending"},
        ]
        ds.clear_drift_events("proj-a")
        assert [e["id"] for e in env.state["events"]] == ["2"]


# =============================================================================
# 7. Demo data never leaks
# =============================================================================

class TestDemoData:

    def test_no_mock_events_when_flag_is_off(self, env):
        one_view(env, {"A": "SUM(T.X)"}, {"A": "SUM(T.X)"})
        env.connect_fails = True
        r = env.check()
        assert not any(e.get("mock") for e in r["events"])
        assert not any(e.get("mock") for e in env.state["events"])

    def test_mock_events_belong_to_the_checked_project(self, env, monkeypatch):
        monkeypatch.setenv("SEMABRIDGE_MOCK_DRIFT", "1")
        one_view(env, {"A": "SUM(T.X)"}, {"A": "SUM(T.X)"}, project="proj-hr")
        env.connect_fails = True
        r = env.check(project="proj-hr")
        assert all(e["project_id"] == "proj-hr" for e in r["events"])

    def test_mock_events_are_labelled(self, env, monkeypatch):
        monkeypatch.setenv("SEMABRIDGE_MOCK_DRIFT", "1")
        one_view(env, {"A": "SUM(T.X)"}, {"A": "SUM(T.X)"})
        env.connect_fails = True
        r = env.check()
        assert all(e.get("mock") is True for e in r["events"])

    def test_mock_generator_has_no_hidden_default_project(self):
        import inspect
        for fn in (ds.get_default_mock_drift_events, ds.seed_mock_drift_events):
            default = inspect.signature(fn).parameters["project_id"].default
            assert default in (inspect.Parameter.empty, None), \
                f"{fn.__name__} defaults to project {default!r}"                # CURRENT GAP: 'proj-comp'

    def test_drift_page_does_not_default_to_a_named_project(self):
        page = Path(ds.__file__).resolve().parents[3] / "frontend" / "src" / "pages" / "DriftMonitoringPage.jsx"
        if not page.exists():
            pytest.skip("frontend not present")
        text = page.read_text(encoding="utf-8")
        assert "proj-comp" not in text and ".includes('comp')" not in text   # CURRENT GAP


# =============================================================================
# 8. Webhook
# =============================================================================

class TestWebhook:

    def test_no_webhook_when_nothing_drifted(self, env):
        one_view(env, {"A": "SUM(T.X)"}, {"A": "SUM(T.X)"})
        env.check(webhook_url="https://hooks.example/x")
        assert env.webhooks == []

    def test_webhook_sent_once_with_at_most_five_events(self, env):
        onto = {f"M{i}": f"SUM(T.X{i})" for i in range(8)}
        sf = {f"M{i}": f"SUM(T.X{i}) * 2" for i in range(8)}
        one_view(env, sf, onto)
        env.check(webhook_url="https://hooks.example/x")
        assert len(env.webhooks) == 1
        assert len(env.webhooks[0][1]["events"]) <= 5

    def test_webhook_failure_does_not_break_the_check(self, env, monkeypatch):
        import requests

        def _down(*a, **k):
            raise ConnectionError("webhook endpoint down")

        monkeypatch.setattr(ds, "_send_webhook", env.real_send_webhook)
        monkeypatch.setattr(requests, "post", _down)
        one_view(env, {"A": "SUM(T.X) * 2"}, {"A": "SUM(T.X)"})
        r = env.check(webhook_url="https://hooks.example/x")
        assert r["drift_detected"] == 1


# =============================================================================
# 9. Deploy Baseline Comparison
# =============================================================================

class TestDeployBaselineDrift:

    def test_same_deploy_no_change_zero_drift_even_when_ontology_sql_differs(self, env):
        # Pre-deploy ontology SQL had SUM(ACCOUNTS."ACCOUNTOWNERSEQ")
        # Post-deploy emitter cast to ::FLOAT, so Snowflake returns SUM(ACCOUNTS.ACCOUNTOWNERSEQ::FLOAT)
        onto_pre_deploy = {"Total_Owner_Seq": 'SUM(ACCOUNTS."ACCOUNTOWNERSEQ")'}
        sf_post_deploy = {"Total_Owner_Seq": 'SUM(ACCOUNTS.ACCOUNTOWNERSEQ::FLOAT)'}
        one_view(env, sf_post_deploy, onto_pre_deploy)
        # Baseline holds the exact post-deploy expression as returned by DESCRIBE:
        p_key = _pkey("proj-test")
        v_key = ds._view_key("DB.S.V1")
        env.state["baselines"][p_key][v_key]["metrics"]["TOTAL_OWNER_SEQ"] = 'SUM(ACCOUNTS.ACCOUNTOWNERSEQ::FLOAT)'

        r = env.check()
        assert r["drift_detected"] == 0
        assert r["status"] == "IN_SYNC"

    def test_manual_edit_multiplier_in_snowflake_is_expression_mismatch(self, env):
        base_expr = "SUM(T.X)"
        sf_edited = "SUM(T.X) * 4.0"
        one_view(env, {"M": sf_edited}, {"M": base_expr})
        r = env.check()
        assert r["drift_detected"] == 1
        assert ("M", "EXPRESSION_MISMATCH") in env.drift_types(r)
        ev = r["events"][0]
        assert ev["proposed_snowflake_definition"] == "redeploy from source"

    def test_float_to_int_cast_change_detected(self, env):
        base_expr = "SUM(T.X::FLOAT)"
        sf_edited = "SUM(T.X::INT)"
        one_view(env, {"M": sf_edited}, {"M": base_expr})
        r = env.check()
        assert ("M", "EXPRESSION_MISMATCH") in env.drift_types(r)

    def test_private_helper_metrics_not_reported_as_unmanaged(self, env):
        # View was deployed with a private helper metric (_HELPER_MAX_DATE)
        sf_metrics = {"SALES": "SUM(T.AMT)", "_HELPER_MAX_DATE": "MAX(DIM_DATE.DATE)"}
        base_metrics = {"SALES": "SUM(T.AMT)", "_HELPER_MAX_DATE": "MAX(DIM_DATE.DATE)"}
        one_view(env, sf_metrics, base_metrics)
        r = env.check()
        assert r["drift_detected"] == 0
        assert not any(e["metric_name"] == "_HELPER_MAX_DATE" for e in r["events"])

    def test_metric_added_manually_in_snowflake_reported_as_unmanaged(self, env):
        # Baseline only had SALES; someone added MANUAL_EXTRA directly in Snowflake
        base_metrics = {"SALES": "SUM(T.AMT)"}
        sf_metrics = {"SALES": "SUM(T.AMT)", "MANUAL_EXTRA": "AVG(T.DISCOUNT)"}
        one_view(env, sf_metrics, base_metrics)
        r = env.check()
        assert r["drift_detected"] == 1
        assert ("MANUAL_EXTRA", "UNMANAGED_IN_SNOWFLAKE") in env.drift_types(r)
        ev = r["events"][0]
        assert ev["proposed_snowflake_definition"] == "redeploy from source"

    def test_no_baseline_returns_baseline_captured(self, env):
        env.supply_baseline = False
        env.state["baselines"] = {}
        env.views["DB.S.V1"] = {"SALES": "SUM(T.AMT)"}
        env.domains.append(domain("D1", "DB.S.V1", [metric("SALES", "SUM(T.AMT)")]))

        # First run: captures baseline and returns BASELINE_CAPTURED
        r1 = env.check()
        assert r1["status"] == "BASELINE_CAPTURED"
        assert r1["drift_detected"] == 0
        assert len(r1["events"]) == 0
        assert r1["checked_metrics"] == 1

        # Second run: baseline exists now, should report IN_SYNC
        r2 = env.check()
        assert r2["status"] == "IN_SYNC"
        assert r2["drift_detected"] == 0

    def test_power_bi_source_drift_detected(self, env):
        one_view(env, {"SALES": "SUM(T.AMT)"}, {"SALES": "SUM(T.AMT)"})
        p_key = _pkey("proj-test")
        v_key = ds._view_key("DB.S.V1")
        env.state["baselines"][p_key][v_key]["source_definitions"] = {
            "SALES": "CALCULATE(SUM(Sales[Amount]))"
        }
        env.domains[0].metrics[0].dax_expression = "CALCULATE(SUM(Sales[Amount]), Year=2024)"

        r = env.check()
        assert r["drift_detected"] == 1
        assert ("SALES", "SOURCE_CHANGED") in env.drift_types(r)
        ev = r["events"][0]
        assert ev["proposed_snowflake_definition"] == "redeploy from source"

    def test_legacy_events_marked_superseded_no_silent_deletion(self, env):
        env.state["events"] = [
            {"id": "ev-old-1", "project_id": "proj-test", "metric_name": "M1", "status": "pending",
             "bi_definition": "SUM(A)", "snowflake_definition": "SUM(B)"}
        ]
        one_view(env, {"M1": "SUM(B)"}, {"M1": "SUM(B)"})
        env.check()
        assert len(env.state["events"]) >= 1
        old_ev = next(e for e in env.state["events"] if e["id"] == "ev-old-1")
        assert old_ev["status"] == "superseded"
        assert "Superseded" in old_ev["resolution_reason"]
