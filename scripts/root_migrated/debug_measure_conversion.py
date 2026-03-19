#!/usr/bin/env python
"""Debug script to trace measure conversion in detail."""

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
from semabridge.core.validate_semantic_model import SemanticModelValidator

def main():
    workspace_id = "d875c0c3-59e9-4d55-a7f0-99595b756718"
    dataset_name = "Probability"
    
    # Step 1: Extract Fabric model
    logger.info("=" * 80)
    logger.info("STEP 1: Extract Fabric model")
    logger.info("=" * 80)
    
    fabric_extractor = FabricExtractor(workspace_id)
    models = fabric_extractor.list_semantic_models()
    logger.info(f"Found {len(models)} semantic models")
    
    # Find the dataset
    dataset_id = None
    for model in models:
        if model.get("displayName") == dataset_name:
            dataset_id = model.get("id")
            break
    
    if not dataset_id:
        logger.error(f"Could not find dataset {dataset_name}")
        return
    
    logger.info(f"Found dataset: {dataset_id}")
    
    # Get the TMSL
    tmsl_dict = fabric_extractor.get_model_definition(dataset_id)
    
    # Count measures in TMSL
    fabric_measures = 0
    if "model" in tmsl_dict and "tables" in tmsl_dict["model"]:
        for table in tmsl_dict["model"]["tables"]:
            if "measures" in table:
                fabric_measures += len(table["measures"])
                for measure in table["measures"]:
                    logger.info(f"  TMSL Measure: {table['name']}.{measure['name']}")
    
    logger.info(f"Total measures in TMSL: {fabric_measures}")
    
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
    
    # Count metrics in SML
    logger.info("\nSML Model Analysis:")
    logger.info(f"  Total datasets: {len(sml_model.datasets)}")
    logger.info(f"  Total dimensions: {len(sml_model.dimensions)}")
    logger.info(f"  Total metrics: {len(sml_model.metrics)}")
    
    # Check metrics at model level
    logger.info(f"\nMetrics at SML model level: {len(sml_model.metrics)}")
    for metric in sml_model.metrics:
        logger.info(f"  [Model-level] {metric.dataset}.{metric.unique_name} ({metric.aggregation})")
    
    # Check metrics per dataset
    logger.info("\nMetrics per dataset:")
    for dataset in sml_model.datasets:
        metrics_list = getattr(dataset, 'metrics', [])
        logger.info(f"  Dataset: {dataset.unique_name}")
        logger.info(f"    - Has 'metrics' attribute: {hasattr(dataset, 'metrics')}")
        logger.info(f"    - Metrics count: {len(metrics_list)}")
        for metric in metrics_list:
            logger.info(f"      * {metric.unique_name} ({metric.aggregation})")
    
    # Step 3: Validate
    logger.info("\n" + "=" * 80)
    logger.info("STEP 3: Validation")
    logger.info("=" * 80)
    
    validator = SemanticModelValidator()
    report = validator.validate(sml_model)
    
    logger.info(f"Validation passed: {report.passed}")
    logger.info(f"Validation issues: {len(report.issues)}")
    for issue in report.issues:
        logger.warning(f"  * {issue.tier}: {issue.message}")
    
    logger.info("\nDone!")

if __name__ == "__main__":
    main()
