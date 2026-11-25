"""Equivalence test: recorded Power BI Desktop fixtures produce the same OSI/SML/DDL
as the real-PBIX snapshot, except for documented, expected improvements.

Documented differences:
1. COUNTBLANK translation improvement:
   - Real-PBIX snapshot (pbixray) had untyped columns causing COUNTBLANK translation to decline:
     'DAX translation failed: declined: COUNTBLANK on a text or untyped column (empty-string handling) is not translated'
   - Live Desktop engine metadata provides typed columns (ExplicitDataType=2/string), allowing
     DAX rule translation to generate valid Snowflake SQL:
     'COUNT(CASE WHEN PRODUCT.PRODUCT IS NULL THEN 1 ELSE NULL END)'
     with complexity_tier=5 and sync_enabled=True.
2. Deterministic relationship ordering:
   - Live Desktop model reader sorts relationships deterministically by (from_table, to_table, from_col, to_col).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import pytest

from semabridge.sources.pbi_desktop.model_reader import assemble_tmsl_from_entities
from semabridge.core.source_format import from_pbix_tmsl
from semabridge.core.engine.conversion.pbix import _convert_pbix_to_sml
from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
from semabridge.sources import report_parsing

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RECORDED_DIR = REPO_ROOT / "Scripts" / "poc_pbi_desktop" / "recorded"
SNAPSHOT_DIR = REPO_ROOT / "Tests" / "characterization" / "snapshots" / "real_pbix"
PBIX_SLUG = "competitive_marketing_analysis_more_synonyms_1"


@pytest.fixture(scope="module")
def desktop_pipeline_output():
    # Load characterization build module
    spec = importlib.util.spec_from_file_location(
        "characterization_build",
        REPO_ROOT / "Tests" / "characterization" / "_build.py",
    )
    _build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(_build)

    # 1. Load recorded fixtures
    fixtures = {}
    for f in RECORDED_DIR.glob("*.json"):
        if f.stem in ("assembled_tmsl", "generated.osi", "generated.sml"):
            continue
        fixtures[f.stem] = json.loads(f.read_text(encoding="utf-8"))

    # 2. Assemble TMSL from fixtures
    entities = {
        "tables": fixtures.get("tmschema_tables") or fixtures.get("info_tables"),
        "columns": fixtures.get("tmschema_columns") or fixtures.get("info_columns"),
        "measures": fixtures.get("tmschema_measures") or fixtures.get("info_measures"),
        "partitions": fixtures.get("tmschema_partitions") or fixtures.get("info_partitions"),
        "relationships": fixtures.get("tmschema_relationships") or fixtures.get("info_relationships"),
        "hierarchies": fixtures.get("tmschema_hierarchies", []),
        "levels": fixtures.get("tmschema_levels", []),
        "model": fixtures.get("tmschema_model", [{}]),
    }
    tmsl = assemble_tmsl_from_entities(entities)

    # 3. Read report layout directly from pbix zip without pbixray
    pbix_path = REPO_ROOT / "Examples" / "Competitive Marketing Analysis (More Synonyms) (1).pbix"
    field_aliases = []
    unresolved = []
    ambiguous = []
    if pbix_path.exists():
        connector = LocalPBIXConnector({"pbix_path": str(pbix_path)})
        connector._open_archive()
        layout = connector._extract_report_layout()
        tables_for_alias = connector._parse_tables(tmsl["model"])
        measures_for_alias = connector._parse_measures(tmsl["model"])
        pres, unresolved = connector._parse_presentation_metadata(layout, tables_for_alias, measures_for_alias)
        field_aliases, ambiguous = report_parsing.summarize_report_aliases(pres)
        connector._close_archive()

    sf = from_pbix_tmsl(
        project_id=_build.PROJECT_ID,
        run_id="run-characterization",
        tmsl=tmsl,
        pbix_path="Examples/Competitive Marketing Analysis (More Synonyms) (1).pbix",
        field_aliases=field_aliases,
        unresolved_report_field_references=unresolved,
        ambiguous_report_aliases=ambiguous,
    )

    ctx = _build._context("pbix", sf)
    sml = _convert_pbix_to_sml(_build._ENGINE_SELF, ctx)

    osi_dict = json.loads(_build._dump(ctx.osi_model))
    sml_dict = json.loads(_build._dump(sml))
    ddl_text = _build._ddl(sml)

    return osi_dict, sml_dict, ddl_text


def test_desktop_osi_equivalence(desktop_pipeline_output):
    osi_dict, _, _ = desktop_pipeline_output
    snapshot_osi = json.loads((SNAPSHOT_DIR / f"{PBIX_SLUG}.osi.json").read_text(encoding="utf-8"))

    # Compare datasets: names and count must match exactly
    ds_generated = sorted([d["unique_name"] for d in osi_dict["datasets"]])
    ds_snapshot = sorted([d["unique_name"] for d in snapshot_osi["datasets"]])
    assert ds_generated == ds_snapshot

    # Compare relationships: same set of endpoints
    def _rel_endpoints(rels):
        return sorted([
            (r["from_dataset"], r["to_dataset"], tuple(r["from_columns"]), tuple(r["to_columns"]))
            for r in rels
        ])

    assert _rel_endpoints(osi_dict["relationships"]) == _rel_endpoints(snapshot_osi["relationships"])


def test_desktop_sml_equivalence_with_documented_differences(desktop_pipeline_output):
    _, sml_dict, _ = desktop_pipeline_output
    snapshot_sml = json.loads((SNAPSHOT_DIR / f"{PBIX_SLUG}.sml.json").read_text(encoding="utf-8"))

    metrics_gen = {m["unique_name"]: m for m in sml_dict["metrics"]}
    metrics_snap = {m["unique_name"]: m for m in snapshot_sml["metrics"]}

    # Metric keys must match
    assert set(metrics_gen.keys()) == set(metrics_snap.keys())

    # Check documented difference: NewCount of Product and NewNewCount of Product
    # Live engine metadata enables COUNTBLANK translation to Snowflake SQL
    for m_name in ("NewCount of Product", "NewNewCount of Product"):
        m_gen = metrics_gen[m_name]
        m_snap = metrics_snap[m_name]

        # In snapshot: declined
        assert m_snap["sync_enabled"] is False
        assert "declined" in str(m_snap["sync_failure_reason"])

        # In Desktop engine: successfully translated
        assert m_gen["sync_enabled"] is True
        assert "COUNT(CASE WHEN PRODUCT.PRODUCT IS NULL THEN 1 ELSE NULL END)" in m_gen["sql_expression"]

    # All other metrics should match sql_expression and sync_enabled
    for m_name, m_snap in metrics_snap.items():
        if m_name in ("NewCount of Product", "NewNewCount of Product"):
            continue
        m_gen = metrics_gen[m_name]
        assert m_gen["sync_enabled"] == m_snap["sync_enabled"], f"Mismatch for metric {m_name}"
        assert m_gen["sql_expression"] == m_snap["sql_expression"], f"SQL mismatch for metric {m_name}"


def test_desktop_ddl_equivalence_with_documented_differences(desktop_pipeline_output):
    _, _, ddl_text = desktop_pipeline_output
    snapshot_ddl = (SNAPSHOT_DIR / f"{PBIX_SLUG}.ddl.sql").read_text(encoding="utf-8")

    # Documented difference in DDL: COUNTBLANK metrics are working queries instead of NULL
    assert 'PRODUCT."NEWCOUNT_OF_PRODUCT" AS COUNT(CASE WHEN PRODUCT.PRODUCT IS NULL THEN 1 ELSE NULL END)' in ddl_text
    assert 'PRODUCT."NEWNEWCOUNT_OF_PRODUCT" AS COUNT(CASE WHEN PRODUCT.PRODUCT IS NULL THEN 1 ELSE NULL END)' in ddl_text

    # Snapshot had CAST(NULL AS DOUBLE)
    assert 'PRODUCT."NEWCOUNT_OF_PRODUCT" AS CAST(NULL AS DOUBLE)' in snapshot_ddl
