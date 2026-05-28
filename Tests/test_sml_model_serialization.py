from semabridge.sml.models import SMLModel


def test_sml_model_supports_model_dump_and_json():
    model = SMLModel(unique_name="model")

    dumped = model.model_dump(mode="json")
    assert dumped["unique_name"] == "model"
    assert dumped["source_platform"] == "snowflake"
    assert dumped["datasets"] == []

    json_text = model.model_dump_json()
    assert '"unique_name": "model"' in json_text
