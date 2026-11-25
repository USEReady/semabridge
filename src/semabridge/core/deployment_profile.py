"""Where this SemaBridge instance runs, and what that implies.

One container image serves every deployment; the profile, read once from
``SEMABRIDGE_DEPLOYMENT_PROFILE``, picks the adapters for identity,
credentials, storage and LLM and turns on production safety rules.

Profiles:
    local             developer machine (default)
    azure             client-ready Azure deployment (Container Apps)
    snowflake_native  Snowflake Native App on Snowpark Container Services
    databricks_app    Databricks App

``SEMABRIDGE_ENV=production`` also marks a ``local`` profile as production
(e.g. a self-hosted VM).
"""
from __future__ import annotations

import os
from enum import Enum


class DeploymentProfile(str, Enum):
    LOCAL = "local"
    AZURE = "azure"
    SNOWFLAKE_NATIVE = "snowflake_native"
    DATABRICKS_APP = "databricks_app"


_ALIASES = {
    "": DeploymentProfile.LOCAL,
    "dev": DeploymentProfile.LOCAL,
    "development": DeploymentProfile.LOCAL,
    "snowflake": DeploymentProfile.SNOWFLAKE_NATIVE,
    "spcs": DeploymentProfile.SNOWFLAKE_NATIVE,
    "databricks": DeploymentProfile.DATABRICKS_APP,
}


class UnknownDeploymentProfileError(ValueError):
    """SEMABRIDGE_DEPLOYMENT_PROFILE names a profile that does not exist."""


def current_profile() -> DeploymentProfile:
    """The active profile. Read on every call so tests and reloads see changes."""
    raw = str(os.environ.get("SEMABRIDGE_DEPLOYMENT_PROFILE", "") or "").strip().lower()
    if raw in _ALIASES:
        return _ALIASES[raw]
    try:
        return DeploymentProfile(raw)
    except ValueError as exc:
        valid = ", ".join(p.value for p in DeploymentProfile)
        raise UnknownDeploymentProfileError(
            f"SEMABRIDGE_DEPLOYMENT_PROFILE={raw!r} is not a known profile (use one of: {valid})."
        ) from exc


def is_production() -> bool:
    """True for every hosted profile, or when SEMABRIDGE_ENV=production."""
    if str(os.environ.get("SEMABRIDGE_ENV", "") or "").strip().lower() in {"production", "prod"}:
        return True
    return current_profile() is not DeploymentProfile.LOCAL
