"""
Real-time progress reporter with Rich live dashboard.

Displays overall progress, worker status, and success/failure
counts during parallel model processing.
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, List, Optional

from semabridge.core.concurrency.models import ProcessingStage, WorkerStatus

logger = logging.getLogger(__name__)


class ProgressReporter:
    """Real-time progress dashboard for parallel model processing.

    Uses the ``rich`` library to provide a live, flicker-free terminal
    dashboard showing overall progress, per-worker status, and
    success/failure counts.

    Args:
        total_models: Total number of models to process.
        show_dashboard: Whether to display the Rich dashboard
            (disable for testing / non-interactive).
    """

    def __init__(
        self,
        total_models: int,
        show_dashboard: bool = True,
    ) -> None:
        self._total_models = max(total_models, 1)
        self._show_dashboard = show_dashboard

        # Thread-safe counters
        self._lock = threading.Lock()
        self._success_count = 0
        self._failure_count = 0
        self._in_progress_count = 0
        self._workers: Dict[str, WorkerStatus] = {}
        self._failed_models: List[tuple[str, str]] = []  # (model, error)
        self._successful_models: List[tuple[str, float]] = []  # (model, duration)

        # Rich components (lazy-initialized)
        self._live = None
        self._progress = None
        self._task_id = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def success_count(self) -> int:
        with self._lock:
            return self._success_count

    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._failure_count

    @property
    def progress_percent(self) -> float:
        with self._lock:
            completed = self._success_count + self._failure_count
            return (completed / self._total_models) * 100

    @property
    def total_models(self) -> int:
        return self._total_models

    def start(self) -> None:
        """Start the progress dashboard."""
        if not self._show_dashboard:
            logger.info("Progress dashboard disabled — logging only")
            return

        try:
            from rich.console import Console
            from rich.live import Live
            from rich.panel import Panel
            from rich.progress import (
                BarColumn,
                Progress,
                SpinnerColumn,
                TextColumn,
                TimeElapsedColumn,
            )
            from rich.table import Table

            self._console = Console()
            self._progress = Progress(
                SpinnerColumn(),
                TextColumn("[bold blue]{task.description}"),
                BarColumn(bar_width=40),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TextColumn("({task.completed}/{task.total})"),
                TimeElapsedColumn(),
            )
            self._task_id = self._progress.add_task(
                "Syncing models…",
                total=self._total_models,
            )
            self._live = Live(
                self._build_layout(),
                console=self._console,
                refresh_per_second=4,
            )
            self._live.start()
            logger.info("Progress dashboard started for %d models", self._total_models)
        except ImportError:
            logger.warning("rich library not available — dashboard disabled")
            self._show_dashboard = False

    def update_worker_status(
        self,
        worker_id: str,
        model_name: Optional[str] = None,
        stage: ProcessingStage = ProcessingStage.IDLE,
    ) -> None:
        """Update the status of a specific worker.

        Args:
            worker_id: Unique worker identifier.
            model_name: Model currently being processed.
            stage: Current processing stage.
        """
        with self._lock:
            if model_name and stage not in (
                ProcessingStage.COMPLETED,
                ProcessingStage.FAILED,
                ProcessingStage.IDLE,
            ):
                self._workers[worker_id] = WorkerStatus(
                    worker_id=worker_id,
                    model_name=model_name,
                    stage=stage,
                )
            else:
                self._workers.pop(worker_id, None)

        self._refresh()

    def report_success(self, model_name: str, duration: float) -> None:
        """Report that a model completed successfully.

        Args:
            model_name: Name of the completed model.
            duration: Processing duration in seconds.
        """
        with self._lock:
            self._success_count += 1
            self._successful_models.append((model_name, duration))

        if self._progress and self._task_id is not None:
            self._progress.update(self._task_id, advance=1)

        self._refresh()
        logger.info("✓ %s completed in %.1fs", model_name, duration)

    def report_failure(self, model_name: str, error: str) -> None:
        """Report that a model failed.

        Args:
            model_name: Name of the failed model.
            error: Error message.
        """
        with self._lock:
            self._failure_count += 1
            self._failed_models.append((model_name, error))

        if self._progress and self._task_id is not None:
            self._progress.update(self._task_id, advance=1)

        self._refresh()
        logger.error("✗ %s failed: %s", model_name, error)

    def stop(self) -> None:
        """Stop the dashboard and show final summary."""
        if self._live:
            try:
                self._live.stop()
            except Exception as exc:
                logger.debug("Error stopping live dashboard: %s", exc)

        self._print_summary()

    # ------------------------------------------------------------------
    # Layout building
    # ------------------------------------------------------------------

    def _build_layout(self):
        """Build the Rich layout for the dashboard."""
        try:
            from rich.panel import Panel
            from rich.table import Table
            from rich.text import Text

            # Main panel
            grid = Table.grid(padding=(0, 1))
            grid.add_column()

            # Progress bar
            if self._progress:
                grid.add_row(self._progress)

            # Worker status table
            worker_table = Table(
                title="Active Workers",
                show_header=True,
                show_lines=False,
                expand=True,
            )
            worker_table.add_column("Worker", style="cyan", width=12)
            worker_table.add_column("Model", style="bold")
            worker_table.add_column("Stage", style="yellow")

            with self._lock:
                for wid, ws in self._workers.items():
                    worker_table.add_row(
                        wid,
                        ws.model_name or "—",
                        ws.stage.value,
                    )

                # Status counts
                pending = (
                    self._total_models
                    - self._success_count
                    - self._failure_count
                    - len(self._workers)
                )
                status_text = Text()
                status_text.append(f"  ✓ Completed: {self._success_count}\n", style="green")
                status_text.append(f"  ✗ Failed: {self._failure_count}\n", style="red")
                status_text.append(f"  ⧗ In Progress: {len(self._workers)}\n", style="yellow")
                status_text.append(f"  ○ Pending: {max(0, pending)}", style="dim")

            grid.add_row(worker_table)
            grid.add_row(status_text)

            # Failed models
            with self._lock:
                if self._failed_models:
                    fail_table = Table(
                        title="Failed Models",
                        show_header=False,
                        show_lines=False,
                    )
                    fail_table.add_column("Model", style="red")
                    fail_table.add_column("Error")
                    for model, err in self._failed_models[-5:]:
                        fail_table.add_row(f"• {model}", err[:80])
                    grid.add_row(fail_table)

            return Panel(
                grid,
                title="[bold]Concurrent Model Synchronization[/bold]",
                border_style="blue",
            )
        except ImportError:
            return ""

    def _refresh(self) -> None:
        """Refresh the live dashboard."""
        if self._live:
            try:
                self._live.update(self._build_layout())
            except Exception as exc:
                logger.debug("Error refreshing live dashboard: %s", exc)

    def _print_summary(self) -> None:
        """Print the final summary after processing completes."""
        try:
            from rich.console import Console
            from rich.panel import Panel
            from rich.table import Table

            console = Console()

            summary = Table(show_header=False, show_lines=False, padding=(0, 2))
            summary.add_column(style="bold")
            summary.add_column()

            with self._lock:
                summary.add_row("Total Models", str(self._total_models))
                summary.add_row("Successful", f"[green]{self._success_count}[/green]")
                summary.add_row("Failed", f"[red]{self._failure_count}[/red]")

                if self._failed_models:
                    summary.add_row("", "")
                    summary.add_row("[bold red]Failed Models:[/bold red]", "")
                    for model, err in self._failed_models:
                        summary.add_row(f"  • {model}", err[:100])

            console.print(
                Panel(summary, title="Sync Summary", border_style="blue")
            )
        except ImportError:
            # Fallback to plain logging
            with self._lock:
                logger.info(
                    "Summary: %d/%d succeeded, %d failed",
                    self._success_count,
                    self._total_models,
                    self._failure_count,
                )
                for model, err in self._failed_models:
                    logger.info("  Failed: %s — %s", model, err)
