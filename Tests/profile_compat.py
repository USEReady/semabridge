import time

t_start = time.perf_counter()
def log(msg):
    print(f"[{time.perf_counter() - t_start:.2f}s] {msg}")

from semabridge.api.services import project_shared

log("Profiling _compat_ensure_loaded_locked sub-steps:")

t0 = time.perf_counter()
project_shared._compat_load_store()
log(f"_compat_load_store done ({time.perf_counter() - t0:.2f}s)")

t0 = time.perf_counter()
project_shared._compat_sync_store_to_db()
log(f"_compat_sync_store_to_db (1) done ({time.perf_counter() - t0:.2f}s)")

t0 = time.perf_counter()
project_shared._compat_deleted_ids()
log(f"_compat_deleted_ids done ({time.perf_counter() - t0:.2f}s)")

t0 = time.perf_counter()
project_shared._compat_bootstrap_projects_from_modular_configs()
log(f"_compat_bootstrap_projects_from_modular_configs done ({time.perf_counter() - t0:.2f}s)")

t0 = time.perf_counter()
project_shared._compat_bootstrap_projects_from_orm()
log(f"_compat_bootstrap_projects_from_orm done ({time.perf_counter() - t0:.2f}s)")

t0 = time.perf_counter()
project_shared._compat_bootstrap_project_from_repo_yaml()
log(f"_compat_bootstrap_project_from_repo_yaml done ({time.perf_counter() - t0:.2f}s)")

t0 = time.perf_counter()
project_shared._compat_refresh_project_statuses_from_runs()
log(f"_compat_refresh_project_statuses_from_runs done ({time.perf_counter() - t0:.2f}s)")

t0 = time.perf_counter()
project_shared._compat_sync_store_to_db()
log(f"_compat_sync_store_to_db (2) done ({time.perf_counter() - t0:.2f}s)")

t0 = time.perf_counter()
project_shared._compat_refresh_projects_from_db()
log(f"_compat_refresh_projects_from_db done ({time.perf_counter() - t0:.2f}s)")

log(f"TOTAL TIME: {time.perf_counter() - t_start:.2f}s")
