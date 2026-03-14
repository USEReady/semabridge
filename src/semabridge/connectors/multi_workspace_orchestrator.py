"""
Multi-Workspace Fabric API Orchestrator.

Provides concurrent, resilient extraction of semantic model metadata
across multiple Microsoft Fabric workspaces. Handles:
- Array of workspace IDs with concurrent async I/O
- continuationToken pagination for large catalogs
- Exponential backoff with Retry-After header parsing for HTTP 429
- Composite primary key enforcement (artifact_id + workspace_id)

Usage:
    orchestrator = MultiWorkspaceOrchestrator(settings.fabric)
    results = orchestrator.discover_all_workspaces()
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

import requests
from requests.exceptions import RequestException

from semabridge.core.exceptions import ConnectorError, RateLimitError
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

# Default retry configuration
MAX_RETRIES = 5
INITIAL_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 60.0
DEFAULT_RETRY_AFTER = 10


class MultiWorkspaceOrchestrator:
    """Orchestrate Fabric API calls across multiple workspaces.

    Processes an array of workspace IDs concurrently while managing
    authentication, pagination, and rate limiting centrally.

    Args:
        fabric_config: FabricConfig instance with credentials and workspace IDs.
        max_workers: Maximum concurrent threads for parallel workspace processing.
    """

    def __init__(self, fabric_config: Any, max_workers: int = 4) -> None:
        self._config = fabric_config
        self._max_workers = max_workers
        self._access_token: Optional[str] = None
        self._token_expiry: float = 0.0

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------

    def discover_all_workspaces(
        self,
        workspace_ids: Optional[List[str]] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Discover semantic models from multiple workspaces concurrently.

        Args:
            workspace_ids: List of workspace GUIDs. Falls back to
                           fabric_config.all_workspace_ids if not provided.

        Returns:
            Dictionary mapping workspace_id → list of semantic model items.

        Raises:
            ConnectorError: If authentication fails.
            RateLimitError: If retries are exhausted after rate limiting.
        """
        ws_ids = workspace_ids or self._config.all_workspace_ids
        if not ws_ids:
            raise ConnectorError(
                "No workspace IDs configured",
                connector_name="MultiWorkspaceOrchestrator",
            )

        logger.info(f"Starting multi-workspace discovery for {len(ws_ids)} workspace(s)")

        results: Dict[str, List[Dict[str, Any]]] = {}

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {
                executor.submit(self._discover_workspace, ws_id): ws_id
                for ws_id in ws_ids
            }

            for future in as_completed(futures):
                ws_id = futures[future]
                try:
                    items = future.result()
                    results[ws_id] = items
                    logger.info(
                        f"Workspace {ws_id}: discovered {len(items)} items"
                    )
                except Exception as exc:
                    logger.error(f"Workspace {ws_id} failed: {exc}")
                    results[ws_id] = []

        total_items = sum(len(v) for v in results.values())
        logger.info(
            f"Multi-workspace discovery complete: {total_items} items "
            f"across {len(ws_ids)} workspaces"
        )
        return results

    def list_workspace_items(
        self,
        workspace_id: str,
        item_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List all items in a workspace with full pagination support.

        Args:
            workspace_id: Target workspace GUID.
            item_type: Optional filter (e.g., "SemanticModel", "Report").

        Returns:
            Complete list of workspace items.
        """
        return self._discover_workspace(workspace_id, item_type=item_type)

    # -------------------------------------------------------------------
    # Internal: Per-Workspace Discovery
    # -------------------------------------------------------------------

    def _discover_workspace(
        self,
        workspace_id: str,
        item_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Discover all items in a single workspace using paginated API calls.

        Args:
            workspace_id: Target workspace GUID.
            item_type: Optional item type filter.

        Returns:
            List of item dictionaries from the Fabric API.
        """
        url = f"{self._config.api_base_url}/workspaces/{workspace_id}/items"
        params: Dict[str, str] = {}
        if item_type:
            params["type"] = item_type

        all_items: List[Dict[str, Any]] = []
        continuation_token: Optional[str] = None

        while True:
            if continuation_token:
                params["continuationToken"] = continuation_token

            response = self._make_request("GET", url, params=params)
            data = response.json()

            items = data.get("value", [])
            # Tag each item with its workspace for composite key enforcement
            for item in items:
                item["_workspace_id"] = workspace_id
            all_items.extend(items)

            # Check for pagination continuation token
            continuation_token = data.get("continuationToken")
            if not continuation_token:
                break

            logger.info(
                f"Workspace {workspace_id}: paginating... "
                f"{len(all_items)} items so far"
            )

        return all_items

    # -------------------------------------------------------------------
    # Internal: HTTP Request with Exponential Backoff
    # -------------------------------------------------------------------

    def _make_request(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, str]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> requests.Response:
        """Make an HTTP request with exponential backoff for rate limiting.

        Implements resilient retry logic:
        1. On HTTP 429: parse Retry-After header, sleep, and retry.
        2. On transient errors (5xx): exponential backoff up to MAX_RETRIES.
        3. On authentication errors (401): refresh token once and retry.

        Args:
            method: HTTP method (GET, POST, etc.).
            url: Target URL.
            params: Query parameters.
            json_body: JSON request body.

        Returns:
            Successful Response object.

        Raises:
            RateLimitError: If all retries are exhausted due to 429s.
            ConnectorError: If the request fails for non-rate-limit reasons.
        """
        headers = self._get_auth_headers()
        backoff = INITIAL_BACKOFF_SECONDS

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = requests.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    json=json_body,
                    timeout=30,
                )

                if response.status_code == 200:
                    return response

                # Rate limit — respect Retry-After header
                if response.status_code == 429:
                    retry_after = int(
                        response.headers.get("Retry-After", DEFAULT_RETRY_AFTER)
                    )
                    logger.warning(
                        f"Rate limited (429). Retry-After={retry_after}s. "
                        f"Attempt {attempt}/{MAX_RETRIES}"
                    )
                    if attempt == MAX_RETRIES:
                        raise RateLimitError(
                            f"Rate limit exceeded after {MAX_RETRIES} retries",
                            retry_after_seconds=retry_after,
                            connector_name="MultiWorkspaceOrchestrator",
                        )
                    time.sleep(retry_after)
                    continue

                # Auth expired — refresh token once
                if response.status_code == 401 and attempt == 1:
                    logger.warning("Auth token expired, refreshing...")
                    self._access_token = None
                    headers = self._get_auth_headers()
                    continue

                # Server errors — exponential backoff
                if response.status_code >= 500:
                    logger.warning(
                        f"Server error {response.status_code}. "
                        f"Backoff={backoff:.1f}s. Attempt {attempt}/{MAX_RETRIES}"
                    )
                    time.sleep(backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                    continue

                # Client errors (4xx, non-429) — fail immediately
                raise ConnectorError(
                    f"Fabric API error {response.status_code}: {response.text[:200]}",
                    connector_name="MultiWorkspaceOrchestrator",
                )

            except RequestException as exc:
                logger.warning(
                    f"Network error: {exc}. "
                    f"Backoff={backoff:.1f}s. Attempt {attempt}/{MAX_RETRIES}"
                )
                if attempt == MAX_RETRIES:
                    raise ConnectorError(
                        f"Network error after {MAX_RETRIES} retries: {exc}",
                        connector_name="MultiWorkspaceOrchestrator",
                    ) from exc
                time.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)

        # Should not reach here, but satisfy the type checker
        raise ConnectorError(
            "Request loop exhausted without result",
            connector_name="MultiWorkspaceOrchestrator",
        )

    # -------------------------------------------------------------------
    # Internal: Authentication
    # -------------------------------------------------------------------

    def _get_auth_headers(self) -> Dict[str, str]:
        """Get authorization headers with a valid Bearer token.

        Returns:
            Dictionary with Authorization and Content-Type headers.
        """
        token = self._get_access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _get_access_token(self) -> str:
        """Get or refresh the Microsoft Entra ID access token.

        Uses client_credentials flow via MSAL.

        Returns:
            Valid access token string.

        Raises:
            ConnectorError: If token acquisition fails.
        """
        if self._access_token and time.time() < self._token_expiry:
            return self._access_token

        try:
            import msal
            import os

            # --- Device-code / delegated flow when no client_secret is set ---
            if self._config.client_secret is None:
                return self._get_access_token_from_credential_manager()

            client_secret = self._config.client_secret.get_secret_value()

            authority = f"https://login.microsoftonline.com/{self._config.tenant_id}"
            app = msal.ConfidentialClientApplication(
                self._config.client_id,
                authority=authority,
                client_credential=client_secret,
            )

            scopes = ["https://api.fabric.microsoft.com/.default"]
            result = app.acquire_token_for_client(scopes=scopes)

            if "access_token" not in result:
                error_desc = result.get("error_description", "Unknown error")
                raise ConnectorError(
                    f"Token acquisition failed: {error_desc}",
                    connector_name="MultiWorkspaceOrchestrator",
                )

            self._access_token = result["access_token"]
            self._token_expiry = time.time() + result.get("expires_in", 3600) - 300

            logger.info("Fabric access token acquired successfully")
            return self._access_token

        except ImportError:
            raise ConnectorError(
                "MSAL library required for Fabric authentication. "
                "Install with: pip install msal",
                connector_name="MultiWorkspaceOrchestrator",
            )

    def _get_access_token_from_credential_manager(self) -> str:
        """Return the stored device-code / delegated-flow access token.

        Falls back to a silent refresh via the stored refresh_token when the
        access_token has expired.  Raises ConnectorError if no valid token exists.
        """
        import os

        # Check FABRIC_ACCESS_TOKEN env var first (CI / pre-issued tokens).
        env_token = os.environ.get("FABRIC_ACCESS_TOKEN")
        if env_token:
            self._access_token = env_token
            self._token_expiry = time.time() + 3600
            logger.debug("MultiWorkspaceOrchestrator: using FABRIC_ACCESS_TOKEN from environment")
            return self._access_token

        try:
            from semabridge.repository.credential_manager import CredentialManager

            cm = CredentialManager()
            if cm.has_valid_token():
                token_data = cm.get_msal_token()
                self._access_token = token_data["access_token"]
                self._token_expiry = time.time() + 600
                logger.debug("MultiWorkspaceOrchestrator: using stored device-code token")
                return self._access_token

            token_data = cm.get_msal_token()
            if token_data and token_data.get("refresh_token"):
                import msal
                tenant_id = token_data.get("tenant_id", "organizations")
                app = msal.PublicClientApplication(
                    client_id="04b07795-8ddb-461a-bbee-02f9e1bf7b46",
                    authority=f"https://login.microsoftonline.com/{tenant_id}",
                )
                result = app.acquire_token_by_refresh_token(
                    token_data["refresh_token"],
                    scopes=["https://api.fabric.microsoft.com/.default"],
                )
                if "access_token" in result:
                    cm.save_msal_token(
                        access_token=result["access_token"],
                        refresh_token=result.get("refresh_token", token_data["refresh_token"]),
                        account_username=token_data.get("account_username", "unknown"),
                        tenant_id=tenant_id,
                        expires_in=result.get("expires_in", 3600),
                    )
                    self._access_token = result["access_token"]
                    self._token_expiry = time.time() + result.get("expires_in", 3600) - 300
                    logger.debug("MultiWorkspaceOrchestrator: device-code token refreshed silently")
                    return self._access_token
                else:
                    logger.warning(
                        "MultiWorkspaceOrchestrator: silent token refresh failed: %s",
                        result.get("error_description", result.get("error", "unknown")),
                    )
        except Exception as exc:
            logger.warning("MultiWorkspaceOrchestrator: device-code token lookup failed: %s", exc)

        logger.warning(
            "No valid Fabric access token found. "
            "Options: (1) sign in via the Connections panel, "
            "(2) set FABRIC_ACCESS_TOKEN env var with a pre-issued token, or "
            "(3) configure FABRIC_CLIENT_SECRET for service-principal auth."
        )
        raise ConnectorError(
            "No valid Fabric access token available. "
            "Sign in via the Connections panel or configure FABRIC_CLIENT_SECRET.",
            connector_name="MultiWorkspaceOrchestrator",
        )
