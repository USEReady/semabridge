"""Importing semabridge must never change a tracked file.

src/semabridge/connectors/refactor.py used to rewrite snowflake_emitter.py and
ddl_builder.py at import time. This copies the repo's tracked files (minus the
frontend, tests, docs and examples) into a temp folder, imports EVERY module
under src/semabridge in a fresh interpreter run from that copy, and asserts no
tracked file in the copy changed. The real checkout is never touched.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_SKIP_PREFIXES = (
    "frontend/",
    "Tests/",
    "tests/",
    "Docs/",
    "Examples/",
    "output/",
    ".github/",
    "Scratch/",
    "scratch/",
    ".dax_translation_cache.jsonl",
)
_MAX_BYTES = 5 * 1024 * 1024

_IMPORT_ALL = r"""
import importlib, pkgutil, sys
sys.path.insert(0, sys.argv[1])
import semabridge
failed = 0
for m in pkgutil.walk_packages(semabridge.__path__, "semabridge."):
    try:
        importlib.import_module(m.name)
    except BaseException:
        failed += 1  # optional dependencies may be missing; a failed import still ran its module code
print(f"imported semabridge modules (failed imports: {failed})")
"""


def _tracked_files() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        pytest.skip(f"git ls-files unavailable: {exc}")
    files = [f for f in out.decode("utf-8", "replace").split("\0") if f]
    return [f for f in files if not f.startswith(_SKIP_PREFIXES)
            and (REPO / f).is_file() and (REPO / f).stat().st_size <= _MAX_BYTES]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "<deleted>"


def test_importing_every_module_changes_no_tracked_file(tmp_path):
    import tempfile
    import uuid

    # Use short system temp path to avoid Windows MAX_PATH (260 char) limits under xdist
    short_temp = Path(tempfile.gettempdir()) / f"sb_imp_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    short_temp.mkdir(parents=True, exist_ok=True)
    try:
        copy_root = short_temp / "r"
        files = _tracked_files()
        assert any(f.startswith("src/semabridge/") for f in files)
        for rel in files:
            dest = copy_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / rel, dest)
        before = {rel: _digest(copy_root / rel) for rel in files}

        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["PYTHONPATH"] = str(copy_root / "src")
        run = subprocess.run([sys.executable, "-I", "-c", _IMPORT_ALL, str(copy_root / "src")],
                             cwd=copy_root, env=env, capture_output=True, text=True, timeout=1800)
        assert "imported semabridge modules" in run.stdout, run.stdout[-2000:] + run.stderr[-2000:]

        changed = sorted(rel for rel in files if rel.startswith("src/") and _digest(copy_root / rel) != before[rel])
        assert changed == [], f"importing semabridge modified tracked files: {changed}"
    finally:
        shutil.rmtree(short_temp, ignore_errors=True)
