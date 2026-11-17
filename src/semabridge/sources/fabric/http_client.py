"""Fabric / Power BI HTTP client with 429 Retry-After handling and sovereign-cloud support.

Supports Microsoft sovereign (national) clouds:
- Public (Commercial / Global)
- US Government GCC
- US Government GCC High
- US Government DoD
- China (21Vianet)
- Germany (legacy)
"""

from __future__ import annotations

import email.utils
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Set

import requests
from requests.exceptions import RequestException

logger = logging.getLogger("semabridge.sources.fabric.http_client")

# Sovereign Cloud Endpoint Mappings
CLOUD_ENDPOINTS: Dict[str, Dict[str, str]] = {
    "public": {
        "fabric_api": "https://api.fabric.microsoft.com/v1",
        "powerbi_api": "https://api.powerbi.com/v1.0/myorg",
        "authority": "https://login.microsoftonline.com",
        "scope": "https://api.fabric.microsoft.com/.default",
    },
    "gcc": {
        "fabric_api": "https://api.fabric.microsoft.us/v1",
        "powerbi_api": "https://api.powerbigov.us/v1.0/myorg",
        "authority": "https://login.microsoftonline.com",
        "scope": "https://api.fabric.microsoft.us/.default",
    },
    "gcchigh": {
        "fabric_api": "https://api.high.fabric.microsoft.us/v1",
        "powerbi_api": "https://api.high.powerbigov.us/v1.0/myorg",
        "authority": "https://login.microsoftonline.us",
        "scope": "https://api.high.fabric.microsoft.us/.default",
    },
    "dod": {
        "fabric_api": "https://api.mil.fabric.microsoft.us/v1",
        "powerbi_api": "https://api.mil.powerbigov.us/v1.0/myorg",
        "authority": "https://login.microsoftonline.us",
        "scope": "https://api.mil.fabric.microsoft.us/.default",
    },
    "china": {
        "fabric_api": "https://api.fabric.microsoft.cn/v1",
        "powerbi_api": "https://api.powerbi.cn/v1.0/myorg",
        "authority": "https://login.chinacloudapi.cn",
        "scope": "https://api.fabric.microsoft.cn/.default",
    },
    "germany": {
        "fabric_api": "https://api.fabric.microsoft.de/v1",
        "powerbi_api": "https://api.powerbi.de/v1.0/myorg",
        "authority": "https://login.microsoftonline.de",
        "scope": "https://api.fabric.microsoft.de/.default",
    },
}

DEFAULT_RETRY_STATUSES: Set[int] = {408, 429, 500, 502, 503, 504}


def get_cloud_endpoints(cloud: Optional[str] = None) -> Dict[str, str]:
    """Resolve sovereign cloud endpoints for a named cloud or environment setting."""
    selected = (cloud or os.getenv("FABRIC_CLOUD_ENVIRONMENT") or "public").strip().lower()
    if selected in ("commercial", "global", "azure", "default"):
        selected = "public"
    elif selected in ("usgov", "gov"):
        selected = "gcc"
    elif selected in ("high", "usgovhigh"):
        selected = "gcchigh"
    elif selected in ("mil", "usdod"):
        selected = "dod"
    elif selected in ("mooncake", "cn"):
        selected = "china"

    endpoints = dict(CLOUD_ENDPOINTS.get(selected, CLOUD_ENDPOINTS["public"]))

    # Allow explicit environment variable overrides
    if os.getenv("FABRIC_API_BASE"):
        endpoints["fabric_api"] = os.environ["FABRIC_API_BASE"].rstrip("/")
    if os.getenv("POWERBI_API_BASE"):
        endpoints["powerbi_api"] = os.environ["POWERBI_API_BASE"].rstrip("/")

    return endpoints


def get_fabric_api_base(cloud: Optional[str] = None) -> str:
    """Get the Fabric API base URL for the active or requested cloud."""
    return get_cloud_endpoints(cloud)["fabric_api"]


def get_powerbi_api_base(cloud: Optional[str] = None) -> str:
    """Get the Power BI API base URL for the active or requested cloud."""
    return get_cloud_endpoints(cloud)["powerbi_api"]


def parse_retry_after(header_value: Optional[str], default_delay: float = 2.0) -> float:
    """Parse HTTP Retry-After header as integer seconds or RFC 2822 date.

    Returns delay in seconds (clamped to >= 0.0).
    """
    if not header_value:
        return default_delay

    raw = str(header_value).strip()
    # Try integer seconds
    try:
        seconds = float(raw)
        return max(0.0, seconds)
    except ValueError:
        pass

    # Try HTTP date
    try:
        parsed_dt = email.utils.parsedate_to_datetime(raw)
        now_dt = datetime.now(timezone.utc)
        if parsed_dt.tzinfo is None:
            parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
        diff = (parsed_dt - now_dt).total_seconds()
        return max(0.0, diff)
    except Exception:
        logger.debug("Could not parse Retry-After header: '%s'", raw)
        return default_delay


class FabricHttpClient:
    """Resilient HTTP client for Fabric and Power BI APIs.

    Handles 429 Too Many Requests with Retry-After, transient 5xx retries,
    and sovereign cloud URL resolution.
    """

    def __init__(
        self,
        cloud: Optional[str] = None,
        token_provider: Optional[Callable[[], str]] = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        default_retry_delay: float = 2.0,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.endpoints = get_cloud_endpoints(cloud)
        self.token_provider = token_provider
        self.timeout = timeout
        self.max_retries = max_retries
        self.default_retry_delay = default_retry_delay
        self.session = session or requests.Session()

    def get_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token_provider:
            token = self.token_provider()
            if token:
                headers["Authorization"] = f"Bearer {token}"
        return headers

    def request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        max_retries: Optional[int] = None,
        retry_delay: Optional[float] = None,
        **kwargs,
    ) -> requests.Response:
        """Send HTTP request with automatic 429 Retry-After and transient retry handling."""
        limit = self.max_retries if max_retries is None else max_retries
        base_delay = self.default_retry_delay if retry_delay is None else retry_delay
        combined_headers = self.get_headers()
        if headers:
            combined_headers.update(headers)

        if "timeout" not in kwargs:
            kwargs["timeout"] = self.timeout

        attempt = 0
        while True:
            try:
                response = self.session.request(method, url, headers=combined_headers, **kwargs)
            except RequestException as exc:
                if attempt < limit:
                    attempt += 1
                    logger.warning("Transient network error for %s %s (attempt %d/%d): %s", method, url, attempt, limit + 1, exc)
                    time.sleep(base_delay)
                    continue
                raise

            # 429 Rate Limit Handling
            if response.status_code == 429 and attempt < limit:
                attempt += 1
                retry_after_hdr = response.headers.get("Retry-After")
                wait_seconds = parse_retry_after(retry_after_hdr, default_delay=base_delay)
                logger.warning(
                    "HTTP 429 Rate Limited on %s %s. Waiting %0.2fs from Retry-After (attempt %d/%d)",
                    method,
                    url,
                    wait_seconds,
                    attempt,
                    limit + 1,
                )
                time.sleep(wait_seconds)
                continue

            # Other transient statuses
            if response.status_code in DEFAULT_RETRY_STATUSES and attempt < limit:
                attempt += 1
                logger.warning(
                    "HTTP %d transient error on %s %s (attempt %d/%d). Retrying in %0.2fs",
                    response.status_code,
                    method,
                    url,
                    attempt,
                    limit + 1,
                    base_delay,
                )
                time.sleep(base_delay)
                continue

            return response

