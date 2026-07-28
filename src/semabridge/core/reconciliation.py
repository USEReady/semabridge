"""
Metric reconciliation — verifies that no metric vanishes silently anywhere
in the sync/deploy pipeline.

The invariant this module checks, for any completed sync run:

    snapshot_metrics == deployed_metrics ∪ dropped_metrics

Every metric present in the Stage 6 SML snapshot must either (a) appear in
the final deployed DDL, or (b) appear in that run's DropLedger with a
reason. A metric left over after subtracting both (``unaccounted``) means it
was silently excluded by some code path that never called
``DropLedger.record(...)`` — see core/drop_ledger.py for why that ledger
exists at all.

This module is deliberately generic: it operates on sets of metric names and
knows nothing about any particular model's tables/columns/metric names. It
is usable both as an importable routine (for tests, or ad-hoc diagnostics
against any run_id) and as a CLI (``python -m semabridge.core.reconciliation
<run_id>``).

Multiplicity, not just membership: two DISTINCT source metrics can sanitize
to the identical normalized identifier (e.g. ``"Total Units YTD Var %"`` and
``"TOTAL_UNITS_YTD_VAR_PCT"`` both normalize to ``TOTAL_UNITS_YTD_VAR_PCT``).
The DDL builders correctly disambiguate this at emission time (``_2``/
``_<hash>`` suffixes — see connectors/metrics_clause_builder.py's
``_resolve_unique_metric_alias``), so both metrics legitimately survive under
distinct DDL names. A set-based comparison collapses the two colliding
snapshot names into a single set element, which is a real blind spot: if one
of the two is later silently dropped while the other still deploys (or gets
a ledger record), presence-only membership checking is satisfied and the
loss goes undetected. This module therefore counts occurrences per
normalized base name — snapshot_count(base) must be fully covered by
deployed_count(base) + dropped_count(base) — not just checked for presence.

Design note — why "deployed" is re-emitted rather than read back verbatim:
deployed DDL is not persisted per-run anywhere in the schema (see
``core/engine/targets/snowflake.py`` / ``core/engine/deployment/snowflake.py``
— the on-disk DDL artifact is overwritten per-project, not kept per-run, and
DropLedger entries are in-memory only, stripped before runs are persisted to
disk — see ``api/services/project_shared.py:_RUN_BLOB_KEYS``). The only
durable, reproducible source of truth for "what would deploy" is the Stage 6
SML snapshot (persisted in the ``snapshots`` table) re-run through the real
DDL-emission builders with placeholder credentials — pure string-building,
no network I/O — exactly the pattern already used by the dry-run mapping
preview (``api/controllers/mappings_controller.py``) to surface Tier C /
DDL-emission-time drops before deployment.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from semabridge.utils.identifiers import IdentifierSanitizer
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

# Matches a METRICS-clause definition line, e.g.:  ALIAS."METRIC_NAME" AS <expr>,
_METRIC_DEF_RE = re.compile(r'^\s*\w+\."([^"]+)"\s+AS\s+(.+?)\s*,?\s*$')
_CLAUSE_CLOSE_RE = re.compile(r'^\s*\)\s*;?\s*$')
_NULL_CAST_RE = re.compile(r'^\s*CAST\s*\(\s*NULL\s+AS\s+DOUBLE\s*\)\s*$', re.IGNORECASE)
# Generic dedup-suffix patterns applied to colliding metric aliases elsewhere
# in the codebase: "_<digits>" (connectors/metrics_clause_builder.py
# ``_resolve_unique_metric_alias``) or "_<4 hex chars>" (connectors/
# ddl_builder.py ``_generate_deterministic_hash``). Not specific to any
# metric name — just the two suffix shapes the emitter itself can produce.
_DEDUP_SUFFIX_RE = re.compile(r'_(?:\d+|[0-9A-F]{4})$')


def _normalize(name: str, sanitizer: IdentifierSanitizer) -> str:
    return sanitizer.sanitize_alias(str(name)).upper()


def _strip_dedup_suffix(name: str) -> str:
    return _DEDUP_SUFFIX_RE.sub("", name)


@dataclass
class ReconciliationReport:
    """Result of reconciling one run's Stage 6 snapshot against its deploy DDL + DropLedger.

    Stores raw inputs (not pre-collapsed sets) so multiplicity is preserved —
    see the module docstring for why that matters. ``snapshot_metrics`` /
    ``dropped_metrics`` remain available as convenience set views for display,
    but ``unaccounted`` is the count-aware check and is what ``is_clean()``
    relies on.
    """

    run_id: str
    project_id: Optional[str]
    snapshot_id: Optional[str]

    snapshot_metric_names: list[str]                  # raw unique_name values from the Stage 6 snapshot
    deployed_live_metrics: set[str]                   # DDL-emitted names, real expression
    deployed_dead_metrics: set[str]                   # DDL-emitted names, deploy-time CAST(NULL AS DOUBLE)
    drop_records: list[dict[str, Any]] = field(default_factory=list)  # raw metric DropRecords
    ddl_error: Optional[str] = None                   # set if DDL generation raised (ledger is still populated)
    sanitizer: IdentifierSanitizer = field(default_factory=IdentifierSanitizer, repr=False)

    @property
    def deployed_metrics(self) -> set[str]:
        return self.deployed_live_metrics | self.deployed_dead_metrics

    @property
    def snapshot_metrics(self) -> set[str]:
        """Distinct normalized bases present in the snapshot.

        Display/back-compat convenience only — two distinct raw metric names
        that collide to the same base collapse to one entry here, same as
        before. Use ``unaccounted`` (count-aware) for the actual invariant
        check, not this property.
        """
        return {_normalize(n, self.sanitizer) for n in self.snapshot_metric_names if n}

    @property
    def dropped_metrics(self) -> set[str]:
        return {
            _normalize(r["entity_name"], self.sanitizer)
            for r in self.drop_records
            if r.get("entity_kind") == "metric" and r.get("entity_name")
        }

    @property
    def unaccounted(self) -> dict[str, int]:
        """normalized base -> shortfall count.

        For each normalized base name, how many snapshot metrics sharing that
        base have neither a deployed DDL entry nor a DropLedger record.
        Counting (not just presence) closes the collision blind spot: if two
        snapshot metrics share a base and only one is accounted for, this
        reports a shortfall of 1 for that base even though the base itself
        is individually "present" among deployed/dropped names.
        """
        snapshot_counts = Counter(_normalize(n, self.sanitizer) for n in self.snapshot_metric_names if n)
        deployed_base_counts = Counter(_strip_dedup_suffix(n) for n in self.deployed_metrics)
        dropped_base_counts = Counter(
            _normalize(r["entity_name"], self.sanitizer)
            for r in self.drop_records
            if r.get("entity_kind") == "metric" and r.get("entity_name")
        )

        shortfall: dict[str, int] = {}
        for base, snap_count in snapshot_counts.items():
            accounted = deployed_base_counts.get(base, 0) + dropped_base_counts.get(base, 0)
            if accounted < snap_count:
                shortfall[base] = snap_count - accounted
        return shortfall

    def is_clean(self) -> bool:
        return not self.unaccounted

    def summary(self) -> str:
        unaccounted = self.unaccounted
        lines = [
            f"Reconciliation for run_id={self.run_id} project_id={self.project_id} snapshot_id={self.snapshot_id}",
            f"  snapshot metrics:      {len(self.snapshot_metric_names)} ({len(self.snapshot_metrics)} distinct normalized names)",
            f"  deployed (live):       {len(self.deployed_live_metrics)}",
            f"  deployed (declared-dead / CAST NULL): {len(self.deployed_dead_metrics)}",
            f"  dropped (ledger):      {len(self.drop_records)}",
            f"  UNACCOUNTED:           {sum(unaccounted.values())}",
        ]
        if self.ddl_error:
            lines.append(f"  (DDL generation raised: {self.ddl_error})")
        if unaccounted:
            lines.append(f"  unaccounted (base -> shortfall count): {dict(sorted(unaccounted.items()))}")
        return "\n".join(lines)


def _extract_deployed_metric_names(ddl_text: str) -> tuple[set[str], set[str]]:
    """Parse every METRICS( ... ) clause in *ddl_text* into (live, declared-dead) name sets.

    "declared-dead" = the expression is exactly ``CAST(NULL AS DOUBLE)`` —
    deploy-time auto-remediation (``connectors/semantic_ddl_sanitizer.py``)
    neutered the metric to let the DDL compile, but the name still appears
    in the deployed semantic view. That is "accounted for" (visible, with a
    ledger record from the deployment pass) but not a *working* metric —
    reported as a distinct bucket rather than silently folded into "live".
    """
    live: set[str] = set()
    dead: set[str] = set()
    in_metrics = False
    for line in (ddl_text or "").splitlines():
        stripped_upper = line.strip().upper()
        if stripped_upper.startswith("METRICS ("):
            in_metrics = True
            continue
        if in_metrics and _CLAUSE_CLOSE_RE.match(line):
            in_metrics = False
            continue
        if not in_metrics:
            continue
        m = _METRIC_DEF_RE.match(line)
        if not m:
            continue
        name, expr = m.groups()
        expr_clean = expr.strip().rstrip(",")
        # Strip a trailing WITH SYNONYMS(...) clause before checking for the
        # NULL-cast shape, same split used in metrics_clause_builder.py.
        marker = " WITH SYNONYMS = ("
        idx = expr_clean.upper().rfind(marker)
        if idx >= 0:
            expr_clean = expr_clean[:idx].rstrip()
        if _NULL_CAST_RE.match(expr_clean):
            dead.add(name.upper())
        else:
            live.add(name.upper())
    return live, dead


def compute_reconciliation(
    *,
    run_id: str,
    project_id: Optional[str],
    snapshot_id: Optional[str],
    snapshot_metric_names: Iterable[str],
    deployed_ddl_text: str,
    drop_records: Iterable[dict[str, Any]],
    ddl_error: Optional[str] = None,
    sanitizer: Optional[IdentifierSanitizer] = None,
) -> ReconciliationReport:
    """Pure computation over already-gathered inputs — no DB/network access.

    Split out from :func:`reconcile_run` so tests can exercise the
    reconciliation logic itself with synthetic snapshots/DDL/ledgers, without
    needing a database or a real (or placeholder-configured) emitter.
    """
    sanitizer = sanitizer or IdentifierSanitizer()
    live, dead = _extract_deployed_metric_names(deployed_ddl_text)
    metric_drop_records = [r for r in drop_records if r.get("entity_kind") == "metric"]

    return ReconciliationReport(
        run_id=run_id,
        project_id=project_id,
        snapshot_id=snapshot_id,
        snapshot_metric_names=[n for n in snapshot_metric_names if n],
        deployed_live_metrics=live,
        deployed_dead_metrics=dead,
        drop_records=metric_drop_records,
        ddl_error=ddl_error,
        sanitizer=sanitizer,
    )


def reconcile_run(run_id: str, *, snowflake_account: Optional[str] = None) -> ReconciliationReport:
    """Build a :class:`ReconciliationReport` for any completed sync run.

    Works against durable state only:
      1. The Stage 6 SML snapshot committed for ``run_id`` (``snapshots`` table).
      2. A deterministic re-emission of deploy-time DDL from that snapshot,
         using the real ``SnowflakeEmitter`` with placeholder credentials
         (pure string-building — no network I/O).
      3. The DropLedger produced by that same re-emission pass.

    Raises ``ValueError`` if the run, its snapshot, or the snapshot payload
    cannot be found (e.g. a failed run whose snapshot payload was cleared —
    see ``model_repository._payload_for_snapshot``).
    """
    from sqlalchemy import select

    from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
    from semabridge.core.behavior import ConnectorBehavior
    from semabridge.core.settings import SnowflakeConfig
    from semabridge.repository.model_repository import ModelRepository
    from semabridge.repository.orm.models import Run, SnapshotRow
    from semabridge.repository.orm.session_factory import db_manager
    from semabridge.sml.serializer import SMLSerializer

    with db_manager.get_session() as session:
        run_row = session.get(Run, run_id)
        if run_row is None:
            raise ValueError(f"No run found with run_id={run_id!r}")
        project_id = run_row.project_id

        snap_row = session.execute(
            select(SnapshotRow)
            .where(SnapshotRow.run_id == run_id, SnapshotRow.deleted_at.is_(None))
            .order_by(SnapshotRow.timestamp.desc())
        ).scalars().first()

    if snap_row is None:
        raise ValueError(f"No SML snapshot found for run_id={run_id!r}")

    repo = ModelRepository()
    snapshot = repo.get_snapshot(snap_row.snapshot_id)
    if not snapshot or not snapshot.sml_blob:
        raise ValueError(
            f"Snapshot {snap_row.snapshot_id!r} for run {run_id!r} has no sml_blob "
            "(failed runs clear their payload — nothing to reconcile)."
        )

    sml_blob = snapshot.sml_blob
    raw_metric_names = [m.get("unique_name") for m in (sml_blob.get("metrics") or []) if m.get("unique_name")]

    sml_model = SMLSerializer._dict_to_model(sml_blob)

    placeholder_cfg = SnowflakeConfig(
        account=snowflake_account or "placeholder",
        user="placeholder",
        warehouse="placeholder",
        database=(sml_blob.get("unique_name") or "placeholder"),
    )
    emitter = SnowflakeEmitter(config=placeholder_cfg, behavior=ConnectorBehavior())

    ddl_error: Optional[str] = None
    ddl_text = ""
    try:
        ddls = emitter.generate_ddls(sml_model)
        ddl_text = "\n\n".join(ddls)
    except Exception as exc:  # noqa: BLE001 - deliberately broad, mirrors dry-run precedent
        ddl_error = str(exc)
        logger.info("reconcile_run: generate_ddls raised (non-fatal, ledger still populated): %s", exc)

    return compute_reconciliation(
        run_id=run_id,
        project_id=project_id,
        snapshot_id=snap_row.snapshot_id,
        snapshot_metric_names=raw_metric_names,
        deployed_ddl_text=ddl_text,
        drop_records=emitter.drop_ledger.to_json(),
        ddl_error=ddl_error,
    )


def _main() -> None:
    import sys

    if len(sys.argv) != 2:
        print("Usage: python -m semabridge.core.reconciliation <run_id>")
        raise SystemExit(2)

    report = reconcile_run(sys.argv[1])
    print(report.summary())
    raise SystemExit(0 if report.is_clean() else 1)


if __name__ == "__main__":
    _main()
