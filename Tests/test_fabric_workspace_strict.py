"""Fabric workspace values: a GUID as-is, or an exact name resolved with the project's credentials.

Never the stored credential's or the .env workspace (the old alias fallback).
Recorded workspace listing only (Tests/fixtures/fabric/workspaces.json).
"""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).parent
WORKSPACES = json.loads((HERE / "fixtures/fabric/workspaces.json").read_text())["value"]
TMSL = json.loads((HERE / "characterization/fixtures/model.tmsl.json").read_text())
STORED_WS = "dddddddd-0000-0000-0000-00000000beef"  # what the old alias fallback would have switched to
DS = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def extract(monkeypatch):
    import semabridge.connectors.fabric_extractor as fe
    from semabridge.core.behavior import ConnectorBehavior
    from semabridge.core.engine.context import RunContext
    from semabridge.core.engine.extraction.fabric import _extract_fabric
    from semabridge.core.settings import FabricConfig

    used = {}

    class _Recorded(fe.FabricExtractor):
        def list_workspaces(self):
            return copy.deepcopy(WORKSPACES)

        def _resolve_model_id_exact(self, ref):
            return DS

        def get_model_definition(self, ds):
            used["workspace"] = self.config.workspace_id
            return copy.deepcopy(TMSL)

        def get_table_row_counts(self, ds):
            return {}

        def get_model_display_name(self, ds):
            return "Sales"

    monkeypatch.setattr(fe, "FabricExtractor", _Recorded)
    monkeypatch.setattr("semabridge.repository.credential_manager.CredentialManager.get_credentials",
                        lambda self, *a, **k: {"workspace_id": STORED_WS})

    def _run(workspace_value):
        ctx = RunContext(project_id="p", run_id="r", start_time=time.time(), source_type="fabric",
                         behavior=ConnectorBehavior(),
                         config=SimpleNamespace(fabric=FabricConfig(workspace_id=STORED_WS), source=None))
        ctx.fabric_access_token = "tok"
        sf = _extract_fabric(SimpleNamespace(_record_step=lambda *a: None), ctx, DS, workspace_value)
        return sf, used.get("workspace")

    return _run


def test_workspace_name_resolves_to_its_guid_and_unknown_name_fails(extract):
    from semabridge.core.engine.exceptions import ExtractionError

    sf, used = extract("Sales Analytics")  # a valid exact name
    assert used == sf.workspace_id == "aaaaaaaa-0000-0000-0000-000000000001"

    with pytest.raises(ExtractionError, match="'semabridge-local' is not a workspace id and no workspace with exactly that name"):
        extract("semabridge-local")  # used to silently switch to the stored / .env workspace
    with pytest.raises(ExtractionError, match="'Shared' matches 2 workspaces"):
        extract("Shared")
    with pytest.raises(ExtractionError, match="no workspace with exactly that name"):
        extract("sales analytics")  # exact name only, no case-folding

    sf, used = extract("aaaaaaaa-0000-0000-0000-000000000002")  # a GUID is used as-is
    assert used == "aaaaaaaa-0000-0000-0000-000000000002"
