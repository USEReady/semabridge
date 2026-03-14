#!/usr/bin/env python
"""Batch test measure pipeline across multiple datasets."""

import json
from datetime import datetime
from pathlib import Path
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# Datasets to test
TEST_DATASETS = [
    "Probability",
    "Competitive Marketing Analysis",
    "Device",
    "Regional Sales Sample",
    "FabricModel",
    "Employee",
    "Core_Finance_v1",
]

def run_test_for_dataset(dataset_name: str) -> dict:
    """Run the test script for a single dataset and parse results."""
    import subprocess
    
    logger.info(f"\n{'='*80}")
    logger.info(f"Testing: {dataset_name}")
    logger.info(f"{'='*80}")
    
    try:
        # Run the test script
        result = subprocess.run(
            ["python", "tests/test_measure_pipeline_detailed.py", "--dataset", dataset_name],
            capture_output=False,
            timeout=300
        )
        
        if result.returncode != 0:
            logger.error(f"❌ Test failed for {dataset_name}")
            return {
                "dataset": dataset_name,
                "status": "FAILED",
                "error": "Non-zero exit code"
            }
        
        # Find the latest output file for this dataset
        output_dir = Path("output")
        json_files = sorted(
            output_dir.glob(f"measure_analysis_{dataset_name.replace(' ', '_')}*.json"),
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )
        
        if not json_files:
            logger.error(f"❌ No output file found for {dataset_name}")
            return {
                "dataset": dataset_name,
                "status": "NO_OUTPUT",
            }
        
        # Load the JSON result
        with open(json_files[0], 'r', encoding='utf-8') as f:
            result_data = json.load(f)
        
        summary = result_data.get("summary", {})
        
        return {
            "dataset": dataset_name,
            "status": "SUCCESS",
            "total_measures": summary.get("total_measures", 0),
            "complete": summary.get("complete_conversion", 0),
            "partial": summary.get("partial_conversion", 0),
            "success_rate": summary.get("success_rate", "N/A"),
            "output_file": json_files[0].name
        }
        
    except subprocess.TimeoutExpired:
        logger.error(f"❌ Test timed out for {dataset_name}")
        return {
            "dataset": dataset_name,
            "status": "TIMEOUT"
        }
    except Exception as e:
        logger.error(f"❌ Test error for {dataset_name}: {e}")
        return {
            "dataset": dataset_name,
            "status": "ERROR",
            "error": str(e)
        }

def main():
    logger.info(f"\n\n{'='*80}")
    logger.info(f"BATCH MEASURE PIPELINE TEST")
    logger.info(f"Testing {len(TEST_DATASETS)} datasets")
    logger.info(f"Start time: {datetime.now().isoformat()}")
    logger.info(f"{'='*80}\n")
    
    results = []
    
    for dataset_name in TEST_DATASETS:
        result = run_test_for_dataset(dataset_name)
        results.append(result)
    
    # Generate summary report
    logger.info(f"\n\n{'='*80}")
    logger.info(f"TEST SUMMARY REPORT")
    logger.info(f"{'='*80}\n")
    
    print("\n")
    print(f"{'Dataset':<40} {'Status':<10} {'Measures':<10} {'Success':<10}")
    print("-" * 70)
    
    success_count = 0
    total_measures = 0
    
    for result in results:
        dataset = result["dataset"]
        status = result["status"]
        
        if status == "SUCCESS":
            measures = result.get("total_measures", 0)
            success_rate = result.get("success_rate", "N/A")
            print(f"{dataset:<40} ✅ {status:<8} {measures:<10} {success_rate:<10}")
            success_count += 1
            total_measures += measures
        else:
            error_msg = result.get("error", status)
            print(f"{dataset:<40} ❌ {status:<8} N/A        N/A")
    
    print("-" * 70)
    print(f"{'TOTALS':<40} {success_count}/{len(TEST_DATASETS)} passed    {total_measures} measures\n")
    
    # Save detailed results to JSON
    report_file = Path("output") / f"batch_test_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_file, 'w', encoding='utf-8') as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "total_datasets": len(TEST_DATASETS),
            "successful": success_count,
            "failed": len(TEST_DATASETS) - success_count,
            "total_measures": total_measures,
            "results": results
        }, f, indent=2)
    
    logger.info(f"\n✅ Detailed results saved to: {report_file}")
    logger.info(f"\nEnd time: {datetime.now().isoformat()}\n")

if __name__ == "__main__":
    main()
