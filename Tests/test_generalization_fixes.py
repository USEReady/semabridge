"""Tests for generalization fixes (Items 1 through 8).

All tests use synthetic models with arbitrary names.
"""
import pytest
from unittest.mock import MagicMock
from types import SimpleNamespace

from semabridge.connectors.table_binding_resolver import (
    normalize_col_name,
    compute_column_compatibility,
    rank_table_candidates,
)
from semabridge.converter.dax_rule_translator import (
    _date_alias,
    translate_time_intelligence,
    find_model_date_table,
    set_model,
)
from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
from semabridge.intermediate.models import (
    OSIModel,
    OSIDataset,
    OSIColumn,
    OSIDataType,
    OSIAggregationType,
    OSIRelationship,
    OSICardinality,
    OSICrossFilterDirection,
)
from semabridge.sml.models import (
    SMLModel,
    SMLDataset,
    SMLColumn,
    SMLMetric,
    SMLRelationship,
    DataType,
    AggregationType,
)
from semabridge.connectors.tables_clause_builder import TablesClauseBuilder
from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder
from semabridge.utils.identifiers import IdentifierSanitizer


# ============================================================================
# Item 1: table_binding_resolver.py
# strip COL_ prefix generically; ID-suffix match only when single candidate
# ============================================================================

def test_item1_table_binding_col_prefix_and_single_candidate_id_suffix():
    # Generic COL_ stripping on arbitrary reserved words
    assert normalize_col_name("COL_CUSTOM_RESERVED") == "CUSTOMRESERVED"
    assert normalize_col_name("col_foobar") == "FOOBAR"
    assert normalize_col_name("UNTOUCHED_COL") == "UNTOUCHEDCOL"

    # ID-suffix match used ONLY when it's the single candidate
    # Case A: exactly one table matches via ID suffix -> candidate accepted
    single_live_schema = {
        "SYNTH_ALPHA_TBL": {"ALPHA_ID", "NAME", "VALUE"},
    }
    ranked_single = rank_table_candidates(
        "Alpha", ["ALPHA", "NAME", "VALUE"], single_live_schema
    )
    assert len(ranked_single) == 1
    assert ranked_single[0]["table_name"] == "SYNTH_ALPHA_TBL"
    assert ranked_single[0]["col_compat"] == 1.0

    # Case B: multiple tables match via ID suffix -> ID suffix match NOT used, neither accepted via ID suffix
    multi_live_schema = {
        "SYNTH_ALPHA_TBL": {"ALPHA_ID", "NAME"},
        "SYNTH_BETA_TBL": {"ALPHA_ID", "BETA_EXTRA"},
    }
    ranked_multi = rank_table_candidates(
        "Alpha", ["ALPHA", "OTHER_COL"], multi_live_schema
    )
    # Neither qualifies with >= 0.8 compat without ID suffix, and because multiple match with ID suffix,
    # neither qualifies via ID suffix either
    viable_multi = [c for c in ranked_multi if c["col_compat"] >= 0.8]
    assert len(viable_multi) == 0


# ============================================================================
# Item 2: dax_rule_translator.py _date_alias
# use model date table (marked as date table or target of date relationships), not "Date" / env
# ============================================================================

def test_item2_dax_rule_translator_date_alias_uses_model_date_table(monkeypatch):
    monkeypatch.setenv("SEMABRIDGE_DATE_ALIAS", "WRONG_ENV_DATE_ALIAS")

    # Synthetic model with arbitrary table names: FACT_SALES -> REL_TARGET_CALENDAR
    fact_ds = SMLDataset(
        unique_name="Synthetic_Fact",
        columns=[SMLColumn(unique_name="TRANSACTION_DATE", data_type=DataType.DATE)],
    )
    cal_ds = SMLDataset(
        unique_name="CALENDAR_DIM_ARBITRARY",
        is_date_table=True,
        columns=[SMLColumn(unique_name="CAL_DATE", data_type=DataType.DATE, is_key=True)],
    )
    rel = SMLRelationship(
        unique_name="rel_fact_cal",
        from_dataset="Synthetic_Fact",
        from_columns=["TRANSACTION_DATE"],
        to_dataset="CALENDAR_DIM_ARBITRARY",
        to_columns=["CAL_DATE"],
    )
    model = SMLModel(
        unique_name="SynthModel",
        datasets=[fact_ds, cal_ds],
        relationships=[rel],
    )

    resolved = _date_alias(model)
    assert resolved == "CALENDAR_DIM_ARBITRARY"
    assert resolved != "Date"
    assert resolved != "WRONG_ENV_DATE_ALIAS"


# ============================================================================
# Item 3: osi_to_sml.py _inject_calendar_dimension
# remove (no tables that aren't in the PBIX)
# ============================================================================

def test_item3_osi_to_sml_does_not_inject_calendar_dimension():
    converter = OSIToSMLConverter()
    assert not hasattr(converter, "_inject_calendar_dimension")

    # Model with only arbitrary custom table
    osi_ds = OSIDataset(
        unique_name="ARBITRARY_CUSTOM_DATASET",
        columns=[OSIColumn(unique_name="CUSTOM_ID", data_type=OSIDataType.INTEGER)],
    )
    osi_model = OSIModel(
        unique_name="CustomModel",
        datasets=[osi_ds],
    )
    sml = converter.from_osi(osi_model)

    dataset_names = [d.unique_name for d in sml.datasets]
    assert dataset_names == ["ARBITRARY_CUSTOM_DATASET"]
    assert "Date" not in dataset_names
    assert "DIM_DATE" not in [d.source_table for d in sml.datasets]


# ============================================================================
# Item 4: tmsl_to_osi.py
# relationship inference from column names off by default
# ============================================================================

def test_item4_tmsl_to_osi_relationship_inference_off_by_default():
    tmsl_data = {
        "tmsl": {
            "model": {
                "name": "SyntheticModel",
                "tables": [
                    {
                        "name": "synth_orders",
                        "columns": [
                            {"name": "order_id", "dataType": "int64"},
                            {"name": "synth_customer_id", "dataType": "int64"},
                        ],
                    },
                    {
                        "name": "synth_customer",
                        "columns": [
                            {"name": "id", "dataType": "int64", "isKey": True},
                            {"name": "customer_name", "dataType": "string"},
                        ],
                    },
                ],
                # No explicit relationships in TMSL
            }
        },
        "workspace_id": "ws-synth",
        "dataset_id": "ds-synth",
    }

    # Off by default
    converter_default = TMSLToOSIConverter()
    osi_default = converter_default.to_osi(tmsl_data)
    assert len(osi_default.relationships) == 0

    # Explicitly enabled
    tmsl_data_enabled = dict(tmsl_data, infer_relationships=True)
    converter_enabled = TMSLToOSIConverter(infer_relationships=True)
    osi_enabled = converter_enabled.to_osi(tmsl_data_enabled)
    assert len(osi_enabled.relationships) >= 1


# ============================================================================
# Item 5: osi_to_sml.py _auto_detect_metrics
# remove; implicit metrics only from SummarizeBy
# ============================================================================

def test_item5_osi_to_sml_does_not_auto_detect_metrics_without_summarizeby():
    converter = OSIToSMLConverter()
    assert not hasattr(converter, "_auto_detect_metrics")

    # Numeric columns without default_aggregation (no summarizeBy)
    ds = OSIDataset(
        unique_name="SynthTable",
        columns=[
            OSIColumn(unique_name="ColNumA", data_type=OSIDataType.INTEGER, default_aggregation=None),
            OSIColumn(unique_name="ColNumB", data_type=OSIDataType.FLOAT, default_aggregation=None),
        ],
    )
    osi_model = OSIModel(unique_name="SynthModel", datasets=[ds])
    sml = converter.from_osi(osi_model)

    # No explicit or summarizeBy metrics -> metrics list must be empty
    assert len(sml.metrics) == 0


# ============================================================================
# Item 6: tables_clause_builder.py
# remove first-column primary key fallback
# ============================================================================

def test_item6_tables_clause_builder_no_first_column_pk_fallback():
    sanitizer = IdentifierSanitizer()
    behavior = SimpleNamespace(snowflake=SimpleNamespace(source_table_mapping=None))
    config = SimpleNamespace(database="SYNTH_DB", schema_name="SYNTH_SCHEMA")

    # Table with no relationships and no key columns
    ds = SMLDataset(
        unique_name="DISCONNECTED_DATASET",
        columns=[
            SMLColumn(unique_name="ARBITRARY_COL_A", is_key=False),
            SMLColumn(unique_name="ARBITRARY_COL_B", is_key=False),
        ],
    )
    model = SMLModel(unique_name="SynthModel", datasets=[ds], relationships=[])

    schema_manager = SimpleNamespace(
        _collect_physical_source_columns=lambda dataset: {c.unique_name: c for c in dataset.columns},
        _resolve_physical_column_name=lambda dataset, col, model=None: col,
    )
    builder = TablesClauseBuilder(
        identifier_sanitizer=sanitizer,
        schema_manager=schema_manager,
        config=config,
        behavior=behavior,
        live_schema_metadata={"DISCONNECTED_DATASET": {"ARBITRARY_COL_A", "ARBITRARY_COL_B"}},
    )
    registry = SimpleNamespace(get_alias=lambda name: name, register=lambda *args: None)
    lines, aliases, _, _, _, _ = builder.build_for_sml(
        model,
        registry=registry,
        metric_counts_by_dataset={},
        related_datasets=set(),
    )
    joined = "\n".join(lines)

    # Disconnected table with no keys must NOT receive a fabricated PRIMARY KEY
    assert "PRIMARY KEY" not in joined


# ============================================================================
# Item 7: metrics_clause_builder.py
# time-intelligence placeholder decided by whether a date relationship exists, not by data
# ============================================================================

def test_item7_time_intelligence_placeholder_decided_by_date_relationship():
    sanitizer = IdentifierSanitizer()
    config = SimpleNamespace(database="SYNTH_DB", schema_name="SYNTH_SCHEMA")

    class DummyTranslator:
        def __init__(self):
            self.column_data_types = {}
        def prefetch_openai_metric_translations(self, **kwargs):
            pass
        def _try_llm_metric_fallback_expression(self, **kwargs):
            return None
        def _auto_qualify_cross_table_refs(self, expr, aliases):
            return expr
        def translate_metric(self, *args, **kwargs):
            return None
    
    # Case A: Dataset has NO date relationship -> time-intelligence metric gets placeholder
    fact_no_date = SMLDataset(
        unique_name="FACT_NO_DATE",
        columns=[SMLColumn(unique_name="VAL", data_type=DataType.INTEGER)],
    )
    dim_other = SMLDataset(
        unique_name="DIM_OTHER",
        columns=[SMLColumn(unique_name="CODE", data_type=DataType.STRING, is_key=True)],
    )
    rel_other = SMLRelationship(
        unique_name="rel_other",
        from_dataset="FACT_NO_DATE",
        from_columns=["VAL"],
        to_dataset="DIM_OTHER",
        to_columns=["CODE"],
    )
    metric_ti = SMLMetric(
        unique_name="TOTAL_VAL_YTD",
        dataset="FACT_NO_DATE",
        expression="TOTALYTD(SUM([VAL]), 'DIM_OTHER'[CODE])",
    )
    model_no_date = SMLModel(
        unique_name="ModelNoDate",
        datasets=[fact_no_date, dim_other],
        metrics=[metric_ti],
        relationships=[rel_other],
    )

    builder = MetricsClauseBuilder(
        identifier_sanitizer=sanitizer,
        schema_manager=None,
        sanitizer=sanitizer,
        translator=DummyTranslator(),
        config=config,
    )
    emittable = set()
    lines = builder.build_for_sml(
        model_no_date,
        dataset_aliases={"FACT_NO_DATE": "FACT_NO_DATE", "DIM_OTHER": "DIM_OTHER"},
        dataset_by_name={"FACT_NO_DATE": fact_no_date, "DIM_OTHER": dim_other},
        dataset_col_lookup={"FACT_NO_DATE": {"VAL"}, "DIM_OTHER": {"CODE"}},
        alias_by_raw={"FACT_NO_DATE": "fact_no_date", "DIM_OTHER": "dim_other"},
        all_physical_col_names={"VAL", "CODE"},
        emittable_metric_name_set=emittable,
    )
    # Becomes placeholder because no date relationship exists
    assert any("CAST(NULL AS DOUBLE)" in line for line in lines)


# ============================================================================
# Item 8: dax_rule_translator.py translate_time_intelligence
# no window functions; use anchor flags or a placeholder with a reason
# ============================================================================

def test_item8_translate_time_intelligence_no_window_functions():
    dax = "TOTALYTD(SUM('SYNTH_SALES'[AMOUNT]), 'SYNTH_CAL'[CAL_DATE])"

    # With precomputed anchor flags -> uses anchor flag column
    flags = {("YTD",): "SYNTH_FLAG_YTD"}
    sql_with_flags = translate_time_intelligence(dax, "SYNTH_SALES", anchor_flag_map=flags)
    assert sql_with_flags is not None
    assert 'SYNTH_SALES."SYNTH_FLAG_YTD"' in sql_with_flags
    assert " OVER " not in sql_with_flags.upper()
    assert "OVER(" not in sql_with_flags.upper().replace(" ", "")

    # Without anchor flags -> placeholder with documented reason, NO window functions
    sql_without_flags = translate_time_intelligence(dax, "SYNTH_SALES")
    assert sql_without_flags is not None
    assert "CAST(NULL AS DOUBLE)" in sql_without_flags
    assert "reason:" in sql_without_flags
    assert " OVER " not in sql_without_flags.upper()
    assert "OVER(" not in sql_without_flags.upper().replace(" ", "")


# ============================================================================
# Synthetic model test: "Periods" date table & no FACT_/DIM_ prefixes
# ============================================================================

def test_synthetic_model_date_table_named_periods_no_fact_dim_prefixes():
    from semabridge.connectors.dataset_classification_keywords import (
        is_date_dataset,
        find_model_date_dataset,
    )
    from semabridge.core.sync_orchestrator import _classify_dataset, _classify_model
    from semabridge.connectors.measure_detector import MeasureDetector

    periods_ds = SMLDataset(
        unique_name="Periods",
        is_date_table=True,
        columns=[
            SMLColumn(unique_name="PeriodKey", data_type=DataType.INTEGER, is_key=True),
            SMLColumn(unique_name="PeriodDate", data_type=DataType.DATE),
        ],
    )
    transactions_ds = SMLDataset(
        unique_name="Transactions",
        columns=[
            SMLColumn(unique_name="TransactionID", data_type=DataType.INTEGER, is_key=True),
            SMLColumn(unique_name="PeriodKey", data_type=DataType.INTEGER),
            SMLColumn(unique_name="Amount", data_type=DataType.FLOAT),
        ],
    )
    rel = SMLRelationship(
        unique_name="rel_trans_periods",
        from_dataset="Transactions",
        from_columns=["PeriodKey"],
        to_dataset="Periods",
        to_columns=["PeriodKey"],
    )
    model = SMLModel(
        unique_name="SyntheticBusinessModel",
        datasets=[periods_ds, transactions_ds],
        relationships=[rel],
    )

    # 1. Date table detection
    assert is_date_dataset(periods_ds, model) is True
    assert is_date_dataset(transactions_ds, model) is False
    found_date_ds = find_model_date_dataset(model)
    assert found_date_ds is not None
    assert found_date_ds.unique_name == "Periods"
    assert _date_alias(model) == "Periods"

    from semabridge.connectors.inference_engine import SmlInferenceEngine

    # 2. Fact/dimension classification in inference_engine
    tables = {"Transactions": {}, "Periods": {}}
    columns = {
        "Transactions": [
            {"name": "TransactionID", "data_type": "INTEGER"},
            {"name": "PeriodKey", "data_type": "INTEGER"},
            {"name": "Amount", "data_type": "FLOAT"},
        ],
        "Periods": [
            {"name": "PeriodKey", "data_type": "INTEGER"},
            {"name": "PeriodDate", "data_type": "DATE"},
        ],
    }
    inf_engine = SmlInferenceEngine(
        tables=tables,
        columns=columns,
        relationships=[
            {"from_table": "Transactions", "to_table": "Periods"},
        ],
        primary_keys={"Transactions": ["TransactionID"], "Periods": ["PeriodKey"]},
    )
    scores = inf_engine.classify()
    assert scores["Transactions"].classification == "FACT"
    assert scores["Periods"].classification in ("DIMENSION", "TIME")

    # 3. Fact/dimension classification in sync_orchestrator
    assert _classify_dataset(transactions_ds, model) == "fact"
    assert _classify_dataset(periods_ds, model) == "dimension"
    assert _classify_model(model) == "fact"

    # 4. Fact table detection in measure_detector
    detector = MeasureDetector(
        tables=tables,
        columns=columns,
        relationships=[
            {"from_table": "Transactions", "to_table": "Periods"},
        ],
    )
    detected_facts = detector.detect_fact_tables()
    assert "Transactions" in detected_facts
    assert "Periods" not in detected_facts

