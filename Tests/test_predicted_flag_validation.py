import pytest
from semabridge.connectors.snowflake_emitter_parts.metric_helpers import validate_metric_column_references

class MockTranslator:
    def __init__(self, anchor_flag_map):
        self.anchor_flag_map = anchor_flag_map

class MockEmitter:
    def __init__(self, translator):
        self.translator = translator

    def _sanitize_col_name(self, col_name: str) -> str:
        return col_name.strip('"')

def test_validate_metric_column_references_rescues_predicted_flag():
    # Setup anchor flag map containing predicted flag IS_SPLY_YEAR for salesfact
    translator = MockTranslator({
        'salesfact': {'IS_SPLY_YEAR': 'IS_SPLY_YEAR', 'IS_YTD': 'IS_YTD'}
    })
    emitter = MockEmitter(translator)
    
    # Raw physical columns for SalesFact (does NOT include IS_SPLY_YEAR)
    dataset_col_lookup = {
        'SalesFact': {'COL_DATE', 'PRODUCTID', 'REVENUE', 'UNITS', 'ZIP'}
    }
    dataset_aliases = {'SalesFact': 'SALESFACT'}
    metric_names = set()
    
    # 1. Metric referencing predicted flag column IS_SPLY_YEAR
    metric_sql_with_flag = 'SUM(CASE WHEN SALESFACT.IS_SPLY_YEAR THEN SALESFACT.UNITS ELSE 0 END)'
    is_valid, err = validate_metric_column_references(
        emitter,
        metric_sql_with_flag,
        'TOTAL_VANARSDEL_UNITS_YTD_SPLY',
        dataset_col_lookup,
        dataset_aliases,
        metric_names,
    )
    assert is_valid is True, f"Expected predicted flag IS_SPLY_YEAR to be rescued, but got error: {err}"
    assert err is None

def test_validate_metric_column_references_fails_closed_on_truly_nonexistent_column():
    translator = MockTranslator({
        'salesfact': {'IS_SPLY_YEAR': 'IS_SPLY_YEAR', 'IS_YTD': 'IS_YTD'}
    })
    emitter = MockEmitter(translator)
    
    dataset_col_lookup = {
        'SalesFact': {'COL_DATE', 'PRODUCTID', 'REVENUE', 'UNITS', 'ZIP'}
    }
    dataset_aliases = {'SalesFact': 'SALESFACT'}
    metric_names = set()
    
    # 2. Metric referencing genuinely nonexistent column
    metric_sql_invalid = 'SUM(CASE WHEN SALESFACT.NONEXISTENT_XYZ THEN SALESFACT.UNITS ELSE 0 END)'
    is_valid, err = validate_metric_column_references(
        emitter,
        metric_sql_invalid,
        'BROKEN_METRIC',
        dataset_col_lookup,
        dataset_aliases,
        metric_names,
    )
    assert is_valid is False, "Expected genuinely nonexistent column to fail validation"
    assert "NONEXISTENT_XYZ" in err or "not found" in err
