"""
Run Summary model for CLI Execution Engine.

Provides structured run status and metadata output following Step 10 of the execution flow.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RunStatus(str, Enum):
    """Final run status values."""
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"
    RUNNING = "RUNNING"


class StepStatus(str, Enum):
    """Individual step status."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class ErrorDetail(BaseModel):
    """Detailed error information."""
    step_number: int
    step_name: str
    error_type: str
    message: str
    traceback: Optional[str] = None


class StepSummary(BaseModel):
    """Summary of an individual execution step."""
    step_number: int
    step_name: str
    status: StepStatus
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_ms: Optional[int] = None
    message: Optional[str] = None
    artifact_ids: List[str] = Field(default_factory=list)


class RunSummary(BaseModel):
    """
    Final run status and metadata (Step 10: Finalize Run).
    
    This is the structured output produced at the end of every CLI execution.
    """
    
    project_id: str
    run_id: str
    status: RunStatus
    started_at: str
    completed_at: Optional[str] = None
    duration_ms: Optional[int] = None
    
    # Connector configuration
    source_type: str
    target_type: Optional[str] = None
    
    # Per-step tracking
    steps_completed: List[StepSummary] = Field(default_factory=list)
    last_successful_step: int = 0
    total_steps: int = 10
    
    # Artifact references
    source_artifact_id: Optional[str] = None
    sml_snapshot_id: Optional[str] = None
    target_artifact_path: Optional[str] = None
    
    # Error details for FAILED/PARTIAL
    errors: List[ErrorDetail] = Field(default_factory=list)
    
    def add_step(
        self,
        step_number: int,
        step_name: str,
        status: StepStatus,
        message: Optional[str] = None,
        duration_ms: Optional[int] = None,
        artifact_ids: Optional[List[str]] = None,
    ) -> None:
        """Add a step result to the summary."""
        step = StepSummary(
            step_number=step_number,
            step_name=step_name,
            status=status,
            completed_at=datetime.utcnow().isoformat(),
            duration_ms=duration_ms,
            message=message,
            artifact_ids=artifact_ids or [],
        )
        self.steps_completed.append(step)
        
        if status == StepStatus.SUCCESS:
            self.last_successful_step = max(self.last_successful_step, step_number)
    
    def add_error(
        self,
        step_number: int,
        step_name: str,
        error: Exception,
        include_traceback: bool = False,
    ) -> None:
        """Add an error to the summary."""
        import traceback as tb
        
        error_detail = ErrorDetail(
            step_number=step_number,
            step_name=step_name,
            error_type=type(error).__name__,
            message=str(error),
            traceback=tb.format_exc() if include_traceback else None,
        )
        self.errors.append(error_detail)
    
    def finalize(self) -> "RunSummary":
        """Finalize the run summary with completion timestamp and duration."""
        self.completed_at = datetime.utcnow().isoformat()
        
        if self.started_at and self.completed_at:
            start = datetime.fromisoformat(self.started_at)
            end = datetime.fromisoformat(self.completed_at)
            self.duration_ms = int((end - start).total_seconds() * 1000)
        
        return self
    
    def to_cli_output(self) -> str:
        """Format for CLI display using Rich-compatible markup."""
        lines = []
        
        # Header
        status_color = {
            RunStatus.SUCCESS: "green",
            RunStatus.FAILED: "red",
            RunStatus.PARTIAL: "yellow",
            RunStatus.RUNNING: "cyan",
        }.get(self.status, "white")
        
        lines.append(f"\n[bold {status_color}]Run {self.status.value}[/bold {status_color}]")
        lines.append(f"  Project ID: {self.project_id}")
        lines.append(f"  Run ID: {self.run_id}")
        lines.append(f"  Duration: {self.duration_ms or 0}ms")
        lines.append(f"  Steps: {self.last_successful_step}/{self.total_steps} completed")
        
        # Step summary
        lines.append("\n[bold]Steps:[/bold]")
        for step in self.steps_completed:
            icon = {
                StepStatus.SUCCESS: "[green]✓[/green]",
                StepStatus.FAILED: "[red]✗[/red]",
                StepStatus.SKIPPED: "[dim]○[/dim]",
                StepStatus.RUNNING: "[cyan]→[/cyan]",
                StepStatus.PENDING: "[dim]·[/dim]",
            }.get(step.status, "?")
            
            msg = f"  {icon} Step {step.step_number}: {step.step_name}"
            if step.message:
                msg += f" - {step.message}"
            lines.append(msg)
        
        # Errors
        if self.errors:
            lines.append("\n[bold red]Errors:[/bold red]")
            for err in self.errors:
                lines.append(f"  [red]Step {err.step_number}[/red]: {err.error_type}: {err.message}")
        
        # Artifacts
        lines.append("\n[bold]Artifacts:[/bold]")
        if self.source_artifact_id:
            lines.append(f"  Source: {self.source_artifact_id}")
        if self.sml_snapshot_id:
            lines.append(f"  SML Snapshot: {self.sml_snapshot_id}")
        if self.target_artifact_path:
            lines.append(f"  Target: {self.target_artifact_path}")
        
        return "\n".join(lines)
    
    def to_json(self) -> Dict[str, Any]:
        """Export as JSON-serializable dict."""
        return self.model_dump(mode='json')


# Step name constants for consistent referencing
STEP_NAMES = {
    1: "Load & Validate Configuration",
    2: "Initialize Identifiers",
    3: "Resolve Authentication",
    4: "Extract from Source",
    5: "Validate & Parse Source Format",
    6: "Convert to Canonical SML",
    7: "Persist Artifacts",
    8: "Convert to Target Format",
    9: "Deploy to Target",
    10: "Finalize Run",
}


def create_run_summary(
    project_id: str,
    run_id: str,
    source_type: str,
    target_type: Optional[str] = None,
) -> RunSummary:
    """Factory function to create a new RunSummary."""
    return RunSummary(
        project_id=project_id,
        run_id=run_id,
        status=RunStatus.RUNNING,
        started_at=datetime.utcnow().isoformat(),
        source_type=source_type,
        target_type=target_type,
    )
