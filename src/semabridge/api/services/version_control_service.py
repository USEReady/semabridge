import hashlib
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from semabridge.repository.model_repository import ModelRepository

logger = logging.getLogger("semabridge.api")


class VersionControlService:
    """Encapsulate version history, snapshot, compare, and rollback logic."""

    def __init__(
        self,
        db_manager: ModelRepository,
        models_path_resolver: Callable[[], Path],
        hash_tracker: Optional[Dict[str, str]] = None,
    ) -> None:
        self.db_manager = db_manager
        self.models_path_resolver = models_path_resolver
        self.hash_tracker = hash_tracker if hash_tracker is not None else {}

    def list_versions(
        self,
        model_id: str = "",
        workspace_id: str = "",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        all_versions: List[Dict[str, Any]] = []

        if model_id:
            all_versions.extend(
                self.db_manager.list_model_versions(
                    model_id=model_id,
                    workspace_id=workspace_id or None,
                    limit=limit,
                )
            )
        else:
            model_ids = self._discover_model_ids()
            for mid in model_ids:
                all_versions.extend(
                    self.db_manager.list_model_versions(
                        model_id=mid,
                        workspace_id=workspace_id or None,
                        limit=limit,
                    )
                )

        for item in all_versions:
            item.setdefault("source", "model_versions")
            item.setdefault("can_compare", True)
            item.setdefault("can_rollback", True)

        return all_versions

    def compare_versions(self, version_id_old: str, version_id_new: str) -> List[Dict[str, Any]]:
        return self.db_manager.compare_model_versions_tabular(version_id_old, version_id_new)

    def delete_versions(self, model_id: str, workspace_id: str = "") -> int:
        return self.db_manager.delete_model_versions(
            model_id=model_id,
            workspace_id=workspace_id or None,
        )

    def get_snapshot(self, version_id: str) -> Optional[Dict[str, Any]]:
        return self.db_manager.get_model_version_snapshot(version_id)

    def rollback_version(
        self,
        version_id: str,
        model_id: str,
        workspace_id: str,
        author: str = "ui",
    ) -> Dict[str, Any]:
        if not version_id:
            raise ValueError("version_id is required")
        if not model_id:
            raise ValueError("model_id is required")

        snapshot = self.db_manager.get_model_version_snapshot(version_id)
        if snapshot is None:
            raise ValueError(f"Version '{version_id}' not found")
        if not isinstance(snapshot, dict) or not snapshot:
            raise ValueError(f"Version '{version_id}' does not contain a usable snapshot")

        new_id = self.db_manager.rollback_model_version(
            model_id=model_id,
            target_version_id=version_id,
            workspace_id=workspace_id,
            author=author,
        )

        target_file = None

        if snapshot and model_id != "default":
            target_file = self._write_snapshot_to_model_file(model_id, snapshot)

        logger.info(
            "ROLLBACK completed model=%s source=%s new=%s file=%s",
            model_id,
            version_id[:8],
            new_id[:8],
            str(target_file) if target_file else "n/a",
        )

        return {
            "status": "success",
            "new_version_id": new_id,
            "model_id": model_id,
            "rolled_back_from": version_id,
            "is_rollback": True,
            "target_file": str(target_file) if target_file else None,
        }

    def _discover_model_ids(self) -> List[str]:
        try:
            model_ids = self.db_manager.list_distinct_model_ids()
        except Exception:
            model_ids = []

        models_path = self.models_path_resolver()
        if models_path.exists():
            for item in sorted(models_path.iterdir()):
                if item.is_file() and item.suffix.lower() in (".yaml", ".yml", ".json"):
                    mid = item.stem
                    if mid not in model_ids:
                        model_ids.append(mid)

        return model_ids

    def _resolve_target_file(self, model_id: str) -> Path:
        models_path = self.models_path_resolver()
        if not models_path.exists():
            raise ValueError(f"Local models path does not exist: {models_path}")

        direct_matches: List[Path] = []
        for ext in (".yaml", ".yml", ".json"):
            candidate = models_path / f"{model_id}{ext}"
            if candidate.exists():
                direct_matches.append(candidate)
        if direct_matches:
            return direct_matches[0]

        normalized_model_id = self._normalize_model_key(model_id)
        recursive_matches: List[Path] = []
        for pattern in ("*.yaml", "*.yml", "*.json"):
            for candidate in models_path.rglob(pattern):
                if self._normalize_model_key(candidate.stem) == normalized_model_id:
                    recursive_matches.append(candidate)

        if recursive_matches:
            recursive_matches.sort(key=lambda path: (len(path.parts), str(path).lower()))
            return recursive_matches[0]

        raise ValueError(
            f"No local model file found for '{model_id}' under {models_path}. "
            "Rollback needs an existing YAML/JSON model file to restore."
        )

    def _write_snapshot_to_model_file(self, model_id: str, snapshot: Dict[str, Any]) -> Path:
        target_file = self._resolve_target_file(model_id)
        rolled_back_content = yaml.dump(
            snapshot,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )
        target_file.write_text(rolled_back_content, encoding="utf-8")
        self.hash_tracker[model_id] = hashlib.sha256(
            rolled_back_content.encode("utf-8")
        ).hexdigest()
        return target_file

    @staticmethod
    def _normalize_model_key(value: str) -> str:
        return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())
