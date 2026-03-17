#!/usr/bin/env python3
"""
Query Assembly Layer - Integration Examples

Shows how to integrate Query Assembly Layer with:
  1. DeterministicTranslator (metric translation)
  2. Semantic Layer (multi-table support)
  3. Snowflake execution
  4. Dashboard generation
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from semabridge.converter.deterministic_translator import DeterministicTranslator
from semabridge.converter.query_assembly import (
    TranslatedMetric,
    DimensionField,
    QueryAssembler,
)


# ============================================================================
# EXAMPLE 1: Basic Metric Translation to Query
# ============================================================================

def example_1_basic_translation():
    """Assemble single metric into executable SQL query."""
    print("\n" + "="*80)
    print("EXAMPLE 1: Single Metric Assembly")
    print("="*80)
    
    # Create metric (simulating translation result)
    # In real usage, this would come from DeterministicTranslator
    metric = TranslatedMetric(
        name="Total Revenue",
        dax_expression="SUM([REVENUE])",
        sql_expression="SUM(fact.REVENUE)",
        joins=[],
        tables_referenced=["SalesFact"]
    )
    
    print(f"\nMetric: {metric.name}")
    print(f"  DAX: {metric.dax_expression}")
    print(f"  SQL: {metric.sql_expression}")
    print(f"  Tables: {metric.tables_referenced}")
    print(f"  Joins: {len(metric.joins)}")
    
    # Step: Assemble into query
    assembler = QueryAssembler()
    query = assembler.assemble_metrics_query([metric])
    
    print(f"\nAssembled SQL:\n{query.full_sql}")


# ============================================================================
# EXAMPLE 2: Multi-Metric Dashboard Query
# ============================================================================

def example_2_dashboard_query():
    """Build complete dashboard query with multiple metrics."""
    print("\n" + "="*80)
    print("EXAMPLE 2: Dashboard with Multiple Metrics")
    print("="*80)
    
    # Create simulated translated metrics
    # In production, these would come from DeterministicTranslator
    metrics = [
        TranslatedMetric(
            name="Total Revenue",
            dax_expression="SUM([REVENUE])",
            sql_expression="SUM(fact.REVENUE)",
            joins=[],
            tables_referenced=["SalesFact"]
        ),
        TranslatedMetric(
            name="Total Units Sold",
            dax_expression="SUM([UNITS])",
            sql_expression="SUM(fact.UNITS)",
            joins=[],
            tables_referenced=["SalesFact"]
        ),
        TranslatedMetric(
            name="Average Price",
            dax_expression="SUM([REVENUE]) / SUM([UNITS])",
            sql_expression="SUM(fact.REVENUE) / SUM(fact.UNITS)",
            joins=[],
            tables_referenced=["SalesFact"]
        ),
        TranslatedMetric(
            name="VanArsdel Revenue",
            dax_expression="SUM([REVENUE]) WHERE Product[ISVANARSDEL]=Yes",
            sql_expression="SUM(CASE WHEN pro.ISVANARSDEL='Yes' THEN fact.REVENUE ELSE 0 END)",
            joins=["LEFT JOIN Product AS pro ON fact.PRODUCTID = pro.PRODUCTID"],
            tables_referenced=["SalesFact", "Product"]
        ),
    ]
    
    print(f"\nMetrics ({len(metrics)}):")
    for m in metrics:
        print(f"  + {m.name}")
    
    # Assemble dashboard query
    print(f"\nAssembling Dashboard Query...")
    assembler = QueryAssembler()
    query = assembler.assemble_metrics_query(metrics)
    
    print(f"\nResult:")
    print(f"  Metrics: {len([m for m in metrics if m.is_success])}")
    print(f"  Joins: {len(query.joins)}")
    print(f"\nGenerated SQL:")
    print(query.full_sql)


# ============================================================================
# EXAMPLE 3: Query with Dimensions and Grouping
# ============================================================================

def example_3_dimensional_analysis():
    """Build query with dimensional analysis (GROUP BY)."""
    print("\n" + "="*80)
    print("EXAMPLE 3: Dimensional Analysis (GROUP BY)")
    print("="*80)
    
    # Create simulated translated metrics
    metrics = [
        TranslatedMetric(
            name="Total Revenue",
            dax_expression="SUM([REVENUE])",
            sql_expression="SUM(fact.REVENUE)",
            joins=["LEFT JOIN Date AS dat ON fact.DATE = dat.DATE"],
            tables_referenced=["SalesFact", "Date"]
        ),
        TranslatedMetric(
            name="Total Units",
            dax_expression="SUM([UNITS])",
            sql_expression="SUM(fact.UNITS)",
            joins=["LEFT JOIN Date AS dat ON fact.DATE = dat.DATE"],
            tables_referenced=["SalesFact", "Date"]
        ),
        TranslatedMetric(
            name="Avg Price",
            dax_expression="SUM([REVENUE]) / SUM([UNITS])",
            sql_expression="SUM(fact.REVENUE) / SUM(fact.UNITS)",
            joins=["LEFT JOIN Date AS dat ON fact.DATE = dat.DATE"],
            tables_referenced=["SalesFact", "Date"]
        ),
    ]
    
    # Define dimensions
    dimensions = [
        DimensionField(table="Date", column="YEAR", alias="Year"),
        DimensionField(table="Date", column="QUARTER", alias="Quarter"),
    ]
    
    print(f"\nMetrics: {len(metrics)}")
    for m in metrics:
        print(f"  + {m.name}")
    
    print(f"\nDimensions: {len(dimensions)}")
    for d in dimensions:
        print(f"  + {d.table}.{d.column}")
    
    # Assemble query with dimensions
    assembler = QueryAssembler()
    query = assembler.assemble_metrics_query(metrics, dimensions=dimensions)
    
    print(f"\nAssembled Query:")
    print(query.full_sql)


# ============================================================================
# EXAMPLE 4: Query Builder - Lower Level Control
# ============================================================================

def example_4_advanced_query_building():
    """Advanced: Build query with fine-grained control."""
    print("\n" + "="*80)
    print("EXAMPLE 4: Advanced Query Building with WHERE Clause")
    print("="*80)
    
    # Create metrics
    translator = DeterministicTranslator()
    
    metrics = [
        TranslatedMetric(
            name="VanArsdel Revenue",
            dax_expression="SUM([REVENUE]) WHERE Product[ISVANARSDEL]=Yes",
            sql_expression="SUM(pro.REVENUE)",
            joins=["LEFT JOIN Product AS pro ON fact.PRODUCTID = pro.PRODUCTID"],
            tables_referenced=["SalesFact", "Product"]
        ),
        TranslatedMetric(
            name="VanArsdel Units",
            dax_expression="SUM([UNITS]) WHERE Product[ISVANARSDEL]=Yes",
            sql_expression="SUM(fact.UNITS)",
            joins=["LEFT JOIN Product AS pro ON fact.PRODUCTID = pro.PRODUCTID"],
            tables_referenced=["SalesFact", "Product"]
        ),
    ]
    
    # Create dimensions
    dimensions = [
        DimensionField(table="Date", column="YEAR", alias="Year"),
    ]
    
    # Build query with WHERE clause
    assembler = QueryAssembler()
    query = assembler.assemble_metrics_query(
        metrics=metrics,
        dimensions=dimensions,
        where_clause="dat.YEAR >= 2024",
        order_by_clause="Year DESC"
    )
    
    print(f"\nAssembled Query:")
    print(query.full_sql)


# ============================================================================
# EXAMPLE 5: Dimension Filter - Product Category Analysis
# ============================================================================

def example_5_product_analysis():
    """Analyze metrics by product category."""
    print("\n" + "="*80)
    print("EXAMPLE 5: Product Category Analysis")
    print("="*80)
    
    translator = DeterministicTranslator()
    
    metrics = [
        TranslatedMetric(
            name="Units Sold",
            dax_expression="SUM([UNITS])",
            sql_expression="SUM(fact.UNITS)",
            joins=["LEFT JOIN Product AS pro ON fact.PRODUCTID = pro.PRODUCTID"],
            tables_referenced=["SalesFact", "Product"]
        ),
        TranslatedMetric(
            name="Revenue",
            dax_expression="SUM([REVENUE])",
            sql_expression="SUM(fact.REVENUE)",
            joins=["LEFT JOIN Product AS pro ON fact.PRODUCTID = pro.PRODUCTID"],
            tables_referenced=["SalesFact", "Product"]
        ),
        TranslatedMetric(
            name="Avg Price",
            dax_expression="SUM([REVENUE]) / SUM([UNITS])",
            sql_expression="SUM(fact.REVENUE) / SUM(fact.UNITS)",
            joins=["LEFT JOIN Product AS pro ON fact.PRODUCTID = pro.PRODUCTID"],
            tables_referenced=["SalesFact", "Product"]
        ),
    ]
    
    dimensions = [
        DimensionField(table="Product", column="CATEGORY", alias="Category"),
    ]
    
    assembler = QueryAssembler()
    query = assembler.assemble_metrics_query(
        metrics=metrics,
        dimensions=dimensions,
        order_by_clause="Revenue DESC"
    )
    
    print(f"\nProduct Category Analysis:")
    print(query.full_sql)


# ============================================================================
# EXAMPLE 6: Join Deduplication Demonstration
# ============================================================================

def example_6_join_deduplication():
    """Show how joins are automatically deduplicated."""
    print("\n" + "="*80)
    print("EXAMPLE 6: Automatic Join Deduplication")
    print("="*80)
    
    # Create metrics that reference same tables (duplicate joins)
    metrics = [
        TranslatedMetric(
            name="Metric 1",
            dax_expression="expr1",
            sql_expression="SUM(fact.UNITS)",
            joins=[
                "LEFT JOIN Product AS pro ON fact.PRODUCTID = pro.PRODUCTID",
                "LEFT JOIN Date AS dat ON fact.DATE = dat.DATE",
            ],
            tables_referenced=["SalesFact", "Product", "Date"]
        ),
        TranslatedMetric(
            name="Metric 2",
            dax_expression="expr2",
            sql_expression="SUM(fact.REVENUE)",
            joins=[
                "LEFT JOIN Date AS dat ON fact.DATE = dat.DATE",
                "LEFT JOIN Sentiment AS sen ON fact.PRODUCTID = sen.PRODUCTID",
            ],
            tables_referenced=["SalesFact", "Date", "Sentiment"]
        ),
    ]
    
    print(f"\nBefore assembly:")
    print(f"  Metric 1 joins: {len(metrics[0].joins)}")
    for j in metrics[0].joins:
        print(f"    + {j[:60]}...")
    print(f"  Metric 2 joins: {len(metrics[1].joins)}")
    for j in metrics[1].joins:
        print(f"    + {j[:60]}...")
    
    assembler = QueryAssembler()
    query = assembler.assemble_metrics_query(metrics)
    
    print(f"\nAfter assembly (deduplicated):")
    print(f"  Total joins: {len(query.joins)}")
    for i, j in enumerate(query.joins, 1):
        print(f"    {i}. {j[:60]}...")
    
    print(f"\nAssembled query:")
    print(query.full_sql)


# ============================================================================
# EXAMPLE 7: Pipeline Integration - DeterministicTranslator → Query Assembly
# ============================================================================

def example_7_full_pipeline():
    """Complete pipeline: DAX → Translation → Query Assembly → Execution."""
    print("\n" + "="*80)
    print("EXAMPLE 7: Complete Pipeline - DAX to Executable SQL")
    print("="*80)
    
    # Define business requirements
    requirements = {
        'dashboard_name': 'Sales Performance',
        'metrics': [
            {'name': 'Total Revenue', 'dax': 'SUM([REVENUE])'},
            {'name': 'Total Units', 'dax': 'SUM([UNITS])'},
            {'name': 'Avg Sentiment', 'dax': 'AVERAGE(Sentiment[SCORE])'},
        ],
        'dimensions': [
            {'table': 'Date', 'column': 'YEAR', 'alias': 'Year'},
            {'table': 'Product', 'column': 'CATEGORY', 'alias': 'Category'},
        ],
        'filters': 'Year >= 2024',
    }
    
    print(f"\nDashboard: {requirements['dashboard_name']}")
    print(f"Metrics: {len(requirements['metrics'])}")
    print(f"Dimensions: {len(requirements['dimensions'])}")
    
    # Step 1: Create translated metrics (simulated)
    print(f"\nStep 1: Create Translated Metrics")
    
    # Metric specifications with pre-translated SQL
    metric_sql_map = {
        'Total Revenue': ('SUM([REVENUE])', 'SUM(fact.REVENUE)', []),
        'Total Units': ('SUM([UNITS])', 'SUM(fact.UNITS)', []),
        'Avg Sentiment': ('AVERAGE(Sentiment[SCORE])', 'AVG(sen.SCORE)', 
                         ["LEFT JOIN Sentiment AS sen ON fact.PRODUCTID = sen.PRODUCTID"]),
    }
    
    metrics = []
    for spec in requirements['metrics']:
        dax_expr, sql_expr, joins = metric_sql_map.get(
            spec['name'],
            (spec['dax'], spec['dax'], [])
        )
        
        metric = TranslatedMetric(
            name=spec['name'],
            dax_expression=dax_expr,
            sql_expression=sql_expr,
            joins=joins,
            tables_referenced=["SalesFact"] + (["Sentiment"] if joins else []),
            is_success=True
        )
        metrics.append(metric)
        print(f"  [OK] {spec['name']}")
    
    # Step 2: Create dimensions
    print(f"\nStep 2: Setup Dimensions")
    dimensions = [
        DimensionField(
            table=d['table'],
            column=d['column'],
            alias=d['alias']
        )
        for d in requirements['dimensions']
    ]
    for d in dimensions:
        print(f"  + {d.table}.{d.column} AS {d.alias}")
    
    # Step 3: Assemble query
    print(f"\nStep 3: Assemble Query")
    assembler = QueryAssembler()
    query = assembler.assemble_metrics_query(
        metrics=metrics,
        dimensions=dimensions,
        where_clause="dat.YEAR >= 2024"
    )
    
    print(f"  Joins merged: {len(query.joins)}")
    print(f"  Valid: Yes")
    
    # Step 4: Display final SQL
    print(f"\nStep 4: Final Executable SQL")
    print(query.full_sql)
    
    # Step 5: Export options
    print(f"\nStep 5: Export Options")
    print(f"  SQL String: {len(query.full_sql)} characters")
    print(f"  Dict: {len(query.to_dict())} keys")
    print(f"  Components: 6 (SELECT, FROM, JOINs, GROUP BY, WHERE, ORDER BY)")


# ============================================================================
# EXAMPLE 8: Error Recovery
# ============================================================================

def example_8_error_recovery():
    """Handle translation errors gracefully."""
    print("\n" + "="*80)
    print("EXAMPLE 8: Error Recovery")
    print("="*80)
    
    # Mix of successful and failed metrics
    metrics = [
        TranslatedMetric(
            name="Valid Metric",
            dax_expression="SUM([REVENUE])",
            sql_expression="SUM(fact.REVENUE)",
            is_success=True
        ),
        TranslatedMetric(
            name="Failed Metric",
            dax_expression="INVALID_EXPRESSION",
            sql_expression="",
            is_success=False,
            error_reason="Invalid DAX syntax"
        ),
        TranslatedMetric(
            name="Another Valid",
            dax_expression="SUM([UNITS])",
            sql_expression="SUM(fact.UNITS)",
            is_success=True
        ),
    ]
    
    print(f"\nInput metrics:")
    for m in metrics:
        status = "OK" if m.is_success else "FAILED"
        print(f"  [{status}] {m.name}: {m.error_reason or 'Success'}")
    
    # Assemble with error handling
    assembler = QueryAssembler()
    try:
        query = assembler.assemble_metrics_query(metrics)
        print(f"\nAssembly result: SUCCESS")
        print(f"  Used {len([m for m in metrics if m.is_success])} successful metrics")
        print(f"  Skipped {len([m for m in metrics if not m.is_success])} failed metrics")
    except ValueError as e:
        print(f"\nAssembly result: FAILED - {e}")


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    print("\n" + "#"*80)
    print("# QUERY ASSEMBLY LAYER - INTEGRATION EXAMPLES")
    print("#"*80)
    
    try:
        example_1_basic_translation()
        example_2_dashboard_query()
        example_3_dimensional_analysis()
        example_4_advanced_query_building()
        example_5_product_analysis()
        example_6_join_deduplication()
        example_7_full_pipeline()
        example_8_error_recovery()
        
        print("\n" + "#"*80)
        print("# ALL EXAMPLES COMPLETED SUCCESSFULLY")
        print("#"*80)
        
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
