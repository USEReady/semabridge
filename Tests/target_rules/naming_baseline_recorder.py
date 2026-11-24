"""Records today's naming behaviour at every naming call site (pre-change baseline).

Run once BEFORE the naming refactor (Phase 3.4, naming stage 2); the output
fixture is what test_naming_parity.py compares the refactored code against:
Snowflake / Fabric / no-target names must stay identical.

    uv run python Tests/target_rules/naming_baseline_recorder.py [pbix_dir]

Sites recorded (src/semabridge/...):
  * api/services/project_mapping_engine.py  _default_target_identifier, _validate_target_name
  * api/services/mapping_service.py         _compat_sanitize_target_name_for_project,
                                            the RESERVED_KEYWORD check in _compat_serialize_auto_map_entity_mappings
  * api/routers/naming_router.py             preview_name
  * the inline "if target == snowflake: snowflake_target_name(...)" blocks in
    core/engine/config.py, api/services/dry_run_job_service.py,
    api/services/mapping_service.py (overrides / persist),
    api/services/project_config_store.py, api/controllers/mappings_controller.py
    -- copied verbatim below as reference implementations (they are inline
    code, not callable on their own);
  * build_entity_mappings on real .pbix models (every dry-run row's target name).
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "fixtures" / "naming_baseline.json"

TARGETS = ["snowflake", "databricks", "fabric", ""]
KINDS = ["column", "metric", None]
NAMES = [
    "Date", "date", "Order", "Value", "Count", "Sales", "Total Sales", "Gross Margin This Year %",
    "% Margin", "Margin %", "Sales $", "Unit $ Price", "2024 Sales", "1st", "City Name", "DM_Pic_fl",
    "DistrictID", "Category (clusters) 2", "Avg $/Unit TY", "a-b", "a.b", "Résumé", "名前", "   padded   ",
    "__x__", "SELECT", "from", "Table", "user", "Role", "KEY", "Year", "Month", "Group", "Order Date",
    "Customer ID", "Customer_ID", "customer id", "X" * 300, "", "#hash", "a#b", "Sales/Units", "Qty&Price",
    "@at", "+plus", "=eq", "Q?", "!bang", "<lt>", "*star", "COL_DATE", "col_order", "Sum_Regular_Sales_Dollars",
]
PATHS = ["Sales.columns.Order Date", "Store.columns.Date", "Sales.metrics.Total Sales", "model.metrics.Margin %", ""]


# --- verbatim copies of the inline blocks (pre-change) --------------------------------

def site_config_py(name, source_path, target_type):
    from semabridge.utils.identifiers import is_column_source_path, is_legacy_auto_column_name, snowflake_target_name
    target_name = str(name or "").strip()
    if target_type == "snowflake" and target_name:
        if is_column_source_path(source_path) and is_legacy_auto_column_name(source_path.split(".columns.", 1)[1], target_name):
            return "<SKIPPED>"
        target_name = snowflake_target_name(target_name, source_path)
    return target_name


def site_dry_run_gate(name, source_path, target_type):
    from semabridge.utils.identifiers import is_column_source_path, is_legacy_auto_column_name, snowflake_target_name
    is_snowflake = str(target_type or "").lower() == "snowflake"
    name = str(name or "").strip()
    if is_snowflake:
        if is_column_source_path(source_path):
            source_name = source_path.split(".columns.", 1)[1]
            if is_legacy_auto_column_name(source_name, name):
                return "<SKIPPED>"
        name = snowflake_target_name(name, source_path)
    return name


def site_manual_overrides(name, source_path, target_type):  # mapping_service._compat_collect_manual_mapping_overrides
    from semabridge.utils.identifiers import snowflake_target_name
    target_name = str(name or "").strip()
    if target_type == "snowflake" and target_name:
        target_name = snowflake_target_name(target_name, source_path)
    return target_name


def site_with_kind(name, source_path, kind, target_type):
    """mapping_service persist (543), project_config_store (506), mappings_controller deploy (1322)."""
    from semabridge.utils.identifiers import snowflake_target_name
    target = str(name or "").strip()
    if target_type == "snowflake" and target:
        target = snowflake_target_name(target, source_path, kind)
    return target


# --- recording ---------------------------------------------------------------------------

def record(pbix_dir: str | None) -> dict:
    from semabridge.api.services import mapping_service, project_mapping_engine
    from semabridge.api.routers.naming_router import preview_name

    out: dict = {"names": NAMES, "paths": PATHS, "sites": {}}
    s = out["sites"]

    s["default_target_identifier"] = {
        t: {str(k): [project_mapping_engine._default_target_identifier(n, t, k) for n in NAMES] for k in KINDS}
        for t in TARGETS
    }
    s["validate_target_name"] = {
        t: {
            str(k): {
                str(c): [
                    project_mapping_engine._validate_target_name(
                        target_name=n, source_name=n, collision_detected=c, target_connector=t, entity_kind=k
                    )
                    for n in NAMES
                ]
                for c in (False, True)
            }
            for k in KINDS
        }
        for t in TARGETS
    }

    original = mapping_service._compat_project_target_type
    try:
        s["sanitize_for_project"] = {}
        for t in TARGETS:
            mapping_service._compat_project_target_type = lambda pid, _t=t: _t
            s["sanitize_for_project"][t] = {
                p: {str(k): [mapping_service._compat_sanitize_target_name_for_project("p", n, p or None, k) for n in NAMES]
                    for k in KINDS}
                for p in PATHS
            }
    finally:
        mapping_service._compat_project_target_type = original

    s["serialize_reserved"] = {}
    for t in TARGETS:
        rows = [
            {"id": f"r{i}-{k}", "source_name": n, "source_path": f"Sales.{'columns' if k == 'column' else 'metrics'}.{n}",
             "target_name": n, "entity_kind": k, "validation_status": "valid", "validation_code": "OK"}
            for i, n in enumerate(NAMES) if n.strip() for k in ("column", "metric")
        ]
        serialized = mapping_service._compat_serialize_auto_map_entity_mappings(rows, target_connector=t)
        s["serialize_reserved"][t] = [
            {k: r.get(k) for k in ("source_path", "target_name", "suggested_target_name", "validation_code",
                                   "validation_status", "validation_message", "collision_detected")}
            for r in serialized
        ]

    s["naming_preview_snowflake"] = {str(k): [preview_name(name=n or "x", kind=k, target="snowflake")["name"]
                                              for n in NAMES] for k in ("column", "metric")}
    s["config_py"] = {t: {p: [site_config_py(n, p, t) for n in NAMES] for p in PATHS} for t in TARGETS}
    s["dry_run_gate"] = {t: {p: [site_dry_run_gate(n, p, t) for n in NAMES] for p in PATHS} for t in TARGETS}
    s["manual_overrides"] = {t: {p: [site_manual_overrides(n, p, t) for n in NAMES] for p in PATHS} for t in TARGETS}
    s["with_kind"] = {
        t: {p: {str(k): [site_with_kind(n, p, k, t) for n in NAMES] for k in KINDS} for p in PATHS} for t in TARGETS
    }

    out["models"] = record_models(pbix_dir)
    return out


def record_models(pbix_dir: str | None) -> dict:
    """Every dry-run row's target name for real models (build_entity_mappings)."""
    from semabridge.api.services.project_mapping_engine import build_entity_mappings
    from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
    from semabridge.converter.osi_to_sml import OSIToSMLConverter
    from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
    from semabridge.core.source_format import from_pbix_tmsl

    models = {"competitive_marketing": REPO / "Examples" / "Competitive Marketing Analysis (More Synonyms) (1).pbix"}
    if pbix_dir:
        models["store_sales"] = Path(pbix_dir) / "Store_Sales.pbix"
    out = {}
    for name, path in models.items():
        if not path.exists():
            continue
        connector = LocalPBIXConnector({"pbix_path": str(path)})
        discovered = connector.discover()
        tmsl = connector.extract() if discovered.get("raw_tmsl") is None else discovered["raw_tmsl"]
        sf = from_pbix_tmsl(project_id="g", run_id="g", tmsl=tmsl, pbix_path=str(path))
        osi = TMSLToOSIConverter().to_osi({"tmsl": sf.tmsl_definition, "workspace_id": "local", "dataset_id": "g",
                                           "display_name": "g", "project_id": "g", "field_aliases": [],
                                           "source_file": None, "infer_relationships": None})
        sml = OSIToSMLConverter().from_osi(osi, row_counts=sf.row_counts)
        model = sml.model_dump(mode="json")
        out[name] = {}
        for t in TARGETS:
            built = build_entity_mappings(project_id="g", model=model, session_key="baseline", target_connector=t)
            out[name][t] = sorted(
                [
                    {k: m.get(k) for k in ("source_path", "target_name", "validation_code", "collision_detected",
                                           "suggested_target_name")}
                    for m in built.get("mappings", [])
                ],
                key=lambda r: str(r["source_path"]),
            )
    return out


def main() -> None:
    os.environ.pop("SEMABRIDGE_TIER5", None)
    for key in list(os.environ):
        if any(m in key.upper() for m in ("OPENAI", "GEMINI", "FEATHERLESS", "ANTHROPIC", "LLM", "API_KEY")):
            os.environ.pop(key, None)

    def _no_network(*_a, **_k):
        raise OSError("network blocked")

    socket.socket.connect = _no_network
    from semabridge.dax_translation.tier5.service import Tier5Service

    Tier5Service.translate = lambda self, request: None
    Tier5Service.translate_batch = lambda self, requests: [None] * len(requests)
    pbix_dir = sys.argv[1] if len(sys.argv) > 1 else None
    cwd = os.getcwd()
    os.chdir(tempfile.mkdtemp())
    try:
        data = record(pbix_dir)
    finally:
        os.chdir(cwd)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes); models: {sorted(data['models'])}")


if __name__ == "__main__":
    main()
