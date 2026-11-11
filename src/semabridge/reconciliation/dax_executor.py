"""Automated DAX execution across Power BI Desktop, REST API, and XMLA backends.

Provides one uniform entrypoint ``run_dax(query, ...) -> List[Dict[str, Any]]``
plus structured metadata via ``execute_dax(...)`` so Golden Reconciliation can
run without manual JSON exports.
"""

from __future__ import annotations

import glob
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import httpx

DaxBackendType = Literal["pbi_desktop", "rest_api", "xmla"]

_PBI_PORT_PATTERNS = (
    r"%LOCALAPPDATA%\Microsoft\Power BI Desktop\AnalysisServicesWorkspaces\*\Data\msmdsrv.port.txt",
    r"%LOCALAPPDATA%\Packages\Microsoft.MicrosoftPowerBIDesktop_*\LocalState\AnalysisServicesWorkspaces\*\Data\msmdsrv.port.txt",
)
_BRACKETED_COL_RE = re.compile(r"^(?:.*\[)([^\[\]]+)\]$")


class DaxExecutionError(RuntimeError):
    """Raised when a DAX backend cannot connect or execute the requested query."""


@dataclass
class DaxExecutionResult:
    """Structured result returned by ``execute_dax``."""

    success: bool
    backend: str
    columns: List[str] = field(default_factory=list)
    rows: List[Dict[str, Any]] = field(default_factory=list)
    execution_ms: float = 0.0
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


def normalize_dax_column_names(raw_columns: List[str]) -> List[str]:
    """Normalize Analysis Services / Power BI column headers such as ``'T'[Col]`` or ``[M]``."""
    stripped: List[str] = []
    for col in raw_columns:
        s = str(col or "").strip()
        m = _BRACKETED_COL_RE.match(s)
        stripped.append(m.group(1).strip() if m else s)

    # Preserve raw name only when stripping creates duplicate headers in the same result set
    counts: Dict[str, int] = {}
    for name in stripped:
        key = name.casefold()
        counts[key] = counts.get(key, 0) + 1

    final_cols: List[str] = []
    for raw, norm in zip(raw_columns, stripped):
        if counts.get(norm.casefold(), 0) > 1:
            final_cols.append(str(raw).strip())
        else:
            final_cols.append(norm)
    return final_cols


def discover_local_pbi_ports() -> List[Dict[str, Any]]:
    """Discover local Power BI Desktop Analysis Services workspace ports from ``msmdsrv.port.txt``."""
    discovered: List[Dict[str, Any]] = []
    seen_ports: set[int] = set()
    for pat in _PBI_PORT_PATTERNS:
        expanded = os.path.expandvars(pat)
        if "%" in expanded:
            continue
        for match in sorted(glob.glob(expanded)):
            p = Path(match)
            try:
                raw_bytes = p.read_bytes()
                # PBI writes msmdsrv.port.txt as UTF-16LE (sometimes UTF-8/ASCII)
                text = raw_bytes.decode("utf-16-le", errors="ignore").strip()
                digits = "".join(ch for ch in text if ch.isdigit())
                if not digits:
                    text = raw_bytes.decode("utf-8", errors="ignore").strip()
                    digits = "".join(ch for ch in text if ch.isdigit())
                if not digits:
                    continue
                port_num = int(digits)
                if port_num <= 0 or port_num in seen_ports:
                    continue
                seen_ports.add(port_num)
                discovered.append(
                    {
                        "port": port_num,
                        "workspace_dir": p.parent.parent.name,
                        "port_file": str(p),
                    }
                )
            except OSError:
                continue
    return discovered


def _open_adomd_connection(conn_str: str):
    """Open an ADOMD.NET connection via ``pyadomd`` or ``pythonnet`` (``clr``)."""
    try:
        from pyadomd import Pyadomd  # type: ignore

        return ("pyadomd", Pyadomd(conn_str))
    except ImportError:
        pass

    try:
        import clr  # type: ignore

        dll_candidates = [
            *glob.glob(r"C:\Program Files\Microsoft Power BI Desktop\bin\Microsoft.AnalysisServices.AdomdClient.dll"),
            *glob.glob(r"C:\Program Files (x86)\Microsoft.NET\ADOMD.NET\*\Microsoft.AnalysisServices.AdomdClient.dll"),
            *glob.glob(r"C:\Windows\Microsoft.NET\assembly\GAC_MSIL\Microsoft.AnalysisServices.AdomdClient\*\Microsoft.AnalysisServices.AdomdClient.dll"),
        ]
        loaded = False
        for dll in dll_candidates:
            if Path(dll).exists():
                clr.AddReference(dll)
                loaded = True
                break
        if not loaded:
            clr.AddReference("Microsoft.AnalysisServices.AdomdClient")
        from Microsoft.AnalysisServices.AdomdClient import AdomdCommand, AdomdConnection  # type: ignore

        return ("clr", (AdomdConnection, AdomdCommand, conn_str))
    except Exception as exc:
        raise DaxExecutionError(
            "ADOMD.NET driver unavailable: install 'pyadomd' (with ADOMD.NET DLL) "
            "or use the Power BI REST 'rest_api' backend."
        ) from exc


def _run_adomd_query(conn_str: str, query: str) -> tuple[List[str], List[Dict[str, Any]]]:
    """Execute a DAX query against an Analysis Services endpoint using ADOMD.NET."""
    mode, driver = _open_adomd_connection(conn_str)
    if mode == "pyadomd":
        with driver as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                raw_cols = [desc[0] for desc in (cur.description or [])]
                cols = normalize_dax_column_names(raw_cols)
                raw_rows = cur.fetchall()
                rows = [dict(zip(cols, list(r))) for r in raw_rows]
                return cols, rows

    AdomdConnection, AdomdCommand, cstr = driver
    conn = AdomdConnection(cstr)
    conn.Open()
    try:
        cmd = AdomdCommand(query, conn)
        reader = cmd.ExecuteReader()
        try:
            raw_cols = [reader.GetName(i) for i in range(reader.FieldCount)]
            cols = normalize_dax_column_names(raw_cols)
            rows: List[Dict[str, Any]] = []
            while reader.Read():
                vals = [reader.GetValue(i) for i in range(reader.FieldCount)]
                rows.append(dict(zip(cols, vals)))
            return cols, rows
        finally:
            reader.Close()
    finally:
        conn.Close()


def _execute_pbi_desktop(query: str, port: Optional[int] = None) -> tuple[List[str], List[Dict[str, Any]], Dict[str, Any]]:
    ports = discover_local_pbi_ports()
    chosen_port = port or (ports[0]["port"] if ports else None)
    if not chosen_port:
        raise DaxExecutionError(
            "No running Power BI Desktop Analysis Services workspace found "
            "(msmdsrv.port.txt not detected). Open the .pbix file in Power BI Desktop "
            "or pass an explicit port / rest_api backend."
        )
    conn_str = f"Provider=MSOLAP;Data Source=localhost:{chosen_port};"
    cols, rows = _run_adomd_query(conn_str, query)
    return cols, rows, {"port": chosen_port}


def _acquire_powerbi_token(
    tenant_id: Optional[str],
    client_id: Optional[str],
    client_secret: Optional[str],
) -> str:
    tid = tenant_id or os.getenv("PBI_TENANT_ID") or os.getenv("FABRIC_TENANT_ID")
    cid = client_id or os.getenv("PBI_CLIENT_ID") or os.getenv("FABRIC_CLIENT_ID")
    csec = client_secret or os.getenv("PBI_CLIENT_SECRET") or os.getenv("FABRIC_CLIENT_SECRET")
    if not (tid and cid and csec):
        raise DaxExecutionError(
            "Service principal credentials missing (tenant_id, client_id, client_secret "
            "or PBI_*/FABRIC_* environment variables)."
        )
    try:
        import msal
    except ImportError as exc:
        raise DaxExecutionError("msal package is required for Power BI REST authentication.") from exc

    app = msal.ConfidentialClientApplication(
        cid,
        authority=f"https://login.microsoftonline.com/{tid}",
        client_credential=csec,
    )
    tok = app.acquire_token_for_client(scopes=["https://analysis.windows.net/powerbi/api/.default"])
    if "access_token" not in tok:
        err = tok.get("error_description") or tok.get("error") or "unknown token failure"
        raise DaxExecutionError(f"Power BI token acquisition failed: {err}")
    return str(tok["access_token"])


def _execute_rest_api(
    query: str,
    *,
    dataset_id: Optional[str],
    workspace_id: Optional[str],
    tenant_id: Optional[str],
    client_id: Optional[str],
    client_secret: Optional[str],
    timeout_seconds: int = 60,
) -> tuple[List[str], List[Dict[str, Any]], Dict[str, Any]]:
    ds_id = (dataset_id or os.getenv("PBI_DATASET_ID") or "").strip()
    ws_id = (workspace_id or os.getenv("PBI_WORKSPACE_ID") or os.getenv("FABRIC_WORKSPACE_ID") or "").strip()
    if not ds_id:
        raise DaxExecutionError("dataset_id (or PBI_DATASET_ID) is required for the rest_api backend.")

    token = _acquire_powerbi_token(tenant_id, client_id, client_secret)
    if ws_id:
        url = f"https://api.powerbi.com/v1.0/myorg/groups/{ws_id}/datasets/{ds_id}/executeQueries"
    else:
        url = f"https://api.powerbi.com/v1.0/myorg/datasets/{ds_id}/executeQueries"

    payload = {
        "queries": [{"query": query}],
        "serializerSettings": {"includeNulls": True},
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=timeout_seconds) as client:
        resp = client.post(url, json=payload, headers=headers)
    if not resp.is_success:
        raise DaxExecutionError(f"Power BI executeQueries HTTP {resp.status_code}: {resp.text[:300]}")

    body = resp.json() if resp.content else {}
    results = body.get("results") or []
    if not results:
        return [], [], {"dataset_id": ds_id, "workspace_id": ws_id or None}
    first = results[0]
    if first.get("error"):
        raise DaxExecutionError(f"Power BI DAX error: {first['error']}")

    tables = first.get("tables") or []
    if not tables:
        return [], [], {"dataset_id": ds_id, "workspace_id": ws_id or None}
    raw_rows = tables[0].get("rows") or []
    if not raw_rows:
        return [], [], {"dataset_id": ds_id, "workspace_id": ws_id or None}

    raw_cols = list(raw_rows[0].keys())
    cols = normalize_dax_column_names(raw_cols)
    normalized_rows: List[Dict[str, Any]] = []
    for raw_r in raw_rows:
        normalized_rows.append({cols[idx]: raw_r.get(raw_cols[idx]) for idx in range(len(raw_cols))})
    return cols, normalized_rows, {"dataset_id": ds_id, "workspace_id": ws_id or None}


def _execute_xmla(
    query: str,
    *,
    workspace: Optional[str],
    dataset: Optional[str],
    tenant_id: Optional[str],
    client_id: Optional[str],
    client_secret: Optional[str],
) -> tuple[List[str], List[Dict[str, Any]], Dict[str, Any]]:
    ws = (workspace or os.getenv("PBI_WORKSPACE_NAME") or "").strip()
    ds = (dataset or os.getenv("PBI_DATASET_NAME") or os.getenv("PBI_DATASET_ID") or "").strip()
    if not ws:
        raise DaxExecutionError("workspace name is required for the xmla backend.")
    tid = tenant_id or os.getenv("PBI_TENANT_ID") or os.getenv("FABRIC_TENANT_ID")
    cid = client_id or os.getenv("PBI_CLIENT_ID") or os.getenv("FABRIC_CLIENT_ID")
    csec = client_secret or os.getenv("PBI_CLIENT_SECRET") or os.getenv("FABRIC_CLIENT_SECRET")

    parts = [f"Data Source=powerbi://api.powerbi.com/v1.0/myorg/{ws}"]
    if ds:
        parts.append(f"Initial Catalog={ds}")
    if tid and cid and csec:
        parts.append(f"User ID=app:{cid}@{tid}")
        parts.append(f"Password={csec}")
    conn_str = ";".join(parts) + ";"
    cols, rows = _run_adomd_query(conn_str, query)
    return cols, rows, {"workspace": ws, "dataset": ds or None}


def execute_dax(
    query: str,
    *,
    backend: Optional[str] = None,
    port: Optional[int] = None,
    dataset_id: Optional[str] = None,
    workspace: Optional[str] = None,
    workspace_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    timeout_seconds: int = 60,
) -> DaxExecutionResult:
    """Execute a DAX query via ``pbi_desktop``, ``rest_api``, or ``xmla`` backend."""
    q_clean = (query or "").strip()
    if not q_clean:
        raise DaxExecutionError("DAX query must not be empty.")

    chosen = (backend or "").strip().lower()
    if not chosen:
        if port or discover_local_pbi_ports():
            chosen = "pbi_desktop"
        elif workspace and not workspace_id:
            chosen = "xmla"
        elif dataset_id or os.getenv("PBI_DATASET_ID"):
            chosen = "rest_api"
        else:
            chosen = "pbi_desktop"

    t0 = time.perf_counter()
    try:
        if chosen == "pbi_desktop":
            cols, rows, meta = _execute_pbi_desktop(q_clean, port=port)
        elif chosen == "rest_api":
            cols, rows, meta = _execute_rest_api(
                q_clean,
                dataset_id=dataset_id,
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                client_id=client_id,
                client_secret=client_secret,
                timeout_seconds=timeout_seconds,
            )
        elif chosen == "xmla":
            cols, rows, meta = _execute_xmla(
                q_clean,
                workspace=workspace,
                dataset=dataset_id,
                tenant_id=tenant_id,
                client_id=client_id,
                client_secret=client_secret,
            )
        else:
            raise DaxExecutionError(
                f"Unsupported DAX backend '{backend}'. Choose 'pbi_desktop', 'rest_api', or 'xmla'."
            )
        elapsed = round((time.perf_counter() - t0) * 1000.0, 2)
        return DaxExecutionResult(
            success=True,
            backend=chosen,
            columns=cols,
            rows=rows,
            execution_ms=elapsed,
            metadata=meta,
        )
    except DaxExecutionError:
        raise
    except Exception as exc:
        elapsed = round((time.perf_counter() - t0) * 1000.0, 2)
        raise DaxExecutionError(f"{chosen} execution failed after {elapsed}ms: {exc}") from exc


def run_dax(query: str, **kwargs: Any) -> List[Dict[str, Any]]:
    """Run a DAX query against the configured backend and return normalized row dicts."""
    res = execute_dax(query, **kwargs)
    return res.rows


def list_available_backends() -> Dict[str, Any]:
    """Return runtime availability summary for all three DAX backends."""
    ports = discover_local_pbi_ports()
    has_sp = bool(
        (os.getenv("PBI_TENANT_ID") or os.getenv("FABRIC_TENANT_ID"))
        and (os.getenv("PBI_CLIENT_ID") or os.getenv("FABRIC_CLIENT_ID"))
        and (os.getenv("PBI_CLIENT_SECRET") or os.getenv("FABRIC_CLIENT_SECRET"))
    )
    has_adomd = False
    try:
        __import__("pyadomd")
        has_adomd = True
    except ImportError:
        try:
            __import__("clr")
            has_adomd = True
        except ImportError:
            has_adomd = False

    return {
        "pbi_desktop": {
            "ports": ports,
            "adomd_driver_installed": has_adomd,
            "ready": bool(ports and has_adomd),
        },
        "rest_api": {
            "service_principal_configured": has_sp,
            "default_dataset_configured": bool(os.getenv("PBI_DATASET_ID")),
            "ready": has_sp,
        },
        "xmla": {
            "adomd_driver_installed": has_adomd,
            "service_principal_configured": has_sp,
            "ready": bool(has_adomd and os.getenv("PBI_WORKSPACE_NAME")),
        },
    }

