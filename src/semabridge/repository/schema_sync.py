"""
Schema Version Manager — YAML-to-DB synchronization.

Compares the SHA-256 hash of the current ``semabridge.yaml`` against
the most recent ``ModelVersionHistory`` row for the same model and
records a new version only when the content has changed.

Usage::

    from semabridge.repository.schema_sync import SchemaVersionManager

    mgr = SchemaVersionManager()
    mgr.sync_model_version("semabridge.yaml")
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

import yaml

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class SchemaVersionManager:
    """Synchronise YAML model definitions with the ORM database.

    On each call to :meth:`sync_model_version`:

    1. Read the raw file and compute its SHA-256 hash.
    2. Parse the YAML to extract ``model_name`` and ``version_tag``.
    3. Query ``model_version_history`` for the latest row of this model.
    4. If the hashes match → log "up to date" and return ``False``.
    5. If they differ (or no row exists) → insert a new record and
       return ``True``.

    The manager obtains a session from the singleton factory in
    :mod:`semabridge.repository.orm.session_factory` so callers don't
    need to worry about engine setup.
    """

    def sync_model_version(
        self,
        yaml_filepath: str,
        *,
        session: Optional["Session"] = None,
    ) -> bool:
        """Compare and (optionally) record a new config version.

        Args:
            yaml_filepath: Path to the ``semabridge.yaml`` file.
            session: An existing SQLAlchemy session.  If ``None`` a
                     fresh session is created from the singleton factory.

        Returns:
            ``True`` if a new version was recorded, ``False`` if the
            content was already up to date.

        Raises:
            FileNotFoundError: If *yaml_filepath* does not exist.
            KeyError: If the YAML lacks a ``model_name`` field.
        """
        from sqlalchemy import desc

        from semabridge.repository.orm.models import ModelVersionHistory

        path = Path(yaml_filepath)
        if not path.exists():
            raise FileNotFoundError(f"YAML file not found: {yaml_filepath}")

        raw_content = path.read_text(encoding="utf-8")
        yaml_hash = hashlib.sha256(raw_content.encode("utf-8")).hexdigest()

        parsed = yaml.safe_load(raw_content) or {}
        model_name: str = parsed.get("model_name", "")
        if not model_name:
            raise KeyError(
                f"'model_name' is missing or empty in {yaml_filepath}"
            )
        version_tag: str = str(parsed.get("version_tag", "unknown"))

        # Obtain a session ---------------------------------------------------
        own_session = session is None
        if own_session:
            from semabridge.repository.orm.session_factory import get_session_factory
            from semabridge.repository.orm.base import Base

            engine = None
            try:
                from semabridge.repository.orm.session_factory import get_engine
                engine = get_engine()
                # Ensure tables exist (idempotent)
                Base.metadata.create_all(bind=engine)
            except Exception:  # noqa: BLE001
                logger.debug("Could not auto-create tables", exc_info=True)

            SessionLocal = get_session_factory()
            session = SessionLocal()

        try:
            latest = (
                session.query(ModelVersionHistory)
                .filter(ModelVersionHistory.model_name == model_name)
                .order_by(desc(ModelVersionHistory.applied_at))
                .first()
            )

            if latest is not None and latest.yaml_hash == yaml_hash:
                logger.info(
                    "Version up to date for '%s' (hash=%s…)",
                    model_name,
                    yaml_hash[:12],
                )
                return False

            # Insert new version record
            new_record = ModelVersionHistory(
                model_name=model_name,
                version_tag=version_tag,
                yaml_hash=yaml_hash,
            )
            session.add(new_record)
            session.commit()

            logger.info(
                "New version detected and recorded for '%s' "
                "(tag=%s, hash=%s…)",
                model_name,
                version_tag,
                yaml_hash[:12],
            )
            return True

        except Exception:
            session.rollback()
            raise
        finally:
            if own_session:
                session.close()
