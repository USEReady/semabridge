import base64
import pytest
from semabridge.connectors.fabric_extractor import FabricExtractor
from semabridge.core.source_format import SourceFormat, from_fabric_tmdl
from semabridge.core.settings import FabricConfig


@pytest.fixture
def mock_config():
    return FabricConfig(
        tenant_id="test-tenant",
        client_id="test-client",
        client_secret="test-secret",
        workspace_id="11111111-2222-3333-4444-555555555555",
    )


@pytest.fixture
def mock_fabric_api(monkeypatch):
    tmdl_parts = [
        {
            "path": "definition/model.tmdl",
            "payload": base64.b64encode(b"model 'TestModel' {}").decode("utf-8"),
            "payloadType": "InlineBase64"
        },
        {
            "path": "definition/tables/Sales.tmdl",
            "payload": base64.b64encode(b"table 'Sales' {}").decode("utf-8"),
            "payloadType": "InlineBase64"
        }
    ]
    
    class FakeResponse:
        def __init__(self, status_code, json_data, headers=None):
            self.status_code = status_code
            self._json_data = json_data
            self.headers = headers or {}
            
        def json(self):
            return self._json_data
            
        def raise_for_status(self):
            pass

    def fake_request(method, url, **kwargs):
        if "getDefinition" in url:
            return FakeResponse(200, {"definition": {"parts": tmdl_parts}})
        return FakeResponse(200, {"status": "Succeeded"})

    monkeypatch.setattr("semabridge.connectors.fabric_extractor.requests.request", fake_request)


class TestTMDLExtraction:

    def test_get_model_definition_returns_tmdl_files(
        self,
        mock_fabric_api,
        mock_config
    ):
        extractor = FabricExtractor(mock_config, access_token="mock-token")

        result = extractor.get_model_definition(
            "22222222-3333-4444-5555-666666666666"
        )

        assert isinstance(result, dict)
        assert "definition/model.tmdl" in result
        assert "definition/tables/Sales.tmdl" in result

    def test_source_format_uses_tmdl_files(self):

        tmdl_files = {
            "definition/model.tmdl":
                "model 'TestModel' {...}"
        }

        sf = from_fabric_tmdl(
            workspace_id="ws-123",
            dataset_id="ds-456",
            dataset_name="Test Model",
            tmdl_files=tmdl_files
        )

        assert sf.tmdl_files == tmdl_files
        assert sf.format_used == "TMDL"
        assert sf.source_type == "fabric"

    def test_validate_format_requires_tmdl_files(self):

        sf = SourceFormat(
            source_type="fabric",
            tmdl_files={}
        )

        with pytest.raises(
            ValueError,
            match="fabric source requires tmdl_files"
        ):
            sf.validate_format()
