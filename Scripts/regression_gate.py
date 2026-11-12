#!/usr/bin/env python3
"""scripts/regression_gate.py - Permanent Regression Quality Gate.

Runs the pytest suite, extracts failing/error test identifiers, compares
against Tests/known_failures.txt, and enforces zero new regressions.
Exits 1 on any NEW failure; exits 0 if no regressions are introduced.
Prints tests that now pass so the baseline in Tests/known_failures.txt can shrink.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    known_failures_path = repo_root / "Tests" / "known_failures.txt"

    if not known_failures_path.exists():
        print(f"❌ ERROR: Known failures file not found: {known_failures_path}")
        return 1

    with open(known_failures_path, "r", encoding="utf-8") as f:
        known_failures = {line.strip() for line in f if line.strip() and not line.startswith("#")}

    print(f"📋 Loaded {len(known_failures)} accepted known failures from {known_failures_path.name}")

    # Determine pytest command
    # Check if pytest-xdist is installed
    has_xdist = False
    try:
        import xdist  # noqa: F401
        has_xdist = True
    except ImportError:
        pass

    cmd = [sys.executable, "-m", "pytest", "--tb=no", "-q", "-p", "no:cacheprovider"]
    if has_xdist:
        cmd.extend(["-n", "auto"])

    # Pass through any additional CLI args (e.g., specific test file or directory)
    if len(sys.argv) > 1:
        cmd.extend(sys.argv[1:])

    print(f"🚀 Running test suite: {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True, encoding="utf-8", errors="replace")

    # Combine stdout and stderr to parse test outcomes
    output = proc.stdout + "\n" + proc.stderr

    current_failures = set()
    for line in output.splitlines():
        # Match lines like 'FAILED Tests/...::test_name' or 'ERROR Tests/...::test_name'
        m = re.match(r"^(?:FAILED|ERROR)\s+([^\s]+)", line.strip())
        if m:
            current_failures.add(m.group(1))

    new_failures = sorted(current_failures - known_failures)
    now_passing = sorted(known_failures - current_failures)
    still_failing = sorted(current_failures & known_failures)

    print("\n" + "=" * 70)
    print("                REGRESSION GATE SUMMARY                ")
    print("=" * 70)
    print(f"Total current failing/error items: {len(current_failures)}")
    print(f"Accepted known failures:            {len(still_failing)}")
    print(f"Newly passing (baseline can shrink):{len(now_passing)}")
    print(f"NEW REGRESSIONS:                    {len(new_failures)}")
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


if __name__ == "__main__":
    sys.exit(main())
