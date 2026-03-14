"""
CLI commands for per-model version control.

Provides:
  semabridge version list    --model <id> [--workspace-id <id>]
  semabridge version compare --model <id> --v1 <id> --v2 <id>
  semabridge version rollback --model <id> --target <id> [--workspace-id <id>]
"""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

version_app = typer.Typer(name="version", help="Model version control commands")


@version_app.command("list")
def version_list(
    model: str = typer.Option(
        ..., "--model", "-m", help="Semantic model identifier"
    ),
    workspace_id: Optional[str] = typer.Option(
        None, "--workspace-id", "-w", help="Fabric workspace ID filter"
    ),
    limit: int = typer.Option(20, "--limit", "-n", help="Max rows to return"),
):
    """
    List version history for a model, newest first.
    """
    from semabridge.repository.model_repository import ModelRepository
    from semabridge.core.settings import resolve_workspace_id

    ws = workspace_id or resolve_workspace_id(prompt_fallback=False)

    db = ModelRepository()
    versions = db.list_model_versions(model_id=model, workspace_id=ws, limit=limit)

    if not versions:
        console.print(
            f"[yellow]No versions found for model '{model}'.[/yellow]"
        )
        raise typer.Exit()

    table = Table(title=f"Version History — {model}", show_lines=True)
    table.add_column("Version ID", style="cyan", no_wrap=True)
    table.add_column("Tag", style="bold magenta")
    table.add_column("Workspace", style="dim")
    table.add_column("Author", style="green")
    table.add_column("Created At")
    table.add_column("Summary")
    table.add_column("Rollback?", style="yellow")

    for v in versions:
        tag = v.get("version_tag") or "—"
        is_rb = "↩ Yes" if v.get("is_rollback") else ""
        table.add_row(
            v["version_id"][:12] + "…",
            tag,
            v["workspace_id"][:12] + "…" if v.get("workspace_id") else "—",
            v.get("author", "system"),
            v["timestamp"],
            v.get("description") or "—",
            is_rb,
        )

    console.print(table)


@version_app.command("compare")
def version_compare(
    model: str = typer.Option(
        ..., "--model", "-m", help="Semantic model identifier"
    ),
    v1: str = typer.Option(
        ..., "--v1", help="First (older) version ID"
    ),
    v2: str = typer.Option(
        ..., "--v2", help="Second (newer) version ID"
    ),
):
    """
    Compare two model versions and render a tabular diff.
    """
    from semabridge.repository.model_repository import ModelRepository

    db = ModelRepository()
    diffs = db.compare_model_versions_tabular(v1, v2)

    if not diffs:
        console.print(
            f"[yellow]No differences found between {v1[:8]}… and {v2[:8]}…[/yellow]"
        )
        raise typer.Exit()

    # Determine version tags for column headers
    old_tag = diffs[0].get("old_version_tag", v1[:8]) if diffs else v1[:8]
    new_tag = diffs[0].get("new_version_tag", v2[:8]) if diffs else v2[:8]

    table = Table(
        title=f"Diff: {old_tag} ↔ {new_tag}",
        show_lines=True,
    )
    table.add_column("Object Name", style="bold")
    table.add_column("Object Type")
    table.add_column("Property")
    table.add_column(f"Old Value ({old_tag})", style="red")
    table.add_column(f"New Value ({new_tag})", style="green")
    table.add_column("Change Type", style="yellow")

    for d in diffs:
        table.add_row(
            d.get("object_name", ""),
            d.get("object_type", ""),
            d.get("property", ""),
            d.get("old_value") or "—",
            d.get("new_value") or "—",
            d.get("change_type", ""),
        )

    console.print(table)


@version_app.command("rollback")
def version_rollback(
    model: str = typer.Option(
        ..., "--model", "-m", help="Semantic model identifier"
    ),
    target: str = typer.Option(
        ..., "--target", "-t", help="Version ID to rollback to"
    ),
    workspace_id: Optional[str] = typer.Option(
        None, "--workspace-id", "-w", help="Fabric workspace ID"
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip confirmation prompt"
    ),
):
    """
    Rollback a model to a previous version.

    Creates a NEW version row with the old snapshot. Never deletes history.
    """
    from semabridge.repository.model_repository import ModelRepository
    from semabridge.core.settings import resolve_workspace_id
    from semabridge.utils.logger import get_logger

    logger = get_logger(__name__)

    ws = workspace_id or resolve_workspace_id(prompt_fallback=False)

    db = ModelRepository()

    # Show what the target version looks like
    snap = db.get_model_version_snapshot(target)
    if snap is None:
        console.print(f"[red]✖ Version '{target}' not found.[/red]")
        raise typer.Exit(code=1)

    # Get version tag for display
    target_tag = db._get_version_tag(target) or target[:12]

    # Preview changes
    head_versions = db.list_model_versions(model_id=model, workspace_id=ws, limit=1)
    change_count = 0
    if head_versions:
        head_id = head_versions[0]["version_id"]
        diffs = db.compare_model_versions_tabular(head_id, target)
        change_count = len(diffs)

    console.print(Panel.fit(
        f"[bold]Rollback Confirmation[/bold]\n\n"
        f"Model:       [cyan]{model}[/cyan]\n"
        f"Target:      [cyan]{target_tag}[/cyan]  ({target[:12]}…)\n"
        f"Changes:     [yellow]{change_count} object(s) will revert[/yellow]\n"
        f"Action:      A [bold]new[/bold] version will be created from this snapshot.\n"
        f"             Existing history is preserved.",
        title="⚠️  Rollback",
        border_style="yellow",
    ))

    if not yes:
        confirmed = typer.confirm("Proceed with rollback?", default=False)
        if not confirmed:
            console.print("[dim]Rollback cancelled.[/dim]")
            raise typer.Exit()

    new_id = db.rollback_model_version(
        model_id=model,
        target_version_id=target,
        workspace_id=ws,
        author="cli",
    )
    logger.info(
        f"ROLLBACK: model={model} restored to version {target_tag} "
        f"new_version={new_id[:8]}…"
    )

    console.print(
        f"[green]✔ Rollback complete.[/green]  New version: [cyan]{new_id}[/cyan]"
    )
