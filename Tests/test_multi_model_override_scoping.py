"""Mapping overrides saved by the multi-model dry run are tagged with the
model they belong to and must only reach that model's sync job."""

from semabridge.api.services.sync_execution_service import _config_with_model_scoped_overrides


def _row(source_path, target_name, model=None):
    row = {"source_path": source_path, "target_name": target_name}
    if model is not None:
        row["model"] = model
    return row


def test_untagged_overrides_return_the_same_config_object():
    config = {"mappings_overrides": [_row("metrics.Total", "TOTAL")]}
    job = {"pbix_path": "C:/data/Sales.pbix", "model_label": "Sales"}
    assert _config_with_model_scoped_overrides(config, job) is config


def test_config_without_overrides_is_untouched():
    config = {"source": {"type": "pbix"}}
    assert _config_with_model_scoped_overrides(config, {"model_label": "A"}) is config


def test_tagged_overrides_only_reach_their_own_pbix_model():
    config = {
        "mappings_overrides": [
            _row("metrics.Total", "TOTAL_A", model="uploads/abc/RegionA.pbix"),
            _row("metrics.Total", "TOTAL_B", model="uploads/abc/RegionB.pbix"),
            _row("Sales.Amount", "AMOUNT"),
        ]
    }
    job_a = {"pbix_path": "C:\\srv\\uploads\\abc\\RegionA.pbix", "model_label": "RegionA"}
    job_b = {"pbix_path": "C:\\srv\\uploads\\abc\\RegionB.pbix", "model_label": "RegionB"}

    scoped_a = _config_with_model_scoped_overrides(config, job_a)["mappings_overrides"]
    scoped_b = _config_with_model_scoped_overrides(config, job_b)["mappings_overrides"]

    assert [r["target_name"] for r in scoped_a] == ["TOTAL_A", "AMOUNT"]
    assert [r["target_name"] for r in scoped_b] == ["TOTAL_B", "AMOUNT"]
    # The shared config is never mutated.
    assert len(config["mappings_overrides"]) == 3


def test_tagged_overrides_match_fabric_models_by_display_name():
    config = {
        "mappings_overrides": [
            _row("metrics.Revenue", "REV_FIN", model="Finance"),
            _row("metrics.Revenue", "REV_OPS", model="Operations"),
        ]
    }
    job = {"dataset_id": "1f2e-guid", "model_label": "Finance", "resolved_display_name": "Finance"}
    scoped = _config_with_model_scoped_overrides(config, job)["mappings_overrides"]
    assert [r["target_name"] for r in scoped] == ["REV_FIN"]


# Every upload stores a file under a fresh 32-hex prefix, and the dry run
# re-uploads. Overrides saved from a dry run are therefore tagged with a
# different path than the project's own copy of the same model, and must
# still reach it -- otherwise every resolved collision comes back on sync.
_DRY_A = "/tmp/uploads/6d2f082dcbd14b428deacaec04528c3e_Sales Model.pbix"
_PROJ_A = "/tmp/uploads/d30a1689e1c249a5bb7ef50cab10327f_Sales Model.pbix"
_DRY_B = "/tmp/uploads/482f2f7b7c0343609b66d473f9eafbbd_Finance.pbix"
_PROJ_B = "/tmp/uploads/30d1ccbb97384e67b65ab601babd9af4_Finance.pbix"


def test_overrides_from_dry_run_upload_reach_project_upload_of_same_model():
    config = {
        "source": {"type": "pbix", "mode": "multiple", "models": [_PROJ_A, _PROJ_B]},
        "mappings_overrides": [
            _row("datasets.Geo.columns.State", "GEO_STATE", model=_DRY_A),
            _row("datasets.Account.columns.REGION", "ACCOUNT_REGION", model=_DRY_B),
        ],
    }
    scoped_a = _config_with_model_scoped_overrides(config, {"pbix_path": _PROJ_A, "model_label": "Sales Model"})
    scoped_b = _config_with_model_scoped_overrides(config, {"pbix_path": _PROJ_B, "model_label": "Finance"})
    assert [r["target_name"] for r in scoped_a["mappings_overrides"]] == ["GEO_STATE"]
    assert [r["target_name"] for r in scoped_b["mappings_overrides"]] == ["ACCOUNT_REGION"]


def test_prefix_free_match_is_skipped_when_two_configured_models_share_a_file_name():
    other_copy = "/tmp/uploads/11111111111111111111111111111111_Sales Model.pbix"
    config = {
        "source": {"type": "pbix", "models": [_PROJ_A, other_copy]},
        "mappings_overrides": [
            _row("datasets.Geo.columns.State", "EXACT", model=_PROJ_A),
            _row("datasets.Geo.columns.State", "OTHER", model=other_copy),
            _row("datasets.Geo.columns.Zip", "AMBIGUOUS", model=_DRY_A),
        ],
    }
    scoped = _config_with_model_scoped_overrides(config, {"pbix_path": _PROJ_A, "model_label": "Sales Model"})
    # Exact tags still route precisely; the stale tag can't be attributed.
    assert [r["target_name"] for r in scoped["mappings_overrides"]] == ["EXACT"]


def test_dry_run_starts_from_saved_names_of_its_own_model():
    from semabridge.api.controllers.mappings_controller import _saved_names_for_dry_run

    project_cfg = {
        "source": {"type": "pbix", "mode": "multiple", "models": [_PROJ_A, _PROJ_B]},
        "mappings_overrides": [
            _row("datasets.Geo.columns.State", "GEO_STATE", model=_DRY_A),
            _row("datasets.Geo.columns.Zip", "ZIP", model=_DRY_A),
            _row("datasets.Account.columns.REGION", "ACCOUNT_REGION", model=_DRY_B),
        ],
    }
    new_dry_run_upload = "/tmp/uploads/99999999999999999999999999999999_Sales Model.pbix"
    existing = _saved_names_for_dry_run(project_cfg, {"type": "pbix"}, [new_dry_run_upload], "snowflake")
    assert existing == {
        "datasets.Geo.columns.State": {"target_name": "GEO_STATE", "is_user_edited": True},
        "datasets.Geo.columns.Zip": {"target_name": "ZIP", "is_user_edited": False},
    }
    assert _saved_names_for_dry_run({}, {"type": "pbix"}, [new_dry_run_upload], "snowflake") == {}
