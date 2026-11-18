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


class SourceUnsupportedError(SourceError):
    """The requested operation is not supported by this source adapter."""


import abc
from typing import Sequence, Dict, Any


class SemanticSourceAdapter(abc.ABC):
    """Uniform adapter interface for source systems (Fabric, PBIX, etc.)."""

    source_type: SourceType

    @abc.abstractmethod
    def resolve_credentials(
        self,
        *,
        identity_id: Optional[str] = None,
        account_token: Optional[str] = None,
        account_id: Optional[str] = None,
        credential_manager: Any = None,
        **kwargs: Any,
    ) -> SourceCredentials:
        """Resolve credentials for the source."""
        ...

    @abc.abstractmethod
    def validate_and_resolve(
        self,
        refs: Sequence[Any],
        workspace_id: Optional[str] = None,
        creds: Optional[SourceCredentials] = None,
        *,
        max_models: int = 50,
        lister: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Validate model references against the source and resolve them to ModelIdentity instances."""
        ...

    @abc.abstractmethod
    def fingerprint(
        self,
        source_payload: Any,
        **kwargs: Any,
    ) -> Optional[str]:
        """Compute a deterministic fingerprint representing the source definition."""
        ...

    @abc.abstractmethod
    def extract_model(
        self,
        identity: ModelIdentity,
        creds: Optional[SourceCredentials] = None,
        *,
        drop_ledger: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Extract the model definition (TMSL / schema)."""
        ...

    @abc.abstractmethod
    def extract_report_layer(
        self,
        identity: ModelIdentity,
        creds: Optional[SourceCredentials] = None,
        *,
        tmsl: Optional[Dict[str, Any]] = None,
        extractor: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Extract the report layer (field aliases, unresolved fields, ambiguous aliases)."""
        ...

    @abc.abstractmethod
    def extract_stats(
        self,
        identity: ModelIdentity,
        creds: Optional[SourceCredentials] = None,
        *,
        tmsl: Optional[Dict[str, Any]] = None,
        extractor: Any = None,
        batch_size: int = 40,
        **kwargs: Any,
    ) -> Any:
        """Extract table row counts or table stats."""
        ...

    @abc.abstractmethod
    def execute_dax(
        self,
        target: Any,
        query: str,
        creds: Optional[SourceCredentials] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Execute a DAX query on the source/model."""
        ...

    @abc.abstractmethod
    def live_definition(
        self,
        identity: ModelIdentity,
        creds: Optional[SourceCredentials] = None,
        *,
        extractor: Any = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Fetch the live definition of the model for drift detection."""
        ...


_ADAPTER_REGISTRY: Dict[str, SemanticSourceAdapter] = {}


def register_source_adapter(source_type: str, adapter: SemanticSourceAdapter) -> None:
    """Register an adapter instance for a source type."""
    _ADAPTER_REGISTRY[source_type.strip().lower()] = adapter


def get_source_adapter(source_type: str) -> SemanticSourceAdapter:
    """Return the registered adapter for ``source_type``; raises SourceConfigError on unknown type."""
    stype = str(source_type or "").strip().lower()
    if not _ADAPTER_REGISTRY:
        _init_default_adapters()
    if stype not in _ADAPTER_REGISTRY:
        supported = sorted(_ADAPTER_REGISTRY.keys())
        raise SourceConfigError(
            f"Unknown or unsupported source type: '{source_type}'. "
            f"Supported source types: {supported}"
        )
    return _ADAPTER_REGISTRY[stype]


def _init_default_adapters() -> None:
    from semabridge.sources.fabric.adapter import FabricAdapter
    from semabridge.sources.pbix.adapter import PbixAdapter

    register_source_adapter("fabric", FabricAdapter())
    register_source_adapter("pbix", PbixAdapter())

