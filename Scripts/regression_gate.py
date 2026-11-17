#!/usr/bin/env python3
"""scripts/regression_gate.py - Permanent Regression Quality Gate.

Runs the pytest suite, extracts failing/error test identifiers, compares
against Tests/known_failures.txt, and enforces zero new regressions.
Exits 1 on any NEW failure; exits 0 if no regressions are introduced.

By default, runs in a clean temporary checkout of HEAD (git worktree) so that
uncommitted local changes never affect the result. Pass --in-place for
intentional local runs on the working directory.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import List, Set


def _run_gate(
    target_dir: Path,
    pytest_args: List[str],
    in_place: bool,
    head_hash: str,
) -> int:
    known_failures_path = target_dir / "Tests" / "known_failures.txt"

    if not known_failures_path.exists():
        print(f"❌ ERROR: Known failures file not found: {known_failures_path}")
        return 1

    with open(known_failures_path, "r", encoding="utf-8") as f:
        known_failures: Set[str] = {
            line.strip() for line in f if line.strip() and not line.startswith("#")
        }

    print(f"📋 Loaded {len(known_failures)} accepted known failures from {known_failures_path.name}")

    # Determine python binary (prefer repository .venv if available)
    repo_root = Path(__file__).resolve().parent.parent
    venv_py = repo_root / ".venv" / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
    python_bin = str(venv_py) if venv_py.exists() else sys.executable

    # Determine pytest command
    has_xdist = False
    try:
        import xdist  # noqa: F401
        has_xdist = True
    except ImportError:
        pass

    cmd = [python_bin, "-m", "pytest", "--tb=no", "-q", "-p", "no:cacheprovider"]
    if has_xdist:
        cmd.extend(["-n", "auto"])

    if pytest_args:
        cmd.extend(pytest_args)

    print(f"🚀 Running test suite: {' '.join(cmd)}")
    proc = subprocess.run(
        cmd,
        cwd=str(target_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    output = proc.stdout + "\n" + proc.stderr

    current_failures = set()
    for line in output.splitlines():
        m = re.match(r"^(?:FAILED|ERROR)\s+([^\s]+)", line.strip())
        if m:
            current_failures.add(m.group(1))

    new_failures = sorted(current_failures - known_failures)
    now_passing = sorted(known_failures - current_failures)
    still_failing = sorted(current_failures & known_failures)

    mode_label = (
        "Running in-place on current working directory (uncommitted changes may affect results)"
        if in_place
        else f"Clean checkout of {head_hash}"
    )

    print("\n" + "=" * 70)
    print("                REGRESSION GATE SUMMARY                ")
    print("=" * 70)
    print(f"Execution mode:                     {mode_label}")
    print(f"Known in file:                      {len(known_failures)}")
    print(f"Failing now and known:              {len(still_failing)}")
    print(f"Newly passing (baseline can shrink):{len(now_passing)}")
    print(f"New failures (REGRESSIONS):         {len(new_failures)}")
    print("=" * 70)

    if now_passing:
        print(f"\n✨ {len(now_passing)} test(s) in known_failures.txt are NOW PASSING:")
        for t in now_passing[:20]:
            print(f"  ✓ {t}")
        if len(now_passing) > 20:
            print(f"  ... and {len(now_passing) - 20} more.")
        print("💡 You may update Tests/known_failures.txt to permanently lock in these fixes.")

    if new_failures:
        print(f"\n❌ REGRESSION FAILURE: {len(new_failures)} NEW failing test(s) detected:")
        for t in new_failures:
            print(f"  ✗ {t}")
        print("\nFix the code or test rule justification before proceeding.")
        return 1

    print("\n✅ Regression gate PASSED: 0 new regressions detected.")
    return 0


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent

    # Determine execution mode
    in_place = "--in-place" in sys.argv
    pytest_args = [arg for arg in sys.argv[1:] if arg != "--in-place"]

    try:
        head_hash = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(repo_root), text=True
        ).strip()
    except Exception:
        head_hash = "HEAD"
    short_hash = head_hash[:7]

    if in_place:
        print(f"⚠️ Running in-place on current working directory ({repo_root})")
        return _run_gate(repo_root, pytest_args, in_place=True, head_hash=short_hash)

    # Clean checkout mode using git worktree
    temp_dir = Path(tempfile.gettempdir()) / f"sb_gate_{uuid.uuid4().hex[:8]}"
    print(f"🧹 Creating clean checkout of {short_hash} at {temp_dir}...")

    try:
        subprocess.check_call(
            ["git", "worktree", "add", "--detach", str(temp_dir), head_hash],
            cwd=str(repo_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        print(f"⚠️ Failed to create git worktree ({exc}); falling back to in-place run.")
        return _run_gate(repo_root, pytest_args, in_place=True, head_hash=short_hash)

    # Copy local .env if available to provide database connection settings
    env_file = repo_root / ".env"
    if env_file.exists():
        shutil.copy(str(env_file), str(temp_dir / ".env"))

    try:
        print(f"🚀 Running regression gate on clean checkout of {short_hash}")
        return _run_gate(temp_dir, pytest_args, in_place=False, head_hash=short_hash)
    finally:
        print(f"🧹 Cleaning up worktree {temp_dir}...")
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(temp_dir)],
            cwd=str(repo_root),
            capture_output=True,
        )
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=str(repo_root),
            capture_output=True,
        )
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
