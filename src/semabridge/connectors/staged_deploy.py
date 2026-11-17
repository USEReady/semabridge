"""Staged Snowflake deploys: build and validate under staging names, promote last.

Snowflake DDL is not transactional. Before this, a deploy replaced the live
enriched views (``CREATE OR REPLACE VIEW <NS>_<FACT>_ENRICHED``) and could
drop one, *then* created the semantic view; a failed semantic-view CREATE
left the live semantic view pointing at changed or missing objects.

A staged deploy (``SEMABRIDGE_STAGED_DEPLOY``, default on):

1. creates every changed enriched view under a staging name
   ``<NAME>__SBSTG_<token>`` (the token is unique per deploy);
2. creates the semantic view under ``<VIEW>__SBSTG_<token>`` with its
   enriched-view references pointed at the staging views, through the same
   bounded remediation loop as before -- Snowflake compiles and validates
   the whole definition here, against the new objects, without touching
   anything live;
3. promotes: re-executes the validated enriched-view SQL under the live
   names, then the validated (remediated) semantic-view DDL under the live
   name. ``CREATE OR REPLACE`` replaces each live object atomically, and
   both statements were just proven to compile, so the live semantic view
   is never left pointing at objects it cannot use. If the semantic-view
   promotion still fails, the enriched views are restored from the
   definitions captured (``GET_DDL``) just before promotion;
4. deferred destructive work (dropping a stale enriched view that could not
   be rebuilt) runs only after a successful promotion;
5. always drops the staging objects (success or failure).

Why not ``ALTER ... RENAME``/``SWAP``: Snowflake has ``ALTER VIEW ... RENAME
TO`` but no view swap, and a semantic view stores its table references by
name, so a renamed staging semantic view would keep pointing at the staging
enriched views. Re-executing the validated DDL under the live names keeps
the final live objects byte-identical to what an unstaged deploy creates.

Every object created/promoted/dropped/cleaned is journaled in
``deploy_events`` (persisted per run to ``run_deploy_events``).
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

STAGING_MARK = "__SBSTG_"
_SV_NAME_RE = re.compile(
    r'(CREATE\s+(?:OR\s+REPLACE\s+)?SEMANTIC\s+VIEW\s+)((?:"[^"]+"|\w+)(?:\s*\.\s*(?:"[^"]+"|\w+))*)',
    re.IGNORECASE,
)


def staged_deploy_enabled() -> bool:
    return str(os.getenv("SEMABRIDGE_STAGED_DEPLOY", "true")).strip().lower() not in ("0", "false", "no", "off")


def _bare(name: str) -> str:
    return str(name or "").rsplit(".", 1)[-1].strip().strip('"')


def _ident_re(name: str) -> "re.Pattern[str]":
    return re.compile(r"(?<![A-Za-z0-9_$])" + re.escape(name) + r"(?![A-Za-z0-9_$])", re.IGNORECASE)


class StagedDeploy:
    def __init__(
        self,
        execute: Callable[..., Any],
        database: str,
        schema: str,
        *,
        token: Optional[str] = None,
        allow: Optional[Callable[[set], None]] = None,
    ) -> None:
        self._execute = execute  # (cursor, sql, context=...) -> Any
        self.database = str(database or "")
        self.schema = str(schema or "")
        self.token = (token or uuid.uuid4().hex[:8]).upper()
        self._allow = allow
        # live NAME (upper) -> (live name as written, staging name, live CREATE sql)
        self.enriched: Dict[str, Tuple[str, str, str]] = {}
        # live semantic view names (bare, as written) staged in this deploy
        self.semantic_views: List[Tuple[str, str]] = []
        self.deferred_drops: List[Tuple[str, str]] = []  # (sql, object name)
        self.events: List[Dict[str, Any]] = []
        self.promoted = False

    # -- naming -------------------------------------------------------------
    def staging_name(self, live_name: str) -> str:
        return f"{_bare(live_name)}{STAGING_MARK}{self.token}"

    def _fq(self, name: str) -> str:
        return f'"{self.database}"."{self.schema}"."{_bare(name)}"'

    def _event(self, action: str, object_type: str, name: str, status: str = "ok", detail: str = "") -> None:
        self.events.append({
            "action": action,
            "object_type": object_type,
            "object_name": self._fq(name),
            "status": status,
            "detail": detail[:2000] if detail else "",
            "at": datetime.now(timezone.utc).isoformat(),
        })

    def _register_allowed(self, *names: str) -> None:
        if self._allow is not None:
            self._allow({_bare(n).upper() for n in names if n})

    # -- enriched views -----------------------------------------------------
    def stage_enriched_view(self, cursor: Any, live_name: str, create_sql: str) -> str:
        """Create the enriched view under its staging name instead of the
        live one. Returns the staging name. Raises like the live CREATE."""
        staging = self.staging_name(live_name)
        self._register_allowed(staging, live_name)
        staged_sql = re.sub(
            r"(CREATE\s+OR\s+REPLACE\s+VIEW\s+)" + re.escape(live_name) + r"(?=\s)",
            lambda m: m.group(1) + staging,
            create_sql, count=1, flags=re.IGNORECASE,
        )
        if staged_sql == create_sql:
            raise ValueError(f"could not stage enriched view {live_name}: CREATE header not found")
        try:
            self._execute(cursor, staged_sql, context="create-enriched-view-staging")
        except Exception as exc:
            self._event("stage_failed", "VIEW", staging, "failed", str(exc))
            raise
        self.enriched[_bare(live_name).upper()] = (_bare(live_name), staging, create_sql)
        self._event("staged", "VIEW", staging)
        return staging

    def defer_drop(self, sql: str, name: str) -> None:
        self.deferred_drops.append((sql, name))
        self._event("drop_deferred", "VIEW", name, "pending")

    def is_staged(self, live_name: str) -> bool:
        return _bare(live_name).upper() in self.enriched

    def staging_fq_for(self, fq_name: str) -> str:
        bare = _bare(fq_name)
        entry = self.enriched.get(bare.upper())
        return self._fq(entry[1]) if entry else fq_name

    def alias_columns(self, table_columns: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """INFORMATION_SCHEMA lists staged views under their staging names;
        expose their columns under the live names the DDL references."""
        if not table_columns:
            return table_columns
        for live_upper, (_live, staging, _sql) in self.enriched.items():
            stg = staging.upper()
            for key in list(table_columns):
                if key == stg:
                    table_columns[live_upper] = table_columns[key]
                elif key.endswith("." + stg):
                    table_columns[key[: -len(stg)] + live_upper] = table_columns[key]
        return table_columns

    # -- semantic views -----------------------------------------------------
    def to_staging(self, ddl: str) -> str:
        """Point a semantic-view DDL at staging objects: its own name and
        every staged enriched view it references."""
        if not ddl:
            return ddl
        out = ddl
        for live_upper, (live, staging, _sql) in self.enriched.items():
            out = _ident_re(live).sub(staging, out)

        def _rename(m: "re.Match[str]") -> str:
            qualified = m.group(2)
            head, sep, last = qualified.rpartition(".")
            quoted = last.strip().startswith('"')
            live = last.strip().strip('"')
            staging = self.staging_name(live)
            self._register_allowed(staging, live)
            if (live, staging) not in self.semantic_views:
                self.semantic_views.append((live, staging))
            new_last = f'"{staging}"' if quoted else staging
            return m.group(1) + (head + sep if sep else "") + new_last

        return _SV_NAME_RE.sub(_rename, out, count=1)

    def to_live(self, ddl: str) -> str:
        return re.sub(re.escape(STAGING_MARK + self.token), "", ddl or "", flags=re.IGNORECASE)

    def record_semantic_staged(self, live: str) -> None:
        self._event("staged", "SEMANTIC VIEW", self.staging_name(live))

    # -- promotion / cleanup ------------------------------------------------
    def _capture_live_ddl(self, cursor: Any, name: str) -> Optional[str]:
        try:
            cursor.execute(f"SELECT GET_DDL('VIEW', '{self._fq(name)}')")
            row = cursor.fetchone()
            return str(row[0]) if row and row[0] else None
        except Exception:  # noqa: BLE001 -- view absent or no privilege
            return None

    def promote(self, cursor: Any, semantic_ddls: List[str]) -> None:
        """Replace the live objects with the validated definitions: enriched
        views first, then the semantic view(s). On a semantic-view failure the
        enriched views are restored and the error re-raised."""
        previous: Dict[str, Optional[str]] = {}
        for live_upper, (live, _staging, create_sql) in self.enriched.items():
            previous[live_upper] = self._capture_live_ddl(cursor, live)
            self._execute(cursor, create_sql, context="promote-enriched-view")
            self._event("promoted", "VIEW", live)
        try:
            for ddl in semantic_ddls:
                self._execute(cursor, ddl, context="promote-semantic-view")
            for live, _staging in self.semantic_views:
                self._event("promoted", "SEMANTIC VIEW", live)
        except Exception as exc:
            for live, _staging in self.semantic_views:
                self._event("promote_failed", "SEMANTIC VIEW", live, "failed", str(exc))
            self._restore_enriched(cursor, previous)
            raise
        self.promoted = True
        for sql, name in self.deferred_drops:
            try:
                self._execute(cursor, sql, context="deferred-drop-enriched-view")
                self._event("dropped", "VIEW", name)
            except Exception as exc:  # noqa: BLE001 -- the view is simply unused
                self._event("drop_failed", "VIEW", name, "failed", str(exc))

    def _restore_enriched(self, cursor: Any, previous: Dict[str, Optional[str]]) -> None:
        for live_upper, ddl in previous.items():
            live = self.enriched[live_upper][0]
            if not ddl:
                # Did not exist before this deploy: the live semantic view
                # cannot reference it, leave it (unused) for the next deploy.
                self._event("restore_skipped", "VIEW", live, "ok", "view did not exist before this deploy")
                continue
            try:
                self._execute(cursor, ddl, context="restore-enriched-view")
                self._event("restored", "VIEW", live)
            except Exception as exc:  # noqa: BLE001
                self._event("restore_failed", "VIEW", live, "failed", str(exc))
                logger.error("Could not restore enriched view %s after a failed promotion: %s", live, exc)

    def cleanup(self, cursor: Any) -> None:
        """Drop every staging object (always; best effort, journaled)."""
        for _live, staging in self.semantic_views:
            self._drop(cursor, "SEMANTIC VIEW", staging)
        for _live_upper, (_live, staging, _sql) in self.enriched.items():
            self._drop(cursor, "VIEW", staging)

    def _drop(self, cursor: Any, kind: str, name: str) -> None:
        try:
            self._execute(cursor, f"DROP {kind} IF EXISTS {self._fq(name)}", context="drop-staging-object")
            self._event("cleaned", kind, name)
        except Exception as exc:  # noqa: BLE001
            self._event("cleanup_failed", kind, name, "failed", str(exc))
            logger.warning("Could not drop staging object %s: %s", name, exc)


def persist_deploy_events(
    events: List[Dict[str, Any]], *, run_id: Optional[str], project_id: Optional[str], model: Optional[str]
) -> int:
    """Write a deploy's journal to ``run_deploy_events`` (never raises)."""
    if not events:
        return 0
    try:
        from semabridge.core.run_model_logs import current_scope
        from semabridge.repository.orm.models import RunDeployEvent
        from semabridge.repository.orm.session_factory import db_manager

        scope = current_scope()
        project_run_id = scope[1] if scope else None
        with db_manager.get_session() as session:
            for ev in events:
                session.add(RunDeployEvent(
                    id=str(uuid.uuid4()),
                    run_id=run_id,
                    project_run_id=project_run_id,
                    project_id=project_id,
                    model=model,
                    action=str(ev.get("action"))[:40],
                    object_type=(str(ev.get("object_type"))[:40] if ev.get("object_type") else None),
                    object_name=ev.get("object_name"),
                    status=str(ev.get("status") or "ok")[:20],
                    detail=ev.get("detail") or None,
                    created_at=datetime.now(timezone.utc),
                ))
            session.commit()
        return len(events)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Deploy journal not persisted for run %s: %s", run_id, exc)
        return 0
