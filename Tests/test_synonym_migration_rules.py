"""Synonyms edited during a multi-model dry run reach the real project:
each model's synonyms go only to that model, and a later edit on the real
project is never overwritten by an older dry-run value."""

from datetime import datetime, timedelta

import pytest

from semabridge.utils.synonyms import copy_synonym_overrides, load_synonym_overrides


@pytest.fixture(autouse=True)
def _orm_tables():
    from semabridge.repository.orm.base import Base
    from semabridge.repository.orm.session_factory import db_manager

    Base.metadata.create_all(bind=db_manager.get_engine())
    yield


def _put(project_id, model, table, column, synonyms, updated_at):
    from semabridge.repository.orm.models import SynonymOverride
    from semabridge.repository.orm.session_factory import db_manager

    with db_manager.get_session() as session:
        row = SynonymOverride(
            project_id=project_id, model_name=model, table_name=table, column_name=column,
            created_at=updated_at, updated_at=updated_at,
        )
        row.synonyms = synonyms
        session.add(row)


def _set(project_id, model, table, column, synonyms, updated_at):
    from sqlalchemy import select

    from semabridge.repository.orm.models import SynonymOverride
    from semabridge.repository.orm.session_factory import db_manager

    with db_manager.get_session() as session:
        row = session.execute(
            select(SynonymOverride).where(
                SynonymOverride.project_id == project_id,
                SynonymOverride.model_name == model,
                SynonymOverride.table_name == table,
                SynonymOverride.column_name == column,
            )
        ).scalars().one()
        row.synonyms = synonyms
        row.updated_at = updated_at


T0 = datetime(2026, 1, 1, 12, 0, 0)


def test_each_model_synonyms_land_only_on_that_model():
    # Two models, each previewed in its own preview project, with the same
    # table/column names.
    _put("preview-m1", "preview_name_1", "table_x", "col_1", ["alias_one"], T0)
    _put("preview-m2", "preview_name_2", "table_x", "col_1", ["alias_two"], T0)

    copied = copy_synonym_overrides(
        ["preview-m1", "preview-m2"], "proj-scope",
        model_name_by_source={"preview-m1": "MODEL_A", "preview-m2": "MODEL_B"},
    )
    assert copied == 2
    real = load_synonym_overrides("proj-scope")
    assert real[("MODEL_A".lower(), "table_x", "col_1")] == ["alias_one"]
    assert real[("MODEL_B".lower(), "table_x", "col_1")] == ["alias_two"]
    assert len(real) == 2


def test_later_edit_on_the_real_project_is_never_overwritten():
    _put("preview-m3", "MODEL_C", "table_x", "col_1", ["dry_run_value"], T0)
    assert copy_synonym_overrides(["preview-m3"], "proj-keep") == 1

    # The user edits the real project's synonyms after the copy.
    _set("proj-keep", "MODEL_C", "table_x", "col_1", ["user_value"], T0 + timedelta(hours=1))

    # Saving the project again (same dry-run source) must not overwrite it.
    assert copy_synonym_overrides(["preview-m3"], "proj-keep") == 0
    assert load_synonym_overrides("proj-keep")[("model_c", "table_x", "col_1")] == ["user_value"]


def test_a_newer_dry_run_edit_does_apply():
    _put("preview-m4", "MODEL_D", "table_x", "col_1", ["first"], T0)
    copy_synonym_overrides(["preview-m4"], "proj-newer")
    # A new dry-run edit made after the project row was written.
    _set("preview-m4", "MODEL_D", "table_x", "col_1", ["second"], T0 + timedelta(hours=2))
    assert copy_synonym_overrides(["preview-m4"], "proj-newer") == 1
    assert load_synonym_overrides("proj-newer")[("model_d", "table_x", "col_1")] == ["second"]
