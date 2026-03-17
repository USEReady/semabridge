"""
Centralized Gemini API wrapper with rate limiting, retry logic, and fallback support.

This module provides:
- Safe API calls with exponential backoff
- Rate limiting (max 1 request every 12 seconds for 5 RPM quota)
- Automatic retry on 429 errors (15s, 30s, 60s)
- Hard fallback if Gemini fails
- Detailed logging
- Optional toggle via USE_GEMINI environment variable
"""

import os
import time
import logging
import warnings
from pathlib import Path
from typing import Optional, Callable, Any
from datetime import datetime, timedelta

# Suppress third-party Gemini SDK migration/deprecation noise in runtime logs.
warnings.filterwarnings(
    "ignore",
    category=FutureWarning,
    module=r"google(\.|$)",
)
warnings.filterwarnings(
    "ignore",
    category=FutureWarning,
    message=r"All support for the `google\.generativeai` package has ended.*",
)
warnings.simplefilter("ignore", FutureWarning)

# Load .env
try:
    from dotenv import load_dotenv
    possible_paths = [
        Path(__file__).resolve().parent.parent.parent / '.env',
        Path.cwd() / '.env',
        Path(__file__).resolve().parent.parent.parent.parent / '.env',
    ]
    for env_path in possible_paths:
        if env_path.exists():
            load_dotenv(env_path, override=False)
            break
except ImportError:
    pass

# Import Gemini SDK
try:
    import google.genai as genai
except ImportError:
    try:
        import google.generativeai as genai
    except ImportError:
        genai = None

logger = logging.getLogger(__name__)


class GeminiAPIError(Exception):
    """Raised when Gemini API call fails after retries."""
    pass


class GeminiRateLimitError(GeminiAPIError):
    """Raised when rate limit (429) persists after retries."""
    pass


class GeminiAPIService:
    """
    Centralized wrapper for Gemini API with rate limiting and retry logic.
    
    Features:
    - Single model: gemini-2.5-flash (5 RPM, 20 RPD)
    - Rate limiting: max 1 request per 12 seconds
    - Exponential backoff: 15s, 30s, 60s on 429 errors
    - Fallback: graceful degradation if API unavailable
    - Logging: detailed request/response logging
    """
    
    # Free tier quota: 5 requests per minute
    # Safe interval: 60 seconds / 5 requests = 12 seconds min per request
    MIN_REQUEST_INTERVAL_SECONDS = 12.0
    
    # Retry settings for 429 errors
    RETRY_DELAYS = [15, 30, 60]  # seconds
    MAX_RETRIES = 3
    
    # Only working free-tier model
    MODEL = "models/gemini-2.5-flash"
    
    def __init__(self):
        """Initialize Gemini API service."""
        self.use_gemini = os.getenv("USE_GEMINI", "true").lower() in ("true", "1", "yes")
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.last_request_time = None
        self.is_available = False
        
        # Validate configuration
        if not self.use_gemini:
            logger.info("✓ Gemini API disabled (USE_GEMINI=false)")
            return
        
        if not self.api_key:
            logger.warning(
                "❌ GEMINI_API_KEY not set. Gemini API disabled. "
                "Set GEMINI_API_KEY=... in .env to enable LLM translation."
            )
            self.use_gemini = False
            return
        
        # Configure Gemini client
        try:
            genai.configure(api_key=self.api_key)
            self.is_available = True
            logger.info(f"✅ Gemini API initialized with {self.MODEL}")
            logger.info(
                f"   ├─ Quota: 5 RPM (1 request per {self.MIN_REQUEST_INTERVAL_SECONDS}s)"
            )
            logger.info(f"   ├─ Retry policy: {self.MAX_RETRIES} attempts with exponential backoff")
            logger.info(f"   └─ Model: {self.MODEL}")
        except Exception as e:
            logger.error(f"Failed to initialize Gemini API: {e}")
            self.use_gemini = False
    
    def call(self, prompt: str, fallback_fn: Optional[Callable] = None) -> str:
        """
        Call Gemini API with rate limiting and retry logic.
        
        Args:
            prompt: The prompt to send to Gemini
            fallback_fn: Optional callable that returns a fallback response if Gemini fails
                        Signature: fallback_fn(prompt: str) -> str
                        
        Returns:
            Response from Gemini API, or fallback response if API unavailable
            
        Raises:
            GeminiAPIError: If API fails and no fallback provided
            GeminiRateLimitError: If rate limit persists after retries
        """
        # If Gemini is disabled, use fallback immediately
        if not self.use_gemini:
            logger.debug("Gemini disabled, using fallback")
            if fallback_fn:
                return fallback_fn(prompt)
            raise GeminiAPIError("Gemini API disabled and no fallback provided")
        
        # If API is not available, use fallback
        if not self.is_available:
            logger.debug("Gemini API not available, using fallback")
            if fallback_fn:
                return fallback_fn(prompt)
            raise GeminiAPIError("Gemini API not available and no fallback provided")
        
        # Rate limiting: wait if necessary
        self._respect_rate_limit()
        
        # Try API call with retries
        last_error = None
        for attempt in range(self.MAX_RETRIES):
            try:
                logger.debug(
                    f"Calling Gemini (attempt {attempt + 1}/{self.MAX_RETRIES}) "
                    f"at {datetime.now().isoformat()}"
                )
                
                # Record request time BEFORE making call
                request_start = time.time()
                
                # Make API call
                model = genai.GenerativeModel(self.MODEL)
                response = model.generate_content(prompt, stream=False)
                
                # Record completion time
                request_end = time.time()
                elapsed = request_end - request_start
                
                if not response or not response.text:
                    raise ValueError("Empty response from Gemini")
                
                logger.info(
                    f"✅ Gemini call succeeded (attempt {attempt + 1}, "
                    f"elapsed: {elapsed:.1f}s)"
                )
                
                # Update last request time for rate limiting
                self.last_request_time = datetime.now()
                
                return response.text.strip()
                
            except Exception as e:
                error_msg = str(e)
                last_error = error_msg
                
                # Check for rate limit error
                is_rate_limit = (
                    "429" in error_msg or 
                    "quota" in error_msg.lower() or
                    "rate limit" in error_msg.lower()
                )
                
                if is_rate_limit:
                    if attempt < self.MAX_RETRIES - 1:
                        delay = self.RETRY_DELAYS[attempt]
                        logger.warning(
                            f"⏳ Rate limit hit (attempt {attempt + 1}/{self.MAX_RETRIES}). "
                            f"Waiting {delay}s before retry..."
                        )
                        time.sleep(delay)
                        continue
                    else:
                        logger.error(
                            f"❌ Rate limit persisted after {self.MAX_RETRIES} attempts. "
                            f"Giving up."
                        )
                        error = GeminiRateLimitError(
                            f"Rate limit error after {self.MAX_RETRIES} retries: {error_msg}"
                        )
                        
                        # Use fallback if available
                        if fallback_fn:
                            logger.info("Using fallback response due to rate limit")
                            return fallback_fn(prompt)
                        raise error
                else:
                    # Non-rate-limit error
                    logger.error(f"Gemini API error: {error_msg[:100]}")
                    
                    # Use fallback immediately on non-quota errors
                    if fallback_fn:
                        logger.info("Using fallback response due to API error")
                        return fallback_fn(prompt)
                    
                    raise GeminiAPIError(f"Gemini API error: {error_msg}")
        
        # Should not reach here, but just in case
        error_msg = f"Failed after {self.MAX_RETRIES} attempts: {last_error}"
        logger.error(error_msg)
        
        if fallback_fn:
            logger.info("Using fallback response due to max retries exceeded")
            return fallback_fn(prompt)
        
        raise GeminiAPIError(error_msg)
    
    def _respect_rate_limit(self) -> None:
        """Wait if necessary to respect rate limit (max 1 request per 12 seconds)."""
        if self.last_request_time is None:
            # First request, no need to wait
            return
        
        elapsed = (datetime.now() - self.last_request_time).total_seconds()
        if elapsed < self.MIN_REQUEST_INTERVAL_SECONDS:
            wait_time = self.MIN_REQUEST_INTERVAL_SECONDS - elapsed
            logger.debug(
                f"Rate limit protection: waiting {wait_time:.1f}s "
                f"(min interval: {self.MIN_REQUEST_INTERVAL_SECONDS}s)"
            )
            time.sleep(wait_time)
    
    def health_check(self) -> dict:
        """
        Check Gemini API health and connectivity.
        
        Returns:
            Dict with health status information
        """
        health = {
            "enabled": self.use_gemini,
            "available": self.is_available,
            "model": self.MODEL,
            "quota_rpm": 5,
            "min_interval_seconds": self.MIN_REQUEST_INTERVAL_SECONDS,
            "last_request": self.last_request_time.isoformat() if self.last_request_time else None,
            "api_key_configured": bool(self.api_key),
        }
        
        if not self.use_gemini:
            health["status"] = "disabled"
        elif not self.is_available:
            health["status"] = "unavailable"
        else:
            health["status"] = "healthy"
        
        return health


# Global instance
_service = None


def get_gemini_service() -> GeminiAPIService:
    """Get or create the global Gemini API service instance."""
    global _service
    if _service is None:
        _service = GeminiAPIService()
    return _service


def call_gemini(prompt: str, fallback_fn: Optional[Callable] = None) -> str:
    """
    Convenience function to call Gemini API.
    
    Args:
        prompt: The prompt to send to Gemini
        fallback_fn: Optional fallback function if API fails
        
    Returns:
        Response from Gemini API or fallback
    """
    service = get_gemini_service()
    return service.call(prompt, fallback_fn)


def gemini_health_check() -> dict:
    """Get Gemini API health status."""
    service = get_gemini_service()
    return service.health_check()
