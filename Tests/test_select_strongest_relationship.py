"""Regression test: ExecutionEngine._select_strongest_relationship's
"column matches target table name" heuristic used to strip a candidate FK
suffix with str.rstrip("_ID_KEYFK") — which strips any trailing characters
IN that set, not the literal suffix "_ID"/"_KEY"/"_FK" — so a column name
ending in any of those characters for an unrelated reason got over-stripped
(e.g. "DECK_ID" -> "DEC", not "DECK", since "K" is itself in the strip set).
Fixed to strip one real suffix from the same fk_suffixes list already used
just above it, instead of a second, inconsistent char-class representation
of "the same suffixes".

Synthetic placeholder names only.
"""
from semabridge.core.execution_engine import ExecutionEngine
from semabridge.sml.models import SMLRelationship


def _rel(unique_name, from_dataset, from_column, to_dataset, to_column="ID"):
    return SMLRelationship(
        unique_name=unique_name,
        from_dataset=from_dataset,
        from_columns=[from_column],
        to_dataset=to_dataset,
        to_columns=[to_column],
    )


def test_over_strip_bug_no_longer_loses_the_table_name_match():
    """The exact bug: "DECK_ID".rstrip("_ID_KEYFK") == "DEC", not "DECK",
    because "K" is itself a member of the strip char set. This candidate
    must now correctly score above an unrelated candidate that has no
    real signal at all."""
    real_match = _rel("r1", "Cards", "DECK_ID", "Deck")
    no_signal = _rel("r2", "Cards", "SOME_OTHER_COLUMN", "Deck")

    winner = ExecutionEngine._select_strongest_relationship(
        object(), [no_signal, real_match]
    )
    assert winner.unique_name == "r1"


def test_column_matching_target_table_name_still_scores_via_real_suffix_strip():
    rel = _rel("r1", "Orders", "CUSTOMER_ID", "Customer")
    other = _rel("r2", "Orders", "UNRELATED_ID", "Customer")

    winner = ExecutionEngine._select_strongest_relationship(object(), [other, rel])
    assert winner.unique_name == "r1"
