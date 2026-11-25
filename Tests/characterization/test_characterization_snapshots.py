"""Golden gate: PBIX, Fabric and Snowflake-source conversion + DDL output is pinned.

Any difference fails. Regenerate ONLY for an intended, reviewed output change:
    SEMABRIDGE_UPDATE_GOLDEN=1 python -m pytest Tests/characterization
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("characterization_build", _HERE / "_build.py")
_build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_build)  # type: ignore[union-attr]

ARTIFACTS = [
    "pbix.osi.json", "pbix.sml.json", "pbix.ddl.sql",
    "fabric.osi.json", "fabric.sml.json", "fabric.ddl.sql",
    "snowflake.sml.json", "snowflake.ddl.sql",
]


@pytest.fixture(scope="module")
def built() -> dict:
    out = _build.build_all()
    if os.getenv("SEMABRIDGE_UPDATE_GOLDEN") == "1":
        _build.write_snapshots()
    return out


@pytest.mark.parametrize("artifact", ARTIFACTS)
def test_snapshot_unchanged(built: dict, artifact: str) -> None:
    expected_path = _build.SNAPSHOT_DIR / artifact
    assert expected_path.exists(), f"missing snapshot {artifact}; run with SEMABRIDGE_UPDATE_GOLDEN=1 once"
    # Line endings are a checkout detail (core.autocrlf), not output.
    expected = expected_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert built[artifact] == expected, f"{artifact} changed – PBIX/Snowflake/Fabric output is no longer identical"
