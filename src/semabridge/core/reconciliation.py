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
# The identifier quoting and AS/as casing must both be optional/case-insensitive:
# our own DDL builders always emit uppercase-quoted (ALIAS."NAME" AS ...), but
# Snowflake's GET_DDL echoes the *live* deployed view back lowercase and only
# quotes identifiers that actually need it (alias.name as ...) — reconcile_run
# parses GET_DDL output directly (see _fetch_live_deployed_ddl), so both
# shapes have to match here.
_METRIC_DEF_RE = re.compile(r'^\s*\w+\."?(\w+)"?\s+AS\s+(.+?)\s*,?\s*$', re.IGNORECASE)
_CLAUSE_CLOSE_RE = re.compile(r'^\s*\)\s*;?\s*$')
_NULL_CAST_RE = re.compile(r'^\s*CAST\s*\(\s*NULL\s+AS\s+DOUBLE\s*\)\s*$', re.IGNORECASE)
# Same GET_DDL-vs-our-DDL spacing/casing mismatch for the trailing synonyms
# clause: we emit " WITH SYNONYMS = (...)", Snowflake's GET_DDL echoes back
# "with synonyms=(...)" with no spaces around "=".
_SYNONYMS_SUFFIX_RE = re.compile(r'\s*WITH\s+SYNONYMS\s*=\s*\(.*\)\s*$', re.IGNORECASE)
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
    deployed_dimensions: set[str] = field(default_factory=set)  # DDL-emitted DIMENSIONS clause names
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
        # Only LIVE deployed metrics count as "deployed" for this invariant.
        # A "declared-dead" (CAST(NULL AS DOUBLE)) DDL entry is not a working
        # metric -- it must additionally have a matching DropLedger record to
        # be considered accounted for, exactly like a metric that's fully
        # absent from the DDL. Folding deployed_dead_metrics in here (as a
        # prior version of this check did) let any NULL-cast metric with zero
        # ledger record silently read as "accounted for" purely because its
        # name still appears in the DDL text -- the same blind spot this
        # module exists to catch.
        deployed_base_counts = Counter(_strip_dedup_suffix(n) for n in self.deployed_live_metrics)
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

    def is_metric_live(self, entity_name: str) -> bool:
        """True if *entity_name* (a raw/unsanitized metric name, e.g. a
        DropRecord's ``entity_name``) normalizes to a base that is present
        among ``deployed_live_metrics`` -- i.e. genuinely emitting real SQL
        in the DDL this report was computed against, not merely textually
        present as a ``CAST(NULL AS DOUBLE)`` placeholder.

        Deliberately checks LIVE only, never ``deployed_dead_metrics``: a
        declared-dead metric is not "confirmed present" in any sense a
        dropped-fields report should treat as resolved -- see this module's
        docstring and ``unaccounted``'s handling of the same distinction.

        Intended use: reconciling a run's DropLedger against the DDL that
        was *actually* deployed, so a drop record from an earlier, less
        complete pass (e.g. a schema-less preview emitter) can be retracted
        once a later, real pass proves the metric deployed successfully.
        See ``core/engine/finalize.py``'s Step 10.
        """
        return _normalize(entity_name, self.sanitizer) in self.deployed_live_metrics

    def is_dimension_live(self, entity_name: str) -> bool:
        """True if *entity_name* (a raw/unsanitized column name, e.g. a
        DropRecord's ``entity_name`` for ``entity_kind == "column"``)
        normalizes to a name present in ``deployed_dimensions`` -- i.e.
        genuinely emitted in the DIMENSIONS clause of the DDL this report
        was computed against.

        Same rationale as ``is_metric_live``: a column can legitimately be
        dropped by an earlier, schema-less pass (no live schema fetched
        yet to confirm it) and then successfully emitted once a later pass
        has a real live-schema connection. Without this check, that earlier
        drop record is never retracted even though the column deployed
        fine -- see ``core/engine/finalize.py``'s Step 10.
        """
        return _normalize(entity_name, self.sanitizer) in self.deployed_dimensions

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

    "declared-dead" = the expression is exactly ``CAST(NULL AS DOUBLE)`` — some
    stage (deploy-time auto-remediation, or a translation path that silently
    accepted the LLM's "I can't translate this" placeholder as if it were
    real SQL) neutered the metric, but the name still appears in the
    deployed semantic view. This is visible, but NOT automatically
    "accounted for" — unlike ``deployed_live_metrics``, entries here still
    require a matching DropLedger record to satisfy the invariant (see
    ``unaccounted`` below); a NULL-cast metric with no ledger record is a
    real bug (a mechanism dropped it without recording why), not a
    by-design bucket to fold in for free.
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
        expr_clean = _SYNONYMS_SUFFIX_RE.sub("", expr_clean).rstrip()
        if _NULL_CAST_RE.match(expr_clean):
            dead.add(name.upper())
        else:
            live.add(name.upper())
    return live, dead


def _extract_deployed_dimension_names(ddl_text: str) -> set[str]:
    """Parse every DIMENSIONS( ... ) clause in *ddl_text* into a set of
    emitted semantic dimension names -- same line shape as the METRICS
    clause (``ALIAS."NAME" AS <expr>,``), so this reuses the same
    definition/close regexes with the clause header swapped.
    """
    names: set[str] = set()
    in_dims = False
    for line in (ddl_text or "").splitlines():
        stripped_upper = line.strip().upper()
        if stripped_upper.startswith("DIMENSIONS ("):
            in_dims = True
            continue
        if in_dims and _CLAUSE_CLOSE_RE.match(line):
            in_dims = False
            continue
        if not in_dims:
            continue
        m = _METRIC_DEF_RE.match(line)
        if not m:
            continue
        names.add(m.group(1).upper())
    return names


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
    dimensions = _extract_deployed_dimension_names(deployed_ddl_text)
    metric_drop_records = [r for r in drop_records if r.get("entity_kind") == "metric"]

    return ReconciliationReport(
        run_id=run_id,
        project_id=project_id,
        snapshot_id=snapshot_id,
        snapshot_metric_names=[n for n in snapshot_metric_names if n],
        deployed_live_metrics=live,
        deployed_dead_metrics=dead,
        deployed_dimensions=dimensions,
        drop_records=metric_drop_records,
        ddl_error=ddl_error,
        sanitizer=sanitizer,
    )


def _fetch_live_deployed_ddl(project_id: str, view_name: str) -> str:
    """Fetch the DDL of the currently-deployed semantic view directly from
    Snowflake via ``GET_DDL``.

    This reflects whatever the real deploy actually put live right now — no
    DAX translation, no LLM calls, nothing re-derived. Uses the same
    identity_id-scoped credential resolution the real deploy path uses
    (``core/engine/deployment/snowflake.py:_deploy_to_snowflake``).
    """
    from sqlalchemy import select

    from semabridge.api.services.project_shared import _compat_load_modular_project
    from semabridge.auth.account_credential_resolver import scoped_account_env
    from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
    from semabridge.repository.orm.models import Account
    from semabridge.repository.orm.session_factory import db_manager

    bundle = _compat_load_modular_project(project_id)
    if not bundle:
        raise ValueError(f"No project config found for project_id={project_id!r}")

    identity_id = ""
    for target in (bundle.get("assembled") or {}).get("targets") or []:
        if isinstance(target, dict) and target.get("type") == "snowflake":
            identity_id = str(target.get("identity_id") or "").strip()
            break
    if not identity_id:
        raise ValueError(f"Project {project_id!r} has no Snowflake identity_id configured")

    with db_manager.get_session() as session:
        account = session.execute(
            select(Account).where(Account.connector_type == "SNOWFLAKE", Account.id == identity_id)
        ).scalars().first()
        if not account:
            raise ValueError(f"No linked Snowflake account found for identity_id={identity_id!r}")

        with scoped_account_env(account, session):
            from semabridge.core.settings import reload_settings

            sf_cfg = reload_settings().snowflake
            return SnowflakeExtractor(sf_cfg).extract_semantic_view_ddl(view_name)


def reconcile_run(run_id: str) -> ReconciliationReport:
    """Build a :class:`ReconciliationReport` for any completed sync run.

    Works against durable state the real deploy already produced — nothing
    here re-derives or re-translates anything:
      1. The Stage 6 SML snapshot for the run (looked up via the run's
         persisted ``summary.sml_snapshot_id`` — see note below on why this
         reads the compat store rather than the ``runs``/``snapshots`` ORM
         tables).
      2. The metric list of the *actually deployed* semantic view, fetched
         live via ``GET_DDL`` (``_fetch_live_deployed_ddl`` above) — not a
         fresh re-emission through the DDL builders, which would re-invoke
         DAX-to-SQL translation (calls to OpenAI/Groq) and could fail or
         diverge from what's actually live for reasons entirely unrelated to
         the real deploy, e.g. an LLM key that has since expired.
      3. The metric-level drop records the real deploy already recorded in
         that same run summary's ``dropped_entities`` — not a freshly
         regenerated DropLedger.

    Note on run tracking: the app currently has two parallel, unsynced run-
    tracking stores — a JSON-backed "compat" store (``_compat_project_runs``,
    read here) that the API/UI actually read and write for run status and
    ``dropped_entities``, and a separate Postgres ORM ``runs``/``snapshots``
    schema that historically backed this function but that nothing in the
    app currently keeps in sync (a run can show ``status="success"`` in the
    compat store while its ORM ``Run`` row is still ``"running"``). This
    function reads the compat store since that's the one durable source that
    actually carries the real deploy's ``dropped_entities``. The SML snapshot
    payload itself is still fetched via ``ModelRepository`` — both stores
    reference the same underlying snapshot id, so that part is shared.

    Raises ``ValueError`` if the run or its snapshot payload cannot be found.
    """
    from semabridge.api.services.project_shared import _compat_load_store, _compat_project_runs
    from semabridge.repository.model_repository import ModelRepository
    from semabridge.sml.serializer import SMLSerializer
    from semabridge.utils.name_translator import get_target_deployment_name

    _compat_load_store()

    run_entry: Optional[dict] = None
    project_id: Optional[str] = None
    for pid, runs in _compat_project_runs.items():
        match = next((r for r in (runs or []) if isinstance(r, dict) and r.get("run_id") == run_id), None)
        if match is not None:
            run_entry, project_id = match, pid
            break

    if run_entry is None:
        raise ValueError(f"No run found with run_id={run_id!r}")

    summary = run_entry.get("summary") or {}
    snapshot_id = summary.get("sml_snapshot_id")
    if not snapshot_id:
        raise ValueError(f"Run {run_id!r} has no sml_snapshot_id recorded — nothing to reconcile.")

    repo = ModelRepository()
    snapshot = repo.get_snapshot(snapshot_id)
    if not snapshot or not snapshot.sml_blob:
        raise ValueError(
            f"Snapshot {snapshot_id!r} for run {run_id!r} has no sml_blob "
            "(failed runs clear their payload — nothing to reconcile)."
        )

    sml_blob = snapshot.sml_blob
    raw_metric_names = [m.get("unique_name") for m in (sml_blob.get("metrics") or []) if m.get("unique_name")]
    sml_model = SMLSerializer._dict_to_model(sml_blob)

    drop_records = [
        r for r in (summary.get("dropped_entities") or [])
        if isinstance(r, dict) and r.get("entity_kind") == "metric"
    ]

    view_name = get_target_deployment_name(sml_model.unique_name or sml_model.label or "model", "snowflake")

    ddl_error: Optional[str] = None
    ddl_text = ""
    try:
        ddl_text = _fetch_live_deployed_ddl(project_id, view_name)
    except Exception as exc:  # noqa: BLE001 - deliberately broad, mirrors dry-run precedent
        ddl_error = str(exc)
        logger.info(
            "reconcile_run: could not fetch live deployed DDL for view %r (non-fatal): %s",
            view_name, exc,
        )

    return compute_reconciliation(
        run_id=run_id,
        project_id=project_id,
        snapshot_id=snapshot_id,
        snapshot_metric_names=raw_metric_names,
        deployed_ddl_text=ddl_text,
        drop_records=drop_records,
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
