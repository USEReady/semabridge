"""E13: cross-project table ownership compares DB.SCHEMA.TABLE and flags other projects' placeholders."""
from __future__ import annotations

from semabridge.connectors.table_binding_resolver import LiveSchemaMetadata, _log_binding
from semabridge.repository.model_repository import ModelRepository


def _repo(tmp_path):
    return ModelRepository(url_override=f"sqlite:///{(tmp_path / 'b.db').as_posix()}")


def test_same_table_name_in_another_schema_is_not_a_conflict(tmp_path):
    repo = _repo(tmp_path)
    repo.save_table_bindings("p1", "m1", [
        {"dataset_key": "sales", "dataset": "Sales", "table": "SALES", "database": "DB", "schema": "A", "matched_by": "exact name"},
    ])
    assert repo.find_other_project_table_bindings("p2", ["SALES"], database="DB", schema="B") == []
    hits = repo.find_other_project_table_bindings("p2", ["SALES"], database="DB", schema="A")
    assert len(hits) == 1 and hits[0]["qualified"] and hits[0]["schema"] == "A"
    assert repo.find_other_project_table_bindings("p2", ["db.a.sales"]) and not repo.find_other_project_table_bindings("p2", ["DB.C.SALES"])
    # Own project is never a conflict.
    assert repo.find_other_project_table_bindings("p1", ["SALES"], database="DB", schema="A") == []


def test_placeholder_created_by_other_project_is_flagged(tmp_path):
    repo = _repo(tmp_path)
    repo.save_table_bindings("p1", "m1", [
        {"dataset_key": "targets", "dataset": "Targets", "table": "TARGETS", "database": "DB", "schema": "S", "matched_by": "created"},
    ])
    hits = repo.find_other_project_table_bindings("p2", [{"table": "TARGETS", "database": "DB", "schema": "S"}])
    assert hits and hits[0]["created_by_other_project"] is True


def test_legacy_unqualified_binding_matches_by_name_and_is_marked(tmp_path):
    repo = _repo(tmp_path)
    repo.save_table_bindings("p1", "m1", [{"dataset_key": "x", "dataset": "X", "table": "X", "matched_by": "columns"}])
    hits = repo.find_other_project_table_bindings("p2", ["X"], database="DB", schema="S")
    assert hits and hits[0]["qualified"] is False


def test_created_flag_and_namespace_survive_later_re_resolution():
    meta = LiveSchemaMetadata()
    meta.binding_log["targets"] = {"dataset": "Targets", "table": "TARGETS", "matched_by": "created",
                                   "tie": [], "database": "DB", "schema": "S"}
    _log_binding(meta, "Targets", "TARGETS", "exact name")
    e = meta.binding_log["targets"]
    assert e["matched_by"] == "created" and e["database"] == "DB" and e["schema"] == "S"
