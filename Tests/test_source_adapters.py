"""Tests for SemanticSourceAdapter, FabricAdapter, PbixAdapter, and adapter registry."""

from __future__ import annotations

import pytest

from semabridge.sources.base import (
    ModelIdentity,
    SemanticSourceAdapter,
    SourceConfigError,
    SourceNotFoundError,
    SourceUnsupportedError,
    get_source_adapter,
)
from semabridge.sources.fabric.adapter import FabricAdapter
from semabridge.sources.pbix.adapter import PbixAdapter


def test_registry_returns_adapters():
    fabric = get_source_adapter("fabric")
    assert isinstance(fabric, FabricAdapter)
    assert fabric.source_type == "fabric"

    pbix = get_source_adapter("pbix")
    assert isinstance(pbix, PbixAdapter)
    assert pbix.source_type == "pbix"


def test_registry_raises_on_unknown_type():
    with pytest.raises(SourceConfigError, match="Unknown or unsupported source type"):
        get_source_adapter("oracle")


def test_pbix_adapter_resolve_credentials():
    adapter = get_source_adapter("pbix")
    creds = adapter.resolve_credentials()
    assert creds.origin == "interactive"
    assert creds.access_token is None


def test_pbix_adapter_unsupported_operations():
    adapter = get_source_adapter("pbix")
    ident = ModelIdentity(source_type="pbix", model_id="dummy.pbix", display_name="dummy")

    with pytest.raises(SourceUnsupportedError, match="does not execute live COUNTROWS"):
        adapter.extract_stats(ident)

    with pytest.raises(SourceUnsupportedError, match="does not support live service drift"):
        adapter.live_definition(ident)


def test_fabric_adapter_requires_workspace_for_validation():
    adapter = get_source_adapter("fabric")
    with pytest.raises(SourceConfigError, match="has no workspace"):
        adapter.validate_and_resolve(["model-1"], workspace_id=None)
