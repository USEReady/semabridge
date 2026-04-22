"""
Diagnostics Router.
Exposes network health check endpoints.
"""

from __future__ import annotations

from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from semabridge.api.services.diagnostics_service import DiagnosticsService, DiagnosticResult

router = APIRouter(prefix="/health", tags=["diagnostics"])

class DiagnosticResponse(BaseModel):
    target: str
    host: str
    port: int
    dns_resolved: bool
    ip_address: Optional[str]
    tcp_connected: bool
    latency_ms: float
    error: Optional[str] = None

@router.get("/diagnose", response_model=List[DiagnosticResponse])
def run_diagnostics(
    service: DiagnosticsService = Depends(DiagnosticsService)
):
    """
    Run network diagnostics for all critical endpoints.
    """
    results = service.diagnose_all()
    # Convert dataclasses to pydantic-friendly dicts
    return [
        {
            "target": r.target,
            "host": r.host,
            "port": r.port,
            "dns_resolved": r.dns_resolved,
            "ip_address": r.ip_address,
            "tcp_connected": r.tcp_connected,
            "latency_ms": r.latency_ms,
            "error": r.error
        }
        for r in results
    ]

@router.get("/proxy-check")
def check_proxy_config():
    """
    Check current proxy configuration status (masking sensitive URLs).
    """
    from semabridge.core.settings import get_settings
    net = get_settings().network
    
    return {
        "https_proxy_configured": bool(net.https_proxy),
        "http_proxy_configured": bool(net.http_proxy),
        "no_proxy_configured": bool(net.no_proxy),
        "active_proxies": list(net.proxies.keys())
    }
