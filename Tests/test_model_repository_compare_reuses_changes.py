from semabridge.repository.model_repository import ModelRepository


def _model_with_amount_type(col_type: str):
    return {
        "unique_name": "SalesModel",
        "datasets": [
            {
                "unique_name": "Orders",
                "columns": [
                    {"unique_name": "order_id", "data_type": "INT"},
                    {"unique_name": "amount", "data_type": col_type},
                ],
            }
        ],
        "metrics": [],
    }


def test_compare_versions_reuses_precomputed_changes(tmp_path):
    db_path = tmp_path / "reuse_changes_1.db"
    repo = ModelRepository(url_override=f"sqlite:///{db_path.as_posix()}")
    repo.ensure_project("proj-reuse", "Reuse Project", "ws-1")

    committed_1, s1 = repo.commit_model("proj-reuse", _model_with_amount_type("VARCHAR"))
    committed_2, s2 = repo.commit_model("proj-reuse", _model_with_amount_type("INT"))
    assert committed_1 is True
    assert committed_2 is True

    diff_rows = repo.compare_versions("proj-reuse", s1, s2)
    assert diff_rows
    dataset_rows = [row for row in diff_rows if row["object"] == "dataset.Orders"]
    assert dataset_rows
    assert "VARCHAR" in dataset_rows[0]["previous_version"]
    assert "INT" in dataset_rows[0]["new_version"]


def test_compare_versions_non_adjacent_falls_back_to_full_diff(tmp_path):
    db_path = tmp_path / "reuse_changes_2.db"
    repo = ModelRepository(url_override=f"sqlite:///{db_path.as_posix()}")
    repo.ensure_project("proj-reuse-2", "Reuse Project 2", "ws-1")

    committed_1, s1 = repo.commit_model("proj-reuse-2", _model_with_amount_type("VARCHAR"))
    committed_2, s2 = repo.commit_model("proj-reuse-2", _model_with_amount_type("INT"))
    assert committed_1 is True
    assert committed_2 is True

    model_v3 = _model_with_amount_type("INT")
    model_v3["metrics"] = [{"unique_name": "Total Sales", "expression": "SUM(amount)"}]
    committed_3, s3 = repo.commit_model("proj-reuse-2", model_v3)
    assert committed_3 is True

    diff_rows = repo.compare_versions("proj-reuse-2", s1, s3)
    assert diff_rows

    dataset_rows = [row for row in diff_rows if row["object"] == "dataset.Orders"]
    assert dataset_rows
    assert "VARCHAR" in dataset_rows[0]["previous_version"]
    assert "INT" in dataset_rows[0]["new_version"]
