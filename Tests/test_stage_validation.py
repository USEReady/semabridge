"""Parametrized stage tests over [pbix, fabric] for Stage (a) Validation."""

from __future__ import annotations

from pathlib import Path
import pytest

from semabridge.sources.base import (
    ModelIdentity,
    SourceConfigError,
    SourceNotFoundError,
    get_source_adapter,
)


@pytest.mark.parametrize("source_type", ["pbix", "fabric"])
def test_validation_adapter_returns_expected_source_type(source_type: str):
    adapter = get_source_adapter(source_type)
    assert adapter.source_type == source_type


@pytest.mark.parametrize("source_type", ["pbix", "fabric"])
def test_validation_rejects_missing_or_invalid_target(source_type: str):
    adapter = get_source_adapter(source_type)
    if source_type == "fabric":
        # Missing workspace raises SourceConfigError
        with pytest.raises(SourceConfigError, match="has no workspace"):
            adapter.validate_and_resolve(["missing-guid"], workspace_id="")
    elif source_type == "pbix":
        # Missing local file raises SourceNotFoundError
        with pytest.raises(SourceNotFoundError, match="PBIX file not found"):
            adapter.validate_and_resolve(["non_existent_file_path_12345.pbix"])


@pytest.mark.parametrize("source_type", ["pbix", "fabric"])
def test_validation_enforces_max_models_cap(source_type: str):
    adapter = get_source_adapter(source_type)
    refs = [f"model_{i}" for i in range(15)]
    if source_type == "fabric":
        fake_catalog = [
            {"id": f"00000000-0000-0000-0000-{i:012d}", "displayName": f"model_{i}"}
            for i in range(15)
        ]
        with pytest.raises(SourceConfigError, match="at most 10 are supported"):
            adapter.validate_and_resolve(
                refs,
                workspace_id="fake-ws-id",
                max_models=10,
                lister=lambda ws, creds: fake_catalog,
            )
    elif source_type == "pbix":
        with pytest.raises(SourceConfigError, match="Up to 10 PBIX files are supported"):
            adapter.validate_and_resolve(refs, max_models=10)


@pytest.mark.parametrize("source_type", ["pbix", "fabric"])
def test_validation_resolves_valid_references(source_type: str, tmp_path: Path):
    adapter = get_source_adapter(source_type)
    if source_type == "fabric":
        # Fake workspace model lister returning 1 model
        fake_models = [{"id": "11111111-2222-3333-4444-555555555555", "displayName": "Sales Model"}]
        check = adapter.validate_and_resolve(
            ["Sales Model"],
            workspace_id="test-workspace",
            max_models=10,
            lister=lambda ws, creds: fake_models,
        )
        assert len(check.identities) == 1
        ident = check.identities[0]
        assert ident.source_type == "fabric"
        assert ident.model_id == "11111111-2222-3333-4444-555555555555"
        assert ident.display_name == "Sales Model"
        assert ident.workspace_id == "test-workspace"
    elif source_type == "pbix":
        dummy_pbix = tmp_path / "MyReport.pbix"
        dummy_pbix.write_bytes(b"dummy pbix content")
        check = adapter.validate_and_resolve([str(dummy_pbix)], max_models=10)
        assert len(check.identities) == 1
        ident = check.identities[0]
        assert ident.source_type == "pbix"
        assert ident.model_id == str(dummy_pbix.resolve())
        assert ident.display_name == "MyReport"
        assert ident.workspace_id is None
