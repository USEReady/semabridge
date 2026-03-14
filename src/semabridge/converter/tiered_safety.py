"""
Tiered Safety DAX Classification Engine.

Provides:
- Hazard category detection (complexity/risk classification)
- Safety tier assignment based on DAX expression complexity
- DAX expression pattern matching for safety analysis
- Model-wide safety classification

The Tiered Safety system classifies DAX measures into safety tiers:
- Tier 1: Simple aggregations (SUM, COUNT, AVG)
- Tier 2: Arithmetic operations and basic functions
- Tier 3: CALCULATE with filters, context manipulation
- Tier 4: Complex recursion, nested CALCULATE
- Tier 5+: Dynamic security, advanced patterns
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set
import re


# ───────────────────────────────────────────────────────────────────────────
# Enumerations
# ───────────────────────────────────────────────────────────────────────────

class SafetyTier(str, Enum):
    """Safety classification tier for DAX expressions."""

    TIER_1 = "tier_1"  # Simple aggregations
    TIER_2 = "tier_2"  # Arithmetic operations
    TIER_3 = "tier_3"  # CALCULATE with filters
    TIER_4 = "tier_4"  # Complex recursion
    TIER_5 = "tier_5"  # Advanced/risky patterns


class HazardCategory(str, Enum):
    """Types of hazards detected in DAX expressions."""

    NONE = "none"
    COMPLEXITY = "complexity"
    RECURSION = "recursion"
    CONTEXT_MANIPULATION = "context_manipulation"
    DYNAMIC_SECURITY = "dynamic_security"
    UNKNOWN_TABLE_REF = "unknown_table_ref"


class OverrideLayerType(str, Enum):
    """Types of override layers in the semantic override system."""

    CORTEX_METADATA = "cortex_metadata"
    SEMANTIC_VIEW = "semantic_view"
    DYNAMIC_TABLE = "dynamic_table"
    SQL_OVERRIDE = "sql_override"
    WINDOW_FUNCTION = "window_function"


# ───────────────────────────────────────────────────────────────────────────
# Data Classes
# ───────────────────────────────────────────────────────────────────────────

@dataclass
class HazardDetection:
    """Result of hazard detection in a DAX expression."""

    category: HazardCategory = HazardCategory.NONE
    severity: int = 0  # 0-10 scale
    details: str = ""
    patterns_matched: List[str] = field(default_factory=list)


@dataclass
class SafetyClassification:
    """Complete safety classification for a DAX measure."""

    measure_name: str
    expression: str
    tier: SafetyTier
    hazards: List[HazardDetection] = field(default_factory=list)
    confidence: float = 1.0
    recommendations: List[str] = field(default_factory=list)

    def add_hazard(self, hazard: HazardDetection) -> None:
        """Add a detected hazard to this classification."""
        self.hazards.append(hazard)

    def add_recommendation(self, rec: str) -> None:
        """Add a safety recommendation."""
        if rec not in self.recommendations:
            self.recommendations.append(rec)


# ───────────────────────────────────────────────────────────────────────────
# Tiered Safety Classifier
# ───────────────────────────────────────────────────────────────────────────

class TieredSafetyClassifier:
    """
    Classifies DAX expressions into safety tiers based on complexity and patterns.
    """

    # Tier 1: Simple aggregations
    TIER_1_PATTERNS = [
        r'\bSUM\s*\(',
        r'\bCOUNT\s*\(',
        r'\bCOUNTA\s*\(',
        r'\bCOUNTX\s*\(',
        r'\bAVERAGE\s*\(',
        r'\bAVERAGEX\s*\(',
        r'\bMIN\s*\(',
        r'\bMAX\s*\(',
    ]

    # Tier 2: Arithmetic and functions
    TIER_2_PATTERNS = [
        r'\bDIVIDE\s*\(',
        r'\bMULTIPLY\s*\(',
        r'\bADD\s*\(',
        r'\bSUBTRACT\s*\(',
        r'\bIF\s*\(',
        r'\bIFERROR\s*\(',
    ]

    # Tier 3: Context manipulation
    TIER_3_PATTERNS = [
        r'\bCALCULATE\s*\(',
        r'\bFILTER\s*\(',
        r'\bALL\s*\(',
    ]

    # Tier 4: Complex patterns
    TIER_4_PATTERNS = [
        r'\bCALCULATE\s*\(.*\bCALCULATE\s*\(',
        r'\bRECURSIVE\s*',
    ]

    # Tier 5: Advanced/risky
    TIER_5_PATTERNS = [
        r'\bUSERPRINCIPALNAME\s*\(',
        r'\bDYNAMICSECURITY\s*',
        r'\bEVALUATE\s*\(',
    ]

    def __init__(self):
        pass

    def classify(self, measure_name: str, expression: str) -> SafetyClassification:
        """
        Classify a DAX measure expression into a safety tier.

        Args:
            measure_name: Name of the measure
            expression: DAX expression string

        Returns:
            SafetyClassification with tier and hazard details
        """
        expression_upper = expression.upper()

        # Detect hazards
        hazards = self._detect_hazards(expression_upper)

        # Determine tier
        tier = self._determine_tier(expression_upper)

        # Create classification
        classification = SafetyClassification(
            measure_name=measure_name,
            expression=expression,
            tier=tier,
            hazards=hazards,
        )

        # Add recommendations based on tier
        if tier == SafetyTier.TIER_4:
            classification.add_recommendation("Consider simplifying this expression to reduce complexity")
        elif tier == SafetyTier.TIER_5:
            classification.add_recommendation("This expression uses advanced features; ensure proper access controls")
            classification.add_recommendation("Review dynamic security implementation")

        return classification

    def _detect_hazards(self, expression: str) -> List[HazardDetection]:
        """Detect specific hazards in the expression."""
        hazards = []

        # Check for recursion
        if re.search(r'\bCALCULATE\s*\(.*\bCALCULATE\s*\(', expression, re.IGNORECASE):
            hazards.append(HazardDetection(
                category=HazardCategory.RECURSION,
                severity=7,
                details="Nested CALCULATE detected",
                patterns_matched=["nested_calculate"],
            ))

        # Check for dynamic security
        if re.search(r'\bUSERPRINCIPALNAME\s*\(|DYNAMICSECURITY', expression):
            hazards.append(HazardDetection(
                category=HazardCategory.DYNAMIC_SECURITY,
                severity=8,
                details="Dynamic security function detected",
                patterns_matched=["userprincipalname", "dynamic_security"],
            ))

        # Check for complexity
        paren_count = expression.count('(')
        if paren_count > 5:
            hazards.append(HazardDetection(
                category=HazardCategory.COMPLEXITY,
                severity=min(5, paren_count // 3),
                details=f"High nesting depth: {paren_count} open parentheses",
                patterns_matched=["high_depth"],
            ))

        return hazards

    def _determine_tier(self, expression: str) -> SafetyTier:
        """Determine the safety tier for the expression."""
        # Check highest tier patterns first
        if any(re.search(pattern, expression, re.IGNORECASE) for pattern in self.TIER_5_PATTERNS):
            return SafetyTier.TIER_5

        if any(re.search(pattern, expression, re.IGNORECASE) for pattern in self.TIER_4_PATTERNS):
            return SafetyTier.TIER_4

        if any(re.search(pattern, expression, re.IGNORECASE) for pattern in self.TIER_3_PATTERNS):
            return SafetyTier.TIER_3

        if any(re.search(pattern, expression, re.IGNORECASE) for pattern in self.TIER_2_PATTERNS):
            return SafetyTier.TIER_2

        if any(re.search(pattern, expression, re.IGNORECASE) for pattern in self.TIER_1_PATTERNS):
            return SafetyTier.TIER_1

        # Default to Tier 1 if no patterns matched
        return SafetyTier.TIER_1
