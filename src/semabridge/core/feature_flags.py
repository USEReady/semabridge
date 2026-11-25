"""Registry of behaviour-changing environment flags (visibility only).

SemaBridge reads many ``SEMABRIDGE_*`` variables ad hoc via ``os.getenv`` in
the module that uses them. This module does **not** change how any flag is
read -- each consumer keeps its own parsing and default. It records, in one
place, which flags change behaviour, their defaults (as implemented by the
consumer named in ``source``) and a description, so that:

* startup logs one block listing every flag whose effective value differs
  from its default (:func:`log_non_default_flags`);
* admins can inspect them via ``GET /api/admin/flags``.

When adding a flag that changes behaviour, register it here too. Values of
flags marked ``secret`` are never returned or logged.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class FeatureFlag:
    name: str
    kind: str  # "bool" | "int" | "str" | "path"
    default: Optional[str]  # None = unset / derived (see description)
    description: str
    source: str
    secret: bool = False

    def raw(self) -> Optional[str]:
        value = os.environ.get(self.name)
        if value is None or not value.strip():
            return None
        return value.strip()

    def is_default(self) -> bool:
        raw = self.raw()
        if raw is None:
            return True
        if self.default is None:
            return False
        if self.kind == "bool":
            default_true = self.default.lower() in _TRUE
            lowered = raw.lower()
            if lowered in _TRUE:
                return default_true
            if lowered in _FALSE:
                return not default_true
            return False
        if self.kind == "int":
            try:
                return int(raw) == int(self.default)
            except ValueError:
                return False
        return raw == self.default

    def effective(self) -> Optional[str]:
        raw = self.raw()
        value = raw if raw is not None else self.default
        if self.secret and value is not None:
            return "***"
        return value

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "default": None if self.secret else self.default,
            "effective": self.effective(),
            "is_set": self.raw() is not None,
            "is_default": self.is_default(),
            "description": self.description,
            "source": self.source,
        }


FLAGS: List[FeatureFlag] = [
    FeatureFlag("AUTH_ENABLED", "bool", "true",
                "Enforce JWT authentication. Fails closed: only an explicit false/0/no/off disables it.",
                "auth/flags.py"),
    FeatureFlag("SEMABRIDGE_DEV_AUTO_LOGIN", "bool", "false",
                "Allow POST /auth/auto-login while auth is enabled (never grants admin). Always allowed when auth is off.",
                "auth/flags.py"),
    FeatureFlag("SEMABRIDGE_DEMO_MODE", "bool", "false",
                "Arm demo mode server-wide ('true' arms it; false/0/off/no force it off for every project).",
                "core/demo_mode.py"),
    FeatureFlag("SEMABRIDGE_DISABLE_DEMO_MODE", "bool", "false",
                "Kill-switch: true/1/yes disables demo mode for every run.",
                "core/demo_mode.py"),
    FeatureFlag("SEMABRIDGE_MOCK_DRIFT", "bool", "false",
                "Serve mock drift-detection data.",
                "reconciliation/mock_parity.py"),
    FeatureFlag("SEMABRIDGE_MOCK_PARITY", "bool", "false",
                "Serve mock parity / comparative-analysis data.",
                "reconciliation/mock_parity.py"),
    FeatureFlag("SEMABRIDGE_STAGED_DEPLOY", "bool", "true",
                "Deploy via a staging schema before swapping into the target.",
                "connectors/staged_deploy.py"),
    FeatureFlag("SEMABRIDGE_DATE_ALIAS", "str", "Date",
                "Date column alias used in translated time-intelligence DAX "
                "(rule translator default 'Date'; legacy dax_translator falls back to 'COL_DATE').",
                "converter/dax_rule_translator.py, converter/dax_translator.py"),
    FeatureFlag("SEMABRIDGE_DATE_RESOLUTION", "path", "Config/date_resolution.yaml",
                "Date-resolution rules file.",
                "converter/date_resolution.py"),
    FeatureFlag("SEMABRIDGE_INFER_RELATIONSHIPS", "bool", "false",
                "Infer relationships the source model does not declare.",
                "converter/tmsl_to_osi.py"),
    FeatureFlag("SEMABRIDGE_DDL_SYNONYMS", "bool", "true",
                "Emit WITH SYNONYMS clauses in generated DDL (unset: behavior config, default on).",
                "connectors/synonym_clause.py"),
    FeatureFlag("SEMABRIDGE_DDL_COMMENTS", "bool", "true",
                "Emit business-metadata COMMENT clauses in generated DDL (unset: behavior config, default on).",
                "connectors/synonym_clause.py"),
    FeatureFlag("SEMABRIDGE_SYNC_EXECUTOR", "str", "thread",
                "Sync executor: 'thread' or 'process' (Fabric-bound jobs always use threads).",
                "api/services/sync_execution_service.py"),
    FeatureFlag("SEMABRIDGE_ALLOW_LOCAL_DB", "bool", "false",
                "Fall back to a local DuckDB file when SEMABRIDGE_DATABASE_URL is unset (otherwise startup fails).",
                "core/db_resolver.py"),
    FeatureFlag("SEMABRIDGE_COMPAT_STORE_PATH", "path", None,
                "Explicit path of the legacy compat JSON store (unset: default under the user's home).",
                "api/services/project_shared.py"),
    FeatureFlag("SEMABRIDGE_MAX_CONCURRENT_RUNS", "int", "2",
                "Maximum project runs executing at once (across all server processes on PostgreSQL).",
                "api/services/run_service.py"),
    FeatureFlag("SEMABRIDGE_MAX_CONCURRENT_MODELS", "int", "4",
                "Limit on models syncing at once (across all server processes on PostgreSQL).",
                "core/run_coordination.py"),
    FeatureFlag("SEMABRIDGE_GLOBAL_SLOTS", "str", "auto",
                "Capacity caps via database slot leases: auto (PostgreSQL only), on, off (per process).",
                "core/run_coordination.py"),
    FeatureFlag("SEMABRIDGE_SLOT_POLL_SECONDS", "str", "0.5",
                "How often a queued run/model re-checks for a slot freed by another process.",
                "core/run_coordination.py"),
    FeatureFlag("SEMABRIDGE_DRIFT_STATE_PATH", "path", None,
                "Local drift schedules/baselines file (drift events are in the database).",
                "governance/drift_service.py"),
    FeatureFlag("SEMABRIDGE_RUN_LEASE_SECONDS", "int", "120",
                "Run lease; a run whose heartbeat is older is considered dead (minimum 5).",
                "core/run_coordination.py"),
    FeatureFlag("SEMABRIDGE_READINESS_TIMEOUT_SECONDS", "int", "300",
                "How long a run waits for target readiness before timing out (minimum 10).",
                "api/services/run_service.py"),
    FeatureFlag("SEMABRIDGE_ALLOWED_SOURCE_ROOTS", "path", None,
                "Extra directories (os.pathsep-separated) user-supplied file paths may live under.",
                "auth/file_access.py"),
    FeatureFlag("SEMABRIDGE_TLS_CA_BUNDLE", "path", None,
                "CA bundle used to verify TLS for outbound token requests (e.g. a corporate proxy's CA).",
                "utils/tls.py"),
    FeatureFlag("SEMABRIDGE_INSECURE_SKIP_TLS_VERIFY", "bool", "false",
                "Turn OFF TLS certificate checks for outbound token requests. Insecure; logged on every use.",
                "utils/tls.py"),
    FeatureFlag("DATABRICKS_HOST", "str", None,
                "Databricks workspace host (e.g. adb-12345.11.azuredatabricks.net).",
                "core/settings.py, connectors/factory.py, auth/token_refresher.py"),
    FeatureFlag("DATABRICKS_AUTH_TYPE", "str", "pat",
                "Databricks auth method: 'pat' (Personal Access Token) or 'service_principal' (OAuth M2M).",
                "core/settings.py"),
    FeatureFlag("DATABRICKS_TOKEN", "str", None,
                "Databricks personal access token (used when DATABRICKS_AUTH_TYPE=pat).",
                "core/settings.py", secret=True),
    FeatureFlag("DATABRICKS_CLIENT_ID", "str", None,
                "Databricks OAuth M2M service principal client ID.",
                "core/settings.py, auth/token_refresher.py"),
    FeatureFlag("DATABRICKS_CLIENT_SECRET", "str", None,
                "Databricks OAuth M2M service principal client secret.",
                "core/settings.py, auth/token_refresher.py", secret=True),
    FeatureFlag("DATABRICKS_WAREHOUSE_ID", "str", None,
                "Databricks SQL warehouse ID for statement execution.",
                "core/settings.py, connectors/factory.py"),
    FeatureFlag("DATABRICKS_CATALOG", "str", "main",
                "Unity Catalog catalog name for Databricks semantic objects.",
                "core/settings.py"),
    FeatureFlag("DATABRICKS_SCHEMA", "str", "semabridge",
                "Unity Catalog schema name for Databricks semantic objects.",
                "core/settings.py"),
    FeatureFlag("SEMABRIDGE_FABRIC_REPORT_LAYER", "bool", "false",
                "Fetch Fabric report definitions bound to a model for field aliases / report-field warnings.",
                "sources/fabric/synonyms.py"),
    FeatureFlag("SEMABRIDGE_FABRIC_REAL_ROW_COUNTS", "bool", "false",
                "Fabric row counts via COUNTROWS (informational only; facts come from relationships).",
                "sources/fabric/stats.py"),
    FeatureFlag("SEMABRIDGE_FABRIC_LIVE_DRIFT", "bool", "false",
                "Drift compares the live Fabric model definition with the last synced snapshot.",
                "sources/fabric/live_definition.py"),
    FeatureFlag("SEMABRIDGE_FABRIC_STRICT_WORKSPACE", "bool", "false",
                "Fabric projects never fall back to the server's .env / stored workspace.",
                "api/services/sync_execution_service.py"),
    FeatureFlag("SEMABRIDGE_SKIP_UNCHANGED_SOURCES", "bool", "false",
                "Scheduled runs skip models whose source fingerprint is unchanged since the last success.",
                "sources/fabric/fingerprint.py"),
    FeatureFlag("SEMABRIDGE_BIND_FROM_M_SOURCE", "bool", "false",
                "Use the Power Query (M) partition source as the table-binding hint after saved per-project bindings.",
                "connectors/table_binding_resolver.py"),
]

_BY_NAME = {flag.name: flag for flag in FLAGS}


def get_flag(name: str) -> Optional[FeatureFlag]:
    return _BY_NAME.get(name)


def flag_report() -> List[Dict[str, Any]]:
    """All registered flags with default and effective value (secrets masked)."""
    return [flag.as_dict() for flag in FLAGS]


def non_default_flags() -> List[FeatureFlag]:
    return [flag for flag in FLAGS if not flag.is_default()]


def format_non_default_flags() -> str:
    changed = non_default_flags()
    if not changed:
        return "Feature flags: all registered flags at their defaults"
    lines = [f"Feature flags differing from defaults ({len(changed)}):"]
    for flag in changed:
        lines.append(f"  {flag.name}={flag.effective()} (default: {flag.default if not flag.secret else '***'})")
    return "\n".join(lines)


def log_non_default_flags(logger: Optional[logging.Logger] = None) -> str:
    """Log one block listing the non-default flags; returns the text."""
    text = format_non_default_flags()
    (logger or logging.getLogger("semabridge.flags")).info(text)
    return text
