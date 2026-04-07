"""Compatibility exports for the legacy project API surface.

New route/controller code should use the smaller project service modules
directly. This module exists to keep older imports stable while the split
is completed safely.
"""

from semabridge.api.legacy_main import (
    _compat_clear_project_schedule,
    _compat_ensure_loaded,
    _compat_project_schedules,
    _execute_project_run,
    scheduler_service,
)
from semabridge.api.services.composite_service import (
    get_all_composite_links,
    get_impact_analysis,
    register_composite_report,
)
from semabridge.api.services.folders_service import (
    create_folder_compat,
    delete_folder_compat,
    list_folders_compat,
    move_project_to_folder_compat,
    rename_folder_compat,
)
from semabridge.api.services.graph_service import (
    compare_graph_snapshots_compat,
    graph_snapshot_compat,
    graph_snapshots_compat,
)
from semabridge.api.services.jobs_service import (
    delete_project_schedule_compat,
    get_jobs_config_compat,
    get_project_schedule_compat,
    list_job_runs_compat,
    list_job_schedules_compat,
    save_project_schedule_compat,
    trigger_job_compat,
    update_jobs_config_compat,
)
from semabridge.api.services.mappings_service import (
    auto_map_compat,
    delete_mappings_compat,
    list_mappings_compat,
    update_mapping_compat,
)
from semabridge.api.services.pbix_service import (
    browse_pbix_files,
    import_pbix,
    upload_pbix_temp,
    upload_project_pbix,
)
from semabridge.api.services.project_runs_service import (
    get_project_runs_compat,
    run_project_now_compat,
)
from semabridge.api.services.projects_service import (
    create_project_compat,
    delete_project_compat,
    get_project_compat,
    get_project_config_compat,
    list_projects_compat,
    patch_project_compat,
    save_project_config_compat,
)
from semabridge.api.services.versioning_service import (
    compare_model_versions,
    delete_model_versions,
    get_version_snapshot,
    list_model_versions,
    rollback_model_version,
)
