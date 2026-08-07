"""Regression tests for the dataset_col_lookup-vs-live-schema staleness bug.

Auto-enrichment (which creates the enriched view and fetches anchor
columns such as MAX_DATE) used to run BEFORE Step 1 (table creation) in
SnowflakeEmitter._execute_deployment_pipeline. On a cold deploy — the fact
table doesn't exist in Snowflake yet — _create_enriched_view's own anchor-
column fetch silently swallows the resulting failure and just omits the
column, so a metric depending on that anchor (e.g. TOTAL_UNITS_SPLY,
depending on MAX_DATE) would non-deterministically flip between "live"
and "dropped" across runs, purely based on whether a *previous* run had
already created the table — same code, same model, different outcome.

The fix moves auto-enrichment to run after Step 1/1.5, once the
underlying table is guaranteed to exist, and resets
SnowflakeEmitter._live_schema_metadata per run so a reused emitter
instance can't carry a prior deploy's schema snapshot into this one.

These tests exercise the real _execute_deployment_pipeline method with
every unrelated step stubbed out via monkeypatch, and a deliberate
RuntimeError sentinel raised right at Step 2 (DDL generation) to halt
execution cleanly once the part under test has run — the outer exception
handler in _execute_deployment_pipeline converts that into a clean
`return False` with the message recorded on `last_deployment_error`, so
no real Snowflake connection or DDL generation is ever required.

All identifiers below are synthetic placeholders — none of this is tied
to any real model, past or present.
"""
from unittest.mock import MagicMock

from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.settings import SnowflakeConfig
from semabridge.sml.models import SMLModel, SMLDataset, SMLColumn, SMLMetric, DataType


def _build_emitter() -> SnowflakeEmitter:
    config = SnowflakeConfig(
        account="test.local",
        user="test_user",
        password="test_password",
        warehouse="test_wh",
        database="test_db",
        schema_name="test_schema",
        role="test_role",
    )
    behavior = ConnectorBehavior()
    behavior.snowflake.create_missing_tables = True
    behavior.snowflake.apply_inferred_types = False
    behavior.snowflake.auto_execute_precompute = True
    behavior.snowflake.auto_create_enriched_view = True
    behavior.snowflake.use_enriched_view_for_metrics = True
    return SnowflakeEmitter(config, behavior)


def _model() -> SMLModel:
    return SMLModel(
        unique_name="synthetic_model",
        datasets=[
            SMLDataset(
                unique_name="SomeFactTable",
                source_table="SomeFactTable",
                columns=[SMLColumn(unique_name="SomeColumn", data_type=DataType.DECIMAL)],
            )
        ],
        metrics=[
            SMLMetric(
                unique_name="Some_Anchor_Dependent_Metric",
                dataset="SomeFactTable",
                sql_expression='SUM(SOMEFACTTABLE."SOMECOLUMN")',
            )
        ],
    )


class _SentinelReached(RuntimeError):
    """Raised by the stubbed DDL-generation step to halt the pipeline
    cleanly right after the part under test (Steps 1/1.5/enrichment) has
    run, without needing to mock the rest of the ~480-line method."""


def _wire_stubs(emitter, monkeypatch, call_order, table_exists_flag, fact_table="SomeFactTable"):
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = MagicMock()
    monkeypatch.setattr(emitter.connection_manager, "get_connection", lambda: (fake_conn, False))

    monkeypatch.setattr(
        emitter.semantic_view_builder, "_precompute_duplicate_mappings", lambda *a, **k: None
    )
    monkeypatch.setattr(emitter, "_drop_deprecated_views", lambda *a, **k: None)

    def _fake_ensure_tables(cur, model):
        call_order.append("ensure_tables")
        table_exists_flag["value"] = True

    monkeypatch.setattr(emitter.schema_manager, "_ensure_source_tables_exist", _fake_ensure_tables)
    monkeypatch.setattr(emitter.schema_manager, "refresh_schema_cache", lambda: None)
    monkeypatch.setattr(emitter.schema_manager, "_fetch_schema_metadata", lambda cur: {})
    monkeypatch.setattr(emitter.schema_manager, "_fetch_model_table_metadata", lambda cur, datasets: {})

    def _fake_precompute_suggestions(model, cur):
        call_order.append("auto_execute_precompute_suggestions")

    monkeypatch.setattr(emitter, "_auto_execute_precompute_suggestions", _fake_precompute_suggestions)
    monkeypatch.setattr(emitter, "_get_fact_tables_needing_enrichment", lambda model: [fact_table])

    def _fake_create_enriched_view(model, cur, fact_table=None):
        call_order.append("create_enriched_view")
        if not table_exists_flag["value"]:
            # Mirrors _fetch_literal's real behavior: SELECT MAX(date_col)
            # FROM a table that doesn't exist yet raises, the exception is
            # swallowed, and the anchor column is simply never added.
            return None
        enriched_name = f"{fact_table.upper()}_ENRICHED"
        # Mirrors _create_enriched_view's real write-back of fact columns
        # plus whatever anchor columns it successfully computed.
        emitter._live_schema_metadata[enriched_name] = {"SOMECOLUMN", "MAX_DATE"}
        return enriched_name

    monkeypatch.setattr(emitter, "_create_enriched_view", _fake_create_enriched_view)

    def _raise_sentinel(*a, **k):
        raise _SentinelReached("reached Step 2 (DDL generation) — test stops here")

    monkeypatch.setattr(emitter.semantic_view_builder, "generate_ddls", _raise_sentinel)
    monkeypatch.setattr(emitter.semantic_view_builder, "generate_ddls_from_osi", _raise_sentinel)


def test_enrichment_runs_after_table_creation_so_anchor_column_survives_cold_start(monkeypatch):
    """The specific bug, closed: simulate the exact cold scenario (fact
    table does not exist when the pipeline starts) and confirm the anchor
    column (MAX_DATE) is present in the live schema snapshot Step 2 would
    have consumed — where before the fix, an anchor-dependent metric would
    have been silently dropped in this exact scenario."""
    emitter = _build_emitter()
    call_order = []
    table_exists_flag = {"value": False}  # cold: table does not exist yet
    _wire_stubs(emitter, monkeypatch, call_order, table_exists_flag)

    result = emitter._execute_deployment_pipeline(_model(), is_osi=False)

    assert result is False  # halted by our sentinel, as designed
    assert "reached Step 2" in (emitter.last_deployment_error or "")

    # The real bug: enrichment used to run BEFORE table creation.
    assert "ensure_tables" in call_order
    assert "create_enriched_view" in call_order
    assert call_order.index("ensure_tables") < call_order.index("create_enriched_view"), (
        "auto-enrichment must run after table creation, not before"
    )

    # The real symptom: the anchor column must have survived into the
    # live schema snapshot Step 2 will read.
    enriched_cols = emitter._live_schema_metadata.get("SOMEFACTTABLE_ENRICHED", set())
    assert "MAX_DATE" in enriched_cols, (
        "MAX_DATE must be present even when the fact table didn't exist "
        "at the start of the deploy — this is exactly the scenario that "
        "used to silently drop anchor-dependent metrics"
    )


def test_repeated_cold_start_runs_produce_identical_results(monkeypatch):
    """Same input (a fresh, cold deploy each time — table doesn't exist
    at the start of either run) must produce the same output, twice in a
    row, on the same reused emitter instance. This directly tests the
    property the bug was violating: the same code, same model, used to
    produce different outcomes purely based on unrelated prior-run state."""
    emitter = _build_emitter()

    def _run_once():
        call_order = []
        table_exists_flag = {"value": False}  # reset to cold for *this* run
        _wire_stubs(emitter, monkeypatch, call_order, table_exists_flag)
        result = emitter._execute_deployment_pipeline(_model(), is_osi=False)
        return result, dict(emitter._live_schema_metadata)

    result_1, schema_1 = _run_once()
    result_2, schema_2 = _run_once()

    assert result_1 is False and result_2 is False
    assert "MAX_DATE" in schema_1.get("SOMEFACTTABLE_ENRICHED", set())
    assert "MAX_DATE" in schema_2.get("SOMEFACTTABLE_ENRICHED", set())
    assert schema_1 == schema_2, (
        "repeated deploys of the same model must produce identical live-schema "
        "snapshots — this is the exact 'same input, same output' property the "
        "bug violated (accept/reject flipped run to run with no code change)"
    )


def test_max_date_anchor_only_queried_against_table_that_owns_it(monkeypatch):
    """Regression test for the KPI-vs-SalesFact bug: _create_enriched_view's
    MAX_DATE anchor injection used to fire an unconditional SELECT MAX(date_col)
    against whichever fact table the auto-enrichment loop happened to be
    visiting, with no check that the table actually has that column. On a
    model with multiple fact-like tables, the table that doesn't own the
    date column caused a real Snowflake error every time — deterministic for
    a given model, but decided by iteration order rather than schema truth.

    This builds a synthetic two-table model — FactWithDate genuinely has the
    date-anchor column, FactWithoutDate does not — and asserts:
      - no query is ever fired against FactWithoutDate for the anchor column
      - FactWithDate's enriched view still gets MAX_DATE added correctly
    """
    emitter = _build_emitter()

    model = SMLModel(
        unique_name="synthetic_multi_fact_model",
        datasets=[
            SMLDataset(
                unique_name="FactWithDate",
                source_table="FactWithDate",
                columns=[
                    SMLColumn(unique_name="OrderDate", data_type=DataType.STRING),
                    SMLColumn(unique_name="Amount", data_type=DataType.DECIMAL),
                ],
            ),
            SMLDataset(
                unique_name="FactWithoutDate",
                source_table="FactWithoutDate",
                columns=[SMLColumn(unique_name="Quantity", data_type=DataType.DECIMAL)],
            ),
        ],
        metrics=[],
    )

    # Fix the date anchor deterministically instead of depending on
    # Config/date_resolution.yaml pattern matching — _find_date_table itself
    # isn't what's under test here.
    monkeypatch.setattr(emitter, "_find_date_table", lambda m: ("FactWithDate", "OrderDate", "OrderDate"))
    monkeypatch.setattr(emitter.semantic_view_builder, "_precompute_suggestions", lambda m: {})
    monkeypatch.setattr(emitter.semantic_view_builder, "get_precompute_details", lambda: [])

    # FactWithDate genuinely has ORDERDATE; FactWithoutDate genuinely does not.
    emitter._live_schema_metadata["FACTWITHDATE"] = {"ORDERDATE", "AMOUNT"}
    emitter._live_schema_metadata["FACTWITHOUTDATE"] = {"QUANTITY"}

    executed_sql: list[str] = []

    def _fake_execute(sql, *a, **k):
        executed_sql.append(sql)

    fake_cursor = MagicMock()
    fake_cursor.execute.side_effect = _fake_execute
    fake_cursor.fetchone.return_value = ("2026-01-01",)

    result_with_date = emitter._create_enriched_view(model, fake_cursor, fact_table="FactWithDate")
    result_without_date = emitter._create_enriched_view(model, fake_cursor, fact_table="FactWithoutDate")

    assert result_with_date == "FactWithDate_ENRICHED"
    assert result_without_date == "FactWithoutDate_ENRICHED"

    # The core fix: no anchor query was ever fired against the table that
    # doesn't own the column — not "fired and gracefully failed", never fired.
    assert not any("FACTWITHOUTDATE" in sql.upper() and "ORDERDATE" in sql.upper() for sql in executed_sql), (
        "MAX_DATE must never be queried against a table that doesn't have "
        "the date-anchor column — selection must be driven by actual column "
        "ownership, not by which table the enrichment loop happened to visit"
    )

    # The table that does own it still gets a real query and the column.
    assert any("FACTWITHDATE" in sql.upper() and "ORDERDATE" in sql.upper() for sql in executed_sql)
    assert "MAX_DATE" in emitter._live_schema_metadata.get("FACTWITHDATE_ENRICHED", set())
    assert "MAX_DATE" not in emitter._live_schema_metadata.get("FACTWITHOUTDATE_ENRICHED", set())


def test_live_schema_metadata_is_reset_between_deploys_on_a_reused_emitter(monkeypatch):
    """A reused emitter instance must not carry a prior deploy's live
    schema snapshot into the next one — e.g. an enriched view/anchor
    column entry for a table that this run's Step 1 might recreate from
    scratch (and which might legitimately have a different physical
    schema this time)."""
    emitter = _build_emitter()
    call_order = []
    table_exists_flag = {"value": True}
    _wire_stubs(emitter, monkeypatch, call_order, table_exists_flag)

    # Seed stale state from a "previous deploy" that must not leak forward.
    emitter._live_schema_metadata["STALE_LEFTOVER_ENRICHED"] = {"SOME_OLD_COLUMN"}

    emitter._execute_deployment_pipeline(_model(), is_osi=False)

    assert "STALE_LEFTOVER_ENRICHED" not in emitter._live_schema_metadata

    # The reset must be in-place (cleared), not a reassignment — SemanticViewBuilder
    # holds this same dict by reference from construction time.
    assert emitter.semantic_view_builder.live_schema_metadata is emitter._live_schema_metadata


def test_enriched_view_mapping_survives_when_behavior_source_table_mapping_is_none(monkeypatch):
    """Regression for the dataset_col_lookup/enriched-view mapping-store
    split: recording an enrichment success used to go to one of two
    unconnected dicts depending on whether behavior.snowflake.source_table_mapping
    happened to be None, with only one of those two dicts ever read by
    TablesClauseBuilder when it builds dataset_col_lookup. If a caller
    ever set source_table_mapping to None explicitly (instead of leaving
    the default empty dict), the enrichment success would be recorded
    into a dict nothing else read — reproducing the exact
    'enrichment succeeded but the anchor column is still reported missing'
    symptom this whole test module guards against, just via a different
    trigger than cold-start ordering.

    Fixed by always recording into SnowflakeEmitter._enriched_view_mapping
    and having every consumer (TablesClauseBuilder, _dataset_source_ref,
    _create_enriched_view) resolve through the shared
    resolve_source_table_mapping() merge instead of reading
    behavior.snowflake.source_table_mapping directly.
    """
    from semabridge.connectors.alias_registry import AliasRegistry
    from semabridge.connectors.tables_clause_builder import TablesClauseBuilder

    emitter = _build_emitter()
    call_order = []
    table_exists_flag = {"value": True}  # warm: table already exists
    _wire_stubs(emitter, monkeypatch, call_order, table_exists_flag)

    # The exact edge case under test: explicitly None, not the Pydantic
    # default empty dict.
    emitter.sf_behavior.source_table_mapping = None

    result = emitter._execute_deployment_pipeline(_model(), is_osi=False)

    assert result is False  # halted by our sentinel, as designed
    assert "create_enriched_view" in call_order

    # The enrichment success must still be recorded somewhere every
    # consumer can see, even though behavior-level mapping is None.
    assert emitter._enriched_view_mapping.get("SomeFactTable") == "SOMEFACTTABLE_ENRICHED"
    assert emitter._get_source_table_mapping().get("SomeFactTable") == "SOMEFACTTABLE_ENRICHED"

    # End-to-end: a freshly built TablesClauseBuilder — sharing the same
    # live_schema_metadata and enriched_view_mapping the real pipeline
    # would hand it — must resolve the fact dataset to the enriched view
    # and therefore see MAX_DATE in dataset_col_lookup, exactly as it
    # would need to for metric-column validation to pass.
    tbuilder = TablesClauseBuilder(
        emitter._id,
        emitter.schema_manager,
        emitter.config,
        emitter.behavior,
        emitter._live_schema_metadata,
        enriched_view_mapping=emitter._enriched_view_mapping,
    )
    _, _, _, dataset_col_lookup, _, _ = tbuilder.build_for_sml(
        _model(), AliasRegistry(), {}, set()
    )

    assert "MAX_DATE" in dataset_col_lookup.get("SomeFactTable", set()), (
        "MAX_DATE must be visible in dataset_col_lookup even when "
        "behavior.snowflake.source_table_mapping is explicitly None — "
        "the enriched-view redirect must never be recorded somewhere "
        "dataset_col_lookup construction can't see"
    )


def test_create_enriched_view_emits_flag_columns_for_discovered_shapes(monkeypatch):
    """Part 2, Step 2 of the MAX_DATE-capability-limit fix: _create_enriched_view
    must precompute one boolean flag column per time-intelligence shape the
    model's metrics actually need (see converter/time_intelligence_shapes.py),
    following the exact same "fetch the anchor once, splice as a literal"
    pattern already used for MAX_DATE itself — never hardcoded to specific
    metric or shape names."""
    from semabridge.sml.models import SMLModel, SMLDataset, SMLColumn, SMLMetric, DataType

    emitter = _build_emitter()

    model = SMLModel(
        unique_name="synthetic_ytd_model",
        datasets=[
            SMLDataset(
                unique_name="FactWithDate",
                source_table="FactWithDate",
                columns=[
                    SMLColumn(unique_name="OrderDate", data_type=DataType.STRING),
                    SMLColumn(unique_name="Amount", data_type=DataType.DECIMAL),
                ],
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="TOTAL_AMOUNT_YTD",
                dataset="FactWithDate",
                expression="TOTALYTD([TOTAL_AMOUNT], 'Date'[Date])",
            ),
            SMLMetric(
                unique_name="TOTAL_AMOUNT_YTD_SPLY",
                dataset="FactWithDate",
                expression="CALCULATE([TOTAL_AMOUNT_YTD], SAMEPERIODLASTYEAR('Date'[Date]))",
            ),
        ],
    )

    monkeypatch.setattr(emitter, "_find_date_table", lambda m: ("FactWithDate", "OrderDate", "OrderDate"))
    monkeypatch.setattr(emitter.semantic_view_builder, "_precompute_suggestions", lambda m: {})
    monkeypatch.setattr(emitter.semantic_view_builder, "get_precompute_details", lambda: [])

    emitter._live_schema_metadata["FACTWITHDATE"] = {"ORDERDATE", "AMOUNT"}

    executed_sql: list[str] = []
    fake_cursor = MagicMock()
    fake_cursor.execute.side_effect = lambda sql, *a, **k: executed_sql.append(sql)
    fake_cursor.fetchone.return_value = ("2026-01-01",)

    result = emitter._create_enriched_view(model, fake_cursor, fact_table="FactWithDate")

    assert result == "FactWithDate_ENRICHED"
    create_view_sql = next(sql for sql in executed_sql if "CREATE OR REPLACE VIEW" in sql.upper())

    # Discovered shapes: direct YTD, and the nested YTD-then-SPLY composition
    # (SAMEPERIODLASTYEAR wraps a measure reference whose own shape is YTD).
    assert 'AS "IS_YTD"' in create_view_sql
    assert 'AS "IS_YTD_SPLY_YEAR"' in create_view_sql
    # Well-formed: each flag column is a parenthesized boolean expression
    # comparing the fact table's OWN date column (not a joined dimension —
    # this runs inside the enriched view's own CREATE VIEW, before any
    # join) against the same anchor literal MAX_DATE uses.
    assert 'f."ORDERDATE"' in create_view_sql
    assert create_view_sql.count("DATE_TRUNC('YEAR'") >= 2  # base YTD + shifted nested YTD

    enriched_cols = emitter._live_schema_metadata.get("FACTWITHDATE_ENRICHED", set())
    assert {"MAX_DATE", "IS_YTD", "IS_YTD_SPLY_YEAR"} <= enriched_cols


def test_create_enriched_view_emits_no_flag_columns_when_model_has_no_time_intelligence(monkeypatch):
    """No time-intelligence shapes anywhere in the model must add no flag
    columns at all — never a default/guessed set."""
    from semabridge.sml.models import SMLModel, SMLDataset, SMLColumn, SMLMetric, DataType

    emitter = _build_emitter()

    model = SMLModel(
        unique_name="synthetic_plain_model",
        datasets=[
            SMLDataset(
                unique_name="FactWithDate",
                source_table="FactWithDate",
                columns=[
                    SMLColumn(unique_name="OrderDate", data_type=DataType.STRING),
                    SMLColumn(unique_name="Amount", data_type=DataType.DECIMAL),
                ],
            ),
        ],
        metrics=[
            SMLMetric(unique_name="TOTAL_AMOUNT", dataset="FactWithDate", expression="SUM([Amount])"),
        ],
    )

    monkeypatch.setattr(emitter, "_find_date_table", lambda m: ("FactWithDate", "OrderDate", "OrderDate"))
    monkeypatch.setattr(emitter.semantic_view_builder, "_precompute_suggestions", lambda m: {})
    monkeypatch.setattr(emitter.semantic_view_builder, "get_precompute_details", lambda: [])
    emitter._live_schema_metadata["FACTWITHDATE"] = {"ORDERDATE", "AMOUNT"}

    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = ("2026-01-01",)

    emitter._create_enriched_view(model, fake_cursor, fact_table="FactWithDate")

    enriched_cols = emitter._live_schema_metadata.get("FACTWITHDATE_ENRICHED", set())
    assert "MAX_DATE" in enriched_cols  # unaffected, still added as before
    assert not any(c.startswith("IS_") for c in enriched_cols)
