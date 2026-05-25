"""
Semantic CLI Commands.

Additional CLI commands for:
- Snapshot management
- Semantic comparison
- Enterprise logging
- Rollback operations via Semantic API

These commands can be imported and added to the main Typer app.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)
console = Console(force_terminal=True, no_color=False)

# Create sub-app for semantic commands
semantic_app = typer.Typer(
    name="semantic",
    help="Semantic API commands for version control, comparison, and rollback",
    add_completion=False,
)


# ============ Snapshot Commands ============

@semantic_app.command("snapshot-create")
def snapshot_create(
    adapter: str = typer.Option("snowflake", "--adapter", "-a", help="Adapter name (snowflake, fabric)"),
    description: str = typer.Option("", "--description", "-d", help="Description for this snapshot"),
    project_id: Optional[str] = typer.Option(None, "--project-id", "-p", help="Project ID (dataset ID)"),
):
    """Create a manual snapshot of current semantic state."""
    from semabridge.repository.semantic_snapshot_manager import SemanticSnapshotManager
    from semabridge.repository.model_repository import ModelRepository
    
    console.print(Panel.fit(
        f"[bold]Create Snapshot[/bold]\n"
        f"Adapter: {adapter}",
        title="Snapshot",
    ))
    
    try:
        # Get current state from the repository
        db_manager = ModelRepository()
        snapshot_manager = SemanticSnapshotManager()
        
        if project_id:
            head = db_manager.get_head(project_id)
            if head:
                semantic_state = head.sml_blob
            else:
                console.print(f"[yellow]No existing state for project {project_id}[/yellow]")
                semantic_state = {}
        else:
            # Use empty state for manual snapshot
            semantic_state = {}
            console.print("[yellow]No project ID specified, creating empty snapshot[/yellow]")
        
        snapshot = snapshot_manager.create_snapshot(
            adapter=adapter,
            semantic_state=semantic_state,
            change_description=description or "Manual snapshot",
        )
        
        console.print(f"\n[green][OK] Snapshot created successfully[/green]")
        console.print(f"  Snapshot ID: {snapshot.snapshot_id}")
        console.print(f"  Measures: {snapshot.measure_count}")
        console.print(f"  Dimensions: {snapshot.dimension_count}")
        console.print(f"  Hash: {snapshot.schema_hash[:16]}...")
        
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)


@semantic_app.command("snapshot-list")
def snapshot_list(
    adapter: str = typer.Option("snowflake", "--adapter", "-a", help="Adapter name"),
    days: int = typer.Option(30, "--days", "-d", help="Show snapshots from last N days"),
    limit: int = typer.Option(20, "--limit", "-l", help="Maximum snapshots to show"),
):
    """List snapshots for an adapter."""
    from semabridge.repository.semantic_snapshot_manager import SemanticSnapshotManager
    
    console.print(Panel.fit(
        f"[bold]Snapshot List[/bold]\n"
        f"Adapter: {adapter}",
        title="Snapshots",
    ))
    
    try:
        snapshot_manager = SemanticSnapshotManager()
        snapshots = snapshot_manager.list_snapshots(adapter, limit=limit)
        
        if not snapshots:
            console.print("[yellow]No snapshots found for this adapter.[/yellow]")
            return
        
        table = Table(title=f"Snapshots ({len(snapshots)})")
        table.add_column("#", style="dim", justify="right")
        table.add_column("Snapshot ID", style="cyan")
        table.add_column("Timestamp", style="white")
        table.add_column("Measures", style="magenta", justify="right")
        table.add_column("Dimensions", style="blue", justify="right")
        table.add_column("Description", style="green")
        
        for i, s in enumerate(snapshots, 1):
            table.add_row(
                str(i),
                s.snapshot_id[:30] + "..." if len(s.snapshot_id) > 30 else s.snapshot_id,
                s.timestamp[:19],
                str(s.measure_count),
                str(s.dimension_count),
                (s.change_description[:30] + "...") if len(s.change_description) > 30 else s.change_description,
            )
        
        console.print(table)
        
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)


# ============ Compare Commands ============

@semantic_app.command("compare")
def compare(
    version1: str = typer.Argument(..., help="First version/snapshot ID"),
    version2: str = typer.Argument(..., help="Second version/snapshot ID"),
    format: str = typer.Option("cli", "--format", "-f", help="Output format: cli, json, html"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file for report"),
):
    """Compare two snapshots and show differences."""
    from semabridge.repository.semantic_snapshot_manager import SemanticSnapshotManager
    from semabridge.repository.semantic_diff_engine import SemanticDiffEngine
    
    console.print(Panel.fit(
        f"[bold]Semantic Comparison[/bold]\n"
        f"From: {version1}\n"
        f"To: {version2}",
        title="Compare",
    ))
    
    try:
        snapshot_manager = SemanticSnapshotManager()
        diff_engine = SemanticDiffEngine(snapshot_manager)
        
        start_time = time.time()
        diff = diff_engine.compare_snapshots(version1, version2)
        
        report = diff_engine.export_diff_as_report(diff, format)
        
        if output:
            with open(output, "w", encoding="utf-8") as f:
                f.write(report)
            console.print(f"\n[green][OK] Report saved to {output}[/green]")
        else:
            console.print()
            console.print(report)
        
        console.print(f"\n[dim]Comparison took {int((time.time() - start_time) * 1000)}ms[/dim]")
        
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)


@semantic_app.command("compare-current")
def compare_current(
    adapter: str = typer.Option("snowflake", "--adapter", "-a", help="Adapter name"),
    project_id: str = typer.Option(..., "--project-id", "-p", help="Project ID to compare"),
    format: str = typer.Option("cli", "--format", "-f", help="Output format"),
):
    """Compare current state vs last snapshot."""
    from semabridge.repository.semantic_snapshot_manager import SemanticSnapshotManager
    from semabridge.repository.semantic_diff_engine import SemanticDiffEngine
    from semabridge.repository.model_repository import ModelRepository
    
    console.print(Panel.fit(
        f"[bold]Compare Current State[/bold]\n"
        f"Adapter: {adapter}\n"
        f"Project: {project_id}",
        title="Compare Current",
    ))
    
    try:
        db_manager = ModelRepository()
        snapshot_manager = SemanticSnapshotManager()
        diff_engine = SemanticDiffEngine(snapshot_manager)
        
        # Get current state from DuckDB
        head = db_manager.get_head(project_id)
        if not head:
            console.print("[yellow]No snapshots found for this project.[/yellow]")
            return
        
        current_state = head.sml_blob
        
        # Get previous snapshot
        snapshots = snapshot_manager.list_snapshots(adapter, limit=2)
        if len(snapshots) < 2:
            console.print("[yellow]Need at least 2 snapshots to compare.[/yellow]")
            return
        
        latest = snapshots[0]
        previous = snapshots[1]
        
        diff = diff_engine.compare_snapshots(previous.snapshot_id, latest.snapshot_id)
        report = diff_engine.export_diff_as_report(diff, format)
        
        console.print()
        console.print(report)
        
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)


# ============ Version Commands ============

@semantic_app.command("version-history")
def version_history(
    adapter: str = typer.Option("snowflake", "--adapter", "-a", help="Adapter name"),
    limit: int = typer.Option(10, "--limit", "-l", help="Number of versions to show"),
    include_superseded: bool = typer.Option(False, "--all", help="Include superseded versions"),
):
    """View version history for an adapter."""
    from semabridge.repository.semantic_version_manager import SemanticVersionManager
    
    console.print(Panel.fit(
        f"[bold]Version History[/bold]\n"
        f"Adapter: {adapter}",
        title="Versions",
    ))
    
    try:
        version_manager = SemanticVersionManager()
        versions = version_manager.get_version_history(adapter, limit, include_superseded)
        
        if not versions:
            console.print("[yellow]No versions found for this adapter.[/yellow]")
            return
        
        current = version_manager.get_current_version(adapter)
        current_id = current.version_id if current else None
        
        table = Table(title=f"Version History ({len(versions)})")
        table.add_column("#", style="dim", justify="right")
        table.add_column("Version ID", style="cyan")
        table.add_column("Type", style="blue")
        table.add_column("Status", style="yellow")
        table.add_column("Timestamp", style="white")
        table.add_column("Summary", style="green")
        
        for i, v in enumerate(versions, 1):
            is_current = v.version_id == current_id
            status = "[bold green]CURRENT[/]" if is_current else v.status
            
            table.add_row(
                str(i),
                v.version_id[:30] + "..." if len(v.version_id) > 30 else v.version_id,
                v.change_type,
                status,
                v.timestamp[:19],
                (v.change_summary[:30] + "...") if len(v.change_summary) > 30 else v.change_summary,
            )
        
        console.print(table)
        
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)


# ============ Rollback Commands ============

@semantic_app.command("rollback-preview")
def rollback_preview(
    adapter: str = typer.Option("snowflake", "--adapter", "-a", help="Adapter name"),
    to_version: str = typer.Option(..., "--to-version", "-v", help="Target version ID"),
):
    """Preview what rollback will change (dry-run)."""
    from semabridge.repository.rollback_orchestrator import RollbackOrchestrator
    
    console.print(Panel.fit(
        f"[bold]Rollback Preview[/bold]\n"
        f"Adapter: {adapter}\n"
        f"Target: {to_version}",
        title="Preview",
    ))
    
    try:
        orchestrator = RollbackOrchestrator()
        validation = orchestrator.validate_rollback_feasibility(adapter, to_version)
        
        if not validation["feasible"]:
            console.print(f"[red]Rollback not feasible: {validation['reason']}[/red]")
            raise typer.Exit(code=1)
        
        console.print("\n[green][OK] Rollback is feasible[/green]")
        
        # Show preview
        preview = orchestrator.preview_rollback(adapter, to_version)
        
        if preview.get("summary"):
            summary = preview["summary"]
            console.print(f"\n[bold]Expected Changes:[/bold]")
            console.print(f"  Total changes: {summary.get('total_changes', 0)}")
            console.print(f"  Measures affected: {summary.get('measures_changed', 0)}")
            console.print(f"  Dimensions affected: {summary.get('dimensions_changed', 0)}")
            
            if summary.get("has_breaking_changes"):
                console.print("\n[yellow]⚠ WARNING: Breaking changes detected![/yellow]")
                for bc in preview.get("breaking_changes", []):
                    console.print(f"  - {bc['entity_type']}: {bc['entity_name']}")
        
        console.print("\n[dim]Use 'semantic rollback' to execute the rollback.[/dim]")
        
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)


@semantic_app.command("rollback-history")
def rollback_history(
    adapter: str = typer.Option("snowflake", "--adapter", "-a", help="Adapter name"),
    limit: int = typer.Option(10, "--limit", "-l", help="Number of rollbacks to show"),
):
    """Show rollback history for an adapter."""
    from semabridge.repository.rollback_orchestrator import RollbackOrchestrator
    
    console.print(Panel.fit(
        f"[bold]Rollback History[/bold]\n"
        f"Adapter: {adapter}",
        title="Rollback History",
    ))
    
    try:
        orchestrator = RollbackOrchestrator()
        history = orchestrator.get_rollback_history(adapter, limit)
        
        if not history:
            console.print("[yellow]No rollbacks found for this adapter.[/yellow]")
            return
        
        table = Table(title=f"Rollback History ({len(history)})")
        table.add_column("#", style="dim", justify="right")
        table.add_column("Version ID", style="cyan")
        table.add_column("From", style="red")
        table.add_column("To", style="green")
        table.add_column("Timestamp", style="white")
        table.add_column("Reason", style="yellow")
        
        for i, rb in enumerate(history, 1):
            table.add_row(
                str(i),
                rb["version_id"][:20] + "..." if len(rb["version_id"]) > 20 else rb["version_id"],
                (rb["from_version"][:12] + "...") if rb["from_version"] and len(rb["from_version"]) > 12 else (rb["from_version"] or "-"),
                (rb["to_version"][:12] + "...") if rb["to_version"] and len(rb["to_version"]) > 12 else (rb["to_version"] or "-"),
                rb["timestamp"][:19],
                (rb["reason"][:20] + "...") if rb["reason"] and len(rb["reason"]) > 20 else (rb["reason"] or "-"),
            )
        
        console.print(table)
        
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)


# ============ Logs Commands ============

@semantic_app.command("logs-show")
def logs_show(
    log_type: str = typer.Argument("operations", help="Log type: operations, errors, audit"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show"),
    search: Optional[str] = typer.Option(None, "--search", "-s", help="Search pattern"),
):
    """Show log entries."""
    from semabridge.utils.enterprise_logger import get_enterprise_logger
    
    console.print(Panel.fit(
        f"[bold]Log Viewer[/bold]\n"
        f"Type: {log_type}\n"
        f"Lines: {lines}",
        title="Logs",
    ))
    
    try:
        logger = get_enterprise_logger()
        log_lines = logger.read_log(log_type, lines, search)
        
        if not log_lines:
            console.print(f"[yellow]No log entries found for '{log_type}'[/yellow]")
            return
        
        console.print(f"\n[dim]Showing {len(log_lines)} lines from {log_type} log:[/dim]\n")
        
        for line in log_lines:
            # Color-code by level
            if "[ERROR]" in line or "[CRITICAL]" in line:
                console.print(f"[red]{line.strip()}[/red]")
            elif "[WARNING]" in line:
                console.print(f"[yellow]{line.strip()}[/yellow]")
            elif "[DEBUG]" in line:
                console.print(f"[dim]{line.strip()}[/dim]")
            else:
                console.print(line.strip())
        
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)


@semantic_app.command("logs-search")
def logs_search(
    pattern: str = typer.Argument(..., help="Search pattern"),
    log_type: str = typer.Option("operations", "--type", "-t", help="Log type to search"),
    lines: int = typer.Option(100, "--lines", "-n", help="Maximum lines to search"),
):
    """Search logs for a pattern."""
    from semabridge.utils.enterprise_logger import get_enterprise_logger
    
    console.print(Panel.fit(
        f"[bold]Log Search[/bold]\n"
        f"Pattern: {pattern}\n"
        f"Type: {log_type}",
        title="Search",
    ))
    
    try:
        logger = get_enterprise_logger()
        matches = logger.read_log(log_type, lines, pattern)
        
        console.print(f"\n[bold]Found {len(matches)} matching lines:[/bold]\n")
        
        for line in matches:
            # Highlight the search pattern
            highlighted = line.replace(pattern, f"[bold yellow]{pattern}[/bold yellow]")
            console.print(highlighted.strip())
        
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)


@semantic_app.command("logs-cleanup")
def logs_cleanup(
    days: int = typer.Option(90, "--days", "-d", help="Delete logs older than N days"),
    confirm: bool = typer.Option(False, "--confirm", "-y", help="Skip confirmation"),
):
    """Clean up old log files."""
    from semabridge.utils.enterprise_logger import get_enterprise_logger
    
    console.print(Panel.fit(
        f"[bold]Log Cleanup[/bold]\n"
        f"Retention: {days} days",
        title="Cleanup",
    ))
    
    if not confirm:
        confirmed = typer.confirm(f"Delete logs older than {days} days?")
        if not confirmed:
            console.print("[yellow]Cancelled.[/yellow]")
            return
    
    try:
        logger = get_enterprise_logger()
        removed = logger.cleanup_old_logs(days)
        
        console.print(f"\n[green][OK] Removed {removed} old log files[/green]")
        
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)


# ============ Status Commands ============

@semantic_app.command("status")
def status():
    """Show overall semantic API status."""
    from semabridge.repository.semantic_version_manager import SemanticVersionManager
    from semabridge.repository.semantic_snapshot_manager import SemanticSnapshotManager
    from semabridge.utils.enterprise_logger import get_enterprise_logger
    
    console.print(Panel.fit(
        "[bold]Semantic API Status[/bold]",
        title="Status",
    ))
    
    try:
        version_manager = SemanticVersionManager()
        snapshot_manager = SemanticSnapshotManager()
        logger = get_enterprise_logger()
        
        table = Table(title="System Status")
        table.add_column("Component", style="cyan")
        table.add_column("Status", style="green")
        table.add_column("Details", style="white")
        
        # Version Manager
        sf_current = version_manager.get_current_version("snowflake")
        fb_current = version_manager.get_current_version("fabric")
        
        table.add_row(
            "Version Manager",
            "[green]OK[/green]",
            f"Snowflake: {sf_current.version_id[:20] if sf_current else 'None'}..."
        )
        
        # Snapshot Manager
        sf_snapshots = len(snapshot_manager._index.get("snowflake", []))
        fb_snapshots = len(snapshot_manager._index.get("fabric", []))
        
        table.add_row(
            "Snapshot Manager",
            "[green]OK[/green]",
            f"Snowflake: {sf_snapshots}, Fabric: {fb_snapshots} snapshots"
        )
        
        # Logging
        log_files = logger.get_log_files()
        total_logs = sum(len(files) for files in log_files.values())
        
        table.add_row(
            "Enterprise Logger",
            "[green]OK[/green]",
            f"{total_logs} log files"
        )
        
        console.print(table)
        
        # Paths
        console.print(f"\n[dim]Metadata dir: {version_manager.metadata_dir}[/dim]")
        console.print(f"[dim]Snapshots dir: {snapshot_manager.snapshots_dir}[/dim]")
        console.print(f"[dim]Logs dir: {logger.logs_dir}[/dim]")
        
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)
