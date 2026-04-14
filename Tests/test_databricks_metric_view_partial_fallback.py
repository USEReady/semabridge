from unittest.mock import patch

from semabridge.connectors.databricks_publisher import DatabricksPublishError, DatabricksPublisher
from semabridge.core.behavior import ConnectorBehavior, DatabricksBehavior
from semabridge.core.settings import DatabricksConfig
from semabridge.sml.models import SMLModel


def _cfg() -> DatabricksConfig:
    return DatabricksConfig(
        host="dbc-b3ffac48-2f5a.cloud.databricks.com",
        token="dummy",
        warehouse_id="wh-1",
        catalog="main",
        schema_name="public",
    )


def test_publish_falls_back_to_sql_when_any_metric_view_fails():
    behavior = ConnectorBehavior(
        databricks=DatabricksBehavior(
            measure_view_type="metric_view",
            measure_view_mode="combined",
            create_measure_views=True,
        )
    )
    publisher = DatabricksPublisher(_cfg(), behavior=behavior)
    model = SMLModel(unique_name="Inventory Semantic Model Project", datasets=[], metrics=[])

    metric_view_sql = [
        "CREATE OR REPLACE VIEW `main`.`public`.`mv_Inventory_Semantic_Model_Project_Measures` WITH METRICS LANGUAGE YAML AS $$\nversion: 1.1\n$$",
        "CREATE OR REPLACE VIEW `main`.`public`.`mv_Inventory_Semantic_Model_Project_Other` WITH METRICS LANGUAGE YAML AS $$\nversion: 1.1\n$$",
    ]
    sql_fallback_sql = [
        "CREATE OR REPLACE VIEW `main`.`public`.`mv_Inventory_Semantic_Model_Project_Measures` AS SELECT 1 AS `x`",
        "CREATE OR REPLACE VIEW `main`.`public`.`mv_Inventory_Semantic_Model_Project_Other` AS SELECT 2 AS `y`",
    ]

    requested_overrides: list[str | None] = []

    def _mock_generate_views(sml_model, view_type_override=None):
        requested_overrides.append(view_type_override)
        if view_type_override == "sql_view":
            return sql_fallback_sql, len(sql_fallback_sql), 0, []
        return metric_view_sql, len(metric_view_sql), 0, []

    def _mock_execute(statements, *args, **kwargs):
        text = "\n".join(statements)
        if "WITH METRICS LANGUAGE YAML" in text and "_Measures" in text:
            raise DatabricksPublishError(
                "Databricks statement state=FAILED: {'error_code': 'BAD_REQUEST', 'message': '[METRIC_VIEW_INVALID_VIEW'}"
            )
        return []

    with patch.object(publisher, "_auto_initialize_missing_tables", return_value=None), patch.object(
        publisher,
        "_determine_view_type",
        return_value="metric_view",
    ), patch.object(
        publisher,
        "_select_measure_view_mode_for_quota",
        return_value="combined",
    ), patch.object(
        publisher,
        "generate_sql_statements",
        return_value=["CREATE TABLE x (id INT)", *metric_view_sql],
    ), patch.object(
        publisher,
        "generate_measure_view_statements",
        side_effect=_mock_generate_views,
    ), patch.object(
        publisher,
        "execute_statements",
        side_effect=_mock_execute,
    ) as execute_mock:
        publisher.publish(model)

    assert "sql_view" in requested_overrides


def test_publish_fallback_persists_sql_debug_artifacts(tmp_path):
    behavior = ConnectorBehavior(
        databricks=DatabricksBehavior(
            measure_view_type="metric_view",
            measure_view_mode="combined",
            create_measure_views=True,
        )
    )
    publisher = DatabricksPublisher(_cfg(), behavior=behavior)
    model = SMLModel(unique_name="Inventory Semantic Model Project", datasets=[], metrics=[])

    metric_view_sql = [
        "CREATE OR REPLACE VIEW `main`.`public`.`mv_Inventory_Semantic_Model_Project_Measures` WITH METRICS LANGUAGE YAML AS $$\nversion: 1.1\n$$",
    ]
    sql_fallback_sql = [
        "CREATE OR REPLACE VIEW `main`.`public`.`mv_Inventory_Semantic_Model_Project_Measures_measures` AS SELECT 1 AS `x`",
    ]

    def _mock_generate_views(sml_model, view_type_override=None):
        if view_type_override == "sql_view":
            return sql_fallback_sql, len(sql_fallback_sql), 0, []
        return metric_view_sql, len(metric_view_sql), 0, []

    def _mock_execute(statements, *args, **kwargs):
        text = "\n".join(statements)
        if "WITH METRICS LANGUAGE YAML" in text:
            raise DatabricksPublishError("forced metric-view failure")
        return []

    with patch.object(publisher, "_auto_initialize_missing_tables", return_value=None), patch.object(
        publisher,
        "_determine_view_type",
        return_value="metric_view",
    ), patch.object(
        publisher,
        "_select_measure_view_mode_for_quota",
        return_value="combined",
    ), patch.object(
        publisher,
        "generate_sql_statements",
        return_value=["CREATE TABLE x (id INT)", *metric_view_sql],
    ), patch.object(
        publisher,
        "generate_measure_view_statements",
        side_effect=_mock_generate_views,
    ), patch.object(
        publisher,
        "execute_statements",
        side_effect=_mock_execute,
    ), patch.object(
        publisher,
        "_debug_sql_artifact_dir",
        return_value=tmp_path,
    ):
        publisher.publish(model)

    sql_artifacts = list(tmp_path.glob("*.sql"))
    assert sql_artifacts
    assert "CREATE OR REPLACE VIEW" in sql_artifacts[0].read_text(encoding="utf-8")
