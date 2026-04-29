from types import SimpleNamespace

import pytest

from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.execution_engine import DeploymentError, ExecutionEngine, RunContext


class _EmitterWithoutCortexMethod:
    def __init__(self, *_args, **_kwargs):
        pass

    def generate_ddls(self, _sml):
        return ["CREATE OR REPLACE VIEW test_view AS SELECT 1"]


class _EmitterWithDeploymentFailure:
    def __init__(self, *_args, **_kwargs):
        self.last_deployment_error = (
            "DDL[0] failed: 000904 (42000): SQL compilation error: "
            "invalid identifier 'ORDERS.QUANTITY'"
        )

    def deploy(self, _sml):
        return False


def test_convert_to_snowflake_target_falls_back_when_emitter_lacks_cortex_method(
    monkeypatch,
    tmp_path,
):
    engine = ExecutionEngine()
    context = RunContext(
        project_id="Demo Model",
        run_id="run-1",
        config=SimpleNamespace(
            snowflake=SimpleNamespace(
                account="acct",
                warehouse="wh",
                database="db",
                schema_name="schema",
            )
        ),
        start_time=0.0,
        source_type="fabric",
        target_type="snowflake",
        behavior=ConnectorBehavior(),
    )
    context.sml_model = SimpleNamespace(datasets=[], metrics=[])

    monkeypatch.setattr(
        "semabridge.connectors.snowflake_emitter.SnowflakeEmitter",
        _EmitterWithoutCortexMethod,
    )
    monkeypatch.setattr(
        "semabridge.connectors.snowflake_emitter_parts.renderers.generate_cortex_yaml",
        lambda _emitter, _sml: "name: demo-model\n",
    )
    monkeypatch.setattr(ExecutionEngine, "_model_output_dir", lambda self, *args, **kwargs: tmp_path)

    engine._convert_to_snowflake_target(context)

    assert (tmp_path / "semantic_view.sql").read_text(encoding="utf-8") == (
        "CREATE OR REPLACE VIEW test_view AS SELECT 1"
    )
    assert (tmp_path / "cortex_analyst.yaml").read_text(encoding="utf-8") == "name: demo-model\n"
    assert context.target_artifact_path == str(tmp_path / "semantic_view.sql")


def test_snowflake_deploy_error_includes_root_cause(
    monkeypatch,
):
    engine = ExecutionEngine()
    context = RunContext(
        project_id="Core_Finance_v1",
        run_id="run-1",
        config=SimpleNamespace(
            snowflake=SimpleNamespace(
                account="acct",
                warehouse="wh",
                database="db",
                schema_name="schema",
                deployment_method="ddl",
            )
        ),
        start_time=0.0,
        source_type="fabric",
        target_type="snowflake",
        behavior=ConnectorBehavior(),
    )
    context.sml_model = SimpleNamespace(datasets=[], metrics=[])

    monkeypatch.setattr(
        "semabridge.connectors.snowflake_emitter.SnowflakeEmitter",
        _EmitterWithDeploymentFailure,
    )

    with pytest.raises(DeploymentError) as exc_info:
        engine._do_snowflake_deploy(context, context.config.snowflake)

    message = str(exc_info.value)
    assert "SnowflakeEmitter.deploy() returned False for model 'Core_Finance_v1'." in message
    assert "invalid identifier 'ORDERS.QUANTITY'" in message
    assert "DDL[0] failed" in message