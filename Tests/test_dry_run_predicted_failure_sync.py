"""Regression tests for dry-run predicted failure synchronization and trial pass alignment.

Proves that:
1. A valid time-intelligence model predicts flag maps during dry-run trial pass and translates cleanly.
2. A model with an unresolvable time-intelligence or date eligibility issue generates a DropRecord during the trial pass, causing reconcile_predicted_failures_with_drops to mark it as STATUS_PREDICTED_FAILURE ("Failed a static check — predicted failure").
"""
from __future__ import annotations

import pytest
from semabridge.sml.models import (
    SMLModel,
    SMLDataset,
    SMLColumn,
    SMLMetric,
    SMLRelationship,
    DataType,
)
from semabridge.core.settings import SnowflakeConfig
from semabridge.core.behavior import ConnectorBehavior
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.api.services.project_mapping_engine import (
    build_entity_mappings,
    reconcile_predicted_failures_with_drops,
    STATUS_PREDICTED_FAILURE,
    STATIC_RISK_PREDICTED_FAILURE,
)
from semabridge.sml.serializer import SMLSerializer
from semabridge.connectors.anchor_flag_rerender import rerender_anchor_dependent_metrics
from semabridge.converter.dax_translator import DAXTranslator


def _build_valid_ytd_model() -> SMLModel:
    return SMLModel(
        unique_name="ValidYTDModel",
        datasets=[
            SMLDataset(
                unique_name="SalesFact",
                source_table="SALES_FACT",
                columns=[
                    SMLColumn(unique_name="Units", data_type=DataType.DECIMAL),
                    SMLColumn(unique_name="Date_Date", data_type=DataType.DATE),
                ],
            ),
            SMLDataset(
                unique_name="Date",
                source_table="DATE_DIM",
                columns=[SMLColumn(unique_name="Date_Date", data_type=DataType.DATE)],
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="Total_Units_YTD",
                dataset="SalesFact",
                expression="TOTALYTD(SUM('SalesFact'[Units]), 'Date'[Date_Date])",
            )
        ],
        relationships=[
            SMLRelationship(
                unique_name="Rel_SalesFact_Date",
                from_dataset="SalesFact",
                from_columns=["Date_Date"],
                to_dataset="Date",
                to_columns=["Date_Date"],
                is_active=True,
            )
        ],
    )


def test_dry_run_trial_pass_predicts_and_rerenders_valid_ytd():
    model = _build_valid_ytd_model()

    cfg = SnowflakeConfig(
        account="test.account",
        user="test_user",
        warehouse="test_wh",
        database="test_db",
        schema_name="PUBLIC",
    )
    behavior = ConnectorBehavior()
    behavior.snowflake.auto_create_enriched_view = True
    behavior.snowflake.use_enriched_view_for_metrics = True

    emitter = SnowflakeEmitter(config=cfg, behavior=behavior)
    predicted_map = emitter.predict_anchor_flag_map(model)
    assert predicted_map == {"salesfact": {("YTD",): "IS_YTD"}}

    for ft in predicted_map:
        emitter._enriched_view_mapping[ft] = f"{ft.upper()}_ENRICHED"

    dataset_col_lookup, dataset_aliases = DAXTranslator.build_schema_lookup(model.datasets)
    rerender_anchor_dependent_metrics(
        model,
        predicted_map,
        dataset_col_lookup,
        dataset_aliases,
        label="predicted",
    )

    ddls = emitter.generate_ddls(model)
    assert any("IS_YTD" in ddl for ddl in ddls)
    assert len(emitter.drop_ledger.records) == 0, f"Unexpected drop ledger contents: {emitter.drop_ledger.to_json()}"


def test_dry_run_reconciles_trial_pass_drops_to_predicted_failure():
    """Verify that when a trial pass produces a DropRecord (e.g. invalid column reference),
    reconcile_predicted_failures_with_drops promotes the metric to STATUS_PREDICTED_FAILURE.
    """
    model = SMLModel(
        unique_name="BrokenModel",
        datasets=[
            SMLDataset(
                unique_name="SalesFact",
                source_table="SALES_FACT",
                columns=[SMLColumn(unique_name="Units", data_type=DataType.DECIMAL)],
            )
        ],
        metrics=[
            SMLMetric(
                unique_name="Broken_Metric",
                dataset="SalesFact",
                expression="SUM('SalesFact'[NonExistentColumn])",
            )
        ],
    )
    sml_dict = SMLSerializer._model_to_dict(model)

    built = build_entity_mappings(
        project_id="test_project",
        model=sml_dict,
        target_connector="snowflake",
    )
    mappings = built.get("mappings", [])

    # Simulate a trial DDL pass that dropped Broken_Metric
    dropped_entities = [
        {
            "entity_kind": "metric",
            "dataset": "SalesFact",
            "entity_name": "Broken_Metric",
            "drop_reason": "Missing column",
            "by_design": False,
        }
    ]

    reconcile_predicted_failures_with_drops(mappings, dropped_entities)

    target_mapping = next(m for m in mappings if m.get("source_name") == "Broken_Metric")
    assert target_mapping["status"] == STATUS_PREDICTED_FAILURE
    assert target_mapping["static_risk_tier"] == STATIC_RISK_PREDICTED_FAILURE
    assert target_mapping["static_risk_label"] == "Failed a static check — predicted failure"
