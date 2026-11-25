"""What this server allows the UI to show (demo data is opt-in, never default)."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/demo-status")
def demo_status() -> Dict[str, Any]:
    """Demo/mock data is available only when the server was started with
    SEMABRIDGE_DEMO_MODE=true. Even then the UI keeps it off until the user
    turns it on; every screen shows live data by default."""
    from semabridge.core.demo_mode import demo_mode_globally_armed
    from semabridge.reconciliation.mock_parity import is_mock_drift_enabled, is_mock_parity_enabled

    return {
        "demo_available": demo_mode_globally_armed(),
        "mock_parity": is_mock_parity_enabled(),
        "mock_drift": is_mock_drift_enabled(),
        "default_mode": "live",
    }
