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
