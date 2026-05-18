import os
import sys
from pathlib import Path

import pytest

from semabridge.connectors.metadata_connectors import TmdlConnector


def test_tmdl_parse_raises_on_invalid_path(tmp_path):
    """Providing a non-existent tmdl_path should raise ValueError."""
    tmdl = TmdlConnector()
    bad_path = tmp_path / "does_not_exist"
    with pytest.raises(ValueError):
        tmdl.parse_to_sml({"tmdl_path": str(bad_path)})


def test_tmdl_parse_raises_when_no_payload():
    """When neither tmdl payload nor tmdl_path is provided, parse_to_sml must raise."""
    tmdl = TmdlConnector()
    with pytest.raises(ValueError):
        tmdl.parse_to_sml({})


def test_build_from_sml_produces_table_tree():
    """Ensure build_from_sml produces a dict with expected keys for tables."""
    tmdl_conn = TmdlConnector()
    _, sml = tmdl_conn.parse_to_sml({
        "tmdl": {
            "manifest.json": {"name": "TreeModel", "description": "", "version": "1.0"},
            "tables/Sales.json": {"name": "Sales", "columns": [{"name": "Amount", "dataType": "double"}]},
        },
        "workspace_id": "ws-1",
        "dataset_id": "ds-1",
        "display_name": "TreeModel",
    })

    tree = tmdl_conn.build_from_sml(sml)
    # Expect manifest and at least one tables/...json entry
    assert "manifest.json" in tree
    table_keys = [k for k in tree.keys() if k.startswith("tables/")]
    assert len(table_keys) >= 1
