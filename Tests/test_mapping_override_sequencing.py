"""Regression tests for the mapping-override sequencing bug.

_apply_mapping_overrides_from_config renames metrics from their raw
source-model names to sanitized target names, and rewrites any sibling
metric's DAX that references the renamed measure by its old bracket name
(e.g. TOTALYTD([Old Name], ...) -> TOTALYTD([NEW_NAME], ...)). If that
rewrite happens AFTER OSIToSMLConverter().from_osi() has already attempted
translation, the dependent metric's DAX still has the stale bracket text at
translation time, the measure registry never matches it, translation
declines, and nothing ever retries — the metric is permanently stuck
unresolved even though the rename+rewrite technically "succeeded" a moment
too late. The fix applies the override to the OSI model before Phase 2
(from_osi's translation) runs, in _convert_pbix_to_sml / _convert_fabric_to_sml
/ _convert_snowflake_to_sml's semantic-view branch.
"""
from __future__ import annotations

from types import SimpleNamespace

from semabridge.core.drop_ledger import DropLedger
from semabridge.core.execution_engine import ExecutionEngine
from semabridge.core.engine.conversion.pbix import _convert_pbix_to_sml


class _EngineStub:
    """Minimal stand-in for ExecutionEngine: real override logic, no DB/step-tracking."""

    _apply_mapping_overrides_from_config = staticmethod(
        ExecutionEngine._apply_mapping_overrides_from_config
    )

    def _record_step(self, *args, **kwargs):
        pass


def _tmsl_with_renamed_dependency():
    # "Pct Units Of Cost" references its dependencies by the OVERRIDE'S
    # TARGET names (TOTAL_UNITS / TOTAL_COST), not by "Total Units" /
    # "Total Cost" — the raw names those measures actually have at
    # extraction time, before any override runs. This models the real,
    # observed failure: a project's mappings_overrides exists specifically
    # to reconcile a pre-existing naming inconsistency between a measure's
    # own registered name and how OTHER measures' raw DAX already
    # reference it. Translating "Pct Units Of Cost" can only succeed once
    # "Total Units"/"Total Cost" have already been renamed to match.
    return {
        "model": {
            "name": "SequencingTestModel",
            "tables": [
                {
                    "name": "SalesFact",
                    "columns": [
                        {"name": "Units", "dataType": "int64"},
                        {"name": "Cost", "dataType": "double"},
                    ],
                    "measures": [
                        {"name": "Total Units", "expression": "SUM([Units])"},
                        {"name": "Total Cost", "expression": "SUM([Cost])"},
                        {
                            "name": "Pct Units Of Cost",
                            "expression": "DIVIDE([TOTAL_UNITS], [TOTAL_COST], 0)",
                        },
                    ],
                }
            ],
        }
    }


def _override_payload():
    return {
        "mappings_overrides": [
            {"source_path": "metrics.Total Units", "target_name": "TOTAL_UNITS"},
            {"source_path": "metrics.Total Cost", "target_name": "TOTAL_COST"},
            {
                "source_path": "metrics.Pct Units Of Cost",
                "target_name": "PCT_UNITS_OF_COST",
            },
        ]
    }


def _build_context(config_path=None, config_payload=None):
    return SimpleNamespace(
        source_format=SimpleNamespace(
            tmsl_definition=_tmsl_with_renamed_dependency(),
            row_counts={},
            field_aliases=[],
        ),
        drop_ledger=DropLedger(),
        project_id="seq-test-project",
        osi_model=None,
        config_path=config_path,
        config_payload=config_payload,
    )


def test_dependent_metric_resolves_when_override_applied_before_translation():
    context = _build_context(config_path="nonexistent.yaml", config_payload=_override_payload())

    sml_model = _convert_pbix_to_sml(_EngineStub(), context)

    by_name = {m.unique_name: m for m in sml_model.metrics}
    assert "TOTAL_UNITS" in by_name
    assert "PCT_UNITS_OF_COST" in by_name

    pct_metric = by_name["PCT_UNITS_OF_COST"]
    assert pct_metric.sql_expression is not None, (
        "PCT_UNITS_OF_COST's DIVIDE operands reference TOTAL_UNITS/TOTAL_COST "
        "(the override's TARGET names) while those measures are still raw "
        "'Total Units'/'Total Cost' at extraction time; the override must "
        "rename them before translation runs for this to resolve."
    )
    assert pct_metric.sync_enabled is True


def test_dependent_metric_stays_unresolved_without_the_pre_translation_override():
    """Same model, but simulating the pre-fix ordering: apply the override
    to the already-converted SML model, after translation already ran
    (i.e. no config wired into the OSI-phase context at all). This proves
    the bug this fix closes is real, not just a redundant safety net."""
    context = _build_context(config_path=None, config_payload=None)

    sml_model = _convert_pbix_to_sml(_EngineStub(), context)

    by_name = {m.unique_name: m for m in sml_model.metrics}
    # Metrics are still under their raw, un-renamed source names.
    assert "Pct Units Of Cost" in by_name
    pct_metric = by_name["Pct Units Of Cost"]
    # sql_expression is the reliable "did translation actually succeed"
    # signal here — sync_enabled's initial optimistic classification for
    # DIVIDE-shaped DAX doesn't get corrected back to False on failure
    # (a separate, already-tracked gating quirk), so it isn't asserted on.
    assert pct_metric.sql_expression is None

    # Applying the override now (post-translation, the old sequencing)
    # renames the metric but cannot retroactively fix its translation.
    ExecutionEngine._apply_mapping_overrides_from_config(
        sml_model, "nonexistent.yaml", config_payload=_override_payload()
    )
    by_name = {m.unique_name: m for m in sml_model.metrics}
    assert "PCT_UNITS_OF_COST" in by_name
    assert by_name["PCT_UNITS_OF_COST"].sql_expression is None, (
        "This mirrors the bug: the rename applied too late still leaves the "
        "metric unresolved because translation already gave up."
    )
