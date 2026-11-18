"""PBIX source adapter implementation conforming to SemanticSourceAdapter.

Wraps local PBIX connector and execution logic into the uniform SemanticSourceAdapter interface.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from semabridge.sources.base import (
    ModelIdentity,
    SemanticSourceAdapter,
    SourceCredentials,
    SourceError,
    SourceNotFoundError,
    SourceQueryError,
    SourceType,
    SourceUnsupportedError,
)
from semabridge.sources.fabric.report_layer import ReportLayer
from semabridge.utils.identifiers import clean_pbix_model_name


@dataclass
class PbixModelCheck:
    identities: List[ModelIdentity]
    warnings: List[str] = field(default_factory=list)
    by_ref: Dict[str, ModelIdentity] = field(default_factory=dict)


class PbixAdapter(SemanticSourceAdapter):
    """Adapter for local Power BI Desktop (.pbix) files."""

    source_type: SourceType = "pbix"

    def resolve_credentials(
        self,
        *,
        identity_id: Optional[str] = None,
        account_token: Optional[str] = None,
        account_id: Optional[str] = None,
        credential_manager: Any = None,
        **kwargs: Any,
    ) -> SourceCredentials:
        # PBIX is local file-based: no cloud OAuth/identity token required
        return SourceCredentials(
            origin="interactive",
            access_token=None,
            expires_at=None,
            identity_id=identity_id,
            account_id=account_id,
        )

    def validate_and_resolve(
        self,
        refs: Sequence[Any],
        workspace_id: Optional[str] = None,
        creds: Optional[SourceCredentials] = None,
        *,
        max_models: int = 10,
        lister: Any = None,
        base_dir: Optional[Any] = None,
        **kwargs: Any,
    ) -> PbixModelCheck:
        from semabridge.sources.base import SourceConfigError

        non_empty_refs = [r for r in refs if str(r or "").strip()]
        if len(non_empty_refs) > max_models:
            raise SourceConfigError(
                f"Up to {max_models} PBIX files are supported per project; found {len(non_empty_refs)}."
            )

        identities: List[ModelIdentity] = []
        warnings: List[str] = []
        by_ref: Dict[str, ModelIdentity] = {}

        for ref in refs:
            raw_path = str(ref or "").strip()
            if not raw_path:
                continue

            # Strip quotes and resolve env vars
            clean_path = raw_path
            if len(clean_path) >= 2 and (
                (clean_path[0] == '"' and clean_path[-1] == '"')
                or (clean_path[0] == "'" and clean_path[-1] == "'")
            ):
                clean_path = clean_path[1:-1].strip()
            clean_path = os.path.expandvars(os.path.expanduser(clean_path))

            p = Path(clean_path)
            if not p.exists() and base_dir is not None:
                bdir = Path(base_dir)
                candidate = bdir / clean_path
                if candidate.suffix.lower() != ".pbix":
                    candidate = candidate.with_suffix(".pbix")
                if candidate.exists():
                    p = candidate

            if not p.exists():
                stem = p.stem
                clean_stem = re.sub(r"^[0-9a-fA-F]{32}_", "", stem)
                candidates = list(Path.cwd().glob(f"Examples/**/{clean_stem}*.pbix")) + list(
                    Path.cwd().glob(f"Examples/**/{stem}*.pbix")
                )
                if candidates and candidates[0].exists():
                    p = candidates[0].resolve()
                else:
                    raise SourceNotFoundError(f"PBIX file not found: {raw_path}")

            resolved_path = str(p.resolve())
            model_name = clean_pbix_model_name(p.name)
            ident = ModelIdentity(
                source_type="pbix",
                model_id=resolved_path,
                display_name=model_name,
                workspace_id=None,
            )
            identities.append(ident)
            by_ref[raw_path] = ident

        return PbixModelCheck(identities=identities, warnings=warnings, by_ref=by_ref)

    def fingerprint(
        self,
        source_payload: Any,
        **kwargs: Any,
    ) -> Optional[str]:
        from semabridge.sources.fabric.fingerprint import get_pbix_fingerprint

        return get_pbix_fingerprint(source_payload)

    def extract_model(
        self,
        identity: ModelIdentity,
        creds: Optional[SourceCredentials] = None,
        *,
        drop_ledger: Any = None,
        **kwargs: Any,
    ) -> Any:
        from semabridge.connectors.local_pbix_connector import LocalPBIXConnector

        connector = LocalPBIXConnector({"pbix_path": identity.model_id}, drop_ledger=drop_ledger)
        discovered = connector.discover()
        tmsl = connector.extract() if discovered.get("raw_tmsl") is None else discovered.get("raw_tmsl")
        if not tmsl:
            raise SourceError(f"Could not extract DataModelSchema from {identity.model_id}")
        return tmsl

    def extract_report_layer(
        self,
        identity: ModelIdentity,
        creds: Optional[SourceCredentials] = None,
        *,
        tmsl: Optional[Dict[str, Any]] = None,
        extractor: Any = None,
        **kwargs: Any,
    ) -> ReportLayer:
        from semabridge.connectors.local_pbix_connector import LocalPBIXConnector

        connector = LocalPBIXConnector({"pbix_path": identity.model_id})
        discovered = connector.discover()
        return ReportLayer(
            field_aliases=discovered.get("field_aliases", []) or [],
            unresolved=discovered.get("unresolved_report_field_references", []) or [],
            ambiguous=discovered.get("ambiguous_report_aliases", []) or [],
            reports=[Path(identity.model_id).name],
            warnings=[],
        )

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
        raise SourceUnsupportedError(
            "PBIX adapter does not execute live COUNTROWS queries; row counts are extracted from model metadata."
        )

    def execute_dax(
        self,
        target: Any,
        query: str,
        creds: Optional[SourceCredentials] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        from semabridge.reconciliation.dax_executor import execute_dax

        backend = "pbi_desktop"
        params = dict(kwargs)
        if isinstance(target, dict):
            backend = target.get("backend", backend)
            for k in ("port", "timeout", "dataset_id", "workspace", "workspace_id"):
                if k in target and k not in params:
                    params[k] = target[k]
        res = execute_dax(query=query, backend=backend, **params)
        if not res.success:
            raise SourceQueryError(res.error or "DAX query failed against local Power BI Desktop backend")
        return {
            "backend": res.backend,
            "columns": res.columns,
            "rows": res.rows,
            "metadata": res.metadata,
        }

    def live_definition(
        self,
        identity: ModelIdentity,
        creds: Optional[SourceCredentials] = None,
        *,
        extractor: Any = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        raise SourceUnsupportedError(
            "PBIX adapter does not support live service drift detection; comparison is done against local file snapshots."
        )
