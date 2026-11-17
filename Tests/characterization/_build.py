"""Builds the characterization (golden) artifacts for the three source pipelines.

Offline and deterministic: no Snowflake / Fabric connection is opened. Each case
runs the REAL engine conversion function for its source type and the real
Snowflake DDL generator, then normalizes volatile values (run ids, timestamps,
absolute paths) so the output is stable across runs and machines.

Cases (8 artifacts):
  pbix       : OSI, SML, DDL   – fixtures/model.tmsl.json as a .pbix source through _convert_pbix_to_sml
  fabric     : OSI, SML, DDL   – the same TMSL as an offline Fabric model through _convert_fabric_to_sml
  snowflake  : SML, DDL        – the pbix DDL fed back as a Snowflake semantic-view source
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots"
FIXTURE_TMSL = Path(__file__).resolve().parent / "fixtures" / "model.tmsl.json"
PBIX_SAMPLE = REPO_ROOT / "Examples" / "characterization_model.pbix"  # logical path only; never opened

_VOLATILE_KEY = re.compile(r"(timestamp|_at$|^run_id$|^created|^updated|^extracted)", re.I)
PROJECT_ID = "characterization"


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("<volatile>" if _VOLATILE_KEY.search(str(k)) else _scrub(v)) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, str):
        root = str(REPO_ROOT)
        return value.replace(root, "<ROOT>").replace(root.replace("\\", "/"), "<ROOT>")
    return value


def _dump(model: Any) -> str:
    raw = json.loads(model.model_dump_json()) if hasattr(model, "model_dump_json") else model
    return json.dumps(_scrub(raw), indent=1, sort_keys=True, default=str)


def _ddl(sml: Any) -> str:
    from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
    from semabridge.core.behavior import ConnectorBehavior
    from semabridge.core.settings import SnowflakeConfig

    cfg = SnowflakeConfig(
        account="characterization.local", user="u", password="p", warehouse="WH",
        database="CHAR_DB", schema_name="CHAR_SCHEMA", role="R",
    )
    behavior = ConnectorBehavior()
    behavior.snowflake.create_missing_tables = False
    behavior.snowflake.apply_inferred_types = False
    behavior.snowflake.auto_execute_precompute = False
    behavior.features.enable_cortex_analyst = False
    text = "\n;\n".join(SnowflakeEmitter(cfg, behavior).generate_ddls(sml))
    return str(_scrub(text))


def _context(source_type: str, source_format: Any):
    from semabridge.core.engine.context import RunContext

    return RunContext(
        project_id=PROJECT_ID,
        run_id="run-characterization",
        config=SimpleNamespace(options=None),
        start_time=time.time(),
        source_type=source_type,
        source_format=source_format,
    )


def _cols(*spec: str):
    return [{"COLUMN_NAME": n, "DATA_TYPE": t, "IS_NULLABLE": "NO" if i == 0 else "YES", "ORDINAL_POSITION": i + 1}
            for i, (n, t) in enumerate(s.split(":") for s in spec)]


_SNOWFLAKE_METADATA: Dict[str, Any] = {
    "database": "CHAR_DB",
    "schema": "CHAR_SCHEMA",
    "tables": {
        "SALES_FACT": {"description": "Sales transactions", "row_count": None},
        "PRODUCT": {"description": "Products", "row_count": None},
        "DATE_DIM": {"description": "Calendar", "row_count": None},
        "GEO": {"description": "Geography", "row_count": None},
    },
    "columns": {
        "SALES_FACT": _cols("SALES_KEY:NUMBER", "PRODUCT_KEY:NUMBER", "DATE_KEY:NUMBER", "GEO_KEY:NUMBER",
                            "UNITS:NUMBER", "REVENUE:NUMBER", "DISCOUNT:NUMBER", "STATUS:VARCHAR"),
        "PRODUCT": _cols("PRODUCT_KEY:NUMBER", "PRODUCT:VARCHAR", "MANUFACTURER:VARCHAR", "SEGMENT:VARCHAR"),
        "DATE_DIM": _cols("DATE_KEY:NUMBER", "DATE:DATE", "YEAR:NUMBER", "MONTH:VARCHAR"),
        "GEO": _cols("GEO_KEY:NUMBER", "REGION:VARCHAR", "STATE:VARCHAR"),
    },
    "primary_keys": {"SALES_FACT": ["SALES_KEY"], "PRODUCT": ["PRODUCT_KEY"], "DATE_DIM": ["DATE_KEY"], "GEO": ["GEO_KEY"]},
    # No declared FKs: with FKs the current Snowflake-source path raises
    # KeyError('from_column') in RelationshipDetector.detect_all (pre-existing
    # bug, tracked separately). The snapshot pins today's FK-less behaviour.
    "foreign_keys": [],
}


_ENGINE_SELF = SimpleNamespace(
    _apply_mapping_overrides_from_config=lambda *a, **k: None,
    _record_step=lambda *a, **k: None,
)


def _pbix_tmsl() -> Dict[str, Any]:
    """The committed TMSL fixture, as a .pbix DataModelSchema would yield it.

    The Examples/*.pbix samples store a binary DataModel that needs pbixray's
    native decoder, which some locked-down machines block; the fixture keeps
    the gate runnable everywhere. Binary extraction itself is not part of this
    characterization – only conversion and DDL generation are.
    """
    tmsl = json.loads(FIXTURE_TMSL.read_text(encoding="utf-8"))
    discovered = {
        "field_aliases": [
            {"field": "Total Units", "field_type": "measure", "table": "Sales", "aliases": ["Units Sold"]},
            {"field": "Manufacturer", "field_type": "column", "table": "Product", "aliases": ["Brand"]},
        ],
        "unresolved_report_field_references": [],
        "ambiguous_report_aliases": [],
    }
    return {"tmsl": tmsl, "discovered": discovered}


def build_all() -> Dict[str, str]:
    from semabridge.core.engine.conversion.fabric import _convert_fabric_to_sml
    from semabridge.core.engine.conversion.pbix import _convert_pbix_to_sml
    from semabridge.core.engine.conversion.snowflake import _convert_snowflake_to_sml
    from semabridge.core.source_format import from_fabric_tmsl, from_pbix_tmsl, from_snowflake_metadata

    out: Dict[str, str] = {}
    src = _pbix_tmsl()

    # pbix
    sf = from_pbix_tmsl(
        project_id=PROJECT_ID, run_id="run-characterization", tmsl=src["tmsl"], pbix_path=str(PBIX_SAMPLE),
        field_aliases=src["discovered"].get("field_aliases", []),
        unresolved_report_field_references=src["discovered"].get("unresolved_report_field_references", []),
        ambiguous_report_aliases=src["discovered"].get("ambiguous_report_aliases", []),
    )
    ctx = _context("pbix", sf)
    sml = _convert_pbix_to_sml(_ENGINE_SELF, ctx)
    out["pbix.osi.json"] = _dump(ctx.osi_model)
    out["pbix.sml.json"] = _dump(sml)
    pbix_ddl = _ddl(sml)
    out["pbix.ddl.sql"] = pbix_ddl

    # fabric (offline: same TMSL, Fabric source format)
    sf = from_fabric_tmsl(
        project_id=PROJECT_ID, run_id="run-characterization", tmsl=src["tmsl"],
        workspace_id="00000000-0000-0000-0000-00000000c0de",
        dataset_id="00000000-0000-0000-0000-00000000da7a", row_counts={},
    )
    sf.dataset_name = "Characterization Model"
    ctx = _context("fabric", sf)
    sml = _convert_fabric_to_sml(_ENGINE_SELF, ctx, None, None)
    out["fabric.osi.json"] = _dump(ctx.osi_model)
    out["fabric.sml.json"] = _dump(sml)
    out["fabric.ddl.sql"] = _ddl(sml)

    # snowflake source (information-schema metadata path: tables, PKs, FKs)
    sf = from_snowflake_metadata(
        project_id=PROJECT_ID, run_id="run-characterization", metadata=_SNOWFLAKE_METADATA,
    )
    ctx = _context("snowflake", sf)
    ctx.config.model = SimpleNamespace(description="Characterization Snowflake source")
    sml = _convert_snowflake_to_sml(_ENGINE_SELF, ctx)
    out["snowflake.sml.json"] = _dump(sml)
    out["snowflake.ddl.sql"] = _ddl(sml)
    return out


def write_snapshots() -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    for name, text in build_all().items():
        (SNAPSHOT_DIR / name).write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    write_snapshots()
    print("snapshots written to", SNAPSHOT_DIR)
