# Concurrency Engine - Design

**Feature:** Parallel Model Processing and Multi-Target Broadcasting
**Version:** 2.0
**Last Updated:** February 6, 2026

---

## 1. Architecture Overview

The Concurrency Engine uses a two-phase parallel execution model:
1. **Phase 1**: Parallel model extraction and conversion to SML/OSI
2. **Phase 2**: Parallel target deployment from SML/OSI

```
┌─────────────────────────────────────────────────────────────┐
│                    Execution Orchestrator                    │
│  (Manages worker pool, task queue, and result collection)   │
└────────────────┬────────────────────────────────────────────┘
                 │
        ┌────────┴────────┬────────────┬──────────────┐
        │                 │            │              │
   ┌────▼─────┐      ┌───▼────┐  ┌───▼────┐    ┌───▼────┐
   │ Worker 1 │      │Worker 2│  │Worker 3│    │Worker N│
   │          │      │        │  │        │    │        │
   │ Model A  │      │Model B │  │Model C │    │Model N │
   └────┬─────┘      └───┬────┘  └───┬────┘    └───┬────┘
        │                │           │              │
        │  Phase 1: Source → SML/OSI               │
        │                │           │              │
        └────────┬───────┴───────────┴──────────────┘
                 │
        ┌────────▼────────────────────────────────┐
        │      SML/OSI Artifacts (In Memory)      │
        └────────┬────────────────────────────────┘
                 │
        ┌────────┴────────┬────────────┬──────────────┐
        │                 │            │              │
   ┌────▼─────┐      ┌───▼────┐  ┌───▼────┐    ┌───▼────┐
   │ Worker 1 │      │Worker 2│  │Worker 3│    │Worker N│
   │          │      │        │  │        │    │        │
   │Target A  │      │Target B│  │Target C│    │Target N│
   └──────────┘      └────────┘  └────────┘    └────────┘
        │
        │  Phase 2: SML/OSI → Targets (Parallel)
        │
```

---

## 2. Component Design

### 2.1 Execution Engine Core

**File:** `src/semabridge/core/execution_engine.py`

```python
import os
import logging
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Callable
from dataclasses import dataclass
from enum import Enum

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from semabridge.core.exceptions import (
    NetworkTransientError,
    RateLimitError,
    PermanentError,
)


class FailureStrategy(Enum):
    """Strategy for handling task failures."""
    BEST_EFFORT = "best_effort"  # Continue on failure
    STRICT = "strict"  # Abort on first failure


@dataclass
class TaskResult:
    """Result of a single task execution."""
    task_id: str
    model_name: str
    status: str  # 'success', 'failed', 'skipped'
    error: str = ""
    duration_seconds: float = 0.0
    retry_count: int = 0


@dataclass
class ExecutionConfig:
    """Configuration for execution engine."""
    max_workers: int = None  # None = auto-detect
    max_retries: int = 3
    retry_delay_seconds: float = 1.0
    failure_strategy: FailureStrategy = FailureStrategy.BEST_EFFORT
    enable_progress_bar: bool = True


class ExecutionEngine:
    """
    Parallel execution engine for semantic model processing.
    
    Supports:
    - Multi-model parallelism
    - Multi-target broadcasting
    - Intelligent retry logic
    - Live progress reporting
    """
    
    def __init__(self, config: ExecutionConfig):
        self.config = config
        self.max_workers = self._determine_workers(config.max_workers)
        self.logger = logging.getLogger(__name__)
        self.results: List[TaskResult] = []
    
    def _determine_workers(self, setting: int = None) -> int:
        """Determine optimal worker count based on hardware."""
        if setting is not None and setting > 0:
            return setting
        
        # Auto-detect: CPU cores - 1 (leave one for OS)
        cpu_count = os.cpu_count() or 1
        return max(1, cpu_count - 1)
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((NetworkTransientError, RateLimitError)),
        reraise=True,
    )
    def _execute_single_model_task(
        self,
        model_name: str,
        source_connector: Any,
        target_connectors: List[Any],
    ) -> TaskResult:
        """
        Execute a single model processing task.
        
        This is the atomic unit of work submitted to a worker.
        
        Args:
            model_name: Name of the model to process
            source_connector: Source connector instance
            target_connectors: List of target connector instances
            
        Returns:
            TaskResult with execution details
        """
        import time
        start_time = time.time()
        
        try:
            # Phase 1: Extract source → SML/OSI
            self.logger.info(f"[{model_name}] Extracting from source...")
            sml_artifact = source_connector.extract_to_sml(model_name)
            
            # Phase 2: Deploy to targets (can be parallelized further)
            target_results = {}
            for target in target_connectors:
                self.logger.info(
                    f"[{model_name}] Deploying to {target.platform_name}..."
                )
                target.emit(sml_artifact)
                target_results[target.platform_name] = "SUCCESS"
            
            duration = time.time() - start_time
            return TaskResult(
                task_id=model_name,
                model_name=model_name,
                status="success",
                duration_seconds=duration,
            )
            
        except (NetworkTransientError, RateLimitError) as e:
            # These will be retried by tenacity
            self.logger.warning(f"[{model_name}] Transient error: {e}")
            raise
            
        except PermanentError as e:
            # Fail fast - don't retry
            self.logger.error(f"[{model_name}] Permanent error: {e}")
            duration = time.time() - start_time
            return TaskResult(
                task_id=model_name,
                model_name=model_name,
                status="failed",
                error=str(e),
                duration_seconds=duration,
            )
            
        except Exception as e:
            # Unknown error - log and fail
            self.logger.exception(f"[{model_name}] Unexpected error: {e}")
            duration = time.time() - start_time
            return TaskResult(
                task_id=model_name,
                model_name=model_name,
                status="failed",
                error=str(e),
                duration_seconds=duration,
            )
    
    def run_batch(
        self,
        model_list: List[str],
        source_connector: Any,
        target_connectors: List[Any],
        progress_callback: Callable = None,
    ) -> List[TaskResult]:
        """
        Execute batch processing of multiple models.
        
        Args:
            model_list: List of model names to process
            source_connector: Source connector instance
            target_connectors: List of target connector instances
            progress_callback: Optional callback for progress updates
            
        Returns:
            List of TaskResult objects
        """
        self.logger.info(
            f"Starting batch execution: {len(model_list)} models, "
            f"{self.max_workers} workers"
        )
        
        results = []
        
        # Use ProcessPoolExecutor for CPU-intensive tasks
        with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all tasks
            future_to_model = {
                executor.submit(
                    self._execute_single_model_task,
                    model_name,
                    source_connector,
                    target_connectors,
                ): model_name
                for model_name in model_list
            }
            
            # Process completed tasks
            completed = 0
            total = len(model_list)
            
            for future in as_completed(future_to_model):
                model_name = future_to_model[future]
                completed += 1
                
                try:
                    result = future.result()
                    results.append(result)
                    
                    if result.status == "success":
                        self.logger.info(
                            f"✓ [{completed}/{total}] {model_name} completed "
                            f"in {result.duration_seconds:.2f}s"
                        )
                    else:
                        self.logger.error(
                            f"✗ [{completed}/{total}] {model_name} failed: "
                            f"{result.error}"
                        )
                    
                    # Call progress callback
                    if progress_callback:
                        progress_callback(completed, total, result)
                    
                except Exception as exc:
                    self.logger.exception(
                        f"✗ [{completed}/{total}] {model_name} raised exception: {exc}"
                    )
                    results.append(
                        TaskResult(
                            task_id=model_name,
                            model_name=model_name,
                            status="failed",
                            error=str(exc),
                        )
                    )
                
                # Handle strict mode
                if (
                    self.config.failure_strategy == FailureStrategy.STRICT
                    and results[-1].status == "failed"
                ):
                    self.logger.error("Strict mode: Aborting on first failure")
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
        
        self.results = results
        return results
    
    def get_summary(self) -> Dict[str, Any]:
        """Get execution summary statistics."""
        total = len(self.results)
        success = sum(1 for r in self.results if r.status == "success")
        failed = sum(1 for r in self.results if r.status == "failed")
        total_duration = sum(r.duration_seconds for r in self.results)
        
        return {
            "total_models": total,
            "successful": success,
            "failed": failed,
            "success_rate": (success / total * 100) if total > 0 else 0,
            "total_duration_seconds": total_duration,
            "average_duration_seconds": (total_duration / total) if total > 0 else 0,
        }
```

### 2.2 Progress Dashboard

**File:** `src/semabridge/core/progress_dashboard.py`

```python
from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.live import Live
from rich.layout import Layout
from typing import List, Dict

from semabridge.core.execution_engine import TaskResult


class ProgressDashboard:
    """
    Live progress dashboard for parallel execution.
    
    Uses Rich library for terminal UI.
    """
    
    def __init__(self, total_models: int, max_workers: int):
        self.console = Console()
        self.total_models = total_models
        self.max_workers = max_workers
        self.completed = 0
        self.failed_models: List[TaskResult] = []
        self.active_workers: Dict[int, str] = {}
        
        # Create progress bar
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            console=self.console,
        )
        self.task_id = self.progress.add_task(
            "Processing models...",
            total=total_models,
        )
    
    def update(self, completed: int, total: int, result: TaskResult):
        """Update dashboard with task result."""
        self.completed = completed
        self.progress.update(self.task_id, completed=completed)
        
        if result.status == "failed":
            self.failed_models.append(result)
    
    def render(self) -> Layout:
        """Render the dashboard layout."""
        layout = Layout()
        
        # Overall progress
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="progress", size=3),
            Layout(name="workers", size=self.max_workers + 2),
            Layout(name="failures", size=10),
        )
        
        # Header
        layout["header"].update(
            f"[bold blue]SemaBridge Execution[/bold blue] "
            f"[dim](Workers: {self.max_workers})[/dim]"
        )
        
        # Progress bar
        layout["progress"].update(self.progress)
        
        # Worker status
        worker_table = Table(title="Active Workers", show_header=True)
        worker_table.add_column("Worker", style="cyan")
        worker_table.add_column("Status", style="green")
        
        for i in range(self.max_workers):
            status = self.active_workers.get(i, "Idle")
            worker_table.add_row(f"Worker {i+1}", status)
        
        layout["workers"].update(worker_table)
        
        # Failures
        if self.failed_models:
            failure_table = Table(title="Failed Models", show_header=True)
            failure_table.add_column("Model", style="red")
            failure_table.add_column("Error", style="yellow")
            
            for result in self.failed_models[-5:]:  # Show last 5
                failure_table.add_row(
                    result.model_name,
                    result.error[:50] + "..." if len(result.error) > 50 else result.error,
                )
            
            layout["failures"].update(failure_table)
        else:
            layout["failures"].update("[green]No failures[/green]")
        
        return layout
    
    def run_with_live_display(self, execution_func):
        """Run execution with live dashboard display."""
        with Live(self.render(), console=self.console, refresh_per_second=4):
            return execution_func(progress_callback=self.update)
```

### 2.3 Configuration Integration

**File:** `src/semabridge/core/execution_config.py`

```python
from pydantic import BaseModel, Field
from typing import Optional

class ExecutionSettings(BaseModel):
    """Execution engine settings from config file."""
    
    max_workers: Optional[int] = Field(
        default=None,
        description="Maximum worker count (None = auto-detect)",
    )
    
    max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Maximum retry attempts for transient failures",
    )
    
    retry_delay_seconds: float = Field(
        default=1.0,
        ge=0.1,
        le=60.0,
        description="Initial retry delay in seconds",
    )
    
    failure_strategy: str = Field(
        default="best_effort",
        description="Failure handling strategy: 'best_effort' or 'strict'",
    )
    
    enable_progress_bar: bool = Field(
        default=True,
        description="Enable live progress dashboard",
    )
    
    class Config:
        extra = "forbid"
```

---

## 3. Integration with CLI

**File:** `src/semabridge/cli/semantic_commands.py`

```python
import typer
from semabridge.core.execution_engine import ExecutionEngine, ExecutionConfig
from semabridge.core.progress_dashboard import ProgressDashboard

@app.command()
def sync(
    workers: int = typer.Option(None, "--workers", "-w", help="Number of parallel workers"),
    strict: bool = typer.Option(False, "--strict", help="Abort on first failure"),
):
    """Sync semantic models with parallel processing."""
    
    # Load config
    config = load_config()
    
    # Create execution config
    exec_config = ExecutionConfig(
        max_workers=workers or config.execution.max_workers,
        failure_strategy="strict" if strict else "best_effort",
    )
    
    # Create engine
    engine = ExecutionEngine(exec_config)
    
    # Discover models
    models = discover_models(config)
    
    # Create dashboard
    dashboard = ProgressDashboard(
        total_models=len(models),
        max_workers=engine.max_workers,
    )
    
    # Execute with live display
    results = dashboard.run_with_live_display(
        lambda progress_callback: engine.run_batch(
            model_list=models,
            source_connector=create_source_connector(config),
            target_connectors=create_target_connectors(config),
            progress_callback=progress_callback,
        )
    )
    
    # Print summary
    summary = engine.get_summary()
    typer.echo(f"\n✓ Completed: {summary['successful']}/{summary['total_models']}")
    typer.echo(f"✗ Failed: {summary['failed']}")
    typer.echo(f"⏱ Total time: {summary['total_duration_seconds']:.2f}s")
```

---

## 4. Error Handling Strategy

### 4.1 Exception Hierarchy

```python
# src/semabridge/core/exceptions.py

class SemabridgeError(Exception):
    """Base exception for all Semabridge errors."""
    pass

class PermanentError(SemabridgeError):
    """Error that should not be retried."""
    pass

class TransientError(SemabridgeError):
    """Error that may succeed on retry."""
    pass

class NetworkTransientError(TransientError):
    """Network-related transient error."""
    pass

class RateLimitError(TransientError):
    """API rate limit exceeded."""
    pass

class AuthenticationError(PermanentError):
    """Authentication failed."""
    pass

class ValidationError(PermanentError):
    """Data validation failed."""
    pass
```

### 4.2 Retry Decision Logic

```python
def should_retry(exception: Exception) -> bool:
    """Determine if exception should trigger retry."""
    
    # Never retry permanent errors
    if isinstance(exception, PermanentError):
        return False
    
    # Always retry transient errors
    if isinstance(exception, TransientError):
        return True
    
    # Check for specific error patterns
    error_msg = str(exception).lower()
    
    # Retry on network issues
    if any(keyword in error_msg for keyword in ['timeout', 'connection', 'network']):
        return True
    
    # Retry on rate limits
    if 'rate limit' in error_msg or '429' in error_msg:
        return True
    
    # Don't retry by default
    return False
```

---

## 5. Performance Optimization

### 5.1 Worker Pool Sizing

```python
def calculate_optimal_workers(
    cpu_count: int,
    task_type: str,  # 'cpu_bound' or 'io_bound'
    memory_gb: float,
) -> int:
    """Calculate optimal worker count based on resources."""
    
    if task_type == 'cpu_bound':
        # CPU-bound: Use ProcessPoolExecutor
        # Leave 1 core for OS
        return max(1, cpu_count - 1)
    
    elif task_type == 'io_bound':
        # I/O-bound: Use ThreadPoolExecutor
        # Can use more threads than cores
        return min(cpu_count * 2, 32)
    
    return cpu_count
```

### 5.2 Memory Management

```python
def estimate_memory_per_model(model_size_mb: float) -> float:
    """Estimate memory needed per model processing."""
    
    # Rule of thumb: 3x model size for processing overhead
    return model_size_mb * 3

def adjust_workers_for_memory(
    optimal_workers: int,
    available_memory_gb: float,
    avg_model_size_mb: float,
) -> int:
    """Adjust worker count to fit in available memory."""
    
    memory_per_model = estimate_memory_per_model(avg_model_size_mb)
    max_workers_by_memory = int(
        (available_memory_gb * 1024) / memory_per_model
    )
    
    return min(optimal_workers, max_workers_by_memory)
```

---

## 6. Testing Strategy

### 6.1 Unit Tests

```python
# tests/core/test_execution_engine.py

def test_worker_count_auto_detection():
    config = ExecutionConfig(max_workers=None)
    engine = ExecutionEngine(config)
    
    cpu_count = os.cpu_count() or 1
    expected = max(1, cpu_count - 1)
    
    assert engine.max_workers == expected

def test_worker_count_override():
    config = ExecutionConfig(max_workers=4)
    engine = ExecutionEngine(config)
    
    assert engine.max_workers == 4

def test_retry_logic_transient_error():
    # Mock transient error
    with patch('connector.extract') as mock_extract:
        mock_extract.side_effect = [
            NetworkTransientError("Timeout"),
            NetworkTransientError("Timeout"),
            {"data": "success"},  # Third attempt succeeds
        ]
        
        result = engine._execute_single_model_task(...)
        
        assert result.status == "success"
        assert result.retry_count == 2

def test_retry_logic_permanent_error():
    # Mock permanent error
    with patch('connector.extract') as mock_extract:
        mock_extract.side_effect = AuthenticationError("Invalid token")
        
        result = engine._execute_single_model_task(...)
        
        assert result.status == "failed"
        assert result.retry_count == 0  # No retries
```

### 6.2 Integration Tests

```python
def test_parallel_execution_multiple_models():
    models = ["model_a", "model_b", "model_c"]
    
    results = engine.run_batch(
        model_list=models,
        source_connector=mock_source,
        target_connectors=[mock_target],
    )
    
    assert len(results) == 3
    assert all(r.status == "success" for r in results)

def test_failure_strategy_best_effort():
    # One model fails, others continue
    config = ExecutionConfig(failure_strategy="best_effort")
    engine = ExecutionEngine(config)
    
    # Mock: model_b fails
    results = engine.run_batch(...)
    
    assert len(results) == 3
    assert results[1].status == "failed"
    assert results[0].status == "success"
    assert results[2].status == "success"

def test_failure_strategy_strict():
    # One model fails, abort all
    config = ExecutionConfig(failure_strategy="strict")
    engine = ExecutionEngine(config)
    
    # Mock: model_b fails
    results = engine.run_batch(...)
    
    # Should stop after first failure
    assert len(results) <= 2
```

---

## 7. Monitoring and Observability

### 7.1 Metrics Collection

```python
@dataclass
class ExecutionMetrics:
    """Metrics for execution monitoring."""
    
    total_models: int
    successful_models: int
    failed_models: int
    total_duration_seconds: float
    average_model_duration_seconds: float
    worker_utilization_percent: float
    retry_count_total: int
    
    def to_dict(self) -> dict:
        return {
            "total_models": self.total_models,
            "successful_models": self.successful_models,
            "failed_models": self.failed_models,
            "success_rate": self.successful_models / self.total_models * 100,
            "total_duration_seconds": self.total_duration_seconds,
            "average_model_duration_seconds": self.average_model_duration_seconds,
            "worker_utilization_percent": self.worker_utilization_percent,
            "retry_count_total": self.retry_count_total,
        }
```

### 7.2 Logging Strategy

```python
# Structured logging for parallel execution
logger.info(
    "Task started",
    extra={
        "worker_id": worker_id,
        "model_name": model_name,
        "task_id": task_id,
        "timestamp": datetime.utcnow().isoformat(),
    }
)

logger.info(
    "Task completed",
    extra={
        "worker_id": worker_id,
        "model_name": model_name,
        "task_id": task_id,
        "duration_seconds": duration,
        "status": "success",
        "timestamp": datetime.utcnow().isoformat(),
    }
)
```

---

## 8. Future Enhancements

1. **Work Stealing Scheduler**: Implement dynamic load balancing
2. **Distributed Execution**: Support execution across multiple machines
3. **Priority Queues**: Allow prioritization of critical models
4. **Adaptive Retry**: Adjust retry strategy based on error patterns
5. **Resource Monitoring**: Track CPU, memory, and network usage
6. **Checkpoint/Resume**: Support resuming failed batch executions
