"""
Enhanced Measure Pipeline Debug Script

This script provides detailed debugging for measure conversion:
- Extracts measures at each pipeline stage
- Compares transformations
- Identifies bottlenecks and dropoffs
- Generates detailed comparison reports

Usage:
    python -m pytest tests/test_measure_pipeline_detailed.py -v -s --dataset COMPETITIVE_MARKETING_ANALYSIS
    
    Or directly:
    python tests/test_measure_pipeline_detailed.py --dataset COMPETITIVE_MARKETING_ANALYSIS
"""

import json
import sys
from typing import Dict, List, Any, Optional, Set, Tuple
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime
import argparse

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from semabridge.connectors.fabric_extractor import FabricExtractor
from semabridge.converter.tmsl_to_sml import TMSLTransformer
from semabridge.core.settings import get_settings
from semabridge.utils.logger import setup_logging, get_logger
from semabridge.repository.model_repository import ModelRepository
from semabridge.core.behavior import ConnectorBehavior
import logging

logger = get_logger(__name__)


# =========================================================================
# Enhanced Measure Info & Tracking
# =========================================================================

@dataclass
class DetailedMeasureInfo:
    """Detailed measure information with pipeline tracking."""
    name: str
    dataset: str
    label: str = ""
    expression: str = ""
    sql_expression: Optional[str] = None
    hidden: bool = False
    aggregation: str = "SUM"
    complexity_tier: int = 1
    requires_time_intel: bool = False
    depends_on_measures: List[str] = field(default_factory=list)
    sync_enabled: bool = True
    sync_failure_reason: Optional[str] = None
    confidence: float = 1.0
    
    # Pipeline tracking
    in_fabric: bool = False
    in_sml: bool = False
    in_snowflake: bool = False
    
    # Transformation notes
    transformation_notes: List[str] = field(default_factory=list)
    
    def mark_stage(self, stage: str):
        """Mark measure as present in a stage."""
        if stage == "fabric":
            self.in_fabric = True
        elif stage == "sml":
            self.in_sml = True
        elif stage == "snowflake":
            self.in_snowflake = True
    
    def add_note(self, note: str):
        """Add transformation note."""
        self.transformation_notes.append(f"[{datetime.now().strftime('%H:%M:%S')}] {note}")
    
    @property
    def pipeline_status(self) -> str:
        """Get pipeline status summary."""
        stages = []
        if self.in_fabric: stages.append("Fabric")
        if self.in_sml: stages.append("SML")
        if self.in_snowflake: stages.append("Snowflake")
        
        if len(stages) == 3:
            return "✅ COMPLETE"
        elif len(stages) == 0:
            return "❌ NOT FOUND"
        else:
            return f"⚠️  PARTIAL ({' → '.join(stages)})"


@dataclass
class MeasurePipelineAnalysis:
    """Complete pipeline analysis for all measures."""
    dataset_id: str
    workspace_id: str
    timestamp: str
    
    # All measures indexed by name
    all_measures: Dict[str, DetailedMeasureInfo] = field(default_factory=dict)
    
    # Stage-specific information
    fabric_measure_count: int = 0
    sml_measure_count: int = 0
    snowflake_measure_count: int = 0
    
    # Analysis results
    analysis_summary: Dict[str, Any] = field(default_factory=dict)
    
    def generate_report(self) -> str:
        """Generate detailed analysis report."""
        lines = [
            "=" * 80,
            "MEASURE PIPELINE ANALYSIS REPORT",
            "=" * 80,
            f"Dataset: {self.dataset_id}",
            f"Workspace: {self.workspace_id}",
            f"Generated: {self.timestamp}",
            "",
            "=" * 80,
            "PIPELINE OVERVIEW",
            "=" * 80,
            "",
            f"Stage Counts:",
            f"  • Fabric (extracted):      {self.fabric_measure_count}",
            f"  • SML (canonical):         {self.sml_measure_count}",
            f"  • Snowflake (target):      {self.snowflake_measure_count}",
            "",
        ]
        
        # Status distribution
        status_dist: Dict[str, int] = {}
        for measure in self.all_measures.values():
            status = measure.pipeline_status
            status_dist[status] = status_dist.get(status, 0) + 1
        
        lines.append("Status Distribution:")
        for status, count in sorted(status_dist.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  {status:<20} {count:>3} measures")
        
        lines.extend(["", "=" * 80, "MEASURES BY STATUS", "=" * 80, ""])
        
        # Complete measures (all stages)
        # A measure is complete if it can be represented in Snowflake
        # (it doesn't need Fabric origin if it's already in SML with SQL expression)
        complete = [m for m in self.all_measures.values() if m.in_sml and m.in_snowflake]
        if complete:
            lines.append(f"\n✅ COMPLETE ({len(complete)} measures):")
            lines.append("-" * 80)
            for measure in sorted(complete, key=lambda m: m.name):
                lines.extend([
                    f"  {measure.dataset}.{measure.name}",
                    f"    Expression: {measure.expression[:70]}..." if len(measure.expression) > 70 else f"    Expression: {measure.expression}",
                    f"    SQL: {measure.sql_expression[:70]}..." if measure.sql_expression and len(measure.sql_expression) > 70 else f"    SQL: {measure.sql_expression}",
                    ""
                ])
        
        # Partial measures (some stages)
        partial = [m for m in self.all_measures.values() if not (m.in_fabric and m.in_sml and m.in_snowflake)]
        if partial:
            lines.append(f"\n⚠️  PARTIAL ({len(partial)} measures):")
            lines.append("-" * 80)
            for measure in sorted(partial, key=lambda m: m.name):
                lines.extend([
                    f"  {measure.dataset}.{measure.name}",
                    f"    Status: {measure.pipeline_status}",
                    f"    Expression: {measure.expression[:70]}..." if measure.expression else "    Expression: (none)",
                ])
                if measure.sync_failure_reason:
                    lines.append(f"    ❌ Reason: {measure.sync_failure_reason}")
                for note in measure.transformation_notes[-3:]:  # Last 3 notes
                    lines.append(f"    📝 {note}")
                lines.append("")
        
        # Dropoff analysis
        lines.extend(["", "=" * 80, "DROPOFF ANALYSIS", "=" * 80, ""])
        
        fabric_only = [m for m in self.all_measures.values() if m.in_fabric and not m.in_sml]
        sml_only = [m for m in self.all_measures.values() if m.in_sml and not m.in_snowflake]
        
        if fabric_only:
            lines.append(f"\n❌ Fabric → SML Dropoff ({len(fabric_only)} measures):")
            lines.append("-" * 80)
            for measure in sorted(fabric_only, key=lambda m: m.name):
                lines.extend([
                    f"  • {measure.dataset}.{measure.name}",
                    f"    Why: Likely filtered by tmsl_to_sml converter (hidden, system table, etc)",
                    ""
                ])
        
        if sml_only:
            lines.append(f"\n❌ SML → Snowflake Dropoff ({len(sml_only)} measures):")
            lines.append("-" * 80)
            for measure in sorted(sml_only, key=lambda m: m.name):
                reason = measure.sync_failure_reason or "Unknown (check sync_enabled flag)"
                lines.extend([
                    f"  • {measure.dataset}.{measure.name}",
                    f"    Reason: {reason}",
                    f"    Complexity: Tier {measure.complexity_tier}",
                    f"    Time Intelligence: {measure.requires_time_intel}",
                    ""
                ])
        
        # Summary statistics
        lines.extend(["", "=" * 80, "STATISTICS", "=" * 80, ""])
        
        total = len(self.all_measures)
        complete_count = len(complete)
        success_rate = (complete_count / total * 100) if total > 0 else 0
        
        lines.extend([
            f"Total unique measures: {total}",
            f"Successfully converted: {complete_count}/{total} ({success_rate:.1f}%)",
            f"Fabric → SML drop rate: {len(fabric_only)}/{self.fabric_measure_count} ({len(fabric_only)/self.fabric_measure_count*100:.1f}%)" if self.fabric_measure_count else "N/A",
            f"SML → Snowflake drop rate: {len(sml_only)}/{self.sml_measure_count} ({len(sml_only)/self.sml_measure_count*100:.1f}%)" if self.sml_measure_count else "N/A",
        ])
        
        lines.extend(["", "=" * 80])
        
        return "\n".join(lines)


# =========================================================================
# Pipeline Analyzer
# =========================================================================

class MeasurePipelineDebugger:
    """Advanced measure pipeline debugger."""
    
    def __init__(self, workspace_id: str, dataset_id: str):
        self.workspace_id = workspace_id
        self.dataset_id = dataset_id
        self.settings = get_settings()
        self.analysis = MeasurePipelineAnalysis(
            dataset_id=dataset_id,
            workspace_id=workspace_id,
            timestamp=datetime.now().isoformat(),
        )
        
        # Initialize components
        self.fabric_extractor = None
        self.tmsl_transformer = TMSLTransformer()
        self.db_manager = ModelRepository()
        
        try:
            self.fabric_extractor = FabricExtractor(self.settings.fabric)
        except Exception as e:
            logger.warning(f"Fabric extractor init failed: {e}")
    
    def analyze_pipeline(self) -> MeasurePipelineAnalysis:
        """Run complete pipeline analysis."""
        logger.info("=" * 80)
        logger.info("STARTING MEASURE PIPELINE ANALYSIS")
        logger.info("=" * 80)
        
        # Stage 1: Extract from Fabric
        logger.info("\n[STAGE 1] Extracting measures from Fabric...")
        self._extract_fabric_measures()
        
        # Stage 2: Convert to SML
        logger.info("\n[STAGE 2] Converting to SML...")
        self._convert_to_sml()
        
        # Stage 3: Check Snowflake generation
        logger.info("\n[STAGE 3] Checking Snowflake SQL generation...")
        self._check_snowflake_generation()
        
        # Generate report
        logger.info("\n[STAGE 4] Generating analysis report...")
        self._generate_analysis()
        
        return self.analysis
    
    def _extract_fabric_measures(self):
        """Extract and catalog measures from Fabric."""
        if not self.fabric_extractor:
            logger.warning("Fabric extractor not available - skipping Fabric extraction")
            return
        
        try:
            # Get TMSL definition (used for reference, but measures are extracted during SML conversion)
            logger.info(f"Fetching model definition for '{self.dataset_id}'...")
            tmsl_dict = self.fabric_extractor.get_model_definition(self.dataset_id)
            
            model = tmsl_dict.get("model", {})
            tables = model.get("tables", [])
            
            # Extract measures from TMSL for reference
            measure_count = 0
            for table in tables:
                table_name = table.get("name", "")
                
                # Skip system tables
                if any(table_name.startswith(prefix) for prefix in ["DateTableTemplate", "LocalDateTable"]):
                    logger.debug(f"Skipping system table: {table_name}")
                    continue
                
                # Try to get measures from TMSL (structure varies by Power BI version)
                measures = table.get("measures", [])
                for measure_obj in measures:
                    measure_name = measure_obj.get("name", "")
                    if not measure_name:
                        continue
                    
                    measure_info = DetailedMeasureInfo(
                        name=measure_name,
                        dataset=table_name,
                        label=measure_obj.get("displayName", measure_name),
                        expression=measure_obj.get("expression", ""),
                        hidden=measure_obj.get("isHidden", False),
                        aggregation=self._infer_aggregation(measure_obj.get("expression", "")),
                    )
                    
                    measure_info.mark_stage("fabric")
                    measure_info.add_note(f"Extracted from Fabric TMSL for table '{table_name}'")
                    
                    self.analysis.all_measures[measure_name] = measure_info
                    measure_count += 1
                    
                    logger.info(f"  ✓ TMSL: {table_name}.{measure_name}")
            
            self.analysis.fabric_measure_count = measure_count
            if measure_count > 0:
                logger.info(f"✅ Found {measure_count} measures in Fabric TMSL")
            else:
                logger.info("ℹ️  No measures directly in TMSL (will extract during SML conversion)")
            
        except Exception as e:
            logger.warning(f"⚠️  Fabric TMSL extraction limited (will use SML conversion): {e}")
    
    def _convert_to_sml(self):
        """Convert Fabric measures to SML and update analysis."""
        if not self.fabric_extractor:
            return
        
        try:
            tmsl_dict = self.fabric_extractor.get_model_definition(self.dataset_id)
            sml_model = self.tmsl_transformer.transform(
                tmsl_dict,
                workspace_id=self.workspace_id,
                dataset_id=self.dataset_id,
            )
            
            # Process SML-level metrics (they are stored at model level, not dataset level)
            sml_measure_count = 0
            if hasattr(sml_model, 'metrics') and sml_model.metrics:
                for metric in sml_model.metrics:
                    if metric.unique_name not in self.analysis.all_measures:
                        # New measure found in SML
                        measure_info = DetailedMeasureInfo(
                            name=metric.unique_name,
                            dataset=metric.dataset,
                            label=getattr(metric, 'label', metric.unique_name),
                            expression=getattr(metric, 'expression', None),
                            sql_expression=None,  # Will be set from DAX translation
                        )
                        self.analysis.all_measures[metric.unique_name] = measure_info
                    else:
                        # Update existing measure
                        measure_info = self.analysis.all_measures[metric.unique_name]
                        # Don't overwrite sql_expression here - will be set from DAX translation
                        measure_info.complexity_tier = getattr(metric, 'complexity_tier', 1)
                        measure_info.requires_time_intel = getattr(metric, 'requires_time_intel', False)
                        measure_info.depends_on_measures = getattr(metric, 'depends_on_measures', [])
                        measure_info.sync_enabled = getattr(metric, 'sync_enabled', True)
                        measure_info.sync_failure_reason = getattr(metric, 'sync_failure_reason', None)
                        measure_info.confidence = getattr(metric, 'confidence', 1.0)
                    
                    measure_info.mark_stage("sml")
                    measure_info.add_note(f"Converted to SML (dataset: '{metric.dataset}')")
                    
                    # Translate DAX to Snowflake SQL using Gemini
                    if hasattr(metric, 'expression') and metric.expression:
                        try:
                            from semabridge.converter.dax_translator import DAXTranslator
                            dax_translator = DAXTranslator()
                            # Use unique_name for table_alias if available
                            metric_label = getattr(metric, 'unique_name', getattr(metric, 'name', 'metric')).lower().replace(" ", "_")
                            dax_result = dax_translator.translate(
                                dax=metric.expression,
                                table_alias=metric_label,
                                dataset_name=metric.dataset,
                                metric_name=metric.unique_name
                            )
                            if dax_result and dax_result.sql:
                                measure_info.sql_expression = dax_result.sql
                                measure_info.complexity_tier = dax_result.tier
                                measure_info.confidence = dax_result.confidence if hasattr(dax_result, 'confidence') else 1.0
                                measure_info.add_note(f"Translated DAX to SQL (tier: {dax_result.tier}, confidence: {measure_info.confidence:.2f})")
                            else:
                                measure_info.sync_failure_reason = "DAX translation failed"
                        except Exception as e:
                            logger.warning(f"Could not translate measure {metric.unique_name}: {e}")
                            measure_info.sync_failure_reason = f"Translation error: {str(e)[:50]}"
                    
                    sml_measure_count += 1
                    
                    complexity = getattr(metric, 'complexity_tier', 1)
                    aggregation = getattr(metric, 'aggregation', 'UNKNOWN')
                    logger.info(f"  ✓ {metric.dataset}.{metric.unique_name} ({aggregation}, tier: {complexity})")
            else:
                logger.info("ℹ️  No metrics created during SML conversion")
            
            self.analysis.sml_measure_count = sml_measure_count
            logger.info(f"✅ Converted {sml_measure_count} measures to SML")
            
        except AttributeError as ae:
            logger.error(f"❌ SML structure issue: {ae}", exc_info=True)
        except Exception as e:
            logger.error(f"❌ Failed to convert to SML: {e}", exc_info=True)
    
    def _check_snowflake_generation(self):
        """Check which measures would be generated in Snowflake SQL."""
        # This would require running the emitter and parsing the DDL
        # For now, we mark measures that are eligible for Snowflake generation
        
        snowflake_eligible = 0
        for measure_info in self.analysis.all_measures.values():
            # Check if measure has SQL expression and is enabled for sync
            if measure_info.sql_expression and measure_info.sync_enabled:
                measure_info.mark_stage("snowflake")
                measure_info.add_note("Eligible for Snowflake METRICS clause")
                snowflake_eligible += 1
                logger.info(f"  ✓ {measure_info.dataset}.{measure_info.name} (SQL: {measure_info.sql_expression[:50]}...)")
            elif measure_info.in_sml and measure_info.sync_failure_reason:
                logger.warning(f"  ⚠️  {measure_info.dataset}.{measure_info.name} - {measure_info.sync_failure_reason}")
        
        self.analysis.snowflake_measure_count = snowflake_eligible
        logger.info(f"✅ {snowflake_eligible} measures eligible for Snowflake generation")
    
    def _infer_aggregation(self, expression: str) -> str:
        """Infer aggregation type from expression."""
        expr_upper = expression.upper()
        
        patterns = {
            "SUM": "SUM",
            "COUNT": "COUNT",
            "AVERAGE": "AVG",
            "MIN": "MIN",
            "MAX": "MAX",
            "DISTINCTCOUNT": "COUNT_DISTINCT",
        }
        
        for pattern, agg in patterns.items():
            if pattern in expr_upper:
                return agg
        
        return "OTHER"
    
    def _generate_analysis(self):
        """Generate analysis summary."""
        total = len(self.analysis.all_measures)
        complete = sum(1 for m in self.analysis.all_measures.values() if m.in_sml and m.in_snowflake)
        partial = sum(1 for m in self.analysis.all_measures.values() if not (m.in_sml and m.in_snowflake))
        
        self.analysis.analysis_summary = {
            "total_measures": total,
            "complete_conversion": complete,
            "partial_conversion": partial,
            "success_rate": f"{complete/total*100:.1f}%" if total > 0 else "N/A",
        }


# =========================================================================
# Main Entry Point
# =========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Debug measure conversion through Fabric → SML → Snowflake pipeline"
    )
    parser.add_argument("--workspace-id", default="", help="Fabric workspace ID (auto-detected if not provided)")  
    parser.add_argument("--dataset", required=True, help="Fabric dataset/model name or ID")
    parser.add_argument("--output", default="output/measure_analysis", help="Output file prefix")
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging(level="INFO")
    
    # Build workspace ID from settings if not provided
    workspace_id = args.workspace_id or get_settings().fabric.workspace_id
    
    # Run debugger
    debugger = MeasurePipelineDebugger(workspace_id=workspace_id, dataset_id=args.dataset)
    analysis = debugger.analyze_pipeline()
    
    # Output report
    report = analysis.generate_report()
    print("\n" + report)
    
    # Save report
    output_path = Path(args.output + f"_{args.dataset}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)
    
    logger.info(f"\n✅ Report saved to: {output_path}")
    
    # Save JSON for programmatic access
    json_path = output_path.with_suffix(".json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "dataset": args.dataset,
            "workspace": workspace_id,
            "timestamp": analysis.timestamp,
            "summary": analysis.analysis_summary,
            "measures": {
                name: {
                    "dataset": m.dataset,
                    "label": m.label,
                    "status": m.pipeline_status,
                    "complexity_tier": m.complexity_tier,
                    "sync_enabled": m.sync_enabled,
                    "in_fabric": m.in_fabric,
                    "in_sml": m.in_sml,
                    "in_snowflake": m.in_snowflake,
                }
                for name, m in analysis.all_measures.items()
            }
        }, f, indent=2)
    
    logger.info(f"✅ JSON saved to: {json_path}")


if __name__ == "__main__":
    main()
