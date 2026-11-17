"""A thin report (live connection, no data model) is classified, not reported as corrupt."""
import json
import zipfile

import pytest

from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
from semabridge.core.exceptions import PBIXLiveConnectionError, PBIXParsingError


def _pbix(path, connections=None, extra=None):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Version", "1.28")
        if connections is not None:
            z.writestr("Connections", json.dumps(connections))
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return path


def _extract(path):
    return LocalPBIXConnector({"pbix_path": str(path), "path": str(path), "file_path": str(path)}).extract()


def test_thin_report_raises_live_connection_with_model_reference(tmp_path):
    p = _pbix(tmp_path / "thin.pbix", {"Version": 3, "Connections": [{
        "Name": "EntityDataSource", "ConnectionType": "pbiServiceLive", "PbiModelDatabaseName": "abc-123"}]})
    with pytest.raises(PBIXLiveConnectionError) as err:
        _extract(p)
    assert err.value.model_reference == "abc-123"
    assert err.value.reason_code == "LIVE_CONNECTION"
    assert isinstance(err.value, PBIXParsingError)          # existing callers still fail safely


def test_model_reference_from_connection_string(tmp_path):
    p = _pbix(tmp_path / "thin.pbix", {"Version": 3, "Connections": [{
        "ConnectionString": "Data Source=pbiazure://api.powerbi.com;Initial Catalog=sales-model;Integrated Security=x"}]})
    with pytest.raises(PBIXLiveConnectionError) as err:
        _extract(p)
    assert err.value.model_reference == "sales-model"


def test_file_without_model_or_connection_is_still_a_plain_parsing_error(tmp_path):
    p = _pbix(tmp_path / "broken.pbix")
    with pytest.raises(PBIXParsingError) as err:
        _extract(p)
    assert not isinstance(err.value, PBIXLiveConnectionError)


def test_published_model_artifacts_alone_are_not_a_live_connection(tmp_path):
    # RemoteArtifacts only records where a full PBIX was published; without a model it is still broken.
    p = _pbix(tmp_path / "x.pbix", {"Version": 3, "RemoteArtifacts": [{"DatasetId": "d1"}]})
    with pytest.raises(PBIXParsingError) as err:
        _extract(p)
    assert not isinstance(err.value, PBIXLiveConnectionError)


def test_discover_classifies_thin_report_without_raising(tmp_path):
    p = _pbix(tmp_path / "thin.pbix", {"Version": 3, "Connections": [{"PbiModelDatabaseName": "abc-123"}]})
    result = LocalPBIXConnector({"pbix_path": str(p)}).discover()
    assert result["tables"] == []
    assert result["metadata"]["reason_code"] == "LIVE_CONNECTION"
    assert result["metadata"]["model_reference"] == "abc-123"
