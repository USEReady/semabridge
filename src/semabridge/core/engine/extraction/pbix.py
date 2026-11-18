from __future__ import annotations

import time
import uuid
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Literal, Optional, Tuple
import yaml
from pydantic import Field
from semabridge.connectors.snowflake_emitter import MissingSourceTableWarning
from semabridge.core.settings import Settings, get_settings
from semabridge.core.config_loader import get_project_file_path
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.run_summary import (
    STEP_NAMES,
    RunStatus,
    RunSummary,
    StepStatus,
    create_run_summary,
)
from semabridge.core.source_format import (
    SourceFormat,
    from_fabric_tmsl,
    from_pbix_tmsl,
    from_snowflake_metadata,
)
from semabridge.intermediate.models import OSIModel
from semabridge.sml.models import SMLModel, SMLRelationship
from semabridge.repository.model_repository import ModelRepository
from semabridge.utils.logger import get_logger
from semabridge.utils.relationship_naming import generate_relationship_name
from semabridge.core.engine.context import RunContext
from semabridge.core.engine.exceptions import (
    ConfigValidationError,
    AuthenticationError,
    ExtractionError,
    NoSourceTablesError,
    SourceFormatError,
    ConversionError,
    PersistenceError,
    DeploymentError,
)

logger = get_logger(__name__)

def _extract_pbix(
    self,
    context: RunContext,
    pbix_path: Optional[str],
) -> SourceFormat:
    """Extract from a local .pbix file."""
    from semabridge.connectors.local_pbix_connector import LocalPBIXConnector

    if not pbix_path:
        source_cfg = getattr(context.config, "source", None)
        pbix_path = (
            str(getattr(source_cfg, "pbix_path", "") or "").strip()
            or str(getattr(source_cfg, "pbix_file_path", "") or "").strip()
            or str(getattr(source_cfg, "source_path", "") or "").strip()
            or str(getattr(source_cfg, "file_path", "") or "").strip()
        )

    if not pbix_path:
        raise ExtractionError(
            "pbix_path is required for PBIX source. "
            "Provide it directly or via source.pbix_path/source.source_path/source.file_path."
        )

    pbix_path = str(pbix_path).strip()
    if len(pbix_path) >= 2 and (
        (pbix_path[0] == '"' and pbix_path[-1] == '"')
        or (pbix_path[0] == "'" and pbix_path[-1] == "'")
    ):
        pbix_path = pbix_path[1:-1].strip()
    pbix_path = os.path.expandvars(os.path.expanduser(pbix_path))

    p = Path(pbix_path)
    if not p.exists():
        stem = p.stem
        clean_stem = re.sub(r"^[0-9a-fA-F]{32}_", "", stem)
        candidates = list(Path.cwd().glob(f"Examples/**/{clean_stem}*.pbix")) + list(Path.cwd().glob(f"Examples/**/{stem}*.pbix"))
        if candidates and candidates[0].exists():
            p = candidates[0].resolve()
            pbix_path = str(p)
        else:
            raise ExtractionError(f"PBIX file not found: {pbix_path}")

    # Multi-file dispatch (sync_execution_service._run_single_job) calls
    # engine.execute() once per PBIX file, each with its own resolved
    # per-job pbix_path, but context.config.source was built once from the
    # shared, project-level config_dict at Step 1 -- it still names
    # whichever file (or none) that dict's source.pbix_path pointed at.
    # Later in this same run, deployment/snowflake.py's opt-in data-backfill
    # reads context.config.source.pbix_path/source_path/file_path to decide
    # what to load from -- without this, every model in a multi-file first
    # sync would backfill from that one shared path instead of its own file.
    # Writing the actually-resolved path back here keeps both readers in
    # sync regardless of whether pbix_path came from the explicit per-job
    # argument or the source_cfg fallback above.
    source_cfg = getattr(context.config, "source", None)
    if source_cfg is None:
        source_cfg = SimpleNamespace()
        object.__setattr__(context.config, "source", source_cfg)
    source_cfg.pbix_path = pbix_path

    drop_ledger = getattr(context, "drop_ledger", None)
    from semabridge.sources.base import ModelIdentity, get_source_adapter
    adapter = get_source_adapter("pbix")
    ident = ModelIdentity(source_type="pbix", model_id=pbix_path, display_name=p.name)
    connector = LocalPBIXConnector({"pbix_path": pbix_path}, drop_ledger=drop_ledger)
    discovered = connector.discover()
    tmsl = adapter.extract_model(ident, drop_ledger=drop_ledger)

    if not tmsl:
        raise ExtractionError(
            f"Could not extract DataModelSchema from {pbix_path}"
        )
    if not (tmsl.get("model", {}) or {}).get("tables"):
        # A model with no tables can only deploy an empty semantic view; the
        # dry run used to report it as "success" with 0 fields. Typical cause:
        # a thin report connected to a dataset in the Power BI service.
        message, reason_code = _empty_pbix_model_message(pbix_path)
        raise NoSourceTablesError(message, reason_code=reason_code)

    logger.debug(
        "PBIX -> TMSL transition complete for %s (%s tables)",
        pbix_path,
        len(tmsl.get("model", {}).get("tables", [])),
    )
    try:
        model_obj = tmsl.get("model", {})
        table_defs = model_obj.get("tables", []) or []
        table_names = [str(t.get("name", "")).strip() or "<unnamed>" for t in table_defs]
        measure_count = sum(len((t.get("measures", []) or [])) for t in table_defs if isinstance(t, dict))
        logger.info(
            "PBIX extraction debug: model=%s, tables=%s, measures=%s, table_names=%s",
            model_obj.get("name", "<unnamed-model>"),
            len(table_defs),
            measure_count,
            table_names,
        )
    except Exception as exc:
        logger.debug("PBIX extraction debug summary skipped: %s", exc)

    source_format = from_pbix_tmsl(
        project_id=context.project_id,
        run_id=context.run_id,
        tmsl=tmsl,
        pbix_path=pbix_path,
        field_aliases=discovered.get("field_aliases", []),
        unresolved_report_field_references=discovered.get("unresolved_report_field_references", []),
        ambiguous_report_aliases=discovered.get("ambiguous_report_aliases", []),
    )

    table_count = len(tmsl.get("model", {}).get("tables", []))
    self._record_step(
        4, StepStatus.SUCCESS,
        f"Extracted {table_count} tables from {p.name}"
    )

    return source_format


def _live_connection_targets(connections: Dict[str, Any]) -> List[str]:
    """Published model(s) a thin report connects to, from its Connections
    part. Power BI writes this in several shapes: RemoteArtifacts[].DatasetId,
    Connections[].PbiModelDatabaseName / PbiServiceModelId, or an
    "Initial Catalog=" in Connections[].ConnectionString."""
    found: List[str] = []
    for item in connections.get("RemoteArtifacts") or []:
        if isinstance(item, dict) and item.get("DatasetId"):
            found.append(str(item["DatasetId"]))
    for conn in connections.get("Connections") or []:
        if not isinstance(conn, dict):
            continue
        for key in ("PbiModelDatabaseName", "PbiServiceModelId"):
            if conn.get(key):
                found.append(str(conn[key]))
        for part in str(conn.get("ConnectionString") or "").split(";"):
            k, _, v = part.partition("=")
            if k.strip().lower() == "initial catalog" and v.strip():
                found.append(v.strip())
    return list(dict.fromkeys(found))


def _empty_pbix_model_message(pbix_path: str) -> Tuple[str, str]:
    """(message, reason_code) for a PBIX that yielded no tables. Names the
    published dataset when the file is a thin report (LIVE_CONNECTION)."""
    import json
    import zipfile

    name = Path(str(pbix_path)).name
    try:
        with zipfile.ZipFile(pbix_path) as archive:
            if "Connections" in archive.namelist():
                connections = json.loads(archive.read("Connections").decode("utf-8-sig") or "{}")
                remote = _live_connection_targets(connections) if isinstance(connections, dict) else []
                if remote:
                    return (
                        f"'{name}' contains no tables: it is a thin report connected to Power BI service "
                        f"dataset {', '.join(remote)}. Add that dataset through a Fabric/Power BI source, "
                        "or export the report with its data model.",
                        "LIVE_CONNECTION",
                    )
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        logger.debug("Could not read Connections from %s: %s", pbix_path, exc)
    return (
        f"'{name}' contains no tables, so there is nothing to sync. Check that the file includes its data model.",
        "NO_TABLES",
    )
