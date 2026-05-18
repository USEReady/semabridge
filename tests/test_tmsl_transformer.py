"""
Unit tests for Fabric semantic-model JSON to SML transformation.

Tests the TMDLTransformer class in transform/tmsl_to_sml.py including:
- Table parsing
- Column type mapping
- Measure parsing with DAX
- Relationship parsing
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from semabridge.converter.tmsl_to_sml import TMDLTransformer, TransformationError
from semabridge.sml.models import DataType, AggregationType, Cardinality, SourcePlatform


class TestTMDLTransformerBasic:
    """Basic transformation tests."""
    
    @pytest.fixture
    def transformer(self):
        """Create a transformer instance."""
        return TMDLTransformer()
    
    def test_transform_empty_model(self, transformer):
        """Test transformation of minimal model."""
        tmsl = {"model": {"name": "EmptyModel"}}
        
        sml = transformer.transform(tmsl, "ws-123", "ds-456")
        
        assert sml.unique_name == "ds-456"
        assert sml.label == "EmptyModel"
        assert sml.source_platform == SourcePlatform.FABRIC
        assert len(sml.datasets) == 0
    
    def test_transform_sets_metadata(self, transformer):
        """Test that metadata is correctly set."""
        tmsl = {
            "model": {
                "name": "SalesModel",
                "description": "Sales analytics"
            }
        }
        
        sml = transformer.transform(tmsl, "ws-123", "ds-456")
        
        assert sml.description == "Sales analytics"
        assert sml.source_system == "fabric"


class TestTableParsing:
    """Tests for semantic-model table parsing."""
    
    @pytest.fixture
    def transformer(self):
        return TMDLTransformer()
    
    def test_parse_simple_table(self, transformer, sample_tmdl_json):
        """Test parsing tables from semantic-model JSON."""
        sml = transformer.transform(sample_tmdl_json, "ws-1", "ds-1")
        
        # Note: TMDLTransformer injects a Calendar dimension if none exists
        # So we expect 3 datasets: Sales, Customer, and Date (auto-injected)
        assert len(sml.datasets) >= 2  # At least the original 2 tables
        
        sales_ds = sml.get_dataset("Sales")
        assert sales_ds is not None
        assert len(sales_ds.columns) == 3
    
    def test_skip_calculation_groups(self, transformer):
        """Test that calculation groups are skipped."""
        tmsl = {
            "model": {
                "name": "Test",
                "tables": [
                    {"name": "Regular Table", "columns": []},
                    {"name": "Calc Group", "calculationGroup": {"columns": []}}
                ]
            }
        }
        
        sml = transformer.transform(tmsl, "ws-1", "ds-1")
        
        # Expect 2 datasets: Regular Table + auto-injected Date dimension
        # Calc Group should be skipped
        assert sml.get_dataset("Regular Table") is not None
        assert sml.get_dataset("Calc Group") is None
    
    def test_skip_hidden_date_tables(self, transformer):
        """Test that hidden DateTableTemplate tables are skipped."""
        tmsl = {
            "model": {
                "name": "Test",
                "tables": [
                    {"name": "Sales", "columns": []},
                    {"name": "DateTableTemplate_abc", "isHidden": True, "columns": []}
                ]
            }
        }
        
        sml = transformer.transform(tmsl, "ws-1", "ds-1")
        
        # DateTableTemplate should be skipped, Sales kept, Date auto-injected
        assert sml.get_dataset("Sales") is not None
        assert sml.get_dataset("DateTableTemplate_abc") is None


class TestColumnParsing:
    """Tests for column type mapping."""
    
    @pytest.fixture
    def transformer(self):
        return TMDLTransformer()
    
    def test_column_type_mapping(self, transformer):
        """Test semantic-model JSON to SML data type mapping."""
        tmsl = {
            "model": {
                "name": "Test",
                "tables": [{
                    "name": "TestTable",
                    "columns": [
                        {"name": "StringCol", "dataType": "string"},
                        {"name": "IntCol", "dataType": "int64"},
                        {"name": "FloatCol", "dataType": "double"},
                        {"name": "DecimalCol", "dataType": "decimal"},
                        {"name": "BoolCol", "dataType": "boolean"},
                        {"name": "DateCol", "dataType": "dateTime"},
                    ]
                }]
            }
        }
        
        sml = transformer.transform(tmsl, "ws-1", "ds-1")
        ds = sml.get_dataset("TestTable")
        
        assert ds.get_column("StringCol").data_type == DataType.STRING
        assert ds.get_column("IntCol").data_type == DataType.INTEGER
        assert ds.get_column("FloatCol").data_type == DataType.FLOAT
        assert ds.get_column("DecimalCol").data_type == DataType.DECIMAL
        assert ds.get_column("BoolCol").data_type == DataType.BOOLEAN
        assert ds.get_column("DateCol").data_type == DataType.DATETIME
    
    def test_column_metadata(self, transformer):
        """Test column metadata preservation."""
        tmsl = {
            "model": {
                "name": "Test",
                "tables": [{
                    "name": "TestTable",
                    "columns": [{
                        "name": "Revenue",
                        "dataType": "double",
                        "description": "Total revenue amount",
                        "isHidden": True,
                        "formatString": "$#,##0.00",
                        "displayFolder": "Financials"
                    }]
                }]
            }
        }
        
        sml = transformer.transform(tmsl, "ws-1", "ds-1")
        col = sml.get_dataset("TestTable").get_column("Revenue")
        
        assert col.description == "Total revenue amount"
        assert col.is_hidden is True
        assert col.format_string == "$#,##0.00"
        assert col.folder == "Financials"

    def test_column_type_mapping_extended_fabric_types(self, transformer):
        """Ensure Fabric variants are mapped without falling back to string."""
        tmsl = {
            "model": {
                "name": "Test",
                "tables": [{
                    "name": "TypedTable",
                    "columns": [
                        {"name": "OrderDate", "dataType": "Date"},
                        {"name": "EventTime", "dataType": "Time"},
                        {"name": "IsActive", "dataType": "Bool"},
                        {"name": "Amount", "dataType": "Currency"},
                        {"name": "Payload", "dataType": "Variant"},
                    ]
                }]
            }
        }

        sml = transformer.transform(tmsl, "ws-1", "ds-1")
        ds = sml.get_dataset("TypedTable")

        assert ds.get_column("OrderDate").data_type == DataType.DATE
        assert ds.get_column("EventTime").data_type == DataType.TIME
        assert ds.get_column("IsActive").data_type == DataType.BOOLEAN
        assert ds.get_column("Amount").data_type == DataType.DECIMAL
        assert ds.get_column("Payload").data_type == DataType.VARIANT


class TestMeasureParsing:
    """Tests for measure parsing and DAX translation."""
    
    @pytest.fixture
    def transformer(self):
        return TMDLTransformer()
    
    def test_parse_measures(self, transformer, sample_tmdl_json):
        """Test parsing measures from semantic-model JSON."""
        sml = transformer.transform(sample_tmdl_json, "ws-1", "ds-1")
        
        # There should be at least 1 metric from the model
        # (may have additional auto-detected metrics)
        assert len(sml.metrics) >= 1
        
        metric = sml.get_metric("Total Revenue")
        assert metric is not None
        assert metric.dataset == "Sales"
        assert "SUM" in metric.expression
    
    def test_measure_dax_translation(self, transformer):
        """Test DAX to SQL translation for simple measures."""
        tmsl = {
            "model": {
                "name": "Test",
                "tables": [{
                    "name": "Sales",
                    "columns": [{"name": "Amount", "dataType": "double"}],
                    "measures": [{
                        "name": "Total Amount",
                        "expression": "SUM([Amount])"
                    }]
                }]
            }
        }
        
        sml = transformer.transform(tmsl, "ws-1", "ds-1")
        metric = sml.get_metric("Total Amount")
        
        # Tier 1 DAX should have SQL translation
        assert metric.sql_expression is not None
        assert "SUM" in metric.sql_expression
    
    def test_measure_multiline_expression(self, transformer):
        """Test handling of multiline DAX expressions (array format)."""
        tmsl = {
            "model": {
                "name": "Test",
                "tables": [{
                    "name": "Sales",
                    "columns": [],
                    "measures": [{
                        "name": "Complex",
                        "expression": ["VAR x = 1", "RETURN x"]
                    }]
                }]
            }
        }
        
        sml = transformer.transform(tmsl, "ws-1", "ds-1")
        metric = sml.get_metric("Complex")
        
        assert "VAR x = 1" in metric.expression
        assert "RETURN x" in metric.expression

    def test_blank_measure_is_retained(self, transformer):
        """Test that a blank/error-state measure is preserved with failure metadata."""
        tmsl = {
            "model": {
                "name": "Test",
                "tables": [{
                    "name": "Sales",
                    "columns": [],
                    "measures": [{
                        "name": "Broken Measure",
                        "expression": "",
                    }],
                }],
            }
        }

        sml = transformer.transform(tmsl, "ws-1", "ds-1")
        metric = sml.get_metric("Broken Measure")

        assert metric is not None
        assert metric.sync_enabled is False
        assert metric.sync_failure_reason == "Empty DAX expression"


class TestRelationshipParsing:
    """Tests for relationship parsing."""
    
    @pytest.fixture
    def transformer(self):
        return TMDLTransformer()
    
    def test_parse_relationships(self, transformer, sample_tmdl_json):
        """Test parsing relationships from semantic-model JSON."""
        sml = transformer.transform(sample_tmdl_json, "ws-1", "ds-1")
        
        assert len(sml.relationships) == 1
        
        rel = sml.relationships[0]
        assert rel.from_dataset == "Sales"
        assert rel.from_column == "CustomerID"
        assert rel.to_dataset == "Customer"
        assert rel.to_column == "ID"
    
    def test_relationship_cardinality_mapping(self, transformer):
        """Test cardinality mapping."""
        tmsl = {
            "model": {
                "name": "Test",
                "tables": [
                    {"name": "A", "columns": []},
                    {"name": "B", "columns": []}
                ],
                "relationships": [
                    {
                        "name": "R1",
                        "fromTable": "A", "fromColumn": "ID",
                        "toTable": "B", "toColumn": "ID",
                        "cardinality": "ManyToOne"
                    }
                ]
            }
        }
        
        sml = transformer.transform(tmsl, "ws-1", "ds-1")
        
        assert sml.relationships[0].cardinality == Cardinality.MANY_TO_ONE
    
    def test_relationship_active_flag(self, transformer):
        """Test relationship active flag parsing."""
        tmsl = {
            "model": {
                "name": "Test",
                "tables": [
                    {"name": "A", "columns": []},
                    {"name": "B", "columns": []}
                ],
                "relationships": [
                    {
                        "fromTable": "A", "fromColumn": "ID",
                        "toTable": "B", "toColumn": "ID",
                        "isActive": False
                    }
                ]
            }
        }
        
        sml = transformer.transform(tmsl, "ws-1", "ds-1")
        
        assert sml.relationships[0].is_active is False


class TestTransformationErrors:
    """Tests for error handling."""
    
    @pytest.fixture
    def transformer(self):
        return TMDLTransformer()
    
    def test_malformed_relationship_skipped(self, transformer):
        """Test that malformed relationships are skipped without crashing."""
        tmsl = {
            "model": {
                "name": "Test",
                "tables": [],
                "relationships": [
                    {"name": "Bad Rel"}  # Missing required fields
                ]
            }
        }
        
        sml = transformer.transform(tmsl, "ws-1", "ds-1")
        
        # Should complete without error, relationship skipped
        assert len(sml.relationships) == 0


# =============================================================================
# Fix: invalid identifier 'SALESFACT.SCORE' (Snowflake error 000904)
# =============================================================================

_TMDL_SALESFACT = {
    "model": {
        "name": "SalesModel",
        "tables": [
            {
                "name": "SalesFact",
                "columns": [
                    {"name": "ID", "dataType": "int64"},
                    {"name": "Score", "dataType": "double"},
                ],
                "measures": [
                    {"name": "Score Total", "expression": "SUM('SalesFact'[Score])"},
                ],
            }
        ],
    }
}


class TestTMDLTransformerAutomatedTranslation:
    """Verify automated DAX translation paths for SalesFact measures.

    Manual SQL overrides are no longer supported in the transform pipeline.
    """

    @pytest.fixture
    def transformer(self):
        return TMDLTransformer()

    def test_automated_translation_uses_safe_alias(self, transformer) -> None:
        """Automated translation emits lowercase alias + quoted identifiers."""
        sml = transformer.transform(_TMDL_SALESFACT, "ws-1", "ds-1")
        metric = next(
            (m for m in sml.metrics if m.unique_name == "Score Total"), None
        )
        assert metric is not None
        assert metric.sql_expression is not None
        assert 'salesfact."SCORE"' in metric.sql_expression
        assert "SALESFACT.SCORE" not in metric.sql_expression

    def test_transform_does_not_accept_metric_overrides(self, transformer) -> None:
        """Manual override injection is intentionally unsupported."""
        with pytest.raises(TypeError):
            transformer.transform(
                _TMDL_SALESFACT,
                "ws-1",
                "ds-1",
                metric_overrides={"Score Total": "SUM(SALESFACT.SCORE)"},
            )

    def test_behavior_no_longer_has_override_alias_map(self) -> None:
        """Connector behavior no longer exposes override alias mapping."""
        from semabridge.core.behavior import ConnectorBehavior

        behavior = ConnectorBehavior()
        assert not hasattr(behavior.semantic_model, "override_alias_map")

    def test_no_overrides_uses_dax_translation(self, transformer) -> None:
        """Automated translator still produces valid sql_expression."""
        sml = transformer.transform(_TMDL_SALESFACT, "ws-1", "ds-1")
        metric = next(
            (m for m in sml.metrics if m.unique_name == "Score Total"), None
        )
        assert metric is not None
        assert metric.sql_expression is not None
        assert 'salesfact."SCORE"' in metric.sql_expression
        assert "SALESFACT.SCORE" not in metric.sql_expression



