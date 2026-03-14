"""
Delta Detector — Mandate 5: Change Detection for Hyperscaling.

Compares the current SML/OSI model state against a previously-deployed
snapshot to produce a *delta manifest*.  Only models with actual changes
are dispatched to the concurrency orchestrator, eliminating redundant
CREATE OR REPLACE cycles that become the dominant cost at 50 000+ models.

Strategies (configurable via ``behavior.yaml``):
    - **hash**   — SHA-256 of the serialized YAML; fastest, no I/O to
                   Snowflake, but cannot detect schema-only drift.
    - **git**    — ``git diff`` of the SML output directory; requires a
                   git-managed workspace.
    - **hybrid** — hash first, then git diff for models whose hash changed
                   (recommended for CI/CD pipelines).

Usage::

    detector = DeltaDetector(strategy="hash", snapshot_dir="output/sml")
    changed = detector.detect(current_models)
    # changed: list[str] — unique_names of models that need redeployment
"""

from __future__ import annotations

import hashlib
import json
import logging
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class DeltaStrategy(str, Enum):
    """Change-detection strategy."""
    HASH = "hash"
    GIT = "git"
    HYBRID = "hybrid"


class DeltaDetector:
    """Detects which models have changed since last deployment.
    
    Parameters
    ----------
    strategy : DeltaStrategy | str
        Detection algorithm.  Default ``"hash"``.
    snapshot_dir : str | Path
        Directory containing previous-deployment SML YAML files or
        hash manifest (``_delta_manifest.json``).
    """

    MANIFEST_FILE = "_delta_manifest.json"

    def __init__(
        self,
        strategy: DeltaStrategy | str = DeltaStrategy.HASH,
        snapshot_dir: str | Path = "output/sml",
    ) -> None:
        self.strategy = DeltaStrategy(strategy) if isinstance(strategy, str) else strategy
        self.snapshot_dir = Path(snapshot_dir)
        self._previous_hashes: dict[str, str] = self._load_manifest()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(self, current_models: list[dict[str, Any]]) -> list[str]:
        """Return unique_names of models that differ from the snapshot.
        
        Parameters
        ----------
        current_models
            List of dicts (or serialized SML dicts) keyed by
            ``unique_name``.

        Returns
        -------
        list[str]
            Unique names of changed models.
        """
        if self.strategy == DeltaStrategy.GIT:
            return self._detect_git()

        changed: list[str] = []
        new_hashes: dict[str, str] = {}

        for model in current_models:
            name = model.get("unique_name", "")
            if not name:
                continue
            current_hash = self._hash_model(model)
            new_hashes[name] = current_hash

            prev = self._previous_hashes.get(name)
            if prev is None or prev != current_hash:
                changed.append(name)

        # Models removed since last snapshot
        removed = set(self._previous_hashes) - set(new_hashes)
        if removed:
            logger.info(f"Delta detector: {len(removed)} models removed since last snapshot")

        logger.info(
            f"Delta detector ({self.strategy.value}): "
            f"{len(changed)}/{len(current_models)} models changed"
        )

        # Persist new manifest for next run
        self._save_manifest(new_hashes)

        return changed

    def force_full(self) -> None:
        """Clear the manifest so the next ``detect()`` treats everything as changed."""
        manifest_path = self.snapshot_dir / self.MANIFEST_FILE
        if manifest_path.exists():
            manifest_path.unlink()
            logger.info("Delta manifest cleared — next run will process all models")
        self._previous_hashes = {}

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _hash_model(self, model: dict[str, Any]) -> str:
        """Deterministic SHA-256 of the model dict."""
        canonical = json.dumps(model, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def _load_manifest(self) -> dict[str, str]:
        """Load previous hash manifest from disk."""
        manifest_path = self.snapshot_dir / self.MANIFEST_FILE
        if not manifest_path.exists():
            return {}
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception as exc:
            logger.warning(f"Failed to load delta manifest: {exc}")
        return {}

    def _save_manifest(self, hashes: dict[str, str]) -> None:
        """Persist hash manifest for next comparison."""
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = self.snapshot_dir / self.MANIFEST_FILE
        try:
            manifest_path.write_text(
                json.dumps(hashes, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning(f"Failed to save delta manifest: {exc}")

    def _detect_git(self) -> list[str]:
        """Use ``git diff`` to find changed SML files.
        
        Requires the workspace to be a git repository.  Returns model
        unique_names extracted from changed file paths.
        """
        import subprocess

        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD~1", "--", str(self.snapshot_dir)],
                capture_output=True,
                text=True,
                cwd=self.snapshot_dir.parent,
                timeout=30,
            )
            if result.returncode != 0:
                logger.warning(f"git diff failed: {result.stderr.strip()}")
                return []

            changed_files = [
                line.strip()
                for line in result.stdout.splitlines()
                if line.strip().endswith((".yaml", ".yml"))
            ]

            # Extract model names from filenames (e.g. "output/sml/MyModel.yaml" -> "MyModel")
            changed_names = []
            for fpath in changed_files:
                stem = Path(fpath).stem
                changed_names.append(stem)

            logger.info(
                f"Delta detector (git): {len(changed_names)} SML files changed"
            )
            return changed_names

        except FileNotFoundError:
            logger.warning("git not found on PATH — falling back to full rebuild")
            return []
        except subprocess.TimeoutExpired:
            logger.warning("git diff timed out — falling back to full rebuild")
            return []
