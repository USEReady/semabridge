"""
Core data models and enums for the concurrent processing system.

Defines ExecutionMode, ProcessingStage, WorkerStatus, ModelResult,
BatchResult, BroadcastResult, RetryConfig, ValidationResult,
ResourceMetrics, and BroadcastScenario.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class ExecutionMode(str, Enum):
    """Execution mode for parallel processing."""

    BEST_EFFORT = "best_effort"  # Continue on failure
    STRICT = "strict"  # Abort on any failure


class ProcessingStage(str, Enum):
    """Stages of model processing through the pipeline."""

    IDLE = "idle"
    VALIDATING = "validating"
    EXTRACTING = "extracting"
    CONVERTING = "converting"
    PERSISTING = "persisting"
    DEPLOYING = "deploying"
    COMPLETED = "completed"
    FAILED = "failed"


class ErrorType(str, Enum):
    """Classification of errors."""

    TRANSIENT = "transient"
    PERMANENT = "permanent"


@dataclass
class RetryConfig:
    """Configuration for retry behavior.

    Attributes:
        max_retries: Maximum number of retry attempts (default 3).
        base_delay: Base delay in seconds for exponential backoff (default 1.0).
        max_delay: Maximum delay cap in seconds (default 60.0).
        exponential_base: Multiplier for exponential backoff (default 2.0).
    """

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError(f"max_retries must be >= 0, got {self.max_retries}")
        if self.base_delay <= 0:
            raise ValueError(f"base_delay must be > 0, got {self.base_delay}")
        if self.max_delay <= 0:
            raise ValueError(f"max_delay must be > 0, got {self.max_delay}")
        if self.exponential_base < 1:
            raise ValueError(
                f"exponential_base must be >= 1, got {self.exponential_base}"
            )


@dataclass
class WorkerStatus:
    """Real-time status of a worker process."""

    worker_id: str
    model_name: Optional[str] = None
    stage: ProcessingStage = ProcessingStage.IDLE
    start_time: Optional[datetime] = None
    progress_percent: float = 0.0


@dataclass
class ModelResult:
    """Result of processing a single model through the pipeline."""

    model_name: str
    success: bool
    duration_seconds: float = 0.0
    error: Optional[str] = None
    error_type: Optional[str] = None  # "transient" or "permanent"
    retry_count: int = 0
    snapshot_id: Optional[str] = None
    step_failed: Optional[str] = None  # Which pipeline step failed

    def __post_init__(self) -> None:
        if self.duration_seconds < 0:
            raise ValueError(
                f"duration_seconds must be >= 0, got {self.duration_seconds}"
            )
        if self.retry_count < 0:
            raise ValueError(f"retry_count must be >= 0, got {self.retry_count}")


@dataclass
class BatchResult:
    """Result of processing a batch of models in parallel."""

    batch_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    total_models: int = 0
    successful_models: List[ModelResult] = field(default_factory=list)
    failed_models: List[ModelResult] = field(default_factory=list)
    total_duration_seconds: float = 0.0
    worker_count: int = 0
    execution_mode: ExecutionMode = ExecutionMode.BEST_EFFORT

    @property
    def success_count(self) -> int:
        return len(self.successful_models)

    @property
    def failure_count(self) -> int:
        return len(self.failed_models)

    @property
    def all_succeeded(self) -> bool:
        return self.failure_count == 0 and self.success_count == self.total_models


@dataclass
class BroadcastResult:
    """Result of broadcasting a single model to multiple targets."""

    model_name: str
    source_extraction_duration: float = 0.0
    target_results: Dict[str, ModelResult] = field(default_factory=dict)

    @property
    def overall_success(self) -> bool:
        return all(r.success for r in self.target_results.values())


@dataclass
class ValidationResult:
    """Result of fail-fast validation checks."""

    success: bool = True
    source_connectivity: bool = True
    credentials_valid: bool = True
    target_connectivity: Dict[str, bool] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)


@dataclass
class ResourceMetrics:
    """Current resource usage metrics snapshot."""

    cpu_percent: float = 0.0
    memory_mb: int = 0
    available_memory_mb: int = 0
    active_workers: int = 0
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class BroadcastScenario:
    """Single-source to multi-target broadcasting scenario."""

    source_model: str
    target_names: List[str] = field(default_factory=list)


@dataclass
class ConcurrencyConfig:
    """Configuration for concurrency settings.

    Supports hierarchical overrides: CLI > Project > Global > Default.
    """

    max_processes: Optional[int] = None  # None = auto-detect
    max_threads: int = 8
    enable_parallel: bool = True
    execution_mode: ExecutionMode = ExecutionMode.BEST_EFFORT
    retry: RetryConfig = field(default_factory=RetryConfig)
    memory_threshold_percent: float = 80.0
    disk_space_check: bool = True
    min_disk_space_mb: int = 1000

    def __post_init__(self) -> None:
        if self.max_processes is not None and self.max_processes < 1:
            raise ValueError(
                f"max_processes must be >= 1 or None (auto), got {self.max_processes}"
            )
        if self.max_threads < 1:
            raise ValueError(f"max_threads must be >= 1, got {self.max_threads}")
        if not (0 < self.memory_threshold_percent <= 100):
            raise ValueError(
                f"memory_threshold_percent must be in (0, 100], "
                f"got {self.memory_threshold_percent}"
            )
        if self.min_disk_space_mb < 0:
            raise ValueError(
                f"min_disk_space_mb must be >= 0, got {self.min_disk_space_mb}"
            )
