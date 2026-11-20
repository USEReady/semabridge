from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
import pytest
import yaml

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


def test_golden_gate_workflow_yaml_spec():
    """Verify that .github/workflows/golden_gate.yml strictly requires SNOWFLAKE_ROLE and WAREHOUSE."""
    repo_root = Path(__file__).resolve().parent.parent
    workflow_path = repo_root / ".github" / "workflows" / "golden_gate.yml"
    assert workflow_path.exists(), "Workflow file must exist"

    content = workflow_path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(content)

    # 1. Minimum grants documented
    assert "GRANT USAGE ON WAREHOUSE" in content
    assert "GRANT USAGE ON DATABASE" in content
    assert "GRANT CREATE SCHEMA ON DATABASE" in content
    assert "CREATE VIEW" in content

    # 2. Secret validation step exists
    job = parsed["jobs"]["golden-gate"]
    steps = job["steps"]
    validation_step = next((s for s in steps if "Validate required Snowflake secrets" in s.get("name", "")), None)
    assert validation_step is not None, "Validation step must exist in workflow"
    val_run = validation_step["run"]
    assert "SNOWFLAKE_ROLE" in val_run
    assert "SNOWFLAKE_WAREHOUSE" in val_run
    assert "ACCOUNTADMIN" in val_run

    # 3. Step passes secrets without fallbacks
    run_step = next((s for s in steps if s.get("name") == "Run Golden Gate"), None)
    assert run_step is not None
    env = run_step["env"]
    assert env["SNOWFLAKE_ROLE"] == "${{ secrets.SNOWFLAKE_ROLE }}"
    assert env["SNOWFLAKE_WAREHOUSE"] == "${{ secrets.SNOWFLAKE_WAREHOUSE }}"


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

