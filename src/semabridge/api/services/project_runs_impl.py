"""Backward-compatible shim — all functions have moved to focused service files.

Import from the specific service file for new code.
"""
from semabridge.api.services.snapshot_service import *  # noqa: F401,F403
from semabridge.api.services.run_service import *  # noqa: F401,F403
from semabridge.api.services.folder_service import *  # noqa: F401,F403
from semabridge.api.services.schedule_service import *  # noqa: F401,F403
from semabridge.api.services.mapping_service import *  # noqa: F401,F403
from semabridge.api.services.version_service import *  # noqa: F401,F403

# Private functions are excluded from `import *` — re-export them explicitly
# so that project_domain_service.py and tests can still access them by name.
from semabridge.api.services.run_service import (  # noqa: F401
    _create_project_run,
    _perform_project_run,
    _execute_project_run,
    _run_project_background,
)
from semabridge.api.services.mapping_service import (  # noqa: F401
    _compat_scope_model_for_dry_run,
    _compat_preferred_snapshot_id_from_sync_result,
    _compat_build_project_entity_mappings,
    _compat_latest_identifier_diagnostics,
    _compat_apply_identifier_diagnostics_to_mappings,
    _compat_is_blocking_mapping,
)
from semabridge.api.services.snapshot_service import (  # noqa: F401
    _compat_capture_snapshots_for_run,
    _compat_latest_sml_state,
    _compat_selected_intermediate_format,
)
# These live in project_shared but tests monkeypatch/access them via project_runs_impl.
from semabridge.api.services.project_shared import (  # noqa: F401
    _compat_ensure_loaded,
    _compat_save_store,
    _compat_project_snapshots,
    _compat_snapshot_groups,
    _compat_project_runs,
    _compat_projects,
    _compat_project_configs,
    _compat_project_schedules,
    _compat_mappings,
    _compat_run_snapshots,
    _compat_folders,
)
