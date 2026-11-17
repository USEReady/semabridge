"""F6 rules for the G05 fake-data scan: mock data that a switch can turn on in production.

Pure functions over source text so they can be unit-tested without scanning the repo.

  F6a  backend reads an environment switch whose name says mock/fake/simulated data
  F6b  backend defines or calls a *mock* / *fake* producer (functions, constants)
  F6c  frontend asks the backend for mock data (mock_mode: true, /seed-mock)
"""

from __future__ import annotations

import re
from typing import List, Tuple

_ENV_READ = re.compile(
    r"""(?:os\.getenv|os\.environ\.get|getenv|environ\.get)\(\s*['"]([A-Z0-9_]*(?:MOCK|FAKE|SIMULAT|DUMMY)[A-Z0-9_]*)['"]"""
    r"""|os\.environ\[\s*['"]([A-Z0-9_]*(?:MOCK|FAKE|SIMULAT|DUMMY)[A-Z0-9_]*)['"]\s*\]"""
)
_MOCK_PRODUCER = re.compile(r"\b(?:def\s+|class\s+)?([A-Za-z_]\w*(?:mock|fake|Mock|Fake|MOCK|FAKE)\w*)\b\s*(?:\(|=\s*[\[{(])")
_FRONT_MOCK = re.compile(r"mock_mode\s*:\s*true|/seed-mock\b|mockMode\s*:\s*true")
_ALLOWED_PRODUCERS = {"unittest", "MagicMock"}  # never valid in src/, but never a data producer either


def _code_lines(text: str):
    in_doc = False
    for no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line.startswith(('"""', "'''")):
            if not (len(line) > 3 and line.endswith(line[:3])):
                in_doc = not in_doc
            continue
        if in_doc or line.startswith("#"):
            continue
        yield no, raw


def backend_hits(text: str) -> List[Tuple[int, str, str]]:
    """(line, rule, snippet) for F6a/F6b in one Python source file."""
    hits: List[Tuple[int, str, str]] = []
    for no, raw in _code_lines(text):
        for m in _ENV_READ.finditer(raw):
            hits.append((no, "F6a", m.group(1) or m.group(2)))
        for m in _MOCK_PRODUCER.finditer(raw):
            if m.group(1) not in _ALLOWED_PRODUCERS:
                hits.append((no, "F6b", m.group(1)))
    return hits


def frontend_hits(text: str) -> List[Tuple[int, str, str]]:
    """(line, rule, snippet) for F6c in one frontend source file."""
    return [(no, "F6c", m.group(0)) for no, raw in enumerate(text.splitlines(), 1) for m in _FRONT_MOCK.finditer(raw)]
