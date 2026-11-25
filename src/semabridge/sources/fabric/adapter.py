"""Fabric source adapter implementation conforming to SemanticSourceAdapter.

Wraps existing sources/fabric/ modules (credentials, validation, fingerprint,
live_definition, dax_target, report_layer, stats) into a uniform interface.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from semabridge.sources.base import (
    ModelIdentity,
    SemanticSourceAdapter,
    SourceConfigError,
    SourceCredentials,
    SourceError,
    SourceType,
)


class FabricAdapter(SemanticSourceAdapter):
    """Adapter for Microsoft Fabric / Power BI workspace semantic models."""

    source_type: SourceType = "fabric"

    def resolve_credentials(
        self,
        *,
        identity_id: Optional[str] = None,
        account_token: Optional[str] = None,
        account_id: Optional[str] = None,
        credential_manager: Any = None,
        **kwargs: Any,
    ) -> SourceCredentials:
        from semabridge.sources.fabric.credentials import resolve_fabric_credentials

        return resolve_fabric_credentials(
            identity_id=identity_id,
            account_token=account_token,
            account_id=account_id,
            credential_manager=credential_manager,
        )

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
        from semabridge.sources.fabric.validation import validate_fabric_models

        ws = str(workspace_id or "").strip()
        if not ws:
            raise SourceConfigError("Fabric source has no workspace: set source.workspace_id in project settings.")
        str_refs = [
            str(m.get("id") or m.get("name") or "").strip() if isinstance(m, dict) else str(m or "").strip()
            for m in refs
        ]
        return validate_fabric_models(str_refs, ws, creds, max_models=max_models, lister=lister)

    def fingerprint(
        self,
        source_payload: Any,
        **kwargs: Any,
    ) -> Optional[str]:
        from semabridge.sources.fabric.fingerprint import get_fabric_fingerprint

        return get_fabric_fingerprint(source_payload)

    def extract_model(
        self,
        identity: ModelIdentity,
        creds: Optional[SourceCredentials] = None,
        *,
        drop_ledger: Any = None,
        extractor: Any = None,
        **kwargs: Any,
    ) -> Any:
        from semabridge.connectors.fabric_extractor import FabricExtractor
        from semabridge.core.settings import FabricConfig
        from semabridge.sources.fabric.credentials import apply_to_extractor

        ext = extractor
        if ext is None:
            ext = FabricExtractor(FabricConfig(workspace_id=identity.workspace_id))
            if creds is not None:
                apply_to_extractor(ext, creds)
        tmsl = ext.get_model_definition(identity.model_id)
        if not tmsl:
            raise SourceError(f"Model '{identity.display_name}' returned no definition.")
        return tmsl

    def extract_report_layer(
        self,
        identity: ModelIdentity,
        creds: Optional[SourceCredentials] = None,
        *,
        tmsl: Optional[Dict[str, Any]] = None,
        extractor: Any = None,
        **kwargs: Any,
    ) -> Any:
        from semabridge.connectors.fabric_extractor import FabricExtractor
        from semabridge.core.settings import FabricConfig
        from semabridge.sources.fabric.credentials import apply_to_extractor
        from semabridge.sources.fabric.report_layer import fetch_report_layer

        ext = extractor
        if ext is None:
            ext = FabricExtractor(FabricConfig(workspace_id=identity.workspace_id))
            if creds is not None:
                apply_to_extractor(ext, creds)
        return fetch_report_layer(ext, identity.workspace_id, identity.model_id, tmsl)

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
        from semabridge.connectors.fabric_extractor import FabricExtractor
        from semabridge.core.settings import FabricConfig
        from semabridge.sources.fabric.credentials import apply_to_extractor
        from semabridge.sources.fabric.stats import fabric_row_counts

        ext = extractor
        if ext is None:
            ext = FabricExtractor(FabricConfig(workspace_id=identity.workspace_id))
            if creds is not None:
                apply_to_extractor(ext, creds)
        return fabric_row_counts(ext, identity.model_id, tmsl or {}, batch_size=batch_size)

    def execute_dax(
        self,
        target: Any,
        query: str,
        creds: Optional[SourceCredentials] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        from semabridge.sources.fabric.dax_target import FabricDaxTarget, run_fabric_dax

        if isinstance(target, FabricDaxTarget):
            dax_target = target
        elif isinstance(target, dict):
            dax_target = FabricDaxTarget(
                project_id=str(target.get("project_id", "")),
                workspace_id=str(target.get("workspace_id", "")),
                dataset_id=str(target.get("dataset_id", "")),
                identity_id=target.get("identity_id"),
            )
        else:
            dax_target = target
        return run_fabric_dax(dax_target, query, creds=creds)

    def live_definition(
        self,
        identity: ModelIdentity,
        creds: Optional[SourceCredentials] = None,
        *,
        extractor: Any = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        from semabridge.connectors.fabric_extractor import FabricExtractor
        from semabridge.core.settings import FabricConfig
        from semabridge.sources.fabric.credentials import apply_to_extractor

        ext = extractor
        if ext is None:
            ext = FabricExtractor(FabricConfig(workspace_id=identity.workspace_id))
            if creds is not None:
                apply_to_extractor(ext, creds)
        tmsl = ext.get_model_definition(identity.model_id)
        if not tmsl:
            raise SourceError(f"Model '{identity.display_name}' returned no live definition.")
        return tmsl
