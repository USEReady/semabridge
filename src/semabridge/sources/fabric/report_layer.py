"""Fabric report layer: field aliases and report-field warnings from the reports bound to a model.

The PBIX path reads the report inside the .pbix; a Fabric model's reports are
separate workspace items. This module:

1. lists the reports bound to the dataset in its workspace
   (Power BI REST ``GET /groups/{ws}/reports``, filtered on ``datasetId``);
2. fetches each report's definition (Fabric Items ``getDefinition``, polling
   the long-running operation when the API answers 202);
3. turns it into the legacy layout -- ``report.json`` (legacy format) or the
   ``definition/`` tree (PBIR) -- and parses it with the SAME parsers the PBIX
   connector uses (semabridge.sources.report_parsing).

The result fills SourceFormat.field_aliases and the two report-field warning
lists exactly like a .pbix does. A report the identity cannot read (401/403)
becomes a warning and the run continues; any other failure raises with a reason.
"""

from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from semabridge.sources import report_parsing
from semabridge.sources.base import SourceAuthError, SourceError, SourceNotFoundError, SourceQueryError
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ReportLayer:
    field_aliases: List[Dict[str, Any]] = field(default_factory=list)
    unresolved: List[Dict[str, Any]] = field(default_factory=list)
    ambiguous: List[Dict[str, Any]] = field(default_factory=list)
    reports: List[str] = field(default_factory=list)  # names of the reports that were read
    warnings: List[str] = field(default_factory=list)  # e.g. a report the identity cannot read


def _powerbi_base() -> str:
    return os.getenv("POWERBI_API_BASE", "https://api.powerbi.com").rstrip("/")


def _check(resp: Any, what: str) -> None:
    if resp.status_code in (401, 403):
        raise SourceAuthError(f"{what}: access denied (HTTP {resp.status_code}).")
    if resp.status_code == 404:
        raise SourceNotFoundError(f"{what}: not found (HTTP 404).")
    if not (200 <= resp.status_code < 300):
        raise SourceQueryError(f"{what} failed (HTTP {resp.status_code}): {str(getattr(resp, 'text', ''))[:300]}",
                               status=resp.status_code)


def list_bound_reports(extractor: Any, workspace_id: str, dataset_id: str) -> List[Dict[str, Any]]:
    """Reports in ``workspace_id`` whose ``datasetId`` is ``dataset_id``."""
    url = f"{_powerbi_base()}/v1.0/myorg/groups/{workspace_id}/reports"
    resp = extractor._request_with_auth_retry("GET", url, timeout=60, transient_retries=2, retry_delay=2.0)
    _check(resp, f"Listing reports in workspace '{workspace_id}'")
    return [r for r in (resp.json() or {}).get("value", []) if str(r.get("datasetId") or "").lower() == dataset_id.lower()]


def _decode_parts(definition: Dict[str, Any]) -> Dict[str, bytes]:
    parts = ((definition or {}).get("definition") or {}).get("parts") or []
    files: Dict[str, bytes] = {}
    for part in parts:
        path = str(part.get("path") or "").lstrip("/")
        payload = part.get("payload") or ""
        if str(part.get("payloadType") or "InlineBase64") != "InlineBase64":
            raise SourceQueryError(f"Unsupported report part payload type for '{path}': {part.get('payloadType')}")
        files[path] = base64.b64decode(payload)
    return files


def get_report_definition(
    extractor: Any,
    workspace_id: str,
    report_id: str,
    *,
    max_polls: int = 60,
    sleep: Callable[[float], None] = time.sleep,
) -> Dict[str, bytes]:
    """The report definition parts ({path: bytes}); polls the operation on 202."""
    base = str(extractor.config.api_base_url).rstrip("/")
    what = f"Report '{report_id}' definition in workspace '{workspace_id}'"
    resp = extractor._request_with_auth_retry(
        "POST", f"{base}/workspaces/{workspace_id}/reports/{report_id}/getDefinition", timeout=60,
        transient_retries=2, retry_delay=2.0,
    )
    _check(resp, what)
    if resp.status_code == 200:
        return _decode_parts(resp.json())

    location = (resp.headers or {}).get("Location") or (resp.headers or {}).get("location")
    op_id = (resp.headers or {}).get("x-ms-operation-id")
    if not location and op_id:
        location = f"{base}/operations/{op_id}"
    if not location:
        raise SourceQueryError(f"{what}: the API accepted the request but returned no operation location.")
    for _ in range(max_polls):
        delay = float((resp.headers or {}).get("Retry-After") or 2)
        sleep(delay)
        resp = extractor._request_with_auth_retry("GET", location, timeout=60, transient_retries=2, retry_delay=2.0)
        _check(resp, what)
        state = str((resp.json() or {}).get("status") or "").lower()
        if state == "succeeded":
            result = extractor._request_with_auth_retry("GET", f"{location.rstrip('/')}/result", timeout=60)
            _check(result, what)
            return _decode_parts(result.json())
        if state in ("failed", "cancelled", "canceled"):
            raise SourceQueryError(f"{what}: operation {state}: {(resp.json() or {}).get('error')}")
    raise SourceQueryError(f"{what}: operation did not finish after {max_polls} polls.")


def layout_from_definition(files: Dict[str, bytes]) -> Optional[Dict[str, Any]]:
    """Legacy ``report.json`` layout, else the PBIR ``definition/`` tree, else None."""
    legacy = files.get("report.json")
    if legacy:
        layout = report_parsing.parse_json_candidate(legacy)
        if layout and layout.get("sections"):
            return layout
    return report_parsing.pbir_layout(files.get, files.keys(), root="definition")


def _model_fields(tmsl: Dict[str, Any]):
    model = (tmsl or {}).get("model") or {}
    tables, measures = [], []
    for t in model.get("tables") or []:
        tables.append({"name": t.get("name"), "columns": [{"name": c.get("name")} for c in t.get("columns") or []]})
        measures += [{"name": m.get("name")} for m in t.get("measures") or []]
    return tables, measures


def fetch_report_layer(
    extractor: Any,
    workspace_id: str,
    dataset_id: str,
    tmsl: Dict[str, Any],
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> ReportLayer:
    """Aliases and report-field warnings from every report bound to the dataset.

    Captions are combined across reports before ambiguity is decided, so a
    caption two reports attach to different fields is reported as ambiguous,
    exactly like two visuals of one .pbix doing so.
    """
    layer = ReportLayer()
    try:
        reports = list_bound_reports(extractor, workspace_id, dataset_id)
    except SourceAuthError as exc:
        layer.warnings.append(f"Report layer skipped: cannot list reports of workspace '{workspace_id}' ({exc}).")
        logger.warning(layer.warnings[-1])
        return layer

    tables, measures = _model_fields(tmsl)
    metadata: List[Dict[str, Any]] = []
    for rep in reports:
        name = str(rep.get("name") or rep.get("id"))
        try:
            files = get_report_definition(extractor, workspace_id, str(rep["id"]), sleep=sleep)
        except SourceAuthError as exc:
            layer.warnings.append(f"Report '{name}' could not be read with this identity and was skipped ({exc}).")
            logger.warning(layer.warnings[-1])
            continue
        layout = layout_from_definition(files)
        if not layout:
            layer.warnings.append(f"Report '{name}' has no readable layout (neither report.json nor definition/pages).")
            logger.warning(layer.warnings[-1])
            continue
        presentation, unresolved = report_parsing.parse_presentation_metadata(layout, tables, measures)
        metadata += presentation
        layer.unresolved += [{**u, "report": name} for u in unresolved]
        layer.reports.append(name)
    layer.field_aliases, layer.ambiguous = report_parsing.summarize_report_aliases(metadata)
    return layer


__all__ = ["ReportLayer", "SourceError", "fetch_report_layer", "get_report_definition",
           "layout_from_definition", "list_bound_reports"]
