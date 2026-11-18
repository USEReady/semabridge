"""'Date' deploys as DATE, not COL_DATE.

Snowflake does not reserve type keywords such as DATE (docs: reserved
keywords, "with the exception of type keywords such as CHAR, DATE,
DECIMAL"). Only DIMENSION / FACT names follow that; physical columns,
metric names and metric SQL keep the old rule, so existing tables (with a
COL_DATE column) and metric references keep working.
"""
from __future__ import annotations

import logging
from pathlib import Path

from semabridge.api.services.project_mapping_engine import build_entity_mappings
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.engine.config import _apply_mapping_overrides_from_config
from semabridge.core.settings import SnowflakeConfig
from semabridge.intermediate.models import (
    OSIAggregationType, OSIColumn, OSIDataset, OSIDataType, OSIMetric, OSIModel,
)
from semabridge.utils.identifiers import IdentifierSanitizer, is_legacy_auto_column_name


def _model():
    return OSIToSMLConverter().from_osi(OSIModel(
        unique_name="Demo", label="Demo", source_platform="pbix",
        datasets=[
            OSIDataset(unique_name="Calendar", columns=[
                OSIColumn(unique_name="Date", data_type=OSIDataType.DATE),
                OSIColumn(unique_name="Order", data_type=OSIDataType.STRING),
            ]),
            OSIDataset(unique_name="Sales", columns=[OSIColumn(unique_name="Amount", data_type=OSIDataType.FLOAT)]),
        ],
        metrics=[OSIMetric(unique_name="Count", dataset="Sales", source_column="Amount", aggregation=OSIAggregationType.SUM)],
        relationships=[],
    ))


def _dims(model):
    logging.disable(logging.WARNING)
    try:
        cfg = SnowflakeConfig(account="t.local", user="u", password="p", warehouse="w",
                              database="DB", schema_name="SCH", role="r")
        ddl = next(d for d in SnowflakeEmitter(cfg, ConnectorBehavior()).generate_ddls(model) if "SEMANTIC VIEW" in d.upper())
    finally:
        logging.disable(logging.NOTSET)
    return ddl


def test_semantic_name_vs_physical_name():
    s = IdentifierSanitizer()
    assert s.sanitize_semantic_alias("Date") == "DATE"
    assert s.sanitize_column("Date") == "COL_DATE"       # physical column unchanged
    assert s.to_alias("Date") == "COL_DATE"              # metric names / SQL refs unchanged
    assert s.sanitize_semantic_alias("Order") == "COL_ORDER"  # really reserved
    assert s.sanitize_semantic_alias("Customer Name") == "CUSTOMER_NAME"


def test_deployed_dimension_is_date_reading_the_existing_col_date_column():
    ddl = _dims(_model())
    assert 'CALENDAR."DATE" AS CALENDAR."COL_DATE"' in ddl
    assert 'CALENDAR."COL_ORDER"' in ddl
    assert 'SALES."COL_COUNT"' in ddl  # metrics keep the old rule


def test_dry_run_shows_the_deployed_names():
    out = build_entity_mappings(project_id="p", model=_model().model_dump(mode="json"), target_connector="snowflake")
    names = {r["source_name"]: (r["target_name"], r["validation_code"]) for r in out["mappings"] if r["entity_kind"] in ("column", "metric")}
    assert names["Date"] == ("DATE", "OK")
    assert names["Order"] == ("COL_ORDER", "OK")
    assert names["Count"] == ("COL_COUNT", "OK")


def test_a_saved_old_automatic_name_is_ignored_but_a_real_choice_is_kept():
    assert is_legacy_auto_column_name("Date", "COL_DATE")
    assert not is_legacy_auto_column_name("Date", "CALENDAR_DATE")
    assert not is_legacy_auto_column_name("Order", "COL_ORDER")  # still the current rule
    model = _model()
    _apply_mapping_overrides_from_config(model, Path("/nonexistent"), {
        "target": {"type": "snowflake"},
        "mappings_overrides": [
            {"source_path": "datasets.Calendar.columns.Date", "target_name": "COL_DATE"},  # old auto name
        ],
    })
    assert 'CALENDAR."DATE" AS CALENDAR."COL_DATE"' in _dims(model)
    model = _model()
    _apply_mapping_overrides_from_config(model, Path("/nonexistent"), {
        "target": {"type": "snowflake"},
        "mappings_overrides": [{"source_path": "datasets.Calendar.columns.Date", "target_name": "Calendar Date"}],
    })
    assert 'CALENDAR."CALENDAR_DATE" AS CALENDAR."COL_DATE"' in _dims(model)
