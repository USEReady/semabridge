"""The project's configured Snowflake target (database / schema / warehouse /
role) must be what every deploy actually uses -- never the .env/default
schema -- and per-run, without touching the shared settings singleton."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from semabridge.auth import credential_builder as cb
from semabridge.core.engine.config import _step1_load_config
from semabridge.core.engine.deployment import snowflake as dep_sf
from semabridge.core.engine.engine import ExecutionEngine
from semabridge.core.settings import SnowflakeConfig, get_settings

PROJECT_TARGET = {
    "type": "snowflake",
    "database": "DB_SANDBOX_X",
    "schema": "SCHEMA_SANDBOX_X",
    "warehouse": "WH_X",
}


def _project_config(**target_extra):
    return {
        "source": {"type": "pbix", "pbix_path": "C:/dir/model_a.pbix"},
        "targets": [{**PROJECT_TARGET, **target_extra}],
    }


def _step1(config_dict):
    engine = ExecutionEngine.__new__(ExecutionEngine)
    engine._record_step = lambda *a, **k: None
    return _step1_load_config(engine, None, "pbix", "snowflake", config_dict=config_dict)


def test_explicit_schema_name_is_honoured_not_replaced_by_env():
    cfg = SnowflakeConfig(account="acc", user="usr", warehouse="wh", database="db", schema_name="SCHEMA_EXPLICIT")
    assert cfg.schema_name == "SCHEMA_EXPLICIT"


def test_step1_applies_the_project_target_to_this_runs_snowflake_config_only():
    before = get_settings().snowflake.schema_name
    config = _step1(_project_config())
    assert config.snowflake.schema_name == "SCHEMA_SANDBOX_X"
    assert config.snowflake.database == "DB_SANDBOX_X"
    assert config.snowflake.warehouse == "WH_X"
    assert get_settings().snowflake.schema_name == before  # singleton untouched


def test_project_without_a_target_schema_keeps_the_previous_default():
    config = _step1({"source": {"type": "pbix"}, "targets": [{"type": "snowflake"}]})
    assert config.snowflake.schema_name == get_settings().snowflake.schema_name


def _account():
    return SimpleNamespace(tag="acct_x", connector_type="SNOWFLAKE", identity_email=None)


def test_account_scoped_config_uses_the_project_target_over_account_bundle(monkeypatch):
    monkeypatch.setattr(cb, "_load_credential_env_map", lambda account, db: {
        "SNOWFLAKE_ACCOUNT": "acc", "SNOWFLAKE_USER": "usr",
        "SNOWFLAKE_DATABASE": "DB_FROM_ACCOUNT", "SNOWFLAKE_WAREHOUSE": "WH_FROM_ACCOUNT",
    })
    base = SnowflakeConfig(account="acc", user="usr")
    built = cb.build_snowflake_config(
        _account(), None, base,
        project_target=cb.project_snowflake_target(SimpleNamespace(target=SimpleNamespace(**PROJECT_TARGET))),
    )
    assert (built.database, built.schema_name, built.warehouse) == ("DB_SANDBOX_X", "SCHEMA_SANDBOX_X", "WH_X")

    # No project target: unchanged precedence (account bundle, then base).
    legacy = cb.build_snowflake_config(_account(), None, base)
    assert legacy.database == "DB_FROM_ACCOUNT"
    assert legacy.schema_name == base.schema_name


def _capture_deploy(monkeypatch, context):
    captured = {}
    stub = SimpleNamespace(_do_snowflake_deploy=lambda ctx, sf_cfg: captured.setdefault("cfg", sf_cfg))
    dep_sf._deploy_to_snowflake(stub, context)
    return captured["cfg"]


def test_deploy_without_identity_uses_the_project_schema(monkeypatch):
    config = _step1(_project_config())
    sf_cfg = _capture_deploy(monkeypatch, SimpleNamespace(config=config))
    assert sf_cfg.schema_name == "SCHEMA_SANDBOX_X"
    assert sf_cfg.database == "DB_SANDBOX_X"


def test_reconciliation_reads_the_view_from_the_project_schema(monkeypatch):
    import semabridge.api.services.project_shared as project_shared
    import semabridge.connectors.snowflake_extractor as extractor_mod
    import semabridge.repository.orm.session_factory as sf_mod
    from semabridge.core import reconciliation

    monkeypatch.setattr(project_shared, "_compat_load_modular_project", lambda pid: {
        "assembled": {"targets": [{**PROJECT_TARGET, "identity_id": "identity-1"}]},
    })
    monkeypatch.setattr(cb, "_load_credential_env_map", lambda account, db: {
        "SNOWFLAKE_ACCOUNT": "acc", "SNOWFLAKE_USER": "usr",
    })

    class _Result:
        def scalars(self):
            return self

        def first(self):
            return _account()

    @contextmanager
    def _get_session():
        yield SimpleNamespace(execute=lambda *a, **k: _Result())

    monkeypatch.setattr(sf_mod.db_manager, "get_session", _get_session)
    seen = {}

    class _Extractor:
        def __init__(self, cfg):
            seen["cfg"] = cfg

        def extract_semantic_view_ddl(self, view_name):
            return "ddl"

    monkeypatch.setattr(extractor_mod, "SnowflakeExtractor", _Extractor)
    assert reconciliation._fetch_live_deployed_ddl("proj-x", "VIEW_X") == "ddl"
    assert seen["cfg"].schema_name == "SCHEMA_SANDBOX_X"
    assert seen["cfg"].database == "DB_SANDBOX_X"


def test_deploy_with_identity_uses_the_project_schema(monkeypatch):
    config = _step1(_project_config(identity_id="identity-1"))
    monkeypatch.setattr(cb, "_load_credential_env_map", lambda account, db: {
        "SNOWFLAKE_ACCOUNT": "acc", "SNOWFLAKE_USER": "usr", "SNOWFLAKE_DATABASE": "DB_FROM_ACCOUNT",
    })

    class _Result:
        def scalars(self):
            return self

        def first(self):
            return _account()

    class _Session:
        def execute(self, *a, **k):
            return _Result()

    @contextmanager
    def _get_session():
        yield _Session()

    import semabridge.repository.orm.session_factory as sf_mod

    monkeypatch.setattr(sf_mod.db_manager, "get_session", _get_session)
    sf_cfg = _capture_deploy(monkeypatch, SimpleNamespace(config=config))
    assert sf_cfg.schema_name == "SCHEMA_SANDBOX_X"
    assert sf_cfg.database == "DB_SANDBOX_X"
    assert sf_cfg.account == "acc"
