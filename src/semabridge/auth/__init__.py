"""
SemaBridge Authentication & Authorization Package.

Provides:
  - Password hashing (bcrypt via passlib)
  - JWT token creation / validation
  - FastAPI dependency for extracting the current user from requests
  - Pydantic request/response schemas for auth endpoints
"""

from semabridge.auth.passwords import hash_password, verify_password
from semabridge.auth.tokens import create_access_token, decode_access_token

__all__ = [
    "hash_password",
    "verify_password",
    "create_access_token",
    "decode_access_token",
]
