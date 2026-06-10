"""
Cross-platform configuration path resolution.

Resolves config/, project, and profile directories with support for
case-insensitive filesystems (Windows: 'Config/' vs Mac/Linux: 'config/').

Usage:
    from semabridge.core.config_paths import ConfigPathResolver
    resolver = ConfigPathResolver()
    cfg_dir = resolver.config_root()        # e.g. /repo/config
    projects = resolver.projects_dir()      # e.g. /repo/config/projects
    profiles = resolver.profiles_dir()      # e.g. /repo/config/profiles
"""
from __future__ import annotations
from pathlib import Path
from typing import List, Optional


class ConfigPathResolver:
    """Resolves config, project, and profile paths with cross-platform casing support."""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self._base = base_dir or Path(__file__).parents[4]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def repo_root(self) -> Path:
        """Absolute path to the repository root (4 levels above this file)."""
        return self._base

    def config_roots(self) -> List[Path]:
        """All candidate config directories in resolution order."""
        cwd = Path.cwd()
        return [
            cwd / "config",
            cwd / "Config",
            self._base / "config",
            self._base / "Config",
        ]

    def config_root(self) -> Path:
        """First existing candidate from config_roots(); falls back to repo/Config."""
        for candidate in self.config_roots():
            if candidate.exists():
                return candidate
        return self._base / "Config"

    def projects_dirs(self) -> List[Path]:
        """All candidate projects subdirectories."""
        return [r / "projects" for r in self.config_roots()]

    def projects_dir(self) -> Path:
        """Single resolved projects directory."""
        return self.config_root() / "projects"

    def profiles_dir(self) -> Path:
        """Single resolved profiles directory."""
        return self.config_root() / "profiles"


# Module-level singleton for convenience
_default_resolver: Optional[ConfigPathResolver] = None


def get_path_resolver() -> ConfigPathResolver:
    """Return the process-wide default ConfigPathResolver."""
    global _default_resolver
    if _default_resolver is None:
        _default_resolver = ConfigPathResolver()
    return _default_resolver
