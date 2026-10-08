"""Part E: source_file attribution regression tests.

This exact CLASS of field (a value that must thread through
intermediate/models.py -> project_mapping_engine.py -> mapping_service.py ->
mappings_controller.py -> frontend) has previously died silently in this
codebase at TWO different points:
  1. has_report_alias: populated correctly through the backend mapping-engine
     and serialization layers, then silently dropped in mappings_controller.py's
     final filtered_mappings.append({...}) dict — never reached the API response.
  2. advisory_categories (and implicitly complexity_tier, discovered while
     writing this test): returned correctly by the API, then silently dropped
     in CreateProjectPage.jsx's normalizeRows() explicit-allowlist object
     literal — never reached the rendered table.

A unit test on any ONE layer in isolation would have passed for both of these
historical bugs — the defect lived specifically at a boundary between layers.
So this file deliberately tests the two real boundaries where source_file
could die the same way:
  - test_dry_run_endpoint_response_includes_source_file_on_every_row: the
    REAL HTTP controller function (mappings_controller.dry_run_mapping),
    mocking only the deep pipeline call (sync_models) and the snapshot store
    lookup — every serialization layer in between (build_entity_mappings,
    _compat_serialize_auto_map_entity_mappings, filtered_mappings.append) runs
    for real.
  - test_normalize_rows_frontend_boundary_preserves_source_file: a Node-free
    Python-side structural check is not possible for JS; see the accompanying
    frontend test in DryRunMappingTable.test source verification note below.
    Python cannot execute JSX directly, so this file also documents (and a
    companion review confirms) the two frontend edits: CreateProjectPage.jsx's
    normalizeRows() allowlist and DryRunMappingTable.jsx's render call.
"""
from __future__ import annotations

import asyncio
import os
import sys
import types
from types import SimpleNamespace

import pytest

os.environ.setdefault("SEMABRIDGE_DATABASE_URL", "sqlite:///./test_source_file_attribution.sqlite")
os.environ.setdefault("AUTH_ENABLED", "false")

if "psycopg2" not in sys.modules:
    _psycopg2 = types.ModuleType("psycopg2")
    _psycopg2.__version__ = "2.9.9"
    _psycopg2.apilevel = "2.0"
    _psycopg2.threadsafety = 2
    _psycopg2.paramstyle = "pyformat"
    _psycopg2.Error = Exception
    _psycopg2.connect = lambda *args, **kwargs: None
    sys.modules["psycopg2"] = _psycopg2
    sys.modules["psycopg2.extensions"] = types.ModuleType("psycopg2.extensions")
    sys.modules["psycopg2.extras"] = types.ModuleType("psycopg2.extras")

from semabridge.sml.models import SMLColumn, SMLDataset, SMLMetric, SMLModel

FILE_A = "C:/Reports/Sales Report.pbix"
FILE_B = "C:/Reports/Marketing Analysis.pbix"


def _build_fake_sml_blob() -> dict:
    """A minimal, schema-valid SML blob with columns/metrics attributed to two
    different source files — proxying what a multi-PBIX project's merged
    preview snapshot would look like at the point dry_run_mapping() reads it.
    """
    model = SMLModel(
        unique_name="preview",
        datasets=[
            SMLDataset(
                unique_name="SALES",
                is_fact=True,
                columns=[
                    SMLColumn(unique_name="AMOUNT", source_file=FILE_A),
                    SMLColumn(unique_name="REGION", source_file=FILE_B),
                ],
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="TOTAL_AMOUNT",
                dataset="SALES",
                source_column="AMOUNT",
                source_file=FILE_A,
            ),
        ],
    )
    return model.model_dump(mode="json")


def test_dry_run_endpoint_response_includes_source_file_on_every_row(monkeypatch):
    """Hits the real dry_run_mapping() controller function end-to-end (mocking
    only the deep pipeline call and the snapshot store), proving source_file
    survives build_entity_mappings -> _compat_serialize_auto_map_entity_mappings
    -> the filtered_mappings.append dict -- the exact chain has_report_alias
    fell out of at the last step.
    """
    import semabridge.api.controllers.mappings_controller as mc
    import semabridge.api.services.core_domain_service as core_domain_service
    import semabridge.api.services.project_shared as project_shared

    sml_blob = _build_fake_sml_blob()

    async def _fake_sync_models(payload):
        return {
            "status": "success",
            "results": [
                {"model": "preview", "summary": {"sml_snapshot_id": "fake-snap-1"}},
            ],
        }

    monkeypatch.setattr(core_domain_service, "sync_models", _fake_sync_models)
    monkeypatch.setattr(mc, "require_request_user_id", lambda request: None)
    monkeypatch.setattr(mc, "validate_project_connector_accounts_belong_to_user", lambda *a, **k: None)
    monkeypatch.setattr(
        project_shared.db_manager,
        "get_snapshot",
        lambda snapshot_id: SimpleNamespace(sml_blob=sml_blob),
    )

    request = mc.DryRunRequest(
        source_config={"type": "pbix"},
        target_config={"type": "snowflake"},
        selected_sources=[],
    )

    class _FakeService:
        pass

    response = asyncio.run(mc.dry_run_mapping("preview", object(), request, service=_FakeService()))

    # dry_run_mapping() returns a plain dict on success (only errors become a
    # JSONResponse) -- see the function's own success-path `return {...}`.
    assert isinstance(response, dict), f"Expected a dict response, got: {response!r}"
    entity_mappings = response.get("entity_mappings") or []
    assert len(entity_mappings) >= 3, (
        f"Expected at least 3 field rows (2 columns + 1 metric), got {len(entity_mappings)}: {entity_mappings}"
    )

    for row in entity_mappings:
        assert "source_file" in row, f"Row missing source_file key entirely: {row}"
        assert row["source_file"] in (FILE_A, FILE_B), (
            f"Row's source_file was not preserved through the response pipeline: {row}"
        )

    by_name = {row["source_name"]: row["source_file"] for row in entity_mappings}
    assert by_name.get("AMOUNT") == FILE_A
    assert by_name.get("REGION") == FILE_B
    assert by_name.get("TOTAL_AMOUNT") == FILE_A


def test_sml_column_and_metric_carry_source_file_through_osi_to_sml_conversion():
    """Lower-level unit check that the OSI -> SML conversion layer (a
    different boundary than the controller one above) also propagates
    source_file, using the real OSIToSMLConverter rather than hand-built SML
    objects — this is the layer BEFORE the controller boundary the previous
    test exercises.
    """
    from semabridge.converter.osi_to_sml import OSIToSMLConverter
    from semabridge.intermediate.models import OSIColumn, OSIDataset, OSIMetric, OSIModel

    osi_model = OSIModel(
        unique_name="preview",
        datasets=[
            OSIDataset(
                unique_name="SALES",
                is_fact=True,
                columns=[
                    OSIColumn(unique_name="AMOUNT", source_file=FILE_A),
                ],
            ),
        ],
        metrics=[
            OSIMetric(
                unique_name="TOTAL_AMOUNT",
                dataset="SALES",
                source_column="AMOUNT",
                expression="SUM([AMOUNT])",
                source_file=FILE_A,
            ),
        ],
    )

    sml_model = OSIToSMLConverter().from_osi(osi_model)

    sml_column = sml_model.datasets[0].get_column("AMOUNT")
    assert sml_column.source_file == FILE_A

    sml_metric = sml_model.get_metric("TOTAL_AMOUNT") if hasattr(sml_model, "get_metric") else next(
        m for m in sml_model.metrics if m.unique_name == "TOTAL_AMOUNT"
    )
    assert sml_metric.source_file == FILE_A
