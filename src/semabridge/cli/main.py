"""
Semabridge CLI.

Main entry point for the Semabridge pipeline.
Commands are defined in cli/commands/ sub-modules.
"""

from __future__ import annotations

import sys
from pathlib import Path

from semabridge.connectors.snowflake_emitter import MissingSourceTableWarning  # noqa: F401 (re-export for compat)

# Add the parent directory to sys.path to allow 'from semabridge import ...'
# when running directly from the project root.
root_dir = Path(__file__).resolve().parent
if root_dir.name == "semabridge" and str(root_dir.parent) not in sys.path:
    sys.path.insert(0, str(root_dir.parent))

from typing import Optional

import typer
from rich.console import Console

from semabridge.core.settings import get_settings
from semabridge.utils.logger import setup_logging, get_logger

# Import semantic CLI commands (already split)
from semabridge.cli.semantic_commands import semantic_app
from semabridge.cli.diff_commands import diff_app
from semabridge.cli.logs_commands import logs_app
from semabridge.cli.version_commands import version_app
from semabridge.cli.sync_commands import sync_app

# Import command groups from commands/ package
from semabridge.cli.commands.utility_commands import register_utility_commands, show_banner as _show_banner_impl
from semabridge.cli.commands.pipeline_commands import register_pipeline_commands
from semabridge.cli.commands.version_history_commands import register_version_history_commands
from semabridge.cli.commands.deploy_commands import register_deploy_commands

# Initialize Typer app with ASCII-safe help text for Windows console compatibility
app = typer.Typer(
    name="semabridge",
    help="Semabridge - Snowflake to Fabric Semantic Model Pipeline",
    add_completion=False,
)

# Register sub-apps
app.add_typer(semantic_app, name="semantic", help="Semantic API commands")
app.add_typer(diff_app, name="diff", help="Compare semantic models")
app.add_typer(logs_app, name="logs", help="View command execution logs")
app.add_typer(version_app, name="version", help="Model version control")
app.add_typer(sync_app, name="sync", help="Bidirectional PBIX/Snowflake/Power BI sync")

# Force UTF-8 encoding for console output on Windows
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding='utf-8')
            sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

console = Console(force_terminal=True, no_color=False)
logger = get_logger(__name__)

# Simple ASCII banner (Windows console compatible)
BANNER = """
+---------------------------------------------------------------+
|    ____                       ____       _     _              |
|   / ___|  ___ _ __ ___   __ _| __ ) _ __(_) __| | __ _  ___   |
|   \\___ \\ / _ \\ '_ ` _ \\ / _` |  _ \\| '__| |/ _` |/ _` |/ _ \\  |
|    ___) |  __/ | | | | | (_| | |_) | |  | | (_| | (_| |  __/  |
|   |____/ \\___|_| |_| |_|\\__,_|____/|_|  |_|\\__,_|\\__, |\\___|  |
|                                                  |___/        |
|                                                               |
+---------------------------------------------------------------+
"""


def show_banner():
    """Display the application banner."""
    _show_banner_impl(console)


def safe_print(*args, **kwargs):
    """Safe print wrapper that handles Windows encoding issues."""
    try:
        console.print(*args, **kwargs)
    except (UnicodeEncodeError, UnicodeDecodeError, OSError):
        try:
            plain_text = " ".join(str(arg) for arg in args)
            print(plain_text)
        except Exception:
            pass


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
    log_level: Optional[str] = typer.Option(
        None,
        "--log-level",
        help="Override logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL). Takes precedence over YAML config."
    ),
    parallel: bool = typer.Option(False, "--parallel", "-p", help="Enable parallel processing mode"),
):
    """Semabridge - Automate Fabric semantic model generation from Snowflake."""

    # If no subcommand, show help
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit(0)

    # Resolve log level
    settings = get_settings()
    yaml_level = settings.logging.level

    effective_level = log_level or ("DEBUG" if verbose else yaml_level)

    # Initialize logging
    setup_logging(level=effective_level)

    if parallel:
        settings.concurrency.enable_parallel = True

    # Log override if applicable
    if log_level:
        logger.debug(f"CLI log level provided ({log_level}); overriding project config ({yaml_level})")
    elif verbose:
        logger.debug(f"Verbose flag provided; setting log level to DEBUG (overriding {yaml_level})")


# Register all command groups
register_utility_commands(app, console)
register_pipeline_commands(app, console, show_banner)
register_version_history_commands(app, console, show_banner)
register_deploy_commands(app, console, show_banner)


def main() -> None:
    """Execute the Semabridge CLI application."""
    app()


if __name__ == "__main__":
    main()
