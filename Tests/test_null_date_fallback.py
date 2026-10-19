from unittest.mock import MagicMock
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.settings import SnowflakeConfig
from semabridge.sml.models import SMLModel, SMLDataset, SMLColumn, SMLMetric, SMLRelationship

def test_enriched_view_null_max_date_fallback():
    cfg = SnowflakeConfig(
        account="TEST", user="TEST", password="PW",
        warehouse="WH", database="SEMABRIDGE", schema_name="SEMABRIDGE_WORKSPACE",
        role="ROLE"
    )
    behavior = ConnectorBehavior()
    emitter = SnowflakeEmitter(config=cfg, behavior=behavior)

    model = SMLModel(
        unique_name="Corporate Spend",
        label="Corporate Spend",
        datasets=[
            SMLDataset(
                unique_name="Date",
                source_table="CORPORATE_SPEND_DATE",
                columns=[
                    SMLColumn(unique_name="Date", data_type="date", is_key=True),
                    SMLColumn(unique_name="Period", data_type="integer"),
                ]
            ),
            SMLDataset(
                unique_name="Fact",
                source_table="CORPORATE_SPEND_FACT",
                is_fact=True,
                columns=[
                    SMLColumn(unique_name="COL_DATE", data_type="date"),
                    SMLColumn(unique_name="COL_VALUE", data_type="decimal"),
                    SMLColumn(unique_name="COST_ELEMENT_ID", data_type="string", is_key=True),
                ]
            )
        ],
        relationships=[
            SMLRelationship(
                unique_name="Rel_Fact_Date",
                from_dataset="Fact",
                from_columns=["COL_DATE"],
                to_dataset="Date",
                to_columns=["Date"],
                is_active=True,
            )
        ],
        metrics=[
            SMLMetric(
                unique_name="AMOUNT",
                dataset="Fact",
                expression="TOTALYTD(SUM(Fact[Value]), 'Date'[Date]) * 0.3",
                sql_expression="SUM(CASE WHEN FACT.IS_YTD THEN FACT.COL_VALUE::FLOAT END) * 0.3"
            )
        ]
    )

    executed_sql = []
    mock_cursor = MagicMock()
    def mock_execute(sql, *args, **kwargs):
        executed_sql.append(str(sql))
        return None
    mock_cursor.execute.side_effect = mock_execute
    # Simulate MAX(COL_DATE) returning NULL
    mock_cursor.fetchone.return_value = (None,)

    view_name = emitter._create_enriched_view(model, mock_cursor, fact_table="Fact")
    print("Created enriched view:", view_name)
    assert view_name is not None
    create_view_sql = [s for s in executed_sql if "CREATE OR REPLACE VIEW" in s][0]
    print("Generated CREATE VIEW SQL:\n", create_view_sql)
    # The anchor is computed inside the view (empty/NULL dates fall back to
    # CURRENT_DATE() at query time); no anchor query runs at sync time.
    assert 'COALESCE(MAX("' in create_view_sql and 'CURRENT_DATE()) AS "MAX_DATE"' in create_view_sql
    assert not [s for s in executed_sql if s.lstrip().upper().startswith("SELECT MAX(")]
    assert "AS \"IS_YTD\"" in create_view_sql
    assert "IS_YTD" in emitter._live_schema_metadata.get(view_name.upper(), set())
    print("\nSUCCESS! Enriched view projects IS_YTD with a computed MAX_DATE anchor.")

if __name__ == "__main__":
    test_enriched_view_null_max_date_fallback()
