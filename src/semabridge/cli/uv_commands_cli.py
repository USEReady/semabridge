"""
CLI interface for the UV Build System.
"""

import typer
from rich.console import Console
from semabridge.core.uv_registry import registry, CommandContext
import semabridge.core.uv_commands # Ensure commands are registered

uv_app = typer.Typer(help="UV Build System Commands")
console = Console()

@uv_app.callback(invoke_without_command=True)
def uv_callback(ctx: typer.Context):
    """UV Build System - Unified command registry."""
    if ctx.invoked_subcommand is None:
        console.print("[bold cyan]SemaBridge UV Build System[/bold cyan]")
        console.print("Use [yellow]semabridge uv --help[/yellow] to see all available commands.\n")
        
        # List registered commands by category
        help_data = registry.get_categorical_help()
        for cat, commands in help_data.items():
            if commands:
                console.print(f"[bold]{cat.upper()}[/bold]")
                for cmd in commands:
                    console.print(f"  [green]{cmd['name']:<12}[/green] {cmd['description']}")
                console.print()

def generic_execute(command_name: str, **kwargs):
    """Helper to execute a command from the registry."""
    cmd = registry.get_command(command_name)
    if not cmd:
        console.print(f"[red]Error:[/red] Command '{command_name}' not found.")
        raise typer.Exit(1)
    
    ctx = CommandContext(options=kwargs)
    try:
        result = cmd.execute(ctx)
        return result
    except Exception as e:
        console.print(f"[red]Execution failed:[/red] {e}")
        raise typer.Exit(1)

# Dynamically add commands to Typer from registry
# Note: For strict Typer discovery, we should ideally define them explicitly
# but here we can wrap them.

@uv_app.command()
def build(
    input_path: str = typer.Option("output/metadata.json", "--input", "-i", help="Input metadata file"),
    output: str = typer.Option("output/sml", "--output", "-o", help="Output directory"),
    auto_detect: bool = typer.Option(True, "--auto-detect/--no-auto-detect", help="Auto-detect relationships"),
):
    """Build SML model."""
    generic_execute("build", input_path=input_path, output=output, auto_detect=auto_detect)

@uv_app.command()
def sync(
    source: str = typer.Option(None, "--source", "-s", help="Source platform"),
    target: str = typer.Option(None, "--target", "-t", help="Target platform"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Skip execution"),
):
    """Sync semantic models."""
    generic_execute("sync", source=source, target=target, dry_run=dry_run)

@uv_app.command()
def validate(
    model: str = typer.Option(None, "--model", "-m", help="Specific model to validate"),
):
    """Validate project."""
    generic_execute("validate", model=model)

@uv_app.command()
def discover(
    pattern: str = typer.Option("*", "--pattern", "-p", help="Discovery pattern"),
):
    """Discover models."""
    generic_execute("discover", pattern=pattern)

@uv_app.command()
def rollback(
    tag: str = typer.Option(..., "--tag", "-t", help="Version tag to rollback to"),
    project_id: str = typer.Option(..., "--project-id", "-p", help="Project ID"),
):
    """Rollback version."""
    generic_execute("rollback", tag=tag, project_id=project_id)

@uv_app.command()
def status():
    """Check system status."""
    generic_execute("status")
