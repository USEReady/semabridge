"""
WebSocket Alert Service.

Provides event-driven notification delivery from the backend to frontend
clients over WebSocket connections. Hooks into the Python logging system
to automatically dispatch warning and error events as JSON payloads.

Architecture:
    logger.warning() → WebSocketAlertHandler (logging.Handler)
                     → WebSocket broadcast → Frontend Toast notification

Usage:
    # In FastAPI app setup:
    from semabridge.api.websocket_alerts import alert_router, get_alert_manager
    app.include_router(alert_router)

    # Warnings are automatically forwarded to connected WebSocket clients
    logger.warning("Model X has unsupported M code")  # → Toast in UI
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

alert_router = APIRouter(tags=["alerts"])


# ---------------------------------------------------------------------------
# Connection Manager — tracks active WebSocket clients
# ---------------------------------------------------------------------------

class AlertConnectionManager:
    """Manages active WebSocket connections for alert broadcasting.

    Thread-safe broadcast to all connected frontend clients.
    """

    def __init__(self) -> None:
        self._active_connections: Set[WebSocket] = set()
        self._alert_history: List[Dict[str, Any]] = []
        self._max_history = 100

    async def connect(self, websocket: WebSocket) -> None:
        """Accept a new WebSocket connection.

        Args:
            websocket: The WebSocket connection to accept.
        """
        await websocket.accept()
        self._active_connections.add(websocket)
        logger.info(
            f"WebSocket client connected. "
            f"Active connections: {len(self._active_connections)}"
        )

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a disconnected WebSocket client.

        Args:
            websocket: The WebSocket connection to remove.
        """
        self._active_connections.discard(websocket)
        logger.info(
            f"WebSocket client disconnected. "
            f"Active connections: {len(self._active_connections)}"
        )

    async def broadcast(self, message: Dict[str, Any]) -> None:
        """Broadcast a JSON message to all connected clients.

        Args:
            message: The alert payload to broadcast.
        """
        # Store in history
        self._alert_history.append(message)
        if len(self._alert_history) > self._max_history:
            self._alert_history = self._alert_history[-self._max_history:]

        disconnected: Set[WebSocket] = set()
        payload = json.dumps(message)

        for connection in self._active_connections:
            try:
                await connection.send_text(payload)
            except Exception:
                disconnected.add(connection)

        # Cleanup dead connections
        self._active_connections -= disconnected

    def get_recent_alerts(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent alert history.

        Args:
            limit: Maximum number of recent alerts to return.

        Returns:
            List of recent alert payloads, newest first.
        """
        return list(reversed(self._alert_history[-limit:]))

    @property
    def client_count(self) -> int:
        """Number of active WebSocket connections."""
        return len(self._active_connections)


# Singleton manager
_alert_manager = AlertConnectionManager()


def get_alert_manager() -> AlertConnectionManager:
    """Get the singleton alert connection manager."""
    return _alert_manager


# ---------------------------------------------------------------------------
# Logging Handler — bridges Python logging → WebSocket
# ---------------------------------------------------------------------------

class WebSocketAlertHandler(logging.Handler):
    """Custom logging handler that dispatches warnings/errors to WebSocket.

    Attached to the root logger, this handler intercepts WARNING and above
    log records and asynchronously broadcasts them to all connected
    frontend clients as structured JSON payloads.

    The handler runs the async broadcast in a fire-and-forget manner
    to avoid blocking the synchronous logging pipeline.
    """

    def __init__(self, manager: AlertConnectionManager, level: int = logging.WARNING) -> None:
        super().__init__(level)
        self._manager = manager

    def emit(self, record: logging.LogRecord) -> None:
        """Emit a log record as a WebSocket alert.

        Args:
            record: The logging.LogRecord to broadcast.
        """
        if self._manager.client_count == 0:
            return  # No clients connected — skip the overhead

        # Filter out sync logs (they are already captured in Run-specific logs)
        sync_loggers = (
            "semabridge.connectors.",
            "semabridge.converter.",
            "semabridge.core.execution_engine",
            "semabridge.core.engine",
            "semabridge.core.concurrency",
            "semabridge.sync.",
        )
        if record.name.startswith(sync_loggers):
            return

        alert_payload: Dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "type": "alert",
            "severity": self._map_severity(record.levelno),
            "level": record.levelname,
            "message": record.getMessage(),
            "source": record.name,
            "function": record.funcName,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "thread": record.threadName,
            "dismissible": True,
        }

        # Fire-and-forget async broadcast
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._manager.broadcast(alert_payload))
        except RuntimeError:
            # No running event loop — schedule it differently
            pass

    @staticmethod
    def _map_severity(level: int) -> str:
        """Map Python log level to UI severity for color-coding.

        Args:
            level: Python logging level integer.

        Returns:
            Severity string: 'info', 'warning', 'error', or 'critical'.
        """
        if level >= logging.CRITICAL:
            return "critical"
        if level >= logging.ERROR:
            return "error"
        if level >= logging.WARNING:
            return "warning"
        return "info"


def install_websocket_alert_handler() -> WebSocketAlertHandler:
    """Install the WebSocket alert handler on the root logger.

    Returns:
        The installed handler instance.
    """
    handler = WebSocketAlertHandler(_alert_manager)
    logging.getLogger().addHandler(handler)
    logger.info("WebSocket alert handler installed on root logger")
    return handler


# ---------------------------------------------------------------------------
# FastAPI WebSocket Endpoint
# ---------------------------------------------------------------------------

@alert_router.websocket("/ws/alerts")
async def websocket_alerts_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time alert notifications.

    Frontend clients connect to this endpoint to receive live
    warning and error notifications from the backend. Messages
    are JSON-encoded with the following structure:

    {
        "id": "uuid",
        "type": "alert",
        "severity": "warning" | "error" | "critical",
        "level": "WARNING",
        "message": "Human-readable diagnostic",
        "source": "semabridge.connectors.fabric_extractor",
        "timestamp": "2026-03-01T09:00:00Z",
        "dismissible": true
    }
    """
    await _alert_manager.connect(websocket)
    try:
        while True:
            # Keep the connection alive; client can send pings
            data = await websocket.receive_text()
            # Optional: handle client commands (e.g., acknowledge alerts)
            if data == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        _alert_manager.disconnect(websocket)


@alert_router.get("/api/alerts/recent")
async def get_recent_alerts(limit: int = 20) -> List[Dict[str, Any]]:
    """Get recent alert history via REST endpoint.

    Args:
        limit: Maximum number of recent alerts.

    Returns:
        List of recent alert payloads.
    """
    return _alert_manager.get_recent_alerts(limit)
