"""Aggregated project-domain exports.

The actual implementations now live in smaller modules so project work can be
split across multiple files without changing the public import surface.
"""
from semabridge.api.services.version_control_impl import version_backend

from semabridge.api.services.project_shared import (
    _compat_clear_project_schedule,
    _compat_default_project_yaml,
    _compat_ensure_loaded,
    _compat_load_repo_yaml_text,
    _compat_now_iso,
    _compat_project_configs,
    _compat_project_runs,
    _compat_project_schedules,
    _compat_projects,
    _compat_repo_yaml_path,
    _compat_save_store,
    _discovery_cache,
    _last_snapshot_hash,
    _normalize_yaml_windows_path_fields,
    _resolve_models_path,
    db_manager,
    logger,
    scheduler_service,
    settings,
    version_control_service,
)
from semabridge.api.services.project_composite_impl import (
    get_all_composite_links,
    get_impact_analysis,
    register_composite_report,
)
from semabridge.api.services.project_pbix_impl import browse_pbix_files, import_pbix
from semabridge.api.services.project_projects_impl import (
    _extract_snapshot_connectors,
    _snapshot_graph_payload,
    create_project_compat,
    delete_project_compat,
    get_project_compat,
    get_project_config_compat,
    graph_snapshot_compat,
    graph_snapshots_compat,
    list_projects_compat,
    patch_project_compat,
    save_project_config_compat,
    compare_graph_snapshots_compat,
    list_project_discovery_compat,
)
from semabridge.api.services.project_runs_impl import (
    _create_project_run,
    _execute_project_run,
    _perform_project_run,
    _run_project_background,
    auto_map_compat,
    capture_manual_snapshots_compat,
    compare_project_snapshots_compat,
    compare_project_model_snapshot_compat,
    create_folder_compat,
    delete_folder_compat,
    delete_mappings_compat,
    delete_project_schedule_compat,
    get_jobs_config_compat,
    get_project_runs_compat,
    get_project_schedule_compat,
    list_folders_compat,
    list_job_runs_compat,
    list_job_schedules_compat,
    list_project_snapshots_compat,
    list_snapshot_groups_compat,
    list_mappings_compat,
    move_project_to_folder_compat,
    rename_folder_compat,
    restore_project_version_compat,
    run_project_now_compat,
    save_project_schedule_compat,
    trigger_job_compat,
    update_jobs_config_compat,
    update_mapping_compat,
    delete_project_snapshots_compat,
    tag_snapshot_compat,
    preview_restore_compat,
    get_audit_logs_compat,
    get_project_lineage_compat,
    toggle_snapshot_pin_compat,
    get_model_history_compat,
    get_project_stats_compat,
    get_snapshot_content_compat,
    get_snapshot_report_compat,
    manual_deploy_compat,
    get_run_conflicts_compat,
)


async def apply_project_retention_policy(project_id: str, days: int = 30):
    return await version_backend.apply_retention_policy(project_id, days_to_keep=days)


async def get_project_storage_stats(project_id: str):
    return await version_backend.get_storage_stats(project_id)

from semabridge.api.services.project_versioning_impl import (
    compare_model_versions,
    compare_versions,
    delete_model_versions,
    get_version_snapshot,
    list_model_versions,
    rollback_model_version,
    rollback_version,
)
