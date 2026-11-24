"""Naming moved into the platform rulebooks (Phase 3.4, naming stages 1-2).

fixtures/naming_baseline.json was recorded by naming_baseline_recorder.py on
the code BEFORE the change. This test runs the refactored code on the same
inputs:

* Snowflake, Fabric and "no target" must be byte-identical to the baseline,
  at every naming call site and for every dry-run row of real models;
* Databricks (decided: Snowflake's naming) must produce Snowflake's names,
  never block reserved words (every identifier is backtick-quoted), and no
  longer collide on "%" names.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any

import pytest

from semabridge.target_rules import DATABRICKS_RULES, SNOWFLAKE_RULES
from semabridge.target_rules.naming import naming_for, normalize_user_target_name, override_deploy_name

HERE = Path(__file__).resolve().parent
BASE = json.loads((HERE / "fixtures" / "naming_baseline.json").read_text(encoding="utf-8"))
NAMES, PATHS, SITES = BASE["names"], BASE["paths"], BASE["sites"]
UNCHANGED = ["snowflake", "fabric", ""]
KINDS = ["column", "metric", None]
_AI_ENV_MARKERS = ("OPENAI", "GEMINI", "FEATHERLESS", "ANTHROPIC", "LLM", "API_KEY")


def _k(kind: Any) -> str:
    return str(kind)


# --- the rulebook part itself ---------------------------------------------------------


def test_rulebooks_carry_naming() -> None:
    from semabridge.utils.identifiers import snowflake_target_name

    assert naming_for("snowflake") is SNOWFLAKE_RULES.naming
    assert naming_for("Databricks") is DATABRICKS_RULES.naming
    assert naming_for("fabric") is None and naming_for("") is None and naming_for(None) is None
    for name in NAMES:
        for path in PATHS:
            for kind in KINDS:
                expected = snowflake_target_name(name, path or None, kind)
                assert SNOWFLAKE_RULES.naming.target_name(name, path or None, kind) == expected
                assert DATABRICKS_RULES.naming.target_name(name, path or None, kind) == expected
    assert DATABRICKS_RULES.naming.reserved_words("column") == frozenset()
    assert "select" in SNOWFLAKE_RULES.naming.reserved_words("column")


# --- every call site against the baseline -----------------------------------------------


def test_default_target_identifier() -> None:
    from semabridge.api.services.project_mapping_engine import _default_target_identifier

    site = SITES["default_target_identifier"]
    for kind in KINDS:
        for t in UNCHANGED:
            assert [_default_target_identifier(n, t, kind) for n in NAMES] == site[t][_k(kind)], (t, kind)
        assert [_default_target_identifier(n, "databricks", kind) for n in NAMES] == site["snowflake"][_k(kind)]


def test_validate_target_name() -> None:
    from semabridge.api.services.project_mapping_engine import _validate_target_name

    site = SITES["validate_target_name"]
    for kind in KINDS:
        for collided in (False, True):
            def run(t):
                return [
                    _validate_target_name(target_name=n, source_name=n, collision_detected=collided,
                                          target_connector=t, entity_kind=kind)
                    for n in NAMES
                ]
            for t in UNCHANGED:
                assert run(t) == site[t][_k(kind)][str(collided)], (t, kind, collided)
            for snow, dbx in zip(site["snowflake"][_k(kind)][str(collided)], run("databricks")):
                assert dbx["validation_code"] != "RESERVED_KEYWORD"
                if snow["validation_code"] != "RESERVED_KEYWORD":
                    expected = dict(snow, validation_message=snow["validation_message"].replace("Snowflake", "Databricks"))
                    assert dbx == expected


def test_sanitize_for_project(monkeypatch) -> None:
    from semabridge.api.services import mapping_service

    site = SITES["sanitize_for_project"]
    for t in UNCHANGED + ["databricks"]:
        monkeypatch.setattr(mapping_service, "_compat_project_target_type", lambda pid, _t=t: _t)
        expected_from = "snowflake" if t == "databricks" else t
        for path in PATHS:
            for kind in KINDS:
                got = [mapping_service._compat_sanitize_target_name_for_project("p", n, path or None, kind) for n in NAMES]
                assert got == site[expected_from][path][_k(kind)], (t, path, kind)


def test_serialize_reserved_check() -> None:
    from semabridge.api.services.mapping_service import _compat_serialize_auto_map_entity_mappings

    site = SITES["serialize_reserved"]
    rows = [
        {"id": f"r{i}-{k}", "source_name": n, "source_path": f"Sales.{'columns' if k == 'column' else 'metrics'}.{n}",
         "target_name": n, "entity_kind": k, "validation_status": "valid", "validation_code": "OK"}
        for i, n in enumerate(NAMES) if n.strip() for k in ("column", "metric")
    ]
    keys = ("source_path", "target_name", "suggested_target_name", "validation_code", "validation_status",
            "validation_message", "collision_detected")
    for t in UNCHANGED:
        got = [{k: r.get(k) for k in keys} for r in _compat_serialize_auto_map_entity_mappings(rows, target_connector=t)]
        assert got == site[t], t
    dbx = _compat_serialize_auto_map_entity_mappings(rows, target_connector="databricks")
    assert all(r.get("validation_code") != "RESERVED_KEYWORD" for r in dbx)


def test_naming_preview() -> None:
    from semabridge.api.routers.naming_router import preview_name

    for kind in ("column", "metric"):
        snow = [preview_name(name=n or "x", kind=kind, target="snowflake")["name"] for n in NAMES]
        assert snow == SITES["naming_preview_snowflake"][kind]
        assert [preview_name(name=n or "x", kind=kind, target="databricks")["name"] for n in NAMES] == snow


@pytest.mark.parametrize("site_name", ["config_py", "dry_run_gate"])
def test_override_deploy_name(site_name) -> None:
    from semabridge.utils.identifiers import snowflake_target_name

    site = SITES[site_name]
    # The dry-run gate only ever sees non-empty names (its loop skips empty
    # ones before naming), so an empty name is not a reachable input there;
    # the recorder's copy of that block did not include the loop's guard.
    reachable = [i for i, n in enumerate(NAMES) if n.strip() or site_name != "dry_run_gate"]
    for path in PATHS:
        for t in UNCHANGED:
            got = [override_deploy_name(NAMES[i], path, t) for i in reachable]
            assert [("<SKIPPED>" if g is None else g) for g in got] == [site[t][path][i] for i in reachable], (t, path)
        # Databricks: Snowflake's names, without the Snowflake-only legacy skip.
        expected = [snowflake_target_name(n.strip(), path) if n.strip() else "" for n in NAMES]
        assert [override_deploy_name(n, path, "databricks") for n in NAMES] == expected


def test_user_names_with_and_without_kind() -> None:
    for path in PATHS:
        for t in UNCHANGED:
            assert [normalize_user_target_name(n, t, path) for n in NAMES] == SITES["manual_overrides"][t][path]
            for kind in KINDS:
                assert [normalize_user_target_name(n, t, path, kind) for n in NAMES] == SITES["with_kind"][t][path][_k(kind)]
        assert [normalize_user_target_name(n, "databricks", path) for n in NAMES] == SITES["manual_overrides"]["snowflake"][path]


# --- every dry-run row of real models -----------------------------------------------------

MODELS = {
    "competitive_marketing": HERE.parents[1] / "Examples" / "Competitive Marketing Analysis (More Synonyms) (1).pbix",
}
if os.environ.get("SEMABRIDGE_GOLDEN_PBIX_DIR", "").strip():
    MODELS["store_sales"] = Path(os.environ["SEMABRIDGE_GOLDEN_PBIX_DIR"].strip()) / "Store_Sales.pbix"


@pytest.fixture
def hermetic(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for key in list(os.environ):
        if any(marker in key.upper() for marker in _AI_ENV_MARKERS):
            monkeypatch.delenv(key, raising=False)
    from semabridge.dax_translation.tier5.service import Tier5Service

    monkeypatch.setattr(Tier5Service, "translate", lambda self, request: None)
    monkeypatch.setattr(Tier5Service, "translate_batch", lambda self, requests: [None] * len(requests))
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: (_ for _ in ()).throw(OSError("network blocked")))


@pytest.mark.parametrize("model", sorted(MODELS))
def test_dry_run_rows_of_real_models(model, hermetic) -> None:
    from semabridge.api.services.project_mapping_engine import build_entity_mappings
    from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
    from semabridge.converter.osi_to_sml import OSIToSMLConverter
    from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
    from semabridge.core.source_format import from_pbix_tmsl

    path = MODELS[model]
    if model not in BASE["models"] or not path.exists():
        pytest.skip(f"{model}: .pbix or baseline not available")
    connector = LocalPBIXConnector({"pbix_path": str(path)})
    discovered = connector.discover()
    tmsl = connector.extract() if discovered.get("raw_tmsl") is None else discovered["raw_tmsl"]
    sf = from_pbix_tmsl(project_id="g", run_id="g", tmsl=tmsl, pbix_path=str(path))
    osi = TMSLToOSIConverter().to_osi({"tmsl": sf.tmsl_definition, "workspace_id": "local", "dataset_id": "g",
                                       "display_name": "g", "project_id": "g", "field_aliases": [],
                                       "source_file": None, "infer_relationships": None})
    sml = OSIToSMLConverter().from_osi(osi, row_counts=sf.row_counts).model_dump(mode="json")

    def rows(target):
        built = build_entity_mappings(project_id="g", model=sml, session_key="baseline", target_connector=target)
        return sorted(
            [{k: m.get(k) for k in ("source_path", "target_name", "validation_code", "collision_detected",
                                    "suggested_target_name")} for m in built.get("mappings", [])],
            key=lambda r: str(r["source_path"]),
        )

    for t in UNCHANGED:
        assert rows(t) == BASE["models"][model][t], (model, t)
    dbx, snow = rows("databricks"), BASE["models"][model]["snowflake"]
    assert [(r["source_path"], r["target_name"]) for r in dbx] == [(r["source_path"], r["target_name"]) for r in snow]
    # Before: Store Sales had 8 collisions on Databricks, Competitive Marketing 2 (the generic rule dropped '%').
    assert sum(1 for r in dbx if r["collision_detected"]) == 0
