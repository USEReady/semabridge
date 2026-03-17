#!/usr/bin/env python3
"""
Example: Batch Translation Integration for DAX Metrics

This shows how to integrate the new batch translation feature into the
main DAX translation pipeline to reduce API calls by ~90%.
"""

from typing import List, Dict, Optional, Any
from semabridge.converter.gemini_dax_translator import get_gemini_translator, GeminiDAXTranslator
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


def batch_translate_metrics(
    metrics: List[Dict[str, Any]],
    table_alias: str,
    batch_size: int = 20
) -> Dict[str, Optional[str]]:
    """
    Batch translate multiple DAX metrics to SQL (90% fewer API calls).
    
    Args:
        metrics: List of metric objects with 'unique_name' and 'expression' keys
        table_alias: SQL table alias (e.g., 'salesfact')
        batch_size: Metrics per API call (default: 20)
        
    Returns:
        Dict mapping metric_name -> sql_expression (or None if failed)
        
    Example:
        metrics = sml_model.metrics  # List of SMLMetric objects
        results = batch_translate_metrics(metrics, "salesfact")
        
        for metric_name, sql in results.items():
            if sql:
                print(f"✓ {metric_name}: {sql}")
            else:
                print(f"✗ {metric_name}: Translation failed")
    """
    
    # Get batch translator
    translator: GeminiDAXTranslator = get_gemini_translator()
    
    if not translator.api_key:
        logger.error("Gemini API key not configured - batch translation unavailable")
        return {}
    
    # Filter metrics that actually need translation (have DAX expression, no SQL yet)
    metrics_to_translate = [
        m for m in metrics
        if hasattr(m, 'expression') and m.expression and 
           (not hasattr(m, 'sql_expression') or not m.sql_expression)
    ]
    
    if not metrics_to_translate:
        logger.info("No metrics need translation (all already have SQL)")
        return {}
    
    logger.info(f"Preparing batch translation for {len(metrics_to_translate)} metrics...")
    
    # Convert to batch format: (name, dax, alias, dataset, schema_context)
    batch = [
        (
            m.unique_name,
            m.expression,
            table_alias,
            m.dataset if hasattr(m, 'dataset') else "Unknown",
            None  # schema_context
        )
        for m in metrics_to_translate
    ]
    
    # Perform batch translation (should make only 2-3 API calls instead of N)
    logger.info(f"🚀 Batch translating {len(batch)} metrics...")
    batch_result = translator.translate_batch(batch, batch_size=batch_size)
    
    # Convert results to metric_name -> sql_expression dict
    results = {}
    for metric_name, translation_result in batch_result.results.items():
        if translation_result.is_valid and translation_result.sql:
            results[metric_name] = translation_result.sql
            logger.debug(f"  ✓ {metric_name}: {translation_result.sql[:70]}...")
        else:
            results[metric_name] = None
            if translation_result.error:
                logger.debug(f"  ✗ {metric_name}: {translation_result.error}")
    
    # Log summary statistics
    logger.info(
        f"Batch Translation Summary:\n"
        f"  • Metrics processed: {batch_result.batch_size}\n"
        f"  • Cached results: {batch_result.cached_count}\n"
        f"  • API calls made: {batch_result.api_calls}\n"
        f"  • Successful translations: {batch_result.successful_count}\n"
        f"  • Failed translations: {batch_result.failed_count}\n"
        f"  • API efficiency: {int((1 - batch_result.api_calls / max(1, batch_result.batch_size)) * 100)}% reduction"
    )
    
    return results


def apply_batch_translations_to_model(
    sml_model: Any,
    table_alias: str,
    batch_size: int = 20
) -> int:
    """
    Apply batch translations directly to an SML model's metrics.
    
    Args:
        sml_model: SMLModel object with .metrics list
        table_alias: SQL table alias
        batch_size: Metrics per API call
        
    Returns:
        Number of successfully translated metrics
        
    Example:
        from semabridge.formats.sml.models import SMLModel
        
        model = load_sml_model("sales.yaml")
        success_count = apply_batch_translations_to_model(model, "salesfact")
        print(f"Translated {success_count} metrics")
    """
    
    if not hasattr(sml_model, 'metrics'):
        logger.error("Model has no metrics attribute")
        return 0
    
    # Get batch translations
    translations = batch_translate_metrics(
        sml_model.metrics,
        table_alias,
        batch_size
    )
    
    # Apply translations to model
    success_count = 0
    for metric in sml_model.metrics:
        if metric.unique_name in translations:
            sql = translations[metric.unique_name]
            if sql:
                metric.sql_expression = sql
                success_count += 1
                logger.debug(f"Applied SQL to {metric.unique_name}")
            else:
                logger.debug(f"Translation failed for {metric.unique_name}")
    
    logger.info(f"Applied {success_count} batch translations to model")
    return success_count


def compare_single_vs_batch():
    """
    Demonstration comparing single vs batch translation performance.
    """
    
    logger.info("\n" + "="*70)
    logger.info("BATCH TRANSLATION PERFORMANCE COMPARISON")
    logger.info("="*70)
    
    # Sample metrics (these are tuples for batch format)
    sample_metrics = [
        ("total_revenue", "SUM([Revenue])", "sales", "Sales"),
        ("avg_price", "AVERAGE([Price])", "sales", "Sales"),
        ("unit_count", "COUNT([UnitID])", "sales", "Sales"),
        ("distinct_customers", "DISTINCTCOUNT([CustomerID])", "sales", "Sales"),
        ("min_quantity", "MIN([Quantity])", "sales", "Sales"),
        ("max_quantity", "MAX([Quantity])", "sales", "Sales"),
        ("median_cost", "MEDIAN([Cost])", "sales", "Sales"),
        ("std_dev", "STDEV([Amount])", "sales", "Sales"),
    ]
    
    # Simulate single translation (47 calls for 47 metrics)
    logger.info("\n📊 Single Translation (Current):")
    logger.info(f"   Metrics: {len(sample_metrics)}")
    logger.info(f"   API calls: {len(sample_metrics)} (1 per metric)")
    logger.info(f"   Total time: ~{len(sample_metrics) * 2}s (est. 2s per API call)")
    logger.info(f"   Quota usage: {len(sample_metrics)} / 1500 (free tier)")
    
    # Simulate batch translation (3 batches of 20)
    batch_size = 20
    num_batches = (len(sample_metrics) + batch_size - 1) // batch_size
    
    logger.info(f"\n📦 Batch Translation (Proposed):")
    logger.info(f"   Metrics: {len(sample_metrics)}")
    logger.info(f"   Batch size: {batch_size}")
    logger.info(f"   API calls: {num_batches} (batches of {batch_size})")
    logger.info(f"   Total time: ~{num_batches * 2}s (est. 2s per batch)")
    logger.info(f"   Quota usage: {num_batches} / 1500 (free tier)")
    
    # Calculate improvement
    reduction = (1 - num_batches / len(sample_metrics)) * 100
    time_saved = (len(sample_metrics) - num_batches) * 2
    
    logger.info(f"\n✅ IMPROVEMENT:")
    logger.info(f"   API calls reduced: {len(sample_metrics)} → {num_batches} ({reduction:.0f}% fewer)")
    logger.info(f"   Time saved: ~{time_saved}s")
    logger.info(f"   Quota saved: {len(sample_metrics) - num_batches} requests")
    
    logger.info("="*70 + "\n")


if __name__ == "__main__":
    # Show comparison
    compare_single_vs_batch()
    
    # Example: Batch translate sample metrics
    logger.info("Example: Batch translating 8 sample metrics...")
    
    sample_batch = [
        ("total_revenue", "SUM([Revenue])", "fact", "Sales", None),
        ("avg_price", "AVERAGE([Price])", "fact", "Sales", None),
        ("count_orders", "COUNT([OrderID])", "fact", "Sales", None),
    ]
    
    translator = get_gemini_translator()
    result = translator.translate_batch(sample_batch)
    
    logger.info(f"\nResults:")
    logger.info(f"  API calls: {result.api_calls}")
    logger.info(f"  Successful: {result.successful_count}")
    logger.info(f"  Failed: {result.failed_count}")
    logger.info(f"  Cached: {result.cached_count}")
    
    for name, translation in result.results.items():
        status = "✓" if translation.is_valid else "✗"
        logger.info(f"  {status} {name}: {translation.sql or translation.error}")
