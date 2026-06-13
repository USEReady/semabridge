import snowflake.connector
import logging
from semabridge.core.settings import get_settings
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from types import SimpleNamespace

logging.basicConfig(level=logging.DEBUG)

settings = get_settings()
conn = snowflake.connector.connect(
    user=settings.snowflake.user,
    password=settings.snowflake.password.get_secret_value() if hasattr(settings.snowflake.password, 'get_secret_value') else settings.snowflake.password,
    account=settings.snowflake.account,
    warehouse=settings.snowflake.warehouse,
    database=settings.snowflake.database,
    schema=settings.snowflake.schema_name,
)
cursor = conn.cursor()
cursor.execute(f'CREATE OR REPLACE TABLE {settings.snowflake.database}.{settings.snowflake.schema_name}."TEST_SALESFACT" ("ID" INT)')
cursor.execute(f'CREATE OR REPLACE TABLE {settings.snowflake.database}.{settings.snowflake.schema_name}."TEST_DATE" ("DATE" DATE)')

date_ds = SimpleNamespace(
    unique_name="TEST_DATE",
    source_table="TEST_DATE",
    is_date_table=True,
    columns=[SimpleNamespace(unique_name="Date", source_column="Date", expression=None)]
)
fact_ds = SimpleNamespace(
    unique_name="TEST_SALESFACT",
    source_table="TEST_SALESFACT",
    is_date_table=False,
    columns=[SimpleNamespace(unique_name="ID", source_column="ID", expression=None)]
)
model = SimpleNamespace(
    unique_name="test_model",
    label="test_model",
    datasets=[date_ds, fact_ds],
    relationships=[],
    metrics=[],
    dimensions=[]
)

emitter = SnowflakeEmitter(config=settings.snowflake)
emitter._live_schema_metadata = {
    "TEST_DATE": {"DATE"},
    "TEST_SALESFACT": {"ID"}
}

old_execute = cursor.execute
def new_execute(command, *args, **kwargs):
    print("EXECUTING DDL:\n", command)
    return old_execute(command, *args, **kwargs)
cursor.execute = new_execute

try:
    emitter._create_enriched_view_for_table(cursor, "TEST_DATE", True, "Date", model)
    emitter._create_enriched_view_for_table(cursor, "TEST_SALESFACT", False, "Date", model)
finally:
    cursor.close()
    conn.close()
