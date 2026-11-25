"""Follow-ups on fabric/batch-1-trust: feature flags and the multi-model ontology review."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace


def test_fabric_parity_feature_flags_exist_and_default_off():
    from semabridge.core.behavior import ConnectorBehavior, FeatureFlags

    names = ["fabric_report_layer", "fabric_real_row_counts", "fabric_live_drift",
             "fabric_strict_workspace", "skip_unchanged_sources", "bind_from_m_source"]
    flags = FeatureFlags()
    assert all(getattr(flags, n) is False for n in names)
    assert ConnectorBehavior().features.bind_from_m_source is False
    assert FeatureFlags(fabric_report_layer=True).fabric_report_layer is True


def test_post_sync_ontology_review_reads_every_model_not_only_the_last(monkeypatch):
    import semabridge.ontology.project_models as pm
    from semabridge.api.services import ontology_review_service as svc

    two = [
        pm.ProjectModel(model_id="sales", model_name="Sales", snapshot_id="s1", blob={"m": "Sales"}),
        pm.ProjectModel(model_id="finance", model_name="Finance", snapshot_id="s2", blob={"m": "Finance"}),
        pm.ProjectModel(model_id="never-synced", model_name="Never", blob=None),
    ]
    monkeypatch.setattr(pm, "project_models", lambda pid: two)
    monkeypatch.setattr(pm, "osi_from_blob", lambda blob: SimpleNamespace(label=blob["m"]))

    async def _head_only(*a, **k):
        raise AssertionError("project head (last synced model) used instead of every model")

    import semabridge.api.routers.export_router as er
    monkeypatch.setattr(er, "_load_osi_model", _head_only)

    models, errors = asyncio.run(svc.read_source_models({"type": "fabric"}, [], project_id="proj-two"))
    assert [m.label for m in models] == ["Sales", "Finance"] and errors == []
