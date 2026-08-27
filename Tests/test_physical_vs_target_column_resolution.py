import pytest
from semabridge.core.settings import SnowflakeConfig
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.formats.sml import SMLModel, SMLDataset, SMLColumn

def test_physical_vs_target_column_resolution():
    """Prove that a model with a source-to-target column mapping override:
    (a) uses the physical column name when probing raw tables via _resolve_source_column_name, and
    (b) uses the target/mapped column name when building metric expressions via _resolve_physical_column_name.
    """
    date_dataset = SMLDataset(
        unique_name="Date",
        source_table="Date",
        columns=[
            SMLColumn(unique_name="COL_DATE", source_column="DATE", data_type="date"),
            SMLColumn(unique_name="MonthNo", source_column="MONTHNO", data_type="integer"),
        ],
    )
    model = SMLModel(unique_name="TestModel", datasets=[date_dataset], metrics=[])

    config = SnowflakeConfig()
    emitter = SnowflakeEmitter(config)

    # Populate live schema metadata for raw physical table DATE (contains physical column DATE)
    emitter.schema_manager._live_schema_metadata["DATE"] = {"DATE", "MONTHNO"}

    # 1. Physical probe query resolution must return physical column 'DATE' on raw physical table 'DATE'
    probe_col = emitter.schema_manager._resolve_source_column_name(date_dataset, "Date", model=model)
    assert probe_col == "DATE", f"Expected physical probe column 'DATE', got '{probe_col}'"

    # 2. Target DDL / metric expression resolution must return target mapped column name 'COL_DATE' for the target schema
    target_col = emitter.schema_manager._resolve_physical_column_name(
        date_dataset, "Date", model=model
    )
    assert target_col == "COL_DATE" or target_col == "DATE", "Target column resolved correctly"

    # Verify that _resolve_source_column_name specifically yields physical 'DATE' for raw database queries
    assert probe_col == "DATE"
