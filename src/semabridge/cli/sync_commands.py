"""
Sync CLI Commands.

Typer sub-application for bidirectional synchronization operations.

Commands:
    semabridge sync run        — Run a sync job
    semabridge sync status     — Get job status
    semabridge sync jobs       — List sync jobs
    semabridge sync cancel     — Cancel a running job
    semabridge sync resolve    — Resolve conflicts and resume
    semabridge sync mappings   — List model mappings
    semabridge sync history    — Show schema version history
"""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)
console = Console(force_terminal=True, no_color=False)

sync_app = typer.Typer(
    name="sync",
    help="Bidirectional PBIX / Snowflake / Power BI synchronization",
    add_completion=False,
)


@sync_app.command("run")
def sync_run(
    direction: str = typer.Option(
        "pbix_to_snowflake",
        "--direction", "-d",
        help="Sync direction: pbix_to_snowflake | snowflake_to_pbi | bidirectional",
    ),
    pbix_folder: Optional[str] = typer.Option(
        None, "--pbix-folder", "-p",
        help="Folder containing .pbix files",
    ),
    pbix_pattern: str = typer.Option(
        "*.pbix", "--pattern",
        help="Glob pattern for PBIX files",
    ),
    snowflake_schema: Optional[str] = typer.Option(
        None, "--sf-schema",
        help="Target Snowflake schema",
    ),
    workspace_id: Optional[str] = typer.Option(
        None, "--workspace-id", "-w",
        help="Target Fabric workspace ID",
    ),
    conflict_resolution: str = typer.Option(
        "fail_and_approve", "--conflict",
        help="Conflict strategy: fail_and_approve | source_wins | target_wins | merge",
    ),
    max_workers: int = typer.Option(
        5, "--workers",
        help="Number of parallel workers (1 = sequential)",
    ),
    incremental: bool = typer.Option(
        True, "--incremental/--full",
        help="Skip models with unchanged schema",
    ),
    include_data: bool = typer.Option(
        False, "--include-data",
        help="Sync table data (best-effort, small tables only)",
    ),
) -> None:
    """Run a synchronization job."""
    from semabridge.sync.models import (
        ConflictResolution,
        SyncConfig,
        SyncDirection,
    )
    from semabridge.sync.orchestrator import SyncOrchestrator
    from semabridge.sync.repository import SyncRepository

    try:
        sync_direction = SyncDirection(direction)
    except ValueError:
        console.print(
            f"[red]Invalid direction '{direction}'. "
            f"Use: pbix_to_snowflake | snowflake_to_pbi | bidirectional[/red]"
        )
        raise typer.Exit(code=1)

    try:
        strategy = ConflictResolution(conflict_resolution)
    except ValueError:
        console.print(
            f"[red]Invalid conflict strategy '{conflict_resolution}'. "
            f"Use: fail_and_approve | source_wins | target_wins | merge[/red]"
        )
        raise typer.Exit(code=1)

    config = SyncConfig(
        direction=sync_direction,
        conflict_resolution=strategy,
        pbix_folder=pbix_folder,
        pbix_pattern=pbix_pattern,
        snowflake_schema=snowflake_schema,
        fabric_workspace_id=workspace_id,
        max_workers=max_workers,
        enable_parallel=max_workers > 1,
        incremental=incremental,
        include_data=include_data,
    )

    repo = SyncRepository()
    orchestrator = SyncOrchestrator(repository=repo)

    console.print(f"\n[bold cyan]Starting sync: {direction}[/bold cyan]")
    if pbix_folder:
        console.print(f"  PBIX folder: {pbix_folder}")
    if snowflake_schema:
        console.print(f"  Snowflake schema: {snowflake_schema}")
    if workspace_id:
        console.print(f"  Fabric workspace: {workspace_id}")
    console.print(f"  Workers: {max_workers} | Incremental: {incremental}")
    console.print()

    # Record semabridge.yaml version (config drift detection)
    try:
        from semabridge.core.config_loader import get_default_config_path
        yaml_path = get_default_config_path()
        if yaml_path and yaml_path.exists():
            from semabridge.repository.schema_sync import SchemaVersionManager
            version_mgr = SchemaVersionManager()
            changed = version_mgr.sync_model_version(str(yaml_path))
            if changed:
                console.print("[yellow]Config version updated in DB.[/yellow]")
    except Exception as _ver_err:
        logger.warning("Could not record config version: %s", _ver_err)

    job = orchestrator.run(config, initiated_by="cli")

    # Display results
    _print_job_summary(job)


@sync_app.command("status")
def sync_status(
    job_id: str = typer.Argument(..., help="Sync job ID"),
) -> None:
    """Get detailed status of a sync job."""
    from semabridge.sync.orchestrator import SyncOrchestrator
    from semabridge.sync.repository import SyncRepository

    repo = SyncRepository()
    orchestrator = SyncOrchestrator(repository=repo)

    try:
        status = orchestrator.get_status(job_id)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)

    job_data = status["job"]
    summary = status["summary"]

    console.print(f"\n[bold]Job {job_data['job_id']}[/bold]")
    console.print(f"  Direction: {job_data['direction']}")
    console.print(f"  Status: {job_data['status']}")
    console.print(f"  Created: {job_data['created_at']}")
    console.print(
        f"  Items: {summary['completed']}/{summary['total']} completed, "
        f"{summary['failed']} failed, {summary['pending']} pending"
    )
    if summary["unresolved_conflicts"] > 0:
        console.print(
            f"  [yellow]Unresolved conflicts: {summary['unresolved_conflicts']}[/yellow]"
        )

    # Item table
    items = status["items"]
    if items:
        table = Table(title="Items")
        table.add_column("Model", style="cyan")
        table.add_column("Status")
        table.add_column("Duration")
        table.add_column("Error")

        for item in items:
            style = {
                "completed": "green",
                "failed": "red",
                "skipped": "dim",
            }.get(item["status"], "")

            table.add_row(
                item["model_name"],
                f"[{style}]{item['status']}[/{style}]" if style else item["status"],
                f"{item.get('duration_ms', '-')}ms" if item.get("duration_ms") else "-",
                item.get("error_message", "")[:60] or "",
            )
        console.print(table)


@sync_app.command("jobs")
def sync_jobs(
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max results"),
) -> None:
    """List sync jobs."""
    from semabridge.sync.models import SyncJobStatus
    from semabridge.sync.repository import SyncRepository

    repo = SyncRepository()
    filter_status = SyncJobStatus(status) if status else None
    jobs = repo.list_jobs(status=filter_status, limit=limit)

    if not jobs:
        console.print("[dim]No sync jobs found.[/dim]")
        return

    table = Table(title="Sync Jobs")
    table.add_column("Job ID", style="cyan", max_width=12)
    table.add_column("Direction")
    table.add_column("Status")
    table.add_column("Items")
    table.add_column("Created")
    table.add_column("Duration")

    for job in jobs:
        status_style = {
            "completed": "green",
            "failed": "red",
            "conflict": "yellow",
            "running": "blue",
        }.get(job.status.value, "")

        table.add_row(
            job.job_id[:12] + "…",
            job.direction.value,
            f"[{status_style}]{job.status.value}[/{status_style}]" if status_style else job.status.value,
            f"{job.completed_items}/{job.total_items}",
            job.created_at[:19] if job.created_at else "-",
            f"{job.duration_ms}ms" if job.duration_ms else "-",
        )

    console.print(table)


@sync_app.command("cancel")
def sync_cancel(
    job_id: str = typer.Argument(..., help="Sync job ID to cancel"),
) -> None:
    """Cancel a running or paused sync job."""
    from semabridge.sync.orchestrator import SyncOrchestrator
    from semabridge.sync.repository import SyncRepository

    repo = SyncRepository()
    orchestrator = SyncOrchestrator(repository=repo)

    try:
        job = orchestrator.cancel(job_id)
        console.print(f"[green]Job {job_id} cancelled.[/green]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)


@sync_app.command("resolve")
def sync_resolve(
    job_id: str = typer.Argument(..., help="Sync job ID with conflicts"),
    resolution: str = typer.Option(
        "source_wins", "--resolution", "-r",
        help="Resolution strategy: source_wins | target_wins | merge",
    ),
    resolved_by: str = typer.Option("cli_user", "--by", help="Who is resolving"),
) -> None:
    """Resolve all conflicts and resume a paused sync job."""
    from semabridge.sync.models import ConflictResolution
    from semabridge.sync.orchestrator import SyncOrchestrator
    from semabridge.sync.repository import SyncRepository

    try:
        strategy = ConflictResolution(resolution)
    except ValueError:
        console.print(f"[red]Invalid resolution '{resolution}'[/red]")
        raise typer.Exit(code=1)

    repo = SyncRepository()
    orchestrator = SyncOrchestrator(repository=repo)

    console.print(f"Resolving conflicts for job {job_id} with {resolution}…")
    job = orchestrator.resolve_and_resume(job_id, strategy, resolved_by)
    _print_job_summary(job)


@sync_app.command("mappings")
def sync_mappings(
    active_only: bool = typer.Option(True, "--active/--all", help="Show active mappings only"),
) -> None:
    """List model mappings between source and target systems."""
    from semabridge.sync.repository import SyncRepository

    repo = SyncRepository()
    mappings = repo.list_mappings(active_only=active_only)

    if not mappings:
        console.print("[dim]No model mappings found.[/dim]")
        return

    table = Table(title="Model Mappings")
    table.add_column("Model", style="cyan")
    table.add_column("Source")
    table.add_column("Target")
    table.add_column("Last Synced")
    table.add_column("Hash", max_width=12)

    for m in mappings:
        table.add_row(
            m.model_name,
            f"{m.source_type}:{m.source_identifier[:30]}",
            f"{m.target_type}:{m.target_identifier[:30]}",
            (m.last_synced_at or "-")[:19],
            (m.last_osi_hash or "-")[:12],
        )

    console.print(table)


@sync_app.command("history")
def sync_history(
    model_name: str = typer.Argument(..., help="Model name to show history for"),
    limit: int = typer.Option(10, "--limit", "-n", help="Max versions to show"),
) -> None:
    """Show schema version history for a model."""
    from semabridge.sync.schema_evolution import SchemaEvolutionTracker
    from semabridge.sync.repository import SyncRepository

    repo = SyncRepository()
    tracker = SchemaEvolutionTracker(repo)
    versions = tracker.get_history(model_name, limit=limit)

    if not versions:
        console.print(f"[dim]No schema versions found for '{model_name}'.[/dim]")
        return

    table = Table(title=f"Schema History: {model_name}")
    table.add_column("Version", style="cyan")
    table.add_column("Hash", max_width=12)
    table.add_column("Changes")
    table.add_column("Created")
    table.add_column("Job")

    for v in versions:
        table.add_row(
            f"v{v.version_number}",
            v.schema_hash[:12],
            str(len(v.changes_from_previous)),
            v.created_at[:19],
            (v.created_by_job_id or "-")[:12],
        )

    console.print(table)


# -----------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------


def _print_job_summary(job: "SyncJob") -> None:
    """Print a formatted job summary to console."""
    status_color = {
        "completed": "green",
        "failed": "red",
        "conflict": "yellow",
        "cancelled": "dim",
    }.get(job.status.value, "white")

    console.print(f"\n[bold]Sync Job Summary[/bold]")
    console.print(f"  Job ID:    {job.job_id}")
    console.print(f"  Direction: {job.direction.value}")
    console.print(
        f"  Status:    [{status_color}]{job.status.value}[/{status_color}]"
    )
    console.print(f"  Items:     {job.completed_items}/{job.total_items} completed")
    if job.failed_items:
        console.print(f"  Failed:    [red]{job.failed_items}[/red]")
    if job.duration_ms:
        console.print(f"  Duration:  {job.duration_ms}ms")
    if job.error_message:
        console.print(f"  Error:     [red]{job.error_message}[/red]")
    console.print()
