"""Ratchet: the number of silent ``except Exception: pass`` blocks in
``src/semabridge/api/services`` and ``src/semabridge/core`` must not grow.

A swallowed exception that changes a user-visible result (status, counts,
deploy decision) makes a failure look like success. Log with context at
WARNING (or propagate) instead. If you remove one, lower ``MAX_ALLOWED``.
"""

from __future__ import annotations

import re
from pathlib import Path

MAX_ALLOWED = 10

_REPO = Path(__file__).resolve().parents[1]
_ROOTS = [_REPO / "src" / "semabridge" / "api" / "services", _REPO / "src" / "semabridge" / "core"]
_PATTERN = re.compile(r"except Exception:\n\s*pass\b")


def _occurrences() -> list[str]:
    found = []
    for root in _ROOTS:
        for path in sorted(root.rglob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in _PATTERN.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                found.append(f"{path.relative_to(_REPO)}:{line}")
    return found


def test_silent_except_pass_does_not_increase():
    found = _occurrences()
    assert len(found) <= MAX_ALLOWED, (
        f"{len(found)} 'except Exception: pass' blocks (max {MAX_ALLOWED}). "
        "Log the failure with context instead. Locations:\n  " + "\n  ".join(found)
    )
