"""
Fabric Semantic Model Extractor.

Handles authentication with Azure AD and extraction of semantic model definitions (TMDL)
from Microsoft Fabric/Power BI using the REST APIs.
"""

from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import Any, Optional

import requests
from requests.exceptions import RequestException

from semabridge.adapters.tmsl_translator import translate_tmsl_to_internal_sml
from semabridge.core.env import get_fabric_access_token_from_env
from semabridge.core.settings import FabricConfig
from semabridge.sml.models import SMLModel
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
            # Conservative lifetime for request-provided tokens.
            self._token_expires_at = time.time() + 600
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
        """Perform one authenticated request with a single retry on HTTP 401."""
        response = requests.request(method, url, headers=self._get_headers(), **kwargs)
        if response.status_code != 401:
            return response

        logger.warning("Fabric API returned 401 for %s %s; resetting auth cache and retrying once", method, url)
        self._reset_auth_cache_for_retry()
        retry_response = requests.request(method, url, headers=self._get_headers(), **kwargs)
        return retry_response

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
                response = self._request_with_auth_retry("GET", api_url, timeout=15)
                if response.status_code == 404:
                    logger.warning("Fabric model list endpoint '%s' returned 404 for workspace %s", source_name, workspace_id)
                    continue

                response.raise_for_status()
                models = _normalize_models(response.json())
                if not models:
                    logger.warning("Fabric model list endpoint '%s' returned no models for workspace %s", source_name, workspace_id)
                    continue

                _cache_models(models)
                logger.info("Found %d semantic models via %s", len(models), source_name)
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
                    logger.warning("Fabric model list endpoint '%s' failed for workspace %s: %s", source_name, workspace_id, e)
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
                    # CredentialManager already checks expiry; use a conservative TTL.
                    self._token_expires_at = time.time() + 600
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
        Get the TMDL definition of a semantic model.
        
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
        # Request TMDL definition format for the semantic model payload.
        api_url = f"{self.config.api_base_url}/workspaces/{workspace_id}/semanticModels/{resolved_id}/getDefinition?format=TMDL"
        
        result = {}
        try:
            logger.info(f"Initiating extraction for model {dataset_id}...")
            response = requests.post(api_url, headers=self._get_headers(), timeout=30)
            
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
            raise FabricExtractionError(f"Failed to initiate model extraction: {e}")

    def get_semantic_model(self, dataset_id: str) -> SMLModel:
        """Get semantic model in internal SML form from Fabric TMDL ingress."""
        resolved_id = self.resolve_model_id(dataset_id)
        raw_tmdl_payload = self.get_model_definition(resolved_id)
        row_counts = self.get_table_row_counts(resolved_id)
        workspace_id = self.resolve_workspace_id(self.config.workspace_id)
        return translate_tmsl_to_internal_sml(
            raw_tmdl_payload,
            workspace_id=workspace_id,
            dataset_id=resolved_id,
            row_counts=row_counts,
        )
    
    def _poll_operation(self, operation_url: str, retry_interval: int) -> dict[str, Any]:
        """Poll the long-running operation until completion."""
        max_retries = 30  # 5-10 minutes max depending on interval
        current_retry = 0
        
        logger.info(f"Polling operation status from {operation_url} (Interval: {retry_interval}s)...")
        
        while current_retry < max_retries:
            time.sleep(retry_interval)
            
            try:
                response = requests.get(operation_url, headers=self._get_headers(), timeout=30)
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

                    # Case C: Definition is behind a result URL (varies by Fabric endpoint)
                    result_url_candidates: list[str] = []

                    # Poll response headers can expose canonical result URL
                    for header_name in ("Location", "Operation-Location", "x-ms-operation-result-url"):
                        header_url = response.headers.get(header_name)
                        if header_url:
                            result_url_candidates.append(header_url)

                    # Response body may include explicit links
                    for key in ("resultUrl", "resourceLocation", "location"):
                        candidate = data.get(key)
                        if isinstance(candidate, str) and candidate:
                            result_url_candidates.append(candidate)

                    # Default fallback if no explicit location is returned
                    result_url_candidates.append(f"{operation_url}/result")

                    # Preserve order while removing duplicates
                    unique_result_urls: list[str] = []
                    seen_urls: set[str] = set()
                    for candidate in result_url_candidates:
                        if candidate not in seen_urls:
                            unique_result_urls.append(candidate)
                            seen_urls.add(candidate)

                    for result_url in unique_result_urls:
                        logger.info(f"Fetching operation result from {result_url}...")
                        try:
                            # Some result endpoints are also long-running operations.
                            # Poll a handful of times before giving up on this URL.
                            for _ in range(6):
                                res_response = requests.get(result_url, headers=self._get_headers(), timeout=30)
                                res_response.raise_for_status()
                                res_data = res_response.json()

                                if "definition" in res_data:
                                    return self._parse_definition_response(res_data)

                                if "definition" in res_data.get("result", {}):
                                    return self._parse_definition_response(res_data["result"])

                                res_status = res_data.get("status")
                                if res_status in ("Failed", "Canceled"):
                                    error = res_data.get("error", {})
                                    raise FabricExtractionError(
                                        f"Extraction failed while fetching result: "
                                        f"{error.get('message', 'Unknown error')} ({error.get('code')})"
                                    )

                                # Some URLs return another status envelope first; if a follow-up
                                # location is present, attempt it in this same loop.
                                follow_up_url = (
                                    res_response.headers.get("Location")
                                    or res_response.headers.get("Operation-Location")
                                    or res_data.get("resultUrl")
                                    or res_data.get("resourceLocation")
                                    or res_data.get("location")
                                )
                                if isinstance(follow_up_url, str) and follow_up_url and follow_up_url not in seen_urls:
                                    unique_result_urls.append(follow_up_url)
                                    seen_urls.add(follow_up_url)

                                if res_status in ("Running", "NotStarted", "InProgress", None):
                                    time.sleep(retry_interval)
                                    continue
                                break
                        except FabricExtractionError:
                            # Preserve semantic errors from nested result polling.
                            raise
                        except Exception as e:
                            logger.warning(f"Failed to fetch result from {result_url}: {e}")
                    
                    # Log full debug info if we still fail
                    logger.debug(f"Response Body: {json.dumps(data, indent=2)}")
                    if isinstance(data.get("error"), dict):
                        err = data["error"]
                        msg = err.get("message", "Unknown error")
                        code = err.get("code")
                        raise FabricExtractionError(
                            f"Extraction failed: {msg} ({code})"
                        )
                    raise FabricExtractionError(f"Model definition missing in operation result. Keys: {list(data.keys())}")
                
                elif status in ("Failed", "Canceled"):
                    error = data.get("error", {})
                    raise FabricExtractionError(f"Extraction failed: {error.get('message', 'Unknown error')} ({error.get('code')})")
                
                # If still Running/NotStarted, continue loop
                current_retry += 1
                
            except RequestException as e:
                logger.warning(f"Network error during polling: {e}")
                current_retry += 1
        
        raise FabricExtractionError("Operation timed out")

    def _parse_definition_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Parse the definition response and extract the semantic model JSON.

        Primary expectation is a ``model.bim`` part payload (base64 JSON).
        Some API responses vary part path casing/separators, so matching is
        normalized and case-insensitive.
        """
        definition = payload.get("definition", payload)  # Handle sub-object or full payload
        parts = definition.get("parts", [])

        def _decode_json_part(part: dict[str, Any]) -> dict[str, Any]:
            encoded_payload = part.get("payload")
            payload_type = part.get("payloadType")
            if payload_type != "InlineBase64":
                raise FabricExtractionError(f"Unsupported payload type: {payload_type}")
            try:
                decoded_bytes = base64.b64decode(encoded_payload)
                decoded_str = decoded_bytes.decode("utf-8-sig")
                parsed = json.loads(decoded_str)
            except Exception as e:
                raise FabricExtractionError(f"Failed to decode model definition payload: {e}")
            if not isinstance(parsed, dict):
                raise FabricExtractionError("Decoded model definition payload is not a JSON object")
            return parsed

        candidate_parts: list[dict[str, Any]] = []
        discovered_paths: list[str] = []

        for part in parts:
            part_path = str(part.get("path", ""))
            discovered_paths.append(part_path)
            normalized_path = part_path.replace("\\", "/").strip().lower()
            if normalized_path == "model.bim" or normalized_path.endswith("/model.bim"):
                return _decode_json_part(part)
            candidate_parts.append(part)

        # Fallback: some service variants may not expose model.bim with a canonical
        # path. Try decoding other JSON-like parts and select one that has a model.
        for part in candidate_parts:
            try:
                parsed = _decode_json_part(part)
            except FabricExtractionError:
                continue
            if isinstance(parsed.get("model"), dict):
                return parsed

        # Optional TOM integration: if an environment supports the
        # Tabular Object Model (TOM) via pythonnet/.NET, let it try to
        # produce a canonical model representation. If unavailable or
        # it fails, fall back to the resilient text-based parser below.
        try:
            from semabridge.adapters.tom_integration import parse_tmdl_with_tom
            from semabridge.repository.model_repository import ModelRepository

            tmdl_model = parse_tmdl_with_tom(parts, sidecar_url=self.config.tom_sidecar_url)
            if tmdl_model is not None:
                # Optionally run shadow-mode parity validator (non-fatal)
                try:
                    import os
                    if os.getenv("SEMABRIDGE_SHADOW_MODE", "false").lower() == "true":
                        from semabridge.adapters.shadow_validator import compare_tom_and_fallback
                        try:
                            parity = compare_tom_and_fallback(parts, cfg=self.config)
                            # Save a small debug artifact for operator inspection
                            try:
                                dbg_path = Path("output/debug") / "shadow_mode" / (str(time.time()).replace('.', '_') + "_parity.json")
                                dbg_path.parent.mkdir(parents=True, exist_ok=True)
                                with open(dbg_path, "w", encoding="utf-8") as _f:
                                    json.dump(parity, _f, indent=2)
                            except Exception:
                                pass
                            logger.info("Shadow-mode parity: %s", parity.get('parity'))
                        except Exception as e:
                            logger.warning("Shadow-mode validator failed: %s", e)
                        # Persist parity report to repository and update consecutive pass counts
                        try:
                            repo = ModelRepository()
                            # resolved_id might not be known here; best-effort: try to update by dataset_id if present
                            dataset_hint = None
                            if isinstance(tmdl_model, dict):
                                dataset_hint = tmdl_model.get('model', {}).get('name')
                            if dataset_hint:
                                repo.update_tom_parity(dataset_hint, parity, tom_used=True, required_passes=self.config.tom_parity_required_passes)
                        except Exception as e:
                            logger.debug("Failed to persist parity report to repository: %s", e)
                except Exception:
                    # Keep this hook non-fatal if environment checks/imports fail
                    pass
                return tmdl_model
        except Exception:
            # Defensive: don't let optional integration raise during parsing.
            logger.debug("TOM integration raised an exception; falling back to text parser")

        # TMDL package fallback: build a model-like JSON payload from
        # definition/*.tmdl parts so downstream converters can continue.
        tmdl_model = self._parse_tmdl_package_parts(parts)
        if tmdl_model is not None:
            # Optionally run shadow-mode parity validator (non-fatal)
            try:
                import os
                if os.getenv("SEMABRIDGE_SHADOW_MODE", "false").lower() == "true":
                    from semabridge.adapters.shadow_validator import compare_tom_and_fallback

                    try:
                        parity = compare_tom_and_fallback(parts, cfg=self.config)
                        try:
                            dbg_path = Path("output/debug") / "shadow_mode" / (str(time.time()).replace('.', '_') + "_parity.json")
                            dbg_path.parent.mkdir(parents=True, exist_ok=True)
                            with open(dbg_path, "w", encoding="utf-8") as _f:
                                json.dump(parity, _f, indent=2)
                        except Exception:
                            pass
                        logger.info("Shadow-mode parity: %s", parity.get('parity'))
                    except Exception as e:
                        logger.warning("Shadow-mode validator failed: %s", e)
            except Exception:
                pass
            return tmdl_model

        raise FabricExtractionError(
            "model.bim not found in definition parts; discovered paths: "
            f"{discovered_paths}"
        )

    @staticmethod
    def _strip_tmdl_identifier(raw: str) -> str:
        return FabricExtractor._normalize_tmdl_identifier(raw)

    @staticmethod
    def _normalize_tmdl_identifier(raw: str) -> str:
        """Normalize a TMDL identifier to a canonical simple name.

        - Strips surrounding quotes or double-quotes
        - Removes surrounding whitespace and trailing colons
        - If fully-qualified (db.schema.table), returns the last segment (table)
        - Collapses multiple internal whitespace to single spaces
        """
        if not raw:
            return ""
        text = str(raw).strip().rstrip(":")

        # Remove surrounding quotes if present
        if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
            text = text[1:-1]

        # If bracket-qualified like [Table] or [Schema].[Table], remove brackets
        text = text.replace("[", "").replace("]", "")

        # If fully-qualified (a.b.c), take last segment as the table name
        if "." in text:
            parts = [p for p in text.split(".") if p]
            if parts:
                text = parts[-1]

        # Collapse repeated whitespace and normalize underscores/spaces
        text = " ".join(text.split())
        return text.strip()

    @staticmethod
    def _parse_tmdl_relationship_endpoint(raw: str) -> tuple[str, str] | None:
        """
        Parse TMDL relationship endpoint into (table, column).

        Handles all forms emitted by the Fabric API:
          - Standard TMDL dot notation:  TableName.'Column Name'
          - Standard TMDL dot notation:  TableName.ColumnName
          - Legacy bracket notation:     'Table Name'[Column Name]
          - Legacy bracket notation:     TableName[ColumnName]
        """
        text = (raw or "").strip()
        if not text:
            return None
        # If bracket-style: 'Table Name'[Column Name] or Schema.'Table Name'[Column]
        bracket_match = re.match(r"^\s*([^\[]+)\[([^\]]+)\]\s*$", text)
        if bracket_match:
            raw_table = bracket_match.group(1).strip()
            raw_col = bracket_match.group(2).strip()
            table_name = FabricExtractor._normalize_tmdl_identifier(raw_table)
            column_name = FabricExtractor._normalize_tmdl_identifier(raw_col)
            if table_name and column_name:
                return table_name, column_name

        # Dot notation: take last '.' split as table/column. Handles schema.table.'Column Name'
        if "." in text:
            # Split on last dot to allow dots in qualifiers
            left, right = text.rsplit(".", 1)
            table_name = FabricExtractor._normalize_tmdl_identifier(left)
            column_name = FabricExtractor._normalize_tmdl_identifier(right)
            if table_name and column_name:
                return table_name, column_name

        # Fallback: attempt to find quoted pair via regex
        simple_match = re.match(r"^\s*['\"]?([^'\"]+)['\"]?\s*[,\.]?\s*['\"]?([^'\"]+)['\"]?\s*$", text)
        if simple_match:
            table_name = FabricExtractor._normalize_tmdl_identifier(simple_match.group(1))
            column_name = FabricExtractor._normalize_tmdl_identifier(simple_match.group(2))
            if table_name and column_name:
                return table_name, column_name

        return None

    def _parse_tmdl_package_parts(self, parts: list[dict[str, Any]]) -> dict[str, Any] | None:
        decoded_text_by_path: dict[str, str] = {}
        decoded_json_by_path: dict[str, dict[str, Any]] = {}
        for part in parts or []:
            path = str(part.get("path", "")).replace("\\", "/").strip()
            payload_type = part.get("payloadType")
            payload = part.get("payload")
            if not path or payload_type != "InlineBase64" or not payload:
                continue
            try:
                decoded = base64.b64decode(payload).decode("utf-8-sig")
                decoded_text_by_path[path] = decoded
                try:
                    parsed_json = json.loads(decoded)
                    if isinstance(parsed_json, dict):
                        decoded_json_by_path[path] = parsed_json
                except Exception:
                    pass
            except Exception:
                continue

        if not decoded_text_by_path:
            return None

        tmdl_paths = [p for p in decoded_text_by_path if p.lower().endswith(".tmdl")]
        if not tmdl_paths:
            return None

        model_name = "FabricModel"
        pbism_path = next(
            (p for p in decoded_json_by_path if p.replace("\\", "/").lower().endswith("definition.pbism")),
            None,
        )
        if pbism_path:
            pbism = decoded_json_by_path.get(pbism_path) or {}
            candidate = (
                pbism.get("displayName")
                or pbism.get("name")
                or ((pbism.get("model") or {}).get("name") if isinstance(pbism.get("model"), dict) else None)
            )
            if isinstance(candidate, str) and candidate.strip():
                model_name = candidate.strip()

        model_tmdl_path = next(
            (p for p in tmdl_paths if p.replace("\\", "/").lower().endswith("definition/model.tmdl")),
            None,
        )
        if model_tmdl_path:
            for line in decoded_text_by_path[model_tmdl_path].splitlines():
                m = re.match(r"^\s*model\s+(.+?)\s*$", line)
                if m:
                    parsed_name = self._strip_tmdl_identifier(m.group(1))
                    if parsed_name:
                        model_name = parsed_name
                    break

        tables: list[dict[str, Any]] = []
        for path, text in decoded_text_by_path.items():
            normalized = path.replace("\\", "/").lower()
            if "/tables/" not in normalized or not normalized.endswith(".tmdl"):
                continue

            table_name = Path(path).stem
            columns: list[dict[str, Any]] = []
            measures: list[dict[str, Any]] = []
            current_measure: dict[str, str] | None = None

            for line in text.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue

                col_match = re.match(r"^\s*(?:column|calculatedColumn)\s+(.+?)\s*$", line)
                if col_match:
                    current_measure = None
                    col_name = self._strip_tmdl_identifier(col_match.group(1))
                    if col_name:
                        columns.append({"name": col_name, "dataType": "string"})
                    continue

                measure_match = re.match(r"^\s*measure\s+(.+?)\s*$", line)
                if measure_match:
                    raw_measure = self._strip_tmdl_identifier(measure_match.group(1))
                    measure_name = raw_measure
                    measure_expr = ""
                    if "=" in raw_measure:
                        name_part, expr_part = raw_measure.split("=", 1)
                        measure_name = self._strip_tmdl_identifier(name_part)
                        measure_expr = expr_part.strip()
                    if measure_name:
                        current_measure = {"name": measure_name, "expression": measure_expr}
                        measures.append(current_measure)
                    continue

                expr_match = re.match(r"^\s*expression\s*:\s*(.+?)\s*$", line)
                if expr_match and current_measure is not None:
                    current_measure["expression"] = expr_match.group(1).strip()
                    continue

                # Continuation lines for multi-line measure expressions.
                if current_measure is not None and not re.match(
                    r"^\s*(?:table|column|calculatedColumn|measure|partition|annotation|lineageTag|formatString|displayFolder)\b",
                    line,
                ):
                    prior = str(current_measure.get("expression") or "").strip()
                    continuation = stripped
                    current_measure["expression"] = f"{prior} {continuation}".strip() if prior else continuation

                table_match = re.match(r"^\s*table\s+(.+?)\s*$", line)
                if table_match:
                    current_measure = None
                    parsed_table_name = self._strip_tmdl_identifier(table_match.group(1))
                    if parsed_table_name:
                        table_name = parsed_table_name

            tables.append({"name": table_name, "columns": columns, "measures": measures})

        rels: list[dict[str, Any]] = []
        # Relationships in a TMDL package live in a dedicated `relationships.tmdl`
        # file (or occasionally inline in `model.tmdl`).  The Fabric API returns
        # paths with a variable-depth prefix (e.g. "SemanticModel/definition/…"
        # or "MyModel.SemanticModel/definition/…"), so we must use a substring
        # check rather than startswith("definition/").
        rel_paths = [
            p
            for p in decoded_text_by_path
            if p.replace("\\", "/").lower().endswith(".tmdl")
            and (
                "/definition/" in p.replace("\\", "/").lower()
                or p.replace("\\", "/").lower().startswith("definition/")
            )
        ]
        for rel_path in sorted(rel_paths):
            rel_text = decoded_text_by_path[rel_path]
            current_rel: dict[str, Any] | None = None
            for line in rel_text.splitlines():
                # ── Format 1 (legacy / inline): single-line arrow notation ──────
                # relationship 'FromTable'[FromCol] -> 'ToTable'[ToCol]
                m = re.match(
                    r"^\s*relationship\s+['\"]?([^'\"]+)['\"]?\[([^\]]+)\]\s*->\s*['\"]?([^'\"]+)['\"]?\[([^\]]+)\]",
                    line,
                )
                if m:
                    from_table, from_column, to_table, to_column = m.groups()
                    rels.append(
                        {
                            "name": f"REL_{from_table}_{from_column}__{to_table}_{to_column}",
                            "fromTable": from_table.strip(),
                            "fromColumn": from_column.strip(),
                            "toTable": to_table.strip(),
                            "toColumn": to_column.strip(),
                            "isActive": True,
                        }
                    )
                    current_rel = None
                    continue

                # ── Format 2 (standard TMDL): block declaration ──────────────
                # relationship <guid-or-name>
                #     fromColumn: TableName.'ColumnName'
                #     toColumn: TableName.'ColumnName'
                #     isActive: false          (optional; default true)
                #     crossFilteringBehavior: bothDirections  (optional)
                #     cardinality: manyToOne   (optional)
                #
                # Guard: a line that starts with "relationship" but also contains
                # a colon is a property line (e.g. "relationshipType: ..."), not a
                # new relationship declaration.
                rel_decl = re.match(r"^\s*relationship\s+(\S+)\s*$", line)
                if rel_decl:
                    rel_name = self._strip_tmdl_identifier(rel_decl.group(1)) or f"REL_{len(rels)+1}"
                    current_rel = {"name": rel_name, "isActive": True}
                    rels.append(current_rel)
                    continue

                if current_rel is not None:
                    kv = re.match(
                        r"^\s*(fromTable|fromColumn|toTable|toColumn|isActive"
                        r"|crossFilteringBehavior|crossFilterBehavior|cardinality)\s*:\s*(.+?)\s*$",
                        line,
                    )
                    if kv:
                        key, raw_val = kv.groups()
                        val = self._strip_tmdl_identifier(raw_val)
                        if key == "isActive":
                            current_rel[key] = str(val).strip().lower() != "false"
                        elif key in ("fromColumn", "toColumn"):
                            # Real TMDL format: "TableName.'ColumnName'" or "TableName[ColumnName]"
                            endpoint = self._parse_tmdl_relationship_endpoint(raw_val)
                            if endpoint:
                                tbl_name, col_name = endpoint
                                current_rel["fromTable" if key == "fromColumn" else "toTable"] = tbl_name
                                current_rel[key] = col_name
                            else:
                                current_rel[key] = val
                        else:
                            # crossFilteringBehavior, cardinality, fromTable, toTable
                            current_rel[key] = val
                    elif re.match(r"^\s*\S", line) and not line.strip().startswith("#"):
                        # A non-indented, non-comment line closes the current block.
                        current_rel = None

        if not tables:
            return None

        # Keep only relationships with complete endpoints.
        rels = [
            r for r in rels
            if all(str(r.get(k) or "").strip() for k in ("fromTable", "fromColumn", "toTable", "toColumn"))
        ]

        # Heuristic recovery for TMDL packages where explicit relationships are
        # sparse/missing: infer FACT -> DIM relationships on shared *_KEY columns.
        table_columns_map: dict[str, set[str]] = {
            str(t.get("name") or "").strip(): {
                str(c.get("name") or "").strip()
                for c in (t.get("columns") or [])
                if str(c.get("name") or "").strip()
            }
            for t in tables
            if str(t.get("name") or "").strip()
        }
        existing_rel_keys = {
            (
                str(r.get("fromTable") or "").strip().casefold(),
                str(r.get("fromColumn") or "").strip().casefold(),
                str(r.get("toTable") or "").strip().casefold(),
                str(r.get("toColumn") or "").strip().casefold(),
            )
            for r in rels
        }

        # Detect fact tables: exact "fact" name, or tables whose name ends with
        # "_fact" / starts with "fact_" (e.g. "spend_fact", "spend_details_fact").
        # Collect all matching fact tables so multi-fact models are handled.
        fact_tables: list[str] = [
            name for name in table_columns_map
            if (
                name.strip().casefold() == "fact"
                or name.strip().casefold().endswith("_fact")
                or name.strip().casefold().startswith("fact_")
            )
        ]
        # Legacy single-fact variable kept for backward compat with Strategy B loop.
        fact_table = fact_tables[0] if fact_tables else None
        inferred_rel_count = 0

        def _try_add_rel(from_table: str, from_col: str, to_table: str, to_col: str) -> bool:
            """Add an inferred relationship if it doesn't already exist. Returns True if added."""
            rel_key = (
                from_table.casefold(),
                from_col.casefold(),
                to_table.casefold(),
                to_col.casefold(),
            )
            if rel_key in existing_rel_keys:
                return False
            rels.append(
                {
                    "name": f"REL_{from_table}_{from_col}__{to_table}_{to_col}",
                    "fromTable": from_table,
                    "fromColumn": from_col,
                    "toTable": to_table,
                    "toColumn": to_col,
                    "isActive": True,
                }
            )
            existing_rel_keys.add(rel_key)
            return True

        def _find_pk_col_in_table(table_name: str, dim_cols: set[str], base_name: str) -> str | None:
            """
            Given a base name (e.g. "Customer"), find the best PK candidate in dim_cols.
            Priority:
              1. <BaseName>_Dim_CK / <BaseName>_CK (Snowflake/enterprise surrogate key pattern)
              2. <BaseName>Code, <BaseName>ID, <BaseName>Key (natural key pattern)
              3. Plain "ID", "Code", or "<TableName>_CK" column
              4. Exact match on base name (last resort — often the descriptive column)
            """
            base_up = base_name.replace(" ", "_").upper()
            # 1. Enterprise CK patterns (most specific)
            for ck_suffix in ("_DIM_CK", "_CK"):
                for dc in dim_cols:
                    if dc.replace(" ", "_").upper() == f"{base_up}{ck_suffix}":
                        return dc
            # 2. <Base>Code / <Base>ID / <Base>Key
            for suffix in ("CODE", "ID", "KEY"):
                for dc in dim_cols:
                    if dc.replace(" ", "_").upper() == f"{base_up}{suffix}":
                        return dc
            # 3. Bare surrogate key columns
            for bare in ("ID", "CODE"):
                for dc in dim_cols:
                    if dc.replace(" ", "_").upper() == bare:
                        return dc
            # Also accept a bare <TableName>_CK column (e.g. "Diversity_Key" in diversity_dim)
            for dc in dim_cols:
                dc_up = dc.replace(" ", "_").upper()
                if dc_up.endswith("_CK") or dc_up.endswith("_KEY"):
                    return dc
            # 4. Exact match (e.g. "Customer" column in Customer table)
            for dc in dim_cols:
                if dc.replace(" ", "_").upper() == base_up:
                    return dc
            return None

        # ── Pass 1: Fact → Dim heuristics ────────────────────────────────────
        # Build a set of (from_table, from_col, to_table) triples already covered
        # so we don't add a second inferred rel when a raw one already exists.
        covered_triples: set[tuple[str, str, str]] = {
            (
                str(r.get("fromTable") or "").strip().casefold(),
                str(r.get("fromColumn") or "").strip().casefold(),
                str(r.get("toTable") or "").strip().casefold(),
            )
            for r in rels
        }

        for fact_table in fact_tables:
            fact_cols = table_columns_map.get(fact_table, set())
            for dim_table, dim_cols in table_columns_map.items():
                if dim_table == fact_table:
                    continue
                for fact_col in fact_cols:
                    norm_fact_col = fact_col.replace(" ", "_").upper()

                    # Strategy A: exact column name match, but only for columns that
                    # look like join keys (end in a key suffix) or whose name exactly
                    # matches the target table name (e.g. YearPeriod → Calendar).
                    # This prevents generic attribute columns like "Source_System_Name"
                    # or "Currency" from creating spurious relationships.
                    _FACT_KEY_SUFFIXES = ("_CK", "_KEY", "_ID", "_CODE", "_DIM_CK")
                    col_looks_like_key = any(norm_fact_col.endswith(s) for s in _FACT_KEY_SUFFIXES)
                    col_matches_table = norm_fact_col == dim_table.replace(" ", "_").upper()
                    if col_looks_like_key or col_matches_table:
                        for dc in dim_cols:
                            if dc.replace(" ", "_").upper() == norm_fact_col:
                                if _try_add_rel(fact_table, fact_col, dim_table, dc):
                                    inferred_rel_count += 1
                                # Mark triple covered regardless — raw rel may already exist
                                covered_triples.add((fact_table.casefold(), fact_col.casefold(), dim_table.casefold()))
                                break

                    # Strategy B: <DimTable>_Key / <DimTable>_ID / <DimTable>_CK → dim PK
                    # e.g. "Customer Key" → Customer table, PK col "Customer"
                    # e.g. "Supplier_Dim_CK" → supplier table, PK col "Supplier_Dim_CK"
                    # Skip if a raw rel already covers this (from, col, to) triple.
                    triple = (fact_table.casefold(), fact_col.casefold(), dim_table.casefold())
                    if triple in covered_triples:
                        continue
                    dim_up = dim_table.replace(" ", "_").upper()
                    stripped = None
                    for suffix in ("_KEY", "_ID", "_CK", "_DIM_CK"):
                        if norm_fact_col == f"{dim_up}{suffix}":
                            stripped = dim_table
                            break
                    if stripped:
                        pk_col = _find_pk_col_in_table(dim_table, dim_cols, stripped)
                        if pk_col:
                            if _try_add_rel(fact_table, fact_col, dim_table, pk_col):
                                inferred_rel_count += 1
                                covered_triples.add(triple)

        # ── Pass 2: Cross-dimension / any-table heuristics ───────────────────
        # For every table pair (A → B) not yet covered, look for FK columns in A
        # whose name encodes a reference to table B.
        # Only consider columns that look like keys (ending in _CK, _KEY, _ID, _Code,
        # _Dim_CK) to avoid false positives from shared attribute columns like
        # "Source_System_Name" or "Currency" that appear in many tables.
        _KEY_SUFFIXES = ("_CK", "_KEY", "_ID", "_CODE", "_DIM_CK")

        for from_table, from_cols in table_columns_map.items():
            for to_table, to_cols in table_columns_map.items():
                if from_table == to_table:
                    continue
                to_up = to_table.replace(" ", "_").upper()
                for fc in from_cols:
                    fc_up = fc.replace(" ", "_").upper()
                    # Skip if already covered by a raw or previously inferred rel
                    triple = (from_table.casefold(), fc.casefold(), to_table.casefold())
                    if triple in covered_triples:
                        continue
                    # Only consider columns that look like surrogate/natural keys
                    if not any(fc_up.endswith(s) for s in _KEY_SUFFIXES):
                        continue
                    # Pattern: "<ToTable>" / "<ToTable>_ID" / "<ToTable>_KEY" / "<ToTable>_CODE"
                    # Also handles enterprise CK patterns: "<ToTable>_CK" / "<ToTable>_DIM_CK"
                    is_fk = (
                        fc_up == to_up
                        or fc_up in (f"{to_up}_ID", f"{to_up}_KEY", f"{to_up}_CODE",
                                     f"{to_up}_CK", f"{to_up}_DIM_CK")
                        or fc_up in (f"{to_up}ID", f"{to_up}KEY", f"{to_up}CODE")
                    )
                    if not is_fk:
                        continue
                    pk_col = _find_pk_col_in_table(to_table, to_cols, to_table)
                    if pk_col:
                        if _try_add_rel(from_table, fc, to_table, pk_col):
                            inferred_rel_count += 1
                            covered_triples.add(triple)

        for table in tables:
            for measure in table.get("measures", []):
                expr = str(measure.get("expression") or "").strip()
                if not expr:
                    measure["expression"] = "BLANK()"

        logger.info(
            "Parsed Fabric TMDL package fallback: tables=%s relationships=%s (inferred=%s)",
            len(tables),
            len(rels),
            inferred_rel_count,
        )
        return {"model": {"name": model_name, "tables": tables, "relationships": rels}}

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
