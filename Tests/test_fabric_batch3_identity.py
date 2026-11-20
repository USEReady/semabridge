"""Batch 3: Fabric model checks and identity, parametrized over [pbix, fabric].

Fabric calls replay a recorded semanticModels response through requests.request
(Tests/fixtures/fabric/semantic_models_ws1.json) -- no live network.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from semabridge.api.services import sync_execution_service as ses

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "fabric" / "semantic_models_ws1.json").read_text())
WS = FIXTURE["workspace_id"]
SALES, FINANCE = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
SOURCES = ["pbix", "fabric"]


class _Recorded:
    """requests.Response stand-in for the recorded listing."""

    status_code, ok, headers, text = 200, True, {}, ""

    def json(self):
        return {"value": FIXTURE["value"]}

    def raise_for_status(self):
        return None


@pytest.fixture
def recorded_fabric(monkeypatch):
    """Replay the recorded workspace listing; any other Fabric URL fails the test."""
    import semabridge.connectors.fabric_extractor as fe
    import semabridge.sources.fabric.credentials as cr

    calls = []

    def _request(method, url, **kw):
        calls.append(url)
        assert url.endswith(f"/workspaces/{WS}/semanticModels"), f"unexpected live call: {method} {url}"
        return _Recorded()

    monkeypatch.setattr(fe.requests, "request", _request)
    monkeypatch.setattr(fe.requests, "post", lambda *a, **k: pytest.fail(f"unexpected live call: POST {a[:1]}"))
    monkeypatch.setattr(cr, "resolve_fabric_credentials",
                        lambda **kw: SimpleNamespace(origin="identity", access_token="tok", expires_at=9e12))
    return calls


def _cfg(source_type, tmp_path, models, **src):
    if source_type == "pbix":
        files = []
        for m in models:
            f = tmp_path / f"{m}.pbix"
            f.write_bytes(b"pbix")
            files.append(str(f))
        return {"source": {"type": "pbix", "models": files, **src}, "targets": [{"type": "snowflake"}]}
    return {"source": {"type": "fabric", "workspace_id": WS, "identity_id": "acct-1", "models": models, **src},
            "targets": [{"type": "snowflake"}]}


# ── 1. validation ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("source_type", SOURCES)
def test_model_validation(source_type, tmp_path, recorded_fabric, caplog):
    from semabridge.domain.exceptions import ValidationError

    if source_type == "pbix":
        jobs, *_ = ses._build_sync_jobs(_cfg(source_type, tmp_path, ["Sales", "Finance"]))
        assert [Path(j["pbix_path"]).stem for j in jobs] == ["Sales", "Finance"]
        assert all("identity" not in j for j in jobs) and recorded_fabric == []  # no Fabric call for PBIX
        return

    # Fabric model validation runs via validate_fabric_project (at save / sync execution)
    jobs = ses.validate_fabric_project(_cfg(source_type, tmp_path, ["Sales", "Finance"]))
    assert [(j["model_label"], j["dataset_id"]) for j in jobs] == [("Sales", SALES), ("Finance", FINANCE)]
    assert jobs[0]["identity"] == {"source_type": "fabric", "model_id": SALES, "display_name": "Sales", "workspace_id": WS}
    # by GUID, and a GUID + its name listed together -> one model, with a warning
    with caplog.at_level(logging.WARNING):
        jobs = ses.validate_fabric_project(_cfg(source_type, tmp_path, [SALES, "sales"]))
    assert [j["dataset_id"] for j in jobs] == [SALES] and "listed more than once" in caplog.text
    with pytest.raises(ValidationError, match=f"'Inventory' is ambiguous in workspace '{WS}'.*33333333.*44444444"):
        ses.validate_fabric_project(_cfg(source_type, tmp_path, ["Inventory"]))
    with pytest.raises(ValidationError, match=f"'Payroll' was not found in workspace '{WS}'"):
        ses.validate_fabric_project(_cfg(source_type, tmp_path, ["Payroll"]))
    from semabridge.sources.base import SourceConfigError
    from semabridge.sources.fabric.validation import list_workspace_models, validate_fabric_models

    creds = SimpleNamespace(origin="identity", access_token="tok", expires_at=9e12)
    with pytest.raises(SourceConfigError, match=f"2 Fabric models configured for workspace '{WS}'; at most 1"):
        validate_fabric_models(["Sales", "Finance"], WS, creds, max_models=1, lister=list_workspace_models)


# ── 2. override matching (sync mapping overrides + ontology overrides) ───────
@pytest.mark.parametrize("source_type", SOURCES)
def test_override_matching_by_guid_or_display_name(source_type, tmp_path, recorded_fabric, monkeypatch):
    if source_type == "pbix":
        cfg = _cfg("pbix", tmp_path, ["Sales", "Finance"])
        jobs, *_ = ses._build_sync_jobs(cfg)
        sales_job = jobs[0]
        tagged = [{"model": cfg["source"]["models"][0], "source_path": "m.A", "target_name": "A1"},
                  {"model": cfg["source"]["models"][1], "source_path": "m.B", "target_name": "B1"}]
        scoped = ses._config_with_model_scoped_overrides({**cfg, "mappings_overrides": tagged}, sales_job)
        assert [r["target_name"] for r in scoped["mappings_overrides"]] == ["A1"]  # unchanged PBIX matching
        return

    # project configured by NAME, override saved under the GUID (and the reverse)
    by_name = _cfg("fabric", tmp_path, ["Sales", "Finance"])
    sales_job = ses.validate_fabric_project(by_name)[0]
    rows = [{"model": SALES, "source_path": "m.A", "target_name": "BY_GUID"},
            {"model": FINANCE, "source_path": "m.B", "target_name": "OTHER_MODEL"}]
    scoped = ses._config_with_model_scoped_overrides({**by_name, "mappings_overrides": rows}, sales_job)
    assert [r["target_name"] for r in scoped["mappings_overrides"]] == ["BY_GUID"]
    by_guid = _cfg("fabric", tmp_path, [SALES, FINANCE])
    sales_job = ses.validate_fabric_project(by_guid)[0]
    rows = [{"model": "Sales", "source_path": "m.A", "target_name": "BY_NAME"},
            {"model": "Finance", "source_path": "m.B", "target_name": "OTHER_MODEL"}]
    scoped = ses._config_with_model_scoped_overrides({**by_guid, "mappings_overrides": rows}, sales_job)
    assert [r["target_name"] for r in scoped["mappings_overrides"]] == ["BY_NAME"]
    assert ses.job_matches_source_ref(sales_job, "Sales") and ses.job_matches_source_ref(sales_job, SALES)

    # ontology overrides: a Fabric run also matches by dataset GUID and display name
    import semabridge.ontology.apply as ap

    seen = {}
    monkeypatch.setattr(ap, "apply_project_ontology", lambda pid, sml, **kw: seen.setdefault("names", kw["model_names"]) or {})
    monkeypatch.setattr(ap, "_attach_project_verified_queries", lambda *a, **k: 0)
    ctx = SimpleNamespace(source_type="fabric", project_id="p", config_payload={}, dry_run=True,
                          sml_model=SimpleNamespace(unique_name="SALES_SEMANTIC", label=None), osi_model=None,
                          source_format=SimpleNamespace(dataset_id=SALES, dataset_name="Sales"))
    ap.apply_ontology_for_run(SimpleNamespace(_summary=None), ctx)
    assert SALES in seen["names"] and "Sales" in seen["names"]


# ── 3. preview ids ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("source_type", SOURCES)
def test_preview_ids(source_type):
    from semabridge.api.controllers.mappings_controller import _dry_run_preview_project_id

    src = {"type": source_type, "workspace_id": WS if source_type == "fabric" else "", "identity_id": "acct-1"}
    a = _dry_run_preview_project_id("u1", src, ["Sales"])
    b = _dry_run_preview_project_id("u1", {**src, "identity_id": "acct-2"}, ["Sales"])
    legacy = f"preview-{uuid.uuid5(uuid.NAMESPACE_DNS, f'u1-{source_type}-' + src['workspace_id'] + '-Sales').hex[:12]}"
    if source_type == "pbix":
        assert a == b == legacy  # PBIX ids unchanged
    else:
        assert a != b and a != legacy  # Fabric ids include the source identity


# ── 4. run report labels ─────────────────────────────────────────────────────
@pytest.mark.parametrize("source_type", SOURCES)
def test_run_report_labels(source_type):
    from semabridge.api.services.run_report_service import _describe_source, _describe_source_for_result

    if source_type == "pbix":
        cfg = "source:\n  type: pbix\n  models: [C:/data/Sales.pbix, C:/data/Finance.pbix]\n"
        assert _describe_source(cfg, {}) == ("Local files", ["Sales.pbix", "Finance.pbix"])
        assert _describe_source_for_result({"pbix_path": "C:/data/Finance.pbix"}, cfg, {}) == ("Local file", ["Finance.pbix"])
    else:
        cfg = f"source:\n  type: fabric\n  workspace_id: {WS}\n  models: [Sales, Finance]\n"
        assert _describe_source(cfg, {})[1] == [f"Fabric dataset Sales (workspace {WS})", f"Fabric dataset Finance (workspace {WS})"]
        one = _describe_source_for_result({"model": "Finance", "dataset_id": FINANCE}, cfg, {"source_type": "fabric"})
        assert one == ("Microsoft Fabric", [f"Fabric dataset {FINANCE} (workspace {WS})"])
