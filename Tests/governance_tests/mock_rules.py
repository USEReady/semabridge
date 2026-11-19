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
_ALLOWED_ENV = {
    "SEMABRIDGE_DEMO_MODE",
    "SEMABRIDGE_DISABLE_DEMO_MODE",
    "SEMABRIDGE_MOCK_PARITY",
    "SEMABRIDGE_MOCK_DRIFT",
}

_ALLOWED_PRODUCERS = {
    "unittest",
    "MagicMock",
    "generate_mock_comparative_data",
    "is_mock_parity_enabled",
    "is_mock_drift_enabled",
    "seed_mock_drift_events",
    "simulate_metric_drift",
    "reset_simulated_metric_drift",
    "get_default_mock_drift_events",
    "_MOCK_BI_REPORTS",
    "_MOCK_AI_TOOLS",
    "mock_active",
}

_UNSANCTIONED_FRONT_MOCK = re.compile(r"fake_mode\s*:\s*true|use_fake_data\s*:\s*true|mock_data\s*:\s*true")


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


def is_gated_demo_code(rel_path: str, line_no: int, lines: List[str], full_text: str) -> bool:
    """Allow business words in demo code only when gated by demo flags."""
    has_demo_gate = "demo_mode_globally_armed" in full_text or "is_mock_parity_enabled" in full_text
    if not has_demo_gate:
        return False
    p = rel_path.replace("\\", "/")
    if "mock_parity.py" in p:
        return True

    # Check if line_no is inside a gated demo block
    for i in range(line_no - 1, -1, -1):
        l = lines[i].strip()
        if "is_mock_drift_enabled()" in l or "is_mock_parity_enabled()" in l or "demo_mode_globally_armed()" in l:
            return True
        if l.startswith(("_MOCK_BI_REPORTS", "_MOCK_AI_TOOLS")):
            return True
        if l.startswith((
            "def derive_semabridge_sql",
            "def reset_simulated_metric_drift",
            "def get_default_mock_drift_events",
            "def seed_mock_drift_events",
            "def simulate_metric_drift",
        )):
            return True
        if l.startswith("def ") or (l.startswith("class ") and not l.startswith("class _MOCK")):
            return False
    return False


def backend_hits(text: str) -> List[Tuple[int, str, str]]:
    """(line, rule, snippet) for F6a/F6b in one Python source file.

    Demo code is allowed only when gated by the registered demo flags.
    Ungated mock env reads or unregistered mock producers are flagged.
    """
    hits: List[Tuple[int, str, str]] = []
    for no, raw in _code_lines(text):
        for m in _ENV_READ.finditer(raw):
            env_var = m.group(1) or m.group(2)
            if env_var not in _ALLOWED_ENV:
                hits.append((no, "F6a", env_var))
        for m in _MOCK_PRODUCER.finditer(raw):
            prod = m.group(1)
            if prod not in _ALLOWED_PRODUCERS:
                hits.append((no, "F6b", prod))
    return hits


# ── F1-F3 (frontend files that call governance / reconciliation APIs) ─────────
_ROW_RX = re.compile(r"\{\s*[\[\w'\"]+[\]'\"]?\s*:\s*['\"][^'\"]+['\"]\s*,\s*\w+\s*:\s*-?\d[\d_.]*\s*\}")


def literal_row_lines(lines: List[str]) -> List[int]:
    """F1: object literals with a number value (hard-coded result rows).

    A JSX inline style object (``style={{ color: 'x', marginTop: 2 }}``) is
    styling, not a result row: a match that directly follows ``style={`` is
    not counted. Any other object literal still is.
    """
    return [i + 1 for i, l in enumerate(lines)
            if any(not re.search(r"style=\{\s*$", l[:m.start()]) for m in _ROW_RX.finditer(l))]


def scenario_switch_lines(lines: List[str]) -> List[int]:
    """F2: a 'scenario' / 'Intentional' switch, or an outcome SET to 'drift'.

    A label derived from a real comparison (``matched ? 'MATCH' : 'DRIFT'``)
    or a page mode (``mode === 'drift'``) does not decide an outcome.
    """
    return [i + 1 for i, l in enumerate(lines)
            if re.search(r"scenario|Intentional", l, re.I)
            or re.search(r"\b(status|outcome|result|verdict|state)\w*\s*(?:[:=]|===?)\s*['\"]drift['\"]", l, re.I)
            or re.search(r"\bset(Status|Outcome|Result|Verdict)\w*\(\s*['\"]drift['\"]", l, re.I)]


def _allowed_default(name: str, value: str) -> bool:
    if re.fullmatch(r"[A-Z_]+", value):  # enum-like option (METRIC, DROP)
        return True
    if re.search(r"(Kind|Type|Mode)$", name) and re.fullmatch(r"[a-z]+(_[a-z]+)*", value):
        return True  # lower_snake option of a *Kind / *Type / *Mode selector
    return bool(re.search(r"(Tol|Tolerance|Pct|Threshold)$", name)) and bool(re.fullmatch(r"\d+(\.\d+)?", value))


def prefilled_input_defaults(lines: List[str]) -> List[Tuple[int, str, str]]:
    """F3: recon/blast/lint inputs that start with a value instead of empty.

    Allowed: option selectors and numeric tolerance/threshold settings. A
    prefilled metric, expression, table, dimension or expected value is not.
    """
    found = [(i + 1, m.group(1), m.group(2)) for i, l in enumerate(lines)
             for m in [re.search(r"const\s*\[\s*(recon\w*|blast\w*|lint\w*)\s*,.*useState\(\s*'([^']+)'\s*\)", l)] if m]
    return [d for d in found if not _allowed_default(d[1], d[2])]


def frontend_hits(text: str) -> List[Tuple[int, str, str]]:
    """(line, rule, snippet) for F6c in one frontend source file.

    Allowed demo endpoints (/seed-mock, mock_mode: true) are gated by demo flag on backend.
    Unsanctioned fake data requests are flagged.
    """
    return [(no, "F6c", m.group(0)) for no, raw in enumerate(text.splitlines(), 1) for m in _UNSANCTIONED_FRONT_MOCK.finditer(raw)]
