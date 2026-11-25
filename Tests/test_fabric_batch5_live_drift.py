"""Batch 5 item 2: live drift for Fabric (features.fabric_live_drift).

Recorded data only: the workspace listing (semantic_models_ws1.json), the model
definition (characterization TMSL fixture, with one measure changed "in Fabric"),
the last synced snapshot (characterization fabric SML snapshot) and recorded
DESCRIBE SEMANTIC VIEW rows. No live Fabric or Snowflake call.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).parent
MODELS = json.loads((HERE / "fixtures/fabric/semantic_models_ws1.json").read_text())
WS = MODELS["workspace_id"]
TMSL = json.loads((HERE / "characterization/fixtures/model.tmsl.json").read_text())
SNAPSHOT = json.loads((HERE / "characterization/snapshots/fabric.sml.json").read_text())
CHANGED_DAX = 'CALCULATE(SUM(Sales[Units]), Sales[Status] <> "Returned")'


def _live_changed():
    live = copy.deepcopy(TMSL)
    live["model"]["tables"][0]["measures"][0]["expression"] = CHANGED_DAX  # Total Units, edited in Fabric after the sync
    return live


@pytest.fixture
def fabric_project(monkeypatch, tmp_path):
    import semabridge.sources.fabric.credentials as cr
    import semabridge.sources.fabric.dax_target as dt
    import semabridge.sources.fabric.live_definition as ld
    import semabridge.sources.fabric.validation as va
    from semabridge.governance import drift_service
    from semabridge.ontology.models import model_key
    from semabridge.ontology.project_models import ProjectModel
    from semabridge.ontology.store import ontology_store

    state = {"features": {}}
    calls = {"fetch": 0}
    monkeypatch.setattr(dt, "load_project_config", lambda pid: {
        "source": {"type": "fabric", "workspace_id": WS, "identity_id": "acct-1", "models": ["Sales"]},
        "behavior": {"features": state["features"]}})
    monkeypatch.setattr(cr, "resolve_fabric_credentials", lambda **kw: SimpleNamespace(origin="identity", access_token="t", expires_at=9e12))
    monkeypatch.setattr(va, "list_workspace_models", lambda ws, creds: MODELS["value"])

    def _fetch(identity, creds):
        calls["fetch"] += 1
        return _live_changed()

    monkeypatch.setattr(ld, "_fetch_definition", _fetch)
    monkeypatch.setattr(ld, "_project_models", lambda pid: [ProjectModel(model_id=model_key("Sales"), model_name="Sales", blob=SNAPSHOT)])
    domain = SimpleNamespace(domain="Sales", id="dom-sales", source_model_id=model_key("Sales"), semantic_view_name=None,
                             metrics=[SimpleNamespace(name="Total Units", sql_expression="SUM(SALES.UNITS)", dax_expression="SUM(Sales[Units])",
                                                      synonyms=[], description="")])
    monkeypatch.setattr(ontology_store, "domains_for", lambda pid: [domain])
    monkeypatch.setattr("semabridge.ai.project_connection.resolve_project_warehouse_config", lambda *a, **k: None)
    monkeypatch.setattr(drift_service, "_state_path", lambda: tmp_path / "drift_state.json")
    return state, calls


def test_live_drift_reports_fabric_change_with_flag_on_and_nothing_changes_off(fabric_project):
    from semabridge.governance.drift_service import run_drift_check

    state, calls = fabric_project
    off = run_drift_check(project_id="proj-fab")
    assert calls["fetch"] == 0 and "mode" not in off and off["events"] == []  # flag off: today's comparison only

    state["features"]["fabric_live_drift"] = True
    on = run_drift_check(project_id="proj-fab")
    assert on["mode"] == "fabric_live" and calls["fetch"] == 1
    ev = [e for e in on["events"] if e["drift_type"] == "EXPRESSION_CHANGED_IN_FABRIC"]
    assert len(ev) == 1 and ev[0]["metric_name"] == "Total Units"
    assert ev[0]["bi_definition"] == CHANGED_DAX and ev[0]["baseline_definition"] == "SUM(Sales[Units])"
    assert ev[0]["source"] == "fabric_live" and ev[0]["status"] == "pending"
    assert not [e for e in on["events"] if e["drift_type"] == "REMOVED_IN_FABRIC"]  # derived metrics aren't "removed"


def test_live_drift_skips_already_deployed_changes_and_reports_ontology_drift():
    from semabridge.sources.fabric.live_definition import compare

    proposed = 'SUM(IFF(SALES."STATUS" <> \'Returned\', SALES."UNITS", 0))'
    # recorded DESCRIBE SEMANTIC VIEW rows (deployed view already carries the new definition)
    deployed = [
        {"object_kind": "METRIC", "object_name": "SALES.TOTAL_UNITS", "property": "EXPRESSION", "property_value": proposed},
        {"object_kind": "METRIC", "object_name": "SALES.TOTAL_UNITS", "property": "SYNONYMS", "property_value": '["Units"]'},
        {"object_kind": "METRIC", "object_name": "SALES.TOTAL_UNITS", "property": "COMMENT", "property_value": "Units sold"},
    ]
    domain = SimpleNamespace(metrics=[SimpleNamespace(name="Total Units", synonyms=["Units", "Units Sold"], description="Units sold, net of returns")])
    found = compare(_live_changed(), SNAPSHOT, deployed, domain=domain, propose=lambda n, d: proposed if d == CHANGED_DAX else d)
    kinds = {f["drift_type"] for f in found}
    assert "EXPRESSION_CHANGED_IN_FABRIC" not in kinds  # already deployed -> not drift
    assert {"ONTOLOGY_SYNONYMS_NOT_DEPLOYED", "ONTOLOGY_DESCRIPTION_NOT_DEPLOYED"} <= kinds
    assert compare(TMSL, SNAPSHOT, None, propose=lambda n, d: d) == []  # unchanged model, no drift
