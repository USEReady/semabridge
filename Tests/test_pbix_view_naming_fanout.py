"""Regression tests for the PBIX view-naming / multi-PBIX fan-out bug.

Root cause (see semabridge memory: semabridge_pbix_view_naming_project_id_leak):
_convert_pbix_to_sml() never set source_data["display_name"], so
TMSLToOSIConverter.to_osi()'s `resolved_unique_name = source_data.get(
"display_name") or dataset_id or display_name` always fell through to
`dataset_id`, which for PBIX is `context.project_id` — a single value
computed ONCE per sync request and shared, unchanged, across every job in a
multi-PBIX batch (see _build_sync_jobs/_run_single_job in
sync_execution_service.py). Every file in a batch therefore produced the
SAME OSIModel/SMLModel.unique_name, and since ddl_builder.py's
_generate_semantic_view() derives the literal "CREATE OR REPLACE SEMANTIC
VIEW" name from sml.unique_name, every file's DDL targeted the identical
fully-qualified view name — the last job to execute silently overwrote the
earlier ones, so N independently-fanned-out sync jobs collapsed to one
Snowflake semantic view.

The fix: derive unique_name from the job's OWN pbix_path (context.source_
format.pbix_path — confirmed per-job-correct, not batch-shared) via the
same IdentifierSanitizer.sanitize_table_name() that
get_pbix_deployment_view_name() (name_translator.py) already uses for the
pre-deploy collision check, so the collision check and the name actually
baked into the DDL agree on the same computation.

These tests exercise _convert_pbix_to_sml() directly (same pattern as
test_mapping_override_sequencing.py) — no DB, no network, no Snowflake.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from semabridge.core.drop_ledger import DropLedger
from semabridge.core.engine.conversion.pbix import _convert_pbix_to_sml
from semabridge.core.execution_engine import ExecutionEngine
from semabridge.domain.exceptions import ValidationError
from semabridge.utils.name_translator import (
    get_pbix_deployment_view_name,
    get_target_deployment_name,
    validate_no_pbix_view_name_collisions,
)


class _EngineStub:
    """Minimal stand-in for ExecutionEngine: real conversion logic, no DB/step-tracking."""

    _apply_mapping_overrides_from_config = staticmethod(
        ExecutionEngine._apply_mapping_overrides_from_config
    )

    def _record_step(self, *args, **kwargs):
        pass


def _tmsl(model_name="AnyInternalPbixModelName"):
    # The internal TMSL model name is deliberately generic/shared across all
    # fixtures below — it must NOT leak into the deployed view name; only the
    # PBIX file's own filename may.
    return {
        "model": {
            "name": model_name,
            "tables": [
                {
                    "name": "SalesFact",
                    "columns": [{"name": "Units", "dataType": "int64"}],
                    "measures": [{"name": "Total Units", "expression": "SUM([Units])"}],
                }
            ],
        }
    }


def _build_context(pbix_path: str, project_id: str):
    return SimpleNamespace(
        source_format=SimpleNamespace(
            tmsl_definition=_tmsl(),
            row_counts={},
            field_aliases=[],
            pbix_path=pbix_path,
        ),
        drop_ledger=DropLedger(),
        project_id=project_id,
        osi_model=None,
        config_path=None,
        config_payload=None,
    )


def _deployed_view_name(sml_model) -> str:
    """Mirrors ddl_builder.py's _generate_semantic_view() exactly."""
    view_name_raw = sml_model.unique_name or sml_model.label or "model"
    return get_target_deployment_name(view_name_raw, "snowflake")


# ---------------------------------------------------------------------------
# (a) Single PBIX file deploys under its own correctly sanitized filename.
# ---------------------------------------------------------------------------


def test_single_pbix_sync_view_name_derives_from_its_own_filename_not_project_id():
    context = _build_context(
        pbix_path="C:/Reports/Sales Report.pbix",
        project_id="some-unrelated-project-guid-123",
    )

    sml_model = _convert_pbix_to_sml(_EngineStub(), context)

    assert sml_model.unique_name == "SALES_REPORT"
    assert "some-unrelated-project-guid-123" not in sml_model.unique_name.lower()

    deployed_name = _deployed_view_name(sml_model)
    assert deployed_name == "SALES_REPORT_SEMANTIC"
    # Must agree with the collision-detector's independent computation —
    # otherwise the check and reality could silently drift apart again.
    assert deployed_name == get_pbix_deployment_view_name("C:/Reports/Sales Report.pbix", "snowflake")


# ---------------------------------------------------------------------------
# (b) A batch of 3 differently-named PBIX files deploys as 3 separate,
#     independently-named views — not one combined view — even though every
#     job in the batch shares the SAME project_id (reproducing the exact
#     batch-sharing condition that caused the collapse).
# ---------------------------------------------------------------------------


def test_multi_pbix_batch_sharing_one_project_id_still_produces_distinct_view_names():
    shared_project_id = "shared-batch-project-id"
    files = [
        "C:/Reports/Sales Report.pbix",
        "C:/Reports/Marketing Analysis.pbix",
        "C:/Reports/Inventory Summary.pbix",
    ]

    sml_models = [
        _convert_pbix_to_sml(_EngineStub(), _build_context(path, shared_project_id))
        for path in files
    ]

    unique_names = [m.unique_name for m in sml_models]
    deployed_names = [_deployed_view_name(m) for m in sml_models]

    assert unique_names == ["SALES_REPORT", "MARKETING_ANALYSIS", "INVENTORY_SUMMARY"]
    assert deployed_names == [
        "SALES_REPORT_SEMANTIC",
        "MARKETING_ANALYSIS_SEMANTIC",
        "INVENTORY_SUMMARY_SEMANTIC",
    ]
    # The core regression: all 3 must be distinct, i.e. 3 independent
    # "CREATE OR REPLACE SEMANTIC VIEW" statements, not 1 shared name that
    # the 2nd and 3rd job would silently overwrite in turn.
    assert len(set(deployed_names)) == 3
    # None of them accidentally fell back to the shared project_id.
    assert all(shared_project_id not in name.lower() for name in unique_names)


# ---------------------------------------------------------------------------
# (c) Two files that would collide on the same sanitized name are caught and
#     blocked with a clear error before any deployment attempt — for either
#     of the colliding files, regardless of which one is listed first.
# ---------------------------------------------------------------------------


def test_colliding_filenames_are_blocked_before_deployment_regardless_of_order():
    colliding_a = "C:/Reports/Sales Report.pbix"
    colliding_b = "C:/Reports/Sales_Report.pbix"

    # Confirms *why* this must be blocked: both really do resolve to the
    # same deployed view name via the exact conversion path used at sync time.
    name_a = _deployed_view_name(_convert_pbix_to_sml(_EngineStub(), _build_context(colliding_a, "proj-x")))
    name_b = _deployed_view_name(_convert_pbix_to_sml(_EngineStub(), _build_context(colliding_b, "proj-x")))
    assert name_a == name_b == "SALES_REPORT_SEMANTIC"

    # Blocked regardless of which colliding file is listed first.
    with pytest.raises(ValidationError, match="Naming collision detected"):
        validate_no_pbix_view_name_collisions([colliding_a, colliding_b])
    with pytest.raises(ValidationError, match="Naming collision detected"):
        validate_no_pbix_view_name_collisions([colliding_b, colliding_a])


def test_build_sync_jobs_blocks_the_whole_batch_when_two_of_three_files_collide(tmp_path):
    """End-to-end at the real job-construction entry point: a 3rd, distinct
    file in the same batch does not mask or get skipped past the collision
    between the other two — the whole batch is blocked before any
    ExecutionEngine.execute() call is made."""
    from semabridge.api.services import sync_execution_service as ses

    file_a = tmp_path / "Sales Report.pbix"
    file_b = tmp_path / "Sales_Report.pbix"
    file_c = tmp_path / "Marketing Analysis.pbix"
    for f in (file_a, file_b, file_c):
        f.write_bytes(b"data")

    source_cfg = {"type": "pbix", "models": [str(file_a), str(file_b), str(file_c)]}
    with pytest.raises(ValidationError, match="Naming collision detected"):
        ses._build_sync_jobs({"source": source_cfg, "targets": [{"type": "snowflake"}]})
