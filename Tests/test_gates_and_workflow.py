from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
import pytest

from Scripts.golden_gate import _get_snowflake_config


def test_regression_gate_clean_checkout_output():
    """Verify regression_gate.py reports clean checkout of HEAD hash."""
    repo_root = Path(__file__).resolve().parent.parent

    # Test clean checkout mode (pass non-existent test file to exit quickly)
    cmd = [sys.executable, str(repo_root / "Scripts" / "regression_gate.py"), "Tests/non_existent_quick_exit.py"]
    proc = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True)
    output = proc.stdout + "\n" + proc.stderr

    assert "clean checkout of" in output.lower()
    assert "known in file" in output.lower()
    assert "failing now and known" in output.lower()
    assert "newly passing" in output.lower()
    assert "new failures" in output.lower()


def test_regression_gate_in_place_output():
    """Verify regression_gate.py --in-place reports running in-place."""
    repo_root = Path(__file__).resolve().parent.parent

    cmd = [
        sys.executable,
        str(repo_root / "Scripts" / "regression_gate.py"),
        "--in-place",
        "Tests/non_existent_quick_exit.py",
    ]
    proc = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True)
    output = proc.stdout + "\n" + proc.stderr

    assert "running in-place on current working directory" in output.lower()


def test_golden_gate_is_local_only_and_no_snowflake_key_in_ci():
    """The live Snowflake golden gate runs locally, not in GitHub Actions, so no
    Snowflake private key is ever stored as a repository secret."""
    repo_root = Path(__file__).resolve().parent.parent
    workflows = repo_root / ".github" / "workflows"
    assert not (workflows / "golden_gate.yml").exists(), "golden gate must not run in CI"
    for wf in sorted(workflows.glob("*.y*ml")):
        text = wf.read_text(encoding="utf-8")
        assert "SNOWFLAKE_PRIVATE_KEY" not in text, f"{wf.name} must not use a Snowflake private key secret"

    gates_doc = (repo_root / "Docs" / "GATES.md").read_text(encoding="utf-8")
    # Minimum grants for the local run stay documented.
    assert "GRANT USAGE ON WAREHOUSE" in gates_doc
    assert "GRANT USAGE ON DATABASE" in gates_doc
    assert "GRANT CREATE SCHEMA ON DATABASE" in gates_doc
    assert "python Scripts/golden_gate.py" in gates_doc


def test_golden_gate_fails_fast_without_role_or_warehouse(monkeypatch):
    """Verify that golden_gate.py raises ValueError when role/warehouse is missing or role is ACCOUNTADMIN."""
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "test-account")
    monkeypatch.setenv("SNOWFLAKE_USER", "test-user")
    monkeypatch.setenv("SNOWFLAKE_PRIVATE_KEY", "-----BEGIN PRIVATE KEY-----\nMOCK\n-----END PRIVATE KEY-----")

    # Missing warehouse fails
    monkeypatch.delenv("SNOWFLAKE_WAREHOUSE", raising=False)
    monkeypatch.setenv("SNOWFLAKE_ROLE", "SEMABRIDGE_DEV")
    with pytest.raises(ValueError, match="SNOWFLAKE_WAREHOUSE"):
        _get_snowflake_config()

    # Missing role fails
    monkeypatch.setenv("SNOWFLAKE_WAREHOUSE", "TEST_WH")
    monkeypatch.delenv("SNOWFLAKE_ROLE", raising=False)
    with pytest.raises(ValueError, match="SNOWFLAKE_ROLE"):
        _get_snowflake_config()

    # ACCOUNTADMIN role fails
    monkeypatch.setenv("SNOWFLAKE_ROLE", "ACCOUNTADMIN")
    with pytest.raises(ValueError, match="ACCOUNTADMIN"):
        _get_snowflake_config()

