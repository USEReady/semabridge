#!/usr/bin/env python
"""Debug script to trace metric emission issues."""

import logging
import json
import sys
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='[%(asctime)s] %(levelname)-8s %(message)s'
)
logger = logging.getLogger(__name__)

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from semabridge.connectors.fabric_extractor import FabricExtractor
from semabridge.converter.tmsl_to_sml import TMSLTransformer
from semabridge.core.settings import get_settings

def main():
    workspace_id = "d875c0c3-59e9-4d55-a7f0-99595b756718"
    dataset_name = "Probability"
    
    # Get settings
    settings = get_settings()
    
    # Step 1: Extract Fabric model
    logger.info("=" * 80)
    logger.info("STEP 1: Extract Fabric model")
    logger.info("=" * 80)
    
    fabric_extractor = FabricExtractor(settings.fabric)
    models = fabric_extractor.list_semantic_models()
    
    dataset_id = None
    for model in models:
        if model.get("displayName") == dataset_name:
            dataset_id = model.get("id")
            break
    
    if not dataset_id:
        logger.error(f"Could not find dataset {dataset_name}")
        return
    
    logger.info(f"Dataset ID: {dataset_id}")
    
    # Get the TMSL
    tmsl_dict = fabric_extractor.get_model_definition(dataset_id)
    
    # Step 2: Transform to SML
    logger.info("\n" + "=" * 80)
    logger.info("STEP 2: Transform to SML")
    logger.info("=" * 80)
    
    transformer = TMSLTransformer()
    sml_model = transformer.transform(
        tmsl_dict,
        workspace_id=workspace_id,
        dataset_id=dataset_id,
    )
    
    # Analyze metrics
    logger.info(f"\nSML Metrics: {len(sml_model.metrics)}")
    
    for i, metric in enumerate(sml_model.metrics):
        logger.info(f"\n[Metric {i+1}]")
        logger.info(f"  unique_name: {metric.unique_name}")
        logger.info(f"  label: {metric.label}")
        logger.info(f"  dataset: '{metric.dataset}'")
        logger.info(f"  source_column: {metric.source_column}")
        logger.info(f"  aggregation: {metric.aggregation}")
        logger.info(f"  expression: {metric.expression[:50] if metric.expression else None}...")
        logger.info(f"  sql_expression: {metric.sql_expression}")
        logger.info(f"  sync_enabled: {metric.sync_enabled}")
        logger.info(f"  is_hidden: {metric.is_hidden}")
        logger.info(f"  complexity_tier: {metric.complexity_tier}")
    
    # Analyze datasets
    logger.info(f"\n\nSML Datasets: {len(sml_model.datasets)}")
    for i, ds in enumerate(sml_model.datasets):
        logger.info(f"\n[Dataset {i+1}]")
        logger.info(f"  unique_name: '{ds.unique_name}'")
        logger.info(f"  source_table: '{ds.source_table}'")
        logger.info(f"  columns: {len(ds.columns)}")
        for col in ds.columns[:3]:  # Show first 3 columns
            logger.info(f"    - {col.unique_name} ({col.data_type})")
    
    # Step 3: Try to emit to Snowflake (dry run)
    logger.info("\n" + "=" * 80)
    logger.info("STEP 3: Check dataset/metric compatibility")
    logger.info("=" * 80)
    
    # Check if metrics' datasets match dataset.unique_names
    dataset_names = {ds.unique_name for ds in sml_model.datasets}
    
    logger.info(f"\nDataset unique_names: {dataset_names}")
    logger.info(f"\nMetric datasets referenced:")
    
    dataset_metric_map = {}
    for metric in sml_model.metrics:
        if metric.dataset not in dataset_metric_map:
            dataset_metric_map[metric.dataset] = []
        dataset_metric_map[metric.dataset].append(metric.unique_name)
        
        if metric.dataset not in dataset_names:
            logger.error(f"  ❌ MISMATCH: Metric '{metric.unique_name}' references dataset '{metric.dataset}' which doesn't exist!")
        else:
            logger.info(f"  ✓ Metric '{metric.unique_name}' → dataset '{metric.dataset}' (EXISTS)")
    
    # Check source_column existence
    logger.info(f"\nMetric source_column validation:")
    for metric in sml_model.metrics:
        ds = next((d for d in sml_model.datasets if d.unique_name == metric.dataset), None)
        if not ds:
            logger.warning(f"  ⚠️  Dataset '{metric.dataset}' not found in SML")
            continue
        
        if metric.source_column:
            col = next((c for c in ds.columns if c.unique_name == metric.source_column), None)
            if col:
                logger.info(f"  ✓ '{metric.unique_name}' column '{metric.source_column}' exists in '{metric.dataset}'")
            else:
                logger.error(f"  ❌ '{metric.unique_name}' column '{metric.source_column}' NOT found in '{metric.dataset}'")
                logger.info(f"     Available columns: {[c.unique_name for c in ds.columns[:5]]}")
    
    logger.info("\n✅ Demo complete!")

if __name__ == "__main__":
    main()
