"""Looking up a target platform's naming rule (callers outside target_rules).

Callers used to hard-code ``if target == "snowflake": snowflake_target_name(...)``
and give every other platform the generic rule. They now ask the platform's
rulebook. Platforms without a naming rule (e.g. Fabric) get None and keep
their previous behaviour.
"""

from __future__ import annotations

from typing import Optional

from semabridge.target_rules.base import TargetNaming
from semabridge.target_rules.registry import UnsupportedTargetError, get_target_rules


def naming_for(target_type: Optional[str]) -> Optional[TargetNaming]:
    target = str(target_type or "").strip().lower()
    if not target:
        return None
    try:
        return get_target_rules(target).naming
    except UnsupportedTargetError:
        return None


def override_deploy_name(name: Optional[str], source_path: Optional[str], target_type: Optional[str]) -> Optional[str]:
    """The name a saved mapping override deploys under, or None when the
    override must be ignored (it is only an old automatic name, e.g. a saved
    COL_DATE for 'Date' on Snowflake, so the current rule applies).
    Used by the sync (core/engine/config.py) and the dry-run gate
    (api/services/dry_run_job_service.py) so both read overrides the same way."""
    text = str(name or "").strip()
    naming = naming_for(target_type)
    if naming is None or not naming.normalizes_user_names or not text:
        return text
    if naming.ignores_legacy_auto_names and naming.is_legacy_auto_name(source_path, text):
        return None
    return naming.target_name(text, source_path)


def normalize_user_target_name(
    name: Optional[str], target_type: Optional[str], source_path: Optional[str] = None, entity_kind: Optional[str] = None
) -> str:
    """A user-chosen (or saved) target name, normalised to the platform's
    rule when it has one that normalises user names; otherwise as given."""
    raw = str(name or "").strip()
    if not raw:
        return raw
    naming = naming_for(target_type)
    if naming is None or not naming.normalizes_user_names:
        return raw
    return naming.target_name(raw, source_path, entity_kind)
