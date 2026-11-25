"""Tests for deterministic relationship ordering in conversion output."""

from __future__ import annotations

import os
from unittest.mock import patch
import pytest

from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter


def test_relationships_deterministic_sorting():
    tmsl = {
        "model": {
            "tables": [
                {"name": "Sales", "columns": [{"name": "Key1"}, {"name": "Key2"}]},
                {"name": "Product", "columns": [{"name": "ProductKey"}]},
                {"name": "Date", "columns": [{"name": "DateKey"}]},
                {"name": "Geo", "columns": [{"name": "GeoKey"}]},
            ],
            "relationships": [
                {
                    "name": "r3",
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

    # Default without flag preserves source order
    conv = TMSLToOSIConverter()
    osi_default = conv.to_osi({"tmsl": tmsl, "dataset_id": "test_ds"})
    assert [r.to_dataset for r in osi_default.relationships] == ["Product", "Date"]

    # With deterministic_relationships enabled, relationships are sorted deterministically
    osi_sorted = conv.to_osi({"tmsl": tmsl, "dataset_id": "test_ds", "deterministic_relationships": True})
    assert [r.to_dataset for r in osi_sorted.relationships] == ["Date", "Product"]


def test_relationships_deterministic_via_env_flag(monkeypatch):
    monkeypatch.setenv("SEMABRIDGE_DETERMINISTIC_RELATIONSHIPS", "1")
    tmsl = {
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

    conv = TMSLToOSIConverter()
    osi = conv.to_osi({"tmsl": tmsl, "dataset_id": "test_ds"})
    assert [r.to_dataset for r in osi.relationships] == ["Date", "Product"]
