"""Tests for deterministic relationship ordering in conversion output."""

from __future__ import annotations

import pytest

from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter


@pytest.fixture
def sample_tmsl():
    return {
        "model": {
            "tables": [
                {"name": "Sales", "columns": [{"name": "Key1"}, {"name": "Key2"}]},
                {"name": "Product", "columns": [{"name": "ProductKey"}]},
                {"name": "Date", "columns": [{"name": "DateKey"}]},
            ],
            "relationships": [
                {
                    "name": "r2",
                    "fromTable": "Sales",
                    "fromColumn": "Key1",
                    "toTable": "Product",
                    "toColumn": "ProductKey",
                },
                {
                    "name": "r1",
                    "fromTable": "Sales",
                    "fromColumn": "Key2",
                    "toTable": "Date",
                    "toColumn": "DateKey",
                },
            ],
        }
    }


def test_relationships_default_off_preserves_source_order(sample_tmsl, monkeypatch):
    monkeypatch.delenv("deterministic_relationship_order", raising=False)
    monkeypatch.delenv("DETERMINISTIC_RELATIONSHIP_ORDER", raising=False)
    conv = TMSLToOSIConverter()
    osi = conv.to_osi({"tmsl": sample_tmsl, "dataset_id": "test_ds"})
    assert [r.to_dataset for r in osi.relationships] == ["Product", "Date"]


def test_relationships_explicit_off_preserves_source_order(sample_tmsl, monkeypatch):
    monkeypatch.setenv("deterministic_relationship_order", "0")
    conv = TMSLToOSIConverter()
    osi = conv.to_osi({"tmsl": sample_tmsl, "dataset_id": "test_ds"})
    assert [r.to_dataset for r in osi.relationships] == ["Product", "Date"]


def test_relationships_enabled_on_sorts_deterministically(sample_tmsl, monkeypatch):
    monkeypatch.setenv("deterministic_relationship_order", "1")
    conv = TMSLToOSIConverter()
    osi = conv.to_osi({"tmsl": sample_tmsl, "dataset_id": "test_ds"})
    assert [r.to_dataset for r in osi.relationships] == ["Date", "Product"]
