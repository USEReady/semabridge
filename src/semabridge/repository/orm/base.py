"""
SQLAlchemy 2.0 DeclarativeBase for the SemaBridge ORM layer.

All mapped models inherit from ``Base``.  The base is intentionally
kept in its own module so that ``models.py`` and any future modules
can import it without circular dependencies.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Root declarative base for all SemaBridge ORM models.

    Uses the modern SQLAlchemy 2.0 ``DeclarativeBase`` pattern with
    strict ``Mapped`` / ``mapped_column`` type hints in subclasses.
    """

    pass
