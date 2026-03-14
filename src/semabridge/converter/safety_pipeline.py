"""
Safety Pipeline — complete tiered safety evaluation pipeline.

Orchestrates:
- DAX expression analysis
- Safety tier classification
- Override generation
- Validation
- Report generation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from semabridge.converter.tiered_safety import (
    SafetyClassification,
    SafetyTier,
    TieredSafetyClassifier,
)
from semabridge.converter.override_schema import SQLOverrideFile
from semabridge.converter.override_generator import OverrideGenerator
from semabridge.converter.override_validator import OverrideValidator, OverrideValidationResult


@dataclass
class SafetyPipelineResult:
    """Result of executing the safety pipeline."""

    model_name: str
    classifications: List[SafetyClassification] = field(default_factory=list)
    overrides: Optional[SQLOverrideFile] = None
    validation_result: Optional[OverrideValidationResult] = None
    warnings: List[str] = field(default_factory=list)

    def get_high_risk_measures(self) -> List[SafetyClassification]:
        """Get measures classified as Tier 4 or higher."""
        return [
            c for c in self.classifications
            if c.tier in (SafetyTier.TIER_4, SafetyTier.TIER_5)
        ]

    def get_complexity_summary(self) -> Dict[str, int]:
        """Get summary of safety tier distribution."""
        summary = {
            "tier_1": 0,
            "tier_2": 0,
            "tier_3": 0,
            "tier_4": 0,
            "tier_5": 0,
        }
        for classification in self.classifications:
            key = f"tier_{classification.tier.value.split('_')[1]}"
            summary[key] += 1
        return summary


class TieredSafetyPipeline:
    """
    Complete pipeline for analyzing and classifying semantic model safety.
    """

    def __init__(
        self,
        database: str = "ANALYSIS_DB",
        schema: str = "SEMANTIC",
        warehouse: str = "COMPUTE_WH",
    ):
        """
        Initialize the safety pipeline.

        Args:
            database: Target Snowflake database
            schema: Target Snowflake schema
            warehouse: Compute warehouse
        """
        self.classifier = TieredSafetyClassifier()
        self.generator = OverrideGenerator(database, schema, warehouse)
        self.validator = OverrideValidator()

    def analyze_model(
        self,
        model: Any,
        generate_overrides: bool = True,
        validate: bool = True,
    ) -> SafetyPipelineResult:
        """
        Analyze a semantic model through the full safety pipeline.

        Args:
            model: Semantic model object to analyze
            generate_overrides: Generate override configuration
            validate: Validate generated overrides

        Returns:
            SafetyPipelineResult with classifications and recommendations
        """
        model_name = getattr(model, 'unique_name', 'model')
        result = SafetyPipelineResult(model_name=model_name)

        # Step 1: Classify measures
        if hasattr(model, 'metrics'):
            for metric in model.metrics:
                measure_name = getattr(metric, 'unique_name', 'metric')
                expression = getattr(metric, 'expression', '')

                classification = self.classifier.classify(measure_name, expression)
                result.classifications.append(classification)

        # Step 2: Generate overrides if requested
        if generate_overrides:
            result.overrides = self.generator.generate_override_file(model)

        # Step 3: Validate overrides if generated
        if validate and result.overrides:
            # Create a temporary validation result for the object
            validation_result = self.validator.validate_config(result.overrides)
            result.validation_result = validation_result

            if not validation_result.is_valid:
                for error in validation_result.errors:
                    result.warnings.append(f"Validation error: {error.message}")

        # Step 4: Generate warnings for high-risk measures
        high_risk = result.get_high_risk_measures()
        if high_risk:
            result.warnings.append(
                f"Found {len(high_risk)} high-risk measures (Tier 4-5) requiring review"
            )

        return result

    def analyze_measure(
        self,
        measure_name: str,
        expression: str,
    ) -> SafetyClassification:
        """
        Analyze a single DAX measure.

        Args:
            measure_name: Name of the measure
            expression: DAX expression

        Returns:
            SafetyClassification
        """
        return self.classifier.classify(measure_name, expression)
