"""Which SemaBridge features exist for a project's target (TargetRules.features),
and a guard for target-specific API entry points.

The guard refuses an action only when the project's target is KNOWN and the
feature is not implemented for it (e.g. Snowflake-only live SQL on a Databricks
project). Projects without a stored target, and every Snowflake project, behave
exactly as before."""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def stored_target_type(project_id: Optional[str]) -> Optional[str]:
    """The project's stored target type (lowercase) or None when unknown."""
    pid = str(project_id or "").strip()
    if not pid:
        return None
    try:
        from semabridge.api.services.project_shared import _compat_stored_target_type

        value = str(_compat_stored_target_type(pid) or "").strip().lower()
        return value or None
    except Exception as exc:
        logger.debug("Target type of project %s unavailable: %s", pid, exc)
        return None


def target_features_for(project_id: Optional[str]) -> Dict[str, Any]:
    """{target, features}. Unknown or unsupported target: every feature off."""
    from semabridge.target_rules import TargetFeatures, UnsupportedTargetError, get_target_rules

    target = stored_target_type(project_id)
    features = TargetFeatures()
    if target:
        try:
            features = get_target_rules(target).features
        except UnsupportedTargetError:
            pass
    return {"target": target, "features": asdict(features)}


def require_target_feature(project_id: Optional[str], feature: str) -> None:
    """HTTP 409 when the project's (known) target lacks ``feature``."""
    from fastapi import HTTPException

    from semabridge.target_rules import UnsupportedTargetError, get_target_rules

    target = stored_target_type(project_id)
    if not target:
        return
    try:
        rules = get_target_rules(target)
    except UnsupportedTargetError:
        return
    if not getattr(rules.features, feature, False):
        label = feature.replace("_", " ")
        raise HTTPException(status_code=409, detail={
            "code": "feature_not_available_for_target",
            "message": f"{label} is not available for {rules.target} projects yet",
            "target": rules.target, "feature": feature,
        })
