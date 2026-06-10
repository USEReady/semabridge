"""Pipeline CLI commands: extract, build, emit, publish."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from semabridge.core.settings import get_settings
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


def register_pipeline_commands(app: typer.Typer, console: Console, show_banner) -> None:
    """Register pipeline commands onto *app*."""

    @app.command()
    def extract(
        output: Path = typer.Option(
            Path("output/metadata.json"),
            "--output", "-o",
            help="Output file for extracted metadata",
        ),
        use_cache: bool = typer.Option(
            True,
            "--cache/--no-cache",
            help="Use incremental cache",
        ),
    ):
        """Extract metadata from Snowflake."""
        show_banner()

        settings = get_settings()

        console.print(Panel.fit(
            f"[bold]Extracting metadata from Snowflake[/bold]\n"
            f"Database: {settings.snowflake.database}\n"
            f"Schema: {settings.snowflake.schema_name}",
            title="Extract",
        ))

        from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
        from semabridge.utils.cache import MetadataCache

        cache = MetadataCache(settings.model.cache_dir) if use_cache else None

        extractor = SnowflakeExtractor(
            config=settings.snowflake,
            cache=cache,
            exclude_tables=settings.model.excluded_table_list,
            include_tables=settings.model.included_table_list,
        )

        metadata = extractor.extract_all()

        # Also read any existing semantic tables
        semantic_data = extractor.read_semantic_tables()
        metadata["semantic_tables"] = semantic_data

        # Save output
        output.parent.mkdir(parents=True, exist_ok=True)
        with open(output, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, default=str)

        console.print(f"\n[green][OK] Extracted metadata saved to {output}[/green]")
        console.print(f"  Tables: {len(metadata['tables'])}")
        console.print(f"  Foreign Keys: {len(metadata['foreign_keys'])}")

    @app.command()
    def build(
        input_path: Path = typer.Option(
            Path("output/metadata.json"),
            "--input", "-i",
            help="Input metadata file",
        ),
        output: Path = typer.Option(
            Path("output/sml"),
            "--output", "-o",
            help="Output directory for SML files",
        ),
        auto_detect: bool = typer.Option(
            True,
            "--auto-detect/--no-auto-detect",
            help="Auto-detect relationships, hierarchies, and measures",
        ),
    ):
        """Build SML model from extracted metadata."""
        show_banner()

        settings = get_settings()

        console.print(Panel.fit(
            f"[bold]Building SML model[/bold]\n"
            f"Input: {input_path}\n"
            f"Output: {output}",
            title="Build",
        ))

        # Load metadata
        with open(input_path, "r", encoding="utf-8") as f:
            metadata = json.load(f)

        from semabridge.sml.assembler import SMLAssembler
        from semabridge.sml.serializer import SMLSerializer
        from semabridge.connectors.relationship_detector import RelationshipDetector
        from semabridge.connectors.hierarchy_detector import HierarchyDetector
        from semabridge.connectors.measure_detector import MeasureDetector

        # Create assembler
        assembler = SMLAssembler(
            model_name=settings.model.name,
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

        if auto_detect:
            # Detect relationships
            rel_detector = RelationshipDetector(
                tables=metadata.get("tables", {}),
                columns=metadata.get("columns", {}),
                primary_keys=metadata.get("primary_keys", {}),
                explicit_fks=metadata.get("foreign_keys", []),
            )

            relationships = rel_detector.detect_all()
            for rel in relationships:
                assembler.add_relationship(
                    name=rel["name"],
                    from_table=rel["from_table"],
                    from_column=rel["from_column"],
                    to_table=rel["to_table"],
                    to_column=rel["to_column"],
                )

            # Detect hierarchies
            hier_detector = HierarchyDetector(
                tables=metadata.get("tables", {}),
                columns=metadata.get("columns", {}),
            )

            hierarchies = hier_detector.detect_all()
            for table_name, table_hierarchies in hierarchies.items():
                attributes = [
                    {"name": col["name"], "column": col["name"]}
                    for col in metadata.get("columns", {}).get(table_name, [])
                ]
                for h in table_hierarchies:
                    assembler.add_dimension(
                        name=h["name"],
                        dataset=table_name,
                        attributes=attributes,
                        hierarchies=[h],
                    )

            # Detect measures
            measure_detector = MeasureDetector(
                tables=metadata.get("tables", {}),
                columns=metadata.get("columns", {}),
                relationships=relationships,
            )

            fact_tables = set(measure_detector.detect_fact_tables())
            for dataset in assembler._datasets:
                if dataset.unique_name in fact_tables:
                    dataset.is_fact = True

            all_measures = measure_detector.detect_all_measures()
            for table_name, measures in all_measures.items():
                for measure in measures[:5]:  # Limit to 5 measures per table
                    assembler.add_metric(
                        name=measure["name"],
                        dataset=table_name,
                        source_column=measure["column"],
                        aggregation=measure["aggregation"],
                    )

        # Build and save
        sml_model = assembler.build()
        SMLSerializer.save(sml_model, output / "model.yaml")

        console.print(f"\n[green][OK] SML model saved to {output}[/green]")
        console.print(f"  Datasets: {sml_model.dataset_count}")
        console.print(f"  Dimensions: {sml_model.dimension_count}")
        console.print(f"  Metrics: {sml_model.metric_count}")
        console.print(f"  Relationships: {sml_model.relationship_count}")

    @app.command()
    def emit(
        input_path: Path = typer.Option(
            Path("output/sml/model.yaml"),
            "--input", "-i",
            help="Input SML file or directory",
        ),
        output: Path = typer.Option(
            Path("output/model.bim"),
            "--output", "-o",
            help="Output model.bim file",
        ),
    ):
        """Generate Fabric model.bim from SML."""
        show_banner()

        settings = get_settings()

        console.print(Panel.fit(
            f"[bold]Generating model.bim[/bold]\n"
            f"Input: {input_path}\n"
            f"Output: {output}",
            title="Emit",
        ))

        from semabridge.sml.serializer import SMLSerializer
        from semabridge.connectors.tmsl_generator import TMSLGenerator

        # Load SML
        sml_model = SMLSerializer.load(input_path)

        # Generate TMSL
        generator = TMSLGenerator(
            sml_model,
            snowflake_server=settings.snowflake.account,
            snowflake_warehouse=settings.snowflake.warehouse,
            snowflake_database=settings.snowflake.database,
            snowflake_schema=settings.snowflake.schema_name,
        )

        generator.save(output)

        summary = generator.get_summary()
        console.print(f"\n[green][OK] Model.bim generated: {output}[/green]")
        console.print(f"  Tables: {summary['tables']}")
        console.print(f"  Columns: {summary['total_columns']}")
        console.print(f"  Measures: {summary['measures']}")
        console.print(f"  Relationships: {summary['relationships']}")

    @app.command()
    def publish(
        input_path: Path = typer.Option(
            Path("output/sml/model.yaml"),
            "--input", "-i",
            help="Input SML file or directory",
        ),
        name: Optional[str] = typer.Option(
            None,
            "--name", "-n",
            help="Override model name",
        ),
        overwrite: bool = typer.Option(
            True,
            "--overwrite/--no-overwrite",
            help="Overwrite existing model",
        ),
    ):
        """Publish semantic model to Fabric."""
        show_banner()

        settings = get_settings()
        model_name = name or settings.model.name

        console.print(Panel.fit(
            f"[bold]Publishing to Fabric[/bold]\n"
            f"Model: {model_name}\n"
            f"Workspace: {settings.fabric.workspace_id}",
            title="Publish",
        ))

        from semabridge.sml.serializer import SMLSerializer
        from semabridge.connectors.fabric_publisher import FabricPublisher

        # Load SML
        sml_model = SMLSerializer.load(input_path)

        # Publish
        publisher = FabricPublisher(settings.fabric)

        result = publisher.publish(
            sml_model=sml_model,
            model_name=model_name,
            snowflake_server=settings.snowflake.account,
            snowflake_warehouse=settings.snowflake.warehouse,
            snowflake_database=settings.snowflake.database,
            snowflake_schema=settings.snowflake.schema_name,
            overwrite=overwrite,
        )

        console.print(f"\n[green][OK] Published to Fabric![/green]")
        console.print(f"  Model ID: {result.get('id', 'N/A')}")
        console.print(f"  Display Name: {result.get('displayName', model_name)}")
