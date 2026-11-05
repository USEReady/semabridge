import random
import re
import uuid
import duckdb
import pytest

from semabridge.converter.dax_ast_parser import DaxAstParser, DaxSqlRenderer
from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder, NULL_PLACEHOLDER_SQL
from semabridge.api.services.run_report_service import _classify_metrics
from semabridge.utils.naming import sanitize_column


def random_id(prefix: str = "T") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:6].upper()}"


def make_builder() -> MetricsClauseBuilder:
    builder = object.__new__(MetricsClauseBuilder)
    return builder


def test_rule1_column_resolution_row_context_duckdb():
    """Rule 1: Innermost iterated table > home table with random names, evaluated in DuckDB."""
    home_tbl = random_id("Home")
    iter_tbl = random_id("Iter")
    col_a = random_id("ColA")
    col_b = random_id("ColB")

    dax = f'COUNTROWS(FILTER(\'{iter_tbl}\', [{col_a}] = "x" && [{col_b}] = "y"))'
    ast = DaxAstParser().parse(dax)
    renderer = DaxSqlRenderer(table_alias=home_tbl, primary_table_name=home_tbl)
    sql = renderer.render(ast)

    # Must resolve against iter_tbl, NOT home_tbl
    assert f'{iter_tbl.upper()}."{col_a.upper()}"' in sql
    assert f'{iter_tbl.upper()}."{col_b.upper()}"' in sql
    assert home_tbl.upper() not in sql

    # Evaluate in DuckDB
    con = duckdb.connect()
    con.execute(f'CREATE TABLE {iter_tbl.upper()} ("{col_a.upper()}" VARCHAR, "{col_b.upper()}" VARCHAR)')
    con.execute(f'INSERT INTO {iter_tbl.upper()} VALUES (\'x\', \'y\'), (\'x\', \'z\'), (\'w\', \'y\'), (\'x\', \'y\')')
    
    # Query using DuckDB
    res = con.execute(f'SELECT {sql} FROM {iter_tbl.upper()} AS {iter_tbl.upper()}').fetchone()[0]
    assert res == 2


def test_rule1_measure_resolved_before_column_duckdb():
    """Rule 1: Measures resolved before columns, evaluated in DuckDB."""
    home_tbl = random_id("Home")
    iter_tbl = random_id("Iter")
    meas_name = random_id("Meas")
    col_x = random_id("ColX")

    # Measure is pre-resolved to 10
    meas_map = {meas_name: "10"}
    dax = f'SUMX(\'{iter_tbl}\', [{meas_name}] + [{col_x}])'
    ast = DaxAstParser().parse(dax)
    renderer = DaxSqlRenderer(table_alias=home_tbl, primary_table_name=home_tbl, measure_sql_map=meas_map)
    sql = renderer.render(ast)

    assert "(10)" in sql
    assert f'{iter_tbl.upper()}."{col_x.upper()}"' in sql

    con = duckdb.connect()
    con.execute(f'CREATE TABLE {iter_tbl.upper()} ("{col_x.upper()}" INTEGER)')
    con.execute(f'INSERT INTO {iter_tbl.upper()} VALUES (5), (15)')

    # Strip ::FLOAT if any for DuckDB compatibility
    duck_sql = sql.replace("::FLOAT", "")
    res = con.execute(f'SELECT {duck_sql} FROM {iter_tbl.upper()} AS {iter_tbl.upper()}').fetchone()[0]
    assert res == (10 + 5) + (10 + 15)  # 40


def test_rule2_check_a_table_column_exists():
    """Check (a): TABLE.COLUMN exists in that table (live schema)."""
    tbl = random_id("Tbl")
    col_real = random_id("ColReal")
    col_fake = random_id("ColFake")

    builder = make_builder()
    dataset_col_lookup = {tbl: {col_real}}
    dataset_aliases = {tbl: tbl}

    # Nonexistent column fails
    fail_expr = f'SUM({tbl}."{col_fake}")'
    reason = builder._run_predeploy_checks("M1", tbl, fail_expr, None, dataset_col_lookup, dataset_aliases, None)
    assert reason is not None
    assert "does not exist in schema" in reason

    # Existing column passes
    good_expr = f'SUM({tbl}."{col_real}")'
    reason_good = builder._run_predeploy_checks("M1", tbl, good_expr, None, dataset_col_lookup, dataset_aliases, None)
    assert reason_good is None


def test_rule2_check_b_filter_lost():
    """Check (b): Every DAX filter condition appears in SQL (filter lost check)."""
    tbl = random_id("Tbl")
    col = random_id("Col")
    dax = f'COUNTROWS(FILTER(\'{tbl}\', [{col}] = "Active"))'

    builder = make_builder()
    # Bare count(*) without CASE or condition loses filter -> FAIL
    bare_sql = "COUNT(*)"
    reason = builder._run_predeploy_checks("M_Filter", tbl, bare_sql, dax, None, {tbl: tbl}, None)
    assert reason is not None
    assert "lost in SQL translation" in reason

    # Preserved filter with CASE -> PASS
    good_sql = f'COUNT(CASE WHEN {tbl}."{col}" = \'Active\' THEN 1 END)'
    reason_good = builder._run_predeploy_checks("M_Filter", tbl, good_sql, dax, None, {tbl: tbl}, None)
    assert reason_good is None


def test_rule2_check_c_lineage_table_mismatch():
    """Check (c): SQL tables == DAX lineage tables."""
    home_tbl = random_id("Home")
    iter_tbl = random_id("Iter")
    col = random_id("Col")

    dax = f'COUNTROWS(FILTER(\'{iter_tbl}\', [{col}] = "xyz"))'
    builder = make_builder()
    aliases = {home_tbl: home_tbl, iter_tbl: iter_tbl}

    # Bug reproduction: SQL referenced home_tbl instead of iter_tbl
    bad_sql = f'COUNT(CASE WHEN ({home_tbl}."{col}" = \'xyz\') THEN 1 END)'
    reason = builder._run_predeploy_checks("M_Lineage", home_tbl, bad_sql, dax, None, aliases, None)
    assert reason is not None
    assert "do not match DAX lineage tables" in reason

    # Correct SQL references iter_tbl (and metric is placed on iter_tbl) -> PASS
    good_sql = f'COUNT(CASE WHEN ({iter_tbl}."{col}" = \'xyz\') THEN 1 END)'
    reason_good = builder._run_predeploy_checks("M_Lineage", iter_tbl, good_sql, dax, None, aliases, None)
    assert reason_good is None


def test_rule2_check_d_fan_out():
    """Check (d): No SUM/COUNT/AVG of a dimension column copied onto a fact."""
    fact_tbl = random_id("Fact")
    dim_tbl = random_id("Dim")
    dim_col = f"{dim_tbl}_ATTRIBUTE"

    class MockRel:
        from_dataset = fact_tbl
        to_dataset = dim_tbl

    builder = make_builder()
    aliases = {fact_tbl: fact_tbl, dim_tbl: dim_tbl}

    # SUM of copied dimension column -> FAIL
    sum_dim = f'SUM({fact_tbl}."{dim_col}")'
    reason = builder._run_predeploy_checks("M_Fanout", fact_tbl, sum_dim, None, None, aliases, [MockRel()])
    assert reason is not None
    assert "fan-out duplication" in reason

    # MIN / MAX / COUNT(DISTINCT) of copied dimension column -> ALLOWED
    min_dim = f'MIN({fact_tbl}."{dim_col}")'
    reason_min = builder._run_predeploy_checks("M_Fanout", fact_tbl, min_dim, None, None, aliases, [MockRel()])
    assert reason_min is None


def test_rule2_check_e_iteration_grain():
    """Check (e): Iterators over VALUES/DISTINCT are not unrolled."""
    tbl = random_id("Tbl")
    col = random_id("Col")
    dax = f'AVERAGEX(VALUES(\'{tbl}\'[{col}]), [{col}] * 2)'

    builder = make_builder()
    # Flat aggregate without group aggregation -> FAIL
    flat_sql = f'AVG({tbl}."{col}" * 2)'
    reason = builder._run_predeploy_checks("M_Grain", tbl, flat_sql, dax, None, {tbl: tbl}, None)
    assert reason is not None
    assert "aggregation over groups requires multi-level grouping" in reason


def test_rule2_check_f_outer_numeric_literals():
    """Check (f): Outer numeric literals preserved."""
    tbl = random_id("Tbl")
    col = random_id("Col")
    dax = f'[{col}] * 100'

    builder = make_builder()
    # Lost * 100 -> FAIL
    lost_sql = f'SUM({tbl}."{col}")'
    reason = builder._run_predeploy_checks("M_Pct", tbl, lost_sql, dax, None, {tbl: tbl}, None)
    assert reason is not None
    assert "outer numeric literal (* 100) from DAX expression was lost" in reason

    # Preserved * 100 -> PASS
    good_sql = f'SUM({tbl}."{col}") * 100'
    reason_good = builder._run_predeploy_checks("M_Pct", tbl, good_sql, dax, None, {tbl: tbl}, None)
    assert reason_good is None


def test_rule2_check_g_no_cast_null_inside_expression():
    """Check (g): No CAST(NULL) inside a larger expression (composite null)."""
    tbl = random_id("Tbl")
    builder = make_builder()

    # Standalone CAST(NULL AS DOUBLE) -> ALLOWED (normal null placeholder)
    assert builder._run_predeploy_checks("M_Null", tbl, NULL_PLACEHOLDER_SQL, None, None, None, None) is None

    # Composite null -> FAIL
    comp_null = f'100 * {NULL_PLACEHOLDER_SQL}'
    reason = builder._run_predeploy_checks("M_Null", tbl, comp_null, None, None, None, None)
    assert reason is not None
    assert "composite null" in reason


def test_rule3_run_report_counts_from_deployed_ddl():
    """Rule 3: Run report counts derived from final deployed DDL."""
    m1 = random_id("M_Live1")
    m2 = random_id("M_Live2")
    m3 = random_id("M_Dead")

    synthetic_ddl = f"""
    CREATE OR REPLACE SEMANTIC VIEW "DB"."SCHEMA"."VIEW"
    METRICS (
      T."{m1}" AS SUM(T.A),
      T."{m2}" AS AVG(T.B),
      T."{m3}" AS CAST(NULL AS DOUBLE) COMMENT = 'not applicable'
    );
    """

    sml_metrics = [
        {"unique_name": m1, "sync_enabled": True},
        {"unique_name": m2, "sync_enabled": True},
        {"unique_name": m3, "sync_enabled": True},
    ]

    from semabridge.core.reconciliation import _extract_deployed_metric_names
    live_mets, dead_mets = _extract_deployed_metric_names(synthetic_ddl)
    assert len(live_mets) == 2
    assert len(dead_mets) == 1

    classified = _classify_metrics(
        sml_metrics,
        dropped_metric_names=dead_mets,
        live_ddl_metric_names=live_mets,
    )
    # Only the 2 live metrics are counted as converted
    assert len(classified["standard"]) == 2
    assert {e["name"] for e in classified["standard"]} == {m1, m2}


def test_rule2_snowflake_error_010220_safety_and_fk_rewrite():
    """Snowflake Error 010220 safety: cross-table columns inside aggregates.
    1. If related via FK (Fact.fk -> Dim.pk), Dim.pk inside aggregate is rewritten to Fact.fk -> PASS.
    2. DuckDB verifies numerical evaluation of the rewritten expression.
    3. If unmapped cross-table column inside aggregate -> fails closed with 010220 reason.
    """
    fact_tbl = random_id("Fact")
    dim_tbl = random_id("Dim")
    fk_col = random_id("FkDate")
    pk_col = random_id("PkDate")
    val_col = random_id("Val")
    other_col = random_id("UnmappedCol")

    class MockRel:
        from_dataset = fact_tbl
        to_dataset = dim_tbl
        from_columns = [fk_col]
        to_columns = [pk_col]

    builder = make_builder()
    rels = [MockRel()]
    aliases = {fact_tbl: fact_tbl, dim_tbl: dim_tbl}
    cols = {
        fact_tbl: {fk_col, val_col},
        dim_tbl: {pk_col, other_col},
    }

    # 1. SQL with Dim.pk inside aggregate on Fact table
    original_sql = f'SUM(CASE WHEN ({dim_tbl}."{pk_col}" = \'2021-01-01\') THEN {fact_tbl}."{val_col}" ELSE 0 END)'
    rewritten_sql = builder._rewrite_foreign_table_columns_in_aggregates(
        original_sql, fact_tbl, rels, aliases
    )
    assert f'{fact_tbl.upper()}."{fk_col.upper()}"' in rewritten_sql.upper()
    assert dim_tbl.upper() not in rewritten_sql.upper()

    reason = builder._run_predeploy_checks("M_TI", fact_tbl, rewritten_sql, None, cols, aliases, rels)
    assert reason is None

    # Verify numerical correctness in DuckDB
    con = duckdb.connect()
    fk_clean = sanitize_column(fk_col)
    val_clean = sanitize_column(val_col)
    con.execute(f'CREATE TABLE "{fact_tbl}" ("{fk_clean}" VARCHAR, "{val_clean}" DOUBLE);')
    con.execute(f'INSERT INTO "{fact_tbl}" VALUES (\'2021-01-01\', 100.0), (\'2021-01-02\', 50.0);')
    res = con.execute(f'SELECT {rewritten_sql} FROM "{fact_tbl}"').fetchone()[0]
    assert res == 100.0

    # 2. Aggregating another metric -> fails closed to prevent error 010220
    other_metric = random_id("MetricOther")
    defined_metrics = {other_metric.upper()}
    agg_over_metric_sql = f'SUM({fact_tbl}."{other_metric}")'
    reason_agg_metric = builder._run_predeploy_checks(
        "M_Derived", fact_tbl, agg_over_metric_sql, None, cols, aliases, rels, defined_metric_names=defined_metrics
    )
    assert reason_agg_metric is not None
    assert "010220" in reason_agg_metric
    assert "wrap aggregate" in reason_agg_metric

    # 4. Mixing raw aggregate with metric reference -> fails closed with Error 010220 reason
    mixed_sql = f'(SUM({fact_tbl}."{val_col}")) / NULLIF({fact_tbl}."{other_metric}", 0)'
    reason_mixed = builder._run_predeploy_checks(
        "M_Mixed", fact_tbl, mixed_sql, None, cols, aliases, rels, defined_metric_names=defined_metrics
    )
    assert reason_mixed is not None
    assert "010220" in reason_mixed
    assert "cannot mix raw aggregate" in reason_mixed

    # 5. Reserved keyword START is recognized in SNOWFLAKE_RESERVED_WORDS
    from semabridge.utils.identifiers import SNOWFLAKE_RESERVED_WORDS
    assert "start" in SNOWFLAKE_RESERVED_WORDS


