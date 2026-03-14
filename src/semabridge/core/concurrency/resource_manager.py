"""
Resource manager for hardware-aware worker pool sizing.

Auto-detects CPU cores, monitors memory, and calculates optimal
worker count respecting configuration overrides.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from semabridge.core.concurrency.models import ResourceMetrics

logger = logging.getLogger(__name__)


@dataclass
class _MemoryInfo:
    total_mb: int
    available_mb: int
    percent_used: float


class ResourceManager:
    """Detect hardware resources and calculate optimal worker count.

    Worker count formula::

        min(model_count, max(1, configured_limit or (cpu_cores - 1)))

    Configuration precedence: CLI > Project Config > Global Config > Default.

    Args:
        max_processes: Explicit process limit (from highest-priority config).
        max_threads: Thread limit per process for I/O work.
        memory_threshold_percent: Reduce workers if memory exceeds this.
    """

    def __init__(
        self,
        max_processes: Optional[int] = None,
        max_threads: int = 8,
        memory_threshold_percent: float = 80.0,
    ) -> None:
        if max_processes is not None and max_processes < 1:
            raise ValueError(f"max_processes must be >= 1, got {max_processes}")
        self._max_processes = max_processes
        self._max_threads = max_threads
        self._memory_threshold = memory_threshold_percent
        self._cpu_count: Optional[int] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_cpu_count(self) -> int:
        """Detect number of available CPU cores (cached)."""
        if self._cpu_count is None:
            self._cpu_count = os.cpu_count() or 1
        return self._cpu_count

    def get_available_memory(self) -> int:
        """Get available memory in MB. Returns 0 if unavailable."""
        info = self._get_memory_info()
        return info.available_mb if info else 0

    def calculate_worker_count(self, model_count: int) -> int:
        """Calculate optimal worker count.

        Args:
            model_count: Number of models to process.

        Returns:
            Worker count (always >= 1).
        """
        if model_count <= 0:
            return 1

        # Determine the cap
        if self._max_processes is not None:
            cap = self._max_processes
        else:
            cap = max(1, self.get_cpu_count() - 1)

        worker_count = min(model_count, cap)
        worker_count = max(1, worker_count)

        # Optionally reduce based on memory pressure
        if self.should_reduce_workers(self.monitor_resources()):
            reduced = max(1, worker_count // 2)
            if reduced < worker_count:
                logger.warning(
                    "Memory pressure detected (>%.0f%%) — reducing workers from %d to %d",
                    self._memory_threshold,
                    worker_count,
                    reduced,
                )
                worker_count = reduced

        logger.info(
            "Worker count: %d (models=%d, cpu_cap=%s, max_processes=%s)",
            worker_count,
            model_count,
            cap,
            self._max_processes,
        )
        return worker_count

    def monitor_resources(self) -> ResourceMetrics:
        """Take a snapshot of current resource usage."""
        mem = self._get_memory_info()
        cpu_pct = 0.0
        try:
            # Try psutil for CPU %, but it's optional
            import psutil  # type: ignore[import-untyped]

            cpu_pct = psutil.cpu_percent(interval=0.1)
        except ImportError:
            pass

        return ResourceMetrics(
            cpu_percent=cpu_pct,
            memory_mb=mem.total_mb - mem.available_mb if mem else 0,
            available_memory_mb=mem.available_mb if mem else 0,
            active_workers=0,  # Caller should update this
            timestamp=datetime.now(),
        )

    def should_reduce_workers(self, metrics: ResourceMetrics) -> bool:
        """Return ``True`` if workers should be reduced due to memory pressure."""
        mem = self._get_memory_info()
        if mem is None:
            return False
        return mem.percent_used > self._memory_threshold

    def check_disk_space(self, path: str, required_mb: int = 1000) -> bool:
        """Check if sufficient disk space is available.

        Args:
            path: Path to check disk space for.
            required_mb: Minimum required free space in MB.

        Returns:
            ``True`` if enough space is available.
        """
        try:
            usage = shutil.disk_usage(path)
            free_mb = usage.free // (1024 * 1024)
            if free_mb < required_mb:
                logger.warning(
                    "Low disk space: %dMB free, %dMB required at %s",
                    free_mb,
                    required_mb,
                    path,
                )
                return False
            return True
        except OSError as exc:
            logger.warning("Could not check disk space at %s: %s", path, exc)
            return True  # Optimistic — don't block on check failure

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_memory_info() -> Optional[_MemoryInfo]:
        """Get memory info via psutil (optional) or fallback."""
        try:
            import psutil  # type: ignore[import-untyped]

            vm = psutil.virtual_memory()
            return _MemoryInfo(
                total_mb=vm.total // (1024 * 1024),
                available_mb=vm.available // (1024 * 1024),
                percent_used=vm.percent,
            )
        except ImportError:
            # psutil not installed — return a safe default
            return _MemoryInfo(total_mb=8192, available_mb=4096, percent_used=50.0)
