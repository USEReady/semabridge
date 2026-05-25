"""
Diff Commands.

CLI commands for comparing semantic models:
- Compare source (live) vs repository HEAD
- Compare two repository versions
- Semantic-level diff (measures, tables, columns)
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)
console = Console(force_terminal=True, no_color=False)

# Create sub-app for diff commands
diff_app = typer.Typer(
    name="diff",
    help="Compare semantic models between source/target or repository versions",
    add_completion=False,
)


@diff_app.command("source")
def diff_source(
    dataset_id: str = typer.Option(..., "--dataset-id", "-d", help="Fabric Dataset ID"),
    source: str = typer.Option("fabric", "--source", "-s", help="Source type: fabric, snowflake"),
    format: str = typer.Option("cli", "--format", "-f", help="Output format: cli, json, html"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file for report"),
):
    """
    Compare LIVE source model against repository HEAD.
    
    Extracts the current model from the source and compares it
    to the latest committed version in the repository.
    
    Example:
        semabridge diff source -d "Customer Profitability" --source fabric
    """
    from semabridge.core.settings import get_settings
    from semabridge.repository.model_repository import ModelRepository
    from semabridge.repository.semantic_diff_engine import SemanticDiffEngine
    from semabridge.repository.command_logger import (
        get_command_logger, CommandType, ActionType
    )
    
    start_time = time.time()
    cmd_logger = get_command_logger()
    log_entry = cmd_logger.log_start(
        command=CommandType.DIFF,
        action_type=ActionType.COMPARISON,
        adapter=source,
        project_id=dataset_id,
        details={"comparison_type": "source_vs_head"}
    )
    
    console.print(Panel.fit(
        f"[bold]Compare Source vs Repository[/bold]\n"
        f"Dataset: {dataset_id}\n"
        f"Source: {source}",
        title="Diff",
    ))
    
    try:
        settings = get_settings()
        db_manager = ModelRepository()
        
        # Step 1: Get repository HEAD
        console.print("\n[bold cyan]Step 1/3: Loading repository HEAD...[/bold cyan]")
        head = db_manager.get_head(dataset_id)
        
        if not head:
            console.print(f"[red]No repository version found for '{dataset_id}'[/red]")
            console.print("Run 'reverse-sync' first to create a baseline.")
            cmd_logger.log_failure(log_entry, "No repository version found", int((time.time() - start_time) * 1000))
            raise typer.Exit(code=1)
        
        head_state = head.sml_blob
        console.print(f"  [green][OK][/green] Loaded HEAD: {head.snapshot_id[:12]}...")
        
        # Step 2: Extract live model from source
        console.print(f"\n[bold cyan]Step 2/3: Extracting live model from {source}...[/bold cyan]")
        
        if source.lower() == "fabric":
            from semabridge.connectors.fabric_extractor import FabricExtractor
            from semabridge.converter.tmsl_to_sml import TMSLTransformer
            
            extractor = FabricExtractor(settings.fabric)
            tmsl = extractor.get_model_definition(dataset_id)
            row_counts = extractor.get_table_row_counts(dataset_id)
            
            transformer = TMSLTransformer()
            live_model = transformer.transform(
                tmsl, 
                settings.fabric.workspace_id, 
                dataset_id,
                row_counts=row_counts
            )
            live_state = live_model.model_dump(mode='json')
            console.print(f"  [green][OK][/green] Extracted {live_model.metric_count} metrics from Fabric")
            
        elif source.lower() == "snowflake":
            from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
            from semabridge.sml.assembler import SMLAssembler
            
            extractor = SnowflakeExtractor(config=settings.snowflake)
            metadata = extractor.extract_all()
            
            assembler = SMLAssembler(
                model_name=dataset_id,
                source_database=metadata.get("database", ""),
                source_schema=metadata.get("schema", ""),
            )
            
            for table_name, table_info in metadata.get("tables", {}).items():
                columns = metadata.get("columns", {}).get(table_name, [])
                assembler.add_table(table_name=table_name, columns=columns)
            
            live_model = assembler.build()
            live_state = live_model.model_dump(mode='json')
            console.print(f"  [green][OK][/green] Extracted {live_model.dataset_count} tables from Snowflake")
        else:
            console.print(f"[red]Unknown source: {source}[/red]")
            cmd_logger.log_failure(log_entry, f"Unknown source: {source}", int((time.time() - start_time) * 1000))
            raise typer.Exit(code=1)
        
        # Step 3: Compare
        console.print("\n[bold cyan]Step 3/3: Comparing models...[/bold cyan]")
        
        diff_engine = SemanticDiffEngine()
        diff = diff_engine.compare_states(
            state_1=head_state,
            state_2=live_state,
            from_snapshot_id=f"HEAD ({head.snapshot_id[:8]})",
            to_snapshot_id=f"LIVE ({source})",
            from_adapter="repository",
            to_adapter=source,
        )
        
        duration_ms = int((time.time() - start_time) * 1000)
        
        # Display or save report
        report = diff_engine.export_diff_as_report(diff, format)
        
        if output:
            with open(output, "w", encoding="utf-8") as f:
                f.write(report)
            console.print(f"\n[green][OK] Report saved to {output}[/green]")
        else:
            console.print()
            _display_semantic_diff(diff)
        
        # Summary
        if diff.summary.total_changes == 0:
            console.print("\n[green]✓ Source and repository are in sync![/green]")
        else:
            console.print(f"\n[yellow]⚠ {diff.summary.total_changes} differences found[/yellow]")
            if diff.summary.has_breaking_changes:
                console.print(f"[red]  Including {len(diff.breaking_changes)} breaking changes![/red]")
        
        console.print(f"\n[dim]Comparison took {duration_ms}ms[/dim]")
        
        cmd_logger.log_success(log_entry, duration_ms, {
            "total_changes": diff.summary.total_changes,
            "breaking_changes": len(diff.breaking_changes),
        })
        
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        cmd_logger.log_failure(log_entry, str(e), duration_ms)
        console.print(f"\n[red]Error: {e}[/red]")
        raise typer.Exit(code=1)


@diff_app.command("versions")
def diff_versions(
    dataset_id: str = typer.Option(..., "--dataset-id", "-d", help="Project/Dataset ID"),
    from_version: Optional[str] = typer.Option(None, "--from", "-f", help="From version (tag or snapshot ID)"),
    to_version: Optional[str] = typer.Option(None, "--to", "-t", help="To version (tag or snapshot ID, default: HEAD)"),
    format: str = typer.Option("cli", "--format", help="Output format: cli, json, html"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file for report"),
):
    """
    Compare two repository versions.
    
    Shows semantic-level changes between versions (measures, tables, columns).
    
    Examples:
        semabridge diff versions -d "mymodel" --from v1.0 --to v2.0
        semabridge diff versions -d "mymodel" --from v1.0  # Compare v1.0 to HEAD
    """
    from semabridge.repository.model_repository import ModelRepository
    from semabridge.repository.semantic_diff_engine import SemanticDiffEngine
    from semabridge.repository.command_logger import (
        get_command_logger, CommandType, ActionType
    )
    
    start_time = time.time()
    cmd_logger = get_command_logger()
    log_entry = cmd_logger.log_start(
        command=CommandType.DIFF,
        action_type=ActionType.COMPARISON,
        project_id=dataset_id,
        details={"comparison_type": "version_vs_version", "from": from_version, "to": to_version}
    )
    
    console.print(Panel.fit(
        f"[bold]Compare Repository Versions[/bold]\n"
        f"Dataset: {dataset_id}\n"
        f"From: {from_version or 'previous'}\n"
        f"To: {to_version or 'HEAD'}",
        title="Diff Versions",
    ))
    
    try:
        db_manager = ModelRepository()
        
        # Resolve versions
        console.print("\n[bold cyan]Step 1/2: Resolving versions...[/bold cyan]")
        
        # Get HEAD
        head = db_manager.get_head(dataset_id)
        if not head:
            console.print(f"[red]No snapshots found for '{dataset_id}'[/red]")
            cmd_logger.log_failure(log_entry, "No snapshots found", int((time.time() - start_time) * 1000))
            raise typer.Exit(code=1)
        
        # Resolve 'to' version (default to HEAD)
        if to_version:
            to_snapshot = db_manager.get_snapshot_by_tag(dataset_id, to_version)
            if not to_snapshot:
                to_snapshot = db_manager.get_snapshot(to_version)
            if not to_snapshot:
                console.print(f"[red]Version not found: {to_version}[/red]")
                cmd_logger.log_failure(log_entry, f"Version not found: {to_version}", int((time.time() - start_time) * 1000))
                raise typer.Exit(code=1)
        else:
            to_snapshot = head
        
        # Resolve 'from' version
        if from_version:
            from_snapshot = db_manager.get_snapshot_by_tag(dataset_id, from_version)
            if not from_snapshot:
                from_snapshot = db_manager.get_snapshot(from_version)
            if not from_snapshot:
                console.print(f"[red]Version not found: {from_version}[/red]")
                cmd_logger.log_failure(log_entry, f"Version not found: {from_version}", int((time.time() - start_time) * 1000))
                raise typer.Exit(code=1)
        else:
            # Get previous snapshot
            snapshots = db_manager.list_snapshots(dataset_id, limit=2)
            if len(snapshots) < 2:
                console.print("[yellow]Only one version exists. Nothing to compare.[/yellow]")
                cmd_logger.log_failure(log_entry, "Only one version exists", int((time.time() - start_time) * 1000))
                raise typer.Exit(code=1)
            from_snapshot = snapshots[1]  # Previous version
        
        console.print(f"  From: {from_snapshot.version_tag or from_snapshot.snapshot_id[:12]} ({from_snapshot.timestamp[:19]})")
        console.print(f"  To: {to_snapshot.version_tag or to_snapshot.snapshot_id[:12]} ({to_snapshot.timestamp[:19]})")
        
        # Compare
        console.print("\n[bold cyan]Step 2/2: Comparing versions...[/bold cyan]")
        
        diff_engine = SemanticDiffEngine()
        diff = diff_engine.compare_states(
            state_1=from_snapshot.sml_blob,
            state_2=to_snapshot.sml_blob,
            from_snapshot_id=from_snapshot.version_tag or from_snapshot.snapshot_id[:12],
            to_snapshot_id=to_snapshot.version_tag or to_snapshot.snapshot_id[:12],
            from_adapter="repository",
            to_adapter="repository",
        )
        
        duration_ms = int((time.time() - start_time) * 1000)
        
        # Display or save report
        if output:
            report = diff_engine.export_diff_as_report(diff, format)
            with open(output, "w", encoding="utf-8") as f:
                f.write(report)
            console.print(f"\n[green][OK] Report saved to {output}[/green]")
        else:
            console.print()
            _display_semantic_diff(diff)
        
        # Summary
        if diff.summary.total_changes == 0:
            console.print("\n[green]✓ Versions are identical![/green]")
        else:
            console.print(f"\n[yellow]Found {diff.summary.total_changes} changes between versions[/yellow]")
        
        console.print(f"\n[dim]Comparison took {duration_ms}ms[/dim]")
        
        cmd_logger.log_success(log_entry, duration_ms, {
            "total_changes": diff.summary.total_changes,
            "from_snapshot": from_snapshot.snapshot_id,
            "to_snapshot": to_snapshot.snapshot_id,
        })
        
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        cmd_logger.log_failure(log_entry, str(e), duration_ms)
        console.print(f"\n[red]Error: {e}[/red]")
        raise typer.Exit(code=1)


def _display_semantic_diff(diff):
    """Display semantic diff in a tabular format (PRD-mandated)."""
    from semabridge.repository.semantic_diff_engine import ChangeType, EntityType
    
    # Summary Panel
    summary_lines = []
    summary_lines.append(f"[bold]Total Changes:[/bold] {diff.summary.total_changes}")
    
    if diff.summary.has_breaking_changes:
        summary_lines.append(f"[red bold]⚠ Breaking Changes:[/red bold] {len(diff.breaking_changes)}")
    
    console.print(Panel("\n".join(summary_lines), title="Summary", border_style="cyan"))
    
    # No changes
    if diff.summary.total_changes == 0:
        return
    
    # Create tabular diff (PRD requirement: Previous version | New version format)
    diff_table = Table(
        title="Changes (Previous → New)",
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
        expand=True,
    )
    diff_table.add_column("Entity Type", style="dim", width=12)
    diff_table.add_column("Name", style="bold", width=25)
    diff_table.add_column("Change", style="yellow", width=10)
    diff_table.add_column("Previous Value", style="red", width=30)
    diff_table.add_column("New Value", style="green", width=30)
    
    # Process all changes
    for change in diff.changes:
        entity_type = change.entity_type.value.upper()
        entity_name = change.entity_name
        
        if change.change_type == ChangeType.ADDED:
            diff_table.add_row(
                entity_type,
                entity_name,
                "[green]+ Added[/green]",
                "—",
                _truncate(str(change.new_value or ""), 28),
            )
        elif change.change_type == ChangeType.REMOVED:
            diff_table.add_row(
                entity_type,
                entity_name,
                "[red]- Removed[/red]",
                _truncate(str(change.old_value or ""), 28),
                "—",
            )
        elif change.change_type == ChangeType.MODIFIED:
            # Show changed fields
            old_display = _truncate(str(change.old_value or ""), 28) if change.old_value else change.change_reason
            new_display = _truncate(str(change.new_value or ""), 28) if change.new_value else "—"
            diff_table.add_row(
                entity_type,
                entity_name,
                "[yellow]~ Modified[/yellow]",
                old_display,
                new_display,
            )
    
    console.print(diff_table)
    console.print()
    
    # Breaking changes detail table
    if diff.breaking_changes:
        breaking_table = Table(
            title="⚠ Breaking Changes",
            show_header=True,
            header_style="bold red",
            border_style="red",
        )
        breaking_table.add_column("Entity Type", width=12)
        breaking_table.add_column("Name", width=25)
        breaking_table.add_column("Reason", width=50)
        
        for c in diff.breaking_changes:
            breaking_table.add_row(
                c.entity_type.value.upper(),
                c.entity_name,
                c.change_reason,
            )
        
        console.print(breaking_table)


def _truncate(text: str, max_len: int) -> str:
    """Truncate text with ellipsis if too long."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


@diff_app.command("stats")
def diff_stats(
    dataset_id: str = typer.Option(..., "--dataset-id", "-d", help="Project/Dataset ID"),
):
    """
    Show model statistics (metrics, tables, columns count).
    
    Displays current HEAD statistics.
    """
    from semabridge.repository.model_repository import ModelRepository
    
    console.print(Panel.fit(
        f"[bold]Model Statistics[/bold]\n"
        f"Dataset: {dataset_id}",
        title="Stats",
    ))
    
    db_manager = ModelRepository()
    head = db_manager.get_head(dataset_id)
    
    if not head:
        console.print(f"[red]No model found for '{dataset_id}'[/red]")
        raise typer.Exit(code=1)
    
    sml = head.sml_blob
    
    # Compute statistics
    metrics = sml.get("metrics", [])
    datasets = sml.get("datasets", [])
    dimensions = sml.get("dimensions", [])
    relationships = sml.get("relationships", [])
    
    # Count columns across all datasets
    total_columns = 0
    for ds in datasets:
        total_columns += len(ds.get("columns", []))
    
    # Display stats
    table = Table(title=f"Model: {sml.get('label', dataset_id)}")
    table.add_column("Category", style="cyan")
    table.add_column("Count", style="bold", justify="right")
    
    table.add_row("Tables/Datasets", str(len(datasets)))
    table.add_row("Columns", str(total_columns))
    table.add_row("Metrics/Measures", str(len(metrics)))
    table.add_row("Dimensions", str(len(dimensions)))
    table.add_row("Relationships", str(len(relationships)))
    
    console.print(table)
    
    # Version info
    console.print(f"\n[dim]Version: {head.version_tag or head.snapshot_id[:12]}[/dim]")
    console.print(f"[dim]Last updated: {head.timestamp[:19]}[/dim]")


@diff_app.command("compare")
def diff_compare(
    dataset_id: str = typer.Option(..., "--dataset-id", "-d", help="Project/Dataset ID"),
    from_id: str = typer.Option(..., "--from", "-f", help="From snapshot ID or tag"),
    to_id: str = typer.Option(..., "--to", "-t", help="To snapshot ID or tag"),
    markdown: bool = typer.Option(False, "--markdown", "-m", help="Output as markdown table"),
):
    """
    Compare two snapshots with simple tabular output.
    
    Shows Previous Version | New Version columns for each changed object.
    
    Examples:
        semabridge diff compare -d "mymodel" --from v1.0 --to v2.0
        semabridge diff compare -d "mymodel" -f abc123 -t def456 --markdown
    """
    from semabridge.repository.model_repository import ModelRepository
    
    console.print(Panel.fit(
        f"[bold]Compare Versions[/bold]\n"
        f"Dataset: {dataset_id}\n"
        f"From: {from_id}  →  To: {to_id}",
        title="Compare",
    ))
    
    db_manager = ModelRepository()
    
    # Resolve snapshot IDs (try tag first, then direct ID)
    from_snap = db_manager.get_snapshot_by_tag(dataset_id, from_id)
    if not from_snap:
        from_snap = db_manager.get_snapshot(from_id)
    
    to_snap = db_manager.get_snapshot_by_tag(dataset_id, to_id)
    if not to_snap:
        to_snap = db_manager.get_snapshot(to_id)
    
    if not from_snap:
        console.print(f"[red]From version not found: {from_id}[/red]")
        raise typer.Exit(code=1)
    
    if not to_snap:
        console.print(f"[red]To version not found: {to_id}[/red]")
        raise typer.Exit(code=1)
    
    # Use the markdown table method
    if markdown:
        md_table = db_manager.compare_versions_markdown(
            dataset_id, 
            from_snap.snapshot_id, 
            to_snap.snapshot_id
        )
        console.print(f"\n{md_table}")
    else:
        # Use Rich table for CLI display
        diff_data = db_manager.compare_versions(
            dataset_id,
            from_snap.snapshot_id,
            to_snap.snapshot_id
        )
        
        if not diff_data:
            console.print("\n[green]✓ No differences found. Versions are identical.[/green]")
            return
        
        table = Table(
            title="Diff: Previous Version → New Version",
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("Object", style="bold", width=30)
        table.add_column("Previous Version", style="red", width=40)
        table.add_column("New Version", style="green", width=40)
        
        for row in diff_data:
            prev = row["previous_version"][:80] + "..." if len(row["previous_version"]) > 80 else row["previous_version"]
            new = row["new_version"][:80] + "..." if len(row["new_version"]) > 80 else row["new_version"]
            table.add_row(row["object"], prev, new)
        
        console.print()
        console.print(table)
        console.print(f"\n[yellow]Found {len(diff_data)} differences[/yellow]")
