"""
Password hashing utilities using bcrypt.

All password operations go through this module to ensure a single,
auditable hashing strategy across the application.
"""

from __future__ import annotations

import bcrypt as _bcrypt


def hash_password(plain: str) -> str:
    """Return a bcrypt hash of *plain*.

    Args:
        plain: The plaintext password to hash.

    Returns:
        A bcrypt-hashed string suitable for database storage.
    """
    return _bcrypt.hashpw(
        plain.encode("utf-8"), _bcrypt.gensalt(rounds=12)
    ).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify *plain* against a bcrypt *hashed* value.

    Args:
        plain: The plaintext password to verify.
        hashed: The stored bcrypt hash.

    Returns:
        ``True`` if the password matches, ``False`` otherwise.
    """
    return _bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
