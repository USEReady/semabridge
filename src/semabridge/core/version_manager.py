"""
Version Manager.

Handles persistence, history, and rollback of configuration versions.
Stored in .semabridge/history/
"""

from __future__ import annotations

import json
import time
import hashlib
from pathlib import Path
from typing import List, Dict, Optional, Any
from datetime import datetime

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class VersionManager:
    """
    Manages version history for semabridge.yaml.
    """
    
    def __init__(self, repo_path: Path = Path(".semabridge")):
        self.history_dir = repo_path / "history"
        self.history_dir.mkdir(parents=True, exist_ok=True)
        
    def create_version(self, content: str, description: str = "Auto-save") -> str:
        """
        Create a new version snapshot.
        
        Args:
            content: The YAML content string
            description: Optional description
            
        Returns:
            version_id: The unique ID of the new version
        """
        timestamp_float = time.time()
        timestamp_int = int(timestamp_float)
        # Generate hash of content for deduplication check or ID generation
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]
        version_id = f"v{timestamp_int}_{content_hash}"
        
        version_data = {
            "version_id": version_id,
            "timestamp": timestamp_float,
            "date": datetime.fromtimestamp(timestamp_int).isoformat(),
            "description": description,
            "content_hash": content_hash,
            "content": content
        }
        
        version_file = self.history_dir / f"{version_id}.json"
        
        try:
            with open(version_file, "w", encoding="utf-8") as f:
                json.dump(version_data, f, indent=2)
            logger.info(f"Created version {version_id}")
            return version_id
        except Exception as e:
            logger.error(f"Failed to create version: {e}")
            raise

    def list_versions(self) -> List[Dict[str, Any]]:
        """
        List all available versions, sorted by timestamp (newest first).
        """
        try:
            versions = []
            for f in self.history_dir.glob("*.json"):
                try:
                    with open(f, "r", encoding="utf-8") as vf:
                        data = json.load(vf)
                        # Ensure minimal fields exist
                        if "version_id" in data and "timestamp" in data:
                            versions.append(data)
                except Exception as e:
                    logger.warning(f"Failed to read version file {f}: {e}")
            
            # Sort by timestamp desc
            return sorted(versions, key=lambda x: x["timestamp"], reverse=True)
            
        except Exception as e:
            logger.error(f"Failed to list versions: {e}")
            return []

    def get_version(self, version_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve full data for a specific version."""
        try:
            version_file = self.history_dir / f"{version_id}.json"
            if not version_file.exists():
                return None
                
            with open(version_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to get version {version_id}: {e}")
            return None
