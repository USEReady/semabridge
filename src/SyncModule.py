"""
SyncModule

This module provides a stable wrapper around the existing CLI sync command
without changing sync behavior or execution flow.

IMPORTANT:
- The sync implementation remains in semabridge.cli.main.sync.
- This wrapper only forwards arguments.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from semabridge.cli.main import sync as cli_sync


def sync(
    source: Optional[str] = None,
    target: Optional[str] = None,
    dataset_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    tag: Optional[str] = None,
    name: Optional[str] = None,
    dry_run: bool = False,
    output_dir: Path = Path("output"),
    parallel: bool = False,
) -> None:
    """Forward directly to the existing CLI sync entrypoint.

    Args:
        source: Source platform.
        target: Target platform.
        dataset_id: Fabric dataset id.
        workspace_id: Fabric workspace id.
        tag: Version tag.
        name: Optional model name override.
        dry_run: Skip final publishing when True.
        output_dir: Output directory.
        parallel: Enable parallel mode when True.
    """
    cli_sync(
        source=source,
        target=target,
        dataset_id=dataset_id,
        workspace_id=workspace_id,
        tag=tag,
        name=name,
        dry_run=dry_run,
        output_dir=output_dir,
        parallel=parallel,
    )
