"""Verify: does the deployed view give the same numbers as Power BI?

One place that (1) runs a DAX query against the project's own Power BI side
(the Fabric model for Fabric projects, else a local Power BI Desktop / the
configured backend), (2) normalises warehouse rows, and (3) compares the two
with the golden verifier. Every outcome is one of:

    matched    values agree within tolerance
    mismatch   values differ
    unverified a side could not be queried (reason given; never counted as a pass)
    failed     the question could not be answered at all
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

MATCHED, MISMATCH, UNVERIFIED, FAILED = "matched", "mismatch", "unverified", "failed"


class VerificationUnavailable(RuntimeError):
    """A side of the comparison cannot be queried here (reason in the message)."""


@dataclass
class Verdict:
    verdict: str
    detail: str = ""

    @property
    def passed(self) -> bool:
        return self.verdict == MATCHED


def rows_as_dicts(rows: Sequence[Any], columns: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    """Warehouse rows as dicts (Databricks returns lists + a column manifest)."""
    out: List[Dict[str, Any]] = []
    for row in rows or []:
        if isinstance(row, dict):
            out.append(dict(row))
        elif isinstance(row, (list, tuple)):
            names = list(columns or []) or [f"col_{i}" for i in range(len(row))]
            out.append({names[i] if i < len(names) else f"col_{i}": v for i, v in enumerate(row)})
    return out


def run_project_dax(project_id: Optional[str], dax_query: str) -> List[Dict[str, Any]]:
    """Rows of ``dax_query`` from the project's Power BI side.

    Fabric projects query their own model; otherwise the default DAX backend
    (a running Power BI Desktop, or a configured REST/XMLA dataset).
    Raises VerificationUnavailable when no Power BI side is reachable.
    """
    if not (dax_query or "").strip():
        raise VerificationUnavailable("no Power BI (DAX) query for this question")
    from semabridge.sources.base import SourceError

    try:
        from semabridge.sources.fabric.dax_target import resolve_fabric_dax_target

        target = resolve_fabric_dax_target(project_id) if project_id else None
    except SourceError:
        target = None
    except Exception:  # noqa: BLE001 -- not a Fabric project
        target = None
    if target is not None:
        from semabridge.sources.base import get_source_adapter

        try:
            res = get_source_adapter("fabric").execute_dax(target, dax_query)
        except SourceError as exc:
            raise VerificationUnavailable(f"the Fabric model could not be queried: {exc}") from exc
        return rows_as_dicts(res.get("rows") or [], res.get("columns"))

    from semabridge.reconciliation.dax_executor import DaxExecutionError, execute_dax

    try:
        res = execute_dax(dax_query)
    except DaxExecutionError as exc:
        raise VerificationUnavailable(f"no Power BI source reachable ({exc})") from exc
    except Exception as exc:  # noqa: BLE001 -- driver missing, Desktop not running, ...
        raise VerificationUnavailable(f"no Power BI source reachable ({exc})") from exc
    if not res.success:
        raise VerificationUnavailable(f"the DAX query failed: {res.error}")
    return rows_as_dicts(res.rows, res.columns)


def compare(metric_name: str, dax_rows: List[Dict[str, Any]], sql_rows: List[Dict[str, Any]],
            *, dax_query: str = "", sql_query: str = "",
            dimension_keys: Optional[List[str]] = None) -> Verdict:
    """Compare Power BI rows with warehouse rows."""
    if not sql_rows and not dax_rows:
        return Verdict(UNVERIFIED, "both sides returned no rows")
    from semabridge.reconciliation.golden_verifier import GoldenQueryVerifier

    res = GoldenQueryVerifier().reconcile_data(
        metric_name=metric_name or "metric",
        dax_rows=dax_rows,
        sql_rows=sql_rows,
        dimension_keys=list(dimension_keys or []),
        dax_query=dax_query,
        sql_query=sql_query,
    )
    if res.status == "CERTIFIED":
        return Verdict(MATCHED, res.certification_badge)
    if res.status == "ERROR":
        return Verdict(UNVERIFIED, res.certification_badge)
    return Verdict(MISMATCH, res.certification_badge)


def summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Counts and percentages for a set of verdicts.

    accuracy_pct = matched / (matched + mismatch): only compared cases count.
    coverage_pct = compared / total: how much was actually checked against Power BI.
    """
    counts = {MATCHED: 0, MISMATCH: 0, UNVERIFIED: 0, FAILED: 0}
    for r in results:
        counts[r.get("verdict") if r.get("verdict") in counts else FAILED] += 1
    total = len(results)
    compared = counts[MATCHED] + counts[MISMATCH]
    return {
        **counts,
        "total": total,
        "compared": compared,
        "accuracy_pct": round(100.0 * counts[MATCHED] / compared, 2) if compared else None,
        "coverage_pct": round(100.0 * compared / total, 2) if total else 0.0,
    }
