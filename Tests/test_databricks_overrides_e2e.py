"""Databricks integration fixes, batch 1: saved names end to end.

Covers the override marker (``mapping_overrides.origin``) from save to run:
project creation (wizard YAML), reload, rename through the API, reset to
automatic, the database migration (existing and fresh databases), the dry-run
display of saved names, target dispatch, the Snowflake-only action refusal,
and that Snowflake / Fabric / no-target projects save exactly as before.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
import sqlalchemy as sa
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

import semabridge.api.services.mapping_service as ms
import semabridge.api.services.project_shared as ps
from semabridge.api.services import project_config_store as store


@pytest.fixture
def projects_dir(tmp_path, monkeypatch):
    pdir = tmp_path / "Config" / "projects"
    pdir.mkdir(parents=True)
    configs, mappings = {}, {}
    monkeypatch.setattr(ps, "_compat_projects_dirs", lambda: [pdir])
    monkeypatch.setattr(ps, "_compat_projects_dir", lambda: pdir)
    monkeypatch.setattr(ps, "_compat_project_yaml_path", lambda pid: pdir / f"{pid}.yaml")
    monkeypatch.setattr(ps, "_compat_project_yaml_paths", lambda pid: [pdir / f"{pid}.yaml"])
    for mod in (ps, ms):
        monkeypatch.setattr(mod, "_compat_project_configs", configs)
        monkeypatch.setattr(mod, "_compat_mappings", mappings)
    monkeypatch.setattr(ps, "_compat_deleted_project_ids", set())
    monkeypatch.setattr(ps, "_compat_save_store", lambda *a, **k: True)
    monkeypatch.setattr(ms, "_compat_save_store", lambda *a, **k: True)
    yield pdir


def _pid(name: str) -> str:
    return f"proj-test-{name}-{time.time_ns()}"


DATABRICKS_YAML = {
    "project_name": "DB",
    "source": {"type": "pbix"},
    "targets": [{"type": "databricks", "catalog": "workspace", "schema": "sb_tests"}],
    # What the wizard now saves for Databricks: an explicit edit (marked) and a
    # name saved earlier without the marker (kept, unmarked).
    "mappings_overrides": [
        {"source_path": "datasets.Store.columns.District ID", "target_name": "district_code", "origin": "user"},
        {"source_path": "metrics.Total Sales", "target_name": "total_sales"},
    ],
}
SNOWFLAKE_YAML = {
    "project_name": "SF",
    "source": {"type": "pbix"},
    "targets": [{"type": "snowflake"}],
    "mappings_overrides": [
        {"source_path": "datasets.Store.columns.District ID", "target_name": "DISTRICT_CODE"},
        {"source_path": "metrics.Total Sales", "target_name": "TOTAL_SALES"},
    ],
}


def _overrides(pid):
    return {o["source_path"]: o for o in store.list_overrides(pid)}


# --- store: marker saved, reloaded, never lost ---------------------------------------------

def test_create_and_reload_keep_the_marker_only_where_set(projects_dir):
    pid = _pid("create")
    store.save_project_config(pid, yaml.safe_dump(DATABRICKS_YAML), reason="create")
    rows = _overrides(pid)
    assert rows["datasets.Store.columns.District ID"]["origin"] == "user"
    assert "origin" not in rows["metrics.Total Sales"]                  # unmarked stays unmarked
    reloaded = yaml.safe_load(store.get_project_config_text(pid, use_cache=False))
    by_path = {o["source_path"]: o for o in reloaded["mappings_overrides"]}
    assert by_path["datasets.Store.columns.District ID"]["origin"] == "user"
    assert "origin" not in by_path["metrics.Total Sales"]


def test_a_row_without_the_key_never_clears_a_marker(projects_dir):
    pid = _pid("keep")
    store.save_project_config(pid, yaml.safe_dump(DATABRICKS_YAML), reason="create")
    plain = {**DATABRICKS_YAML, "mappings_overrides": [
        {"source_path": "datasets.Store.columns.District ID", "target_name": "district_code"},
        {"source_path": "metrics.Total Sales", "target_name": "total_sales"}]}
    store.save_project_config(pid, yaml.safe_dump(plain), reason="save")
    assert _overrides(pid)["datasets.Store.columns.District ID"]["origin"] == "user"
    store.upsert_override(pid, source_path="datasets.Store.columns.District ID", target_name="district_code2")
    assert _overrides(pid)["datasets.Store.columns.District ID"]["origin"] == "user"
    store.upsert_override(pid, source_path="metrics.Total Sales", target_name="sales_total", origin="user")
    assert _overrides(pid)["metrics.Total Sales"]["origin"] == "user"


def test_snowflake_project_saves_and_renders_exactly_as_before(projects_dir):
    pid = _pid("sf")
    text = yaml.safe_dump(SNOWFLAKE_YAML, sort_keys=False)
    store.save_project_config(pid, text, reason="create")
    assert all("origin" not in o for o in store.list_overrides(pid))
    rendered = store.get_project_config_text(pid, use_cache=False)
    assert "origin" not in rendered
    assert yaml.safe_load(rendered)["mappings_overrides"] == SNOWFLAKE_YAML["mappings_overrides"]
    assert store.render_config_text(text, store.list_overrides(pid)) == yaml.safe_dump(
        yaml.safe_load(text), sort_keys=False, allow_unicode=False)


def test_reset_one_field_to_automatic(projects_dir):
    pid = _pid("reset1")
    store.save_project_config(pid, yaml.safe_dump(DATABRICKS_YAML), reason="create")
    assert store.delete_override(pid, "datasets.Store.columns.District ID") == 1
    assert list(_overrides(pid)) == ["metrics.Total Sales"]
    assert store.delete_override(pid, "datasets.Store.columns.District ID") == 0
    exported = yaml.safe_load((projects_dir / f"{pid}.yaml").read_text())
    assert [o["source_path"] for o in exported["mappings_overrides"]] == ["metrics.Total Sales"]


def test_legacy_store_edit_flag_becomes_a_marker_for_databricks_only(projects_dir):
    for target, expect in (("databricks", "user"), ("snowflake", None)):
        pid = _pid(f"legacy-{target}")
        ps._compat_mappings[f"{pid}-m1"] = {
            "project_id": pid, "source_path": "metrics.Total Sales", "target_name": "Sales Total",
            "is_user_edited": True, "updated_at": "2026-01-01T00:00:00",
        }
        cfg = {"project_name": "L", "targets": [{"type": target}]}
        store.save_project_config(pid, yaml.safe_dump(cfg), reason="import", merge_store_edits=True)
        assert _overrides(pid)["metrics.Total Sales"].get("origin") == expect


# --- database: migration on existing and fresh databases -------------------------------------

OLD_TABLE = """CREATE TABLE mapping_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id VARCHAR(255) NOT NULL,
    model_key VARCHAR(512) NOT NULL DEFAULT '', model_ref TEXT, source_path VARCHAR(1024) NOT NULL,
    target_name VARCHAR(1024) NOT NULL, entity_kind VARCHAR(32), source_name VARCHAR(1024),
    position INTEGER, updated_at DATETIME NOT NULL, updated_by VARCHAR(255))"""


def _cols(engine):
    return {c["name"] for c in sa.inspect(engine).get_columns("mapping_overrides")}


def test_existing_local_database_gets_the_column_and_keeps_rows(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql(OLD_TABLE)
        conn.exec_driver_sql("INSERT INTO mapping_overrides (project_id, source_path, target_name, updated_at) "
                             "VALUES ('p', 'metrics.A', 'A', '2026-01-01')")
    store._ensure_origin_column(engine)
    store._ensure_origin_column(engine)                              # idempotent
    assert "origin" in _cols(engine)
    with engine.begin() as conn:
        assert conn.exec_driver_sql("SELECT target_name, origin FROM mapping_overrides").fetchall() == [("A", None)]


def _run_migration(engine, fn):
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mod = importlib.import_module("semabridge.migrations.schema_versions.sb0006_mapping_override_origin")
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(mod, fn)()
    return mod


def test_migration_sb0006_upgrades_existing_tables_and_is_idempotent(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql(OLD_TABLE)
        conn.exec_driver_sql("INSERT INTO mapping_overrides (project_id, source_path, target_name, updated_at) "
                             "VALUES ('p', 'metrics.A', 'A', '2026-01-01')")
    mod = _run_migration(engine, "upgrade")
    assert mod.down_revision == "sb0005_multi_process_state"
    assert "origin" in _cols(engine)
    _run_migration(engine, "upgrade")                                  # already there: no-op
    with engine.begin() as conn:
        assert conn.exec_driver_sql("SELECT origin FROM mapping_overrides").fetchall() == [(None,)]
    _run_migration(engine, "downgrade")
    assert "origin" not in _cols(engine)


def test_migration_sb0006_on_a_fresh_database(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    _run_migration(engine, "upgrade")                                  # no table yet: nothing to do
    assert not sa.inspect(engine).has_table("mapping_overrides")
    from semabridge.repository.orm.base import Base
    from semabridge.repository.orm.project_models import MappingOverride

    Base.metadata.create_all(bind=engine, tables=[MappingOverride.__table__])   # fresh install (ORM)
    assert "origin" in _cols(engine)
    _run_migration(engine, "upgrade")                                  # then the chain: no-op
    assert "origin" in _cols(engine)


def test_migration_chain_head_is_sb0006():
    """The app's own runner configuration (repository/alembic_runner.py) sees
    sb0006 as the single head, right after sb0005."""
    from alembic.script import ScriptDirectory

    from semabridge.repository.alembic_runner import _alembic_config

    script = ScriptDirectory.from_config(_alembic_config(None))
    assert len(script.get_heads()) == 1                                 # one linear chain
    assert script.get_revision("sb0006_mapping_override_origin").down_revision == "sb0005_multi_process_state"
    assert script.get_revision("sb0007_databricks_probe_evidence").down_revision == "sb0006_mapping_override_origin"


# --- API: rename, reset, deploy (wizard) ----------------------------------------------------

@pytest.fixture
def client(projects_dir, monkeypatch):
    import semabridge.api.controllers.mappings_controller as mc

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setattr(mc, "_assert_project_access", lambda *a, **k: None)
    monkeypatch.setattr(ms, "_compat_ensure_loaded", lambda: None)
    app = FastAPI()
    app.include_router(mc.router)
    return TestClient(app)


def _project(pid, cfg):
    store.save_project_config(pid, yaml.safe_dump(cfg), reason="create")


def test_api_rename_marks_databricks_edits_only(client):
    for target, cfg, expect in (("databricks", DATABRICKS_YAML, "user"), ("snowflake", SNOWFLAKE_YAML, None)):
        pid = _pid(f"api-{target}")
        _project(pid, cfg)
        mid = f"{pid}-qty"
        ps._compat_mappings[mid] = {"id": mid, "project_id": pid, "source_path": "datasets.Sales.columns.Qty",
                                   "source_name": "Qty", "entity_kind": "column"}
        r = client.put(f"/api/projects/{pid}/mappings/{mid}", json={"target_name": "Units Sold"})
        assert r.status_code == 200, r.text
        row = _overrides(pid)["datasets.Sales.columns.Qty"]
        assert row.get("origin") == expect
        assert row["target_name"] == ("units_sold" if target == "databricks" else "UNITS_SOLD")


def test_api_reset_to_automatic(client):
    pid = _pid("api-reset")
    _project(pid, DATABRICKS_YAML)
    ps._compat_mappings["m1"] = {"id": "m1", "project_id": pid, "source_path": "datasets.Store.columns.District ID",
                                 "status": "manual", "is_user_edited": True, "origin": "user"}
    r = client.post(f"/api/projects/{pid}/mapping-overrides/reset",
                    json={"source_path": "datasets.Store.columns.District ID"})
    assert r.status_code == 200 and r.json()["removed"] == 1
    assert list(_overrides(pid)) == ["metrics.Total Sales"]          # other saved names stay
    assert ps._compat_mappings["m1"]["status"] == "auto" and "origin" not in ps._compat_mappings["m1"]
    assert client.post(f"/api/projects/{pid}/mapping-overrides/reset", json={"source_path": ""}).status_code == 400


def _stub_deploy(monkeypatch, created_target):
    import semabridge.api.services.project_domain_service as pds

    async def create(payload):
        created_target.append(payload["targets"][0]["type"])
        pid = _pid("created")
        cfg = {"project_name": "W", "targets": payload["targets"]}
        store.save_project_config(pid, yaml.safe_dump(cfg), reason="create")
        return {"id": pid}

    async def run_now(*a, **k):
        return {"run_id": "r1", "status": "running"}

    monkeypatch.setattr(pds, "create_project_compat", create)
    monkeypatch.setattr(pds, "run_project_now_compat", run_now)


def test_wizard_deploy_saves_only_genuine_databricks_edits(client, monkeypatch):
    created: list = []
    _stub_deploy(monkeypatch, created)
    rows = [
        {"id": "a", "source_path": "datasets.Sales.columns.Qty", "target_name": "units", "user_edited": True},
        {"id": "b", "source_path": "datasets.Sales.columns.Amount", "target_name": "amount", "user_edited": False},
        {"id": "c", "source_path": "metrics.Total Sales", "target_name": "total_sales"},
    ]
    r = client.post("/api/projects/preview/deploy", json={"field_mappings": rows, "target_type": "databricks"})
    assert r.status_code == 200, r.text
    assert created == ["databricks"]                                   # B5: not a Snowflake preview project
    pid = r.json()["project_id"]
    saved = _overrides(pid)
    assert list(saved) == ["datasets.Sales.columns.Qty"] and saved["datasets.Sales.columns.Qty"]["origin"] == "user"


def test_wizard_deploy_for_snowflake_saves_every_row_as_before(client, monkeypatch):
    created: list = []
    _stub_deploy(monkeypatch, created)
    rows = [{"id": "a", "source_path": "datasets.Sales.columns.Qty", "target_name": "UNITS"},
            {"id": "b", "source_path": "datasets.Sales.columns.Amount", "target_name": "AMOUNT"}]
    r = client.post("/api/projects/preview/deploy", json={"field_mappings": rows})
    assert r.status_code == 200, r.text
    assert created == ["snowflake"]
    saved = _overrides(r.json()["project_id"])
    assert set(saved) == {"datasets.Sales.columns.Qty", "datasets.Sales.columns.Amount"}
    assert all("origin" not in o for o in saved.values())


# --- dry run display of saved names (B1 / B2) and provenance -----------------------------

def test_dry_run_shows_saved_databricks_names_as_the_run_reads_them():
    from semabridge.api.controllers.mappings_controller import _saved_names_for_dry_run

    cfg = {"mappings_overrides": [
        {"source_path": "datasets.T.columns.Date", "target_name": "col_date"},          # Snowflake would drop it
        {"source_path": "metrics.Total Sales", "target_name": "TOTAL_SALES"},           # old capitals
        {"source_path": "datasets.T.columns.Qty", "target_name": "units", "origin": "user"},
    ]}
    db = _saved_names_for_dry_run(cfg, {}, [], "databricks")
    assert db["datasets.T.columns.Date"]["target_name"] == "col_date"
    assert db["metrics.Total Sales"]["target_name"] == "total_sales"
    assert db["metrics.Total Sales"]["override_origin"] == "unmarked"
    assert db["datasets.T.columns.Qty"]["override_origin"] == "user" and db["datasets.T.columns.Qty"]["preserve_name"]
    sf = _saved_names_for_dry_run(cfg, {}, [], "snowflake")
    assert "datasets.T.columns.Date" not in sf or sf["datasets.T.columns.Date"]["target_name"] != "col_date"
    assert all("override_origin" not in v for v in sf.values())


# --- target dispatch ------------------------------------------------------------------------

def test_type_less_databricks_target_is_refused_not_run_as_snowflake():
    from semabridge.api.services.sync_execution_service import _build_sync_jobs, resolve_target_type
    from semabridge.domain.exceptions import ValidationError

    assert resolve_target_type({"type": "databricks"}) == "databricks"
    assert resolve_target_type({}) == "snowflake"                          # legacy default kept
    assert resolve_target_type({"database": "DB", "schema": "S"}) == "snowflake"
    with pytest.raises(ValidationError):
        resolve_target_type({"catalog": "workspace", "schema": "sb"})
    with pytest.raises(ValidationError):
        _build_sync_jobs({"source": {"type": "pbix", "pbix_path": "x.pbix"},
                          "targets": [{"catalog": "workspace", "schema": "sb"}]})


def test_wizard_dry_run_refuses_type_less_databricks_target():
    from fastapi import HTTPException

    from semabridge.api.controllers.mappings_controller import _wizard_target_type

    assert _wizard_target_type({"type": "databricks"}) == "databricks"
    with pytest.raises(HTTPException) as exc:
        _wizard_target_type({"catalog": "workspace"})
    assert exc.value.status_code == 400


def test_project_list_keeps_a_databricks_target(projects_dir):
    pid = _pid("list")
    _project(pid, DATABRICKS_YAML)
    assert ps._compat_stored_target_type(pid) == "databricks"
    assert ps._compat_stored_target_type(_pid("none")) == ""


# --- Snowflake-only actions refused -----------------------------------------------------------

def test_add_missing_column_is_refused_for_databricks():
    from fastapi import HTTPException

    from semabridge.api.sync_router import _refuse_non_snowflake_target

    _refuse_non_snowflake_target(yaml.safe_dump(SNOWFLAKE_YAML))             # allowed
    _refuse_non_snowflake_target("project_name: x\n")                       # no target: Snowflake (as before)
    with pytest.raises(HTTPException) as exc:
        _refuse_non_snowflake_target(yaml.safe_dump(DATABRICKS_YAML))
    assert exc.value.status_code == 400


def test_planned_view_names_are_reported_as_planned_for_databricks_only():
    from types import SimpleNamespace

    from semabridge.api.services.sync_execution_service import _planned_view_names

    ctx = SimpleNamespace(databricks_view_plan={"views": [{"name": "sales_metrics"}, {"name": "store_metrics"}]})
    assert _planned_view_names(ctx, "databricks") == {
        "planned_view_names": ["sales_metrics", "store_metrics"], "view_status": "planned"}
    assert _planned_view_names(ctx, "snowflake") == {}
    assert _planned_view_names(SimpleNamespace(databricks_view_plan=None), "databricks") == {}


def _chain(engine, rev):
    from alembic import command

    from semabridge.repository.alembic_runner import _alembic_config

    with engine.begin() as conn:
        command.upgrade(_alembic_config(conn), rev)


def test_full_migration_chain_fresh_install(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'chain_fresh.db'}")
    _chain(engine, "head")
    assert "origin" in _cols(engine)


def test_full_migration_chain_existing_database_keeps_rows(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'chain_old.db'}")
    _chain(engine, "sb0005_multi_process_state")
    with engine.begin() as conn:                                      # the table as databases made before sb0006 have it
        conn.exec_driver_sql("DROP TABLE mapping_overrides")
        conn.exec_driver_sql(OLD_TABLE)
    assert "origin" not in _cols(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO mapping_overrides (project_id, model_key, source_path, target_name, updated_at) "
                             "VALUES ('p', '', 'metrics.A', 'A', '2026-01-01')")
    _chain(engine, "head")
    with engine.begin() as conn:
        assert conn.exec_driver_sql("SELECT target_name, origin FROM mapping_overrides").fetchall() == [("A", None)]


def test_full_migration_chain_creates_probe_evidence_table(tmp_path):
    """3.5: sb0007 adds databricks_probe_evidence on a fresh database and on an
    existing one at the previous head (sb0006), idempotently."""
    fresh = sa.create_engine(f"sqlite:///{tmp_path / 'p_fresh.db'}")
    _chain(fresh, "head")
    assert sa.inspect(fresh).has_table("databricks_probe_evidence")

    old = sa.create_engine(f"sqlite:///{tmp_path / 'p_old.db'}")
    _chain(old, "sb0006_mapping_override_origin")
    with old.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS databricks_probe_evidence")   # as databases made before sb0007
    _chain(old, "head")
    cols = {c["name"] for c in sa.inspect(old).get_columns("databricks_probe_evidence")}
    assert {"run_id", "workspace_host", "warehouse_id", "outcomes_json", "started_at", "completed_at"} <= cols


def test_full_migration_chain_creates_deploy_tables(tmp_path):
    """3.8: sb0008 adds the deploy record, names, journal and lease tables on a fresh
    database and on one at the previous head (sb0007), idempotently."""
    from alembic.script import ScriptDirectory

    from semabridge.repository.alembic_runner import _alembic_config

    script = ScriptDirectory.from_config(_alembic_config(None))
    assert script.get_revision("sb0008_databricks_deploy").down_revision == "sb0007_databricks_probe_evidence"
    tables = ("databricks_deployments", "databricks_deployed_objects", "databricks_deployed_names",
              "databricks_deploy_journal", "databricks_deploy_leases")
    fresh = sa.create_engine(f"sqlite:///{tmp_path / 'd_fresh.db'}")
    _chain(fresh, "head")
    assert all(sa.inspect(fresh).has_table(t) for t in tables)
    old = sa.create_engine(f"sqlite:///{tmp_path / 'd_old.db'}")
    _chain(old, "sb0007_databricks_probe_evidence")
    with old.begin() as conn:
        for t in tables:
            conn.exec_driver_sql(f"DROP TABLE IF EXISTS {t}")
    _chain(old, "head")
    cols = {c["name"] for c in sa.inspect(old).get_columns("databricks_deploy_journal")}
    assert {"run_id", "seq", "destination", "state", "definition_hash", "previous_hash", "statement_id"} <= cols


def test_full_migration_chain_creates_deploy_intents_table(tmp_path):
    """3.8b: sb0009 adds databricks_deploy_intents after sb0008; one linear chain."""
    from alembic.script import ScriptDirectory

    from semabridge.repository.alembic_runner import _alembic_config

    script = ScriptDirectory.from_config(_alembic_config(None))
    assert len(script.get_heads()) == 1
    assert script.get_revision("sb0009_databricks_deploy_intents").down_revision == "sb0008_databricks_deploy"
    eng = sa.create_engine(f"sqlite:///{tmp_path / 'i.db'}")
    _chain(eng, "head")
    cols = {c["name"] for c in sa.inspect(eng).get_columns("databricks_deploy_intents")}
    assert {"intent_id", "run_id", "fingerprint", "status", "expires_at", "used_at"} <= cols


def test_sb0010_repairs_a_journal_created_with_the_old_column(tmp_path):
    """A dev build could create databricks_deploy_journal with body_sha256; sb0010
    renames it to definition_hash. Idempotent on correct databases."""
    from alembic.script import ScriptDirectory

    from semabridge.repository.alembic_runner import _alembic_config

    script = ScriptDirectory.from_config(_alembic_config(None))
    assert len(script.get_heads()) == 1
    assert script.get_revision("sb0010_dbx_journal_repair").down_revision == "sb0009_databricks_deploy_intents"
    eng = sa.create_engine(f"sqlite:///{tmp_path / 'j.db'}")
    _chain(eng, "sb0009_databricks_deploy_intents")
    with eng.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE databricks_deploy_journal RENAME COLUMN definition_hash TO body_sha256")
    _chain(eng, "head")
    cols = {c["name"] for c in sa.inspect(eng).get_columns("databricks_deploy_journal")}
    assert "definition_hash" in cols and "body_sha256" not in cols
    fresh = sa.create_engine(f"sqlite:///{tmp_path / 'f.db'}")
    _chain(fresh, "head")                                   # correct database: no-op
    assert "definition_hash" in {c["name"] for c in sa.inspect(fresh).get_columns("databricks_deploy_journal")}


def test_every_revision_id_fits_postgres_alembic_version():
    """alembic_version.version_num is VARCHAR(32) on Postgres: a longer revision id
    fails the upgrade (the whole step is rolled back)."""
    from alembic.script import ScriptDirectory

    from semabridge.repository.alembic_runner import _alembic_config

    script = ScriptDirectory.from_config(_alembic_config(None))
    too_long = [r.revision for r in script.walk_revisions() if len(r.revision) > 32]
    assert not too_long, too_long


def test_plan_revision_columns_hold_millisecond_revisions(tmp_path):
    """Plan revisions are millisecond timestamps (> 2**31): the deploy tables must
    store them as BIGINT (Postgres refused INTEGER). sb0011 widens older tables."""
    from semabridge.repository.orm import project_models as m

    for model in (m.DatabricksDeployment, m.DatabricksDeployIntent):
        assert isinstance(model.__table__.c.plan_revision.type, sa.BigInteger), model.__tablename__
    eng = sa.create_engine(f"sqlite:///{tmp_path / 'r.db'}")
    _chain(eng, "head")
    for table in ("databricks_deployments", "databricks_deploy_intents"):
        col = next(c for c in sa.inspect(eng).get_columns(table) if c["name"] == "plan_revision")
        assert isinstance(col["type"], sa.BigInteger), table
