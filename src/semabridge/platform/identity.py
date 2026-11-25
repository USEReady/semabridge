"""Signed-in user supplied by the hosting platform.

Each hosted profile has a front door that authenticates the user and passes
who they are in request headers:

* azure            Container Apps built-in authentication (Microsoft Entra ID):
                   ``X-MS-CLIENT-PRINCIPAL-NAME`` / ``-ID`` / ``-IDP``
* snowflake_native Snowpark Container Services ingress: ``Sf-Context-Current-User``
* databricks_app   Databricks Apps proxy: ``X-Forwarded-Email`` / ``X-Forwarded-User``

Those headers are trusted ONLY when the profile matches AND
``SEMABRIDGE_PLATFORM_AUTH=true`` declares that the front door is configured
(it strips client-sent copies). Otherwise anyone could send the header, so it
is ignored. ``/auth/platform-login`` turns a trusted identity into a normal
SemaBridge session; the rest of the app is unchanged.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional

from semabridge.core.deployment_profile import DeploymentProfile, current_profile


@dataclass(frozen=True)
class PlatformIdentity:
    provider: str
    subject: str
    email: str
    display_name: str


def platform_auth_enabled() -> bool:
    return str(os.environ.get("SEMABRIDGE_PLATFORM_AUTH", "") or "").strip().lower() in {"1", "true", "yes", "on"}


def _get(headers: Mapping[str, str], name: str) -> str:
    # Starlette headers are case-insensitive; plain dicts (tests) may not be.
    value = headers.get(name)
    if value is None:
        value = headers.get(name.lower())
    return str(value or "").strip()


def platform_identity(headers: Mapping[str, str]) -> Optional[PlatformIdentity]:
    """The trusted platform user for this request, or None."""
    if not platform_auth_enabled():
        return None
    profile = current_profile()
    if profile is DeploymentProfile.AZURE:
        name = _get(headers, "X-MS-CLIENT-PRINCIPAL-NAME")
        subject = _get(headers, "X-MS-CLIENT-PRINCIPAL-ID") or name
        if not name:
            return None
        return PlatformIdentity("entra_id", subject, name if "@" in name else "", name)
    if profile is DeploymentProfile.SNOWFLAKE_NATIVE:
        user = _get(headers, "Sf-Context-Current-User")
        if not user:
            return None
        return PlatformIdentity("snowflake", user, "", user)
    if profile is DeploymentProfile.DATABRICKS_APP:
        email = _get(headers, "X-Forwarded-Email")
        subject = _get(headers, "X-Forwarded-User") or email
        if not (email or subject):
            return None
        return PlatformIdentity("databricks", subject, email, _get(headers, "X-Forwarded-Preferred-Username") or email or subject)
    return None


def role_for(identity: PlatformIdentity) -> str:
    """``admin`` for identities listed in SEMABRIDGE_PLATFORM_ADMINS (emails or
    user names, comma separated), otherwise SEMABRIDGE_PLATFORM_DEFAULT_ROLE
    (default ``viewer``: a normal user who owns their own projects)."""
    admins = {
        a.strip().casefold()
        for a in str(os.environ.get("SEMABRIDGE_PLATFORM_ADMINS", "") or "").split(",")
        if a.strip()
    }
    keys = {identity.email.casefold(), identity.subject.casefold(), identity.display_name.casefold()} - {""}
    if keys & admins:
        return "admin"
    role = str(os.environ.get("SEMABRIDGE_PLATFORM_DEFAULT_ROLE", "viewer") or "viewer").strip().lower()
    return "viewer" if role == "admin" else role  # admin is granted only by the explicit list
