"""E14: every inactive relationship used by USERELATIONSHIP gets its own role alias."""
from __future__ import annotations

from types import SimpleNamespace

from semabridge.connectors.relationships_clause_builder import RelationshipsClauseBuilder
from semabridge.utils.identifiers import IdentifierSanitizer, inactive_role_alias_map, _relationship_signature


class _DDL:
    def to_snowflake_relationship_name(self, name: str) -> str:
        return IdentifierSanitizer().sanitize_column(name)


class _Schema:
    def _resolve_physical_column_name(self, dataset, column: str) -> str:
        return IdentifierSanitizer().sanitize_column(column)


def _rel(to_col, name, active=False, from_col="Posting_Date", from_ds="spend_fact"):
    return SimpleNamespace(unique_name=name, is_active=active, from_dataset=from_ds, to_dataset="date",
                           from_columns=[from_col], to_columns=[to_col])


def _model(rels):
    return SimpleNamespace(relationships=rels, metrics=[
        SimpleNamespace(expression="CALCULATE(SUM(spend_fact[Amt]), USERELATIONSHIP(spend_fact[Posting_Date], 'date'[Cal_DT]))"),
        SimpleNamespace(expression="CALCULATE(SUM(spend_fact[Amt]), USERELATIONSHIP(spend_fact[Posting_Date], 'date'[LY_CAL_DT]))"),
    ])


def test_single_inactive_relationship_keeps_short_alias():
    rels = [_rel("LY_CAL_DT", "r1")]
    m = inactive_role_alias_map(rels, _model(rels))
    assert list(m.values()) == ["DATE_POSTING_DATE"]


def test_two_inactive_relationships_on_same_fk_get_distinct_aliases_and_both_emit():
    rels = [_rel("Cal_DT", "r_cal"), _rel("LY_CAL_DT", "r_ly")]
    model = _model(rels)
    m = inactive_role_alias_map(rels, model)
    aliases = [m[_relationship_signature(r)] for r in rels]
    assert aliases == ["DATE_POSTING_DATE_CAL_DT", "DATE_POSTING_DATE_LY_CAL_DT"]

    builder = RelationshipsClauseBuilder(IdentifierSanitizer(), _Schema(), _DDL())
    lines = builder.build_for_osi(
        model,
        dataset_aliases={"spend_fact": "SPEND_FACT", "date": "COL_DATE"},
        dataset_by_name={"spend_fact": SimpleNamespace(), "date": SimpleNamespace()},
        dataset_col_lookup={"spend_fact": {"POSTING_DATE"}, "date": {"CAL_DT", "LY_CAL_DT"}},
        declared_pk_by_alias={
            "__keys__DATE_POSTING_DATE_CAL_DT": [["CAL_DT"]],
            "__keys__DATE_POSTING_DATE_LY_CAL_DT": [["LY_CAL_DT"]],
        },
        relationship_target_alias={},
    )
    assert len(lines) == 2
    assert 'REFERENCES DATE_POSTING_DATE_CAL_DT ("CAL_DT")' in lines[0]
    assert 'REFERENCES DATE_POSTING_DATE_LY_CAL_DT ("LY_CAL_DT")' in lines[1]


def test_unused_inactive_relationship_gets_no_alias():
    rels = [_rel("Cal_DT", "r_cal", from_col="Ship_Date")]
    assert inactive_role_alias_map(rels, _model(rels)) == {}
