"""Golden gate on the real sample reports (Examples/**/*.pbix): conversion + DDL output is pinned.

Runs on Linux CI on every PR (.github/workflows/characterization.yml) with
SEMABRIDGE_REQUIRE_REAL_PBIX=1, so a missing decoder or a missing snapshot is a
failure there. Locally, machines whose policy blocks pbixray's native decoder skip.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("characterization_real_pbix", _HERE / "_build_real_pbix.py")
_real = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_real)  # type: ignore[union-attr]

_REQUIRED = os.getenv("SEMABRIDGE_REQUIRE_REAL_PBIX") == "1"
_SAMPLES = _real.pbix_samples()


def test_real_pbix_samples_exist():
    assert _SAMPLES, "no Examples/**/*.pbix sample found for real-PBIX characterization"


@pytest.mark.parametrize("pbix", _SAMPLES, ids=[_real.slug(p) for p in _SAMPLES])
def test_real_pbix_snapshot_unchanged(pbix: Path) -> None:
    reason = _real.pbixray_available()
    if reason:
        if _REQUIRED:
            pytest.fail(f"SEMABRIDGE_REQUIRE_REAL_PBIX=1 but {reason}")
        pytest.skip(reason)
    built = _real.build_one(pbix)
    if os.getenv("SEMABRIDGE_UPDATE_GOLDEN") == "1":
        _real.write()
    for kind in _real.KINDS:
        path = _real.snapshot_path(pbix, kind)
        assert path.exists(), (
            f"missing snapshot {path.name}; generate it on Linux CI "
            f"(push the branch: the 'bootstrap' job commits missing snapshots)"
        )
        expected = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        assert built[kind] == expected, f"{path.name} changed – real PBIX output is no longer identical"
