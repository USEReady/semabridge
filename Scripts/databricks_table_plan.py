"""Read-only: the Databricks table plan for a .pbix against your workspace (Phase 3.4).

    uv run python Scripts/databricks_table_plan.py <file.pbix> [source_schemas] [on_missing_source]

    e.g. uv run python Scripts/databricks_table_plan.py C:/Nimish/Store_Sales.pbix workspace.sb_tests

Converts the .pbix on this machine with the Databricks rules (no AI tier),
reads table/column metadata from Unity Catalog through the guarded client
(SELECT on information_schema only -- the warehouse starts if stopped),
then prints, per Power BI table: bound to which Unity Catalog table (and
how), or stub / skip; and a few measures before / after the real column
names are put in. Nothing is created or changed in Databricks.
Uses DATABRICKS_* from .env; prints no secrets.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    pbix = Path(sys.argv[1]).resolve()
    sources = sys.argv[2] if len(sys.argv) > 2 else None
    mode = sys.argv[3] if len(sys.argv) > 3 else "stub"
    os.environ.pop("SEMABRIDGE_TIER5", None)

    from semabridge.core.settings import DatabricksConfig
    from semabridge.target_backends.databricks.auth import DatabricksConnectionInfo

    try:
        info = DatabricksConnectionInfo.from_config(DatabricksConfig())
    except Exception:
        from dotenv import dotenv_values

        info = DatabricksConnectionInfo.from_env({k: v for k, v in dotenv_values(".env").items() if v})

    cwd = os.getcwd()
    os.chdir(tempfile.mkdtemp())  # conversion writes debug files to ./output
    try:
        from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
        from semabridge.converter.osi_to_sml import OSIToSMLConverter
        from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
        from semabridge.core.source_format import from_pbix_tmsl
        from semabridge.dax_translation.tier5.service import Tier5Service
        from semabridge.target_rules import DATABRICKS_RULES

        Tier5Service.translate = lambda self, request: None
        Tier5Service.translate_batch = lambda self, requests: [None] * len(requests)
        connector = LocalPBIXConnector({"pbix_path": str(pbix)})
        discovered = connector.discover()
        tmsl = connector.extract() if discovered.get("raw_tmsl") is None else discovered["raw_tmsl"]
        sf = from_pbix_tmsl(project_id="plan", run_id="plan", tmsl=tmsl, pbix_path=str(pbix))
        osi = TMSLToOSIConverter().to_osi({"tmsl": sf.tmsl_definition, "workspace_id": "local", "dataset_id": pbix.stem,
                                           "display_name": pbix.stem, "project_id": "plan", "field_aliases": [],
                                           "source_file": None, "infer_relationships": None})
        sml = OSIToSMLConverter(rules=DATABRICKS_RULES).from_osi(osi, row_counts=sf.row_counts)
    finally:
        os.chdir(cwd)

    from semabridge.target_backends.databricks.client import DatabricksSqlClient, DatabricksSqlError
    from semabridge.target_backends.databricks.tables import (
        TableMatchingError, parse_source_schemas, plan_tables, read_unity_catalog, rewrite_measure_columns,
    )
    from semabridge.target_backends.databricks.warehouses import resolve_warehouse

    schemas = parse_source_schemas(sources, info.catalog, info.schema)
    print(f"Model: {pbix.name}  ({len(sml.datasets)} tables, {len(sml.metrics)} measures)")
    print(f"Searching: {', '.join(f'{c}.{s}' for c, s in schemas)}   on_missing_source={mode}")
    client = DatabricksSqlClient(info, query_tags={"purpose": "table_plan_check"})
    try:
        if not client.warehouse_id:
            client.warehouse_id = resolve_warehouse(client)["warehouse"].id
        catalog_meta = read_unity_catalog(client, schemas)
        plan = plan_tables(sml, catalog_meta, source_schemas=schemas, on_missing_source=mode)
    except (DatabricksSqlError, TableMatchingError) as exc:
        print(f"\nStopped: {exc}")
        return 1

    print("\nTables:")
    for b in plan.bindings:
        if b.action == "bind":
            extra = f"  missing columns: {', '.join(b.missing_columns)}" if b.missing_columns else ""
            print(f"  BIND  {b.dataset:<24} -> {b.table.full_name}  ({b.matched_by}){extra}")
        elif b.action == "stub":
            cols = ", ".join(f"{n} {t}" for n, t in b.stub_columns[:6])
            more = " ..." if len(b.stub_columns) > 6 else ""
            print(f"  STUB  {b.dataset:<24} (no rows; columns: {cols}{more})")
        else:
            print(f"  SKIP  {b.dataset}")
    for w in plan.warnings:
        print(f"  note: {w}")

    rmap = plan.rewrite_map()
    changed = [(m.unique_name, m.sql_expression, rewrite_measure_columns(m.sql_expression, rmap))
               for m in sml.metrics if m.sql_expression]
    changed = [c for c in changed if c[1] != c[2]]
    print(f"\nMeasures using real column names: {len(changed)} of {len(sml.metrics)}")
    for name, before, after in changed[:3]:
        print(f"  {name}\n    before: {before[:150]}\n    after:  {after[:150]}")
    print("\nDone: read-only, nothing created or changed in Databricks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
