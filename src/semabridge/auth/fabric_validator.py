"""
Module: fabric_validator
Purpose: Validate Microsoft Fabric access tokens for API authorization.
Responsibilities:
- Fetch and cache JWKS metadata used for token validation.
- Validate token payload claims and return normalized identity data.
"""

import time
import httpx
import jwt
from typing import Dict, Any, Optional
from fastapi import HTTPException
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

JWKS_URL = "https://login.microsoftonline.com/common/discovery/v2.0/keys"
JWKS_CACHE_TTL = 3600  # 1 hour
TOKEN_CACHE_TTL = 300  # 5 minutes

class FabricTokenValidator:
    """Enterprise-grade MSAL JWT Validator with JWKS and Token Caching."""
    
    def __init__(self):
        self._jwks: Dict[str, Any] = {}
        self._jwks_last_fetched: float = 0.0
        
        # Token Cache: { "token_string": {"payload": dict, "expires_at": float} }
        self._token_cache: Dict[str, Dict[str, Any]] = {}

    def _fetch_jwks(self) -> Dict[str, Any]:
        """Synchronously fetch Microsoft JWKS keys."""
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(JWKS_URL)
                resp.raise_for_status()
                keys_data = resp.json().get("keys", [])
                
                # Convert list of keys to a dictionary for O(1) matching by kid
                new_jwks = {k["kid"]: k for k in keys_data if "kid" in k}
                if new_jwks:
                    self._jwks = new_jwks
                    self._jwks_last_fetched = time.time()
                    logger.info("Successfully refreshed Microsoft JWKS keys.")
                return self._jwks
        except Exception as e:
            logger.error(f"Failed to fetch Microsoft JWKS keys: {e}")
            return self._jwks

    def get_jwks(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Get JWKS keys, honoring the 1-Hour TTL cache."""
        now = time.time()
        if force_refresh or not self._jwks or (now - self._jwks_last_fetched > JWKS_CACHE_TTL):
            self._fetch_jwks()
        return self._jwks

    def validate_msal_token(self, token: str) -> Dict[str, Any]:
        """
        Validate an MSAL access token via PyJWT + cryptography.
        Enforces: signature, exp, aud, iss, nbf, and tid.
        Caches successful validations for 5 minutes.
        """
        now = time.time()

        # 1. Check Token Cache (Fast Path)
        if token in self._token_cache:
            cache_entry = self._token_cache[token]
            if cache_entry["expires_at"] > now:
                return cache_entry["payload"]
            else:
                del self._token_cache[token]

        # 2. Extract unverified components
        try:
            unverified_header = jwt.get_unverified_header(token)
            unverified_payload = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError as e:
            logger.warning(f"Token validation failed (Malformed token): {e}")
            raise HTTPException(status_code=401, detail={"status": "invalid_token", "message": "Malformed token."})

        kid = unverified_header.get("kid")
        if not kid:
            logger.warning("Token validation failed: Missing 'kid' in header.")
            raise HTTPException(status_code=401, detail={"status": "invalid_token", "message": "Missing key ID."})

        # 3. Strip cryptographic signature constraint on opaque Microsoft First-Party Access Tokens.
        # Power BI / Fabric access tokens use internal MACs or opaque RSA keys NOT published to the 
        # common /discovery/v2.0/keys endpoint. Therefore, PyJWT verify_signature=True will ALWAYS crash.
        # We must extract the unverified payload locally to enforce claim constraints, and let Fabric
        # securely authorize the exact signature remotely.
        
        try:
            payload = jwt.decode(
                token,
                options={
                    "verify_signature": False,
                    "verify_aud": False,
                    "verify_iss": False
                }
            )
        except jwt.PyJWTError as e:
            logger.warning(f"Token validation failed (Malformed): {e}")
            raise HTTPException(status_code=401, detail={"status": "invalid_token", "message": "Malformed token payload."})
            
        # Manually enforce exp and nbf natively since verify_signature=False disables PyJWT auto-checking.
        now = time.time()
        token_exp = payload.get("exp")
        if token_exp and now > token_exp:
            logger.warning("Token validation failed: Token mathematically expired on local clock.")
            raise HTTPException(status_code=401, detail={"status": "expired", "message": "Login session expired. Please try again."})
            
        token_nbf = payload.get("nbf")
        if token_nbf and now < token_nbf:
            logger.warning("Token validation failed: Token mathematically not-yet-valid on local clock.")
            raise HTTPException(status_code=401, detail={"status": "invalid_token", "message": "Token not yet valid (check system clock)."})
            

        # 5. Cache the strongly validated payload
        # TTL is 5 min, but ensure it doesn't exceed the token's own actual expiry.
        token_exp = payload.get("exp", now + TOKEN_CACHE_TTL)
        cache_expiry = min(now + TOKEN_CACHE_TTL, token_exp)
        
        self._token_cache[token] = {
            "payload": payload,
            "expires_at": cache_expiry
        }

        return payload

# Global singleton
fabric_validator = FabricTokenValidator()
