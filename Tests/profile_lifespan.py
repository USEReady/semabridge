import time
import asyncio

async def profile_lifespan_steps():
    t_start = time.perf_counter()
    def log(msg):
        print(f"[{time.perf_counter() - t_start:.2f}s] {msg}")

    log("Step 1: Get engine")
    from semabridge.repository.orm.session_factory import get_engine
    eng = get_engine()
    log("Engine obtained")

    log("Step 2: Base.metadata.create_all (checkfirst=True)")
    from semabridge.repository.orm.base import Base
    import semabridge.repository.orm.models
    import semabridge.repository.orm.cache_models
    t0 = time.perf_counter()
    Base.metadata.create_all(bind=eng, checkfirst=True)
    log(f"create_all done ({time.perf_counter() - t0:.2f}s)")

    log("Step 3: _apply_schema_compatibility_fixes")
    t0 = time.perf_counter()
    from semabridge.api.bootstrap.db_migrations import _apply_schema_compatibility_fixes
    _apply_schema_compatibility_fixes()
    log(f"_apply_schema_compatibility_fixes done ({time.perf_counter() - t0:.2f}s)")

    log("Step 4: Alembic upgrade_database")
    t0 = time.perf_counter()
    from semabridge.repository.alembic_runner import upgrade_database
    report = await asyncio.to_thread(upgrade_database, eng)
    log(f"Alembic done ({time.perf_counter() - t0:.2f}s): {report}")

    log("Step 5: _migrate_credentials_table")
    t0 = time.perf_counter()
    from semabridge.api.bootstrap.db_migrations import _migrate_credentials_table
    _migrate_credentials_table()
    log(f"_migrate_credentials_table done ({time.perf_counter() - t0:.2f}s)")

    log("Step 6: _apply_rls_policies")
    t0 = time.perf_counter()
    from semabridge.api.bootstrap.db_migrations import _apply_rls_policies
    _apply_rls_policies()
    log(f"_apply_rls_policies done ({time.perf_counter() - t0:.2f}s)")

    log("Step 7: CredentialManager inject_all")
    t0 = time.perf_counter()
    from semabridge.repository.credential_manager import CredentialManager
    CredentialManager().inject_all()
    log(f"CredentialManager done ({time.perf_counter() - t0:.2f}s)")

    log("Step 8: scheduler_leadership")
    t0 = time.perf_counter()
    from semabridge.core.run_coordination import scheduler_leadership
    await asyncio.to_thread(scheduler_leadership.is_leader)
    log(f"scheduler_leadership done ({time.perf_counter() - t0:.2f}s)")

    log("Step 9: sweep_stale_runs")
    t0 = time.perf_counter()
    from semabridge.core.run_coordination import sweep_stale_runs
    await asyncio.to_thread(sweep_stale_runs)
    log(f"sweep_stale_runs done ({time.perf_counter() - t0:.2f}s)")

    log("Step 10: _compat_ensure_loaded")
    t0 = time.perf_counter()
    from semabridge.api.services.project_api_service import _compat_ensure_loaded
    _compat_ensure_loaded()
    log(f"_compat_ensure_loaded done ({time.perf_counter() - t0:.2f}s)")

    log("Step 11: restore_drift_schedules")
    t0 = time.perf_counter()
    from semabridge.governance.drift_service import restore_drift_schedules
    await asyncio.to_thread(restore_drift_schedules)
    log(f"restore_drift_schedules done ({time.perf_counter() - t0:.2f}s)")

    log(f"TOTAL LIFESPAN TIME: {time.perf_counter() - t_start:.2f}s")

if __name__ == "__main__":
    asyncio.run(profile_lifespan_steps())
