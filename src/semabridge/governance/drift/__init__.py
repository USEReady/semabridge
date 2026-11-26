"""Drift Monitoring Platform Adapter Package.

Provides a unified, platform-independent architecture for schema and metric
drift detection between deployed BI semantic models and live cloud warehouses.
"""

from __future__ import annotations

from semabridge.governance.drift.models import (
    DriftCategory,
    DriftFinding,
    ObjectDef,
    ObjectKind,
    ViewDefinition,
)
from semabridge.governance.drift.baseline_store import (
    get_active_baseline,
    is_object_not_deployed,
    list_baseline_history,
    save_deploy_baseline,
)
from semabridge.governance.drift.normalize import (
    has_expression,
    is_placeholder_expression,
    normalize_description,
    normalize_expression,
)
from semabridge.governance.drift.comparison import compare_views
from semabridge.governance.drift.webhook import validate_webhook_url

__all__ = [
    "DriftCategory",
    "ObjectKind",
    "ObjectDef",
    "ViewDefinition",
    "DriftFinding",
    "save_deploy_baseline",
    "get_active_baseline",
    "list_baseline_history",
    "is_object_not_deployed",
    "normalize_expression",
    "normalize_description",
    "has_expression",
    "is_placeholder_expression",
    "compare_views",
    "validate_webhook_url",
]
