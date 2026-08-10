"""Regression tests for utils/precompute_aggregation.py -- the load/lookup
helpers backing the precompute_aggregation_overrides table, mirroring
utils/synonyms.py's load_synonym_overrides/lookup_synonym_override shape
and its graceful "missing table -> no overrides, never raise" behavior.
"""
import os

from semabridge.utils.precompute_aggregation import (
    load_precompute_aggregation_overrides,
    lookup_precompute_aggregation_override,
    precompute_aggregation_override_key,
)


def _prepare_db() -> None:
    from semabridge.repository.orm.base import Base
    from semabridge.repository.orm.session_factory import db_manager, reset_engine

    os.environ["SEMABRIDGE_DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"
    os.environ["SEMABRIDGE_DB_BACKEND"] = "orm"

    reset_engine()
    db_manager.reset()
    engine = db_manager.get_engine()
    Base.metadata.create_all(bind=engine)


def test_load_precompute_aggregation_overrides_with_no_project_id_returns_empty():
    assert load_precompute_aggregation_overrides(None) == {}
    assert load_precompute_aggregation_overrides("") == {}


def test_load_precompute_aggregation_overrides_round_trips_a_persisted_row():
    """A row actually written to the DB via the ORM model must come back
    out of the loader, keyed and cased exactly like lookup expects."""
    _prepare_db()

    from semabridge.repository.orm.models import PrecomputeAggregationOverride
    from semabridge.repository.orm.session_factory import db_manager

    with db_manager.get_session() as session:
        session.add(
            PrecomputeAggregationOverride(
                id="override-1",
                project_id="proj-synthetic",
                model_name="SyntheticModel",
                table_name="ScoreDim",
                column_name="Score",
                aggregation_strategy="max",
            )
        )
        session.commit()

    overrides = load_precompute_aggregation_overrides("proj-synthetic")
    assert overrides[precompute_aggregation_override_key("SyntheticModel", "ScoreDim", "Score")] == "MAX"

    resolved = lookup_precompute_aggregation_override(
        overrides, ["SyntheticModel", "proj-synthetic"], "ScoreDim", "Score"
    )
    assert resolved == "MAX"

    # A different project's override must not leak across.
    assert load_precompute_aggregation_overrides("some-other-project") == {}


def test_load_precompute_aggregation_overrides_drops_unrecognized_strategy_values():
    """A row with a bogus aggregation_strategy (e.g. hand-edited DB row, or
    a future strategy this codebase doesn't know how to emit yet) must be
    silently dropped, not surfaced as if it were valid."""
    _prepare_db()

    from semabridge.repository.orm.models import PrecomputeAggregationOverride
    from semabridge.repository.orm.session_factory import db_manager

    with db_manager.get_session() as session:
        session.add(
            PrecomputeAggregationOverride(
                id="override-2",
                project_id="proj-synthetic-2",
                model_name="SyntheticModel",
                table_name="ScoreDim",
                column_name="Score",
                aggregation_strategy="NONSENSE",
            )
        )
        session.commit()

    assert load_precompute_aggregation_overrides("proj-synthetic-2") == {}


def test_lookup_precompute_aggregation_override_tries_each_model_name_candidate():
    overrides = {
        precompute_aggregation_override_key("proj-synthetic", "ScoreDim", "Score"): "MODE",
    }
    # First candidate ("SomeModel") doesn't match; second ("proj-synthetic") does.
    assert lookup_precompute_aggregation_override(
        overrides, ["SomeModel", "proj-synthetic"], "ScoreDim", "Score"
    ) == "MODE"
    # No candidate matches -> None, not an empty string or KeyError.
    assert lookup_precompute_aggregation_override(
        overrides, ["SomeModel"], "ScoreDim", "Score"
    ) is None
    # Empty/None overrides dict -> None, never raises.
    assert lookup_precompute_aggregation_override(None, ["proj-synthetic"], "ScoreDim", "Score") is None
