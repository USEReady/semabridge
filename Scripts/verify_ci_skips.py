#!/usr/bin/env python3
"""verify_ci_skips.py - Verify and enforce explicit, documented test skips in CI.

Parses pytest output (-rs summary), aggregates skip reasons with counts,
prints a summary, and fails (exit 1) if any skip is for an undocumented reason.
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

# Documented allowed skip reason substrings in CI
ALLOWED_REASON_PATTERNS = [
    r"SEMABRIDGE_LIVE_TESTS",
    r"needs a real external service",
    r"SEMABRIDGE_SLOW_TESTS",
    r"wall-clock timing test",
    r"got empty parameter set",
]


def verify_skips(log_path: Path) -> int:
    if not log_path.exists():
        print(f"❌ ERROR: Pytest log file not found: {log_path}")
        return 1

    content = log_path.read_text(encoding="utf-8", errors="replace")
    
    # Match lines like:
    # SKIPPED [1] Tests/test_foo.py:123: some reason here
    # SKIPPED [44] Tests/test_local_pbix_connector.py: some reason
    skip_pattern = re.compile(r"^SKIPPED\s+\[\d+\]\s+([^:]+?(?::\d+)?):\s*(.*)$")
    
    skips_by_reason: dict[str, list[str]] = defaultdict(list)
    for raw_line in content.splitlines():
        line = raw_line.strip()
        m = skip_pattern.match(line)
        if m:
            test_loc = m.group(1).strip()
            reason = m.group(2).strip() or "No reason provided"
            skips_by_reason[reason].append(test_loc)

    total_skips = sum(len(locs) for locs in skips_by_reason.values())

    print("\n" + "=" * 70)
    print("                    CI TEST SKIP SUMMARY                    ")
    print("=" * 70)
    print(f"Total skipped tests: {total_skips}")
    print(f"Distinct skip reasons: {len(skips_by_reason)}")
    print("-" * 70)
    
    disallowed_skips: dict[str, list[str]] = {}
    for reason, tests in sorted(skips_by_reason.items(), key=lambda x: -len(x[1])):
        is_allowed = any(re.search(pat, reason, re.IGNORECASE) for pat in ALLOWED_REASON_PATTERNS)
        status_tag = "✓ ALLOWED" if is_allowed else "✗ DISALLOWED"
        print(f"[{status_tag}] ({len(tests)} tests) Reason: {reason}")
        if not is_allowed:
            disallowed_skips[reason] = tests

    print("=" * 70)

    if disallowed_skips:
        print(f"\n❌ FAILURE: Found {len(disallowed_skips)} undocumented skip reason(s) in CI:")
        for reason, tests in disallowed_skips.items():
            print(f"\n  Reason: '{reason}' ({len(tests)} test(s)):")
            for t in tests[:5]:
                print(f"    - {t}")
            if len(tests) > 5:
                print(f"    ... and {len(tests) - 5} more")
        print("\nAll skips in CI must be explicit and documented (e.g. SEMABRIDGE_LIVE_TESTS, SEMABRIDGE_SLOW_TESTS).")
        return 1

    print("\n✅ All test skips in CI are documented and permitted.")
    return 0


def main() -> int:
    log_file = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("pytest-output.log")
    return verify_skips(log_file)


if __name__ == "__main__":
    sys.exit(main())
