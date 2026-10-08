"""Tests for the load_source_data first-sync default.

Covers the acceptance criteria directly:
  1. A brand-new project's first sync (no options.load_source_data
     configured, no prior successful run) resolves load_source_data=True
     automatically, tagged with trigger="first_sync_default".
  2. A second sync of the same project (a prior successful run now
     exists) resolves back to load_source_data=False -- the original
     opt-in-only default -- so nothing gets reloaded/duplicated.
  3. A project that explicitly sets options.load_source_data (true OR
     false) always uses that value, regardless of run history -- existing
     projects' behavior is never changed by this default.
  4. has_any_successful_run is ORM-backed (survives restart, unlike the
     in-memory compat store), excludes the run currently in progress, and
     fails closed (assumes NOT first sync) on a query error.
"""
from __future__ import annotations

from types import SimpleNamespace

from semabridge.core.engine.deployment.snowflake import _resolve_load_source_data
from semabridge.repository.model_repository import ModelRepository


def _repo(tmp_path, name="runs.db") -> ModelRepository:
    db_path = tmp_path / name
    return ModelRepository(url_override=f"sqlite:///{db_path.as_posix()}")


# ---------------------------------------------------------------------------
# ModelRepository.has_any_successful_run
# ---------------------------------------------------------------------------

def test_has_any_successful_run_false_for_brand_new_project(tmp_path):
    repo = _repo(tmp_path)
    assert repo.has_any_successful_run("proj-new") is False


def test_has_any_successful_run_true_after_a_successful_run(tmp_path):
    repo = _repo(tmp_path)
    repo.record_run_start(run_id="run-1", project_id="proj-a", source_type="pbix", target_type="snowflake")
    repo.record_run_complete(run_id="run-1", status="success", final_step=10, duration_ms=1000)

    assert repo.has_any_successful_run("proj-a") is True


def test_has_any_successful_run_ignores_failed_runs(tmp_path):
    repo = _repo(tmp_path)
    repo.record_run_start(run_id="run-1", project_id="proj-a", source_type="pbix", target_type="snowflake")
    repo.record_run_complete(run_id="run-1", status="failed", final_step=4, duration_ms=500)

    assert repo.has_any_successful_run("proj-a") is False


def test_has_any_successful_run_excludes_the_currently_in_flight_run(tmp_path):
    """The run in progress right now may already have an ORM row (status
    "running", written by record_run_start before deploy even happens) --
    it must never count as "a prior successful run" just because a row
    with its run_id exists."""
    repo = _repo(tmp_path)
    repo.record_run_start(run_id="run-current", project_id="proj-a", source_type="pbix", target_type="snowflake")
    # Still "running" -- not success -- but exclude_run_id must ALSO guard
    # against a future change that marks it success before this check runs.

    assert repo.has_any_successful_run("proj-a", exclude_run_id="run-current") is False


def test_has_any_successful_run_is_scoped_to_the_right_project(tmp_path):
    repo = _repo(tmp_path)
    repo.record_run_start(run_id="run-1", project_id="proj-a", source_type="pbix", target_type="snowflake")
    repo.record_run_complete(run_id="run-1", status="success", final_step=10, duration_ms=1000)

    assert repo.has_any_successful_run("proj-b") is False


def test_has_any_successful_run_fails_closed_on_query_error(monkeypatch, tmp_path):
    repo = _repo(tmp_path)

    def broken_session():
        raise RuntimeError("simulated DB outage")

    monkeypatch.setattr(repo, "_session", broken_session)

    # Fail closed: assume a prior successful run exists rather than risk
    # auto-enabling a real data write when we can't actually confirm it's
    # safe to.
    assert repo.has_any_successful_run("proj-a") is True


# ---------------------------------------------------------------------------
# _resolve_load_source_data
# ---------------------------------------------------------------------------

def _fake_context(project_id="proj-a", run_id="run-1", options=None):
    return SimpleNamespace(
        project_id=project_id,
        run_id=run_id,
        config=SimpleNamespace(options=options),
    )


def test_explicit_true_wins_regardless_of_run_history(monkeypatch):
    monkeypatch.setattr(
        "semabridge.repository.model_repository.ModelRepository.has_any_successful_run",
        lambda self, *a, **k: (_ for _ in ()).throw(AssertionError("must not consult run history when explicitly set")),
    )
    context = _fake_context(options=SimpleNamespace(load_source_data=True))
    load_source_data, trigger = _resolve_load_source_data(context)
    assert load_source_data is True
    assert trigger == "explicit"


def test_explicit_false_wins_regardless_of_run_history(monkeypatch):
    monkeypatch.setattr(
        "semabridge.repository.model_repository.ModelRepository.has_any_successful_run",
        lambda self, *a, **k: (_ for _ in ()).throw(AssertionError("must not consult run history when explicitly set")),
    )
    context = _fake_context(options=SimpleNamespace(load_source_data=False))
    load_source_data, trigger = _resolve_load_source_data(context)
    assert load_source_data is False
    assert trigger is None


def test_unset_defaults_true_on_first_sync(monkeypatch):
    monkeypatch.setattr(
        "semabridge.repository.model_repository.ModelRepository.has_any_successful_run",
        lambda self, project_id, exclude_run_id=None: False,  # no prior successful run
    )
    # options present but WITHOUT the load_source_data key at all (mirrors
    # config.py's SimpleNamespace(**options_cfg) when the YAML never set it).
    context = _fake_context(options=SimpleNamespace())
    load_source_data, trigger = _resolve_load_source_data(context)
    assert load_source_data is True
    assert trigger == "first_sync_default"


def test_unset_defaults_false_when_a_prior_successful_run_exists(monkeypatch):
    monkeypatch.setattr(
        "semabridge.repository.model_repository.ModelRepository.has_any_successful_run",
        lambda self, project_id, exclude_run_id=None: True,  # a prior successful run exists
    )
    context = _fake_context(options=SimpleNamespace())
    load_source_data, trigger = _resolve_load_source_data(context)
    assert load_source_data is False
    assert trigger is None


def test_unset_with_no_options_namespace_at_all_defaults_by_run_history(monkeypatch):
    """A project whose config never had an `options:` block at all (options
    is None, not just missing the one key) must behave identically to one
    with an empty options block -- still eligible for the first-sync
    default, not silently disabled."""
    monkeypatch.setattr(
        "semabridge.repository.model_repository.ModelRepository.has_any_successful_run",
        lambda self, project_id, exclude_run_id=None: False,
    )
    context = _fake_context(options=None)
    load_source_data, trigger = _resolve_load_source_data(context)
    assert load_source_data is True
    assert trigger == "first_sync_default"


def test_has_any_successful_run_called_with_correct_project_and_exclusion(monkeypatch):
    captured = {}

    def fake_has_any_successful_run(self, project_id, exclude_run_id=None):
        captured["project_id"] = project_id
        captured["exclude_run_id"] = exclude_run_id
        return False

    monkeypatch.setattr(
        "semabridge.repository.model_repository.ModelRepository.has_any_successful_run",
        fake_has_any_successful_run,
    )
    context = _fake_context(project_id="proj-xyz", run_id="run-999", options=SimpleNamespace())
    _resolve_load_source_data(context)

    assert captured["project_id"] == "proj-xyz"
    assert captured["exclude_run_id"] == "run-999"
