"""
Batch Measure Pipeline Analyzer

Runs measure pipeline analysis on multiple datasets and generates
a comparison report showing which datasets have the best/worst
measure conversion success rates.

Usage:
    python tests/batch_measure_pipeline_analysis.py --datasets DATASET1 DATASET2 DATASET3
    python tests/batch_measure_pipeline_analysis.py --all  # Analyze all models in workspace
"""

import sys
from typing import List, Dict, Optional
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
import json
import argparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from semabridge.core.settings import get_settings
from semabridge.utils.logger import setup_logging, get_logger
# from tests.test_measure_pipeline_detailed import MeasurePipelineDebugger

logger = get_logger(__name__)


@dataclass
class DatasetAnalysisResult:
    """Analysis result for a single dataset."""
    dataset_name: str
    total_measures: int
    fabric_measures: int
    sml_measures: int
    snowflake_measures: int
    success_rate: float
    fabric_sml_dropoff: int
    sml_snowflake_dropoff: int
    status: str  # "✅ EXCELLENT", "⚠️ GOOD", "❌ POOR"
    

def analyze_dataset(workspace_id: str, dataset_name: str) -> Optional[DatasetAnalysisResult]:
    """Analyze a single dataset."""
    logger.info(f"Analyzing {dataset_name}...")
    
    try:
        debugger = MeasurePipelineDebugger(workspace_id, dataset_name)
        analysis = debugger.analyze_pipeline()
        
        success_rate = (analysis.snowflake_measure_count / analysis.fabric_measure_count * 100 
                       if analysis.fabric_measure_count > 0 else 0)
        
        # Determine status
        if success_rate >= 85:
            status = "✅ EXCELLENT"
        elif success_rate >= 70:
            status = "⚠️  GOOD"
        elif success_rate >= 50:
            status = "⚠️  FAIR"
        else:
            status = "❌ POOR"
        
        return DatasetAnalysisResult(
            dataset_name=dataset_name,
            total_measures=len(analysis.all_measures),
            fabric_measures=analysis.fabric_measure_count,
            sml_measures=analysis.sml_measure_count,
            snowflake_measures=analysis.snowflake_measure_count,
            success_rate=success_rate,
            fabric_sml_dropoff=analysis.fabric_measure_count - analysis.sml_measure_count,
            sml_snowflake_dropoff=analysis.sml_measure_count - analysis.snowflake_measure_count,
            status=status,
        )
    
    except Exception as e:
        logger.error(f"Failed to analyze {dataset_name}: {e}")
        return None


def generate_comparison_report(results: List[DatasetAnalysisResult]) -> str:
    """Generate comparison report for all datasets."""
    
    lines = [
        "=" * 100,
        "BATCH MEASURE PIPELINE ANALYSIS REPORT",
        "=" * 100,
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Datasets analyzed: {len(results)}",
        "",
    ]
    
    # Summary table
    lines.extend([
        "SUMMARY TABLE",
        "=" * 100,
        "",
        f"{'Dataset':<35} {'Status':<15} {'Success Rate':<15} {'Fabric':<8} {'SML':<8} {'Snowflake':<10}",
        "-" * 100,
    ])
    
    for result in sorted(results, key=lambda r: r.success_rate, reverse=True):
        lines.append(
            f"{result.dataset_name:<35} {result.status:<15} {result.success_rate:>6.1f}%        "
            f"{result.fabric_measures:>6} {result.sml_measures:>6} {result.snowflake_measures:>8}"
        )
    
    lines.extend([
        "",
        "DETAILED ANALYSIS",
        "=" * 100,
        "",
    ])
    
    # Group by status
    excellent = [r for r in results if r.success_rate >= 85]
    good = [r for r in results if 70 <= r.success_rate < 85]
    fair = [r for r in results if 50 <= r.success_rate < 70]
    poor = [r for r in results if r.success_rate < 50]
    
    for group_name, group_results in [
        ("✅ EXCELLENT (≥85%)", excellent),
        ("⚠️  GOOD (70-84%)", good),
        ("⚠️  FAIR (50-69%)", fair),
        ("❌ POOR (<50%)", poor),
    ]:
        if group_results:
            lines.extend([
                "",
                group_name,
                "-" * 100,
            ])
            for result in group_results:
                lines.extend([
                    f"  • {result.dataset_name}",
                    f"    Success Rate: {result.success_rate:.1f}%",
                    f"    Measures: Fabric={result.fabric_measures}, SML={result.sml_measures}, Snowflake={result.snowflake_measures}",
                    f"    Dropoffs: Fabric→SML: {result.fabric_sml_dropoff}, SML→Snowflake: {result.sml_snowflake_dropoff}",
                ])
    
    # Statistics
    avg_success = sum(r.success_rate for r in results) / len(results) if results else 0
    total_measures = sum(r.fabric_measures for r in results)
    total_converted = sum(r.snowflake_measures for r in results)
    
    lines.extend([
        "",
        "STATISTICS",
        "=" * 100,
        f"Average success rate: {avg_success:.1f}%",
        f"Total measures across all datasets: {total_measures}",
        f"Total successfully converted: {total_converted}",
        f"Overall conversion rate: {total_converted/total_measures*100:.1f}%" if total_measures > 0 else "N/A",
        "",
    ])
    
    lines.append("=" * 100)
    
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Run batch measure pipeline analysis on multiple datasets"
    )
    parser.add_argument(
        "--datasets",
        nargs="+",
        help="List of dataset names to analyze"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Analyze all models in workspace (requires repository access)"
    )
    parser.add_argument(
        "--workspace-id",
        help="Workspace ID (auto-detected from .env if not provided)"
    )
    parser.add_argument(
        "--output",
        default="output/batch_analysis",
        help="Output file prefix"
    )
    
    args = parser.parse_args()
    
    if not args.datasets and not args.all:
        parser.print_help()
        return
    
    setup_logging(level="INFO")
    
    workspace_id = args.workspace_id or get_settings().fabric.workspace_id
    
    # Get datasets to analyze
    datasets_to_analyze = []
    
    if args.datasets:
        datasets_to_analyze = args.datasets
    elif args.all:
        logger.info("Fetching all models from workspace...")
        # Would need to query Fabric API for all models
        logger.warning("--all not yet implemented, use --datasets instead")
        return
    
    # Run analysis on each dataset
    logger.info(f"Analyzing {len(datasets_to_analyze)} datasets...")
    results = []
    
    for i, dataset in enumerate(datasets_to_analyze, 1):
        logger.info(f"[{i}/{len(datasets_to_analyze)}] Analyzing {dataset}...")
        result = analyze_dataset(workspace_id, dataset)
        if result:
            results.append(result)
    
    # Generate and output report
    report = generate_comparison_report(results)
    print("\n" + report)
    
    # Save report
    output_path = Path(f"{args.output}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)
    
    logger.info(f"Report saved to: {output_path}")
    
    # Save JSON
    json_path = output_path.with_suffix(".json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "workspace_id": workspace_id,
            "datasets": [
                {
                    "name": r.dataset_name,
                    "success_rate": f"{r.success_rate:.1f}%",
                    "measures": {
                        "fabric": r.fabric_measures,
                        "sml": r.sml_measures,
                        "snowflake": r.snowflake_measures,
                    },
                    "dropoffs": {
                        "fabric_to_sml": r.fabric_sml_dropoff,
                        "sml_to_snowflake": r.sml_snowflake_dropoff,
                    },
                }
                for r in results
            ]
        }, f, indent=2)
    
    logger.info(f"JSON saved to: {json_path}")


if __name__ == "__main__":
    main()
