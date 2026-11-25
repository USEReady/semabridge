"""Parametrized stage tests over [pbix, fabric] for Stage (b) Fingerprinting."""

from __future__ import annotations

from pathlib import Path
import pytest

from semabridge.sources.base import get_source_adapter


@pytest.mark.parametrize("source_type", ["pbix", "fabric"])
def test_fingerprint_produces_deterministic_hash(source_type: str, tmp_path: Path):
    adapter = get_source_adapter(source_type)
    if source_type == "fabric":
        payload = {"definition": {"model": {"tables": [{"name": "Sales"}]}}}
        fp1 = adapter.fingerprint(payload)
        fp2 = adapter.fingerprint(payload)
        assert fp1 and fp1.startswith("fabric-sha256:")
        assert fp1 == fp2
    elif source_type == "pbix":
        sample = tmp_path / "model.pbix"
        sample.write_bytes(b"PK\x03\x04test_pbix_binary_data")
        fp1 = adapter.fingerprint(str(sample))
        fp2 = adapter.fingerprint(str(sample))
        assert fp1 and fp1.startswith("pbix-sha256:")
        assert fp1 == fp2


@pytest.mark.parametrize("source_type", ["pbix", "fabric"])
def test_fingerprint_changes_when_content_changes(source_type: str, tmp_path: Path):
    adapter = get_source_adapter(source_type)
    if source_type == "fabric":
        fp1 = adapter.fingerprint({"definition": {"model": {"tables": [{"name": "A"}]}}})
        fp2 = adapter.fingerprint({"definition": {"model": {"tables": [{"name": "B"}]}}})
        assert fp1 != fp2
    elif source_type == "pbix":
        f1 = tmp_path / "f1.pbix"
        f2 = tmp_path / "f2.pbix"
        f1.write_bytes(b"content 1")
        f2.write_bytes(b"content 2")
        fp1 = adapter.fingerprint(str(f1))
        fp2 = adapter.fingerprint(str(f2))
        assert fp1 != fp2
