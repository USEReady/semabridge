"""The orchestrator adapter's local path runs the real ExecutionEngine end to end on a small fixture.

Only the network boundaries are replaced: the Fabric model definition comes from
the characterization TMSL fixture (recorded model), the identity token is
stubbed, and the two live-Snowflake reads (table-binding preview, live DDL) use
the offline equivalents. Everything else -- config, conversion, ontology,
persistence (in-memory test DB), finalize -- is the real pipeline.
"""

from __future__ import annotations

import asyncio
import copy
import json
import uuid
from pathlib import Path

WS = "aaaaaaaa-0000-0000-0000-000000000001"
TMSL = json.loads((Path(__file__).parent / "characterization/fixtures/model.tmsl.json").read_text())


def test_adapter_local_path_completes_on_fixture(monkeypatch):
    import semabridge.auth.token_resolver as tr
    import semabridge.connectors.fabric_extractor as fe
    import semabridge.core.engine.deployment.snowflake as dep
    import semabridge.sources.fabric.dax_target as dt
    from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
    from semabridge.orchestration.orchestrator_adapter import run_sync

    # The run persists its snapshot: make sure the (in-memory test) schema exists
    # on this worker, as other persistence tests do.
    from semabridge.repository.orm.base import Base
    from semabridge.repository.orm.session_factory import db_manager

    Base.metadata.create_all(bind=db_manager.get_engine(), checkfirst=True)

    project_id = f"adapter-fixture-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(dt, "load_project_config", lambda pid: {
        "project_id": pid, "source": {"type": "fabric", "identity_id": "acct-1", "workspace_id": WS}})
    monkeypatch.setattr(tr, "resolve_fabric_access_token", lambda header, identity_id=None: "tok")
    fetched = []

    class _RecordedModel(fe.FabricExtractor):
        def _resolve_model_id_exact(self, ref):
            return "11111111-1111-1111-1111-111111111111"

        def get_model_definition(self, dataset_id):
            fetched.append((dataset_id, self.config.workspace_id, self._access_token))
            return copy.deepcopy(TMSL)

        def get_table_row_counts(self, dataset_id):
            return {}

        def get_model_display_name(self, dataset_id):
            return "Characterization Model"

    monkeypatch.setattr(fe, "FabricExtractor", _RecordedModel)
    monkeypatch.setattr(dep, "preview_table_bindings", lambda context: {"bindings": [], "creates": []})
    monkeypatch.setattr(SnowflakeEmitter, "generate_ddls_live", lambda self, model: self.generate_ddls(model))

    result = asyncio.run(run_sync("tenant", "Characterization Model", "CHAR_DB", "CHAR_SCHEMA",
                                  mode="local", project_id=project_id, deploy=False))

    assert result.status == "completed", result.error
    assert result.error == "" and result.deployed is False  # dry run: nothing deployed
    # the engine read the model with the job's workspace and identity token
    assert fetched == [("11111111-1111-1111-1111-111111111111", WS, "tok")]
