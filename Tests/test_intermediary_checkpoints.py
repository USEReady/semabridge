import pytest
from semabridge.intermediate.models import OSIDataType, OSICardinality
from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
from semabridge.converter.osi_to_sml import OSIToSMLConverter

@pytest.fixture
def tmsl_payload():
    return {
        "dataset_id": "TestModel",
        "display_name": "TestModel",
        "synonym_overrides": {},
        "tmsl": {
            "model": {
                "name": "TestModel",
                "tables": [
                    {
                        "name": "Sales",
                        "columns": [
                            {"name": "SalesID", "dataType": "int64"},
                            {"name": "Amount", "dataType": "double", "summarizeBy": "sum"},
                            {"name": "SaleDate", "dataType": "dateTime"}
                        ]
                    },
                    {
                        "name": "Customer",
                        "columns": [
                            {"name": "CustomerID", "dataType": "int64"},
                            {"name": "Name", "dataType": "string"}
                        ]
                    }
                ],
                "relationships": [
                    {
                        "name": "rel1",
                        "fromTable": "Sales",
                        "fromColumn": "CustomerID",
                        "toTable": "Customer",
                        "toColumn": "CustomerID"
                    }
                ]
            }
        }
    }

@pytest.fixture
def osi_model(tmsl_payload):
    converter = TMSLToOSIConverter()
    # Mocking _create_metrics_from_aggregation_columns and others might be needed if they rely on external state,
    # but based on the code, they should work with this payload.
    # Wait, the dataset 'Sales' needs a measure for "Total Sales"
    tmsl_payload["tmsl"]["model"]["tables"][0]["measures"] = [
        {"name": "Total Sales", "expression": "SUM([Amount])"}
    ]
    return converter.to_osi(tmsl_payload)

@pytest.fixture
def sml_model(osi_model):
    converter = OSIToSMLConverter()
    return converter.from_osi(osi_model)


class TestCheckpoint1_TMSL_to_OSI:

    def test_datasets_count(self, osi_model):
        assert len(osi_model.datasets) == 2

    def test_dataset_names(self, osi_model):
        names = [ds.unique_name for ds in osi_model.datasets]
        assert 'Sales' in names
        assert 'Customer' in names

    def test_column_type_mapping(self, osi_model):
        sales = next(ds for ds in osi_model.datasets if ds.unique_name == 'Sales')
        amount_col = next(c for c in sales.columns if c.unique_name == 'Amount')
        assert amount_col.data_type == OSIDataType.FLOAT

    def test_measure_candidate_flagged(self, osi_model):
        sales = next(ds for ds in osi_model.datasets if ds.unique_name == 'Sales')
        amount_col = next(c for c in sales.columns if c.unique_name == 'Amount')
        assert amount_col.is_measure_candidate is True

    def test_explicit_metric_has_expression(self, osi_model):
        metric = next(m for m in osi_model.metrics if m.unique_name == 'Total Sales')
        assert metric.expression is not None
        assert metric.expression.strip() != ''

    def test_auto_metric_expression_not_empty(self, osi_model):
        metric = next(m for m in osi_model.metrics if m.unique_name == 'Amount')
        assert metric.expression is not None and metric.expression.strip() != ''

    def test_relationship_endpoints(self, osi_model):
        rel = osi_model.relationships[0]
        assert rel.from_dataset == 'Sales'
        assert rel.to_dataset   == 'Customer'

    def test_hidden_tables_excluded(self, osi_model):
        names = [ds.unique_name for ds in osi_model.datasets]
        assert not any(n.startswith('LocalDateTable_') for n in names)


class TestCheckpoint2_OSI_to_SML:

    def test_explicit_metric_has_sql(self, sml_model):
        metric = next(m for m in sml_model.metrics if m.unique_name == 'Total Sales')
        assert metric.sql_expression is not None
        assert 'SUM' in metric.sql_expression.upper()

    def test_explicit_metric_sync_enabled(self, sml_model):
        metric = next(m for m in sml_model.metrics if m.unique_name == 'Total Sales')
        assert metric.sync_enabled is True

    def test_auto_metric_sync_enabled(self, sml_model):
        metric = next(m for m in sml_model.metrics if m.unique_name == 'Amount')
        assert metric.sync_enabled is True

    def test_calendar_dataset_injected(self, sml_model):
        names = [ds.unique_name for ds in sml_model.datasets]
        assert 'Date' in names

    def test_datasets_include_original_two(self, sml_model):
        names = [ds.unique_name for ds in sml_model.datasets]
        assert 'Sales' in names and 'Customer' in names

    def test_relationship_cardinality_preserved(self, sml_model):
        rel = next(r for r in sml_model.relationships if r.from_dataset == 'Sales')
        from semabridge.sml.models import Cardinality as SMLCardinality
        assert rel.cardinality == SMLCardinality.MANY_TO_ONE
