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


def _load_known_failures(known_failures_path: Path) -> Set[str]:
    """Parse and validate known_failures.txt.

    Requirements:
    - Single test per line (must contain '::' and not contain wildcards).
    - Reason required after '#'.
    - Empty lines and comment-only lines are ignored.
    """
    if not known_failures_path.exists():
        raise FileNotFoundError(f"Known failures file not found: {known_failures_path}")

    known_failures: Set[str] = set()
    with open(known_failures_path, "r", encoding="utf-8") as f:
        for line_no, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "#" not in line:
                raise ValueError(
                    f"{known_failures_path.name}:{line_no}: reason required (e.g. 'test_path::test_name # reason')"
                )
            test_id, _, reason = line.partition("#")
            test_id = test_id.strip()
            reason = reason.strip()
            if not test_id:
                continue
            if not reason:
                raise ValueError(
                    f"{known_failures_path.name}:{line_no}: reason required after '#' for '{test_id}'"
                )
            if "::" not in test_id or any(c in test_id for c in ("*", "?", "[", "]")):
                raise ValueError(
                    f"{known_failures_path.name}:{line_no}: '{test_id}' must match a single test name containing '::', not a wildcard pattern."
                )
            known_failures.add(test_id)
    return known_failures


def _run_gate(
    target_dir: Path,
    pytest_args: List[str],
    in_place: bool,
    head_hash: str,
) -> int:
    known_failures_path = target_dir / "Tests" / "known_failures.txt"

    try:
        known_failures = _load_known_failures(known_failures_path)
    except Exception as exc:
        print(f"❌ ERROR: {exc}")
        return 1

    print(f"📋 Loaded {len(known_failures)} accepted known failures from {known_failures_path.name}")

    # Determine python binary (prefer current venv if active, else repo .venv)
    repo_root = Path(__file__).resolve().parent.parent
    if getattr(sys, "base_prefix", sys.prefix) != sys.prefix:
        python_bin = sys.executable
    else:
        venv_py = repo_root / ".venv" / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
        python_bin = str(venv_py) if venv_py.exists() else sys.executable

    # Determine pytest command
    has_xdist = False
    try:
        import xdist  # noqa: F401
        has_xdist = True
    except ImportError:
        pass

    gate_basetemp = tempfile.mkdtemp(prefix="sb_gate_bt_")
    cmd = [
        python_bin,
        "-m",
        "pytest",
        "--tb=no",
        "-q",
        "-p",
        "no:cacheprovider",
        f"--basetemp={gate_basetemp}",
    ]
    if has_xdist:
        cmd.extend(["-n", "auto"])

    if pytest_args:
        cmd.extend(pytest_args)
    else:
        cmd.append("Tests")

    env = os.environ.copy()
    src_dir = target_dir / "src"
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else str(src_dir)

    print(f"🚀 Running test suite: {' '.join(cmd)}")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(target_dir),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    finally:
        shutil.rmtree(gate_basetemp, ignore_errors=True)

    output = proc.stdout + "\n" + proc.stderr

    current_failures = set()
    for raw_line in output.splitlines():
        line = raw_line.strip()
        m_err = re.match(r"^ERROR\s+(?:collecting\s+)?([^\s\-]+)", line)
        if m_err:
            item = m_err.group(1).strip()
            current_failures.add(item)
            continue
        m_fail = re.match(r"^FAILED\s+([^\s\-]+)", line)
        if m_fail:
            item = m_fail.group(1).strip()
            current_failures.add(item)
            continue
        m_worker = re.search(r"\[gw\d+\]\s+\[\s*\d+%\s*\]\s+(?:FAILED|ERROR)\s+([^\s]+)", line)
        if m_worker:
            current_failures.add(m_worker.group(1).strip())

    total_failures = len(current_failures)
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
    print(f"Total failures/errors found:        {total_failures}")
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
