from types import SimpleNamespace
from pathlib import Path

from semabridge.core.execution_engine import ExecutionEngine


def test_mapping_overrides_apply_metrics_from_in_memory_payload():
    metric = SimpleNamespace(unique_name="Rep Sfdc Checklist C - Sum of Daily Delivery Ld Rate (%)", label="Rep Sfdc Checklist C - Sum of Daily Delivery Ld Rate (%)")
    dataset = SimpleNamespace(unique_name="REP_SFDC_CHECKLIST__C", columns=[])
    sml_model = SimpleNamespace(datasets=[dataset], metrics=[metric])

    payload = {
        "mappings_overrides": [
            {
                "source_path": "metrics.Rep Sfdc Checklist C - Sum of Daily Delivery Ld Rate (%)",
                "target_name": "REP_SFDC_CHECKLIST_C_SUM_OF_DAILY_DELIVERY_LD_RATE_8AF4",
            }
        ]
    }

    ExecutionEngine._apply_mapping_overrides_from_config(
        sml_model,
        Path("C:/non-existent-config.yaml"),
        config_payload=payload,
    )

    assert metric.unique_name == "REP_SFDC_CHECKLIST_C_SUM_OF_DAILY_DELIVERY_LD_RATE_8AF4"
    assert metric.label == "REP_SFDC_CHECKLIST_C_SUM_OF_DAILY_DELIVERY_LD_RATE_8AF4"
