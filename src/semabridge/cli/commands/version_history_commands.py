"""Version history CLI commands: history, rollback, list-projects."""
from __future__ import annotations

import time
import uuid
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from semabridge.core.settings import get_settings
from semabridge.core.run_helpers import elapsed_ms
from semabridge.repository.model_repository import ModelRepository
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


def _display_rollback_preview(diff, current, target, console: Console) -> None:
    """Display the semantic diff preview for rollback with tabular format."""
    from rich.tree import Tree

    # Use DuckDBManager.compare_versions for accurate diff (filters false positives)
    db_manager = ModelRepository()
    real_changes = db_manager.compare_versions(
        current.project_id,
        current.snapshot_id,  # From (current HEAD)
        target.snapshot_id     # To (target rollback version)
    )

    # Summary
    console.print()
    real_change_count = len(real_changes)
    summary_text = f"[bold]Changes if rollback proceeds:[/bold]\n\n"
    summary_text += f"Total Changes: {real_change_count}\n"

    if diff.summary.has_breaking_changes:
        summary_text += f"[red]⚠ Breaking Changes: {len(diff.breaking_changes)}[/red]\n"

    console.print(Panel(summary_text.strip(), title="Impact Summary", border_style="cyan"))

    # If no real changes, show message and return early
    if real_change_count == 0:
        console.print("\n  [green]✓ No semantic changes detected. Versions are identical.[/green]")
        return

    # ============================================================
    # TABULAR DIFF: Previous Version | New Version
    # ============================================================
    console.print("\n[bold cyan]Changes (Previous → New):[/bold cyan]")

    table = Table(
        title="Rollback Diff: Previous Version → New Version",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Object", style="bold", width=30)
    table.add_column("Previous Value", style="red", width=40)
    table.add_column("New Value", style="green", width=40)

    for row in real_changes[:20]:  # Limit to 20 rows for readability
        prev = row["previous_version"][:60] + "..." if len(row["previous_version"]) > 60 else row["previous_version"]
        new = row["new_version"][:60] + "..." if len(row["new_version"]) > 60 else row["new_version"]
        table.add_row(row["object"], prev, new)

    console.print()
    console.print(table)

    if len(real_changes) > 20:
        console.print(f"[dim]... and {len(real_changes) - 20} more changes[/dim]")

    console.print()

    # ============================================================
    # Tree view summary by change type
    # ============================================================
    added = [r for r in real_changes if r["previous_version"] == "—"]
    removed = [r for r in real_changes if r["new_version"] == "—"]
    modified = [r for r in real_changes if r["previous_version"] != "—" and r["new_version"] != "—"]

    if added or removed or modified:
        tree = Tree(f"[bold]CHANGES[/bold] ({len(real_changes)} total)")

        if added:
            branch = tree.add(f"[green]↩ Will be RESTORED ({len(added)})[/green]")
            for c in added[:5]:
                branch.add(f"[green]{c['object']}[/green]")
            if len(added) > 5:
                branch.add(f"[dim]... and {len(added) - 5} more[/dim]")

        if modified:
            branch = tree.add(f"[yellow]~ Will be MODIFIED ({len(modified)})[/yellow]")
            for c in modified[:5]:
                branch.add(f"[yellow]{c['object']}[/yellow]")
            if len(modified) > 5:
                branch.add(f"[dim]... and {len(modified) - 5} more[/dim]")

        if removed:
            branch = tree.add(f"[red]✗ Will be REMOVED ({len(removed)})[/red]")
            for c in removed[:5]:
                branch.add(f"[red]{c['object']}[/red]")
            if len(removed) > 5:
                branch.add(f"[dim]... and {len(removed) - 5} more[/dim]")

        console.print(tree)
        console.print()


def register_version_history_commands(app: typer.Typer, console: Console, show_banner) -> None:
    """Register version history commands onto *app*."""

    @app.command()
    def history(
        dataset_id: str = typer.Option(..., "--dataset-id", "-d", help="Fabric Dataset ID (project ID)"),
        limit: int = typer.Option(10, "--limit", "-l", help="Number of snapshots to show"),
    ):
        """
        View version history for a semantic model.

        Shows all snapshots stored in DuckDB for the given dataset.
        """
        show_banner()

        console.print(Panel.fit(
            f"[bold]Version History[/bold]\n"
            f"Dataset: {dataset_id}",
            title="History",
        ))

        db_manager = ModelRepository()
        snapshots = db_manager.list_snapshots(dataset_id, limit=limit)

        if not snapshots:
            console.print("[yellow]No snapshots found for this dataset.[/yellow]")
            console.print(f"Run: python main.py reverse-sync -d {dataset_id} --sync --tag v1.0")
            return

        head = db_manager.get_head(dataset_id)

        table = Table(title=f"Snapshot History ({len(snapshots)} versions)")
        table.add_column("#", style="dim", justify="right")
        table.add_column("Snapshot ID", style="cyan")
        table.add_column("Tag", style="green")
        table.add_column("Status", style="bold")
        table.add_column("Dur (ms)", justify="right")
        table.add_column("Timestamp", style="white")
        table.add_column("Metrics", style="magenta", justify="right")
        table.add_column("HEAD", style="yellow")

        for i, snap in enumerate(snapshots):
            metrics = len(snap.sml_blob.get("metrics", []))
            is_head = "●" if snap.snapshot_id == head.snapshot_id else ""

            status_color = "[green]" if snap.status == "success" else "[red]"
            status_display = f"{status_color}{snap.status}[/]"

            table.add_row(
                str(i + 1),
                snap.snapshot_id[:12] + "...",
                snap.version_tag or "-",
                status_display,
                str(snap.duration_ms or "-"),
                snap.timestamp[:19],
                str(metrics),
                is_head
            )

        console.print(table)
        console.print(f"\n[dim]Use 'rollback' command to restore a previous version[/dim]")

    @app.command()
    def rollback(
        dataset_id: str = typer.Option(..., "--dataset-id", "-d", help="Fabric Dataset ID (project ID)"),
        snapshot_id: Optional[str] = typer.Option(None, "--snapshot-id", "-s", help="Target snapshot ID to rollback to"),
        tag: Optional[str] = typer.Option(None, "--tag", "-t", help="Target version tag to rollback to (e.g., v1.0)"),
        sync: bool = typer.Option(False, "--sync", help="Sync rolled-back version bidirectionally to Snowflake and Fabric"),
        rollback_tag: Optional[str] = typer.Option(None, "--rollback-tag", help="Tag for the rollback snapshot"),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
        dry_run: bool = typer.Option(False, "--dry-run", help="Show preview only, do not execute"),
    ):
        """
        Rollback to a previous version of the semantic model.

        TWO-PHASE ROLLBACK FLOW:

        Phase 1 (Preview): Shows semantic diff of what will change
        Phase 2 (Confirmation): Asks for user confirmation before proceeding

        Use --sync to also update Snowflake AND Fabric with the rolled-back version.
        Use --yes to skip the confirmation prompt.
        Use --dry-run to only show what would change without executing.

        Rollback is non-destructive - the old history is preserved.
        """
        from semabridge.repository.command_logger import (
            get_command_logger, CommandType, ActionType
        )
        from semabridge.repository.semantic_diff_engine import SemanticDiffEngine
        from semabridge.connectors.snowflake_emitter import SnowflakeEmitter

        show_banner()
        settings = get_settings()
        start_time = time.time()
        run_id = str(uuid.uuid4())

        # Initialize command logging
        cmd_logger = get_command_logger()
        log_entry = cmd_logger.log_start(
            command=CommandType.ROLLBACK,
            action_type=ActionType.ROLLBACK_OPERATION,
            project_id=dataset_id,
            details={
                "target_tag": tag,
                "target_snapshot": snapshot_id,
                "sync": sync,
                "run_id": run_id,
            }
        )

        if not snapshot_id and not tag:
            console.print("[red]Error: Must specify either --snapshot-id or --tag[/red]")
            cmd_logger.log_failure(log_entry, "Missing target version", elapsed_ms(start_time))
            raise typer.Exit(code=1)

        console.print(Panel.fit(
            f"[bold]Rollback[/bold]\n"
            f"Dataset: {dataset_id}\n"
            f"Target: {tag or snapshot_id[:12] + '...'}\n"
            f"Sync Bidirectionally: {'Yes' if sync else 'No'}",
            title="Rollback",
        ))

        try:
            db_manager = ModelRepository()
            diff_engine = SemanticDiffEngine()

            # ================================================================
            # STEP 1: Resolve target and current versions
            # ================================================================
            console.print("\n[bold cyan]Step 1/4: Resolving Versions[/bold cyan]")

            # Get current HEAD
            current = db_manager.get_head(dataset_id)
            if not current:
                console.print(f"[red]Error: No snapshots found for '{dataset_id}'[/red]")
                cmd_logger.log_failure(log_entry, "No snapshots found", elapsed_ms(start_time))
                raise typer.Exit(code=1)

            # Find target snapshot
            if tag:
                target = db_manager.get_snapshot_by_tag(dataset_id, tag)
                if not target:
                    console.print(f"[red]Error: No snapshot found with tag '{tag}'[/red]")
                    cmd_logger.log_failure(log_entry, f"Tag not found: {tag}", elapsed_ms(start_time))
                    raise typer.Exit(code=1)
            else:
                target = db_manager.get_snapshot(snapshot_id)
                if not target:
                    console.print(f"[red]Error: Snapshot '{snapshot_id}' not found[/red]")
                    cmd_logger.log_failure(log_entry, f"Snapshot not found: {snapshot_id}", elapsed_ms(start_time))
                    raise typer.Exit(code=1)

            console.print(f"  Current (HEAD): {current.version_tag or current.snapshot_id[:12]} ({current.timestamp[:19]})")
            console.print(f"  Target: {target.version_tag or target.snapshot_id[:12]} ({target.timestamp[:19]})")

            # Check if already at target
            if current.snapshot_id == target.snapshot_id:
                console.print("\n[yellow]Already at target version. Nothing to rollback.[/yellow]")
                cmd_logger.log_success(log_entry, elapsed_ms(start_time), {"result": "no_change"})
                return

            # ================================================================
            # STEP 2: PREVIEW PHASE - Generate Semantic Diff
            # ================================================================
            console.print("\n[bold cyan]Step 2/4: Preview - Changes After Rollback[/bold cyan]")
            cmd_logger.log_phase(log_entry, "preview", {"phase": "preview"})

            # Compare current → target (what changes when we rollback)
            diff = diff_engine.compare_states(
                state_1=current.sml_blob,
                state_2=target.sml_blob,
                from_snapshot_id=f"CURRENT ({current.version_tag or current.snapshot_id[:8]})",
                to_snapshot_id=f"TARGET ({target.version_tag or target.snapshot_id[:8]})",
                from_adapter="repository",
                to_adapter="repository",
            )

            # Get real change count using normalized comparison (filters false positives)
            real_changes = db_manager.compare_versions(
                current.project_id,
                current.snapshot_id,
                target.snapshot_id
            )
            real_change_count = len(real_changes)

            # Display semantic diff (uses real_change_count internally)
            _display_rollback_preview(diff, current, target, console)

            # Log preview details
            log_entry.details.update({
                "preview_total_changes": real_change_count,
                "preview_breaking_changes": len(diff.breaking_changes),
                "preview_measures_added": diff.summary.measures_added,
                "preview_measures_removed": diff.summary.measures_removed,
                "preview_datasets_added": diff.summary.datasets_added,
                "preview_datasets_removed": diff.summary.datasets_removed,
            })

            # If dry run, stop here
            if dry_run:
                console.print("\n[yellow]Dry run mode - rollback not executed.[/yellow]")
                cmd_logger.log_phase(log_entry, "preview", {"result": "dry_run"})
                cmd_logger.log_success(log_entry, elapsed_ms(start_time), {"result": "dry_run"})
                return

            # ================================================================
            # STEP 3: CONFIRMATION PHASE
            # ================================================================
            console.print("\n[bold cyan]Step 3/4: Confirmation[/bold cyan]")

            # If no real changes, skip confirmation
            if real_change_count == 0:
                console.print("  [green]✓ No changes to apply. Versions are identical.[/green]")
                cmd_logger.log_success(log_entry, elapsed_ms(start_time), {"result": "no_changes"})
                return

            if not yes:
                # Show confirmation prompt
                console.print()
                console.print(Panel.fit(
                    f"[bold yellow]⚠ Rollback will apply {real_change_count} changes[/bold yellow]\n\n"
                    f"From: {current.version_tag or current.snapshot_id[:12]}\n"
                    f"To: {target.version_tag or target.snapshot_id[:12]}\n\n"
                    + (f"[red]Including {len(diff.breaking_changes)} breaking changes![/red]\n" if diff.breaking_changes else "")
                    + f"Sync to Snowflake & Fabric: {'Yes' if sync else 'No'}",
                    title="⚠ Confirm Rollback",
                    border_style="yellow",
                ))

                confirm = typer.confirm("Do you want to proceed with rollback?", default=False)

                if not confirm:
                    console.print("\n[yellow]Rollback aborted by user.[/yellow]")
                    cmd_logger.log_aborted(log_entry, "User cancelled", elapsed_ms(start_time))
                    raise typer.Exit(code=0)

            # User confirmed
            cmd_logger.log_phase(log_entry, "confirmed", {"phase": "confirmed"})
            console.print("  [green]✓[/green] Confirmed")

            # ================================================================
            # STEP 4: EXECUTE ROLLBACK
            # ================================================================
            console.print("\n[bold cyan]Step 4/4: Executing Rollback[/bold cyan]")

            rb_tag = rollback_tag or f"rollback_to_{target.version_tag or target.snapshot_id[:8]}"
            success, new_snapshot_id, changes = db_manager.rollback(dataset_id, target.snapshot_id, rb_tag)

            if success:
                console.print(f"  [green][OK][/green] Created rollback snapshot: {new_snapshot_id[:12]}...")
                console.print(f"  Tag: {rb_tag}")

                # Sync if requested
                if sync:
                    from semabridge.sml.models import SMLModel
                    sml_model = SMLModel.model_validate(target.sml_blob)

                    console.print("\n  Deploying to Snowflake...")
                    emitter = SnowflakeEmitter(settings.snowflake)
                    emitter.deploy(sml_model)
                    console.print(f"  [green][OK][/green] Snowflake Semantic Views updated")

                    console.print("  Deploying to Fabric...")
                    from semabridge.connectors.fabric_publisher import FabricPublisher
                    publisher = FabricPublisher(settings.fabric)
                    publisher.publish(
                        sml_model=sml_model,
                        model_name=dataset_id,
                        snowflake_server=settings.snowflake.account,
                        snowflake_warehouse=settings.snowflake.warehouse,
                        snowflake_database=settings.snowflake.database,
                        snowflake_schema=settings.snowflake.schema_name,
                        overwrite=True
                    )
                    console.print(f"  [green][OK][/green] Fabric Semantic Model updated")
                else:
                    console.print("  [yellow]Skipping synchronization (use --sync to execute)[/yellow]")
            else:
                console.print(f"  [yellow]No changes needed (already at target state)[/yellow]")

            duration_ms = elapsed_ms(start_time)
            console.print(f"\n[green][OK] Rollback complete![/green]")
            console.print(f"[dim]Duration: {duration_ms}ms[/dim]")

            # Log success
            cmd_logger.log_success(log_entry, duration_ms, {
                "result": "success",
                "new_snapshot_id": new_snapshot_id if success else None,
                "synced": sync,
                "changes_applied": diff.summary.total_changes,
            })

        except typer.Exit:
            raise  # Re-raise Exit to preserve exit code
        except Exception as e:
            duration_ms = elapsed_ms(start_time)
            console.print(f"\n[red]Error: Rollback execution failed[/red]")
            console.print(f"[yellow]Cause:[/yellow] {str(e)}")
            console.print(f"[blue]Fix:[/blue] Verify the target snapshot ID and your database connectivity.")

            cmd_logger.log_failure(log_entry, str(e), duration_ms)
            if settings.logging.level == "DEBUG":
                import traceback
                traceback.print_exc()
            raise typer.Exit(code=1)

    @app.command()
    def list_projects():
        """
        List all tracked projects (semantic models) in DuckDB.
        """
        show_banner()

        console.print(Panel.fit(
            "[bold]Tracked Projects[/bold]\n"
            "All semantic models with version history",
            title="Projects",
        ))

        db_manager = ModelRepository()
        conn = db_manager._get_connection()

        try:
            results = conn.execute("""
                SELECT p.project_id, p.name, p.workspace_id, p.last_updated,
                       COUNT(s.snapshot_id) as snapshot_count, p.adapter
                FROM projects p
                LEFT JOIN snapshots s ON p.project_id = s.project_id
                GROUP BY p.project_id, p.name, p.workspace_id, p.last_updated, p.adapter
                ORDER BY p.last_updated DESC
            """).fetchall()

            if not results:
                console.print("[yellow]No projects found.[/yellow]")
                console.print("Run 'reverse-sync' to track a Fabric model.")
                return

            table = Table(title=f"Tracked Projects ({len(results)})")
            table.add_column("Project ID", style="cyan")
            table.add_column("Name", style="green")
            table.add_column("Adapter", style="blue")
            table.add_column("Snapshots", style="magenta", justify="right")
            table.add_column("Last Updated", style="white")

            for r in results:
                table.add_row(
                    r[0][:20] + "..." if len(r[0]) > 20 else r[0],
                    r[1] or "-",
                    r[5] or "fabric",
                    str(r[4]),
                    str(r[3])[:19] if r[3] else "-"
                )

            console.print(table)

        finally:
            conn.close()
