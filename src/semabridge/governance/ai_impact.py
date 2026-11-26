"""Forwarding alias module for ai_impact_service."""
from semabridge.governance.ai_impact_service import (
    AIImpactReport,
    QuestionImpactResult,
    run_ai_impact_check,
    clear_ai_impact_cache,
)

__all__ = [
    "AIImpactReport",
    "QuestionImpactResult",
    "run_ai_impact_check",
    "clear_ai_impact_cache",
]
