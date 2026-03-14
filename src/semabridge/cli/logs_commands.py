"""
Logs Commands.

CLI commands for viewing command execution history and logs.
"""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)
console = Console(force_terminal=True, no_color=False)

# Create sub-app for logs commands
logs_app = typer.Typer(
    name="logs",
    help="View command execution history and logs",
    add_completion=False,
)


@logs_app.command("list")
def logs_list(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of log entries to show"),
    command: Optional[str] = typer.Option(None, "--command", "-c", help="Filter by command type"),
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status: success, failed"),
):
    """
    List recent command executions.
    
    Shows command history with status, duration, and timestamps.
    
    Examples:
        semabridge logs list
        semabridge logs list --command sync --status failed
    """
    from semabridge.repository.command_logger import (
        get_command_logger, CommandType, CommandStatus
    )
    
    console.print(Panel.fit(
        "[bold]Command History[/bold]",
        title="Logs",
    ))
    
    cmd_logger = get_command_logger()
    
    # Parse filters
    command_filter = None
    if command:
        # Map intuitive names to internal CommandType
        cmd_mapped = command.lower().replace("-", "_")
        if cmd_mapped in ["sync", "semantic_sync", "reverse_sync"]:
            command_filter = CommandType.DEPLOY
        else:
            try:
                command_filter = CommandType(command)
            except ValueError:
                console.print(f"[yellow]Unknown command type: {command}[/yellow]")
    
    status_filter = None
    if status:
        try:
            status_filter = CommandStatus(status)
        except ValueError:
            console.print(f"[yellow]Unknown status: {status}[/yellow]")
    
    entries = cmd_logger.get_recent_logs(
        limit=limit,
        command_filter=command_filter,
        status_filter=status_filter,
    )
    
    if not entries:
        console.print("[yellow]No log entries found.[/yellow]")
        return
    
    table = Table(title=f"Recent Commands ({len(entries)})")
    table.add_column("#", style="dim", justify="right")
    table.add_column("Command", style="cyan")
    table.add_column("Status", style="bold")
    table.add_column("Adapter", style="blue")
    table.add_column("Duration", justify="right")
    table.add_column("Timestamp", style="white")
    table.add_column("Project", style="green")
    
    for i, entry in enumerate(entries, 1):
        status_display = {
            "success": "[green]success[/green]",
            "failed": "[red]failed[/red]",
            "started": "[yellow]started[/yellow]",
            "in_progress": "[cyan]running[/cyan]",
        }.get(entry.status.value, entry.status.value)
        
        duration = f"{entry.duration_ms}ms" if entry.duration_ms else "-"
        
        table.add_row(
            str(i),
            entry.command.value,
            status_display,
            entry.adapter or "-",
            duration,
            entry.started_at[:19] if entry.started_at else "-",
            (entry.project_id[:15] + "...") if entry.project_id and len(entry.project_id) > 15 else (entry.project_id or "-"),
        )
    
    console.print(table)


@logs_app.command("stats")
def logs_stats(
    days: int = typer.Option(30, "--days", "-d", help="Statistics for last N days"),
):
    """
    Show command execution statistics.
    
    Displays success rates and average durations by command type.
    """
    from semabridge.repository.command_logger import get_command_logger
    
    console.print(Panel.fit(
        f"[bold]Command Statistics[/bold]\n"
        f"Period: Last {days} days",
        title="Stats",
    ))
    
    cmd_logger = get_command_logger()
    stats = cmd_logger.get_command_stats(days)
    
    if not stats["commands"]:
        console.print("[yellow]No commands executed in this period.[/yellow]")
        return
    
    table = Table(title=f"Command Statistics ({days} days)")
    table.add_column("Command", style="cyan")
    table.add_column("Total", style="bold", justify="right")
    table.add_column("Success", style="green", justify="right")
    table.add_column("Failed", style="red", justify="right")
    table.add_column("Success Rate", justify="right")
    table.add_column("Avg Duration", justify="right")
    
    for cmd in stats["commands"]:
        rate_color = "green" if cmd["success_rate"] >= 90 else "yellow" if cmd["success_rate"] >= 70 else "red"
        
        table.add_row(
            cmd["command"],
            str(cmd["total"]),
            str(cmd["success"]),
            str(cmd["failed"]),
            f"[{rate_color}]{cmd['success_rate']}%[/{rate_color}]",
            f"{cmd['avg_duration_ms']}ms" if cmd["avg_duration_ms"] else "-",
        )
    
    console.print(table)


@logs_app.command("show")
def logs_show(
    log_id: str = typer.Argument(..., help="Log entry ID to view"),
):
    """
    Show details of a specific log entry.
    """
    import json
    from semabridge.repository.command_logger import get_command_logger
    
    cmd_logger = get_command_logger()
    
    # Find the log entry
    entries = cmd_logger.get_recent_logs(limit=1000)  # Search through recent
    entry = None
    for e in entries:
        if e.log_id.startswith(log_id):
            entry = e
            break
    
    if not entry:
        console.print(f"[red]Log entry not found: {log_id}[/red]")
        raise typer.Exit(code=1)
    
    # Display details
    console.print(Panel.fit(
        f"[bold]Log Entry Details[/bold]",
        title="Log",
    ))
    
    status_color = {"success": "green", "failed": "red"}.get(entry.status.value, "yellow")
    
    console.print(f"[bold]ID:[/bold] {entry.log_id}")
    console.print(f"[bold]Command:[/bold] {entry.command.value}")
    console.print(f"[bold]Action Type:[/bold] {entry.action_type.value}")
    console.print(f"[bold]Status:[/bold] [{status_color}]{entry.status.value}[/{status_color}]")
    console.print(f"[bold]Started:[/bold] {entry.started_at}")
    console.print(f"[bold]Completed:[/bold] {entry.completed_at or '-'}")
    console.print(f"[bold]Duration:[/bold] {entry.duration_ms}ms" if entry.duration_ms else "[bold]Duration:[/bold] -")
    console.print(f"[bold]Adapter:[/bold] {entry.adapter or '-'}")
    console.print(f"[bold]Project ID:[/bold] {entry.project_id or '-'}")
    console.print(f"[bold]Initiated By:[/bold] {entry.initiated_by}")
    
    if entry.error_message:
        console.print(f"\n[red bold]Error:[/red bold]\n{entry.error_message}")
    
    if entry.details:
        console.print(f"\n[bold]Details:[/bold]")
        console.print(json.dumps(entry.details, indent=2))
