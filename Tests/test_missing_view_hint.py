from semabridge.ai import missing_view

RAW = ("[TABLE_OR_VIEW_NOT_FOUND] The table or view `workspace`.`default`.`Store_Sales_2_metric_view` "
       "cannot be found. Verify the spelling and correctness of the schema and catalog. SQLSTATE: 42P01")


def test_missing_view_is_explained(monkeypatch):
    monkeypatch.setattr(missing_view, "_last_successful_sync", lambda pid: "2026-11-19")
    msg = missing_view.explain(RAW, "proj-dbt2")
    assert msg.startswith("The view workspace.default.Store_Sales_2_metric_view is not in Databricks.")
    assert "2026-11-19" in msg and "Re-sync the project" in msg
    assert "TABLE_OR_VIEW_NOT_FOUND" in msg


def test_other_errors_unchanged():
    assert missing_view.explain("PARSE_SYNTAX_ERROR near x", "p") == "PARSE_SYNTAX_ERROR near x"
    assert missing_view.explain(None, "p") is None
