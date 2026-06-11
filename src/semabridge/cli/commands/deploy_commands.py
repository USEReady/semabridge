"""Deploy/sync CLI commands: sync, semantic-sync, sync-measures, compare."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from semabridge.core.settings import get_settings
from semabridge.core.run_helpers import elapsed_ms, normalize_sync_mode, resolve_run_status
from semabridge.connectors.fabric_extractor import FabricExtractor
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter, MissingSourceTableWarning
from semabridge.repository.model_repository import ModelRepository
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


def _run_snowflake_to_fabric(
    settings,
    name,
    dry_run,
    output_dir,
    console: Console,
    parallel=False,
    include_tables: Optional[str] = None,
    semantic_view: Optional[str] = None,
):
    model_name = name or settings.model.name
    semantic_view_name = semantic_view.strip() if semantic_view and semantic_view.strip() else None
    runtime_include_tables = None
    if include_tables and include_tables.strip():
        runtime_include_tables = [t.strip().upper() for t in include_tables.split(",") if t.strip()]
    else:
        runtime_include_tables = settings.model.included_table_list

    console.print(Panel.fit(
        f"[bold]Deploy: Snowflake -> Fabric[/bold]\n"
        f"Model: {model_name}\n"
        f"{f'Semantic View: {semantic_view_name}' if semantic_view_name else 'Extraction: Table metadata mode'}\n"
        f"{'[yellow]DRY RUN - No publish[/yellow]' if dry_run else 'Publishing to Fabric'}",
        title="Deploy",
    ))

    start_time = time.time()
    run_id = str(uuid.uuid4())

    # Import command logger
    from semabridge.repository.command_logger import get_command_logger, CommandType, ActionType
    cmd_logger = get_command_logger()
    log_entry = cmd_logger.log_start(
        command=CommandType.DEPLOY,
        action_type=ActionType.DEPLOYMENT,
        project_id=model_name,
        details={"source": "snowflake", "target": "fabric", "dry_run": dry_run}
    )

    try:
        from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
        from semabridge.connectors.relationship_detector import RelationshipDetector
        from semabridge.connectors.measure_detector import MeasureDetector
        from semabridge.sml.assembler import SMLAssembler
        from semabridge.sml.serializer import SMLSerializer
        from semabridge.connectors.tmsl_generator import TMSLGenerator
        from semabridge.connectors.fabric_publisher import FabricPublisher
        from semabridge.utils.cache import MetadataCache
        from semabridge.connectors.inference_engine import SmlInferenceEngine
        from semabridge.converter.semantic_view_to_osi import SemanticViewToOSIConverter
        from semabridge.converter.osi_to_sml import OSIToSMLConverter

        # Step 1: Extract
        console.print("\n[bold cyan]Step 1/4: Extracting Snowflake metadata...[/bold cyan]")
        cache = MetadataCache(settings.model.cache_dir) if settings.model.cache_enabled else None
        extractor = SnowflakeExtractor(
            config=settings.snowflake,
            cache=cache,
            exclude_tables=settings.model.excluded_table_list,
            include_tables=runtime_include_tables,
        )
        concurrency_cfg = settings.concurrency
        use_parallel = parallel or concurrency_cfg.enable_parallel
        max_workers = concurrency_cfg.max_workers
        if semantic_view_name:
            console.print(f"  [dim]Semantic view mode enabled for: {semantic_view_name}[/dim]")
            ddl = extractor.extract_semantic_view_ddl(semantic_view_name)
            converter = SemanticViewToOSIConverter()
            table_map = converter._parse_tables_clause(ddl)
            semantic_tables = sorted({v.get("table_name", "").upper() for v in table_map.values() if v.get("table_name")})
            console.print(f"  [green][OK][/green] Parsed semantic view DDL with {len(semantic_tables)} base table(s)")
            if semantic_tables:
                console.print(f"  [dim]Semantic view base tables: {', '.join(semantic_tables)}[/dim]")

            # Pull column metadata for only the semantic-view referenced tables.
            scoped_extractor = SnowflakeExtractor(
                config=settings.snowflake,
                cache=cache,
                exclude_tables=settings.model.excluded_table_list,
                include_tables=semantic_tables if semantic_tables else None,
            )
            metadata = scoped_extractor.extract_all(parallel=use_parallel, max_workers=max_workers)
            col_meta = {
                t: metadata.get("columns", {}).get(t, [])
                for t in metadata.get("tables", {})
            }
            osi_model = converter.to_osi(
                {
                    "ddl": ddl,
                    "view_name": semantic_view_name,
                    "column_metadata": col_meta,
                }
            )
            console.print(
                f"  [green][OK][/green] Extracted OSI from semantic view: "
                f"{len(osi_model.datasets)} dataset(s), "
                f"{len(osi_model.dimensions)} dimension(s), "
                f"{len(osi_model.metrics)} metric(s), "
                f"{len(osi_model.relationships)} relationship(s)"
            )

            console.print("\n[bold cyan]Step 2/4: Building SML model...[/bold cyan]")
            sml_model = OSIToSMLConverter().from_osi(osi_model)
        else:
            metadata = extractor.extract_all(parallel=use_parallel, max_workers=max_workers)
            semantic_data = extractor.read_semantic_tables()
            extracted_tables = len(metadata.get("tables", {}))
            console.print(f"  [green][OK][/green] Extracted {extracted_tables} tables")
            if runtime_include_tables:
                console.print(f"  [dim]Applied include filter: {', '.join(runtime_include_tables)}[/dim]")

            if extracted_tables == 0:
                raise ValueError(
                    "No tables matched extraction. If you provided a semantic model/view name, "
                    "use --semantic-view <view_name> instead of --include-tables."
                )

            # Step 2: Build SML
            console.print("\n[bold cyan]Step 2/4: Building SML model...[/bold cyan]")
            assembler = SMLAssembler(
                model_name=model_name,
                description=settings.model.description,
                source_database=metadata.get("database", ""),
                source_schema=metadata.get("schema", ""),
            )

            # Add tables
            for table_name, table_info in metadata.get("tables", {}).items():
                columns = metadata.get("columns", {}).get(table_name, [])
                assembler.add_table(
                    table_name=table_name,
                    columns=columns,
                    description=table_info.get("description", ""),
                    row_count=table_info.get("row_count"),
                )

            # Detect relationships
            rel_detector = RelationshipDetector(
                tables=metadata.get("tables", {}),
                columns=metadata.get("columns", {}),
                primary_keys=metadata.get("primary_keys", {}),
                explicit_fks=metadata.get("foreign_keys", []),
            )
            relationships = rel_detector.detect_all()
            for rel in relationships:
                assembler.add_relationship(rel["name"], rel["from_table"], rel["from_column"], rel["to_table"], rel["to_column"])

            # Semantic Inference
            engine = SmlInferenceEngine(
                tables=metadata.get("tables", {}),
                columns=metadata.get("columns", {}),
                relationships=relationships,
                primary_keys=metadata.get("primary_keys", {})
            )
            scores = engine.classify()
            classification_map = {}
            for ds in assembler._datasets:
                score = scores.get(ds.unique_name)
                if score:
                    classification = score.classification
                    classification_map[ds.unique_name] = classification
                    if classification == "FACT":
                        ds.is_fact = True
                    elif classification == "TIME":
                        ds.is_fact = False
                    else:
                        ds.is_fact = False

            # Measures
            measure_detector = MeasureDetector(
                tables=metadata.get("tables", {}),
                columns=metadata.get("columns", {}),
                relationships=relationships,
            )
            all_measures = measure_detector.detect_all_measures(classification=classification_map)
            for table_name, measures in all_measures.items():
                for measure in measures[:5]:
                    assembler.add_metric(measure["name"], table_name, measure["column"], measure["aggregation"])

            # Semantic Data
            for measure in semantic_data.get("measures", []):
                assembler.add_metric(
                    name=measure["name"],
                    dataset=measure["table_name"],
                    source_column="",
                    expression=measure["expression"],
                    description=measure.get("description", ""),
                )

            sml_model = assembler.build()

            # Architecture Compliance: Pass through OSI Intermediate
            from semabridge.converter.sml_to_osi import SMLToOSIConverter
            sml_to_osi = SMLToOSIConverter()
            osi_to_sml = OSIToSMLConverter()
            osi_model = sml_to_osi.to_osi(sml_model)
            sml_model = osi_to_sml.from_osi(osi_model)

        # Keep deployment model name deterministic from CLI option.
        sml_model.unique_name = model_name
        sml_model.label = model_name

        # Normalize relationships to avoid ambiguous paths (multiple paths between same tables)
        from semabridge.core.execution_engine import ExecutionEngine
        engine = ExecutionEngine()
        engine._normalize_relationships_for_target(sml_model)

        total_columns = sum(len(ds.columns) for ds in sml_model.datasets)
        total_measures = len(sml_model.metrics)
        total_dim_attrs = sum(len(dim.attributes) for dim in sml_model.dimensions)
        if total_columns == 0:
            raise ValueError(
                "No columns were extracted for deployment. Ensure the selected semantic view/tables exist "
                "and include column definitions."
            )

        output_dir.mkdir(parents=True, exist_ok=True)
        sml_path = output_dir / "sml" / "model.yaml"
        SMLSerializer.save(sml_model, sml_path)
        console.print(
            f"  [green][OK][/green] Built OSI-compatible SML model with {sml_model.dataset_count} dataset(s), "
            f"{total_columns} column(s), {total_measures} measure(s), {len(sml_model.relationships)} relationship(s)"
        )
        console.print(
            f"  [dim]Mapping check: Snowflake dimension attributes={total_dim_attrs} -> Fabric columns={total_columns}; "
            f"Snowflake metrics={total_measures} -> Fabric measures={total_measures}[/dim]"
        )

        # Step 3: Versioning
        console.print("\n[bold cyan]Step 3/4: Versioning in DuckDB...[/bold cyan]")
        # Ensure legacy DuckDBManager does not collide with an active
        # SQLAlchemy DuckDB engine created earlier in this run (e.g., cache).
        try:
            from semabridge.repository.orm.session_factory import db_manager
            db_manager.dispose()
        except Exception as _dispose_err:
            logger.debug(f"Non-fatal DB engine dispose warning: {_dispose_err}")
        db_manager = ModelRepository()
        db_manager.ensure_project(model_name, sml_model.label, settings.fabric.workspace_id, adapter="snowflake")
        sml_dict = sml_model.model_dump(mode='json')
        committed, snapshot_id = db_manager.commit_model(
            project_id=model_name,
            sml_json=sml_dict,
            run_id=run_id,
            initiated_by="cli"
        )

        # Step 4: Generate
        console.print("\n[bold cyan]Step 4/5: Generating model.bim...[/bold cyan]")
        generator = TMSLGenerator(
            sml_model,
            snowflake_server=settings.snowflake.account,
            snowflake_warehouse=settings.snowflake.warehouse,
            snowflake_database=settings.snowflake.database,
            snowflake_schema=settings.snowflake.schema_name,
        )
        bim_path = output_dir / "model.bim"
        generator.save(bim_path)
        console.print(f"  [green][OK][/green] Generated {bim_path}")

        # Step 5: Publish
        if dry_run:
            console.print("\n[bold yellow]Step 5/5: SKIPPED (dry run)[/bold yellow]")
        else:
            console.print("\n[bold cyan]Step 5/5: Publishing to Fabric...[/bold cyan]")
            publisher = FabricPublisher(settings.fabric)
            publish_name = (
                sml_model.label
                or sml_model.unique_name
                or model_name
            )
            result = publisher.publish(
                sml_model=sml_model,
                model_name=publish_name,
                snowflake_server=settings.snowflake.account,
                snowflake_warehouse=settings.snowflake.warehouse,
                snowflake_database=settings.snowflake.database,
                snowflake_schema=settings.snowflake.schema_name,
                overwrite=True,
            )
            console.print(f"  [green][OK][/green] Published to Fabric (ID: {result.get('id')})")

        duration = elapsed_ms(start_time)
        db_manager.commit_model(project_id=model_name, sml_json=sml_dict, status="success", duration_ms=duration, run_id=run_id)
        cmd_logger.log_success(log_entry, duration, {"snapshot_id": snapshot_id})
        console.print(f"\n[green][OK] Deploy complete![/green]")

    except Exception as e:
        duration = elapsed_ms(start_time)
        console.print(f"\n[red]Error: Snowflake -> Fabric deployment failed[/red]")
        console.print(f"[yellow]Cause:[/yellow] {str(e)}")
        console.print(f"[blue]Fix:[/blue] Check network connectivity and verify Fabric API permissions.")

        cmd_logger.log_failure(log_entry, str(e), duration)
        try:
            db_manager = ModelRepository()
            db_manager.commit_model(project_id=model_name, sml_json={}, status="failed", duration_ms=duration, error_message=str(e), run_id=run_id)
        except Exception:
            pass

        if settings.logging.level == "DEBUG":
            import traceback
            traceback.print_exc()
        raise typer.Exit(code=1)


def _run_fabric_to_snowflake(settings, dataset_id, workspace_id, tag, sync, console: Console, parallel=False, auto_enrich=False):
    ws_id = workspace_id or settings.fabric.workspace_id
    from semabridge.core.behavior import ConnectorBehavior
    from semabridge.core.config_loader import get_project_file_path

    behavior = ConnectorBehavior()
    behavior_path = get_project_file_path("behavior.yaml")
    if behavior_path.exists():
        try:
            behavior = ConnectorBehavior.from_yaml(behavior_path)
        except Exception as be:
            logger.warning(f"Failed to parse behavior.yaml, using defaults: {be}")

    if auto_enrich:
        behavior.snowflake.auto_create_enriched_view = True
        behavior.snowflake.auto_execute_precompute = True
        behavior.snowflake.use_enriched_view_for_metrics = True

    console.print(Panel.fit(
        f"[bold]Deploy: Fabric -> Snowflake[/bold]\n"
        f"Dataset: {dataset_id}\n"
        f"Workspace: {ws_id}\n"
        f"Sync Views: {sync}",
        title="Deploy",
    ))

    start_time = time.time()
    run_id = str(uuid.uuid4())

    from semabridge.repository.command_logger import get_command_logger, CommandType, ActionType
    cmd_logger = get_command_logger()
    log_entry = cmd_logger.log_start(
        command=CommandType.DEPLOY,
        action_type=ActionType.DEPLOYMENT,
        project_id=dataset_id,
        details={"source": "fabric", "target": "snowflake", "sync": sync}
    )

    try:
        from semabridge.connectors.fabric_extractor import FabricExtractor
        from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
        from semabridge.converter.osi_to_sml import OSIToSMLConverter
        from semabridge.sml.serializer import SMLSerializer

        # Step 1: Extract
        if behavior.features.offline_mode:
            offline_path = Path(behavior.features.offline_fabric_model_path)
            console.print("\n[bold cyan]Step 1/4: Loading local Fabric model (OFFLINE mode)...[/bold cyan]")
            if not offline_path.exists():
                raise FileNotFoundError(
                    f"offline_mode enabled but file not found: {offline_path}"
                )
            with open(offline_path, "r", encoding="utf-8") as f:
                tmsl = json.load(f)
            if isinstance(tmsl, dict) and "model" not in tmsl and "tables" in tmsl:
                tmsl = {"model": tmsl}
            row_counts = {}
            table_count = len(tmsl.get("model", {}).get("tables", []))
            console.print(f"  [green][OK][/green] Loaded {table_count} tables from {offline_path}")
        else:
            console.print("\n[bold cyan]Step 1/4: Extracting from Fabric...[/bold cyan]")
            extractor = FabricExtractor(settings.fabric)
            tmsl = extractor.get_model_definition(dataset_id)

            console.print("[dim]Fetching table statistics...[/dim]", end="")
            row_counts = extractor.get_table_row_counts(dataset_id)
            console.print(f" [green][OK] ({len(row_counts)} tables)[/green]")

        # Step 2: Transform
        console.print("\n[bold cyan]Step 2/4: Transforming to SML (via OSI)...[/bold cyan]")

        # 2a. TMSL -> OSI
        tmsl_converter = TMSLToOSIConverter()
        source_data = {
            "tmsl": tmsl,
            "workspace_id": ws_id,
            "dataset_id": dataset_id
        }
        osi_model = tmsl_converter.to_osi(source_data)
        console.print(f"  [dim]Converted to OSI ({len(osi_model.metrics)} metrics)[/dim]")

        # 2b. OSI -> SML
        osi_sml_converter = OSIToSMLConverter()
        sml_model = osi_sml_converter.from_osi(osi_model)
        console.print(f"  [green][OK][/green] Transformed to SML ({sml_model.metric_count} metrics)")

        # Step 3: Version Control
        console.print("\n[bold cyan]Step 3/4: Versioning in DuckDB...[/bold cyan]")
        # Ensure legacy DuckDBManager does not collide with an active
        # SQLAlchemy DuckDB engine created earlier in this run.
        try:
            from semabridge.repository.orm.session_factory import db_manager
            db_manager.dispose()
        except Exception as _dispose_err:
            logger.debug(f"Non-fatal DB engine dispose warning: {_dispose_err}")
        db_manager = ModelRepository()
        db_manager.ensure_project(dataset_id, sml_model.label, ws_id, adapter="fabric")

        sml_dict = sml_model.model_dump(mode='json')
        duration = elapsed_ms(start_time)
        committed, snapshot_id = db_manager.commit_model(
            project_id=dataset_id,
            sml_json=sml_dict,
            tag=tag,
            status="success",
            duration_ms=duration,
            run_id=run_id
        )
        if committed:
            console.print(f"  [green][OK][/green] Committed: {snapshot_id}")
        else:
            console.print(f"  [yellow]No changes detected[/yellow]")

        # Step 4: Emission/Sync
        console.print("\n[bold cyan]Step 4/4: Syncing to Snowflake...[/bold cyan]")
        concurrency_cfg = settings.concurrency
        use_parallel = parallel or concurrency_cfg.enable_parallel
        max_workers = concurrency_cfg.max_workers
        emitter = SnowflakeEmitter(settings.snowflake, behavior=behavior)
        output_dir = Path("output/reverse")
        output_dir.mkdir(parents=True, exist_ok=True)

        # Persist SML artifact for inspection and downstream reuse.
        sml_artifact_path = output_dir / "sml" / "model.yaml"
        SMLSerializer.save(sml_model, sml_artifact_path)

        ddls = emitter.generate_ddls(sml_model)
        full_ddl = "\n\n".join(ddls)
        yaml_out = emitter.generate_cortex_yaml(sml_model)

        with open(output_dir / "semantic_view.sql", "w") as f:
            f.write(full_ddl)
        with open(output_dir / "cortex_analyst.yaml", "w") as f:
            f.write(yaml_out)
        console.print(f"  [green][OK][/green] Artifacts generated in {output_dir}")
        console.print(f"  [dim]SML artifact: {sml_artifact_path}[/dim]")

        if sync:
            console.print("  Deploying to Snowflake...")
            try:
                emitter.deploy(
                    sml_model,
                    parallel=use_parallel,
                    max_workers=max_workers
                )
                try:
                    from semabridge.converter.sml_to_osi import SMLToOSIConverter
                    import yaml

                    osi_model = SMLToOSIConverter().to_osi(sml_model)
                    inferred_json = Path("output") / "osi_inferred.json"
                    inferred_yaml = Path("output") / "osi_inferred.yaml"
                    with open(inferred_json, "w", encoding="utf-8") as jf:
                        json.dump(osi_model.model_dump(mode="json"), jf, indent=2)
                    with open(inferred_yaml, "w", encoding="utf-8") as yf:
                        yaml.safe_dump(osi_model.model_dump(mode="json"), yf, sort_keys=False)
                    console.print(f"  [green][OK][/green] Inferred OSI artifacts: {inferred_json}, {inferred_yaml}")
                except Exception as ex:
                    logger.warning(f"Failed to export inferred OSI artifacts (non-fatal): {ex}")
                console.print("  [green][OK][/green] Semantic Views updated")
            except MissingSourceTableWarning as w:
                console.print("\n  [yellow]⚠ Missing underlying table detected:[/yellow]")
                console.print(f"    [yellow]{str(w)}[/yellow]")
                console.print("  [green][OK][/green] Semantic Views updated")

        else:
            console.print("  [yellow]Skipping sync (use --dry-run=false to execute, default is sync)[/yellow]")

        duration = elapsed_ms(start_time)
        cmd_logger.log_success(log_entry, duration, {"snapshot_id": snapshot_id})
        console.print(f"\n[green][OK] Deploy complete![/green]")

    except Exception as e:
        duration = elapsed_ms(start_time)
        console.print(f"\n[red]Error: Fabric -> Snowflake deployment failed[/red]")
        console.print(f"[yellow]Cause:[/yellow] {str(e)}")
        console.print(f"[blue]Fix:[/blue] Check Snowflake warehouse status and verify Fabric workspace accessibility.")

        cmd_logger.log_failure(log_entry, str(e), duration)
        try:
            db_manager = ModelRepository()
            head = db_manager.get_head(dataset_id)
            sml_json = head.sml_blob if head else {}
            db_manager.commit_model(project_id=dataset_id, sml_json=sml_json, tag=tag, status="failed", duration_ms=duration, error_message=str(e), run_id=run_id)
        except Exception:
            pass

        if settings.logging.level == "DEBUG":
            import traceback
            traceback.print_exc()
        raise typer.Exit(code=1)


def register_deploy_commands(app: typer.Typer, console: Console, show_banner) -> None:
    """Register deploy/sync commands onto *app*."""

    @app.command(name="sync-measures")
    def sync_measures(
        dataset_id: str = typer.Option(..., "--dataset-id", "-d", help="Fabric Dataset ID to sync measures from"),
        workspace_id: Optional[str] = typer.Option(None, "--workspace-id", "-w", help="Optional Fabric Workspace ID override"),
        measures: Optional[str] = typer.Option(None, "--measures", "-m", help="Comma-separated measure names to sync (default: all syncable)"),
        dimensions: Optional[str] = typer.Option(None, "--dimensions", help="Override dimensions for evaluation context"),
        partition_by: Optional[str] = typer.Option("'Date'[Year]", "--partition-by", help="Partition dimension for large datasets"),
        dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be synced without executing"),
    ):
        """
        Sync complex DAX measures from Fabric to Snowflake.

        Evaluates DAX measures via Fabric API and writes results to
        MEASURES_<name> tables in Snowflake for use in Cortex Analyst.

        This is useful for measures that cannot be translated to SQL
        (Time Intelligence, CALCULATE with filters, etc.)

        Example:
            semabridge sync-measures --dataset-id abc-123
            semabridge sync-measures --dataset-id abc-123 --measures "Sales YTD,Revenue MTD"
        """
        show_banner()

        settings = get_settings()

        if workspace_id:
            settings.fabric.workspace_id = workspace_id

        console.print(Panel.fit(
            f"[bold]Sync DAX Measures[/bold]\n"
            f"Dataset: {dataset_id}\n"
            f"Target: Snowflake {settings.snowflake.database}.{settings.snowflake.schema_name}",
            title="Measure Sync",
        ))

        try:
            # Extract model definition
            console.print("\n[bold cyan]Step 1/3: Extracting model definition...[/bold cyan]")
            extractor = FabricExtractor(settings.fabric)

            # Get model info
            fabric_source = extractor.get_model_definition(dataset_id=dataset_id)
            if not fabric_source:
                console.print("[red]Error: Could not extract model definition[/red]")
                raise typer.Exit(code=1)

            # Convert to SML (via OSI) to get measure metadata
            console.print("\n[bold cyan]Step 2/3: Analyzing measures (via OSI)...[/bold cyan]")
            from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
            from semabridge.converter.osi_to_sml import OSIToSMLConverter

            tmsl_converter = TMSLToOSIConverter()
            source_data = {
                "tmsl": fabric_source,
                "workspace_id": settings.fabric.workspace_id,
                "dataset_id": dataset_id
            }
            osi_model = tmsl_converter.to_osi(source_data)

            osi_sml_converter = OSIToSMLConverter()
            sml_model = osi_sml_converter.from_osi(osi_model)

            # Filter to syncable measures
            syncable = [m for m in sml_model.metrics if m.sync_enabled and not m.is_hidden]

            # Apply measure filter if specified
            if measures:
                measure_names = [m.strip() for m in measures.split(",")]
                syncable = [m for m in syncable if m.unique_name in measure_names]

            console.print(f"  Found {len(sml_model.metrics)} total measures")
            console.print(f"  Syncable: {len(syncable)}")

            # Classification by tier
            tier_counts = {}
            for m in sml_model.metrics:
                tier = m.complexity_tier
                tier_counts[tier] = tier_counts.get(tier, 0) + 1

            tier_table = Table(title="Measure Complexity Analysis")
            tier_table.add_column("Tier", style="cyan")
            tier_table.add_column("Description", style="white")
            tier_table.add_column("Count", justify="right")
            tier_table.add_column("Status", style="green")

            tier_table.add_row("1", "Simple Aggregations", str(tier_counts.get(1, 0)), "[green]Translatable[/green]")
            tier_table.add_row("2", "Arithmetic/Branching", str(tier_counts.get(2, 0)), "[green]Translatable[/green]")
            tier_table.add_row("3", "Time Intelligence", str(tier_counts.get(3, 0)), "[yellow]Window Functions[/yellow]")
            tier_table.add_row("4", "Complex (unsupported)", str(tier_counts.get(4, 0)), "[red]Requires Sync[/red]")

            console.print(tier_table)

            if dry_run:
                console.print("\n[yellow]Dry run - no data synced[/yellow]")

                # List measures that would be synced
                if syncable:
                    console.print("\n[bold]Measures to sync:[/bold]")
                    for m in syncable:
                        dims = m.group_by_dimensions or ["'Date'[Year]"]
                        console.print(f"  • {m.unique_name} (Tier {m.complexity_tier}, dims: {len(dims)})")
                return

            # Step 3: Execute sync
            console.print("\n[bold cyan]Step 3/3: Syncing measures to Snowflake...[/bold cyan]")

            emitter = SnowflakeEmitter(settings.snowflake)

            results = emitter.sync_all_measures(
                sml=sml_model,
                fabric_extractor=extractor,
                dataset_id=dataset_id,
            )

            # Display results
            result_table = Table(title="Sync Results")
            result_table.add_column("Measure", style="cyan")
            result_table.add_column("Status", style="white")
            result_table.add_column("Rows", justify="right")
            result_table.add_column("Details", style="dim")

            for name, result in results.items():
                status = result.get("status", "unknown")
                rows = result.get("rows", 0)
                error = result.get("error", "")

                status_display = {
                    "success": "[green]✓ Success[/green]",
                    "failed": "[red]✗ Failed[/red]",
                    "empty": "[yellow]○ Empty[/yellow]",
                    "skipped": "[dim]- Skipped[/dim]",
                }.get(status, status)

                result_table.add_row(
                    name,
                    status_display,
                    str(rows) if status == "success" else "-",
                    error[:40] if error else "",
                )

            console.print(result_table)

            # Summary
            success = sum(1 for r in results.values() if r.get("status") == "success")
            failed = sum(1 for r in results.values() if r.get("status") == "failed")
            total_rows = sum(r.get("rows", 0) for r in results.values())

            console.print(f"\n[bold]Summary:[/bold] {success} success, {failed} failed, {total_rows:,} total rows")

            if success > 0:
                console.print(f"\n[green]✓ Measures synced to MEASURES_* tables in Snowflake[/green]")

        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            import traceback
            console.print(f"[dim]{traceback.format_exc()}[/dim]")
            raise typer.Exit(code=1)

    @app.command("semantic-sync")
    def semantic_sync(
        source: Optional[str] = typer.Argument(None, help="Source platform (snowflake/fabric). Optional if defined in config."),
        target: Optional[str] = typer.Argument(None, help="Target platform (snowflake/fabric). Inferred if omitted."),
        dataset_id: Optional[str] = typer.Option(None, "--dataset-id", "-d", help="Fabric Dataset ID (Required if converting from Fabric)"),
        workspace_id: Optional[str] = typer.Option(None, "--workspace-id", "-w", help="Fabric Workspace ID"),
        tag: Optional[str] = typer.Option(None, "--tag", "-t", help="Version tag"),
        name: Optional[str] = typer.Option(None, "--name", "-n", help="Override model name"),
        dry_run: bool = typer.Option(False, "--dry-run", help="Skip final publishing"),
        semantic_view: Optional[str] = typer.Option(
            None,
            "--semantic-view",
            help="Snowflake semantic view name to extract and sync",
        ),
        include_tables: Optional[str] = typer.Option(
            None,
            "--include-tables",
            help="Comma-separated Snowflake tables to include for this run only",
        ),
        output_dir: Path = typer.Option(Path("output"), "--output-dir", "-o", help="Output directory"),
        parallel: bool = typer.Option(False, "--parallel", "-p", help="Enable concurrent extraction and deployment"),
        auto_enrich: bool = typer.Option(False, "--auto-enrich", help="Automatically enrich Snowflake tables with pre-computed columns"),
    ):
        """
        Synchronize semantic model between platforms.

        Flows:
        source='snowflake' (target='fabric') -> Extracts from Snowflake, builds SML, deploys to Fabric.
        source='fabric' (target='snowflake') -> Extracts from Fabric, versions in DuckDB, syncs Snowflake views.

        Usage:
        semabridge semantic-sync snowflake        # Sync Snowflake -> Fabric
        semabridge semantic-sync fabric           # Sync Fabric -> Snowflake
        semabridge semantic-sync snowflake fabric # Explicit
        """
        show_banner()
        settings = get_settings()

        # Load config to backfill missing parameters
        from semabridge.core.config_loader import get_default_config_path, load_yaml_file
        config_path = get_default_config_path()

        if config_path:
            try:
                config = load_yaml_file(config_path)

                # If source not provided in CLI, try to get from config
                if not source and "source" in config and isinstance(config["source"], dict) and "type" in config["source"]:
                    source = config["source"]["type"]
                    console.print(f"[dim]Inferred source from config: {source}[/dim]")

                # Backfill dataset_id if missing and source is fabric
                if not dataset_id and "source" in config and isinstance(config["source"], dict):
                    if source and source.lower() == "fabric":
                        # Check for explicit dataset_id first
                        if "dataset_id" in config["source"]:
                            dataset_id = config["source"]["dataset_id"]
                            console.print(f"[dim]Using dataset ID from config: {dataset_id}[/dim]")
                        # Fall back to models list (multi-model config)
                        elif "models" in config["source"] and config["source"]["models"]:
                            models_list = config["source"]["models"]
                            dataset_id = models_list[0]
                            console.print(f"[dim]Using first model from config: {dataset_id} ({len(models_list)} models available)[/dim]")

                # Backfill target if missing
                if not target and "target" in config and isinstance(config["target"], dict) and "type" in config["target"]:
                    target = config["target"]["type"]
                    console.print(f"[dim]Inferred target from config: {target}[/dim]")

                # Allow model name override from config if not CLI specified
                if not name and "model_name" in config:
                    name = config["model_name"]

                # Allow version tag override from config if not CLI specified
                if not tag and "version_tag" in config:
                    tag = str(config["version_tag"])
                    console.print(f"[dim]Using version tag from config: {tag}[/dim]")

            except Exception as e:
                console.print(f"[yellow]Warning: Failed to load config: {e}[/yellow]")

        if not source:
            console.print("[red]Error: Source platform not specified and could not be found in config.[/red]")
            raise typer.Exit(code=1)

        source = source.lower()

        if target:
            target = target.lower()
        else:
            # Infer target
            if source == "snowflake":
                target = "fabric"
                console.print(f"[dim]Inferring target: {target}[/dim]")
            elif source == "fabric":
                target = "snowflake"
                console.print(f"[dim]Inferring target: {target}[/dim]")
            else:
                console.print(f"[red]Error: Could not infer target for source '{source}'. Please specify target.[/red]")
                raise typer.Exit(code=1)

        if source == "snowflake" and target == "fabric":
            _run_snowflake_to_fabric(
                settings,
                name,
                dry_run,
                output_dir,
                console,
                parallel=parallel,
                semantic_view=semantic_view,
                include_tables=include_tables,
            )
        elif source == "fabric" and target == "snowflake":
            from semabridge.core.behavior import ConnectorBehavior
            from semabridge.core.config_loader import get_project_file_path
            behavior = ConnectorBehavior()
            behavior_path = get_project_file_path("behavior.yaml")
            if behavior_path.exists():
                try:
                    behavior = ConnectorBehavior.from_yaml(behavior_path)
                except Exception:
                    pass

            if not dataset_id and behavior.features.offline_mode:
                dataset_id = name or "offline_model"
                console.print(f"[dim]offline_mode enabled - using dataset id: {dataset_id}[/dim]")

            if not dataset_id:
                console.print("[red]Error: --dataset-id is required when source is 'fabric'[/red]")
                raise typer.Exit(code=1)
            sync_to_snowflake = not dry_run
            _run_fabric_to_snowflake(settings, dataset_id, workspace_id, tag, sync_to_snowflake, console, parallel=parallel, auto_enrich=auto_enrich)
        else:
            console.print(f"[red]Error: Unsupported flow from {source} to {target}[/red]")
            raise typer.Exit(code=1)

    @app.command("sync")
    def sync(
        source: Optional[str] = typer.Argument(None, help="Source platform (snowflake/fabric). Optional if defined in config."),
        target: Optional[str] = typer.Argument(None, help="Target platform (snowflake/fabric). Inferred if omitted."),
        dataset_id: Optional[str] = typer.Option(None, "--dataset-id", "-d", help="Fabric Dataset ID (Required if converting from Fabric)"),
        workspace_id: Optional[str] = typer.Option(None, "--workspace-id", "-w", help="Fabric Workspace ID"),
        tag: Optional[str] = typer.Option(None, "--tag", "-t", help="Version tag"),
        name: Optional[str] = typer.Option(None, "--name", "-n", help="Override model name"),
        dry_run: bool = typer.Option(False, "--dry-run", help="Skip final publishing"),
        semantic_view: Optional[str] = typer.Option(
            None,
            "--semantic-view",
            help="Snowflake semantic view name to extract and sync",
        ),
        include_tables: Optional[str] = typer.Option(
            None,
            "--include-tables",
            help="Comma-separated Snowflake tables to include for this run only",
        ),
        output_dir: Path = typer.Option(Path("output"), "--output-dir", "-o", help="Output directory"),
        parallel: bool = typer.Option(False, "--parallel", "-p", help="Enable concurrent extraction and deployment"),
        auto_enrich: bool = typer.Option(False, "--auto-enrich", help="Automatically enrich Snowflake tables with pre-computed columns"),
    ):
        """
        Core sync function.

        WARNING:
        Do not modify this function directly.
        All enhancements must wrap this function externally.
        """
        semantic_sync(
            source=source,
            target=target,
            dataset_id=dataset_id,
            workspace_id=workspace_id,
            tag=tag,
            name=name,
            dry_run=dry_run,
            semantic_view=semantic_view,
            include_tables=include_tables,
            output_dir=output_dir,
            parallel=parallel,
            auto_enrich=auto_enrich,
        )

    @app.command()
    def compare(
        source: str = typer.Argument(..., help="Source environment (snowflake/fabric)"),
        target: str = typer.Argument(..., help="Target environment (snowflake/fabric)"),
        dataset_id: str = typer.Option(..., "--dataset-id", "-d", help="Fabric Dataset ID or Name"),
        format: str = typer.Option("cli", "--format", "-f", help="Output format: cli, json, html"),
        output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file for report"),
    ):
        """
        Compare semantic models (e.g. Source vs Repository).
        """
        show_banner()
        source = source.lower()
        target = target.lower()

        # 1. Resolve Dataset ID
        db_manager = ModelRepository()
        conn = db_manager._get_connection()
        try:
            results = conn.execute("SELECT project_id, name FROM projects").fetchall()
        finally:
            conn.close()

        matched_id = dataset_id

        # Check exact ID match
        if any(r[0] == dataset_id for r in results):
            matched_id = dataset_id
        else:
            # Search by name or ID
            search = dataset_id.lower()
            matches = []
            for pid, name in results:
                # Check ID
                if search == pid.lower():
                    matches = [(pid, name)]
                    break
                # Check Name
                if name and name.lower() == search:
                    matches = [(pid, name)]
                    break

                # Substrings
                match_id = search in pid.lower()
                match_name = name and search in name.lower()

                if match_id or match_name:
                    matches.append((pid, name))

            if len(matches) == 1:
                matched_id = matches[0][0]
                console.print(f"[dim]Resolved project '{dataset_id}' to ID: {matched_id} ({matches[0][1]})[/dim]")
            elif len(matches) > 1:
                console.print(f"[red]Ambiguous project name '{dataset_id}'. Matches:[/red]")
                for m in matches:
                    console.print(f"  - {m[1]} ({m[0]})")
                raise typer.Exit(code=1)
            else:
                console.print(f"[red]Project '{dataset_id}' not found.[/red]")
                raise typer.Exit(code=1)

        # 2. Invoke Diff
        from semabridge.cli.diff_commands import diff_source

        if source in ["snowflake", "fabric"]:
            diff_source(dataset_id=matched_id, source=source, format=format, output=output)
        else:
            console.print(f"[red]Unsupported source: {source}[/red]")
            raise typer.Exit(code=1)
