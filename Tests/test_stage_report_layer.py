"""Parametrized stage tests over [pbix, fabric] for Stage (d) Report Layer."""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from semabridge.sources.base import (
    ModelIdentity,
    get_source_adapter,
)
from semabridge.sources.fabric.report_layer import ReportLayer


@pytest.mark.parametrize("source_type", ["pbix", "fabric"])
def test_extract_report_layer_returns_report_layer_dataclass(source_type: str, monkeypatch):
    adapter = get_source_adapter(source_type)
    if source_type == "fabric":
        fake_extractor = MagicMock()
        monkeypatch.setattr(
            "semabridge.sources.fabric.report_layer.fetch_report_layer",
            lambda ext, ws, ds, tmsl: ReportLayer(
                field_aliases=[{"table": "T", "field": "F", "alias": "A"}],
                unresolved=[],
                ambiguous=[],
                reports=["Report1"],
                warnings=[],
            ),
        )
        ident = ModelIdentity("fabric", "ds-1", "FabricModel", "ws-1")
        layer = adapter.extract_report_layer(ident, extractor=fake_extractor)
        assert isinstance(layer, ReportLayer)
        assert len(layer.field_aliases) == 1
        assert layer.reports == ["Report1"]
    elif source_type == "pbix":
        fake_connector = MagicMock()
        fake_connector.discover.return_value = {
            "field_aliases": [{"table": "Sales", "field": "Units", "alias": "Total Units"}],
            "unresolved_report_field_references": [],
            "ambiguous_report_aliases": [],
        }
        monkeypatch.setattr(
            "semabridge.connectors.local_pbix_connector.LocalPBIXConnector",
            lambda *args, **kwargs: fake_connector,
        )
        ident = ModelIdentity("pbix", "sample.pbix", "SampleModel")
        layer = adapter.extract_report_layer(ident)
        assert isinstance(layer, ReportLayer)
        assert len(layer.field_aliases) == 1
        assert layer.field_aliases[0]["alias"] == "Total Units"
        assert layer.reports == ["sample.pbix"]
