"""
Error Reporter — deployment error tracking and reporting.

Provides:
- ModelDeploymentResult for individual model deployment outcomes
- DeploymentReport for aggregated status across models
- Error categorization and fix suggestions
- Formatted output (text, JSON, HTML)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
import json
from datetime import datetime


# ───────────────────────────────────────────────────────────────────────────
# Enumerations
# ───────────────────────────────────────────────────────────────────────────

class DeploymentStatus(str, Enum):
    """Deployment status for a model."""

    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"
    SKIPPED = "skipped"


class ErrorCategory(str, Enum):
    """Categories of deployment errors."""

    SYNTAX_ERROR = "syntax_error"
    MISSING_TABLE = "missing_table"
    PERMISSION_ERROR = "permission_error"
    VALIDATION_ERROR = "validation_error"
    CONNECTION_ERROR = "connection_error"
    UNKNOWN = "unknown"


# ───────────────────────────────────────────────────────────────────────────
# Result Classes
# ───────────────────────────────────────────────────────────────────────────

@dataclass
class DeploymentError:
    """A single deployment error."""

    category: ErrorCategory
    message: str
    details: Optional[str] = None
    line_number: Optional[int] = None
    suggested_fix: Optional[str] = None


@dataclass
class ModelDeploymentResult:
    """Result of deploying a single model."""

    model_name: str
    status: DeploymentStatus
    errors: List[DeploymentError] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    deployed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None

    def add_error(
        self,
        category: ErrorCategory,
        message: str,
        details: Optional[str] = None,
    ) -> None:
        """Add an error to this result."""
        error = DeploymentError(
            category=category,
            message=message,
            details=details,
            suggested_fix=suggest_fix(category, message),
        )
        self.errors.append(error)

    def add_warning(self, warning: str) -> None:
        """Add a warning."""
        self.warnings.append(warning)

    def is_success(self) -> bool:
        """Check if deployment succeeded."""
        return self.status == DeploymentStatus.SUCCESS


@dataclass
class DeploymentReport:
    """Aggregated deployment report."""

    report_time: datetime = field(default_factory=datetime.utcnow)
    results: List[ModelDeploymentResult] = field(default_factory=list)
    overall_status: DeploymentStatus = DeploymentStatus.SUCCESS

    def add_result(self, result: ModelDeploymentResult) -> None:
        """Add a model deployment result."""
        self.results.append(result)
        self._update_overall_status()

    def _update_overall_status(self) -> None:
        """Update the overall status based on results."""
        statuses = [r.status for r in self.results]

        if any(s == DeploymentStatus.FAILED for s in statuses):
            self.overall_status = DeploymentStatus.FAILED
        elif any(s == DeploymentStatus.PARTIAL_SUCCESS for s in statuses):
            self.overall_status = DeploymentStatus.PARTIAL_SUCCESS
        else:
            self.overall_status = DeploymentStatus.SUCCESS

    def get_summary(self) -> Dict[str, int]:
        """Get summary of deployment outcomes."""
        return {
            "total_models": len(self.results),
            "successful": len([r for r in self.results if r.is_success()]),
            "partial_success": len([r for r in self.results if r.status == DeploymentStatus.PARTIAL_SUCCESS]),
            "failed": len([r for r in self.results if r.status == DeploymentStatus.FAILED]),
            "total_errors": sum(len(r.errors) for r in self.results),
        }

    def format_text(self) -> str:
        """Format report as human-readable text."""
        lines = [
            f"Deployment Report — {self.report_time.isoformat()}",
            f"Overall Status: {self.overall_status.value.upper()}",
            "",
        ]

        summary = self.get_summary()
        lines.append(f"Summary: {summary['successful']}/{summary['total_models']} models deployed successfully")
        lines.append("")

        for result in self.results:
            lines.append(f"Model: {result.model_name}")
            lines.append(f"  Status: {result.status.value}")

            if result.errors:
                lines.append(f"  Errors ({len(result.errors)}):")
                for error in result.errors:
                    lines.append(f"    - [{error.category.value}] {error.message}")
                    if error.suggested_fix:
                        lines.append(f"      Fix: {error.suggested_fix}")

            if result.warnings:
                lines.append(f"  Warnings ({len(result.warnings)}):")
                for warning in result.warnings:
                    lines.append(f"    - {warning}")

            lines.append("")

        return "\n".join(lines)

    def format_json(self) -> str:
        """Format report as JSON."""
        data = {
            "report_time": self.report_time.isoformat(),
            "overall_status": self.overall_status.value,
            "summary": self.get_summary(),
            "results": [
                {
                    "model_name": r.model_name,
                    "status": r.status.value,
                    "errors": [
                        {
                            "category": e.category.value,
                            "message": e.message,
                            "suggested_fix": e.suggested_fix,
                        }
                        for e in r.errors
                    ],
                    "warnings": r.warnings,
                }
                for r in self.results
            ],
        }

        return json.dumps(data, indent=2)


# ───────────────────────────────────────────────────────────────────────────
# Fix Suggestions
# ───────────────────────────────────────────────────────────────────────────

def suggest_fix(category: ErrorCategory, message: str) -> Optional[str]:
    """
    Suggest a fix for a deployment error.

    Args:
        category: Error category
        message: Error message

    Returns:
        Suggested fix or None
    """
    if category == ErrorCategory.MISSING_TABLE:
        return "Verify the source table exists and is accessible. Check table name spelling and schema."

    elif category == ErrorCategory.PERMISSION_ERROR:
        return "Grant necessary permissions (USAGE, CREATE) to the Snowflake role."

    elif category == ErrorCategory.SYNTAX_ERROR:
        return "Check SQL syntax for typos. Review the generated DDL for issues."

    elif category == ErrorCategory.VALIDATION_ERROR:
        return "Review model validation rules. Ensure all required fields are populated."

    elif category == ErrorCategory.CONNECTION_ERROR:
        return "Check Snowflake connection credentials and network connectivity."

    return None


def suggest_fixes_for_errors(errors: List[DeploymentError]) -> List[str]:
    """
    Generate fix suggestions for a list of errors.

    Args:
        errors: List of deployment errors

    Returns:
        List of suggested fixes
    """
    suggestions = []

    for error in errors:
        fix = suggest_fix(error.category, error.message)
        if fix and fix not in suggestions:
            suggestions.append(fix)

    return suggestions
