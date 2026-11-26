"""Renderer snapshot: today's translation of every DAX test case.

renderer_corpus.py translates the feature-test cases, the complex-measure
file and a set of direct renderer cases (one or more per place the renderer
spells Snowflake SQL). This test compares the result with
fixtures/renderer_corpus.json, so refactoring the renderer (Phase 2b: SQL
spelling moved into the dialect) is proven not to change any output.

Runs the corpus in a separate process: the feature cases use generic module
names (fixture, cases, adapter) that must not leak into this process.

Updating after an INTENDED change: SEMABRIDGE_UPDATE_GOLDEN=1, review the
fixture diff, commit it with the change.
"""

from __future__ import annotations

import difflib
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
CORPUS = HERE / "renderer_corpus.py"
FIXTURE = HERE / "fixtures" / "renderer_corpus.json"
UPDATE = os.environ.get("SEMABRIDGE_UPDATE_GOLDEN", "").strip().lower() in ("1", "true", "yes", "on")


def _run_corpus(tmp_path: Path) -> str:
    env = {
        key: value
        for key, value in os.environ.items()
        # Never the AI tier, never a developer's .env.
        if key != "SEMABRIDGE_TIER5"
    }
    env["SEMABRIDGE_DISABLE_DOTENV"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(REPO_ROOT / "src"), env.get("PYTHONPATH", "")]))
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, str(CORPUS)],
        capture_output=True,
        cwd=tmp_path,  # any debug files land in the temp folder
        env=env,
        timeout=600,
    )
    if result.returncode != 0:
        pytest.fail(f"renderer_corpus.py failed:\n{result.stderr.decode('utf-8', 'replace')[-4000:]}")
    return result.stdout.decode("utf-8").replace("\r\n", "\n")


def test_renderer_output_matches_snapshot(tmp_path: Path) -> None:
    actual = _run_corpus(tmp_path)

    if UPDATE:
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(actual, encoding="utf-8", newline="\n")
        return

    if not FIXTURE.exists():
        pytest.fail(f"No snapshot at {FIXTURE}. Create it with SEMABRIDGE_UPDATE_GOLDEN=1 and commit it.")

    expected = FIXTURE.read_text(encoding="utf-8").replace("\r\n", "\n")
    if expected != actual:
        diff = list(
            difflib.unified_diff(
                expected.splitlines(), actual.splitlines(),
                fromfile="snapshot", tofile="actual", lineterm="", n=3,
            )
        )
        shown = "\n".join(diff[:120])
        more = f"\n... {len(diff) - 120} more diff lines" if len(diff) > 120 else ""
        pytest.fail(
            "Renderer output changed. If intended, rerun with SEMABRIDGE_UPDATE_GOLDEN=1 "
            f"and commit the snapshot diff.\n\n{shown}{more}"
        )
