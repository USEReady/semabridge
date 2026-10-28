"""Tests for table collision blockers, resolutions (a & b), and deploy gating.

Tests:
1. A synthetic model whose table name exists with different columns -> dry run shows the blocker
   ("<TABLE> already exists with different columns.") and deploy is not attempted.
2. Resolving via (a) -> binds to the chosen compatible table.
3. Resolving via (b) ->
   - If schema doesn't exist: clear error message (SemaBridge never creates schemas).
   - If schema exists: placeholders are created in the other schema and the view deploys.
Never reuse an incompatible table, and never modify existing tables.
"""

import pytest
from unittest.mock import MagicMock
from semabridge.connectors.schema_manager import SnowflakeSchemaManager, clear_schema_cache
from semabridge.connectors.table_binding_resolver import (
    LiveSchemaMetadata,
    check_table_name_collision_blocker,
    resolve_dataset_to_physical_table,
)
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.connectors.sql_guard import set_allowed_model_objects, clear_allowed_model_objects, clear_allowed_absent_tables
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.exceptions import ConnectorError
from semabridge.core.settings import SnowflakeConfig
from semabridge.sml.models import (
    SMLColumn,
    SMLDataset,
    SMLModel,
    SMLMetric,
    AggregationType,
    DataType,
)
from semabridge.utils.identifiers import IdentifierSanitizer


class MockSnowflakeConnection:
    """Mock Snowflake connection tracking SQL queries and providing mock results."""

    def __init__(self, schemas=None, tables_by_schema=None, columns_by_table=None, columns_by_schema=None):
        self.schemas = set(schemas or ["TEST_SCHEMA", "OTHER_DATA_SCHEMA"])
        self.tables_by_schema = tables_by_schema or {
            "TEST_SCHEMA": ["MY_TABLE", "COMPATIBLE_CANDIDATE"],
            "OTHER_DATA_SCHEMA": [],
        }
        self.columns_by_schema = columns_by_schema or {}
        self.columns_by_table = columns_by_table or {
            "MY_TABLE": {"DIFF_COL_X", "DIFF_COL_Y"},
            "COMPATIBLE_CANDIDATE": {"COLA", "COLB", "COLC"},
        }
        self.executed_statements = []

    def get_connection(self):
        return (self, False)

    def close(self):
        pass

    def cursor(self):
        return MockSnowflakeCursor(self)

    def _execute_sql(self, cursor, sql, params=None, context="", expected_failure=False):
        self.executed_statements.append((sql, params, context))
        sql_upper = sql.upper().strip()

        # Check schema existence
        if "SHOW SCHEMAS LIKE" in sql_upper:
            import re
            m = re.search(r"SHOW SCHEMAS LIKE '([^']+)'", sql_upper)
            if m:
                sch = m.group(1).upper()
                cursor._rows = [(None, sch)] if sch in self.schemas else []
            else:
                cursor._rows = []
            return cursor

        # Show tables in schema
        if "SHOW TABLES IN SCHEMA" in sql_upper:
            import re
            m = re.search(r'SHOW TABLES IN SCHEMA [^.]+\."?([A-Za-z0-9_]+)"?', sql_upper)
            sch = m.group(1).upper() if m else "TEST_SCHEMA"
            tbls = self.tables_by_schema.get(sch, [])
            cursor._rows = [(None, t) for t in tbls]
            return cursor

        # Show views
        if "SHOW VIEWS IN SCHEMA" in sql_upper:
            cursor._rows = []
            return cursor

        # Fetch schema metadata
        if "FROM INFORMATION_SCHEMA.COLUMNS" in sql_upper:
            rows = []
            cols_map = self.columns_by_table
            if params and str(params[0]).upper() in self.columns_by_schema:
                cols_map = self.columns_by_schema[str(params[0]).upper()]
            filter_tables = [str(p).upper() for p in params[1:]] if params and len(params) > 1 else None
            for tbl, cols in cols_map.items():
                if filter_tables is not None and tbl.upper() not in filter_tables:
                    continue
                for c in cols:
                    rows.append((tbl, c))
            cursor._rows = rows
            return cursor

        if sql_upper.startswith("CREATE TABLE IF NOT EXISTS"):
            # Mock table creation
            import re
            m = re.search(r'CREATE TABLE IF NOT EXISTS\s+([A-Za-z0-9_"]+)\."?([A-Za-z0-9_]+)"?', sql)
            if m:
                sch = m.group(1).strip('"').upper()
                tbl = m.group(2).strip('"').upper()
                self.tables_by_schema.setdefault(sch, []).append(tbl)
                col_matches = re.findall(r'(\b[A-Za-z0-9_]+\b)\s+(?:INT|VARCHAR|DECIMAL|DATE|FLOAT|BOOLEAN|TEXT|NUMBER)', sql, re.IGNORECASE)
                cols_set = {c.upper() for c in col_matches if c.upper() not in ("CREATE", "TABLE", "IF", "NOT", "EXISTS")}
                if not cols_set:
                    cols_set = {"COLA", "COLB", "COLC"}
                if sch in self.columns_by_schema:
                    self.columns_by_schema[sch][tbl] = cols_set
                self.columns_by_table[tbl] = cols_set
            cursor._rows = []
            return cursor

        if "SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW" in sql_upper:
            cursor._rows = [("name: test",)]
            return cursor

        if "SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML" in sql_upper:
            cursor._rows = [("success",)]
            return cursor

        cursor._rows = []
        return cursor


def _make_emitter(config, behavior, conn):
    emitter = SnowflakeEmitter(config=config, behavior=behavior)
    emitter.connection_manager = conn
    emitter.schema_manager.connection_manager = conn
    return emitter


class MockSnowflakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows = []

    def execute(self, sql, params=None):
        return self.conn._execute_sql(self, sql, params)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


def _build_synthetic_model():
    return SMLModel(
        unique_name="SyntheticModel",
        label="SyntheticModel",
        datasets=[
            SMLDataset(
                unique_name="My_Table",
                columns=[
                    SMLColumn(unique_name="ColA", data_type=DataType.INTEGER),
                    SMLColumn(unique_name="ColB", data_type=DataType.STRING),
                    SMLColumn(unique_name="ColC", data_type=DataType.DECIMAL),
                ],
            )
        ],
        metrics=[
            SMLMetric(
                unique_name="Total_ColA",
                dataset="My_Table",
                expression="My_Table.ColA",
                aggregation=AggregationType.SUM,
            )
        ],
    )


def test_dry_run_shows_blocker_and_sync_blocked():
    """Test 1: A synthetic model whose table name exists with different columns ->
    dry run shows the blocker ("<TABLE> already exists with different columns.")
    and deploy isn't attempted.
    """
    clear_schema_cache()
    clear_allowed_model_objects()
    clear_allowed_absent_tables()

    conn = MockSnowflakeConnection(
        schemas={"TEST_DB_SCHEMA"},
        tables_by_schema={"TEST_DB_SCHEMA": ["MY_TABLE", "COMPATIBLE_CANDIDATE"]},
        columns_by_table={
            "MY_TABLE": {"DIFF_X", "DIFF_Y"},  # 0% compatibility with ColA, ColB, ColC
            "COMPATIBLE_CANDIDATE": {"COLA", "COLB", "COLC"},  # 100% compatible
        },
    )

    config = SnowflakeConfig(
        account="test.account",
        user="test_user",
        password="test_password",
        warehouse="TEST_WH",
        database="TEST_DB",
        schema_name="TEST_DB_SCHEMA",
    )
    behavior = ConnectorBehavior()
    emitter = _make_emitter(config, behavior, conn)

    model = _build_synthetic_model()

    # Dry run preview of table bindings
    preview = emitter.preview_table_bindings(model)
    assert isinstance(preview, dict)
    blockers = preview.get("blockers") or []
    assert len(blockers) >= 1

    blocker = blockers[0]
    assert blocker["table_name"] == "MY_TABLE"
    assert "already exists with different columns" in blocker["message"]
    assert "MY_TABLE already exists with different columns." == blocker["headline"]

    # Blocker offers two resolutions
    resolutions = blocker.get("resolutions") or []
    assert len(resolutions) == 2
    res_a = next(r for r in resolutions if r["type"] == "map_table")
    res_b = next(r for r in resolutions if r["type"] == "use_schema")

    # (a) Choose from compatible candidates
    assert "COMPATIBLE_CANDIDATE" in res_a["candidates"]
    # Incompatible table is not in candidate list
    assert "MY_TABLE" not in res_a["candidates"]

    # (b) Use different schema
    assert "Use a different schema" in res_b["description"]

    # Attempting deploy while blocker is open MUST be prevented / not attempted
    success = emitter.deploy(model)
    assert success is False
    assert "Deployment blocked: cannot sync while blockers are open" in emitter.last_deployment_error
    assert "MY_TABLE already exists with different columns" in emitter.last_deployment_error

    # Ensure schema_manager._ensure_source_tables_exist directly raises ConnectorError
    with pytest.raises(ConnectorError) as exc_info:
        emitter.schema_manager._ensure_source_tables_exist(conn.cursor(), model)
    assert "Deployment blocked: cannot sync while blockers are open" in str(exc_info.value)
    # Ensure existing table was never overwritten or modified
    for sql, _, _ in conn.executed_statements:
        assert not sql.upper().startswith("CREATE OR REPLACE TABLE")
        assert not sql.upper().startswith("ALTER TABLE")
        assert not sql.upper().startswith("DROP TABLE")


def test_resolving_via_a_binds_to_chosen_table():
    """Test 2: Resolving via (a) -> binds to chosen compatible table,
    persists in the project's own bindings (per project/model), never in global behavior,
    and deploy proceeds.
    """
    clear_schema_cache()
    clear_allowed_model_objects()
    clear_allowed_absent_tables()

    conn = MockSnowflakeConnection(
        schemas={"TEST_DB_SCHEMA"},
        tables_by_schema={"TEST_DB_SCHEMA": ["MY_TABLE", "COMPATIBLE_CANDIDATE"]},
        columns_by_table={
            "MY_TABLE": {"DIFF_X", "DIFF_Y"},
            "COMPATIBLE_CANDIDATE": {"COLA", "COLB", "COLC"},
        },
    )

    # Resolution (a): Save binding strictly in the project's own table bindings
    from semabridge.repository.model_repository import ModelRepository
    repo = ModelRepository()
    repo.save_table_bindings(
        "PROJECT_A",
        "syntheticmodel",
        [{"dataset_key": "MY_TABLE", "dataset": "My_Table", "table": "COMPATIBLE_CANDIDATE", "matched_by": "manual"}],
    )

    behavior = ConnectorBehavior()
    # Ensure behavior.snowflake.source_table_mapping is NEVER modified or written to
    assert not behavior.snowflake.source_table_mapping

    config = SnowflakeConfig(
        account="test.account",
        user="test_user",
        password="test_password",
        warehouse="TEST_WH",
        database="TEST_DB",
        schema_name="TEST_DB_SCHEMA",
    )
    emitter = _make_emitter(config, behavior, conn)
    emitter.binding_scope = ("PROJECT_A", "syntheticmodel")

    model = _build_synthetic_model()

    # Dry run shows no blockers when resolved via (a) in PROJECT_A
    preview = emitter.preview_table_bindings(model)
    assert not preview.get("blockers")

    table_entries = preview.get("tables") or []
    bound_entry = next(e for e in table_entries if e["dataset"] == "My_Table")
    assert bound_entry["action"] == "bind"
    assert bound_entry["table"] == "COMPATIBLE_CANDIDATE"

    # DDL generation binds to COMPATIBLE_CANDIDATE
    ddls = emitter.semantic_view_builder.generate_ddls(model)
    full_ddl = "\n".join(ddls)
    assert "COMPATIBLE_CANDIDATE" in full_ddl
    # Incompatible table is never reused
    assert '"TEST_DB_SCHEMA"."MY_TABLE"' not in full_ddl
    # Global behavior was never touched
    assert not behavior.snowflake.source_table_mapping


def test_resolution_a_project_isolation():
    """Test 2b: A mapping chosen in project A doesn't affect project B."""
    clear_schema_cache()
    clear_allowed_model_objects()
    clear_allowed_absent_tables()

    conn = MockSnowflakeConnection(
        schemas={"TEST_DB_SCHEMA"},
        tables_by_schema={"TEST_DB_SCHEMA": ["MY_TABLE", "COMPATIBLE_CANDIDATE"]},
        columns_by_table={
            "MY_TABLE": {"DIFF_X", "DIFF_Y"},
            "COMPATIBLE_CANDIDATE": {"COLA", "COLB", "COLC"},
        },
    )

    from semabridge.repository.model_repository import ModelRepository
    repo = ModelRepository()
    # Save binding for project A
    repo.save_table_bindings(
        "PROJECT_A",
        "syntheticmodel",
        [{"dataset_key": "MY_TABLE", "dataset": "My_Table", "table": "COMPATIBLE_CANDIDATE", "matched_by": "manual"}],
    )

    behavior = ConnectorBehavior()
    config = SnowflakeConfig(
        account="test.account",
        user="test_user",
        password="test_password",
        warehouse="TEST_WH",
        database="TEST_DB",
        schema_name="TEST_DB_SCHEMA",
    )
    model = _build_synthetic_model()

    # Project A dry run: uses project A's binding scope, no blocker
    emitter_a = _make_emitter(config, behavior, conn)
    emitter_a.binding_scope = ("PROJECT_A", "syntheticmodel")
    preview_a = emitter_a.preview_table_bindings(model)
    assert not preview_a.get("blockers")
    bound_entry_a = next(e for e in preview_a.get("tables", []) if e["dataset"] == "My_Table")
    assert bound_entry_a["table"] == "COMPATIBLE_CANDIDATE"

    # Project B dry run: separate project_id, no saved bindings
    emitter_b = _make_emitter(config, behavior, conn)
    emitter_b.binding_scope = ("PROJECT_B", "syntheticmodel")
    preview_b = emitter_b.preview_table_bindings(model)

    # Project B is NOT affected by Project A's mapping: shows the blocker
    assert len(preview_b.get("blockers", [])) >= 1
    assert preview_b["blockers"][0]["headline"] == "MY_TABLE already exists with different columns."

    # Global behavior was never touched
    assert not behavior.snowflake.source_table_mapping


def test_resolution_b_per_project_source_data_schema_isolation():
    """Test 3c: Project A uses schema X, project B stays on its own schema."""
    from types import SimpleNamespace
    from semabridge.auth.credential_builder import (
        apply_project_snowflake_target,
        project_snowflake_target,
    )

    # Global SnowflakeConfig with default source-data schema (e.g. from env var)
    global_default_schema = "GLOBAL_DEFAULT_SCHEMA"
    base_sf_config = SnowflakeConfig(
        account="test.account",
        user="test_user",
        password="test_password",
        warehouse="TEST_WH",
        database="TEST_DB",
        schema_name="VIEW_SCHEMA",
        source_data_schema=global_default_schema,
    )

    # Project A config sets target source_data_schema to SCHEMA_X
    proj_a_config = SimpleNamespace(
        target={
            "type": "snowflake",
            "database": "TEST_DB",
            "schema": "VIEW_SCHEMA",
            "source_data_schema": "SCHEMA_X",
        },
        options={},
    )
    target_a = project_snowflake_target(proj_a_config)
    sf_cfg_a = apply_project_snowflake_target(base_sf_config, target_a)

    # Project B config sets NO source_data_schema
    proj_b_config = SimpleNamespace(
        target={
            "type": "snowflake",
            "database": "TEST_DB",
            "schema": "VIEW_SCHEMA",
        },
        options={},
    )
    target_b = project_snowflake_target(proj_b_config)
    sf_cfg_b = apply_project_snowflake_target(base_sf_config, target_b)

    # Project A uses SCHEMA_X
    assert sf_cfg_a.source_data_schema == "SCHEMA_X"
    # Project B stays on its own schema (falls back to global default)
    assert sf_cfg_b.source_data_schema == "GLOBAL_DEFAULT_SCHEMA"
    # Global base_sf_config was never mutated
    assert base_sf_config.source_data_schema == "GLOBAL_DEFAULT_SCHEMA"


def test_resolving_via_b_nonexistent_schema_shows_clear_message():
    """Test 3a: Resolving via (b) with non-existent schema ->
    SemaBridge never creates schemas; shows a clear error message.
    """
    clear_schema_cache()
    clear_allowed_model_objects()
    clear_allowed_absent_tables()

    conn = MockSnowflakeConnection(
        schemas={"TEST_DB_SCHEMA"},  # NON_EXISTENT_SCHEMA is absent
        tables_by_schema={"TEST_DB_SCHEMA": ["MY_TABLE"]},
        columns_by_table={"MY_TABLE": {"DIFF_X", "DIFF_Y"}},
    )

    config = SnowflakeConfig(
        account="test.account",
        user="test_user",
        password="test_password",
        warehouse="TEST_WH",
        database="TEST_DB",
        schema_name="TEST_DB_SCHEMA",
        source_data_schema="NON_EXISTENT_SCHEMA",
    )
    behavior = ConnectorBehavior()
    emitter = _make_emitter(config, behavior, conn)

    model = _build_synthetic_model()

    # Fails with clear message that schema doesn't exist and SemaBridge never creates schemas
    with pytest.raises(ConnectorError) as exc_info:
        emitter.preview_table_bindings(model)

    err = str(exc_info.value)
    assert "Source-data schema 'NON_EXISTENT_SCHEMA' does not exist" in err
    assert "SemaBridge never creates schemas" in err


def test_resolving_via_b_existing_schema_creates_placeholders_and_deploys():
    """Test 3b: Resolving via (b) with existing schema ->
    placeholders are created in the other schema and the view deploys.
    """
    clear_schema_cache()
    clear_allowed_model_objects()
    clear_allowed_absent_tables()

    conn = MockSnowflakeConnection(
        schemas={"TEST_DB_SCHEMA", "DATA_SCHEMA"},
        tables_by_schema={
            "TEST_DB_SCHEMA": ["MY_TABLE"],  # colliding table in view schema
            "DATA_SCHEMA": [],                # other schema has no colliding table
        },
        columns_by_schema={
            "TEST_DB_SCHEMA": {"MY_TABLE": {"DIFF_X", "DIFF_Y"}},
            "DATA_SCHEMA": {},
        },
    )

    config = SnowflakeConfig(
        account="test.account",
        user="test_user",
        password="test_password",
        warehouse="TEST_WH",
        database="TEST_DB",
        schema_name="TEST_DB_SCHEMA",
        source_data_schema="DATA_SCHEMA",
    )
    behavior = ConnectorBehavior()
    emitter = _make_emitter(config, behavior, conn)

    model = _build_synthetic_model()

    # Dry run preview plans placeholder creation in DATA_SCHEMA without blockers
    preview = emitter.preview_table_bindings(model)
    assert not preview.get("blockers")

    table_entries = preview.get("tables") or []
    entry = next(e for e in table_entries if e["dataset"] == "My_Table")
    assert entry["action"] == "create"
    assert entry["table"] == "MY_TABLE"

    # Deploy creates placeholder table in DATA_SCHEMA and deploys semantic view in TEST_DB_SCHEMA
    set_allowed_model_objects({"SYNTHETICMODEL_SEMANTIC"})
    emitter.deploy(model)

    # Verify placeholder table was created in DATA_SCHEMA
    created_tables = [
        sql for sql, _, _ in conn.executed_statements
        if "CREATE TABLE IF NOT EXISTS" in sql.upper()
    ]
    assert len(created_tables) == 1
    assert "DATA_SCHEMA" in created_tables[0]
    assert "MY_TABLE" in created_tables[0]

    # Verify semantic view was created in TEST_DB_SCHEMA referencing DATA_SCHEMA.MY_TABLE
    created_views = [
        sql for sql, _, _ in conn.executed_statements
        if "CREATE OR REPLACE SEMANTIC VIEW" in sql.upper()
    ]
    assert len(created_views) == 1
    view_sql = created_views[0]
    assert '"TEST_DB"."TEST_DB_SCHEMA"."SYNTHETICMODEL_SEMANTIC"' in view_sql
    assert '"TEST_DB"."DATA_SCHEMA"."MY_TABLE"' in view_sql

    clear_allowed_model_objects()
    clear_allowed_absent_tables()


def test_bidirectional_compatibility_tiny_table_blocked():
    """A tiny table in Snowflake (e.g. 1 column out of 10) cannot bind even though
    100% of its columns exist in the model, because compat < 0.5.
    Conversely, schema evolution (e.g. 3 of 4 columns, compat=0.75 >= 0.5 and live_compat=1.0 >= 0.8)
    is accepted without collision blocker.
    """
    # 1. Tiny table: live table has 1 column, model has 10 columns (compat = 0.1 < 0.5)
    model_cols_large = [f"COL_{i}" for i in range(10)]
    live_meta_tiny = {"MY_DATASET": {"COL_0"}}
    blocker = check_table_name_collision_blocker("MY_DATASET", model_cols_large, live_meta_tiny)
    assert blocker is not None
    assert blocker["type"] == "table_column_mismatch"

    # 2. Schema evolution: live table has 3 columns, model has 4 (compat = 0.75 >= 0.5, live_compat = 1.0 >= 0.8)
    model_cols_evolved = ["COL_A", "COL_B", "COL_C", "COL_D"]
    live_meta_evolved = {"MY_DATASET": {"COL_A", "COL_B", "COL_C"}}
    blocker_evolved = check_table_name_collision_blocker("MY_DATASET", model_cols_evolved, live_meta_evolved)
    assert blocker_evolved is None
