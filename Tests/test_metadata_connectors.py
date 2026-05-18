import json

from semabridge.connectors.metadata_connectors import TmdlConnector


def _write_tree(root, tree: dict) -> None:
    for rel_path, content in tree.items():
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content), encoding="utf-8")


def _sample_tmdl_tree() -> dict:
    return {
        "manifest.json": {
            "name": "SalesModel",
            "description": "Sales semantic model",
            "version": "1.0",
        },
        "tables/Sales.json": {
            "name": "Sales",
            "label": "Sales",
            "description": "",
            "columns": [
                {"name": "Amount", "label": "Amount", "dataType": "double", "description": ""},
            ],
            "measures": [
                {"name": "Total Sales", "label": "Total Sales", "expression": "SUM('Sales'[Amount])"},
            ],
        },
        "relationships.json": {"relationships": []},
    }


def test_tmdl_connector_parses_filesystem_tree(tmp_path):
    tmdl_dir = tmp_path / "tmdl_example"
    _write_tree(tmdl_dir, _sample_tmdl_tree())

    connector = TmdlConnector()
    osi, sml = connector.parse_to_sml({
        "tmdl_path": str(tmdl_dir),
        "workspace_id": "ws-1",
        "dataset_id": "ds-1",
        "display_name": "SalesModel",
    })

    assert connector.source_format == "TMDL"
    assert osi.unique_name == "SalesModel"
    payload = sml.model_dump(mode="json")
    assert payload["unique_name"] == "SalesModel"
    assert payload["datasets"]


def test_tmdl_connector_can_build_from_sml_and_round_trip(tmp_path):
    connector = TmdlConnector()
    _, sml_model = connector.parse_to_sml({
        "tmdl": _sample_tmdl_tree(),
        "workspace_id": "ws-1",
        "dataset_id": "ds-1",
        "display_name": "SalesModel",
    })

    tmdl_tree = connector.build_from_sml(sml_model)
    assert "manifest.json" in tmdl_tree
    assert any(path.startswith("tables/") for path in tmdl_tree)
    assert any(path.endswith("Sales.json") for path in tmdl_tree if path.startswith("tables/"))

    tmdl_dir = tmp_path / "tmdl_round_trip"
    _write_tree(tmdl_dir, tmdl_tree)
    assert (tmdl_dir / "manifest.json").exists()
    assert (tmdl_dir / "tables" / "Sales.json").exists()
