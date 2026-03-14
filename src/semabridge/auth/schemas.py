"""
Pydantic request / response schemas for authentication endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


# ── Request Schemas ──────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    """Body of ``POST /auth/register``."""

    username: str = Field(..., min_length=3, max_length=50, description="Unique username")
    email: EmailStr = Field(..., description="User email address")
    password: str = Field(..., min_length=8, max_length=128, description="Plaintext password")


class LoginRequest(BaseModel):
    """Body of ``POST /auth/login``."""

    username: str = Field(..., description="Username or email")
    password: str = Field(..., description="Plaintext password")


class CredentialSaveRequest(BaseModel):
    """Body of ``POST /auth/credentials/{service}``."""

    key: str = Field(..., description="Credential key (e.g. tenant_id)")
    value: str = Field(..., description="Credential value")


# ── Response Schemas ─────────────────────────────────────────────────────

class TokenResponse(BaseModel):
    """Response from ``POST /auth/login``."""

    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    """Public-facing user representation."""

    id: int
    username: str
    email: str
    role: str
    is_active: bool
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class CredentialListItem(BaseModel):
    """Single credential key visible to the user (value masked)."""

    service: str
    key: str
    has_value: bool = True

    model_config = {"from_attributes": True}
