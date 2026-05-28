from semabridge.transformers.payload_to_osi import payload_to_osi


def test_payload_to_osi_infers_source_column_when_metric_name_matches_dataset_column():
    payload = {
        "model_name": "model",
        "datasets": [
            {
                "unique_name": "Date",
                "columns": [
                    {"unique_name": "MonthNo", "data_type": "integer"},
                ],
            }
        ],
        "metrics": [
            {
                "unique_name": "MonthNo",
                "dataset": "Date",
                "aggregation": "sum",
            }
        ],
    }

    osi = payload_to_osi(payload)
    assert len(osi.metrics) == 1
    assert osi.metrics[0].source_column == "MonthNo"
