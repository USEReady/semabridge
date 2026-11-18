"""Parametrized stage tests over [pbix, fabric] for Stage (c) Extraction."""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from semabridge.sources.base import (
    ModelIdentity,
    SourceError,
    get_source_adapter,
)


@pytest.mark.parametrize("source_type", ["pbix", "fabric"])
def test_extraction_returns_model_definition(source_type: str, monkeypatch):
    adapter = get_source_adapter(source_type)
    if source_type == "fabric":
        fake_extractor = MagicMock()
        fake_extractor.get_model_definition.return_value = {
            "model": {"name": "FabricModel", "tables": [{"name": "T1"}]}
        }
        ident = ModelIdentity(
            source_type="fabric",
            model_id="guid-1234",
            display_name="FabricModel",
            workspace_id="ws-1234",
        )
        tmsl = adapter.extract_model(ident, extractor=fake_extractor)
        assert tmsl["model"]["name"] == "FabricModel"
        fake_extractor.get_model_definition.assert_called_once_with("guid-1234")
    elif source_type == "pbix":
        fake_connector = MagicMock()
        fake_connector.discover.return_value = {"raw_tmsl": None}
        fake_connector.extract.return_value = {
            "model": {"name": "PbixModel", "tables": [{"name": "T1"}]}
        }
        monkeypatch.setattr(
            "semabridge.connectors.local_pbix_connector.LocalPBIXConnector",
            lambda *args, **kwargs: fake_connector,
        )
        ident = ModelIdentity(
            source_type="pbix",
            model_id="dummy.pbix",
            display_name="PbixModel",
        )
        tmsl = adapter.extract_model(ident)
        assert tmsl["model"]["name"] == "PbixModel"


@pytest.mark.parametrize("source_type", ["pbix", "fabric"])
def test_extraction_fails_on_empty_model_definition(source_type: str, monkeypatch):
    adapter = get_source_adapter(source_type)
    if source_type == "fabric":
        fake_extractor = MagicMock()
        fake_extractor.get_model_definition.return_value = None
        ident = ModelIdentity(
            source_type="fabric",
            model_id="guid-empty",
            display_name="EmptyFabric",
            workspace_id="ws-1234",
        )
        with pytest.raises(SourceError, match="returned no definition"):
            adapter.extract_model(ident, extractor=fake_extractor)
    elif source_type == "pbix":
        fake_connector = MagicMock()
        fake_connector.discover.return_value = {"raw_tmsl": None}
        fake_connector.extract.return_value = None
        monkeypatch.setattr(
            "semabridge.connectors.local_pbix_connector.LocalPBIXConnector",
            lambda *args, **kwargs: fake_connector,
        )
        ident = ModelIdentity(
            source_type="pbix",
            model_id="empty.pbix",
            display_name="EmptyPbix",
        )
        with pytest.raises(SourceError, match="Could not extract DataModelSchema"):
            adapter.extract_model(ident)
