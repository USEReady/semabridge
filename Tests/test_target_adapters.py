"""Step 9 dispatches through the TargetAdapter registry; an unknown target
fails instead of reporting a deploy that never happened."""
from types import SimpleNamespace

import pytest

from semabridge.core.engine.deployment.base import _step9_deploy
from semabridge.core.run_summary import StepStatus
from semabridge.core.targets import (
    TargetAdapter,
    UnknownTargetError,
    get_target_adapter,
    register_target_adapter,
    registered_targets,
)


def test_builtin_targets_registered():
    assert {"snowflake", "databricks", "fabric"} <= set(registered_targets())
    assert isinstance(get_target_adapter("Databricks"), TargetAdapter)


def test_unknown_target_is_an_error():
    with pytest.raises(UnknownTargetError, match="available:"):
        get_target_adapter("bigquery")


def _engine():
    calls, steps = [], []
    engine = SimpleNamespace(
        _deploy_to_snowflake=lambda ctx: calls.append("snowflake"),
        _deploy_to_databricks=lambda ctx: calls.append("databricks"),
        _deploy_to_fabric=lambda ctx: calls.append("fabric"),
        _record_step=lambda *a: steps.append(a),
    )
    return engine, calls, steps


@pytest.mark.parametrize("target", ["snowflake", "databricks", "fabric"])
def test_step9_routes_to_the_target(target):
    engine, calls, steps = _engine()
    _step9_deploy(engine, SimpleNamespace(target_type=target, deploy_step_message=None))
    assert calls == [target]
    assert steps == [(9, StepStatus.SUCCESS, "Deployment complete")]


def test_step9_with_unknown_target_fails_instead_of_claiming_success():
    from semabridge.core.engine.exceptions import DeploymentError

    engine, calls, steps = _engine()
    with pytest.raises(DeploymentError, match="No deployment target named 'bigquery'"):
        _step9_deploy(engine, SimpleNamespace(target_type="bigquery", deploy_step_message=None))
    assert calls == []
    assert steps[0][1] == StepStatus.FAILED


def test_a_new_target_plugs_in_without_engine_changes():
    seen = []

    class Lakehouse:
        name = "lakehouse_test"

        def deploy(self, engine, context):
            seen.append(context.sml_model)

    register_target_adapter(Lakehouse())
    engine, _, steps = _engine()
    _step9_deploy(engine, SimpleNamespace(target_type="lakehouse_test", sml_model="M", deploy_step_message=None))
    assert seen == ["M"] and steps[0][1] == StepStatus.SUCCESS
