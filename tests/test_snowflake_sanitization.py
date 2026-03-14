
import logging
import sys
import unittest
from unittest.mock import MagicMock

# Adjust path to include src
sys.path.append("src")

from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.converter.dax_translator import DAXTranslator
from semabridge.formats.sml.models import SMLModel, SMLDataset, SMLMetric, SMLColumn, AggregationType, DataType

# Invoke logger to see output
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TestSnowflakeEmitterSpaces(unittest.TestCase):
    def test_sanitize_col_name(self):
        config = MagicMock()
        emitter = SnowflakeEmitter(config)
        
        # Test cases
        cases = {
            "Simple": "SIMPLE",
            "With Space": "WITH_SPACE",
            "Multi   Space": "MULTI_SPACE",
            "Special%Char": "SPECIAL_CHAR",
            "Mixed Case": "MIXED_CASE",
            "'Table'[Column]": "COLUMN",
            "Order Date": "ORDER_DATE",
            "Rev   For   Travel": "REV_FOR_TRAVEL"
        }
        
        for input_str, expected in cases.items():
            result = emitter._sanitize_col_name(input_str)
            self.assertEqual(result, expected, f"Failed for '{input_str}'")

    def test_dax_translator_quote(self):
        translator = DAXTranslator(llm_config=False)
        
        # Test cases
        cases = {
            "Simple": '"SIMPLE"',
            "With Space": '"WITH_SPACE"',
            "Multi   Space": '"MULTI_SPACE"',
            "Special%Char": '"SPECIAL_CHAR"',
            "'Table'[Column]": '"COLUMN"',
            "Rev For Exp Travel": '"REV_FOR_EXP_TRAVEL"'
        }
        
        for input_str, expected in cases.items():
            result = translator._quote(input_str)
            self.assertEqual(result, expected, f"Failed for '{input_str}'")

    def test_generate_semantic_view_spaces(self):
        # 1. Setup SML Model with spaces
        sml = SMLModel(
            unique_name="SalesModel",
            label="Sales Model",
            source_system="fabric",
        )
        
        # Dataset with space in name and column
        ds = SMLDataset(
            unique_name="Fact Sales",
            label="Fact Sales",
            source_table="FACT_SALES",
            columns=[
                SMLColumn(unique_name="ID", data_type=DataType.INTEGER, is_key=True),
                SMLColumn(unique_name="Subscription Revenue", data_type=DataType.DECIMAL),
            ],
            is_fact=True
        )
        sml.datasets.append(ds)
        
        # Metric referencing column with space
        metric = SMLMetric(
            unique_name="Total Revenue",
            label="Total Revenue",
            dataset="Fact Sales",
            source_column="Subscription Revenue",
            aggregation=AggregationType.SUM
        )
        sml.metrics.append(metric)
        
        # 2. Initialize Emitter
        config = MagicMock()
        config.database = "TEST_DB"
        config.schema_name = "TEST_SCHEMA"
        
        emitter = SnowflakeEmitter(config)
        
        # 3. Generate SQL
        sql = emitter._generate_semantic_view(sml)
        
        logger.info(f"Generated SQL:\n{sql}")
        
        # 4. Assertions
        # Check that table alias uses sanitized name (e.g. FACT_SALES)
        # Check that column reference uses sanitized name (SUBSCRIPTION_REVENUE)
        
        self.assertIn('SUM(FACT_SALES."SUBSCRIPTION_REVENUE")', sql)
        self.assertNotIn('SUBSCRIPTION REVENUE', sql) # Should not contain space
        
        print("Test Passed!")

    def test_metric_with_missing_source_column(self):
        """Metric referencing a non-existent column must be skipped, not crash."""
        sml = SMLModel(
            unique_name="TestModel",
            label="Test Model",
            source_system="fabric",
        )
        
        ds = SMLDataset(
            unique_name="SalesFact",
            label="Sales Fact",
            source_table="SALESFACT",
            columns=[
                SMLColumn(unique_name="ProductId", data_type=DataType.STRING, is_key=True),
                SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL),
            ],
            is_fact=True
        )
        sml.datasets.append(ds)
        
        # Valid metric — Revenue exists
        valid_metric = SMLMetric(
            unique_name="Total Revenue",
            label="Total Revenue",
            dataset="SalesFact",
            source_column="Revenue",
            aggregation=AggregationType.SUM
        )
        sml.metrics.append(valid_metric)
        
        # Invalid metric — Score does NOT exist in columns
        bad_metric = SMLMetric(
            unique_name="Total Score",
            label="Total Score",
            dataset="SalesFact",
            source_column="Score",
            aggregation=AggregationType.SUM
        )
        sml.metrics.append(bad_metric)
        
        config = MagicMock()
        config.database = "TEST_DB"
        config.schema_name = "TEST_SCHEMA"
        
        emitter = SnowflakeEmitter(config)
        sql = emitter._generate_semantic_view(sml)
        
        logger.info(f"Generated SQL:\n{sql}")
        
        # Valid metric SHOULD be present
        self.assertIn('REVENUE', sql)
        # Invalid metric MUST NOT be present — prevents Snowflake 000904 error
        self.assertNotIn('SCORE', sql)
        # No exception raised — graceful skip
        
        print("test_metric_with_missing_source_column Passed!")

    def test_measure_detector_sml_types(self):
        """MeasureDetector must handle SML enum values (e.g., 'integer'), not just Snowflake types."""
        from semabridge.connectors.measure_detector import MeasureDetector
        
        tables = {"TestTable": {"row_count": 100}}
        # These use SML enum values, not Snowflake types
        columns = {
            "TestTable": [
                {"name": "Id", "data_type": "string"},       # SML string → not numeric
                {"name": "Revenue", "data_type": "decimal"},  # SML decimal → numeric → measure
                {"name": "Units", "data_type": "integer"},    # SML integer → numeric → measure
            ]
        }
        relationships = []
        
        detector = MeasureDetector(tables, columns, relationships)
        measures = detector.detect_measures("TestTable", columns["TestTable"], is_fact=True)
        
        measure_cols = {m["column"] for m in measures}
        
        # Revenue and Units are numeric and should be detected as measures
        self.assertIn("Revenue", measure_cols, "Revenue should be detected as measure")
        self.assertIn("Units", measure_cols, "Units should be detected as measure")
        # Id is a string and should NOT be detected
        self.assertNotIn("Id", measure_cols, "Id should not be detected as measure")
        
        print("test_measure_detector_sml_types Passed!")

if __name__ == "__main__":
    unittest.main()
