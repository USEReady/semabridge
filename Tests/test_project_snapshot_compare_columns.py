from semabridge.api.services.project_runs_impl import _diff_columns


def test_diff_columns_supports_unique_name_keys():
    left_cols = [{"unique_name": "amount", "data_type": "VARCHAR"}]
    right_cols = [{"unique_name": "amount", "data_type": "INT"}]

    rows = _diff_columns(left_cols, right_cols)
    assert rows
    assert rows[0]["name"] == "amount"
    assert rows[0]["status"] == "MODIFIED"
    assert rows[0]["previousType"] == "VARCHAR"
