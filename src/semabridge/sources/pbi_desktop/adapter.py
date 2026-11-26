"""Power BI Desktop semantic source adapter conforming to SemanticSourceAdapter.

Extracts live semantic models and statistics from local running Power BI Desktop
Analysis Services instances over TCP, extracts report presentation layers from the
underlying .pbix archive (pure zip, no native extensions), and executes DAX queries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple
import zipfile

from semabridge.core.feature_flags import is_pbi_desktop_source_enabled
from semabridge.reconciliation.dax_executor import execute_dax
from semabridge.sources import report_parsing
from semabridge.sources.base import (
    ModelIdentity,
    SemanticSourceAdapter,
    SourceConfigError,
    SourceCredentials,
    SourceError,
    SourceNotFoundError,
    SourceQueryError,
    SourceType,
    SourceUnsupportedError,
)
from semabridge.sources.fabric.report_layer import ReportLayer
from semabridge.sources.pbi_desktop.discovery import DiscoveredDesktopInstance, discover_running_instances
from semabridge.sources.pbi_desktop.model_reader import read_tmsl_from_engine
from semabridge.sources.pbi_desktop.preselection import validate_preselected_desktop_source
from semabridge.utils.identifiers import clean_pbix_model_name

logger = logging.getLogger(__name__)


@dataclass
class PbiDesktopModelCheck:
    """Result of validate_and_resolve for Power BI Desktop models."""

    identities: List[ModelIdentity]
    warnings: List[str] = field(default_factory=list)
    by_ref: Dict[str, ModelIdentity] = field(default_factory=dict)


class PbiDesktopAdapter(SemanticSourceAdapter):
    """Adapter for running Power BI Desktop Analysis Services semantic models."""

    source_type: SourceType = "pbi_desktop"

    def __init__(self) -> None:
        self._resolved_file_paths: Dict[str, Optional[str]] = {}

    @property
    def capabilities(self) -> Dict[str, Any]:
        return {
            "source_type": "pbi_desktop",
            "live_model_extraction": True,
            "countrows_stats": True,
            "dax_execution": True,
            "report_layer_from_pbix": True,
            "requires_cloud_auth": False,
            "local_only": True,
        }

    def _check_feature_flag(self) -> None:
        if not is_pbi_desktop_source_enabled():
            raise SourceUnsupportedError(
                "Power BI Desktop source is disabled (feature flag 'pbi_desktop_source' is off)."
            )

    def resolve_credentials(
        self,
        *,
        identity_id: Optional[str] = None,
        account_token: Optional[str] = None,
        account_id: Optional[str] = None,
        credential_manager: Any = None,
        **kwargs: Any,
    ) -> SourceCredentials:
        # Local Desktop runs over local TCP: no cloud token required
        return SourceCredentials(
            origin="local engine",
            access_token=None,
            expires_at=None,
            identity_id=identity_id,
            account_id=account_id,
        )

    def _parse_server_and_database(self, ref: Any) -> Optional[Tuple[str, int, str]]:
        """Parse server and database from dict or string ref.

        Returns (host, port, database_name) or None if ref is a file path.
        """
        if isinstance(ref, dict):
            server = str(ref.get("server") or "").strip()
            database = str(ref.get("database") or "").strip()
            if not server or not database:
                return None
            m = re.match(r"^([^:]+):(\d+)$", server)
            if not m:
                raise SourceConfigError(f"Invalid server format '{server}'; expected 'localhost:<port>'")
            host, port_str = m.group(1).strip(), m.group(2).strip()
            return host, int(port_str), database

        s = str(ref or "").strip()
        # Format: localhost:<port>/<database> or 127.0.0.1:<port>/<database>
        m_slash = re.match(r"^([^:]+):(\d+)/(.+)$", s)
        if m_slash:
            return m_slash.group(1).strip(), int(m_slash.group(2).strip()), m_slash.group(3).strip()

        return None

    def validate_and_resolve(
        self,
        refs: Sequence[Any],
        workspace_id: Optional[str] = None,
        creds: Optional[SourceCredentials] = None,
        *,
        max_models: int = 10,
        lister: Any = None,
        instances_provider: Optional[Any] = None,
        **kwargs: Any,
    ) -> PbiDesktopModelCheck:
        self._check_feature_flag()

        non_empty = [r for r in refs if r]
        if len(non_empty) > max_models:
            raise SourceConfigError(
                f"Up to {max_models} Power BI Desktop models are supported per project; found {len(non_empty)}."
            )

        running_instances = instances_provider() if instances_provider is not None else discover_running_instances(verify_active=True)

        identities: List[ModelIdentity] = []
        warnings: List[str] = []
        by_ref: Dict[str, ModelIdentity] = {}

        for ref in refs:
            raw_key = str(ref)
            parsed_server = self._parse_server_and_database(ref)

            matched_inst: Optional[DiscoveredDesktopInstance] = None

            if parsed_server is not None:
                host, port, database = parsed_server
                server_str = f"{host}:{port}"
                matched_inst = validate_preselected_desktop_source(
                    server=server_str,
                    database=database,
                    instances_provider=lambda: running_instances,
                )
            else:
                # Reference is a file name, file path, or model dict
                if isinstance(ref, dict):
                    raw_path = str(ref.get("file_path") or ref.get("path") or ref.get("name") or "").strip().strip('"').strip("'")
                else:
                    raw_path = str(ref).strip().strip('"').strip("'")
                clean_ref_name = Path(raw_path).name.lower()
                clean_ref_stem = Path(raw_path).stem.lower()

                for inst in running_instances:
                    inst_file_name = inst.file_name.lower()
                    inst_stem = Path(inst.file_name).stem.lower()
                    inst_full_path = (inst.file_path or "").lower()

                    if (
                        clean_ref_name == inst_file_name
                        or clean_ref_stem == inst_stem
                        or (inst_full_path and Path(raw_path).resolve() == Path(inst.file_path).resolve())
                    ):
                        matched_inst = inst
                        break

                if not matched_inst:
                    file_name = Path(raw_path).name or raw_path
                    raise SourceNotFoundError(
                        f"Open {file_name} in Power BI Desktop and try again"
                    )

            if matched_inst.is_live_connected:
                warnings.append(
                    f"Model '{matched_inst.file_name}' appears to be a live connection to Fabric / Power BI service; "
                    "no local tabular tables were detected. Consider using the Fabric connector directly."
                )

            if matched_inst.has_unsaved_changes:
                warnings.append(
                    f"Model '{matched_inst.file_name}' has unsaved changes in Power BI Desktop."
                )

            model_id = f"localhost:{matched_inst.port}/{matched_inst.database_name}"
            display_name = clean_pbix_model_name(matched_inst.file_name)

            ident = ModelIdentity(
                source_type="pbi_desktop",
                model_id=model_id,
                display_name=display_name,
                workspace_id=matched_inst.workspace_folder,
            )
            identities.append(ident)
            by_ref[raw_key] = ident

            # Cache the file path for report layer extraction
            self._resolved_file_paths[model_id] = matched_inst.file_path

        return PbiDesktopModelCheck(identities=identities, warnings=warnings, by_ref=by_ref)

    def _resolve_target_port_and_catalog(self, target: Any) -> Tuple[int, str]:
        """Resolve port and catalog name from identity, dict, or string."""
        if isinstance(target, ModelIdentity):
            target = target.model_id
        elif isinstance(target, dict):
            server = target.get("server") or ""
            database = target.get("database") or ""
            if ":" in server:
                port_str = server.split(":")[-1]
                return int(port_str), database
            if "port" in target:
                return int(target["port"]), database
            target = target.get("model_id") or ""

        s = str(target or "").strip()
        m = re.match(r"^(?:localhost|127\.0\.0\.1):(\d+)(?:/(.+))?$", s, re.IGNORECASE)
        if m:
            port = int(m.group(1))
            catalog = m.group(2) or ""
            return port, catalog

        raise SourceError(f"Cannot resolve port and database from target: '{target}'")

    def fingerprint(
        self,
        source_payload: Any,
        **kwargs: Any,
    ) -> Optional[str]:
        if not source_payload:
            return None
        canonical = json.dumps(source_payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def extract_model(
        self,
        identity: ModelIdentity,
        creds: Optional[SourceCredentials] = None,
        *,
        drop_ledger: Any = None,
        query_executor: Optional[Any] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        self._check_feature_flag()
        port, catalog = self._resolve_target_port_and_catalog(identity)
        tmsl = read_tmsl_from_engine(port=port, database_name=catalog, query_executor=query_executor)
        if not tmsl or not tmsl.get("model"):
            raise SourceError(f"Could not extract model from Power BI Desktop at localhost:{port}/{catalog}")
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
        self._check_feature_flag()
        file_path = self._resolved_file_paths.get(identity.model_id)
        if not file_path or not Path(file_path).exists():
            msg = f"Report layer not extracted for '{identity.display_name}': running .pbix file path is unknown."
            logger.warning(msg)
            return ReportLayer(
                field_aliases=[],
                unresolved=[],
                ambiguous=[],
                reports=[],
                warnings=[msg],
            )

        p = Path(file_path)
        try:
            with zipfile.ZipFile(str(p), "r") as zf:
                namelist = zf.namelist()
                layout_data: Optional[Dict[str, Any]] = None

                # 1. Legacy Report/Layout
                if "Report/Layout" in namelist:
                    raw_layout = zf.read("Report/Layout")
                    layout_data = report_parsing.parse_json_candidate(raw_layout)

                # 2. PBIR format
                if not layout_data:
                    def _read_zf(n: str) -> Optional[bytes]:
                        return zf.read(n) if n in namelist else None

                    layout_data = report_parsing.pbir_layout(_read_zf, namelist)

                if not layout_data:
                    return ReportLayer(
                        field_aliases=[],
                        unresolved=[],
                        ambiguous=[],
                        reports=[p.name],
                        warnings=["No Report/Layout or PBIR definition found in .pbix archive."],
                    )

                if not tmsl:
                    tmsl = self.extract_model(identity, creds)
                model = tmsl.get("model", {})
                tables = model.get("tables", [])
                measures: List[Dict[str, Any]] = []
                for t in tables:
                    for m in t.get("measures", []):
                        m_copy = dict(m)
                        m_copy["tableName"] = t.get("name")
                        measures.append(m_copy)

                presentation_metadata, unresolved = report_parsing.parse_presentation_metadata(
                    layout_data, tables, measures
                )
                field_aliases, ambiguous = report_parsing.summarize_report_aliases(presentation_metadata)

                return ReportLayer(
                    field_aliases=field_aliases,
                    unresolved=unresolved,
                    ambiguous=ambiguous,
                    reports=[p.name],
                    warnings=[],
                )
        except Exception as exc:
            msg = f"Failed to extract report layer from '{file_path}': {exc}"
            logger.warning(msg)
            return ReportLayer(
                field_aliases=[],
                unresolved=[],
                ambiguous=[],
                reports=[p.name],
                warnings=[msg],
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
    ) -> Dict[str, int]:
        self._check_feature_flag()
        if not tmsl:
            tmsl = self.extract_model(identity, creds)
        tables = tmsl.get("model", {}).get("tables", [])
        stats: Dict[str, int] = {}
        for t in tables:
            t_name = t.get("name")
            if not t_name:
                continue
            query = f"EVALUATE ROW(\"RowCount\", COUNTROWS('{t_name}'))"
            try:
                res = self.execute_dax(target=identity, query=query, creds=creds)
                rows = res.get("rows", [])
                if rows and "RowCount" in rows[0]:
                    stats[t_name] = int(rows[0]["RowCount"] or 0)
            except Exception as exc:
                logger.warning(f"COUNTROWS failed for table '{t_name}': {exc}")
        return stats

    def execute_dax(
        self,
        target: Any,
        query: str,
        creds: Optional[SourceCredentials] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        self._check_feature_flag()
        port, catalog = self._resolve_target_port_and_catalog(target)
        res = execute_dax(query=query, backend="pbi_desktop", port=port, timeout=kwargs.get("timeout", 60))
        if not res.success:
            raise SourceQueryError(res.error or f"DAX query failed against localhost:{port}")
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
        self._check_feature_flag()
        return self.extract_model(identity, creds)
