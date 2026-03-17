#!/usr/bin/env python3
"""
Practical Integration Examples: Multi-table Semantic DAX Translation

Real-world usage patterns for supporting cross-table metrics in the
deterministic DAX translation system.
"""

# =============================================================================
# EXAMPLE 1: Using Semantic Translator in OSI to SML Conversion
# =============================================================================

"""
File: src/semabridge/converter/osi_to_sml.py

Add semantic analysis to metric conversion:
"""

from semabridge.converter.deterministic_translator import DeterministicTranslator

def convert_osi_metric_with_semantics(osi_metric, dataset):
    """Enhanced metric conversion using semantic layer."""
    
    translator = DeterministicTranslator()
    dax_expression = osi_metric.expression
    
    # Get semantic information about this metric
    analysis = translator.analyze_dax_semantics(dax_expression)
    
    # Translate DAX to SQL
    result = translator.translate(
        dax_expression,
        table_alias="fact",
        dataset_name=dataset,
        metric_name=osi_metric.name
    )
    
    # Enhanced metric now includes:
    sml_metric = {
        "name": osi_metric.name,
        "expression": dax_expression,
        "sql": result.sql,
        "tables": result.tables_referenced,  # NEW!
        "joins": result.joins,  # NEW!
        "deterministic": result.is_success,
    }
    
    return sml_metric


# =============================================================================
# EXAMPLE 2: Building Multi-table Queries
# =============================================================================

def build_complete_query(metric_sql, joins, base_table="SalesFact"):
    """Construct complete SQL query with joins."""
    
    # Start with base query
    query = f"SELECT {metric_sql}\nFROM {base_table} AS fact"
    
    # Add all required joins
    for join_clause in (joins or []):
        query += f"\n{join_clause}"
    
    # Add WHERE clause (if needed)
    # query += "\nWHERE ..."
    
    return query


# Example:
# translator = DeterministicTranslator()
# result = translator.translate(dax, "fact", "dataset", "metric_name")
# 
# query = build_complete_query(result.sql, result.joins)
# # SELECT SUM(fact."UNITS")
# # FROM SalesFact AS fact
# # LEFT JOIN Product AS pro ON fact.PRODUCTID = pro.PRODUCTID


# =============================================================================
# EXAMPLE 3: Handling Metric Filters with Product Dimension
# =============================================================================

def translate_product_filtered_metric(metric_name, base_expression,
                                      product_filter_column,
                                      product_filter_value):
    """
    Translate metric with product dimension filter.
    
    Example:
      metric_name = "Units_VanArsdel"
      base_expression = "SUM([UNITS])"
      product_filter_column = "ISVANARSDEL"
      product_filter_value = "Yes"
    """
    
    translator = DeterministicTranslator()
    
    # Translate base expression
    result = translator.translate(base_expression, "fact", "SalesFact", 
                                  metric_name)
    
    # Check if Product table is needed
    if not result.tables_referenced or "Product" not in result.tables_referenced:
        # Add Product to analysis if filter references it
        result.tables_referenced.append("Product")
        
        # Plan join
        analysis = translator.analyze_dax_semantics(
            f"SUM([UNITS]) * Product[{product_filter_column}]"
        )
        result.joins = analysis['required_joins']
    
    return {
        'metric_name': metric_name,
        'sql_expression': result.sql,
        'filter_clause': f"Product.{product_filter_column} = '{product_filter_value}'",
        'joins': result.joins,
        'complete_query': f"""
            SELECT {result.sql}
            FROM SalesFact AS fact
            {chr(10).join(result.joins)}
            WHERE Product.{product_filter_column} = '{product_filter_value}'
        """
    }


# Example usage:
# query = translate_product_filtered_metric(
#     "Units_VanArsdel",
#     "SUM([UNITS])",
#     "ISVANARSDEL",
#     "Yes"
# )
# print(query['complete_query'])


# =============================================================================
# EXAMPLE 4: Date Dimension Intelligence
# =============================================================================

def translate_date_based_metric(metric_name, base_expression,
                                date_filter_year=None,
                                date_filter_month=None):
    """
    Translate metric with date dimension.
    
    Example:
      metric_name = "YTD_Units"
      base_expression = "SUM([UNITS])"
      date_filter_year = 2025
    """
    
    translator = DeterministicTranslator()
    
    # Analyze for date references
    analysis = translator.analyze_dax_semantics(
        f"{base_expression} by Date[DATE]"
    )
    
    # Translate
    result = translator.translate(base_expression, "fact", "SalesFact",
                                  metric_name)
    
    # Add Date table analysis if needed
    if "Date" not in result.tables_referenced:
        result.tables_referenced.append("Date")
        # Plan join to Date
        date_join = "LEFT JOIN Date AS dat ON fact.DATE = dat.DATE"
        result.joins = [date_join]
    
    # Build WHERE clause
    where_conditions = []
    if date_filter_year:
        where_conditions.append(f"dat.YEAR = {date_filter_year}")
    if date_filter_month:
        where_conditions.append(f"dat.MONTH = {date_filter_month}")
    
    where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
    
    return {
        'metric_name': metric_name,
        'sql_expression': result.sql,
        'joins': result.joins,
        'where_clause': where_clause,
        'complete_query': f"""
            SELECT {result.sql}
            FROM SalesFact AS fact
            {chr(10).join(result.joins)}
            WHERE {where_clause}
        """
    }


# Example usage:
# query = translate_date_based_metric(
#     "YTD_Units_2025",
#     "SUM([UNITS])",
#     date_filter_year=2025
# )


# =============================================================================
# EXAMPLE 5: Sentiment Analysis (Complex Multi-table)
# =============================================================================

def translate_sentiment_metric(metric_name, aggregation_function="AVERAGE"):
    """
    Translate sentiment metrics that require sentiment table.
    
    Handles: AVERAGE(Sentiment[Score]), etc.
    """
    
    translator = DeterministicTranslator()
    
    # Analyze what's needed
    dax_expr = f"{aggregation_function}(Sentiment[SCORE])"
    analysis = translator.analyze_dax_semantics(dax_expr)
    
    # Map aggregation to SQL
    sql_agg_map = {
        "AVERAGE": "AVG",
        "SUM": "SUM",
        "MAX": "MAX", 
        "MIN": "MIN",
        "COUNT": "COUNT",
    }
    
    sql_agg = sql_agg_map.get(aggregation_function, "AVG")
    
    # Build result
    return {
        'metric_name': metric_name,
        'sql_expression': f"{sql_agg}(sen.SCORE)",
        'tables': analysis['tables_referenced'],
        'joins': analysis['required_joins'],
        'complete_query': f"""
            SELECT {sql_agg}(sen.SCORE)
            FROM SalesFact AS fact
            LEFT JOIN Sentiment AS sen ON fact.PRODUCTID = sen.PRODUCTID
            GROUP BY fact.PRODUCTID
        """
    }


# =============================================================================
# EXAMPLE 6: Caching Semantic Analysis Results
# =============================================================================

class SemanticCacheManager:
    """Cache semantic analysis results to avoid recomputation."""
    
    def __init__(self):
        self.cache = {}
    
    def get_analysis(self, dax_expression):
        """Get cached semantic analysis or compute."""
        
        # Use DAX as cache key
        cache_key = hash(dax_expression)
        
        if cache_key in self.cache:
            return self.cache[cache_key]
        
        # Compute if not cached
        translator = DeterministicTranslator()
        analysis = translator.analyze_dax_semantics(dax_expression)
        
        # Cache result
        self.cache[cache_key] = analysis
        
        return analysis
    
    def clear(self):
        """Clear cache."""
        self.cache.clear()


# Usage:
# cache = SemanticCacheManager()
# analysis = cache.get_analysis(dax_expr)  # Computed
# analysis = cache.get_analysis(dax_expr)  # From cache


# =============================================================================
# EXAMPLE 7: Validation & Error Reporting
# =============================================================================

def validate_and_translate(metric_name, dax_expression, dataset):
    """Validate metric and provide detailed error reporting."""
    
    try:
        translator = DeterministicTranslator()
        
        # Perform semantic analysis first
        analysis = translator.analyze_dax_semantics(dax_expression)
        
        # Check for errors
        if analysis['errors']:
            return {
                'valid': False,
                'metric_name': metric_name,
                'errors': analysis['errors'],
                'message': f"Semantic analysis failed: {analysis['errors'][0]}"
            }
        
        # Translate
        result = translator.translate(dax_expression, "fact", dataset,
                                      metric_name)
        
        if not result.is_success:
            return {
                'valid': False,
                'metric_name': metric_name,
                'error': result.error_reason,
                'message': f"Translation failed: {result.error_reason}"
            }
        
        # Success
        return {
            'valid': True,
            'metric_name': metric_name,
            'sql': result.sql,
            'tables': result.tables_referenced,
            'joins': result.joins,
            'deterministic': True,
            'message': 'Translation successful'
        }
        
    except Exception as e:
        return {
            'valid': False,
            'metric_name': metric_name,
            'error': str(e),
            'message': f'Unexpected error: {str(e)}'
        }


# Usage:
# result = validate_and_translate(
#     "Units_VanArsdel",
#     "SUM([UNITS])",
#     "SalesFact"
# )
# print(result['message'])


# =============================================================================
# EXAMPLE 8: Bulk Metric Translation with Semantic Enrichment
# =============================================================================

def translate_metrics_batch(metrics, dataset):
    """Translate multiple metrics with semantic information."""
    
    translator = DeterministicTranslator()
    results = []
    
    for metric in metrics:
        # Translate each metric
        result = translator.translate(
            metric['expression'],
            table_alias="fact",
            dataset_name=dataset,
            metric_name=metric['name']
        )
        
        # Add semantic information
        enriched = {
            'name': metric['name'],
            'expression': metric['expression'],
            'status': 'success' if result.is_success else 'failed',
            'sql': result.sql,
            'tables': result.tables_referenced,
            'join_count': len(result.joins),
            'joins': result.joins,
            'requires_joins': len(result.joins) > 0,
            'error': result.error_reason if not result.is_success else None,
        }
        
        results.append(enriched)
    
    return results


# Example:
# metrics = [
#     {'name': 'Total_Revenue', 'expression': 'SUM([REVENUE])'},
#     {'name': 'Units_VanArsdel', 'expression': 'SUM([UNITS]) * Product[ISVANARSDEL]'},
#     {'name': 'Avg_Sentiment', 'expression': 'AVERAGE(Sentiment[SCORE])'},
# ]
#
# results = translate_metrics_batch(metrics, "SalesFact")
# for r in results:
#     print(f"{r['name']}: {r['status']} ({r['join_count']} joins)")


# =============================================================================
# EXAMPLE 9: Dynamic Filter Building
# =============================================================================

def create_dynamic_filter_query(metric_sql, joins,
                               filter_table=None,
                               filter_column=None,
                               filter_value=None):
    """Build query with dynamic WHERE clause."""
    
    # Base query
    query = f"SELECT {metric_sql}\nFROM SalesFact AS fact"
    
    # Add joins
    for join in (joins or []):
        query += f"\n{join}"
    
    # Add filter if provided
    if filter_table and filter_column and filter_value:
        # Determine alias from join
        alias_map = {'Product': 'pro', 'Date': 'dat', 'Sentiment': 'sen'}
        alias = alias_map.get(filter_table, 't')
        
        query += f"\nWHERE {alias}.{filter_column} = '{filter_value}'"
    
    return query


# =============================================================================
# EXAMPLE 10: Monitoring & Logging
# =============================================================================

def translate_with_monitoring(dax_expression, metric_name, dataset):
    """Translate metric and log semantic details."""
    
    import time
    import logging
    
    logger = logging.getLogger("SemanticTranslation")
    start_time = time.time()
    
    translator = DeterministicTranslator()
    
    # Analyze semantic structure
    analysis = translator.analyze_dax_semantics(dax_expression)
    
    # Translate
    result = translator.translate(dax_expression, "fact", dataset,
                                  metric_name)
    
    elapsed = time.time() - start_time
    
    # Log detailed information
    logger.info(f"[{metric_name}] Translation Summary:")
    logger.info(f"  DAX: {dax_expression[:80]}")
    logger.info(f"  Status: {'SUCCESS' if result.is_success else 'FAILED'}")
    logger.info(f"  Tables: {result.tables_referenced}")
    logger.info(f"  Joins: {len(result.joins)}")
    logger.info(f"  SQL: {result.sql[:80] if result.sql else 'N/A'}")
    logger.info(f"  Time: {elapsed:.2f}s")
    
    if result.joins:
        for join in result.joins:
            logger.debug(f"    Join: {join[:80]}")
    
    return result


# =============================================================================
# CONCLUSION
# =============================================================================

"""
These examples demonstrate how to integrate the semantic layer into:

1. OSI to SML conversion pipeline
2. Query building and execution
3. Metric filtering with dimensions
4. Date-based analysis
5. Sentiment/complex measures
6. Performance optimization via caching
7. Error handling and validation
8. Batch processing
9. Dynamic filter construction
10. Monitoring and logging

The semantic layer is now production-ready for supporting multi-table
DAX metrics in deterministic translation pipeline.
"""
