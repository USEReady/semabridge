"""Deploy gate for BI Truth Suite regression verification.

It reads the most recent run and returns a verdict. It never executes the suite:
a deploy must not wait on warehouse queries, and a run started by the deploy would
be testing the model about to be replaced.

It reports by default. Only a project configured to block is ever stopped, so an
existing project's deploys are unaffected.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class GateMode(str, Enum):
    REPORT = "report"
    BLOCK = "block"


class GateStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"             # a critical measure is outside tolerance
    STALE = "stale"               # the latest run is too old
    INCOMPLETE = "incomplete"     # the run does not cover the critical measures
    NOT_CONFIGURED = "not_configured"
    NOT_EVALUATED = "not_evaluated"  # the gate itself could not run


DEFAULT_MAX_RESULT_AGE_HOURS = 24


@dataclass
class GateFinding:
    measure: str
    window: str = ""
    expected: Any = None
    actual: Any = None
    variance_pct: Optional[float] = None
    tolerance_pct: Optional[float] = None
    reason: str = ""

    # Compatibility properties for existing callers
    @property
    def metric_name(self) -> str:
        return self.measure

    @property
    def window_name(self) -> str:
        return self.window

    @property
    def received(self) -> Any:
        return self.actual

    @property
    def tolerance(self) -> Optional[float]:
        return self.tolerance_pct

    def to_dict(self) -> Dict[str, Any]:
        """An explicit shape, so the run record does not depend on attribute order."""
        return {
            "measure": self.measure,
            "window": self.window,
            "expected": self.expected,
            "actual": self.actual,  # hardcoding-ok: comparison-contract
            "variance_pct": self.variance_pct,
            "tolerance_pct": self.tolerance_pct,
            "reason": self.reason,
        }


@dataclass
class GateVerdict:
    status: GateStatus
    mode: GateMode = GateMode.REPORT
    reason: str = ""
    findings: List[GateFinding] = field(default_factory=list)
    run_at: Optional[str] = None
    action: str = ""

    @property
    def blocks_deploy(self) -> bool:
        """Only a project in block mode is ever stopped."""
        if self.mode is not GateMode.BLOCK:
            return False
        return self.status in (
            GateStatus.FAILED,
            GateStatus.STALE,
            GateStatus.INCOMPLETE,
            GateStatus.NOT_EVALUATED,
        )



    @property
    def passed(self) -> bool:
        return self.status in (GateStatus.PASSED, GateStatus.NOT_CONFIGURED)

    @property
    def critical_passed(self) -> int:
        return 0 if self.findings else (1 if self.status is GateStatus.PASSED else 0)

    @property
    def critical_total(self) -> int:
        return len(self.findings) if self.findings else (1 if self.status is GateStatus.PASSED else 0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "mode": self.mode.value,
            "reason": self.reason,
            "action": self.action,
            "run_at": self.run_at,
            "findings": [f.to_dict() for f in self.findings],
            "blocks_deploy": self.blocks_deploy,
            "passed": self.passed,
        }


def _parse_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def check_deploy_gate(
    critical_measures: Optional[List[str]] = None,
    latest_run: Optional[Dict[str, Any]] = None,
    mode: Any = GateMode.REPORT,
    max_age_hours: int = DEFAULT_MAX_RESULT_AGE_HOURS,
    now: Optional[datetime] = None,
) -> GateVerdict:
    """The verdict for one project, from its most recent run. Pure function."""
    gate_mode = mode if isinstance(mode, GateMode) else GateMode(str(mode or "report").lower())
    critical = [str(m) for m in (critical_measures or []) if str(m).strip()]

    if not critical:
        return GateVerdict(
            GateStatus.NOT_CONFIGURED,
            gate_mode,
            reason="no critical measures are configured for this project",
        )

    if not latest_run:
        return GateVerdict(
            GateStatus.STALE,
            gate_mode,
            reason="the suite has never been run for this project",
            action="run the BI truth suite",
        )

    run_at_raw = latest_run.get("run_at") or latest_run.get("ran_at")
    run_at = _parse_time(run_at_raw)
    if run_at is None:
        return GateVerdict(
            GateStatus.STALE,
            gate_mode,
            reason="the most recent run has no usable timestamp",
            action="run the BI truth suite",
        )

    current = now or datetime.now(timezone.utc)
    if current - run_at > timedelta(hours=max_age_hours):
        age = current - run_at
        return GateVerdict(
            GateStatus.STALE,
            gate_mode,
            reason=f"the most recent run is {int(age.total_seconds() // 3600)} hours old, "
                   f"older than the {max_age_hours} hour limit",
            run_at=str(run_at_raw),
            action="run the BI truth suite",
        )

    results = list(latest_run.get("results") or [])
    covered = {str(r.get("measure") or r.get("metric_name") or "") for r in results}
    missing = [m for m in critical if m not in covered]
    if missing:
        return GateVerdict(
            GateStatus.INCOMPLETE,
            gate_mode,
            reason=f"the most recent run does not cover: {', '.join(sorted(missing))}",
            run_at=str(run_at_raw),
            action="run the BI truth suite",
        )

    findings: List[GateFinding] = []
    for r in results:
        measure = str(r.get("measure") or r.get("metric_name") or "")
        if measure not in critical:
            continue
        verdict = str(r.get("verdict") or ("PASS" if r.get("passed") else "FAIL")).upper()
        if verdict in ("FAIL", "ROWS_DIFFER", "NOT_COMPARABLE"):
            findings.append(GateFinding(
                measure=measure,
                window=str(r.get("window") or r.get("window_name") or ""),
                expected=r.get("expected") or r.get("expected_value"),
                actual=r.get("actual") or r.get("actual_value"),  # hardcoding-ok: comparison-contract
                variance_pct=r.get("variance_pct"),
                tolerance_pct=r.get("tolerance_pct") or r.get("tolerance"),
                reason=str(r.get("reason") or r.get("error") or verdict),
            ))

    if findings:
        return GateVerdict(
            GateStatus.FAILED,
            gate_mode,
            reason=f"{len(findings)} critical measure(s) did not pass",
            findings=findings,
            run_at=str(run_at_raw),
            action="review the failing measures or accept the new benchmark",
        )

    return GateVerdict(
        GateStatus.PASSED,
        gate_mode,
        reason="every critical measure passed",
        run_at=str(run_at_raw),
    )


def gate_could_not_evaluate(reason: str, mode: Any = GateMode.REPORT) -> GateVerdict:
    """For an error inside the gate itself.

    In report mode the deploy carries on and the error is recorded. In block mode
    it stops, because a gate that fails open is not a gate.
    """
    gate_mode = mode if isinstance(mode, GateMode) else GateMode(str(mode or "report").lower())
    return GateVerdict(
        GateStatus.NOT_EVALUATED,
        gate_mode,
        reason=f"the gate could not be evaluated: {reason}",
        action="run the BI truth suite",
    )
