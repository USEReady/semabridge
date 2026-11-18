"""Parametrized stage tests over [pbix, fabric] for Stage (f) Live Drift."""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from semabridge.sources.base import (
    ModelIdentity,
    SourceUnsupportedError,
    get_source_adapter,
)


@pytest.mark.parametrize("source_type", ["pbix", "fabric"])
def test_live_definition_support(source_type: str, monkeypatch):
    adapter = get_source_adapter(source_type)
    if source_type == "fabric":
        fake_extractor = MagicMock()
        fake_extractor.get_model_definition.return_value = {
            "model": {"name": "LiveModel", "tables": []}
        }
        ident = ModelIdentity("fabric", "ds-live", "LiveModel", "ws-live")
        tmsl = adapter.live_definition(ident, extractor=fake_extractor)
        assert tmsl["model"]["name"] == "LiveModel"
        fake_extractor.get_model_definition.assert_called_once_with("ds-live")
    elif source_type == "pbix":
        ident = ModelIdentity("pbix", "local.pbix", "LocalModel")
        with pytest.raises(SourceUnsupportedError, match="does not support live service drift"):
            adapter.live_definition(ident)
