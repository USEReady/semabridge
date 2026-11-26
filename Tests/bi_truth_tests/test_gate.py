"""3. The deploy gate.

It reports by default. Only a project configured to block is ever stopped, so an
existing project's deploys carry on untouched.
"""
from __future__ import annotations

from bi_truth_tests import adapter
from bi_truth_tests.conftest import result, run

G = adapter.names(
    adapter.gate,
    "check_deploy_gate", "gate_could_not_evaluate",
    "GateMode", "GateStatus", "GateFinding",
)
M = G.GateMode
S = G.GateStatus


# ------------------------------------------------------------------ not blocking

def test_3_1_report_mode_never_blocks_whatever_the_verdict():
    for results in (
        [result("M1", "FAIL")],
        [result("M1", "ROWS_DIFFER")],
        [result("M1", "NOT_COMPARABLE")],
    ):
        v = G.check_deploy_gate(["M1"], run(results), mode=M.REPORT)
        assert not v.blocks_deploy


def test_3_2_report_mode_is_the_default():
    v = G.check_deploy_gate(["M1"], run([result("M1", "FAIL")]))
    assert v.mode is M.REPORT
    assert not v.blocks_deploy


def test_3_3_a_stale_run_does_not_block_in_report_mode():
    v = G.check_deploy_gate(["M1"], run([result("M1")], age_hours=999), mode=M.REPORT)
    assert v.status is S.STALE
    assert not v.blocks_deploy


def test_3_4_a_gate_error_does_not_block_in_report_mode():
    v = G.gate_could_not_evaluate("the database was unreachable", mode=M.REPORT)
    assert v.status is S.NOT_EVALUATED
    assert not v.blocks_deploy


def test_3_5_a_project_with_no_critical_measures_is_untouched():
    for mode in (M.REPORT, M.BLOCK):
        v = G.check_deploy_gate([], run([]), mode=mode)
        assert v.status is S.NOT_CONFIGURED
        assert not v.blocks_deploy


# ------------------------------------------------------------------ blocking, when asked

def test_3_6_block_mode_stops_on_a_failing_critical_measure():
    v = G.check_deploy_gate(["M1"], run([result("M1", "FAIL")]), mode=M.BLOCK)
    assert v.status is S.FAILED
    assert v.blocks_deploy


def test_3_7_a_non_critical_failure_never_blocks():
    v = G.check_deploy_gate(
        ["M1"],
        run([result("M1", "PASS"), result("M2", "FAIL")]),
        mode=M.BLOCK,
    )
    assert v.status is S.PASSED
    assert not v.blocks_deploy


def test_3_8_block_mode_refuses_a_stale_run():
    v = G.check_deploy_gate(["M1"], run([result("M1")], age_hours=999), mode=M.BLOCK)
    assert v.status is S.STALE
    assert v.blocks_deploy


def test_3_9_block_mode_refuses_when_the_gate_itself_failed():
    """A gate that fails open is not a gate."""
    v = G.gate_could_not_evaluate("the database was unreachable", mode=M.BLOCK)
    assert v.blocks_deploy


def test_3_10_block_mode_refuses_a_run_missing_a_critical_measure():
    v = G.check_deploy_gate(
        ["M1", "M2"],
        run([result("M1", "PASS")]),
        mode=M.BLOCK,
    )
    assert v.status is S.INCOMPLETE
    assert "M2" in v.reason
    assert v.blocks_deploy


def test_3_11_a_never_run_suite_is_stale():
    v = G.check_deploy_gate(["M1"], None, mode=M.BLOCK)
    assert v.status is S.STALE
    assert v.blocks_deploy


# ------------------------------------------------------------------ what it says

def test_3_12_a_failure_names_the_measure_and_its_numbers():
    v = G.check_deploy_gate(
        ["M1"],
        run([result("M1", "FAIL", window="w2", expected=300, actual=280,
                    variance_pct=6.67, tolerance_pct=0.5)]),
    )
    f = v.findings[0]
    assert f.measure == "M1"
    assert f.window == "w2"
    assert f.expected == 300 and f.actual == 280
    assert f.variance_pct == 6.67 and f.tolerance_pct == 0.5


def test_3_13_a_refusal_says_how_to_clear_it():
    """A refusal with no way forward gets the gate switched off."""
    for v in (
        G.check_deploy_gate(["M1"], run([result("M1")], age_hours=999)),
        G.check_deploy_gate(["M1", "M2"], run([result("M1")])),
        G.gate_could_not_evaluate("some error"),
    ):
        assert v.action, f"{v.status} offered no action"


def test_3_14_a_rows_differ_result_is_a_finding_not_a_pass():
    v = G.check_deploy_gate(["M1"], run([result("M1", "ROWS_DIFFER")]))
    assert v.status is S.FAILED


def test_3_15_a_not_comparable_critical_measure_is_a_finding():
    v = G.check_deploy_gate(["M1"], run([result("M1", "NOT_COMPARABLE")]))
    assert v.status is S.FAILED


def test_3_16_findings_serialise_to_a_defined_shape():
    """Not __dict__, whose keys depend on whatever attributes happen to exist."""
    v = G.check_deploy_gate(["M1"], run([result("M1", "FAIL")]))
    d = v.to_dict()
    assert set(d) >= {"status", "mode", "reason", "findings", "blocks_deploy"}
    assert set(d["findings"][0]) >= {"measure", "window", "expected", "actual", "reason"}


def test_3_17_the_gate_never_runs_the_suite():
    """It reads a result. A deploy must not wait on warehouse queries, and a run
    started by the deploy would test the model about to be replaced."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(adapter.gate))
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    for forbidden in ("run_truth_suite", "execute", "cursor", "connect", "fetchall"):
        assert forbidden not in called, f"the gate calls {forbidden}"
    for forbidden in ("snowflake", "databricks", "sqlalchemy"):
        assert forbidden not in imported, f"the gate imports {forbidden}"


def test_3_18_everything_passing_passes():
    v = G.check_deploy_gate(
        ["M1", "M2"],
        run([result("M1", "PASS"), result("M2", "PASS")]),
        mode=M.BLOCK,
    )
    assert v.status is S.PASSED
    assert not v.blocks_deploy
