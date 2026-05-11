from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs
from semabridge.core.settings import SnowflakeConfig


def _base_config(account: str) -> SnowflakeConfig:
    return SnowflakeConfig(
        account=account,
        user="test_user",
        password="test_password",
        warehouse="test_wh",
        database="TEST_DB",
        schema_name="PUBLIC",
    )


def test_connect_kwargs_accepts_plain_account_id() -> None:
    kwargs = get_snowflake_connect_kwargs(_base_config("WUYAVQU-NG15736"))
    assert kwargs["account"] == "WUYAVQU-NG15736"


def test_connect_kwargs_normalizes_snowflake_domain_account() -> None:
    kwargs = get_snowflake_connect_kwargs(
        _base_config("WUYAVQU-NG15736.snowflakecomputing.com")
    )
    assert kwargs["account"] == "WUYAVQU-NG15736"


def test_connect_kwargs_normalizes_snowflake_url_account() -> None:
    kwargs = get_snowflake_connect_kwargs(
        _base_config("https://WUYAVQU-NG15736.snowflakecomputing.com")
    )
    assert kwargs["account"] == "WUYAVQU-NG15736"

