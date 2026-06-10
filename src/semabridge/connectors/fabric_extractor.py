"""
Fabric Semantic Model Extractor.

Handles authentication with Azure AD and extraction of semantic model definitions (TMSL)
from Microsoft Fabric/Power BI using the REST APIs.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import requests
from requests.exceptions import RequestException

from semabridge.core.env import get_fabric_access_token_from_env
from semabridge.core.settings import FabricConfig
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class FabricExtractionError(Exception):
    """Raised when Fabric extraction fails."""
    pass


class FabricExtractor:
    """
    Extracts semantic model definitions from Microsoft Fabric.
    """
    
    def __init__(self, config: FabricConfig, access_token: Optional[str] = None):
        """
        Initialize the extractor.
        
        Args:
            config: Fabric configuration with credentials
            access_token: Optional pre-issued/delegated bearer token from request
        """
        self.config = config
        self._access_token: Optional[str] = access_token.strip() if access_token else None
        self._token_expires_at: float = 0
        self._model_cache: dict[str, dict] = {}  # Cache for model lookups
        self._skip_env_token_once: bool = False

        if self._access_token:
            # Conservative in-memory TTL for request-provided tokens.
            # Set to 5 min so the extractor re-checks the DB before the token
            # actually expires (avoids 401s from stale in-memory tokens).
            self._token_expires_at = time.time() + 300
            logger.info("FabricExtractor initialized with request-provided access token")

    def list_workspaces(self) -> list[dict[str, Any]]:
        """List Fabric workspaces visible to the authenticated principal."""
        api_url = f"{self.config.api_base_url}/workspaces"
        try:
            response = self._request_with_auth_retry("GET", api_url, timeout=15)
            response.raise_for_status()
            return response.json().get("value", [])
        except RequestException as e:
            logger.warning("Workspace resolution: failed to list workspaces: %s", e)
            return []

    def _reset_auth_cache_for_retry(self) -> None:
        """Clear cached auth state after a 401 so the next request reacquires a token."""
        self._access_token = None
        self._token_expires_at = 0
        # If an env token is stale/revoked, bypass it once and attempt real refresh/acquire.
        self._skip_env_token_once = True

    def _request_with_auth_retry(self, method: str, url: str, **kwargs) -> requests.Response:
        """Perform an authenticated request with 401 refresh and optional transient retries."""
        transient_retries = int(kwargs.pop("transient_retries", 0) or 0)
        retry_delay = float(kwargs.pop("retry_delay", 2.0) or 0)
        retry_statuses = set(kwargs.pop("retry_statuses", (408, 429, 500, 502, 503, 504)))
        auth_retry_used = False
        attempt = 0

        while True:
            try:
                response = requests.request(method, url, headers=self._get_headers(), **kwargs)
            except RequestException as exc:
                if attempt < transient_retries:
                    attempt += 1
                    logger.warning(
                        "Fabric API transient network error for %s %s (attempt %d/%d): %s",
                        method,
                        url,
                        attempt,
                        transient_retries + 1,
                        exc,
                    )
                    if retry_delay > 0:
                        time.sleep(retry_delay)
                    continue
                raise

            if response.status_code == 401 and not auth_retry_used:
                logger.warning("Fabric API returned 401 for %s %s; resetting auth cache and retrying once", method, url)
                self._reset_auth_cache_for_retry()
                auth_retry_used = True
                continue

            if response.status_code in retry_statuses and attempt < transient_retries:
                attempt += 1
                logger.warning(
                    "Fabric API transient HTTP %s for %s %s (attempt %d/%d)",
                    response.status_code,
                    method,
                    url,
                    attempt,
                    transient_retries + 1,
                )
                if retry_delay > 0:
                    time.sleep(retry_delay)
                continue

            return response

    def resolve_workspace_id(self, workspace_id_or_name: str) -> str:
        """Resolve workspace display name to GUID. Returns original value if unresolved."""
        import re

        guid_pattern = r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
        if re.match(guid_pattern, workspace_id_or_name):
            return workspace_id_or_name

        for ws in self.list_workspaces():
            if ws.get("displayName", "").strip().lower() == workspace_id_or_name.strip().lower():
                resolved = ws.get("id", "")
                if resolved:
                    logger.info("Resolved workspace '%s' -> '%s'", workspace_id_or_name, resolved)
                    return resolved

        logger.warning("Could not resolve workspace '%s' to GUID; using as-is", workspace_id_or_name)
        return workspace_id_or_name
    
    def list_semantic_models(self) -> list[dict[str, Any]]:
        """
        List all semantic models in the workspace.
        
        Returns:
            List of model dictionaries with 'id', 'displayName', 'description', etc.
            Returns empty list if workspace has no semantic models (404 response).
        """
        workspace_id = self.resolve_workspace_id(self.config.workspace_id)

        def _normalize_models(payload: Any) -> list[dict[str, Any]]:
            if isinstance(payload, dict):
                raw_models = payload.get("value")
                if raw_models is None:
                    raw_models = payload.get("items")
                if raw_models is None:
                    raw_models = payload.get("data")
            elif isinstance(payload, list):
                raw_models = payload
            else:
                raw_models = []

            models: list[dict[str, Any]] = []
            for item in raw_models or []:
                if not isinstance(item, dict):
                    continue
                model_id = str(item.get("id") or item.get("datasetId") or item.get("semanticModelId") or "").strip()
                display_name = str(item.get("displayName") or item.get("name") or item.get("title") or model_id).strip()
                if not model_id and not display_name:
                    continue
                normalized = {
                    **item,
                    "id": model_id or display_name,
                    "displayName": display_name or model_id,
                }
                models.append(normalized)
            return models

        def _cache_models(models: list[dict[str, Any]]) -> None:
            for model in models:
                display_name = str(model.get("displayName", "")).lower()
                model_id = str(model.get("id", ""))
                if display_name:
                    self._model_cache[display_name] = model
                if model_id:
                    self._model_cache[model_id] = model

        candidate_urls = [
            ("semanticModels", f"{self.config.api_base_url}/workspaces/{workspace_id}/semanticModels"),
            ("items", f"{self.config.api_base_url}/workspaces/{workspace_id}/items?type=SemanticModel"),
            ("datasets", f"{self.config.api_base_url}/workspaces/{workspace_id}/datasets"),
        ]

        last_error: Optional[str] = None
        logger.info("Listing semantic models in workspace %s...", workspace_id)
        for source_name, api_url in candidate_urls:
            try:
                # Fabric Items API paginates at 10 items per page via continuationToken.
                # Follow all pages until no continuationToken is present.
                all_models: list[dict] = []
                next_url: Optional[str] = api_url
                page = 0
                while next_url:
                    response = self._request_with_auth_retry(
                        "GET",
                        next_url,
                        timeout=20,
                        transient_retries=2,
                        retry_delay=2,
                    )
                    if response.status_code == 404:
                        logger.warning("Fabric model list endpoint '%s' returned 404 for workspace %s", source_name, workspace_id)
                        all_models = []
                        break

                    response.raise_for_status()
                    payload = response.json()
                    page_models = _normalize_models(payload)
                    all_models.extend(page_models)
                    page += 1

                    # Fabric uses continuationToken / continuationUri for pagination
                    continuation_token = None
                    if isinstance(payload, dict):
                        continuation_token = payload.get("continuationToken") or payload.get("@odata.nextLink")
                        continuation_uri = payload.get("continuationUri")
                        if continuation_uri:
                            next_url = continuation_uri
                        elif continuation_token and not continuation_uri:
                            # Build next URL by appending continuationToken as query param
                            sep = "&" if "?" in api_url else "?"
                            next_url = f"{api_url}{sep}continuationToken={continuation_token}"
                        else:
                            next_url = None
                    else:
                        next_url = None

                models = all_models
                if not models:
                    logger.warning("Fabric model list endpoint '%s' returned no models for workspace %s", source_name, workspace_id)
                    continue

                _cache_models(models)
                logger.info("Found %d semantic models via %s (%d page(s))", len(models), source_name, page)
                return models
            except RequestException as e:
                last_error = str(e)
                # Provide actionable guidance for 401 errors — the most common SP misconfiguration.
                if hasattr(e, "response") and e.response is not None and e.response.status_code == 401:
                    body_preview = (e.response.text or "").strip()
                    if body_preview:
                        body_preview = body_preview[:300]
                    logger.warning(
                        "Fabric model list endpoint '%s' returned 401 for workspace %s. "
                        "For service-principal auth, verify: (1) 'Service principals can use Fabric APIs' "
                        "is enabled in the Fabric Admin Portal → Tenant settings, "
                        "(2) the app registration has Microsoft Fabric 'Item.Read.All' Application permission "
                        "with admin consent granted, and (3) the service principal is added as a workspace member. "
                        "Response body (truncated): %s",
                        source_name, workspace_id,
                        body_preview or "<empty>",
                    )
                else:
                    logger.warning(
                        "Fabric model list endpoint '%s' failed for workspace %s: %s%s",
                        source_name,
                        workspace_id,
                        e,
                        self._network_failure_hint(e),
                    )
                continue

        if last_error:
            raise FabricExtractionError(f"Failed to list semantic models: {last_error}")
        return []
    
    def resolve_model_id(self, dataset_id_or_name: str) -> str:
        """
        Resolve a model name or ID to the actual GUID.
        
        If already a valid GUID, returns as-is. Otherwise searches by display name.
        
        Raises:
            FabricExtractionError: If a name is provided but workspace has no semantic models.
        """
        import re
        
        # Check if it's already a GUID pattern
        guid_pattern = r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
        if re.match(guid_pattern, dataset_id_or_name):
            return dataset_id_or_name
        
        # Try cache first
        if dataset_id_or_name.lower() in self._model_cache:
            return self._model_cache[dataset_id_or_name.lower()].get("id", dataset_id_or_name)
        
        # Fetch models and search
        models = self.list_semantic_models()
        
        # If workspace has no models, raise a helpful error
        if not models:
            raise FabricExtractionError(
                f"Workspace '{self.config.workspace_id}' has no semantic models available. "
                f"Please ensure the workspace contains Power BI semantic models or data items. "
                f"Workspaces with only lakehouses, notebooks, or other non-semantic-model items will not have extractable models."
            )
        
        for model in models:
            if model.get("displayName", "").lower() == dataset_id_or_name.lower():
                logger.info(f"Resolved '{dataset_id_or_name}' to ID: {model.get('id')}")
                return model.get("id")
        
        # If no match found, raise error listing available models
        available = ", ".join([m.get("displayName", "") for m in models[:5]])
        logger.warning(f"Could not resolve '{dataset_id_or_name}' to a model ID")
        raise FabricExtractionError(
            f"Semantic model '{dataset_id_or_name}' not found in workspace '{self.config.workspace_id}'. "
            f"Available models: {available}"
        )

    def get_model_display_name(self, dataset_id: str) -> str:
        """Resolve a model ID back to its display name using cache/discovery."""
        if dataset_id in self._model_cache:
            return self._model_cache[dataset_id].get("displayName", dataset_id)
        
        # If not in cache, try one list refresh
        try:
            models = self.list_semantic_models()
            for m in models:
                if str(m.get("id")) == str(dataset_id):
                    return m.get("displayName", dataset_id)
        except Exception:
            pass
            
        return dataset_id
    
    def _get_access_token(self) -> str:
        """
        Get or refresh Azure AD access token.

        Strategy (in order):
        1. Return previously cached token if still valid (5-min buffer).
        1.5. Use FABRIC_ACCESS_TOKEN env var if set (CI / pre-issued tokens).
        2. If *client_secret* is configured, use the service-principal
           (client-credentials) flow — suitable for headless / CI usage.
        3. Otherwise fall back to the device-code token stored in the
           CredentialManager (set by the interactive Fabric login flow in the
           API).  If that token is expired it will be refreshed silently via
           the stored refresh_token.
        """
        import os

        # 1. Cached token still valid?
        if self._access_token and time.time() < self._token_expires_at - 300:
            return self._access_token

        # 1.5. Pre-issued token supplied via environment variable.
        env_token = get_fabric_access_token_from_env()
        if env_token and not self._skip_env_token_once:
            # Validate expiry from JWT 'exp' claim where possible so we don't
            # repeatedly use an expired pre-issued token (common for short-lived
            # developer tokens placed in .env). If parsing fails, fall back to
            # a conservative 1-hour TTL.
            try:
                token_valid = False
                exp_ts = None
                parts = env_token.split('.')
                if len(parts) == 3:
                    payload = parts[1]
                    import base64 as _b64
                    rem = len(payload) % 4
                    if rem:
                        payload += '=' * (4 - rem)
                    decoded = _b64.urlsafe_b64decode(payload.encode())
                    payload_json = json.loads(decoded)
                    exp_ts = int(payload_json.get('exp', 0))
                if exp_ts and exp_ts > int(time.time()) + 60:
                    token_valid = True
                    self._token_expires_at = exp_ts
                else:
                    # Token is already expired or about to expire; do not use.
                    token_valid = False
            except Exception:
                # Unable to parse JWT; assume 1 hour validity but still check
                # whether it's expired relative to current time.
                self._token_expires_at = time.time() + 3600
                token_valid = time.time() < self._token_expires_at - 60

            if token_valid:
                self._access_token = env_token
                logger.debug("Using FABRIC_ACCESS_TOKEN from environment (validated)")
                return self._access_token
            else:
                logger.debug("FABRIC_ACCESS_TOKEN from environment is expired or near-expiry; skipping to allow refresh")

        if env_token and self._skip_env_token_once:
            logger.debug("Skipping FABRIC_ACCESS_TOKEN once after a 401 to force token reacquisition")
            self._skip_env_token_once = False

        # 2. Service-principal (client-credentials) flow.
        if self.config.client_secret is not None:
            url = f"https://login.microsoftonline.com/{self.config.tenant_id}/oauth2/v2.0/token"
            data = {
                "grant_type": "client_credentials",
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret.get_secret_value(),
                "scope": "https://api.fabric.microsoft.com/.default",
            }
            try:
                logger.debug("Requesting new Azure AD access token (service principal)...")
                response = requests.post(url, data=data, timeout=20)
                response.raise_for_status()
                result = response.json()
                self._access_token = result["access_token"]
                self._token_expires_at = time.time() + result.get("expires_in", 3600)
                logger.debug("Successfully acquired service-principal access token")
                return self._access_token
            except RequestException as e:
                logger.error(f"Failed to acquire access token (service principal): {e}")
                if hasattr(e, 'response') and e.response is not None:
                    logger.error(f"Response: {e.response.text}")
                raise FabricExtractionError(f"Authentication failed: {e}")

        # 3. Device-code / delegated flow — use stored MSAL token.
        try:
            from semabridge.repository.credential_manager import CredentialManager

            cm = CredentialManager()

            token_data = cm.get_msal_token()
            if token_data and cm.has_valid_token():
                self._access_token = token_data.get("access_token")
                if self._access_token:
                    # Use 5-min in-memory TTL so the extractor re-reads from DB
                    # before the token actually expires, enabling silent refresh.
                    self._token_expires_at = time.time() + 300
                    logger.debug("Using stored device-code access token")
                    return self._access_token

            # Attempt silent refresh via stored refresh_token.
            if token_data and token_data.get("refresh_token"):
                import msal
                tenant_id = token_data.get("tenant_id", "organizations")
                authority = f"https://login.microsoftonline.com/{tenant_id}"
                app = msal.PublicClientApplication(
                    client_id="04b07795-8ddb-461a-bbee-02f9e1bf7b46",
                    authority=authority,
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
                    self._token_expires_at = time.time() + result.get("expires_in", 3600)
                    logger.debug("Device-code token refreshed silently")
                    return self._access_token
                else:
                    logger.warning(
                        "Silent token refresh failed: %s",
                        result.get("error_description", result.get("error", "unknown")),
                    )
        except Exception as exc:
            logger.warning("Device-code token lookup failed: %s", exc)

        logger.warning(
            "No valid Fabric access token found. "
            "Options: (1) sign in via the Connections panel, "
            "(2) set FABRIC_ACCESS_TOKEN env var with a pre-issued token, or "
            "(3) configure FABRIC_CLIENT_SECRET for service-principal auth."
        )
        raise FabricExtractionError(
            "No valid Fabric access token available. "
            "Sign in via the Connections panel or configure FABRIC_CLIENT_SECRET "
            "for service-principal auth."
        )
    
    def _get_headers(self) -> dict[str, str]:
        """Get standard API headers."""
        return {
            "Authorization": f"Bearer {self._get_access_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
    
    def get_model_definition(self, dataset_id: str) -> dict[str, Any]:
        """
        Get the TMSL definition of a semantic model.
        
        This handles the long-running async operation:
        1. POST /getDefinition
        2. Poll operation status
        3. GET result payload
        4. Decode Base64 model.bim
        
        Args:
            dataset_id: Model GUID or display name (will be resolved automatically)
        """
        workspace_id = self.resolve_workspace_id(self.config.workspace_id)
        
        # Resolve name to ID if needed
        resolved_id = self.resolve_model_id(dataset_id)
        if resolved_id != dataset_id:
            logger.info(f"Resolved '{dataset_id}' -> '{resolved_id}'")
        
        # 1. Initiate Export
        api_url = f"{self.config.api_base_url}/workspaces/{workspace_id}/semanticModels/{resolved_id}/getDefinition?format=TMSL"
        
        result = {}
        try:
            logger.info(f"Initiating extraction for model {dataset_id}...")
            response = self._request_with_auth_retry(
                "POST",
                api_url,
                timeout=30,
                transient_retries=2,
                retry_delay=3,
            )
            
            # Handle sync completion (rare but possible)
            if response.status_code == 200:
                payload = response.json()
                result = self._parse_definition_response(payload)
            
            # Handle async accepted
            elif response.status_code == 202:
                operation_url = response.headers.get("Location")
                retry_after = int(response.headers.get("Retry-After", "10"))
                
                if not operation_url:
                     # Fallback to operation ID if Location missing
                    op_id = response.headers.get("x-ms-operation-id")
                    if op_id:
                        operation_url = f"{self.config.api_base_url}/operations/{op_id}"
                    else:
                         raise FabricExtractionError("Async operation initiated but no Location or Operation ID returned")
                
                result = self._poll_operation(operation_url, retry_after)
            else:
                 response.raise_for_status()
                 
            # DEBUG: Save raw definition
            try:
                safe_dataset = re.sub(r"[^A-Za-z0-9_.-]", "_", str(dataset_id or resolved_id or "model"))
                safe_dataset = re.sub(r"_+", "_", safe_dataset).strip("._") or "model"
                debug_path = Path("output/debug") / safe_dataset / "raw_fabric_model.json"
                debug_path.parent.mkdir(parents=True, exist_ok=True)
                with open(debug_path, "w", encoding="utf-8") as f:
                    json.dump(result, f, indent=2)
                logger.info(f"Saved raw model definition to {debug_path}")
            except Exception as e:
                logger.warning(f"Failed to save debug artifact: {e}")
                
            return result
            
        except RequestException as e:
            raise FabricExtractionError(
                f"Failed to initiate model extraction: {e}{self._network_failure_hint(e)}"
            )

    def _network_failure_hint(self, exc: BaseException) -> str:
        message = str(exc)
        if not any(token in message for token in ("ConnectTimeout", "timed out", "Connection refused", "ProxyError")):
            return ""

        proxy_names = [
            name for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
            if os.environ.get(name) or os.environ.get(name.lower())
        ]
        proxy_hint = ""
        if proxy_names:
            proxy_hint = f" Proxy environment variables are set ({', '.join(proxy_names)}); verify they are reachable or clear them."

        return (
            " This is a network connectivity timeout before Fabric returned a response; "
            "retry the run or verify outbound access to api.fabric.microsoft.com."
            f"{proxy_hint}"
        )
    
    def _poll_operation(self, operation_url: str, retry_interval: int) -> dict[str, Any]:
        """Poll the long-running operation until completion."""
        max_retries = 30  # 5-10 minutes max depending on interval
        current_retry = 0
        
        logger.info(f"Polling operation status from {operation_url} (Interval: {retry_interval}s)...")
        
        while current_retry < max_retries:
            time.sleep(retry_interval)
            
            try:
                response = self._get_operation_response(operation_url)
                response.raise_for_status()
                
                data = response.json()
                status = data.get("status")
                
                logger.debug(f"Operation status: {status}")
                
                if status == "Succeeded":
                    # Case A: Definition is in the status body (rare for async)
                    if "definition" in data:
                        return self._parse_definition_response(data)
                    
                    # Case B: Definition is in a nested result object
                    if "result" in data and "definition" in data["result"]:
                         return self._parse_definition_response(data["result"])
                         
                    # Case C: Explicit /result endpoint (Common Fabric pattern)
                    # We assume operation_url is like .../operations/{id}
                    # Result is at .../operations/{id}/result
                    try:
                        return self._fetch_operation_result(operation_url)
                    except Exception as e:
                        if isinstance(e, FabricExtractionError):
                            raise
                        logger.warning(f"Failed to fetch operation result for {operation_url}: {e}")
                    
                    # Log full debug info if we still fail
                    logger.debug(f"Response Body: {json.dumps(data, indent=2)}")
                    status_error = self._format_operation_error(data)
                    if status_error:
                        raise FabricExtractionError(
                            f"Fabric operation completed without a model definition: {status_error}"
                        )
                    raise FabricExtractionError(
                        f"Model definition missing in operation result. Keys: {list(data.keys())}"
                    )
                
                elif status in ("Failed", "Canceled"):
                    raise FabricExtractionError(
                        f"Extraction failed: {self._format_operation_error(data) or 'Unknown Fabric operation error'}"
                    )
                
                # If still Running/NotStarted, continue loop
                current_retry += 1
                
            except RequestException as e:
                logger.warning(f"Network error during polling: {e}")
                current_retry += 1
        
        raise FabricExtractionError("Operation timed out")

    def _fetch_operation_result(self, operation_url: str) -> dict[str, Any]:
        last_error: Exception | None = None
        for candidate_url in self._operation_url_candidates(operation_url):
            result_url = f"{candidate_url.rstrip('/')}/result"
            logger.info(f"Fetching operation result from {result_url}...")
            try:
                res_response = self._request_with_auth_retry("GET", result_url, timeout=30)
                res_response.raise_for_status()
                res_data = res_response.json()

                if "definition" in res_data:
                    return self._parse_definition_response(res_data)

                if "definition" in res_data.get("result", {}):
                    return self._parse_definition_response(res_data["result"])

                result_error = self._format_operation_error(res_data)
                if result_error:
                    raise FabricExtractionError(
                        f"Fabric operation result returned an error: {result_error}"
                    )

                result_status = res_data.get("status")
                if result_status and result_status != "Succeeded":
                    raise FabricExtractionError(
                        "Fabric operation result did not contain a model definition "
                        f"(status={result_status}, keys={list(res_data.keys())})"
                    )

                raise FabricExtractionError(
                    "Model definition missing in operation result response. "
                    f"Keys: {list(res_data.keys())}"
                )
            except FabricExtractionError:
                raise
            except Exception as exc:
                last_error = exc
                logger.warning(f"Failed to fetch result from {result_url}: {exc}")
                continue

        if last_error:
            raise last_error
        raise FabricExtractionError("Operation result could not be fetched")

    def _get_operation_response(self, operation_url: str) -> requests.Response:
        """Fetch an operation status, falling back from regional redirect URLs.

        Fabric sometimes returns regional operation URLs such as
        ``wabi-...analysis.windows.net/v1/operations/{id}``. Those endpoints can
        intermittently connect-timeout while the canonical Fabric API operation
        endpoint remains reachable.
        """
        last_error: RequestException | None = None
        for candidate_url in self._operation_url_candidates(operation_url):
            try:
                return self._request_with_auth_retry("GET", candidate_url, timeout=30)
            except RequestException as exc:
                last_error = exc
                if candidate_url != operation_url:
                    logger.warning(
                        "Operation polling fallback failed for %s: %s",
                        candidate_url,
                        exc,
                    )
                continue

        if last_error:
            raise last_error
        return self._request_with_auth_retry("GET", operation_url, timeout=30)

    def _operation_url_candidates(self, operation_url: str) -> list[str]:
        candidates = [operation_url]
        try:
            parsed = urlparse(operation_url)
            path = parsed.path or ""
            marker = "/operations/"
            if marker not in path:
                return candidates

            operation_path = path[path.index(marker):].strip("/")
            api_base = str(self.config.api_base_url or "").rstrip("/")
            if api_base:
                fallback = f"{api_base}/{operation_path}"
                if fallback not in candidates:
                    candidates.append(fallback)
        except Exception:
            return candidates
        return candidates

    def _format_operation_error(self, payload: Any) -> str:
        """Return a readable Fabric error string from operation/status payloads."""
        if not isinstance(payload, dict):
            return ""

        error = payload.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "").strip()
            message = str(error.get("message") or error.get("details") or "").strip()
            if code and message:
                return f"{code}: {message}"
            return message or code
        if isinstance(error, str) and error.strip():
            return error.strip()

        result = payload.get("result")
        if isinstance(result, dict):
            return self._format_operation_error(result)

        return ""

    def _parse_definition_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Parse the definition response and extract model.bim.
        
        Response -> definition -> parts -> [path="model.bim", payload="base64...", payloadType="InlineBase64"]
        """
        definition = payload.get("definition", payload) # Handle if passed 'definition' sub-object or full payload
        parts = definition.get("parts", [])
        
        for part in parts:
            if part.get("path") == "model.bim":
                encoded_payload = part.get("payload")
                payload_type = part.get("payloadType")
                
                if payload_type == "InlineBase64":
                    try:
                        decoded_bytes = base64.b64decode(encoded_payload)
                        # BOM handling: Microsoft often adds UTF-8 BOM
                        decoded_str = decoded_bytes.decode("utf-8-sig")
                        return json.loads(decoded_str)
                    except Exception as e:
                        raise FabricExtractionError(f"Failed to decode model.bim: {e}")
                else:
                    raise FabricExtractionError(f"Unsupported payload type: {payload_type}")
        
        raise FabricExtractionError("model.bim not found in definition parts")

    def execute_dax_query(self, dataset_id: str, dax_query: str, silent: bool = False) -> list[dict[str, Any]]:
        """
        Execute a DAX query against a semantic model.
        
        Args:
            dataset_id: Model GUID
            dax_query: DAX query string
            silent: If True, suppress error logs (useful for optional queries)
            
        Returns:
            List of rows (dictionaries)
        """
        resolved_id = self.resolve_model_id(dataset_id)
        workspace_id = self.config.workspace_id
        
        # Fallback to Power BI REST API for executeQueries as it's more reliable/standard
        api_url = f"https://api.powerbi.com/v1.0/myorg/datasets/{resolved_id}/executeQueries"
        
        payload = {
            "queries": [{"query": dax_query}],
            "serializerSettings": {"includeNulls": True}
        }
        
        try:
            if not silent:
                logger.info(f"Executing DAX query on {dataset_id}...")
            response = requests.post(api_url, headers=self._get_headers(), json=payload, timeout=60)
            response.raise_for_status()
            
            # Parse response
            # Format: { "results": [ { "tables": [ { "rows": [...] } ] } ] }
            data = response.json()
            if "results" in data and len(data["results"]) > 0:
                 tables = data["results"][0].get("tables", [])
                 if tables and len(tables) > 0:
                     return tables[0].get("rows", [])
            
            return []
            
        except RequestException as e:
            if hasattr(e, 'response') and e.response is not None:
                body = e.response.text or ""
            else:
                body = ""

            # Power BI wraps hidden/auto-generated table names in <oii>…</oii>.
            # Querying these tables via the REST API always returns 400; treat
            # it as a non-issue and skip gracefully.
            is_hidden_table_error = (
                "<oii>" in body
                or "Cannot find table" in body
                or "DatasetExecuteQueriesError" in body
                and "Cannot find" in body
            )

            if is_hidden_table_error:
                logger.debug(
                    "DAX skipped hidden/auto-date table (expected): %s",
                    body[:200],
                )
            elif not silent:
                error_details = str(e)
                if body:
                    error_details += f"\nResponse Body: {body}"
                logger.warning(f"DAX Query failed: {error_details}")
            else:
                logger.debug(f"Optional DAX Query failed: {e}")
            return []

    def get_table_row_counts(self, dataset_id: str) -> dict[str, int]:
        """
        Fetch row counts for all tables in the model using DAX.
        
        Uses INFO.TABLES() or explicit COUNTROWS for robustness.
        """
        # Query to get all tables and their cardinality
        # Note: INFO.TABLES() is a DMV that returns metadata including estimated cardinality
        dax = """
        EVALUATE 
        SELECTCOLUMNS(
            FILTER(
                INFO.TABLES(),
                [IsHidden] = false
            ),
            "TableName", [Name], 
            "RowCount", [Cardinality]
        )
        """
        
        try:
            # Silent execution because DMVs are sometimes restricted on REST API
            rows = self.execute_dax_query(dataset_id, dax, silent=True)
            counts = {}
            for row in rows:
                t_name = row.get("TableName")
                # Cardinality might be missing or explicitly row count
                count = row.get("RowCount") or row.get("[RowCount]") or 0
                if t_name:
                    counts[t_name] = int(count)
            
            if counts:
                logger.info(f"Fetched row counts for {len(counts)} tables")
            return counts
            
        except Exception as e:
            logger.warning(f"Failed to fetch row counts: {e}")
            return {}

    # =========================================================================
    # Measure Sync Query Methods (Complex DAX Support)
    # =========================================================================
    
    # API limits
    MAX_ROWS_PER_QUERY = 100000
    MAX_QUERY_TIMEOUT_SEC = 225
    
    # Name prefixes that Power BI uses for auto-generated hidden date tables.
    # These tables are NOT queryable via the REST API and must be excluded from
    # DAX queries (attempting to reference them returns a 400 with <oii> tags).
    _AUTO_DATE_TABLE_PREFIXES = (
        "DateTableTemplate_",
        "LocalDateTable_",
        "DateTable_",
    )

    @classmethod
    def _is_auto_date_dimension(cls, dim_column: str) -> bool:
        """Return True if *dim_column* references a known hidden auto-date table.

        Examples of auto-date columns::

            "'DateTableTemplate_abc123'[Year]"
            "'LocalDateTable_xyz'[Month]"
        """
        # Strip leading quote/apostrophe and extract the table name
        table_part = dim_column.split("[")[0].strip().strip("'")
        return any(table_part.startswith(p) for p in cls._AUTO_DATE_TABLE_PREFIXES)

    def execute_measure_sync_query(
        self,
        dataset_id: str,
        measure_name: str,
        group_by_dimensions: list[str],
        filters: Optional[dict[str, Any]] = None,
        use_fallback_pattern: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Execute a DAX query specifically for syncing a measure to Snowflake.
        
        Constructs a proper EVALUATE statement with dimension context to avoid
        context transition errors common with SUMMARIZECOLUMNS.
        
        Args:
            dataset_id: Semantic model ID
            measure_name: The measure to evaluate (e.g., [Sales YTD])
            group_by_dimensions: Columns to group by (e.g., ["'Date'[Year]", "'Region'[Name]"])
            filters: Optional static filters to apply
            use_fallback_pattern: If True, use ADDCOLUMNS(SUMMARIZE()) instead of SUMMARIZECOLUMNS
            
        Returns:
            List of row dictionaries
        """
        # Strip dimensions that reference hidden auto-date tables. Power BI
        # auto-generates tables like DateTableTemplate_* / LocalDateTable_*
        # — they cannot be queried via REST and will always produce a 400 with
        # "<oii>TableName</oii>" in the error body.
        visible_dims = [
            d for d in group_by_dimensions
            if not self._is_auto_date_dimension(d)
        ]
        if len(visible_dims) < len(group_by_dimensions):
            skipped = set(group_by_dimensions) - set(visible_dims)
            logger.debug(
                "execute_measure_sync_query: filtered out auto-date dimensions "
                "for measure '%s': %s",
                measure_name,
                skipped,
            )
        if not visible_dims:
            logger.debug(
                "execute_measure_sync_query: skipping measure '%s' — "
                "all requested dimensions were auto-date tables.",
                measure_name,
            )
            return []
        group_by_dimensions = visible_dims

        dim_string = ", ".join(group_by_dimensions)
        
        if use_fallback_pattern:
            # Safe pattern for complex measures with context transition issues
            # ADDCOLUMNS wraps CALCULATE to force proper context transition
            base_table = group_by_dimensions[0].split('[')[0].strip("'") if group_by_dimensions else "Date"
            dax = f"""
EVALUATE
ADDCOLUMNS(
    SUMMARIZE('{base_table}', {dim_string}),
    "Value", CALCULATE({measure_name})
)
"""
        else:
            # Standard SUMMARIZECOLUMNS pattern (faster but stricter on context)
            dax = f"""
EVALUATE
SUMMARIZECOLUMNS(
    {dim_string},
    "Value", {measure_name}
)
"""
        
        # Add filters if specified
        if filters:
            filter_clauses = []
            for col, val in filters.items():
                if isinstance(val, str):
                    filter_clauses.append(f'{col} = "{val}"')
                elif isinstance(val, (int, float)):
                    filter_clauses.append(f'{col} = {val}')
            if filter_clauses:
                filter_str = " && ".join(filter_clauses)
                if use_fallback_pattern:
                    # Add FILTER to SUMMARIZE
                    dax = dax.replace(
                        f"SUMMARIZE('{base_table}'",
                        f"FILTER(SUMMARIZE('{base_table}'"
                    ).rstrip(")") + f", {filter_str}))"
                else:
                    # Add FILTER to SUMMARIZECOLUMNS
                    dax = dax.replace(
                        "SUMMARIZECOLUMNS(",
                        f"SUMMARIZECOLUMNS(FILTER(ALL({dim_string.split(',')[0]}), {filter_str}), "
                    )
        
        logger.debug(f"Executing measure sync DAX: {dax[:300]}...")
        return self.execute_dax_query(dataset_id, dax)
    
    def execute_paginated_measure_sync(
        self,
        dataset_id: str,
        measure_name: str,
        group_by_dimensions: list[str],
        partition_column: str,
        partition_values: list[Any],
        parallel: bool = False,
        max_workers: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Execute measure sync with pagination to avoid 100k row limit.
        
        Partitions the query by a dimension (typically Date or Year) and
        combines results. Automatically switches to fallback pattern on
        context transition errors.
        
        When parallel=True, partitions are queried concurrently via
        TracedThreadPoolExecutor for significant latency reduction.
        
        Args:
            dataset_id: Semantic model ID
            measure_name: The measure to evaluate
            group_by_dimensions: Columns to group by
            partition_column: Column to partition by (e.g., "'Date'[Year]")
            partition_values: Values to iterate (e.g., [2020, 2021, 2022, 2023])
            parallel: Enable concurrent partition queries
            max_workers: Number of worker threads for parallel mode
            
        Returns:
            Combined list of all row dictionaries
        """
        if parallel and len(partition_values) > 1:
            return self._execute_partitions_parallel(
                dataset_id=dataset_id,
                measure_name=measure_name,
                group_by_dimensions=group_by_dimensions,
                partition_column=partition_column,
                partition_values=partition_values,
                max_workers=max_workers,
            )
        
        return self._execute_partitions_sequential(
            dataset_id=dataset_id,
            measure_name=measure_name,
            group_by_dimensions=group_by_dimensions,
            partition_column=partition_column,
            partition_values=partition_values,
        )
    
    def _execute_partitions_sequential(
        self,
        dataset_id: str,
        measure_name: str,
        group_by_dimensions: list[str],
        partition_column: str,
        partition_values: list[Any],
    ) -> list[dict[str, Any]]:
        """Execute partition queries sequentially (legacy behavior)."""
        all_results = []
        use_fallback = False
        
        for partition_val in partition_values:
            filters = {partition_column: partition_val}
            
            try:
                rows = self.execute_measure_sync_query(
                    dataset_id=dataset_id,
                    measure_name=measure_name,
                    group_by_dimensions=group_by_dimensions,
                    filters=filters,
                    use_fallback_pattern=use_fallback,
                )
                all_results.extend(rows)
                logger.debug(f"Partition {partition_val}: {len(rows)} rows")
                
            except Exception as e:
                error_msg = str(e).lower()
                
                # Context transition error - switch to fallback pattern
                if "summarizecolumns" in error_msg or "context" in error_msg or "cannot be used" in error_msg:
                    if not use_fallback:
                        logger.warning(f"Context error for {measure_name}, switching to fallback pattern")
                        use_fallback = True
                        # Retry this partition with fallback
                        rows = self.execute_measure_sync_query(
                            dataset_id=dataset_id,
                            measure_name=measure_name,
                            group_by_dimensions=group_by_dimensions,
                            filters=filters,
                            use_fallback_pattern=True,
                        )
                        all_results.extend(rows)
                    else:
                        logger.error(f"Fallback also failed for partition {partition_val}: {e}")
                        raise
                else:
                    logger.error(f"Failed to sync partition {partition_val}: {e}")
                    raise
        
        logger.info(f"Synced {len(all_results)} total rows for measure {measure_name}")
        return all_results
    
    def _execute_partitions_parallel(
        self,
        dataset_id: str,
        measure_name: str,
        group_by_dimensions: list[str],
        partition_column: str,
        partition_values: list[Any],
        max_workers: int = 5,
    ) -> list[dict[str, Any]]:
        """Execute partition queries concurrently via TracedThreadPoolExecutor.
        
        Each partition is submitted as an independent DAX query to a thread
        pool. Results are aggregated in partition order after all futures
        complete. Errors are collected and raised as AggregatedError.
        """
        from semabridge.utils.concurrency import (
            TracedThreadPoolExecutor,
            fan_in_results,
        )
        
        logger.info(
            f"Parallel partition sync: {len(partition_values)} partitions "
            f"with {max_workers} workers for measure {measure_name}"
        )
        
        def _query_partition(partition_val: Any) -> list[dict[str, Any]]:
            """Execute a single partition query (runs in worker thread)."""
            filters = {partition_column: partition_val}
            try:
                rows = self.execute_measure_sync_query(
                    dataset_id=dataset_id,
                    measure_name=measure_name,
                    group_by_dimensions=group_by_dimensions,
                    filters=filters,
                    use_fallback_pattern=False,
                )
                logger.debug(f"Partition {partition_val}: {len(rows)} rows")
                return rows
            except Exception as e:
                error_msg = str(e).lower()
                # Context transition error - retry with fallback pattern
                if "summarizecolumns" in error_msg or "context" in error_msg or "cannot be used" in error_msg:
                    logger.warning(f"Context error for partition {partition_val}, using fallback")
                    rows = self.execute_measure_sync_query(
                        dataset_id=dataset_id,
                        measure_name=measure_name,
                        group_by_dimensions=group_by_dimensions,
                        filters=filters,
                        use_fallback_pattern=True,
                    )
                    return rows
                raise
        
        tasks = [lambda pv=pv: _query_partition(pv) for pv in partition_values]
        
        with TracedThreadPoolExecutor(max_workers=max_workers) as executor:
            partition_results = fan_in_results(
                executor=executor,
                tasks=tasks,
                task_label="DAX partition query",
            )
        
        # Flatten partition results into a single list
        all_results = []
        for rows in partition_results:
            all_results.extend(rows)
        
        logger.info(f"Parallel sync complete: {len(all_results)} total rows for {measure_name}")
        return all_results
    
    def estimate_measure_cardinality(
        self,
        dataset_id: str,
        group_by_dimensions: list[str]
    ) -> int:
        """
        Estimate the result cardinality for a measure query.
        
        Uses COUNTROWS on the dimension cross-product to estimate
        whether pagination is needed.
        
        Returns:
            Estimated row count
        """
        dim_string = ", ".join(group_by_dimensions)
        dax = f"""
EVALUATE
ROW("EstimatedRows", COUNTROWS(SUMMARIZE(ALL(), {dim_string})))
"""
        try:
            rows = self.execute_dax_query(dataset_id, dax)
            if rows:
                return int(rows[0].get("EstimatedRows", 0) or 0)
        except Exception as e:
            logger.warning(f"Cardinality estimation failed: {e}")
        
        return 0
    
    def get_date_dimension_values(
        self,
        dataset_id: str,
        date_column: str = "'Date'[Year]"
    ) -> list[Any]:
        """
        Get distinct values from a date dimension for partitioning.
        
        Returns:
            List of distinct values (e.g., [2020, 2021, 2022, 2023])
        """
        dax = f"""
EVALUATE
DISTINCT({date_column})
ORDER BY {date_column}
"""
        try:
            rows = self.execute_dax_query(dataset_id, dax)
            col_name = date_column.split('[')[1].rstrip(']')
            values = [row.get(f"[{col_name}]") or row.get(col_name) for row in rows]
            return [v for v in values if v is not None]
        except Exception as e:
            logger.warning(f"Failed to get date dimension values: {e}")
            return []
