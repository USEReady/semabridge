"""Presentation-layer error translation.

Domain exceptions (from domain/exceptions.py) are caught here and converted
to HTTP responses. This is the ONLY place in the codebase that maps
business errors to HTTP status codes.
"""
from fastapi import Request
from fastapi.responses import JSONResponse
from semabridge.domain.exceptions import (
    SemaBridgeError, NotFoundError, ValidationError, AuthenticationError,
    PermissionError, ExternalServiceError, ConflictError, InternalError,
    ConfigurationError, RateLimitError,
)

_STATUS_MAP = {
    NotFoundError: 404,
    ValidationError: 400,
    AuthenticationError: 401,
    PermissionError: 403,
    ConflictError: 409,
    ConfigurationError: 422,
    ExternalServiceError: 503,
    InternalError: 500,
    RateLimitError: 429,
}


async def domain_exception_handler(request: Request, exc: SemaBridgeError) -> JSONResponse:
    """Translate a domain exception to an HTTP JSON response.

    Preserves the {"detail": "..."} shape that the frontend expects.
    """
    status_code = _STATUS_MAP.get(type(exc), 500)
    return JSONResponse(
        content={"detail": str(exc), "error_type": type(exc).__name__},
        status_code=status_code,
    )
