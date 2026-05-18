"""
Fabric Publisher.

Publishes semantic models to Microsoft Fabric using the REST API.
No XMLA required - uses base64-encoded model.bim payloads.
"""

from __future__ import annotations

import base64
import json
import re
import time
from typing import Any, Optional

import httpx
import msal

from semabridge.core.env import get_fabric_access_token_from_env
from semabridge.core.settings import FabricConfig
from semabridge.connectors.tmsl_generator import TMDLGenerator
from semabridge.sml.models import SMLModel
from semabridge.utils.logger import get_logger
from semabridge.utils.relationship_naming import generate_relationship_name

logger = get_logger(__name__)


class PublishError(Exception):
    """Raised when model publishing fails."""
    pass


class FabricPublisher:
    """
    Publishes semantic models to Microsoft Fabric.
    
    Uses the Fabric REST API:
    - POST /workspaces/{workspaceId}/semanticModels (create)
    - POST /workspaces/{workspaceId}/semanticModels/{modelId}/updateDefinition (update)
    
    Handles long-running operations with polling.
    """
    
    # API endpoints
    FABRIC_API_BASE = "https://api.fabric.microsoft.com/v1"
    FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
    
    def __init__(self, config: FabricConfig):
        """
        Initialize the publisher.
        
        Args:
            config: Fabric configuration with credentials
        """
        self.config = config
        self._access_token: Optional[str] = None
        self._token_expiry: float = 0
        self._resolved_workspace_id: Optional[str] = None
    
    def _get_access_token(self, force_refresh: bool = False) -> str:
        """Get a valid access token, refreshing if needed.

        Supports automatic retry: if the cached token is rejected by Microsoft
        with 401, callers can set force_refresh=True to acquire a brand-new one.
        """
        current_time = time.time()

        if not force_refresh and self._access_token and current_time < self._token_expiry - 300:
            return self._access_token

        logger.debug("Acquiring new access token for Fabric API...")

        # --- Pre-issued token from environment variable (CI / automation) ---
        env_token = get_fabric_access_token_from_env()
        if env_token:
            self._access_token = env_token
            self._token_expiry = current_time + 3600
            logger.debug("FabricPublisher: using FABRIC_ACCESS_TOKEN from environment")
            return self._access_token

        # --- Device-code / delegated flow when no client_secret is configured ---
        if self.config.client_secret is None:
            token = self._get_access_token_from_credential_manager()
            self._access_token = token
            self._token_expiry = current_time + 3000  # ~50 min (tokens last ~60 min)
            return self._access_token

        from semabridge.core.settings import get_settings
        network_settings = get_settings().network

        app = msal.ConfidentialClientApplication(
            client_id=self.config.client_id,
            client_credential=self.config.client_secret.get_secret_value(),
            authority=f"https://login.microsoftonline.com/{self.config.tenant_id}",
            proxies=network_settings.proxies,
        )

        result = app.acquire_token_for_client(scopes=[self.FABRIC_SCOPE])

        if "access_token" not in result:
            error = result.get("error_description", "Unknown authentication error")
            raise PublishError(f"Failed to acquire access token: {error}")

        self._access_token = result["access_token"]
        self._token_expiry = current_time + result.get("expires_in", 3600)

        logger.debug("Access token acquired successfully (service principal)")
        return self._access_token

    def _get_access_token_from_credential_manager(self) -> str:
        """Acquire a Fabric-scoped access token via the stored refresh_token.

        IMPORTANT: This always performs a silent refresh using the Fabric API
        scope (``https://api.fabric.microsoft.com/.default``).  It does NOT
        trust ``has_valid_token()`` because the stored access token may have
        been refreshed by the API layer with the Power BI scope, whose
        audience the Fabric REST API rejects as "TokenExpired".

        If the refresh_token is expired or revoked, this method raises
        immediately with an actionable error — it does NOT fall back to a
        stored expired access token (which would always cause a 401).

        Raises:
            PublishError: If no valid token can be acquired.
        """
        try:
            from semabridge.repository.credential_manager import CredentialManager

            cm = CredentialManager()
            token_data = cm.get_msal_token()

            if not token_data:
                raise PublishError(
                    "No stored MSAL token found. "
                    "Please sign in via Settings → Connections → Fabric → Sign In."
                )

            refresh_token = token_data.get("refresh_token")
            stored_access = token_data.get("access_token", "")
            logger.info(
                "FabricPublisher: token_data has refresh_token=%s, "
                "access_token_tail=...%s",
                bool(refresh_token),
                stored_access[-8:] if stored_access else "(empty)",
            )

            if not refresh_token:
                # No refresh token at all — check if stored access token is still valid
                import time as _t
                try:
                    expires_at = int(token_data.get("expires_at", "0"))
                    remaining = expires_at - _t.time()
                    if remaining > 60:
                        logger.warning(
                            "No refresh_token available; using stored access token "
                            "(expires in %.0f seconds)", remaining,
                        )
                        return stored_access
                except (ValueError, TypeError):
                    pass

                raise PublishError(
                    "No refresh_token stored and access token is expired. "
                    "Please re-authenticate: Settings → Connections → Fabric → Sign In."
                )

            # Always acquire a FRESH token with the Fabric API scope.
            tenant_id = token_data.get("tenant_id", "organizations")
            logger.info(
                "FabricPublisher: refreshing token with scope=%s tenant=%s",
                self.FABRIC_SCOPE, tenant_id,
            )

            from semabridge.core.settings import get_settings
            network_settings = get_settings().network

            app = msal.PublicClientApplication(
                client_id="04b07795-8ddb-461a-bbee-02f9e1bf7b46",
                authority=f"https://login.microsoftonline.com/{tenant_id}",
                proxies=network_settings.proxies,
            )
            result = app.acquire_token_by_refresh_token(
                refresh_token,
                scopes=[self.FABRIC_SCOPE],
            )

            if "access_token" in result:
                new_token = result["access_token"]
                new_refresh = result.get("refresh_token", refresh_token)
                expires_in = result.get("expires_in", 3600)

                logger.info(
                    "FabricPublisher: ✅ acquired fresh Fabric-scoped token "
                    "(expires_in=%ds, tail=...%s)",
                    expires_in, new_token[-8:],
                )

                # Persist refreshed tokens back to credential store.
                cm.save_msal_token(
                    access_token=new_token,
                    refresh_token=new_refresh,
                    account_username=token_data.get("account_username", "unknown"),
                    tenant_id=tenant_id,
                    expires_in=expires_in,
                )
                return new_token
            else:
                # Refresh FAILED — extract the actual error from MSAL
                error_code = result.get("error", "unknown_error")
                error_desc = result.get("error_description", "No description provided")
                logger.error(
                    "FabricPublisher: ❌ token refresh FAILED: "
                    "error=%s description=%s",
                    error_code, error_desc,
                )
                raise PublishError(
                    f"Fabric token refresh failed: {error_code} — {error_desc}. "
                    "Please re-authenticate: Settings → Connections → Fabric → Sign In."
                )

        except PublishError:
            raise
        except Exception as exc:
            logger.error("FabricPublisher: token acquisition exception: %s", exc)
            raise PublishError(
                f"Fabric token acquisition failed: {exc}. "
                "Please re-authenticate: Settings → Connections → Fabric → Sign In."
            )

    def _get_headers(self) -> dict[str, str]:
        """Get request headers with auth token."""
        return {
            "Authorization": f"Bearer {self._get_access_token()}",
            "Content-Type": "application/json",
        }

    def list_workspaces(self) -> list[dict[str, Any]]:
        """List Fabric workspaces visible to the authenticated principal."""
        url = f"{self.FABRIC_API_BASE}/workspaces"
        from semabridge.core.settings import get_settings
        proxy = get_settings().network.https_proxy
        
        try:
            with httpx.Client(timeout=30, proxy=proxy) as client:
                response = client.get(url, headers=self._get_headers())
                if response.status_code != 200:
                    logger.warning("Workspace resolution: list failed with %s", response.status_code)
                    return []
                return response.json().get("value", [])
        except Exception as exc:
            logger.warning("Workspace resolution: failed to list workspaces: %s", exc)
            return []

    def resolve_workspace_id(self, workspace_id_or_name: str) -> str:
        """Resolve workspace display name to GUID. Returns original value if unresolved."""
        raw = str(workspace_id_or_name or "").strip()
        if not raw:
            return raw

        guid_pattern = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
        if re.match(guid_pattern, raw):
            return raw

        for ws in self.list_workspaces():
            if ws.get("displayName", "").strip().lower() == raw.lower():
                resolved = ws.get("id", "")
                if resolved:
                    logger.info("Resolved Fabric workspace '%s' -> '%s'", raw, resolved)
                    return resolved

        logger.warning("Could not resolve Fabric workspace '%s' to GUID; using as-is", raw)
        return raw

    def _workspace_id(self) -> str:
        """Get resolved workspace ID with secure fallback chain.

        Resolution order:
        1. FabricConfig.workspace_id — static .env or UI-provided target.
        2. Credential store (DuckDB) — fallback source workspace.

        The result is cached for the lifetime of this publisher instance.
        """
        if not self._resolved_workspace_id:
            # self.config.workspace_id is set by the execution engine to the TARGET
            # workspace. It must always take priority. The credential store holds the
            # SOURCE workspace and must never override an explicitly configured target.
            config_ws = (self.config.workspace_id or "").strip()

            if config_ws:
                self._resolved_workspace_id = self.resolve_workspace_id(config_ws)
                logger.info(
                    "FabricPublisher resolved target workspace from config: %s",
                    self._resolved_workspace_id,
                )
            else:
                # No workspace in config — fall back to credential store.
                stored_ws: str = ""
                try:
                    from semabridge.repository.credential_manager import CredentialManager
                    cm = CredentialManager()
                    creds = cm.get_credentials("fabric", mask_secrets=False)
                    stored_ws = (creds.get("workspace_id") or "").strip()
                except Exception as exc:
                    logger.debug("Could not read workspace from credential store: %s", exc)

                if stored_ws:
                    logger.info(
                        "No workspace in FabricConfig — using credential store workspace: %s",
                        stored_ws,
                    )
                self._resolved_workspace_id = self.resolve_workspace_id(stored_ws)
        return self._resolved_workspace_id
    
    def publish(
        self,
        sml_model: SMLModel,
        model_name: Optional[str] = None,
        description: Optional[str] = None,
        snowflake_server: str = "",
        snowflake_warehouse: str = "",
        snowflake_database: str = "",
        snowflake_schema: str = "",
        overwrite: bool = True,
    ) -> dict[str, Any]:
        """
        Publish an SML model to Fabric.
        
        Args:
            sml_model: SML model to publish
            model_name: Override model name (uses SML name if not provided)
            description: Override description
            snowflake_server: Snowflake server for M expressions
            snowflake_warehouse: Snowflake warehouse
            snowflake_database: Snowflake database
            snowflake_schema: Snowflake schema
            overwrite: Whether to overwrite existing model
            
        Returns:
            API response with model details
        """
        display_name = model_name or sml_model.label or sml_model.unique_name
        model_description = description or sml_model.description
        
        logger.info(f"Publishing semantic model: {display_name}")
        
        # Check if model already exists
        existing_model = self.find_model_by_name(display_name)
        
        if existing_model and not overwrite:
            raise PublishError(f"Model '{display_name}' already exists and overwrite=False")
        
        # Generate TMDL model payload
        generator = TMDLGenerator(
            sml_model,
            snowflake_server=snowflake_server,
            snowflake_warehouse=snowflake_warehouse,
            snowflake_database=snowflake_database,
            snowflake_schema=snowflake_schema,
        )
        
        model_bim = generator.generate()
        definition_pbism = generator.generate_definition_pbism()
        platform_file = generator.generate_platform_file()
        
        # Encode as base64
        model_bim_b64 = base64.b64encode(
            json.dumps(model_bim, indent=2).encode("utf-8")
        ).decode("ascii")
        
        definition_pbism_b64 = base64.b64encode(
            json.dumps(definition_pbism, indent=2).encode("utf-8")
        ).decode("ascii")
        
        platform_b64 = base64.b64encode(
            json.dumps(platform_file, indent=2).encode("utf-8")
        ).decode("ascii")
        
        # Build API payload
        payload = {
            "displayName": display_name,
            "description": model_description,
            "definition": {
                "parts": [
                    {
                        "path": "model.bim",
                        "payload": model_bim_b64,
                        "payloadType": "InlineBase64",
                    },
                    {
                        "path": "definition.pbism",
                        "payload": definition_pbism_b64,
                        "payloadType": "InlineBase64",
                    },
                    {
                        "path": ".platform",
                        "payload": platform_b64,
                        "payloadType": "InlineBase64",
                    },
                ]
            }
        }

        validation_stats = self._validate_full_definition_payload(payload)
        logger.info(
            "Fabric publish preflight: model=%s relationships=%s",
            display_name,
            validation_stats,
        )
        
        if existing_model:
            # Update existing model
            logger.info("Deploy mode: full overwrite via updateDefinition (in-place)")
            result = self._update_model(existing_model["id"], payload)
        else:
            # Create new model
            logger.info("Deploy mode: create new semantic model with full definition")
            result = self._create_model(payload)
            
        # Trigger refresh to ensure changes are visible
        if "id" in result:
             self.refresh_model(result["id"])
             
        return result
    
    def _create_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create a new semantic model with 401 auto-retry."""
        workspace_id = self._workspace_id()
        
        # Use the unified Fabric Items API rather than the legacy semanticModels path
        url = f"{self.FABRIC_API_BASE}/workspaces/{workspace_id}/items"

        # The items API strictly requires the 'type' attribute
        item_payload = {
            "displayName": payload["displayName"],
            "type": "SemanticModel", 
            "description": payload.get("description", ""),
            "definition": payload["definition"]
        }

        logger.info(f"Creating semantic model '{payload['displayName']}' in workspace {workspace_id} via Items API")

        from semabridge.core.settings import get_settings
        proxy = get_settings().network.https_proxy

        for attempt in range(2):  # 1 normal + 1 retry on 401
            with httpx.Client(timeout=60, proxy=proxy) as client:
                response = client.post(url, headers=self._get_headers(), json=item_payload)

                if response.status_code == 202:
                    return self._poll_operation(response)
                elif response.status_code == 201:
                    result = response.json()
                    logger.info(f"Created semantic model: {result.get('displayName')} (ID: {result.get('id')})")
                    return result
                elif response.status_code == 401 and attempt == 0:
                    logger.warning("Create got 401 — refreshing token and retrying...")
                    self._access_token = None  # force re-acquire
                    self._token_expiry = 0
                    self._get_access_token(force_refresh=True)
                    continue
                elif response.status_code == 404:
                    error_text = response.text
                    logger.error("Create failed with 404 EntityNotFound: workspace=%s. This usually means the workspace is a Pro workspace and lacks a Fabric Capacity (F-SKU or PPU).", workspace_id)
                    raise PublishError(
                        f"Deployment Failed: The workspace '{workspace_id}' does not exist, or you do not have permission, "
                        f"or it is NOT backed by a Fabric Capacity. Fabric's semantic model creation API strictly requires "
                        f"a workspace with a Fabric Capacity (F-SKU) or Premium Per User (PPU). Error: {error_text}"
                    )
                else:
                    error_text = response.text
                    logger.error(
                        "Create failed: workspace=%s status=%s body=%s",
                        workspace_id, response.status_code, error_text,
                    )
                    raise PublishError(f"Failed to create model: {response.status_code} - {error_text}")

        raise PublishError("Failed to create model after 401 retry")
    
    def _update_model(
        self,
        model_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Update an existing semantic model with 401 auto-retry."""
        workspace_id = self._workspace_id()
        # Use the unified items API path
        url = f"{self.FABRIC_API_BASE}/workspaces/{workspace_id}/items/{model_id}/updateDefinition"

        logger.info(f"Updating semantic model {model_id} in workspace {workspace_id} via Items API")

        update_payload = {"definition": payload["definition"]}

        from semabridge.core.settings import get_settings
        proxy = get_settings().network.https_proxy

        for attempt in range(2):
            with httpx.Client(timeout=60, proxy=proxy) as client:
                response = client.post(url, headers=self._get_headers(), json=update_payload)

                if response.status_code == 202:
                    return self._poll_operation(response)
                elif response.status_code == 200:
                    logger.info(f"Updated semantic model: {payload['displayName']} (ID: {model_id})")
                    return {"id": model_id, "displayName": payload["displayName"], "status": "updated"}
                elif response.status_code == 401 and attempt == 0:
                    logger.warning("Update got 401 — refreshing token and retrying...")
                    self._access_token = None
                    self._token_expiry = 0
                    self._get_access_token(force_refresh=True)
                    continue
                else:
                    error_text = response.text
                    logger.error(f"Update failed: {response.status_code} - {error_text}")
                    raise PublishError(f"Failed to update model: {response.status_code} - {error_text}")

        raise PublishError("Failed to update model after 401 retry")

    def _validate_full_definition_payload(self, payload: dict[str, Any]) -> dict[str, int]:
        """Validate model.bim relationships before sending an overwrite payload.

        This enforces idempotent, clean deployments by blocking payloads that
        contain system-generated relationship names or duplicate endpoints.
        """
        model_bim = self._extract_model_bim_payload(payload)
        relationships = model_bim.get("model", {}).get("relationships", [])
        if not isinstance(relationships, list):
            raise PublishError("Invalid payload: model.relationships must be a list")

        seen_endpoints: set[tuple[str, str, str, str]] = set()
        duplicate_count = 0

        for rel in relationships:
            if not isinstance(rel, dict):
                raise PublishError("Invalid payload: relationship entries must be objects")

            name = str(rel.get("name") or "")
            if name.upper().startswith("SYS_RELATIONSHIP"):
                raise PublishError(
                    f"Invalid payload: system relationship name detected ({name})"
                )

            endpoint = (
                str(rel.get("fromTable") or "").upper(),
                str(rel.get("fromColumn") or "").upper(),
                str(rel.get("toTable") or "").upper(),
                str(rel.get("toColumn") or "").upper(),
            )
            if not all(endpoint):
                raise PublishError(
                    f"Invalid payload: incomplete relationship endpoint for '{name or 'unnamed'}'"
                )

            expected_name = generate_relationship_name(
                str(rel.get("fromTable") or ""),
                str(rel.get("fromColumn") or ""),
                str(rel.get("toTable") or ""),
                str(rel.get("toColumn") or ""),
            )
            if name != expected_name:
                raise PublishError(
                    "Invalid payload: non-deterministic relationship name "
                    f"'{name}' (expected '{expected_name}')"
                )

            if endpoint in seen_endpoints:
                duplicate_count += 1
            seen_endpoints.add(endpoint)

        if duplicate_count > 0:
            raise PublishError(
                f"Invalid payload: duplicate relationship endpoints detected ({duplicate_count})"
            )

        return {
            "total": len(relationships),
            "unique_endpoints": len(seen_endpoints),
            "duplicates": duplicate_count,
        }

    @staticmethod
    def _extract_model_bim_payload(payload: dict[str, Any]) -> dict[str, Any]:
        """Decode and return model.bim JSON from a Fabric definition payload."""
        parts = payload.get("definition", {}).get("parts", [])
        if not isinstance(parts, list):
            raise PublishError("Invalid payload: definition.parts must be a list")

        model_bim_part = next((p for p in parts if p.get("path") == "model.bim"), None)
        if not model_bim_part:
            raise PublishError("Invalid payload: model.bim part not found")

        encoded = model_bim_part.get("payload")
        if not isinstance(encoded, str) or not encoded:
            raise PublishError("Invalid payload: model.bim payload missing")

        try:
            return json.loads(base64.b64decode(encoded).decode("utf-8"))
        except Exception as exc:
            raise PublishError(f"Invalid payload: failed to decode model.bim ({exc})") from exc

    def refresh_model(self, model_id: str) -> bool:
        """Trigger a refresh of the semantic model."""
        workspace_id = self._workspace_id()
        url = f"{self.FABRIC_API_BASE}/workspaces/{workspace_id}/semanticModels/{model_id}/refresh"
        
        logger.info(f"Triggering refresh for model: {model_id}")
        
        from semabridge.core.settings import get_settings
        proxy = get_settings().network.https_proxy

        with httpx.Client(timeout=30, proxy=proxy) as client:
            response = client.post(url, headers=self._get_headers())
            
            if response.status_code == 202:
                logger.info("Refresh initiated successfully")
                return True
            else:
                logger.warning(f"Failed to trigger refresh: {response.status_code} - {response.text}")
                return False
    
    def _poll_operation(
        self,
        initial_response: httpx.Response,
        max_wait: int = 300,
        poll_interval: int = 5,
    ) -> dict[str, Any]:
        """Poll a long-running operation until completion."""
        operation_url = initial_response.headers.get("Location")
        operation_id = initial_response.headers.get("x-ms-operation-id")
        retry_after = int(initial_response.headers.get("Retry-After", poll_interval))
        
        if not operation_url:
            # No operation URL, check if we got a result
            if initial_response.status_code in (200, 201):
                return initial_response.json()
            raise PublishError("No operation URL in response")
        
        logger.debug(f"Polling operation: {operation_id}")
        
        from semabridge.core.settings import get_settings
        proxy = get_settings().network.https_proxy

        start_time = time.time()
        
        with httpx.Client(timeout=30, proxy=proxy) as client:
            while time.time() - start_time < max_wait:
                time.sleep(retry_after)
                
                response = client.get(operation_url, headers=self._get_headers())
                
                if response.status_code != 200:
                    raise PublishError(f"Operation status check failed: {response.status_code}")
                
                result = response.json()
                status = result.get("status", "").lower()
                
                if status == "succeeded":
                    logger.info("Operation completed successfully")
                    return result.get("result", result)
                elif status == "failed":
                    error = result.get("error", {})
                    raise PublishError(f"Operation failed: {error.get('message', 'Unknown error')}")
                elif status in ("running", "inprogress", "notstarted"):
                    logger.debug(f"Operation status: {status}")
                    continue
                else:
                    logger.warning(f"Unknown operation status: {status}")
        
        raise PublishError(f"Operation timed out after {max_wait} seconds")
    
    def find_model_by_name(self, name: str) -> Optional[dict[str, Any]]:
        """Find a semantic model by name in the workspace."""
        workspace_id = self._workspace_id()
        # Use Items API instead of legacy semanticModels path
        url = f"{self.FABRIC_API_BASE}/workspaces/{workspace_id}/items?type=SemanticModel"
        
        from semabridge.core.settings import get_settings
        proxy = get_settings().network.https_proxy

        try:
            with httpx.Client(timeout=30, proxy=proxy) as client:
                response = client.get(url, headers=self._get_headers())
                
                if response.status_code != 200:
                    logger.warning(f"Could not list models: {response.status_code}")
                    return None
                
                models = response.json().get("value", [])
                
                for model in models:
                    if model.get("displayName", "").lower() == name.lower():
                        return model
                
                return None
        except Exception as e:
            logger.warning(f"Error finding model: {e}")
            return None
    
    def list_models(self) -> list[dict[str, Any]]:
        """List all semantic models in the workspace."""
        workspace_id = self._workspace_id()
        # Use Items API instead of legacy semanticModels path
        url = f"{self.FABRIC_API_BASE}/workspaces/{workspace_id}/items?type=SemanticModel"
        
        from semabridge.core.settings import get_settings
        proxy = get_settings().network.https_proxy

        with httpx.Client(timeout=30, proxy=proxy) as client:
            response = client.get(url, headers=self._get_headers())
            
            if response.status_code != 200:
                raise PublishError(f"Failed to list models: {response.status_code}")
            
            return response.json().get("value", [])
    
    def delete_model(self, model_id: str) -> bool:
        """Delete a semantic model."""
        workspace_id = self._workspace_id()
        # Use Items API rather than legacy path
        url = f"{self.FABRIC_API_BASE}/workspaces/{workspace_id}/items/{model_id}"
        
        from semabridge.core.settings import get_settings
        proxy = get_settings().network.https_proxy

        with httpx.Client(timeout=30, proxy=proxy) as client:
            response = client.delete(url, headers=self._get_headers())
            
            if response.status_code in (200, 204):
                logger.info(f"Deleted semantic model: {model_id}")
                return True
            else:
                logger.error(f"Delete failed: {response.status_code}")
                return False
    
    def test_connection(self) -> bool:
        """Test Fabric API connectivity."""
        try:
            self._get_access_token()
            models = self.list_models()
            logger.info(f"Connected to Fabric workspace. Found {len(models)} semantic models.")
            return True
        except Exception as e:
            logger.error(f"Fabric connection test failed: {e}")
            return False
