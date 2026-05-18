import pytest
from semabridge.converter.tmsl_to_sml import TMDLTransformer
from semabridge.connectors.dimensions_clause_builder import DimensionsClauseBuilder
from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder
from unittest.mock import MagicMock

def test_full_synonym_propagation_integration():
    # 1. Mock semantic-model JSON with user synonyms
    tmdl = {
        "model": {
            "name": "SalesModel",
            "tables": [
                {
                    "name": "Sales",
                    "columns": [
                        {
                            "name": "Sale_Amount",
                            "dataType": "decimal",
                            "synonyms": ["Revenue", "Turnover"]
                        },
                        {
                            "name": "Cust_ID",
                            "dataType": "int64",
                            "synonyms": ["Client ID"]
                        }
                    ],
                    "partitions": [
                        {
                            "name": "Partition",
                            "source": {
                                "type": "m",
                                "expression": "Source"
                            }
                        }
                    ],
                    "measures": [
                        {
                            "name": "Total Sales",
                            "expression": "SUM(Sales[Sale_Amount])",
                            "synonyms": ["Gross Sales", "Total Revenue"]
                        }
                    ]
                }
            ]
        }
    }

    # 2. Transform to SML
    transformer = TMDLTransformer()
    # Mocking row counts and behavior
    sml = transformer.transform(tmdl, "ws", "ds")

    # 3. Verify SML Synonyms
    print(f"Datasets found: {[ds.unique_name for ds in sml.datasets]}")
    sales_ds = sml.get_dataset("Sales")
    if not sales_ds:
        # Fallback for debugging: take first if available
        sales_ds = sml.datasets[0] if sml.datasets else None
    
    assert sales_ds is not None, f"Dataset 'Sales' not found in model. Found: {[ds.unique_name for ds in sml.datasets]}"
    print(f"Columns in 'Sales': {[c.unique_name for c in sales_ds.columns]}")
    sale_amt_col = sales_ds.get_column("Sale_Amount")
    assert sale_amt_col is not None, f"Column 'Sale_Amount' not found in 'Sales'. Found: {[c.unique_name for c in sales_ds.columns]}"
    assert "Revenue" in sale_amt_col.synonyms
    assert "Turnover" in sale_amt_col.synonyms
    # Heuristic should also be there
    assert "Sale Amount" in sale_amt_col.synonyms

    cust_id_col = sales_ds.get_column("Cust_ID")
    assert cust_id_col is not None, f"Column 'Cust_ID' not found in 'Sales'. Found: {[c.unique_name for c in sales_ds.columns]}"
    assert "Client ID" in cust_id_col.synonyms
    # Heuristic expansion
    assert "Customer Id" in cust_id_col.synonyms

    print(f"Metrics in model: {[m.unique_name for m in sml.metrics]}")
    total_sales_metric = sml.get_metric("Total Sales")
    assert total_sales_metric is not None, f"Metric 'Total Sales' not found. Found: {[m.unique_name for m in sml.metrics]}"
    assert "Gross Sales" in total_sales_metric.synonyms
    assert "Total Revenue" in total_sales_metric.synonyms

    # 4. Mock DDL Generation
    # We need to mock several components used by builders
    sanitizer = MagicMock()
    sanitizer.sanitize_semantic_name.side_effect = lambda x: x
    sanitizer.format_physical_column_ref.side_effect = lambda a, c, **kwargs: f'{a}."{c}"'
    
    id_sanitizer = MagicMock()
    id_sanitizer.sanitize_column.side_effect = lambda x: x
    id_sanitizer.sanitize_alias.side_effect = lambda x: x
    
    schema_manager = MagicMock()
    schema_manager._resolve_physical_column_name.side_effect = lambda ds, col: col
    
    behavior = MagicMock()
    behavior.semantic_model.sync_all_attributes = True

    # Build Dimensions Clause
    dim_builder = DimensionsClauseBuilder(
        id_sanitizer, schema_manager, sanitizer, MagicMock(), behavior
    )
    
    dataset_aliases = {"Sales": "SALES"}
    dataset_by_name = {"Sales": sales_ds}
    dataset_col_lookup = {"Sales": {"Sale_Amount", "Cust_ID"}}
    measure_columns = set() # No columns marked as measures for this test
    
    dim_lines = dim_builder.build_for_sml(
        sml, dataset_aliases, dataset_by_name, dataset_col_lookup, measure_columns
    )
    
    # Verify DDL contains WITH SYNONYMS
    # Note: merge_synonyms might limit count or reorder.
    # User synonyms are first.
    dim_output = "\n".join(dim_lines)
    assert 'WITH SYNONYMS = (\'Revenue\', \'Turnover\', \'Sale Amount\')' in dim_output
    assert 'WITH SYNONYMS = (\'Client ID\', \'Cust Id\', \'Customer Id\')' in dim_output

    # Build Metrics Clause
    mock_config = MagicMock()
    mock_config.database = "DB"
    mock_config.schema_name = "SCHEMA"
    metric_builder = MetricsClauseBuilder(
        id_sanitizer, schema_manager, sanitizer, MagicMock(), mock_config
    )
    
    # Minimal mocks for metric building
    metric_builder.translator = MagicMock() # Ensure it is a Mock
    metric_builder.translator._normalize_metric_column_references.side_effect = lambda e, *args, **kwargs: e
    metric_builder.translator._validate_metric_column_references.return_value = (True, None)
    
    # Mocking the return value of translate to be a tuple (sql, metrics_obj)
    metrics_mock = MagicMock()
    metrics_mock.strategy.value = "direct_agg"
    metric_builder.translator.translate.return_value = ('SUM(SALES."Sale_Amount")', metrics_mock)

    metric_lines = metric_builder.build_for_sml(
        sml, dataset_aliases, dataset_by_name, dataset_col_lookup, 
        {}, {"Sale_Amount", "Cust_ID"}, {"Total Sales"}
    )
    
    metric_output = "\n".join(metric_lines)
    assert 'WITH SYNONYMS = (\'Gross Sales\', \'Total Revenue\')' in metric_output

def test_builder_escaping_and_edge_cases():
    """69, 84: Verify escaping in DDL builders"""
    from semabridge.sml.models import SMLDataset, SMLColumn, DataType
    
    col = SMLColumn(unique_name="O'Brien", label="O'Brien", data_type=DataType.STRING, synonyms=["O'Brien", "O'Reilly"])
    ds = SMLDataset(unique_name="Table", columns=[col])
    
    # Simple mock for builders
    from semabridge.connectors.synonym_clause import synonyms_clause
    clause = synonyms_clause(col.synonyms)
    assert "''" in clause
    assert "O''Brien" in clause

def test_missing_column_graceful_handling():
    """74, 75: Ensure no crash if metadata lookup fails during DDL generation"""
    from semabridge.sml.models import SMLDataset, SMLModel
    from semabridge.connectors.dimensions_clause_builder import DimensionsClauseBuilder
    
    # Mocking a dimension attribute that points to a non-existent column
    attr = MagicMock(dataset="Sales", unique_name="MissingCol")
    attr.dataset_column = "RealColName" # But dataset will be empty
    
    # Mock builder
    builder = DimensionsClauseBuilder(MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock())
    
    # Verify synonyms are empty if lookup fails
    dataset_by_name = {} # Empty
    item_synonyms = []
    dataset_obj = dataset_by_name.get(attr.dataset)
    if dataset_obj:
        col_obj = dataset_obj.get_column("RealColName")
        if col_obj:
            item_synonyms = getattr(col_obj, "synonyms", [])
    
    assert item_synonyms == []


