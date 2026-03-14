"""
File manager for atomic, concurrent-safe file operations.

Provides atomic writes (write-to-temp-then-rename), file locking for
shared resources, disk space checking, and unique path generation
per worker to prevent conflicts.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Timeout for acquiring file locks (seconds)
_LOCK_TIMEOUT = 10.0
_LOCK_RETRY_DELAY = 0.2
_LOCK_MAX_RETRIES = 50  # 50 × 0.2s = 10s


class FileLock:
    """Simple file-based lock using a .lock file.

    Uses atomic os.open(O_CREAT | O_EXCL) for cross-process safety.
    """

    def __init__(self, path: Path, timeout: float = _LOCK_TIMEOUT) -> None:
        self._lock_path = path.with_suffix(path.suffix + ".lock")
        self._timeout = timeout
        self._fd: Optional[int] = None

    def acquire(self) -> bool:
        """Acquire the file lock with timeout.

        Returns:
            ``True`` if lock acquired, ``False`` on timeout.
        """
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            try:
                self._fd = os.open(
                    str(self._lock_path),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
                return True
            except FileExistsError:
                time.sleep(_LOCK_RETRY_DELAY)
            except OSError as e:
                logger.warning("Lock acquire error for %s: %s", self._lock_path, e)
                time.sleep(_LOCK_RETRY_DELAY)
        return False

    def release(self) -> None:
        """Release the file lock."""
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        try:
            os.unlink(str(self._lock_path))
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning("Lock release error for %s: %s", self._lock_path, e)

    def __enter__(self):
        if not self.acquire():
            raise TimeoutError(
                f"Could not acquire file lock: {self._lock_path} "
                f"(timeout={self._timeout}s)"
            )
        return self

    def __exit__(self, *exc_info):
        self.release()


class FileManager:
    """Handle concurrent file system operations safely.

    Features:
    - Atomic writes (write-to-temp-then-rename)
    - Unique file paths per worker
    - File locking for shared resources
    - Disk space checking

    Args:
        base_path: Base output directory for artifacts.
    """

    def __init__(self, base_path: Path) -> None:
        self._base_path = Path(base_path)
        self._base_path.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write_artifact(
        self,
        artifact_name: str,
        content: str,
        worker_id: str,
    ) -> Path:
        """Write an artifact atomically.

        Uses the write-to-temp-then-rename pattern to prevent
        partial/corrupted files.

        Args:
            artifact_name: File name for the artifact.
            content: Content to write.
            worker_id: Unique identifier of the calling worker.

        Returns:
            Path to the written file.
        """
        # Generate unique directory per worker
        worker_dir = self._base_path / worker_id
        worker_dir.mkdir(parents=True, exist_ok=True)
        target_path = worker_dir / artifact_name

        # Atomic write: temp file → rename
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=str(worker_dir),
                prefix=f".{artifact_name}_",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                # Atomic rename (same filesystem)
                os.replace(tmp_path, str(target_path))
            except Exception:
                # Clean up temp file on failure
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as exc:
            logger.error(
                "Failed to write artifact %s for worker %s: %s",
                artifact_name,
                worker_id,
                exc,
            )
            raise

        logger.debug("Wrote artifact: %s", target_path)
        return target_path

    def write_shared_artifact(
        self,
        artifact_name: str,
        content: str,
    ) -> Path:
        """Write to a shared location with file locking.

        Args:
            artifact_name: File name for the artifact.
            content: Content to write.

        Returns:
            Path to the written file.
        """
        target_path = self._base_path / artifact_name
        lock = FileLock(target_path)

        with lock:
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self._base_path),
                prefix=f".{artifact_name}_",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                os.replace(tmp_path, str(target_path))
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

        return target_path

    def acquire_lock(
        self,
        resource_name: str,
        timeout: float = _LOCK_TIMEOUT,
    ) -> FileLock:
        """Create a file lock for a shared resource.

        Args:
            resource_name: Name of the resource to lock.
            timeout: Seconds to wait for the lock.

        Returns:
            A :class:`FileLock` instance (not yet acquired).
        """
        lock_path = self._base_path / resource_name
        return FileLock(lock_path, timeout=timeout)

    def ensure_disk_space(self, required_mb: int = 1000) -> bool:
        """Check if sufficient disk space is available.

        Args:
            required_mb: Minimum required free space in MB.

        Returns:
            ``True`` if enough space is available.

        Raises:
            OSError: If disk space check fails critically.
        """
        import shutil

        try:
            usage = shutil.disk_usage(str(self._base_path))
            free_mb = usage.free // (1024 * 1024)
            if free_mb < required_mb:
                logger.warning(
                    "Insufficient disk space: %dMB free, %dMB required at %s",
                    free_mb,
                    required_mb,
                    self._base_path,
                )
                return False
            return True
        except OSError as exc:
            logger.error("Disk space check failed: %s", exc)
            raise

    def get_worker_dir(self, worker_id: str) -> Path:
        """Get or create the artifact directory for a worker.

        Args:
            worker_id: Unique worker identifier.

        Returns:
            Path to the worker's artifact directory.
        """
        worker_dir = self._base_path / worker_id
        worker_dir.mkdir(parents=True, exist_ok=True)
        return worker_dir

    def cleanup_worker_dir(self, worker_id: str) -> None:
        """Remove a worker's artifact directory after completion.

        Args:
            worker_id: Worker whose directory to clean up.
        """
        worker_dir = self._base_path / worker_id
        if worker_dir.exists():
            import shutil

            shutil.rmtree(str(worker_dir), ignore_errors=True)
            logger.debug("Cleaned up worker directory: %s", worker_dir)
