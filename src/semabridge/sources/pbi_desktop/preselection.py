"""Pre-selected source validation for Power BI Desktop External Tools launches."""

from __future__ import annotations

import logging
import re
from typing import Callable, List, Optional, Tuple

from semabridge.sources.base import SourceError, SourceNotFoundError
from semabridge.sources.pbi_desktop.discovery import DiscoveredDesktopInstance, discover_running_instances

logger = logging.getLogger(__name__)

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def parse_desktop_server(server: str) -> Tuple[str, Optional[int]]:
    """Parse server string into (host, port).

    Accepts:
      - localhost:51234 -> ("localhost", 51234)
      - 127.0.0.1:51234 -> ("127.0.0.1", 51234)
      - localhost -> ("localhost", None)
      - [::1]:51234 -> ("::1", 51234)
    """
    s = str(server or "").strip()
    if not s:
        raise SourceError("Server address is empty or not provided.")

    # Strip protocol if accidentally passed (e.g. http://)
    s = re.sub(r"^https?://", "", s, flags=re.IGNORECASE).rstrip("/")

    # Check IPv6 bracketed host with port: [::1]:port
    m_ipv6 = re.match(r"^\[([a-fA-F0-9:]+)\](?::(\d+))?$", s)
    if m_ipv6:
        host = m_ipv6.group(1)
        port = int(m_ipv6.group(2)) if m_ipv6.group(2) else None
        return host, port

    if ":" in s:
        parts = s.split(":", 1)
        host = parts[0].strip()
        try:
            port = int(parts[1].strip())
        except ValueError:
            raise SourceError(f"Invalid server port in '{server}'")
        return host, port

    return s, None


def validate_preselected_desktop_source(
    server: str,
    database: str,
    instances_provider: Optional[Callable[[], List[DiscoveredDesktopInstance]]] = None,
) -> DiscoveredDesktopInstance:
    """Validate a pre-selected Power BI Desktop instance from launcher params.

    Rules:
    1. Accept ONLY localhost / 127.0.0.1 (and ::1) servers. Never connect to a non-local address.
    2. Accept ONLY an engine currently found by discovery with that database name.
    3. If the file/engine is no longer running, raise SourceNotFoundError("That file isn't open anymore").

    Returns the matched DiscoveredDesktopInstance.
    """
    host, port = parse_desktop_server(server)

    # Security rule: Never connect to a non-local address
    if host.lower() not in LOCAL_HOSTS:
        raise SourceError(
            f"Non-local server rejected: '{host}'. Power BI Desktop adapter only connects to localhost / 127.0.0.1."
        )

    clean_db = str(database or "").strip()
    if not clean_db:
        raise SourceError("Database identifier is empty or not provided.")

    instances = (
        instances_provider()
        if instances_provider is not None
        else discover_running_instances(verify_active=True)
    )

    matched: Optional[DiscoveredDesktopInstance] = None
    for inst in instances:
        db_match = inst.database_name.lower() == clean_db.lower()
        port_match = (port is None) or (inst.port == port)
        if db_match and port_match:
            matched = inst
            break

    if not matched:
        raise SourceNotFoundError(
            f"That file isn't open anymore (no running Power BI Desktop engine on {host}"
            + (f":{port}" if port else "")
            + f" with database '{clean_db}')."
        )

    return matched
