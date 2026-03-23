from __future__ import annotations

import pytest
from pydantic import SecretStr

from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.settings import SnowflakeConfig


@pytest.fixture()
def emitter() -> SnowflakeEmitter:
    config = SnowflakeConfig(
        account="acc",
        user="usr",
        password=SecretStr("pwd"),
        warehouse="wh",
        database="db",
        schema_name="PUBLIC",
        role="rl",
    )
    return SnowflakeEmitter(config, behavior=ConnectorBehavior())


def test_generate_ctas_sql_quotes_and_aliases(emitter: SnowflakeEmitter) -> None:
    sql = emitter.generate_ctas_sql(
        "REP_SFDC_CUSTOMER_PROJECT_C",
        [
            {"name": "PROJECT_SIZE", "type": "INTEGER"},
            {"name": "START_DATE", "type": "DATE"},
            {"name": "EVENT_TS", "type": "TIMESTAMP"},
            {"name": "DESCRIPTION", "type": "STRING"},
        ],
        "PUBLIC",
    )

    assert 'CREATE OR REPLACE TABLE PUBLIC."REP_SFDC_CUSTOMER_PROJECT_C__FIXED" AS' in sql
    assert 'FROM PUBLIC."REP_SFDC_CUSTOMER_PROJECT_C";' in sql
    assert 'TRY_TO_NUMBER("PROJECT_SIZE") AS "PROJECT_SIZE"' in sql
    assert 'TRY_TO_DATE("START_DATE") AS "START_DATE"' in sql
    assert 'TRY_TO_TIMESTAMP("EVENT_TS") AS "EVENT_TS"' in sql
    assert '"DESCRIPTION" AS "DESCRIPTION"' in sql
    assert "TRY_TO_NUMBER(\"PROJECT_SIZE\" AS" not in sql


def test_generate_ctas_sql_rejects_empty_columns(emitter: SnowflakeEmitter) -> None:
    with pytest.raises(ValueError, match="No columns inferred"):
        emitter.generate_ctas_sql("T1", [], "PUBLIC")


def test_generate_ctas_sql_rejects_all_varchar(emitter: SnowflakeEmitter) -> None:
    with pytest.raises(ValueError, match="all VARCHAR"):
        emitter.generate_ctas_sql(
            "T1",
            [
                {"name": "A", "type": "VARCHAR"},
                {"name": "B", "type": "STRING"},
            ],
            "PUBLIC",
        )
