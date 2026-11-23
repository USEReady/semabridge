"""The one place the Databricks planner path does HTTP.

``HttpTransport`` is a tiny interface so tests can replace the network with
a fake Databricks (no sockets). ``RequestsTransport`` is the real one.
Responses carry the decoded JSON body (or None) and never echo headers, so
tokens cannot leak through errors or logs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: Any = None                 # decoded JSON, or None
    text: str = ""                   # raw text (first 2 KB), for messages
    retry_after: Optional[float] = None


class TransportError(ConnectionError):
    """The request could not be completed (DNS, connect, read timeout, ...).

    ``sent`` is False when the request certainly never reached the server
    (e.g. connection refused), so even a write may safely be retried.
    """

    def __init__(self, message: str, *, sent: bool) -> None:
        super().__init__(message)
        self.sent = sent


class HttpTransport:
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        json: Any = None,
        data: Optional[Mapping[str, str]] = None,
        params: Optional[Mapping[str, Any]] = None,
        timeout: float = 30,
    ) -> HttpResponse:  # pragma: no cover - interface
        raise NotImplementedError


class RequestsTransport(HttpTransport):
    def __init__(self) -> None:
        import requests

        self._requests = requests
        self._session = requests.Session()

    def request(self, method, url, *, headers=None, json=None, data=None, params=None, timeout=30) -> HttpResponse:
        requests = self._requests
        try:
            resp = self._session.request(
                method, url, headers=dict(headers or {}), json=json, data=data, params=params, timeout=timeout
            )
        except requests.exceptions.ConnectTimeout as exc:
            raise TransportError(f"connect timeout: {type(exc).__name__}", sent=False) from None
        except requests.exceptions.ConnectionError as exc:
            # Only "could not establish a connection" (refused, DNS) is known
            # not to have reached the server; an aborted connection may have.
            text = str(exc)
            never_sent = "NewConnectionError" in text or "Failed to establish" in text or "NameResolution" in text
            raise TransportError(f"connection error: {type(exc).__name__}", sent=not never_sent) from None
        except requests.exceptions.Timeout as exc:
            raise TransportError(f"read timeout: {type(exc).__name__}", sent=True) from None
        except requests.exceptions.RequestException as exc:
            raise TransportError(f"request failed: {type(exc).__name__}", sent=True) from None
        try:
            body: Dict[str, Any] | None = resp.json()
        except ValueError:
            body = None
        retry_after = None
        raw_retry = resp.headers.get("Retry-After")
        if raw_retry:
            try:
                retry_after = float(raw_retry)
            except ValueError:
                retry_after = None
        return HttpResponse(status=resp.status_code, body=body, text=(resp.text or "")[:2048], retry_after=retry_after)
