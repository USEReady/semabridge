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


def databricks_planner_on() -> bool:
    """``behavior.databricks.use_planner_path`` from the global behavior.yaml.

    Off (the default) means the existing Databricks publisher deploys, with its
    own naming rule; on means the planner deploys with naming v2 (snake_case).
    Screens and the dry-run gate must show the names that will deploy."""
    try:
        from semabridge.core.behavior import ConnectorBehavior
        from semabridge.core.config_loader import get_project_file_path

        path = get_project_file_path("behavior.yaml")
        if path.exists():
            return bool(ConnectorBehavior.from_yaml(path).databricks.use_planner_path)
    except Exception:  # noqa: BLE001 -- unreadable config: the default (off)
        pass
    return False


class _PublisherDatabricksNaming(TargetNaming):
    """The existing DatabricksPublisher's rule (planner path off)."""

    platform_label = "Databricks"
    normalizes_user_names = True
    ignores_legacy_auto_names = False

    def target_name(self, name: str, source_path: Optional[str] = None, entity_kind: Optional[str] = None) -> str:
        from semabridge.utils.identifiers import databricks_target_name

        return databricks_target_name(name, source_path, entity_kind)

    def accepts_target_name(self, name: str, entity_kind: Optional[str] = None) -> bool:
        return self.target_name(name, entity_kind=entity_kind) == str(name)


_PUBLISHER_DATABRICKS_NAMING = _PublisherDatabricksNaming()


def naming_for(target_type: Optional[str]) -> Optional[TargetNaming]:
    target = str(target_type or "").strip().lower()
    if not target:
        return None
    if target in ("databricks", "databricks_sql", "dbx", "databricks_metric_view") and not databricks_planner_on():
        return _PUBLISHER_DATABRICKS_NAMING
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
