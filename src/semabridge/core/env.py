"""Shared environment helpers for SemaBridge."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


def dotenv_disabled() -> bool:
    """True when SEMABRIDGE_DISABLE_DOTENV is set: no .env file is loaded
    into the process. The test suite sets it so a developer's .env (real
    Fabric/Snowflake credentials, demo/mock flags) never leaks into tests."""
    return os.environ.get("SEMABRIDGE_DISABLE_DOTENV", "").strip().lower() in ("1", "true", "yes", "on")


@lru_cache()
def load_repo_dotenv() -> None:
    """Load the repository .env file if python-dotenv is available."""
    if dotenv_disabled():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)


def get_fabric_access_token_from_env() -> str:
    """Return the temporary Fabric access token from env or .env."""
    load_repo_dotenv()
    return os.environ.get("FABRIC_ACCESS_TOKEN", "").strip()