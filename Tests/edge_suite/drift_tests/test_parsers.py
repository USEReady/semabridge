"""2. Parsers, and the round trip that a fresh deploy must pass.

The bug these cases exist for: a parser that stores the expression as the name
and the name as the expression, so every object looks deleted and re-added.
"""
from __future__ import annotations

import pytest

from drift_tests import adapter
from drift_tests.conftest import read_fixture

M = adapter.models
C = adapter.names(adapter.comparison, "compare")
P = adapter.names(adapter.parsers, "parse_ddl", "parse_yaml",
                  "assert_names_are_identifiers", "split_top_level",
                  "DefinitionUnreadable")

VIEW = "DB1.SCH1.VIEW_ONE"


def ddl():
    return P.parse_ddl(read_fixture("snowflake", "view_deployed.sql"), VIEW)


def yml():
    return P.parse_yaml(read_fixture("databricks", "view_deployed.yaml"), VIEW)


def names_of(definition, kind):
    return sorted(o.name.upper() for o in definition.objects if o.kind == kind)


def one(definition, kind, name):
    found = [o for o in definition.objects
             if o.kind == kind and o.name.upper() == name.upper()]
    assert len(found) == 1, f"expected exactly one {kind} named {name}, got {len(found)}"
    return found[0]


# ---------------------------------------------------------------- the core split

def test_2_1_ddl_parses_every_kind():
    definition = ddl()
    assert names_of(definition, M.METRIC) == [
        "FLAGGED_RATE", "ITEM_COUNT", "TOTAL_AMOUNT", "TYPED_TOTAL", "UNRESOLVED"]
    assert "CATEGORY" in names_of(definition, M.DIMENSION)
    assert names_of(definition, M.FACT)
    assert [o for o in definition.objects if o.kind == M.RELATIONSHIP]


def test_2_1b_name_and_expression_are_not_swapped():
    metric = one(ddl(), M.METRIC, "TOTAL_AMOUNT")
    assert metric.name.upper() == "TOTAL_AMOUNT"
    assert "SUM" in metric.expression.upper()


def test_2_2_synonyms_clause_stays_out_of_the_name_and_expression():
    metric = one(ddl(), M.METRIC, "TOTAL_AMOUNT")
    assert "synonyms" not in metric.name.lower()
    assert "synonyms" not in metric.expression.lower()


def test_2_3_comment_clause_becomes_the_description():
    assert one(ddl(), M.METRIC, "TOTAL_AMOUNT").description == "Total amount"


def test_2_4_entry_with_both_clauses_is_handled():
    metric = one(ddl(), M.METRIC, "TOTAL_AMOUNT")
    assert metric.description and "SUM" in metric.expression.upper()


def test_2_5_visibility_marker_is_stripped_from_the_name():
    assert all(not o.name.lower().startswith("private") for o in ddl().objects)


def test_2_5b_non_additive_clause_stays_out_of_the_name():
    metric = one(ddl(), M.METRIC, "ITEM_COUNT")
    assert "additive" not in metric.name.lower()


def test_2_6_expression_containing_as_commas_and_brackets_splits_correctly():
    metric = one(ddl(), M.METRIC, "TYPED_TOTAL")
    assert metric.name.upper() == "TYPED_TOTAL"
    assert metric.expression.upper().startswith("CAST(")


def test_2_7_no_object_contains_text_from_the_next_section():
    for o in ddl().objects:
        for word in ("facts", "dimensions", "metrics", "relationships"):
            assert f") {word} (" not in o.name.lower()
            assert f") {word} (" not in (o.expression or "").lower()


def test_2_8_table_entries_keep_the_key_clause_out_of_the_name():
    assert all("primary key" not in o.name.lower() for o in ddl().objects)


def test_2_10_every_parsed_name_is_an_identifier():
    P.assert_names_are_identifiers(ddl())
    P.assert_names_are_identifiers(yml())


def test_2_11_object_count_per_kind_matches_the_definition():
    text = read_fixture("snowflake", "view_deployed.sql")
    metrics_block = text.split("metrics (")[1].rsplit(")", 2)[0]
    assert len(P.split_top_level(metrics_block)) == len(names_of(ddl(), M.METRIC))


def test_2_12_empty_definition_is_refused_clearly():
    with pytest.raises(P.DefinitionUnreadable):
        P.parse_ddl("", VIEW)
    with pytest.raises(P.DefinitionUnreadable):
        P.parse_yaml("", VIEW)


def test_2_13_malformed_definition_is_refused_not_half_parsed():
    with pytest.raises(P.DefinitionUnreadable):
        P.parse_yaml("measures: [ {name: a, expr: 'SUM(x)'", VIEW)


def test_2_14_truncated_definition_is_refused():
    text = read_fixture("snowflake", "view_deployed.sql")[:40]
    with pytest.raises(P.DefinitionUnreadable):
        P.parse_ddl(text, VIEW)


def test_2_15_unknown_clause_does_not_stop_the_rest():
    text = read_fixture("databricks", "view_deployed.yaml") + "\nunknown_section:\n  - a: 1\n"
    assert [o for o in P.parse_yaml(text, VIEW).objects if o.kind == M.METRIC]


def test_2_17_duplicate_names_are_deterministic_and_do_not_crash():
    text = read_fixture("databricks", "view_deployed.yaml") + (
        "\n  - name: total_amount\n    expr: SUM(entity.other)\n")
    first = P.parse_yaml(text, VIEW).by_key()
    second = P.parse_yaml(text, VIEW).by_key()
    assert list(first) == list(second)


def test_2_18_a_very_large_definition_still_parses():
    text = read_fixture("snowflake", "view_deployed.sql")
    extra = ",\n\t\t".join(f"ENTITY.GEN_{i} as SUM(ENTITY.AMOUNT::FLOAT)" for i in range(500))
    text = text.replace("ENTITY.TYPED_TOTAL", extra + ",\n\t\tENTITY.TYPED_TOTAL")
    assert len(names_of(P.parse_ddl(text, VIEW), M.METRIC)) >= 500


def test_2_19_yaml_parses_facts_and_joins_too():
    definition = yml()
    assert [o for o in definition.objects if o.kind == M.FACT]
    assert [o for o in definition.objects if o.kind == M.RELATIONSHIP]


# ---------------------------------------------------------------- round trip

def test_9_1_ddl_fresh_deploy_shows_no_drift():
    baseline = ddl()
    live = P.parse_ddl(read_fixture("snowflake", "view_live_reformatted.sql"), VIEW)
    assert C.compare(baseline, live) == []


def test_9_1b_yaml_fresh_deploy_shows_no_drift():
    baseline = yml()
    live = P.parse_yaml(read_fixture("databricks", "view_live_reformatted.yaml"), VIEW)
    assert C.compare(baseline, live) == []


def test_9_2_one_changed_expression_gives_exactly_one_finding():
    baseline = ddl()
    text = read_fixture("snowflake", "view_live_reformatted.sql").replace(
        "sum( ENTITY.AMOUNT :: FLOAT )", "avg( ENTITY.AMOUNT :: FLOAT )")
    findings = C.compare(baseline, P.parse_ddl(text, VIEW))
    assert len(findings) == 1 and findings[0].category == M.EXPRESSION_DRIFT


def test_9_3_one_changed_description_gives_exactly_one_finding():
    baseline = ddl()
    text = read_fixture("snowflake", "view_live_reformatted.sql").replace(
        "comment='Category of the row'", "comment='Category of each row'")
    findings = C.compare(baseline, P.parse_ddl(text, VIEW))
    assert len(findings) == 1 and findings[0].category == M.DESCRIPTION_DRIFT
