"""Shared types for source adapters: model identity, credentials, typed errors.

Errors carry a human-readable reason and are raised, never turned into an empty
result: an empty list from a source call always means "the source returned no
rows", never "the call failed".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

SourceType = Literal["fabric", "pbix", "snowflake"]
CredentialOrigin = Literal["identity", "account", "interactive", "extractor_default"]


@dataclass(frozen=True)
class ModelIdentity:
    """One key for a source model, used for overrides, preview ids, snapshots, drift and reports."""

    source_type: SourceType
    model_id: str  # Fabric dataset GUID | absolute .pbix path | DB.SCHEMA
    display_name: str
    workspace_id: Optional[str] = None

    @property
    def key(self) -> str:
        return f"{self.source_type}:{self.workspace_id or '-'}:{self.model_id}".lower()


@dataclass(frozen=True)
class SourceCredentials:
    """Credentials resolved once for a run (or an API call) and passed to every stage.

    ``access_token`` is None only for ``origin == "extractor_default"``: no
    delegated token is configured, so the FabricExtractor authenticates itself
    with its configured service principal / FABRIC_ACCESS_TOKEN (its own
    documented strategy). That is a configuration choice, not a failure.
    """

    origin: CredentialOrigin
    access_token: Optional[str] = None
    expires_at: Optional[float] = None
    identity_id: Optional[str] = None
    account_id: Optional[str] = None


class SourceError(Exception):
    """Base class: a source call failed. The message says why."""


class SourceAuthError(SourceError):
    """Credentials could not be resolved, or the source rejected them."""


class SourceNotFoundError(SourceError):
    """The workspace / model / item does not exist or is not visible to this identity."""


class SourceConfigError(SourceError):
    """The project config is missing something the source needs (no defaults are guessed)."""


class SourceQueryError(SourceError):
    """A query against the source failed."""

    def __init__(self, message: str, *, status: Optional[int] = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body
