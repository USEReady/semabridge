"""TDD test to diagnose which code path _generate_measure_view_statements is taking."""
import pytest
from semabridge.connectors.databricks_publisher import DatabricksPublisher, VIEW_TYPE_METRIC
from semabridge.sml.models import SMLModel, SMLDataset, SMLMetric, SMLRelationship, SMLColumn, DataType
from semabridge.core.settings import DatabricksConfig
from semabridge.core.behavior import ConnectorBehavior, DatabricksBehavior
from pathlib import Path


@pytest.fixture
def databricks_config():
    """Create a test config."""
    return DatabricksConfig(
        host="localhost",
        token="test_token",
        catalog="semabridge",
        schema_name="public",
    )


@pytest.fixture
def behavior_config():
    """Create behavior config with all required settings."""
    return ConnectorBehavior(
        databricks=DatabricksBehavior(
            create_metadata_table=True,
            create_measure_views=True,
            model_artifact_mode="per_model",  # THIS IS KEY
            model_metadata_suffix="metadata",
            model_metric_view_suffix="metric_view",
            model_fact_root="",
            measure_view_type="metric_view",  # THIS IS KEY
            measure_view_mode="combined",
            view_prefix="mv",
            enable_simple_dax_translation=True,
            metric_view_only_sum_translation=True,
            enable_cross_table_joins=True,
            enable_metric_view_joins=True,
            enable_cross_table_sql_fallback=False,
            enable_low_confidence_drafts=True,
            emit_metric_views_for_all_datasets=True,
            source_catalog="semabridge",
            source_schema="public",
            source_table_mapping={
                "fact_table": "semabridge.public.fact_table",
                "Project_Measures": "semabridge.public.project_measures",
            }
        )
    )


def test_generate_measure_view_statements_uses_metric_view_when_per_model_mode(
    databricks_config, behavior_config
):
    """Test that generate_measure_view_statements produces YAML metric views in per_model mode."""
    publisher = DatabricksPublisher(databricks_config, behavior_config)

    # Mock the execute_statements to avoid actual DB calls
    publisher.execute_statements = lambda stmts, concurrent=False: []

    # Create a minimal model with measures
    model = SMLModel(
        unique_name="test_model",
        label="Test Model",
        datasets=[
            SMLDataset(
                unique_name="Project_Measures",
                label="Project_Measures",
                columns=[],  # EMPTY - measure-only table
                metrics=[],
            ),
            SMLDataset(
                unique_name="fact_table",
                label="Fact Table",
                columns=[
                    SMLColumn(unique_name="fact_id", data_type=DataType.INTEGER),
                    SMLColumn(unique_name="amount", data_type=DataType.DECIMAL),
                ],
                metrics=[],
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="Total_Amount",
                dataset="Project_Measures",
                expression="SUM([Amount])",
                source_column="amount",
                aggregation="sum",
            ),
            SMLMetric(
                unique_name="Fact_Amount",
                dataset="fact_table",
                expression="SUM([Amount])",
                source_column="amount",
                aggregation="sum",
            ),
        ],
        relationships=[
            SMLRelationship(
                unique_name="fact_to_measures",
                is_active=True,
                from_dataset="fact_table",
                from_columns=["fact_id"],
                to_dataset="Project_Measures",
                to_columns=["project_id"],
            ),
        ],
    )

    # Debug: Check mode settings
    print(f"\n=== Mode Check ===")
    print(f" publisher._is_model_artifact_mode(): {publisher._is_model_artifact_mode()}")
    print(f" behavior_config.databricks.model_artifact_mode: {behavior_config.databricks.model_artifact_mode}")
    print(f" behavior_config.databricks.measure_view_type: {behavior_config.databricks.measure_view_type}")
    print(f" VIEW_TYPE_METRIC constant: {VIEW_TYPE_METRIC}")
    print(f" behavior_config.databricks.measure_view_type == VIEW_TYPE_METRIC: {behavior_config.databricks.measure_view_type == VIEW_TYPE_METRIC}")

    # Call generate_measure_view_statements with explicit override
    print(f"\n=== Calling generate_measure_view_statements ===")
    print(f" Passing view_type_override='metric_view'")
    stmts, created, skipped, details = publisher.generate_measure_view_statements(
        model, view_type_override="metric_view"
    )

    print(f"\n === Results ===")
    print(f" Statements generated: {len(stmts)}")
    if stmts:
        print(f" Statement type: {'METRIC VIEW' if 'WITH METRICS LANGUAGE YAML' in stmts[0] else 'SQL VIEW'}")
        print(f" First 200 chars:\n{stmts[0][:200]}")

    # ASSERT: Should generate YAML metric view, not SQL view
    assert len(stmts) > 0, "No statements generated"
    assert "WITH METRICS LANGUAGE YAML" in stmts[0], (
        f"Expected YAML metric view, got SQL view.\n"
        f"Statement snippet:\n{stmts[0][:300]}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
