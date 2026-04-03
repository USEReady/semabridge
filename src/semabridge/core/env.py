"""Shared environment helpers for SemaBridge."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


@lru_cache()
def load_repo_dotenv() -> None:
    """Load the repository .env file if python-dotenv is available."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)


def get_fabric_access_token_from_env() -> str:
    """Return the temporary Fabric access token from env or .env."""
    load_repo_dotenv()
    return os.environ.get("FABRIC_ACCESS_TOKEN", "").strip()