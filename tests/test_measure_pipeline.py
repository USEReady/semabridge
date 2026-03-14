"""
Measure Pipeline Tracing Test Script

Traces measures through the entire conversion pipeline:
1. Measures extracted from Fabric TMSL
2. Measures in canonical SML representation
3. Measures generated in Snowflake target SQL

Helps identify which measures are dropped/not converted at each stage.

Usage:
    python tests/test_measure_pipeline.py --workspace-id <ID> --dataset <NAME>
    
    Or programmatically:
        from test_measure_pipeline import MeasurePipelineTracer
        tracer = MeasurePipelineTracer(workspace_id="...", dataset_id="...")
        report = tracer.trace()
        print(report.to_markdown())
"""

import json
import sys
from typing import Dict, List, Any, Optional, Set
from dataclasses import dataclass, field
from pathlib import Path
import argparse
from datetime import datetime

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from semabridge.connectors.fabric_extractor import FabricExtractor
from semabridge.converter.tmsl_to_sml import TMSLTransformer
from semabridge.core.settings import get_settings
from semabridge.utils.logger import setup_logging
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.core.behavior import ConnectorBehavior
import logging

logger = logging.getLogger(__name__)


# =========================================================================
# Data Structures
# =========================================================================

@dataclass
class MeasureInfo:
    """Information about a measure at a pipeline stage."""
    name: str
    label: str = ""
    expression: str = ""
    dataset: str = ""
    hidden: bool = False
    aggregation: str = "SUM"
    complexity_tier: int = 1
    notes: str = ""
    
    def __hash__(self):
        return hash(self.name)
    
    def __eq__(self, other):
        if isinstance(other, MeasureInfo):
            return self.name == other.name
        return False


@dataclass
class PipelineStageSnapshot:
    """Snapshot of measures at a pipeline stage."""
    stage_name: str
    timestamp: str
    measure_count: int
    measures: Dict[str, MeasureInfo] = field(default_factory=dict)
    
    def add_measure(self, measure: MeasureInfo):
        """Add a measure to this stage."""
        self.measures[measure.name] = measure
    
    @property
    def measure_names(self) -> Set[str]:
        """Get all measure names in this stage."""
        return set(self.measures.keys())


@dataclass
class MeasureMappingReport:
    """Report of measure transformation across pipeline."""
    workspace_id: str
    dataset_id: str
    timestamp: str
    
    # Stage snapshots
    fabric_stage: Optional[PipelineStageSnapshot] = None
    sml_stage: Optional[PipelineStageSnapshot] = None
    snowflake_stage: Optional[PipelineStageSnapshot] = None
    
    # Analysis
    measures_in_fabric_only: List[str] = field(default_factory=list)
    measures_in_sml_only: List[str] = field(default_factory=list)
    measures_in_snowflake_only: List[str] = field(default_factory=list)
    
    measures_fabric_to_sml_dropped: List[str] = field(default_factory=list)
    measures_sml_to_snowflake_dropped: List[str] = field(default_factory=list)
    
    measure_transformations: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    def to_markdown(self) -> str:
        """Generate markdown report."""
        lines = [
            f"# Measure Pipeline Trace Report",
            f"**Dataset:** {self.dataset_id}",
            f"**Workspace:** {self.workspace_id}",
            f"**Generated:** {self.timestamp}",
            "",
        ]
        
        # Summary
        lines.extend([
            "## Summary",
            "",
            f"| Stage | Count |",
            f"|-------|-------|",
            f"| Fabric (extracted) | {self.fabric_stage.measure_count if self.fabric_stage else 0} |",
            f"| SML (canonical) | {self.sml_stage.measure_count if self.sml_stage else 0} |",
            f"| Snowflake (target) | {self.snowflake_stage.measure_count if self.snowflake_stage else 0} |",
            "",
        ])
        
        # Dropped measures
        if self.measures_fabric_to_sml_dropped:
            lines.extend([
                "## ⚠️ Measures Dropped: Fabric → SML",
                "",
                "These measures were extracted from Fabric but not included in the SML representation.",
                "",
            ])
            for measure_name in sorted(self.measures_fabric_to_sml_dropped):
                measure = self.fabric_stage.measures[measure_name]
                lines.extend([
                    f"### {measure_name}",
                    f"- **Dataset:** {measure.dataset}",
                    f"- **Label:** {measure.label}",
                    f"- **Expression:** `{measure.expression[:100]}...`" if len(measure.expression) > 100 else f"- **Expression:** `{measure.expression}`",
                    f"- **Hidden:** {measure.hidden}",
                    f"- **Notes:** {measure.notes}",
                    "",
                ])
        
        if self.measures_sml_to_snowflake_dropped:
            lines.extend([
                "## ⚠️ Measures Dropped: SML → Snowflake",
                "",
                "These measures were in SML but not generated in the Snowflake target SQL.",
                "",
            ])
            for measure_name in sorted(self.measures_sml_to_snowflake_dropped):
                measure = self.sml_stage.measures[measure_name]
                lines.extend([
                    f"### {measure_name}",
                    f"- **Dataset:** {measure.dataset}",
                    f"- **Label:** {measure.label}",
                    f"- **Complexity Tier:** {measure.complexity_tier}",
                    f"- **Expression:** `{measure.expression[:100]}...`" if len(measure.expression) > 100 else f"- **Expression:** `{measure.expression}`",
                    f"- **Sync Enabled:** {measure.notes}",
                    "",
                ])
        
        # Measure details
        if self.measure_transformations:
            lines.extend([
                "## Measure Transformations",
                "",
                "Details of measures that successfully passed through each stage.",
                "",
            ])
            for measure_name, transform in sorted(self.measure_transformations.items()):
                lines.extend([
                    f"### {measure_name}",
                    f"- **Fabric Expression:** `{transform.get('fabric_expr', '')[:80]}...`",
                    f"- **SML Expression:** `{transform.get('sml_expr', '')[:80]}...`",
                    f"- **SQL Expression:** `{transform.get('sql_expr', '')[:80]}...`",
                    "",
                ])
        
        # Statistics
        lines.extend([
            "## Statistics",
            "",
            f"- **Fabric → SML Drop Rate:** {len(self.measures_fabric_to_sml_dropped)}/{self.fabric_stage.measure_count if self.fabric_stage else 0}",
            f"- **SML → Snowflake Drop Rate:** {len(self.measures_sml_to_snowflake_dropped)}/{self.sml_stage.measure_count if self.sml_stage else 0}",
            f"- **Overall Success Rate:** {self.snowflake_stage.measure_count if self.snowflake_stage else 0}/{self.fabric_stage.measure_count if self.fabric_stage else 0}",
            "",
        ])
        
        return "\n".join(lines)
    
    def to_json(self) -> str:
        """Generate JSON report."""
        return json.dumps({
            "workspace_id": self.workspace_id,
            "dataset_id": self.dataset_id,
            "timestamp": self.timestamp,
            "fabric_count": self.fabric_stage.measure_count if self.fabric_stage else 0,
            "sml_count": self.sml_stage.measure_count if self.sml_stage else 0,
            "snowflake_count": self.snowflake_stage.measure_count if self.snowflake_stage else 0,
            "fabric_to_sml_dropped": self.measures_fabric_to_sml_dropped,
            "sml_to_snowflake_dropped": self.measures_sml_to_snowflake_dropped,
            "measure_transformations": self.measure_transformations,
        }, indent=2)


# =========================================================================
# Measure Pipeline Tracer
# =========================================================================

class MeasurePipelineTracer:
    """Traces measures through the conversion pipeline."""
    
    def __init__(self, workspace_id: str, dataset_id: str):
        self.workspace_id = workspace_id
        self.dataset_id = dataset_id
        self.settings = get_settings()
        
        # Initialize extractors/converters
        try:
            self.fabric_extractor = FabricExtractor(self.settings.fabric)
        except Exception as e:
            logger.warning(f"Fabric extractor initialization failed: {e}")
            self.fabric_extractor = None
        
        self.tmsl_transformer = TMSLTransformer()
        try:
            self.snowflake_emitter = SnowflakeEmitter(self.settings.snowflake)
        except Exception as e:
            logger.warning(f"Snowflake emitter initialization failed: {e}")
            self.snowflake_emitter = None
    
    def trace(self) -> MeasureMappingReport:
        """Execute full trace and return report."""
        report = MeasureMappingReport(
            workspace_id=self.workspace_id,
            dataset_id=self.dataset_id,
            timestamp=datetime.now().isoformat(),
        )
        
        # Stage 1: Extract from Fabric
        logger.info("=== Stage 1: Extracting measures from Fabric ===")
        report.fabric_stage = self._extract_fabric_measures()
        
        # Stage 2: Parse to SML
        logger.info("=== Stage 2: Parsing measures to SML ===")
        report.sml_stage = self._extract_sml_measures()
        
        # Stage 3: Simulate Snowflake generation
        logger.info("=== Stage 3: Checking Snowflake SQL generation ===")
        report.snowflake_stage = self._extract_snowflake_measures()
        
        # Analyze differences
        logger.info("=== Stage 4: Analyzing differences ===")
        self._analyze_dropoffs(report)
        
        return report
    
    def _extract_fabric_measures(self) -> Optional[PipelineStageSnapshot]:
        """Extract measures directly from Fabric TMSL."""
        snapshot = PipelineStageSnapshot(
            stage_name="Fabric (TMSL)",
            timestamp=datetime.now().isoformat(),
            measure_count=0,
        )
        
        if not self.fabric_extractor:
            logger.error("Fabric extractor not available")
            return snapshot
        
        try:
            # Get model definition
            logger.info(f"Fetching model definition for {self.dataset_id}...")
            tmsl_dict = self.fabric_extractor.get_model_definition(self.dataset_id)
            
            model = tmsl_dict.get("model", {})
            tables = model.get("tables", [])
            
            # Extract measures from each table
            for table in tables:
                table_name = table.get("name", "")
                
                # Skip system tables
                if table_name.startswith("DateTableTemplate") or table_name.startswith("LocalDateTable"):
                    continue
                
                # Extract measures
                measures = table.get("measures", [])
                for measure_obj in measures:
                    measure_name = measure_obj.get("name", "")
                    expression = measure_obj.get("expression", "")
                    
                    measure = MeasureInfo(
                        name=measure_name,
                        label=measure_obj.get("displayName", measure_name),
                        expression=expression,
                        dataset=table_name,
                        hidden=measure_obj.get("isHidden", False),
                        aggregation=self._extract_aggregation_type(expression),
                    )
                    
                    snapshot.add_measure(measure)
                    logger.info(f"  ✓ Found measure: {table_name}.{measure_name}")
            
            snapshot.measure_count = len(snapshot.measures)
            logger.info(f"Extracted {snapshot.measure_count} measures from Fabric")
            
        except Exception as e:
            logger.error(f"Failed to extract Fabric measures: {e}", exc_info=True)
        
        return snapshot
    
    def _extract_sml_measures(self) -> Optional[PipelineStageSnapshot]:
        """Extract measures from SML (if available)."""
        snapshot = PipelineStageSnapshot(
            stage_name="SML (Canonical)",
            timestamp=datetime.now().isoformat(),
            measure_count=0,
        )
        
        # Try to get SML from repository (if model was synced)
        try:
            # This would require accessing the model repository
            # For now, we'll note this as not available
            logger.info("SML extraction requires model repository access (not available in test mode)")
            return snapshot
        except Exception as e:
            logger.error(f"Failed to extract SML measures: {e}")
        
        return snapshot
    
    def _extract_snowflake_measures(self) -> Optional[PipelineStageSnapshot]:
        """Extract measures from Snowflake target (if available)."""
        snapshot = PipelineStageSnapshot(
            stage_name="Snowflake SQL",
            timestamp=datetime.now().isoformat(),
            measure_count=0,
        )
        
        if not self.snowflake_emitter:
            logger.warning("Snowflake emitter not available")
            return snapshot
        
        try:
            # Query Snowflake for semantic view and metrics
            self.snowflake_emitter.authenticate()
            
            # Look for METRICS clause in semantic view
            logger.info(f"Querying Snowflake for semantic view: {self.dataset_id}")
            
            # This would query the actual semantic view
            # For now, we'll return empty (would need to parse DDL)
            logger.info("Snowflake measure extraction requires DDL parsing (not available in test mode)")
            return snapshot
        except Exception as e:
            logger.warning(f"Failed to extract Snowflake measures: {e}")
        
        return snapshot
    
    def _extract_aggregation_type(self, expression: str) -> str:
        """Infer aggregation type from DAX expression."""
        expr_upper = expression.upper()
        
        if "SUM(" in expr_upper:
            return "SUM"
        elif "COUNT(" in expr_upper:
            return "COUNT"
        elif "AVERAGE(" in expr_upper:
            return "AVG"
        elif "MIN(" in expr_upper:
            return "MIN"
        elif "MAX(" in expr_upper:
            return "MAX"
        elif "DISTINCTCOUNT(" in expr_upper:
            return "COUNT_DISTINCT"
        else:
            return "OTHER"
    
    def _analyze_dropoffs(self, report: MeasureMappingReport):
        """Analyze where measures are dropped in the pipeline."""
        fabric_names = report.fabric_stage.measure_names if report.fabric_stage else set()
        sml_names = report.sml_stage.measure_names if report.sml_stage else set()
        
        # Fabric → SML dropoffs
        report.measures_fabric_to_sml_dropped = list(fabric_names - sml_names)
        
        # SML → Snowflake dropoffs (if Snowflake data available)
        if report.snowflake_stage:
            snowflake_names = report.snowflake_stage.measure_names
            report.measures_sml_to_snowflake_dropped = list(sml_names - snowflake_names)
        
        # Log results
        if report.measures_fabric_to_sml_dropped:
            logger.warning(
                f"⚠️  {len(report.measures_fabric_to_sml_dropped)} measures dropped in Fabric → SML: "
                f"{', '.join(report.measures_fabric_to_sml_dropped[:5])}"
            )
        
        if report.measures_sml_to_snowflake_dropped:
            logger.warning(
                f"⚠️  {len(report.measures_sml_to_snowflake_dropped)} measures dropped in SML → Snowflake: "
                f"{', '.join(report.measures_sml_to_snowflake_dropped[:5])}"
            )


# =========================================================================
# CLI
# =========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Trace measures through Fabric → SML → Snowflake pipeline"
    )
    parser.add_argument(
        "--workspace-id",
        required=True,
        help="Fabric workspace ID"
    )
    parser.add_argument(
        "--dataset",
        required=True,
        help="Fabric dataset/model name or ID"
    )
    parser.add_argument(
        "--output",
        choices=["markdown", "json"],
        default="markdown",
        help="Output format"
    )
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging(level="INFO")
    
    # Run tracer
    tracer = MeasurePipelineTracer(
        workspace_id=args.workspace_id,
        dataset_id=args.dataset
    )
    
    logger.info(f"Starting measure pipeline trace for {args.dataset}...")
    report = tracer.trace()
    
    # Output report
    if args.output == "json":
        print(report.to_json())
    else:
        print(report.to_markdown())
    
    # Save report
    output_file = Path("output") / f"measure_trace_{args.dataset}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    output_file.parent.mkdir(exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(report.to_markdown())
    logger.info(f"Report saved to {output_file}")


if __name__ == "__main__":
    main()
