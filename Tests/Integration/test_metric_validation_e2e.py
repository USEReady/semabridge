#!/usr/bin/env python3
"""
End-to-end test for metric column validation during semantic view generation.

This test demonstrates that:
1. Invalid column references are skipped with warnings
2. Valid metrics are included in the DDL
3. The resulting DDL is syntactically correct
4. Error messages help debug column mapping issues
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.core.settings import SnowflakeConfig
from semabridge.core.behavior import ConnectorBehavior
from semabridge.formats.sml.models import (
    SMLModel, SMLDataset, SMLDimension, SMLAttribute,
    SMLMetric, SMLColumn, SMLRelationship, AggregationType
)
from unittest.mock import MagicMock, patch


def test_metric_invalid_column_reference_skipped():
    """
    Test that metrics with invalid column references are skipped
    and don't cause the deployment to fail.
    
    This simulates the real-world issue where:
    - Metric SQL: SUM(SALESFACT.UNITS)
    - But UNITS column doesn't exist in SALESFACT
    - The metric should be skipped with a warning
    """
    
    # Setup
    config = SnowflakeConfig(
        account="test.local",
        user="test_user",
        password="test_password",  # noqa: S106
        warehouse="test_wh",
        database="test_db",
        schema_name="test_schema",
        role="test_role"
    )
    behavior = ConnectorBehavior()
    emitter = SnowflakeEmitter(config, behavior)
    
    # Create SML model with two datasets
    salesfact = SMLDataset(
        unique_name="salesfact",
        label="Sales Fact",
        source_table="SALES_FACT",
        is_fact=True,
        columns=[
            SMLColumn(unique_name="revenue", label="Revenue", data_type="float", is_measure_candidate=True),
            SMLColumn(unique_name="date_id", label="Date ID", data_type="integer", is_measure_candidate=False, is_key=True),
        ]
    )
    
    date_dim = SMLDataset(
        unique_name="date",
        label="Date Dimension",
        source_table="DATE_DIM",
        is_fact=False,
        columns=[
            SMLColumn(unique_name="date_id", label="Date ID", data_type="integer", is_key=True),
            SMLColumn(unique_name="year", label="Year", data_type="integer", is_measure_candidate=False),
        ]
    )
    
    # Create metrics - one valid, one invalid
    valid_metric = SMLMetric(
        unique_name="total_revenue",
        label="Total Revenue",
        dataset="salesfact",
        source_column="revenue",
        aggregation=AggregationType.SUM,
        sql_expression="SUM(sf.\"REVENUE\")",
    )
    
    # This metric references a non-existent column - should be skipped
    invalid_metric = SMLMetric(
        unique_name="total_units",
        label="Total Units",
        dataset="salesfact",
        source_column=None,
        aggregation=AggregationType.NONE,
        sql_expression="SUM(sf.\"UNITS\")",  # UNITS doesn't exist
    )
    
    sml = SMLModel(
        unique_name="sales_model",
        label="Sales Model",
        datasets=[salesfact, date_dim],
        dimensions=[],
        metrics=[valid_metric, invalid_metric],
        relationships=[]
    )
    
    # Generate DDL
    ddl = emitter._generate_semantic_view(sml)
    
    # Verify results
    assert ddl is not None
    assert "total_revenue" in ddl.lower()
    assert "total_units" not in ddl.lower()  # Invalid metric should be skipped
    assert "SUM" in ddl  # Valid metric should be there
    
    print("✓ Test passed: Invalid metric was skipped, valid metric included")
    print(f"\nGenerated DDL:\n{ddl}")


def test_metric_valid_column_reference_included():
    """
    Test that metrics with valid column references are included in the DDL.
    """
    
    # Setup
    config = SnowflakeConfig(
        account="test.local",
        user="test_user",
        password="test_password",  # noqa: S106
        warehouse="test_wh",
        database="test_db",
        schema_name="test_schema",
        role="test_role"
    )
    behavior = ConnectorBehavior()
    emitter = SnowflakeEmitter(config, behavior)
    
    # Create SML model
    salesfact = SMLDataset(
        unique_name="salesfact",
        label="Sales Fact",
        source_table="SALES_FACT",
        is_fact=True,
        columns=[
            SMLColumn(unique_name="revenue", label="Revenue", data_type="float", is_measure_candidate=True),
            SMLColumn(unique_name="quantity", label="Quantity", data_type="integer", is_measure_candidate=True),
            SMLColumn(unique_name="date_id", label="Date ID", data_type="integer", is_measure_candidate=False, is_key=True),
        ]
    )
    
    # Create metrics with valid column references
    metric1 = SMLMetric(
        unique_name="total_revenue",
        label="Total Revenue",
        dataset="salesfact",
        source_column="revenue",
        aggregation=AggregationType.SUM,
        sql_expression="SUM(sf.\"REVENUE\")",
    )
    
    metric2 = SMLMetric(
        unique_name="avg_quantity",
        label="Average Quantity",
        dataset="salesfact",
        source_column="quantity",
        aggregation=AggregationType.AVG,
        sql_expression="AVG(sf.\"QUANTITY\")",
    )
    
    sml = SMLModel(
        unique_name="sales_model",
        label="Sales Model",
        datasets=[salesfact],
        dimensions=[],
        metrics=[metric1, metric2],
        relationships=[]
    )
    
    # Generate DDL
    ddl = emitter._generate_semantic_view(sml)
    
    # Verify results
    assert "total_revenue" in ddl.lower()
    assert "avg_quantity" in ddl.lower()
    
    print("✓ Test passed: All valid metrics included in DDL")


def test_metric_cross_table_reference_validation():
    """
    Test cross-table metric references are properly validated.
    """
    
    # Setup
    config = SnowflakeConfig(
        account="test.local",
        user="test_user",
        password="test_password",  # noqa: S106
        warehouse="test_wh",
        database="test_db",
        schema_name="test_schema",
        role="test_role"
    )
    behavior = ConnectorBehavior()
    emitter = SnowflakeEmitter(config, behavior)
    
    # Create single dataset with multiple metrics
    salesfact = SMLDataset(
        unique_name="salesfact",
        label="Sales Fact",
        source_table="SALES_FACT",
        is_fact=True,
        columns=[
            SMLColumn(unique_name="revenue", label="Revenue", data_type="float", is_measure_candidate=True),
            SMLColumn(unique_name="quantity", label="Quantity", data_type="integer", is_measure_candidate=True),
            SMLColumn(unique_name="date_id", label="Date ID", data_type="integer", is_measure_candidate=False, is_key=True),
        ]
    )
    
    # Create metrics with various expression patterns
    metric1 = SMLMetric(
        unique_name="total_revenue",
        label="Total Revenue",
        dataset="salesfact",
        source_column="revenue",
        aggregation=AggregationType.SUM,
    )
    
    metric2 = SMLMetric(
        unique_name="revenue_quantity_ratio",
        label="Revenue per Quantity",
        dataset="salesfact",
        sql_expression="SUM(salesfact.\"REVENUE\") / NULLIF(SUM(salesfact.\"QUANTITY\"), 0)",
    )
    
    sml = SMLModel(
        unique_name="sales_model",
        label="Sales Model",
        datasets=[salesfact],
        dimensions=[],
        metrics=[metric1, metric2],
        relationships=[]
    )
    
    # Generate DDL
    ddl = emitter._generate_semantic_view(sml)
    
    # Verify that both metrics are included
    assert "total_revenue" in ddl.lower()
    assert "revenue_quantity_ratio" in ddl.lower()
    
    print("[OK] Test passed: Cross-table metrics properly validated and included")


def test_metric_wrong_column_in_target_table_healed():
    """
    Test that metrics referencing a column that's in a different table are healed.
    
    Example: SUM(d."REVENUE") where REVENUE is in salesfact, not date table.
    """
    
    # Setup
    config = SnowflakeConfig(
        account="test.local",
        user="test_user",
        password="test_password",  # noqa: S106
        warehouse="test_wh",
        database="test_db",
        schema_name="test_schema",
        role="test_role"
    )
    behavior = ConnectorBehavior()
    emitter = SnowflakeEmitter(config, behavior)
    
    # Create datasets - revenue is in salesfact, not in date
    salesfact = SMLDataset(
        unique_name="salesfact",
        label="Sales Fact",
        source_table="SALES_FACT",
        is_fact=True,
        columns=[
            SMLColumn(unique_name="revenue", label="Revenue", data_type="float", is_measure_candidate=True),
            SMLColumn(unique_name="date_id", label="Date ID", data_type="integer", is_measure_candidate=False, is_key=True),
        ]
    )
    
    date_dim = SMLDataset(
        unique_name="date",
        label="Date Dimension",
        source_table="DATE_DIM",
        is_fact=False,
        columns=[
            SMLColumn(unique_name="date_id", label="Date ID", data_type="integer", is_key=True),
            SMLColumn(unique_name="month", label="Month", data_type="integer", is_measure_candidate=False),
        ]
    )
    
    # Create relationship
    rel = SMLRelationship(
        unique_name="sales_date",
        from_dataset="salesfact",
        to_dataset="date",
        from_columns=["date_id"],
        to_columns=["date_id"],
        is_active=True
    )
    
    # Create invalid cross-table metric
    # Tries to sum REVENUE from the date table (where it doesn't exist)
    invalid_metric = SMLMetric(
        unique_name="date_revenue",
        label="Date Revenue",
        dataset="date",
        sql_expression="SUM(d.\"REVENUE\")",  # REVENUE not in date table!
    )
    
    sml = SMLModel(
        unique_name="sales_model",
        label="Sales Model",
        datasets=[salesfact, date_dim],
        dimensions=[],
        metrics=[invalid_metric],
        relationships=[rel]
    )
    
    # Generate DDL
    ddl = emitter._generate_semantic_view(sml)
    
    # Verify that the metric is healed and included under salesfact
    assert "date_revenue" in ddl.lower()
    assert 'salesfact."date_revenue"' in ddl.lower()
    
    print("✓ Test passed: Metric with column mismatch was skipped")


if __name__ == "__main__":
    print("\n" + "="*70)
    print("RUNNING END-TO-END METRIC VALIDATION TESTS")
    print("="*70 + "\n")
    
    try:
        print("Test 1: Invalid column reference skipped")
        print("-" * 70)
        test_metric_invalid_column_reference_skipped()
        print()
        
        print("Test 2: Valid column references included")
        print("-" * 70)
        test_metric_valid_column_reference_included()
        print()
        
        print("Test 3: Cross-table references validated")
        print("-" * 70)
        test_metric_cross_table_reference_validation()
        print()
        
        print("Test 4: Wrong column in target table skipped")
        print("-" * 70)
        test_metric_wrong_column_in_target_table_skipped()
        print()
        
        print("="*70)
        print("ALL TESTS PASSED ✓")
        print("="*70)
        
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
