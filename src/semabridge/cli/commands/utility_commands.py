"""Utility CLI commands: app-version, init, config, validate, list-fabric-models."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from semabridge import __version__
from semabridge.core.settings import get_settings
from semabridge.utils.logger import get_logger
from semabridge.connectors.fabric_extractor import FabricExtractor

console = Console(force_terminal=True, no_color=False)
logger = get_logger(__name__)


def show_banner(console: Console) -> None:
    """Display the application banner."""
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
    try:
        console.print(BANNER, style="cyan")
    except (UnicodeEncodeError, UnicodeDecodeError, OSError):
        try:
            print("=== SemaBridge - Snowflake to Fabric Pipeline ===")
        except Exception:
            pass


def register_utility_commands(app: typer.Typer, _console: Console) -> None:
    """Register utility commands onto *app*."""

    @app.command(name="app-version")
    def app_version():
        """Show version information."""
        _console.print(f"Semabridge v{__version__}")

    @app.command()
    def init(
        force: bool = typer.Option(
            False,
            "--force",
            help="Skip reconfigure confirmation and continue initialization",
        ),
    ):
        """
        Initialize Semabridge with interactive global configuration setup.
        """
        from semabridge.core.initializer import SemabridgeInitializer
        from rich.prompt import Confirm, Prompt
        from rich.progress import Progress, SpinnerColumn, TextColumn
        import yaml

        show_banner(_console)

        # Early-exit check
        global_config_path = SemabridgeInitializer.get_default_config_path()
        if global_config_path.exists() and not force:
            should_reconfigure = Confirm.ask(
                "? Semabridge is already initialized. Reconfigure?",
                default=False,
            )
            if not should_reconfigure:
                _console.print("[yellow]Initialization skipped. Existing configuration kept.[/yellow]")
                raise typer.Exit(code=0)

        _console.print(
            Panel.fit(
                "[bold]Initialize Semabridge: Set up your global configuration and database backend.[/bold]",
                title="Init",
                border_style="cyan",
            )
        )
        _console.print()

        selected_db_backend = Prompt.ask(
            "Prompt 1 (Backend): Select database backend",
            choices=["duckdb", "orm"],
            default="duckdb",
        ).strip().lower()

        selected_repository_path: Optional[Path] = None
        orm_connection_url = ""
        orm_connection_url_env_key = "SEMABRIDGE_DATABASE_URL"

        if selected_db_backend == "duckdb":
            default_repository_path = SemabridgeInitializer.get_default_repository_path()
            repository_input = Prompt.ask(
                "Where would you like to store the Semabridge repository?",
                default=str(default_repository_path),
            ).strip()
            selected_repository_path = Path(repository_input).expanduser()
        else:
            orm_connection_url = Prompt.ask(
                "Enter the ORM connection URL (or press Enter to rely on the SEMABRIDGE_DATABASE_URL env var):",
                default="",
                show_default=False,
            ).strip()

        selected_log_level = Prompt.ask(
            "Prompt 3 (Logging): Select default logging level",
            choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
            default="INFO",
        ).strip().upper()

        workspace_ids_input = Prompt.ask(
            "Enter Fabric workspace ID(s) (comma-separated, or press Enter to skip):",
            default="",
            show_default=False,
        )

        snowflake_default_warehouse = Prompt.ask(
            "Enter Snowflake default warehouse (or press Enter to skip):",
            default="",
            show_default=False,
        ).strip()

        _console.print()

        # Perform initialization with progress
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=_console,
            transient=True,
        ) as progress:
            progress.add_task(description="Initializing repository...", total=None)

            initializer = SemabridgeInitializer(
                repository_path=selected_repository_path,
                local=False,
                log_dir=None,  # Will be inferred from the repo path
                db_backend=selected_db_backend,
                orm_connection_url_env=orm_connection_url_env_key,
            )
            result = initializer.initialize()

        # Save/reconfigure global settings
        if result.success:
            try:
                target_config_path = result.config_path or initializer.config_path
                cfg_text = ""
                if target_config_path.exists():
                    cfg_text = target_config_path.read_text(encoding="utf-8")
                cfg = yaml.safe_load(cfg_text) or {}

                cfg.setdefault("core", {})["repository_path"] = str(result.repository_path)
                cfg["core"]["local_models_path"] = str(result.repository_path.parent / "models")
                cfg["config_path"] = str(target_config_path)

                # Logging level
                cfg.setdefault("logging", {})["level"] = selected_log_level
                # Log file co-located with repo
                log_file_path = str(result.repository_path.parent / "semabridge.log")
                cfg["logging"]["log_file"] = log_file_path

                # Database settings
                cfg.setdefault("database", {})["backend"] = selected_db_backend
                if selected_db_backend == "duckdb":
                    cfg["database"]["db_path"] = str(result.repository_path.parent / "semabridge_state.db")
                    cfg["database"]["writer_thread_queue"] = True
                    cfg["database"].pop("connection_url_env", None)
                else:
                    cfg["database"]["connection_url_env"] = orm_connection_url_env_key
                    cfg["database"].pop("db_path", None)
                    cfg["database"].pop("writer_thread_queue", None)
                    if orm_connection_url:
                        os.environ[orm_connection_url_env_key] = orm_connection_url

                # Workspace IDs
                ws_list = [w.strip() for w in workspace_ids_input.split(",") if w.strip()]
                if ws_list:
                    cfg.setdefault("fabric", {})["workspace_ids"] = ws_list
                    cfg["fabric"]["default_workspace_id"] = ws_list[0]

                # Snowflake defaults
                if snowflake_default_warehouse:
                    cfg.setdefault("snowflake", {})["default_warehouse"] = snowflake_default_warehouse

                target_config_path.write_text(
                    yaml.dump(cfg, default_flow_style=False, sort_keys=False),
                    encoding="utf-8",
                )
                result.config_path = target_config_path
            except Exception as exc:
                _console.print(f"[red][FAIL][/red] Failed to save configuration: {exc}")
                raise typer.Exit(code=1)

        # Display result
        if result.success:
            _console.print("[green][OK][/green] Semabridge initialized successfully.")
            _console.print()

            table = Table(show_header=False, box=None)
            table.add_column("Item", style="cyan")
            table.add_column("Path", style="white")

            table.add_row("Repository", str(result.repository_path))
            if result.config_path:
                table.add_row("Config", str(result.config_path))
            table.add_row("DB Backend", selected_db_backend)
            table.add_row("Log file", str(result.repository_path.parent / "semabridge.log"))

            _console.print(table)
            _console.print()

            if selected_db_backend == "orm":
                _console.print(
                    "  [green]✔[/green] Version history is managed via the configured ORM database "
                    f"(env: [cyan]{orm_connection_url_env_key}[/cyan])."
                )
            else:
                _console.print(
                    "  [green]✔[/green] Version history is stored in local DuckDB file: "
                    f"[cyan]{result.repository_path.parent / 'semabridge_state.db'}[/cyan]"
                )
            _console.print(
                f"  [green]✔[/green] Logs will be stored at: "
                f"[cyan]{result.repository_path.parent / 'semabridge.log'}[/cyan]"
            )
            _console.print()

            _console.print(f"  • Review configuration: [cyan]semabridge config[/cyan]")
            _console.print(f"  • Validate connections: [cyan]semabridge validate[/cyan]")
            _console.print(f"  • Start syncing: [cyan]semabridge semantic sync[/cyan]")
            _console.print(f"  • View logs: [cyan]semabridge logs list[/cyan]")

            if result.config_path:
                _console.print()
                _console.print(f"[dim]Edit {result.config_path} to customize settings.[/dim]")
        else:
            _console.print(f"[red][FAIL][/red] {result.message}")
            raise typer.Exit(code=1)

    @app.command()
    def config():
        """Show current configuration (secrets masked)."""
        show_banner(_console)

        settings = get_settings()

        table = Table(title="Configuration", show_header=True)
        table.add_column("Setting", style="cyan")
        table.add_column("Value", style="green")

        # Source/Target settings from semabridge.yaml
        try:
            from semabridge.core.config_loader import get_default_config_path, load_and_merge_configs
            config_path = get_default_config_path()
            if config_path:
                run_config, _ = load_and_merge_configs([config_path], resolve_env=False)

                # Source settings
                table.add_section()
                if run_config and run_config.get("source"):
                    source = run_config["source"]
                    table.add_row("Source Type", source.get("type", "Not configured"))
                    if source.get("dataset_id"):
                        table.add_row("Source Dataset ID", source.get("dataset_id"))
                else:
                    table.add_row("Source", "Not configured")

                # Target settings
                table.add_section()
                if run_config and run_config.get("target"):
                    target = run_config["target"]
                    table.add_row("Target Type", target.get("type", "Not configured"))
                    table.add_row("Target Deploy", str(target.get("deploy", False)))
                else:
                    table.add_row("Target", "Not configured")

                # Model name from YAML
                if run_config and run_config.get("model_name"):
                    table.add_section()
                    table.add_row("Model Name (YAML)", run_config["model_name"])
        except Exception:
            pass  # Fall back to showing just env-based settings

        # Snowflake settings
        table.add_section()
        table.add_row("Snowflake Account", settings.snowflake.account)
        table.add_row("Snowflake User", settings.snowflake.user)
        table.add_row("Snowflake Password", "********")
        table.add_row("Snowflake Warehouse", settings.snowflake.warehouse)
        table.add_row("Snowflake Database", settings.snowflake.database)
        table.add_row("Snowflake Schema", settings.snowflake.schema_name)

        # Fabric settings
        table.add_section()
        table.add_row("Fabric Tenant ID", settings.fabric.tenant_id[:8] + "..." if len(settings.fabric.tenant_id) > 8 else settings.fabric.tenant_id)
        table.add_row("Fabric Client ID", settings.fabric.client_id[:8] + "..." if len(settings.fabric.client_id) > 8 else settings.fabric.client_id)
        table.add_row("Fabric Client Secret", "********")
        table.add_row("Fabric Workspace ID", settings.fabric.workspace_id)

        # Model settings from env
        table.add_section()
        table.add_row("Model Name", settings.model.name)
        table.add_row("Cache Enabled", str(settings.model.cache_enabled))

        _console.print(table)

    @app.command()
    def validate():
        """Validate Snowflake and Fabric connections."""
        show_banner(_console)

        settings = get_settings()

        _console.print("\n[bold]Validating connections...[/bold]\n")

        # Test Snowflake
        _console.print("Testing Snowflake connection...", end=" ")
        try:
            from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
            extractor = SnowflakeExtractor(settings.snowflake)
            extractor.test_connection()
            _console.print("[green][OK] Connected[/green]")
        except Exception as e:
            _console.print("[red][FAIL][/red]")
            _console.print(f"\n[red]Error: Snowflake connection failed[/red]")
            _console.print(f"[yellow]Cause:[/yellow] {str(e)}")
            _console.print(f"[blue]Fix:[/blue] Verify SNOWFLAKE_ACCOUNT, USER, and PASSWORD in your configuration.")

            if settings.logging.level == "DEBUG":
                import traceback
                traceback.print_exc()

        # Test Fabric
        _console.print("Testing Fabric connection...", end=" ")
        try:
            from semabridge.connectors.fabric_publisher import FabricPublisher
            publisher = FabricPublisher(settings.fabric)
            if publisher.test_connection():
                _console.print("[green][OK] Connected[/green]")
            else:
                _console.print("[red][FAIL][/red]")
                _console.print(f"\n[red]Error: Fabric connection failed[/red]")
                _console.print(f"[yellow]Cause:[/yellow] API returned unproductive status")
                _console.print(f"[blue]Fix:[/blue] Verify FABRIC_CLIENT_ID and CLIENT_SECRET have correct permissions.")
        except Exception as e:
            _console.print("[red][FAIL][/red]")
            _console.print(f"\n[red]Error: Fabric connection failed[/red]")
            _console.print(f"[yellow]Cause:[/yellow] {str(e)}")
            _console.print(f"[blue]Fix:[/blue] Verify FABRIC_TENANT_ID and CLIENT_ID are correct.")

            if settings.logging.level == "DEBUG":
                import traceback
                traceback.print_exc()

        _console.print()

    @app.command()
    def list_fabric_models():
        """List all semantic models in the Fabric workspace."""
        show_banner(_console)

        settings = get_settings()

        _console.print(Panel.fit(
            f"[bold]Fabric Semantic Models[/bold]\n"
            f"Workspace: {settings.fabric.workspace_id}",
            title="List Models",
        ))

        try:
            extractor = FabricExtractor(settings.fabric)
            models = extractor.list_semantic_models()

            if not models:
                _console.print("[yellow]No semantic models found in workspace.[/yellow]")
                return

            table = Table(title=f"Found {len(models)} Models", show_header=True)
            table.add_column("#", style="dim", width=3)
            table.add_column("ID (use this for --dataset-id)", style="cyan", no_wrap=True)
            table.add_column("Display Name", style="green")
            table.add_column("Description", style="white", max_width=40)

            for i, model in enumerate(models, 1):
                table.add_row(
                    str(i),
                    model.get("id", "N/A"),
                    model.get("displayName", "N/A"),
                    (model.get("description", "") or "")[:40]
                )

            _console.print(table)
            _console.print("\n[dim]Use the ID column value with: python main.py reverse-sync --dataset-id <ID>[/dim]")

        except Exception as e:
            _console.print(f"[red]Error: {e}[/red]")
