"""Batch 5 item 3: a TMDL-only model definition fails with a clear reason (recorded response)."""

import json
from pathlib import Path

import pytest

RECORDED = json.loads((Path(__file__).parent / "fixtures/fabric/getdefinition_tmdl.json").read_text())


def test_tmdl_definition_raises_a_clear_error():
    from semabridge.connectors.fabric_extractor import FabricExtractionError, FabricExtractor
    from semabridge.core.settings import FabricConfig

    ext = FabricExtractor(FabricConfig(workspace_id="ws"), access_token="t")
    with pytest.raises(FabricExtractionError) as exc:
        ext._parse_definition_response(RECORDED)
    msg = str(exc.value)
    assert "TMDL format" in msg and "no model.bim" in msg and "cannot read TMDL yet" in msg
    assert "definition/tables/Sales.tmdl" in msg and "3 .tmdl part(s)" in msg
    with pytest.raises(FabricExtractionError, match="model.bim not found"):  # neither format: unchanged message
        ext._parse_definition_response({"definition": {"parts": [{"path": "definition.pbism", "payload": ""}]}})
