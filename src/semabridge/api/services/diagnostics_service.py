"""
Network Diagnostics Service.
Provides utilities to check DNS resolution and TCP reachability for required endpoints.
"""

from __future__ import annotations

import socket
import time
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
from urllib.parse import urlparse

from semabridge.utils.logger import get_logger
from semabridge.core.settings import get_settings

logger = get_logger(__name__)

@dataclass
class DiagnosticResult:
    target: str
    host: str
    port: int
    dns_resolved: bool
    ip_address: Optional[str]
    tcp_connected: bool
    latency_ms: float
    error: Optional[str] = None

class DiagnosticsService:
    """
    Service for diagnosing network connectivity to external systems.
    """

    DEFAULT_TARGETS = [
        {"name": "Microsoft Auth", "url": "https://login.microsoftonline.com"},
        {"name": "Fabric API", "url": "https://api.fabric.microsoft.com"},
        {"name": "Power BI API", "url": "https://api.powerbi.com"},
    ]

    def diagnose_all(self, extra_targets: Optional[List[Dict[str, str]]] = None) -> List[DiagnosticResult]:
        """
        Run diagnostics on all default targets and optional extra targets.
        """
        targets = self.DEFAULT_TARGETS.copy()
        if extra_targets:
            targets.extend(extra_targets)

        # Add Snowflake host if configured
        try:
            settings = get_settings()
            if settings.snowflake.account:
                # Basic snowflake host construction: {account}.snowflakecomputing.com
                # Note: This is a simplification; real hosts can vary.
                host = settings.snowflake.account
                if "." not in host:
                    host = f"{host}.snowflakecomputing.com"
                targets.append({"name": "Snowflake Account", "url": f"https://{host}"})
        except Exception as exc:
            logger.debug("Could not add Snowflake to diagnostics targets: %s", exc)

        results = []
        for target in targets:
            results.append(self.diagnose_target(target["name"], target["url"]))
        
        return results

    def diagnose_target(self, name: str, url: str) -> DiagnosticResult:
        """
        Diagnose a single target URL.
        """
        parsed = urlparse(url)
        host = parsed.hostname or url
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        
        dns_resolved = False
        ip_address = None
        tcp_connected = False
        latency_ms = -1.0
        error = None
        
        start_time = time.perf_counter()
        
        try:
            # 1. DNS Check
            try:
                ip_address = socket.gethostbyname(host)
                dns_resolved = True
            except socket.gaierror as e:
                error = f"DNS Resolution Failed: {e}"
                return DiagnosticResult(name, host, port, False, None, False, -1.0, error)

            # 2. TCP Check
            try:
                with socket.create_connection((host, port), timeout=5) as sock:
                    tcp_connected = True
                    end_time = time.perf_counter()
                    latency_ms = (end_time - start_time) * 1000
            except (socket.timeout, ConnectionRefusedError) as e:
                error = f"TCP Connection Failed: {e}"
            except Exception as e:
                error = f"Unexpected Error: {e}"

        except Exception as e:
            error = str(e)

        return DiagnosticResult(
            target=name,
            host=host,
            port=port,
            dns_resolved=dns_resolved,
            ip_address=ip_address,
            tcp_connected=tcp_connected,
            latency_ms=round(latency_ms, 2) if latency_ms > 0 else -1.0,
            error=error
        )
