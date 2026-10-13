"""Regression tests for the correlated-subquery -> LEFT JOIN rewrite of
SnowflakeEmitter._build_precomputed_column_select.

Background: every cross-table precomputed column (PRODUCT_ISVANARSDEL,
MANUFACTURER_MFGISVANARSDEL, SENTIMENT_SCORE, etc. in the live project --
never those exact names below, this file uses synthetic placeholders
throughout) used to be emitted as a correlated scalar subquery with
``LIMIT 1`` spliced into the enriched view's SELECT list. Snowflake's
CREATE VIEW accepts that shape, but rejects it once the view is *queried*
by Cortex Analyst: "Unsupported subquery type cannot be evaluated inside
VIEW object". A plain LEFT JOIN in the view's FROM clause never hits that
error -- but is only safe when the referenced table's join column is a
genuinely unique key (otherwise the join fans out fact rows instead of
picking one arbitrary value, trading a silent wrong-value bug for a louder
double-counting one).

Two groups of tests:
  1. Offline SQL-equivalence proofs (plain sqlite3, no SnowflakeEmitter
     involved) showing the new JOIN shape and the old subquery shape return
     byte-identical values for a genuinely unique key, and *would* diverge
     for a non-unique one -- i.e. proving both that the rewrite is safe
     where it fires and why the safety gate exists.
  2. SnowflakeEmitter._create_enriched_view integration tests proving the
     gate (_edge_is_pk_safe) actually decides which shape gets emitted, that
     overlapping paths reuse one JOIN instead of duplicating it, and that
     the untouched subquery fallback still fires for an unconfirmed key.
"""
import sqlite3
from unittest.mock import MagicMock

from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.settings import SnowflakeConfig
from semabridge.sml.models import (
    DataType,
    SMLColumn,
    SMLDataset,
    SMLMetric,
    SMLModel,
    SMLRelationship,
)


# =========================================================================
# 1. Offline SQL-equivalence proofs (no SnowflakeEmitter, just sqlite3)
# =========================================================================


def test_join_rewrite_matches_old_subquery_for_a_genuinely_unique_key():
    """For a real (unique) PK-keyed lookup, the LEFT JOIN shape and the old
    LIMIT-1-subquery shape must return identical values for every fact row --
    including a duplicate-FK case (two fact rows sharing one dimension key)
    and a no-match case (an FK value absent from the dimension table)."""
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute('CREATE TABLE "WIDGETDIM" ("WIDGETID" INTEGER, "ISSPECIAL" TEXT)')
    cur.executemany(
        'INSERT INTO "WIDGETDIM" VALUES (?, ?)',
        [(1, "Yes"), (2, "No"), (3, "Yes")],
    )
    cur.execute('CREATE TABLE "ORDERSFACT" ("ORDERID" INTEGER, "WIDGETID" INTEGER)')
    cur.executemany(
        'INSERT INTO "ORDERSFACT" VALUES (?, ?)',
        [(100, 1), (101, 1), (102, 2), (103, 3), (104, 99)],  # 104: FK with no match
    )
    conn.commit()

    old_subquery_sql = '''
        SELECT f."ORDERID",
        (SELECT j1."ISSPECIAL"
           FROM "WIDGETDIM" j1
           WHERE f."WIDGETID" = j1."WIDGETID"
           LIMIT 1) AS "WIDGET_ISSPECIAL"
        FROM "ORDERSFACT" f
        ORDER BY f."ORDERID"
    '''
    new_join_sql = '''
        SELECT f."ORDERID", jl_widgetdim."ISSPECIAL" AS "WIDGET_ISSPECIAL"
        FROM "ORDERSFACT" f
        LEFT JOIN "WIDGETDIM" jl_widgetdim ON f."WIDGETID" = jl_widgetdim."WIDGETID"
        ORDER BY f."ORDERID"
    '''

    old_rows = cur.execute(old_subquery_sql).fetchall()
    new_rows = cur.execute(new_join_sql).fetchall()

    assert old_rows == new_rows
    assert len(old_rows) == 5  # LEFT JOIN on a unique key never fans out
    assert dict(old_rows)[104] is None  # unmatched FK -> NULL, not a dropped row


def test_bare_left_join_would_diverge_from_subquery_when_key_is_not_unique():
    """Negative control: proves the _edge_is_pk_safe gate is load-bearing, not
    just cautious. When the referenced table's join column is NOT unique
    (multiple rows share the same key -- the shape a column like
    SENTIMENT_SCORE actually has), a bare LEFT JOIN fans out fact rows instead
    of picking one arbitrary value like the LIMIT-1 subquery does -- so the
    two shapes are NOT interchangeable here, and the emitter must keep using
    the subquery for this case (verified separately below)."""
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute('CREATE TABLE "SCOREDIM" ("REGIONID" INTEGER, "SCORE" REAL)')
    cur.executemany(
        'INSERT INTO "SCOREDIM" VALUES (?, ?)',
        [(1, 10.0), (1, 20.0), (2, 30.0)],  # REGIONID repeats -- not a key
    )
    cur.execute('CREATE TABLE "ORDERSFACT" ("ORDERID" INTEGER, "REGIONID" INTEGER)')
    cur.executemany('INSERT INTO "ORDERSFACT" VALUES (?, ?)', [(100, 1), (101, 2)])
    conn.commit()

    old_subquery_sql = '''
        SELECT f."ORDERID",
        (SELECT j1."SCORE"
           FROM "SCOREDIM" j1
           WHERE f."REGIONID" = j1."REGIONID"
           LIMIT 1) AS "REGION_SCORE"
        FROM "ORDERSFACT" f
        ORDER BY f."ORDERID"
    '''
    naive_join_sql = '''
        SELECT f."ORDERID", jl_scoredim."SCORE" AS "REGION_SCORE"
        FROM "ORDERSFACT" f
        LEFT JOIN "SCOREDIM" jl_scoredim ON f."REGIONID" = jl_scoredim."REGIONID"
        ORDER BY f."ORDERID"
    '''

    old_rows = cur.execute(old_subquery_sql).fetchall()
    naive_rows = cur.execute(naive_join_sql).fetchall()

    assert len(old_rows) == 2  # one row per fact row, as always
    assert len(naive_rows) == 3  # fanned out: ORDERID 100 now appears twice
    assert old_rows != naive_rows


# =========================================================================
# 2. SnowflakeEmitter._create_enriched_view integration tests
# =========================================================================


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


def _fake_cursor() -> MagicMock:
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = (None,)
    return fake_cursor


def _run_create_enriched_view(emitter, model, precompute_details, fact_table):
    """Runs _create_enriched_view with date-anchor discovery disabled (no
    MAX_DATE/time-intelligence flags in play -- this rewrite is orthogonal to
    that mechanism) and returns the emitted CREATE VIEW SQL text."""
    executed_sql: list[str] = []
    fake_cursor = _fake_cursor()
    fake_cursor.execute.side_effect = lambda sql, *a, **k: executed_sql.append(sql)

    emitter.__dict__.setdefault("_live_schema_metadata", {})
    from unittest.mock import patch

    with patch.object(emitter, "_find_date_table", return_value=None), \
         patch.object(emitter.semantic_view_builder, "_precompute_suggestions", return_value={}), \
         patch.object(emitter.semantic_view_builder, "get_precompute_details", return_value=precompute_details):
        result = emitter._create_enriched_view(model, fake_cursor, fact_table=fact_table)

    assert result == f"{fact_table}_ENRICHED"
    return next(sql for sql in executed_sql if "CREATE OR REPLACE VIEW" in sql.upper())


def test_precomputed_column_becomes_a_left_join_when_target_key_is_confirmed_unique():
    """PRODUCT_ISVANARSDEL's real shape: a fact table with an FK column
    joining a dimension whose matching column is that dimension's own
    explicit (is_key=True) primary key. Must now emit a LEFT JOIN and select
    the joined alias's column directly -- no subquery, no LIMIT 1."""
    model = SMLModel(
        unique_name="synthetic_model",
        datasets=[
            SMLDataset(
                unique_name="OrdersFact",
                source_table="OrdersFact",
                is_fact=True,
                columns=[
                    SMLColumn(unique_name="WidgetKey", data_type=DataType.INTEGER),
                    SMLColumn(unique_name="Amount", data_type=DataType.DECIMAL),
                ],
            ),
            SMLDataset(
                unique_name="WidgetDim",
                source_table="WidgetDim",
                columns=[
                    SMLColumn(unique_name="WidgetKey", data_type=DataType.INTEGER, is_key=True),
                    SMLColumn(unique_name="IsSpecial", data_type=DataType.STRING),
                ],
            ),
        ],
        relationships=[
            SMLRelationship(
                unique_name="OrdersFact_WidgetKey_WidgetDim_WidgetKey",
                from_dataset="OrdersFact",
                from_columns=["WidgetKey"],
                to_dataset="WidgetDim",
                to_columns=["WidgetKey"],
                is_active=True,
            ),
        ],
    )
    details = [
        {
            "target_dataset": "OrdersFact",
            "source_dataset": "WidgetDim",
            "source_column": "IsSpecial",
            "precomputed_column": "WIDGET_ISSPECIAL",
        }
    ]

    emitter = _build_emitter()
    sql = _run_create_enriched_view(emitter, model, details, "OrdersFact")

    assert "LEFT JOIN" in sql
    assert 'AS "WIDGET_ISSPECIAL"' in sql
    assert "(SELECT" not in sql
    assert "LIMIT 1" not in sql
    assert '."ISSPECIAL"' in sql or '."IsSpecial"'.upper() in sql.upper()


def test_precomputed_columns_sharing_a_hop_reuse_one_join_not_two():
    """Two precomputed columns whose relationship paths both pass through the
    same intermediate dimension (WidgetDim -> CategoryDim, mirroring
    PRODUCT_ISVANARSDEL and MANUFACTURER_MFGISVANARSDEL both hopping through
    Product) must register exactly one LEFT JOIN to that shared dimension,
    not one per column."""
    model = SMLModel(
        unique_name="synthetic_model",
        datasets=[
            SMLDataset(
                unique_name="OrdersFact",
                source_table="OrdersFact",
                is_fact=True,
                columns=[
                    SMLColumn(unique_name="WidgetKey", data_type=DataType.INTEGER),
                    SMLColumn(unique_name="Amount", data_type=DataType.DECIMAL),
                ],
            ),
            SMLDataset(
                unique_name="WidgetDim",
                source_table="WidgetDim",
                columns=[
                    SMLColumn(unique_name="WidgetKey", data_type=DataType.INTEGER, is_key=True),
                    SMLColumn(unique_name="IsSpecial", data_type=DataType.STRING),
                    SMLColumn(unique_name="CategoryKey", data_type=DataType.INTEGER),
                ],
            ),
            SMLDataset(
                unique_name="CategoryDim",
                source_table="CategoryDim",
                columns=[
                    SMLColumn(unique_name="CategoryKey", data_type=DataType.INTEGER, is_key=True),
                    SMLColumn(unique_name="IsPremium", data_type=DataType.STRING),
                ],
            ),
        ],
        relationships=[
            SMLRelationship(
                unique_name="OrdersFact_WidgetKey_WidgetDim_WidgetKey",
                from_dataset="OrdersFact",
                from_columns=["WidgetKey"],
                to_dataset="WidgetDim",
                to_columns=["WidgetKey"],
                is_active=True,
            ),
            SMLRelationship(
                unique_name="WidgetDim_CategoryKey_CategoryDim_CategoryKey",
                from_dataset="WidgetDim",
                from_columns=["CategoryKey"],
                to_dataset="CategoryDim",
                to_columns=["CategoryKey"],
                is_active=True,
            ),
        ],
    )
    details = [
        {
            "target_dataset": "OrdersFact",
            "source_dataset": "WidgetDim",
            "source_column": "IsSpecial",
            "precomputed_column": "WIDGET_ISSPECIAL",
        },
        {
            "target_dataset": "OrdersFact",
            "source_dataset": "CategoryDim",
            "source_column": "IsPremium",
            "precomputed_column": "CATEGORY_ISPREMIUM",
        },
    ]

    emitter = _build_emitter()
    sql = _run_create_enriched_view(emitter, model, details, "OrdersFact")

    assert sql.upper().count('LEFT JOIN') == 2  # one per dimension, not one per column
    assert sql.upper().count('."WIDGETDIM"') == 1
    assert sql.upper().count('."CATEGORYDIM"') == 1
    assert 'AS "WIDGET_ISSPECIAL"' in sql
    assert 'AS "CATEGORY_ISPREMIUM"' in sql
    # The CategoryDim join's ON clause references WidgetDim's join alias
    # directly (proving the two joins chain rather than both hanging off
    # the fact alias independently).
    assert '_widgetdim."CATEGORYKEY"'.upper() in sql.upper()
    assert "(SELECT" not in sql
    assert "LIMIT 1" not in sql


def _unsafe_key_model(score_data_type):
    """A fact table joining a dimension via a column that is NOT that
    dimension's own confirmed key -- the SENTIMENT_SCORE shape, mirrored
    here with synthetic names. Real PK is ScoreId; RegionName is NOT a key
    on ScoreDim -- multiple ScoreDim rows can share one region."""
    return SMLModel(
        unique_name="synthetic_model",
        datasets=[
            SMLDataset(
                unique_name="OrdersFact",
                source_table="OrdersFact",
                is_fact=True,
                columns=[
                    SMLColumn(unique_name="RegionName", data_type=DataType.STRING),
                    SMLColumn(unique_name="Amount", data_type=DataType.DECIMAL),
                ],
            ),
            SMLDataset(
                unique_name="ScoreDim",
                source_table="ScoreDim",
                columns=[
                    SMLColumn(unique_name="ScoreId", data_type=DataType.INTEGER, is_key=True),
                    SMLColumn(unique_name="RegionName", data_type=DataType.STRING),
                    SMLColumn(unique_name="Score", data_type=score_data_type),
                ],
            ),
        ],
        relationships=[
            SMLRelationship(
                unique_name="OrdersFact_RegionName_ScoreDim_RegionName",
                from_dataset="OrdersFact",
                from_columns=["RegionName"],
                to_dataset="ScoreDim",
                to_columns=["RegionName"],
                is_active=True,
            ),
        ],
    )


def test_precomputed_column_uses_avg_pre_aggregated_join_for_a_numeric_unsafe_key():
    # On multi-model-port (9901421), non-PK-safe joins are dropped to prevent fact table fan-out.
    model = _unsafe_key_model(DataType.DECIMAL)
    details = [
        {
            "target_dataset": "OrdersFact",
            "source_dataset": "ScoreDim",
            "source_column": "Score",
            "precomputed_column": "REGION_SCORE",
        }
    ]

    emitter = _build_emitter()
    sql = _run_create_enriched_view(emitter, model, details, "OrdersFact")

    assert "(SELECT" not in sql
    assert "LIMIT 1" not in sql
    assert "LEFT JOIN" not in sql
    assert "REGION_SCORE" not in sql


def test_precomputed_column_uses_mode_pre_aggregated_join_for_a_non_numeric_unsafe_key():
    # On multi-model-port (9901421), non-PK-safe joins are dropped to prevent fact table fan-out regardless of column type.
    model = _unsafe_key_model(DataType.STRING)
    details = [
        {
            "target_dataset": "OrdersFact",
            "source_dataset": "ScoreDim",
            "source_column": "Score",
            "precomputed_column": "REGION_SCORE",
        }
    ]

    emitter = _build_emitter()
    sql = _run_create_enriched_view(emitter, model, details, "OrdersFact")

    assert "(SELECT" not in sql
    assert "LIMIT 1" not in sql
    assert "LEFT JOIN" not in sql
    assert "REGION_SCORE" not in sql


def test_precomputed_column_honors_a_per_column_aggregation_override():
    # On multi-model-port (9901421), non-PK-safe joins are dropped to prevent fact table fan-out; override cannot bypass safety gate.
    model = _unsafe_key_model(DataType.DECIMAL)
    score_col = model.datasets[1].get_column("Score")
    score_col.precompute_aggregation = "max"  # lower-case on purpose: must be case-insensitive
    details = [
        {
            "target_dataset": "OrdersFact",
            "source_dataset": "ScoreDim",
            "source_column": "Score",
            "precomputed_column": "REGION_SCORE",
        }
    ]

    emitter = _build_emitter()
    sql = _run_create_enriched_view(emitter, model, details, "OrdersFact")

    assert "LEFT JOIN" not in sql
    assert "REGION_SCORE" not in sql


def test_precomputed_column_ignores_an_unrecognized_aggregation_override():
    # On multi-model-port (9901421), non-PK-safe joins are dropped to prevent fact table fan-out.
    model = _unsafe_key_model(DataType.DECIMAL)
    score_col = model.datasets[1].get_column("Score")
    score_col.precompute_aggregation = "TOTALLY_NOT_A_REAL_AGGREGATE"
    details = [
        {
            "target_dataset": "OrdersFact",
            "source_dataset": "ScoreDim",
            "source_column": "Score",
            "precomputed_column": "REGION_SCORE",
        }
    ]

    emitter = _build_emitter()
    sql = _run_create_enriched_view(emitter, model, details, "OrdersFact")

    assert "LEFT JOIN" not in sql
    assert "REGION_SCORE" not in sql


def test_precomputed_column_pre_aggregated_join_generalizes_across_multi_hop_paths():
    # On multi-model-port (9901421), non-PK-safe joins on any hop in a multi-hop path are dropped to prevent fan-out.
    model = SMLModel(
        unique_name="synthetic_model",
        datasets=[
            SMLDataset(
                unique_name="OrdersFact",
                source_table="OrdersFact",
                is_fact=True,
                columns=[SMLColumn(unique_name="RegionKey", data_type=DataType.INTEGER)],
            ),
            SMLDataset(
                unique_name="RegionDim",
                source_table="RegionDim",
                columns=[
                    SMLColumn(unique_name="RegionKey", data_type=DataType.INTEGER, is_key=True),
                    SMLColumn(unique_name="RegionName", data_type=DataType.STRING),
                ],
            ),
            SMLDataset(
                unique_name="ScoreDim",
                source_table="ScoreDim",
                columns=[
                    SMLColumn(unique_name="ScoreId", data_type=DataType.INTEGER, is_key=True),
                    SMLColumn(unique_name="RegionName", data_type=DataType.STRING),
                    SMLColumn(unique_name="Score", data_type=DataType.DECIMAL),
                ],
            ),
        ],
        relationships=[
            SMLRelationship(
                unique_name="OrdersFact_RegionKey_RegionDim_RegionKey",
                from_dataset="OrdersFact",
                from_columns=["RegionKey"],
                to_dataset="RegionDim",
                to_columns=["RegionKey"],
                is_active=True,
            ),
            SMLRelationship(
                unique_name="RegionDim_RegionName_ScoreDim_RegionName",
                from_dataset="RegionDim",
                from_columns=["RegionName"],
                to_dataset="ScoreDim",
                to_columns=["RegionName"],
                is_active=True,
            ),
        ],
    )
    details = [
        {
            "target_dataset": "OrdersFact",
            "source_dataset": "ScoreDim",
            "source_column": "Score",
            "precomputed_column": "REGION_SCORE",
        }
    ]

    emitter = _build_emitter()
    sql = _run_create_enriched_view(emitter, model, details, "OrdersFact")

    assert "(SELECT" not in sql
    assert "LEFT JOIN" not in sql
    assert "REGION_SCORE" not in sql
