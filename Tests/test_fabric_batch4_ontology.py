"""Batch 4: ontology parity for Fabric (report layer, review before the dry run, domain synonyms).

Recorded responses only: Tests/fixtures/fabric/reports/recorded_reports_ws1.json
(a legacy report.json report, a PBIR report served through a 202 operation, a
report the identity cannot read) and semantic_models_ws1.json. Any other URL
fails the test; nothing goes to the network.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).parent
REC = json.loads((HERE / "fixtures/fabric/reports/recorded_reports_ws1.json").read_text())
MODELS = json.loads((HERE / "fixtures/fabric/semantic_models_ws1.json").read_text())
TMSL = json.loads((HERE / "characterization/fixtures/model.tmsl.json").read_text())
WS, DS = REC["workspace_id"], REC["dataset_id"]
FABRIC_API = "https://api.fabric.microsoft.com/v1"

_spec = importlib.util.spec_from_file_location("characterization_build", HERE / "characterization/_build.py")
_build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_build)


class _Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}
        self.ok = 200 <= status < 300
        self.text = json.dumps(body) if body is not None else ""

    def json(self):
        return self._body


class RecordedFabric:
    """Replays the recorded report responses; counts every call."""

    def __init__(self):
        self.calls, self._polls = [], {}

    def __call__(self, method, url, **kw):
        self.calls.append((method, url))
        if method == "GET" and url.endswith(f"/v1.0/myorg/groups/{WS}/reports"):
            return _Resp(200, REC["reports"])
        if method == "POST" and url.startswith(f"{FABRIC_API}/workspaces/{WS}/reports/") and url.endswith("/getDefinition"):
            rec = REC["getDefinition"][url.split("/reports/")[1].split("/")[0]]
            return _Resp(rec["status"], rec.get("body"), rec.get("headers"))
        if method == "GET" and "/operations/" in url:
            op = url.split("/operations/")[1].split("/")[0]
            if url.endswith("/result"):
                return _Resp(200, REC["operations"][op]["result"])
            i = self._polls.get(op, 0)
            self._polls[op] = i + 1
            return _Resp(200, REC["operations"][op]["polls"][min(i, len(REC["operations"][op]["polls"]) - 1)])
        pytest.fail(f"unexpected live call: {method} {url}")


def _extractor(recorded):
    from semabridge.connectors.fabric_extractor import FabricExtractor
    from semabridge.core.settings import FabricConfig

    ext = FabricExtractor(FabricConfig(workspace_id=WS), access_token="tok")
    ext._token_expires_at = time.time() + 3600
    ext._request_with_auth_retry = lambda method, url, **kw: recorded(method, url)
    return ext


def _layer():
    from semabridge.sources.fabric.report_layer import fetch_report_layer

    rec = RecordedFabric()
    return fetch_report_layer(_extractor(rec), WS, DS, TMSL, sleep=lambda s: None), rec


def _fabric_ddl(field_aliases):
    from semabridge.core.engine.conversion.fabric import _convert_fabric_to_sml
    from semabridge.core.source_format import from_fabric_tmsl

    sf = from_fabric_tmsl(project_id=_build.PROJECT_ID, run_id="run-characterization", tmsl=copy.deepcopy(TMSL),
                          workspace_id="00000000-0000-0000-0000-00000000c0de",
                          dataset_id="00000000-0000-0000-0000-00000000da7a", row_counts={})
    sf.dataset_name = "Characterization Model"
    sf.field_aliases = field_aliases
    ctx = _build._context("fabric", sf)
    sml = _convert_fabric_to_sml(_build._ENGINE_SELF, ctx, None, None)
    return _build._ddl(sml), ctx.osi_model, sml


# ── 1. report renames -> synonyms -> WITH SYNONYMS (flag on); identical with flag off ──
def test_report_renames_reach_with_synonyms_and_flag_off_is_identical():
    layer, rec = _layer()
    by_field = {(a["field"], a["field_type"]): a["aliases"] for a in layer.field_aliases}
    assert by_field[("Total Units", "measure")] == ["Units Sold"]            # legacy report.json
    assert by_field[("Total Revenue", "measure")] == ["Revenue $"]          # PBIR, via 202 + polling
    assert by_field[("Manufacturer", "column")] == ["Brand"]
    assert any(u.endswith("/operations/op-pbir-1/result") for _, u in rec.calls)
    assert layer.reports == ["Sales Overview", "Revenue (PBIR)"]           # other-dataset report not read

    ddl_on, _, _ = _fabric_ddl(layer.field_aliases)
    assert "WITH SYNONYMS = ('Units Sold')" in ddl_on and "'Revenue $'" in ddl_on and "'Brand'" in ddl_on
    ddl_off, _, _ = _fabric_ddl([])
    snapshot = (HERE / "characterization/snapshots/fabric.ddl.sql").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert ddl_off == snapshot  # flag off (no aliases): byte-identical to the golden Fabric DDL


# ── 2. unresolved / ambiguous report fields become run warnings; unreadable report = warning ──
def test_report_field_warnings_and_unreadable_report_through_extraction(monkeypatch):
    import semabridge.connectors.fabric_extractor as fe
    from semabridge.core.behavior import ConnectorBehavior
    from semabridge.core.engine.context import RunContext
    from semabridge.core.engine.extraction.fabric import _extract_fabric
    from semabridge.core.settings import FabricConfig

    rec = RecordedFabric()

    class _FakeExtractor(fe.FabricExtractor):
        def __init__(self, cfg, access_token=None):
            super().__init__(cfg, access_token="tok")
            self._request_with_auth_retry = lambda method, url, **kw: rec(method, url)

        def _resolve_model_id_exact(self, ref):
            return DS

        def get_model_definition(self, ds):
            return copy.deepcopy(TMSL)

        def get_table_row_counts(self, ds):
            return {}

        def get_model_display_name(self, ds):
            return "Sales"

    monkeypatch.setattr(fe, "FabricExtractor", _FakeExtractor)
    monkeypatch.setattr("semabridge.sources.fabric.report_layer.time.sleep", lambda s: None)

    def _run(flag):
        behavior = ConnectorBehavior()
        behavior.features.fabric_report_layer = flag
        ctx = RunContext(project_id="p", run_id="r", config=SimpleNamespace(fabric=FabricConfig(workspace_id=WS), source=None),
                         start_time=time.time(), source_type="fabric", behavior=behavior)
        ctx.fabric_access_token = "tok"
        steps = []
        sf = _extract_fabric(SimpleNamespace(_record_step=lambda *a: steps.append(a)), ctx, DS, WS)
        return sf, ctx

    sf, ctx = _run(True)
    assert [u["field"] for u in sf.unresolved_report_field_references] == ["Units Legacy"]
    assert sf.unresolved_report_field_references[0]["report"] == "Sales Overview"
    assert [a["alias"] for a in sf.ambiguous_report_aliases] == ["Volume"]   # claimed by two fields across reports
    assert any("'Exec Only' could not be read" in w for w in ctx.source_warnings)  # 403 -> warning, run continues
    assert sf.field_aliases and sf.tmsl_definition

    rec.calls.clear()
    sf_off, ctx_off = _run(False)
    assert sf_off.field_aliases == [] and ctx_off.source_warnings == [] and rec.calls == []  # flag off: no report calls


# ── 3. review before the dry run reads every selected Fabric model and flags conflicts ──
def test_review_flags_same_measure_defined_differently_across_two_fabric_models(monkeypatch):
    import semabridge.sources.fabric.credentials as cr
    from semabridge.api.services.ontology_review_service import analyze
    from semabridge.sources.fabric.model_reader import read_fabric_models

    monkeypatch.setattr(cr, "resolve_fabric_credentials", lambda **kw: SimpleNamespace(origin="identity", access_token="t", expires_at=9e12))
    sales = copy.deepcopy(TMSL)
    finance = copy.deepcopy(TMSL)
    finance["model"]["tables"][0]["measures"][0]["expression"] = "SUMX(Sales, Sales[Units] * 2)"  # Total Units differs
    defs = {"11111111-1111-1111-1111-111111111111": sales, "22222222-2222-2222-2222-222222222222": finance}
    read = []

    def _factory(identity, creds):
        read.append(identity.display_name)
        return SimpleNamespace(get_model_definition=lambda guid: defs[guid])

    models, errors = read_fabric_models(
        {"type": "fabric", "workspace_id": WS, "identity_id": "acct-1"}, ["Sales", "Finance"],
        lister=lambda ws, creds: MODELS["value"], extractor_factory=_factory,
    )
    assert read == ["Sales", "Finance"] and errors == []
    conflicts = analyze(models)["conflicts"]
    assert any("Total Units" in json.dumps(c) for c in conflicts)


# ── 4. /from-osi gets the report synonyms for Fabric when the flag is on ─────
def test_domain_bootstrap_gets_fabric_report_synonyms(monkeypatch):
    import semabridge.sources.fabric.dax_target as dt
    from semabridge.ontology.per_model import build_model_domain
    from semabridge.ontology.project_models import ProjectModel

    layer, _ = _layer()
    _, _, sml = _fabric_ddl(layer.field_aliases)
    blob = json.loads(sml.model_dump_json())  # what the sync stores as the model snapshot
    configs = {
        "p-fab-on": {"source": {"type": "fabric"}, "behavior": {"features": {"fabric_report_layer": True}}},
        "p-fab-off": {"source": {"type": "fabric"}},
        "p-pbix": {"source": {"type": "pbix"}, "behavior": {"features": {"fabric_report_layer": True}}},
    }
    monkeypatch.setattr(dt, "load_project_config", lambda pid: configs[pid])

    def _synonyms(pid):
        dom = build_model_domain(pid, ProjectModel(model_id="sales", model_name="Sales", blob=blob))
        metric = next(m for m in dom.metrics if m.name.casefold() == "total units")
        return list(metric.synonyms or [])

    assert "Units Sold" in _synonyms("p-fab-on")        # /from-osi domain gets the report synonym
    assert "Units Sold" not in _synonyms("p-fab-off")   # flag off: exactly as before
    assert "Units Sold" not in _synonyms("p-pbix")      # PBIX bootstrap unchanged
