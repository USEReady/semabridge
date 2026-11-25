"""Ontology (synonym) overrides and project account links require access to
the project; they used to be open to any signed-in user."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from semabridge.api.routers import synonyms_router as sr
from semabridge.api.services import project_ownership_service as pos


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setattr(pos, "auth_is_enabled", lambda: True)
    monkeypatch.setattr(pos, "get_request_user_id", lambda request: "user-2")
    owned = {"proj-mine"}
    monkeypatch.setattr(
        pos, "is_project_owned_by_user",
        lambda project_id, user_id, **kw: project_id in owned,
    )


class _NoDb:
    def execute(self, *a, **k):  # reached only when access is granted
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []), scalar_one_or_none=lambda: None)


def test_reading_another_users_overrides_is_forbidden(auth_on):
    with pytest.raises(HTTPException) as exc:
        sr.get_model_ontology_overrides("proj-theirs", "Sales", request=object(), db=_NoDb())
    assert exc.value.status_code == 403


def test_owner_can_read_overrides(auth_on):
    assert sr.get_model_ontology_overrides("proj-mine", "Sales", request=object(), db=_NoDb()) == []


def test_upsert_and_delete_check_the_project(auth_on):
    req = sr.SynonymOverrideRequest(project_id="proj-theirs", model_name="m", table_name="t", column_name="c", synonyms=["x"])
    with pytest.raises(HTTPException):
        sr.upsert_synonym_override(req, request=object(), db=_NoDb())
    with pytest.raises(HTTPException):
        sr.delete_synonym_override("proj-theirs", "m", "t", "c", request=object(), db=_NoDb())
    with pytest.raises(HTTPException):
        sr.get_synonym_override("proj-theirs", "m", "t", "c", request=object(), db=_NoDb())


def test_link_account_requires_project_access(monkeypatch, auth_on):
    from semabridge.api import account_router as ar

    project = SimpleNamespace(project_id="proj-theirs", account_id=None)
    account = SimpleNamespace(id="acct-1", owner_id="user-2")
    results = iter([project, account])

    class Db:
        def execute(self, *a, **k):
            value = next(results)
            return SimpleNamespace(scalar_one_or_none=lambda: value)

        def commit(self):
            raise AssertionError("must not commit for a project the user cannot access")

    monkeypatch.setattr(ar, "_try_get_user", lambda request, db: SimpleNamespace(id="user-2"))
    body = SimpleNamespace(account_id="acct-1", warehouse=None, database=None, schema_name=None, role=None)
    with pytest.raises(HTTPException) as exc:
        ar.link_project_account(request=object(), project_id="proj-theirs", body=body, db=Db())
    assert exc.value.status_code == 403
    assert project.account_id is None
