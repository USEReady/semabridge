from __future__ import annotations

import asyncio

from semabridge.api.websocket_alerts import AlertConnectionManager


class _DisconnectingWebSocket:
    def __init__(self, manager: AlertConnectionManager) -> None:
        self._manager = manager
        self.sent_payloads: list[str] = []

    async def send_text(self, payload: str) -> None:
        self.sent_payloads.append(payload)
        self._manager.disconnect(self)


class _RecordingWebSocket:
    def __init__(self) -> None:
        self.sent_payloads: list[str] = []

    async def send_text(self, payload: str) -> None:
        self.sent_payloads.append(payload)


def test_broadcast_handles_disconnect_during_iteration() -> None:
    manager = AlertConnectionManager()
    disconnecting = _DisconnectingWebSocket(manager)
    steady = _RecordingWebSocket()

    manager._active_connections.add(disconnecting)
    manager._active_connections.add(steady)

    asyncio.run(manager.broadcast({"type": "alert", "message": "boom"}))

    assert disconnecting.sent_payloads == ['{"type": "alert", "message": "boom"}']
    assert steady.sent_payloads == ['{"type": "alert", "message": "boom"}']
    assert manager.client_count == 1