from __future__ import annotations

from types import SimpleNamespace

from semabridge.core.engine.extraction import snowflake as snowflake_engine


class _SessionContext:
    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self.session

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class _Session:
    def __init__(self, account):
        self.account = account

    def execute(self, statement):
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(first=lambda: self.account)
        )


def test_snowflake_scoped_extraction_uses_isolated_config(monkeypatch):
    account = SimpleNamespace(
        connector_type="SNOWFLAKE",
        id="account-1",
        tag="primary",
        identity_email="service@example.com",
    )
    session = _Session(account)
    base_config = SimpleNamespace(snowflake=SimpleNamespace(account="base"))
    isolated_config = SimpleNamespace(account="isolated")
    context = SimpleNamespace(config=base_config)
    captured = {}

    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "ambient-account")
    monkeypatch.setattr(
        snowflake_engine,
        "db_manager",
        SimpleNamespace(get_session=lambda: _SessionContext(session)),
        raising=False,
    )
    monkeypatch.setattr(
        snowflake_engine,
        "build_snowflake_config",
        lambda account, session, config: isolated_config,
        raising=False,
    )

    def _fake_unscoped(extracted_context, dataset_id, sf_cfg=None):
        # The scoped extractor must hand the isolated config through
        # explicitly rather than mutating extracted_context.config (which
        # may be the shared, process-global Settings singleton when no
        # per-account override is in play).
        captured["context"] = extracted_context
        captured["sf_cfg"] = sf_cfg
        return "source-format"

    result = snowflake_engine._extract_snowflake_scoped(
        SimpleNamespace(_extract_snowflake_unscoped=_fake_unscoped),
        context,
        None,
        "account-1",
    )

    assert result == "source-format"
    # The isolated config was threaded through as an explicit argument...
    assert captured["sf_cfg"] is isolated_config
    # ...and the original context (and its config) were never touched.
    assert captured["context"] is context
    assert context.config is base_config
    assert context.config.snowflake.account == "base"
    # No os.environ mutation occurred — the ambient env var is untouched.
    assert snowflake_engine.os.environ["SNOWFLAKE_ACCOUNT"] == "ambient-account"
