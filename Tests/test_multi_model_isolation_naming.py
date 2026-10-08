"""Multi-model project isolation: naming / physical objects / view dedup /
PBIX resolution.

Every model of a multi-model batch must deploy to its OWN semantic view and
its OWN physical objects, while a single-model run keeps exactly the same
object names it always had. All names here are generic placeholders.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from semabridge.api.services import sync_execution_service as ses
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.engine.targets import snowflake as targets_snowflake
from semabridge.core.execution_engine import ExecutionEngine
from semabridge.core.settings import SnowflakeConfig
from semabridge.domain.exceptions import ValidationError
from semabridge.intermediate.models import (
    OSIAggregationType,
    OSIColumn,
    OSIDataset,
    OSIDataType,
    OSIMetric,
    OSIModel,
)
from semabridge.utils.model_dedup import fingerprint_model
from semabridge.utils.name_translator import BatchViewNameRegistry, build_physical_namespace


def _config() -> SnowflakeConfig:
    return SnowflakeConfig(
        account="test.local", user="u", password="p", warehouse="wh",
        database="DB", schema_name="SCH", role="r",
    )


def _sml(name: str):
    """Two models built from one template: both have a `Fact` table."""
    return OSIToSMLConverter().from_osi(
        OSIModel(
            unique_name=name,
            label=name,
            source_platform="pbix",
            datasets=[
                OSIDataset(
                    unique_name="Fact",
                    columns=[
                        OSIColumn(unique_name="Amount", data_type=OSIDataType.FLOAT),
                        OSIColumn(unique_name="Region", data_type=OSIDataType.STRING),
                    ],
                )
            ],
            metrics=[
                OSIMetric(
                    unique_name="Total",
                    dataset="Fact",
                    source_column="Amount",
                    aggregation=OSIAggregationType.SUM,
                )
            ],
            relationships=[],
        )
    )


def _semantic_ddl(ddls: list[str]) -> str:
    return next(d for d in ddls if "SEMANTIC VIEW" in d.upper())


# ---------------------------------------------------------------------------
# (a) single-model runs keep their exact names
# ---------------------------------------------------------------------------


def test_single_model_batch_gets_no_namespace(tmp_path):
    file_a = tmp_path / "Region A.pbix"
    file_a.write_bytes(b"a")
    jobs, *_ = ses._build_sync_jobs({"source": {"type": "pbix", "models": [str(file_a)]}, "targets": [{"type": "snowflake"}]})
    assert len(jobs) == 1
    assert jobs[0]["physical_namespace"] is None
    assert jobs[0]["batch_model_count"] == 1
    assert jobs[0]["model_label"] == str(file_a)


def test_single_model_emitter_names_are_unchanged():
    model = _sml("REGION_A")
    plain = SnowflakeEmitter(_config(), ConnectorBehavior())
    ddl = _semantic_ddl(plain.generate_ddls(model))
    assert '."FACT"' in ddl
    assert '_FACT"' not in ddl
    assert plain._namespaced_physical_name("FACT_ENRICHED") == "FACT_ENRICHED"
    assert model.datasets[0].source_table == "Fact"


def test_single_model_context_has_no_emitter_namespace_and_same_output_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = SimpleNamespace(physical_namespace=None, source_type="pbix")
    assert targets_snowflake.emitter_physical_namespace(ctx) is None
    engine = ExecutionEngine()
    assert engine._model_output_dir("reverse", model_name="proj-1") == Path("output/reverse/proj-1")
    assert engine._model_output_dir("reverse", model_name="proj-1", model_subdir=None) == Path("output/reverse/proj-1")


# ---------------------------------------------------------------------------
# (b) two models with identical table names in one batch are isolated
# ---------------------------------------------------------------------------


def test_multi_model_batch_assigns_distinct_deterministic_namespaces(tmp_path):
    file_a = tmp_path / "Region A.pbix"
    file_b = tmp_path / "Region B.pbix"
    file_a.write_bytes(b"a")
    file_b.write_bytes(b"b")
    cfg = {"source": {"type": "pbix", "models": [str(file_a), str(file_b)]}, "targets": [{"type": "snowflake"}]}

    jobs, *_ = ses._build_sync_jobs(cfg)
    again, *_ = ses._build_sync_jobs(cfg)

    assert [j["physical_namespace"] for j in jobs] == ["REGION_A", "REGION_B"]
    assert [j["physical_namespace"] for j in again] == ["REGION_A", "REGION_B"]  # deterministic
    assert all(j["batch_model_count"] == 2 for j in jobs)


def test_colliding_display_names_still_get_distinct_namespaces(tmp_path):
    file_a = tmp_path / "Sales Report.pbix"
    file_b = tmp_path / "Sales_Report.pbix"
    file_a.write_bytes(b"a")
    file_b.write_bytes(b"b")
    jobs, *_ = ses._build_sync_jobs({"source": {"type": "pbix", "models": [str(file_a), str(file_b)]}, "targets": [{"type": "snowflake"}]})
    namespaces = [j["physical_namespace"] for j in jobs]
    assert len(set(namespaces)) == 2


def test_two_models_with_same_table_get_distinct_physical_objects_and_views():
    model_a, model_b = _sml("REGION_A"), _sml("REGION_B")
    emitter_a = SnowflakeEmitter(_config(), ConnectorBehavior(), physical_namespace="REGION_A")
    emitter_b = SnowflakeEmitter(_config(), ConnectorBehavior(), physical_namespace="REGION_B")

    ddl_a = _semantic_ddl(emitter_a.generate_ddls(model_a))
    ddl_b = _semantic_ddl(emitter_b.generate_ddls(model_b))

    # Distinct views...
    assert '"REGION_A_SEMANTIC"' in ddl_a
    assert '"REGION_B_SEMANTIC"' in ddl_b
    # ...each referencing ONLY its own physical table.
    assert '"REGION_A_FACT"' in ddl_a and '"REGION_B_FACT"' not in ddl_a
    assert '"REGION_B_FACT"' in ddl_b and '"REGION_A_FACT"' not in ddl_b
    # Shared, generated calendar dimension is not namespaced.
    assert '"REGION_A_DIM_DATE"' not in ddl_a
    # The model itself is restored after DDL generation.
    assert model_a.datasets[0].source_table == "Fact"
    # Enriched views / measure tables are namespaced the same way.
    assert emitter_a._namespaced_physical_name("Fact_ENRICHED") == "REGION_A_Fact_ENRICHED"
    assert emitter_a._strip_physical_namespace("REGION_A_FACT") == "FACT"


def test_namespace_never_applied_to_snowflake_sourced_models():
    ctx = SimpleNamespace(physical_namespace="REGION_A", source_type="snowflake")
    assert targets_snowflake.emitter_physical_namespace(ctx) is None
    ctx_pbix = SimpleNamespace(physical_namespace="REGION_A", source_type="pbix")
    assert targets_snowflake.emitter_physical_namespace(ctx_pbix) == "REGION_A"


def test_multi_model_output_dir_is_per_model(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    engine = ExecutionEngine()
    a = engine._model_output_dir("reverse", model_name="proj-1", model_subdir="REGION_A")
    b = engine._model_output_dir("reverse", model_name="proj-1", model_subdir="REGION_B")
    assert a != b
    assert a == Path("output/reverse/proj-1/REGION_A")


def test_namespaced_backfill_reads_original_pbix_table_name():
    from semabridge.connectors.snowflake_emitter import _NamespacedPbixTableReader

    requested = []
    inner = SimpleNamespace(get_table=lambda name: requested.append(name) or "df")
    reader = _NamespacedPbixTableReader(inner, {"REGION_A_Fact": "Fact"})
    assert reader.get_table("REGION_A_Fact") == "df"
    assert requested == ["Fact"]


def test_build_physical_namespace_is_safe_and_bounded():
    assert build_physical_namespace("C:/x/Region A.pbix") == "REGION_A"
    long_ns = build_physical_namespace("x" * 200)
    assert len(long_ns) <= 40
    assert long_ns == build_physical_namespace("x" * 200)


def test_fabric_batch_registry_disambiguates_only_real_collisions():
    registry = BatchViewNameRegistry()
    assert registry.claim("run-1", "Sales") == "Sales"
    assert registry.claim("run-2", "Sales") == "Sales_2"
    assert registry.claim("run-3", "Marketing") == "Marketing"
    assert registry.claim("run-1", "Sales") == "Sales"  # idempotent per owner


# ---------------------------------------------------------------------------
# (c) fingerprint dedup never merges two different models of a batch
# ---------------------------------------------------------------------------


def _template_model(name: str, formula: str) -> SimpleNamespace:
    return SimpleNamespace(
        unique_name=name,
        label=name,
        datasets=[SimpleNamespace(unique_name="Fact", columns=[SimpleNamespace(unique_name="Amount")])],
        metrics=[SimpleNamespace(unique_name="Total", aggregation="sum", dataset="Fact", expression=formula)],
        relationships=[],
    )


def test_expression_aware_fingerprint_separates_template_models():
    a = _template_model("REGION_A", "SUM(Fact[Amount])")
    b = _template_model("REGION_B", "SUM(Fact[Amount]) * 1.2")
    assert fingerprint_model(a) == fingerprint_model(b)  # structure alone matches
    assert fingerprint_model(a, include_expressions=True) != fingerprint_model(b, include_expressions=True)
    same = _template_model("RENAMED", "  sum(Fact[Amount])\n")
    assert fingerprint_model(a, include_expressions=True) == fingerprint_model(same, include_expressions=True)


def _dedup_context(model, batch_model_count, project_id="proj-1"):
    return SimpleNamespace(
        sml_model=model,
        project_id=project_id,
        batch_model_count=batch_model_count,
        config=SimpleNamespace(snowflake=SimpleNamespace(database="DB", schema_name="SCH")),
        model_structural_fingerprint=None,
        model_fingerprint_scope_key=None,
    )


def test_batch_model_is_never_redirected_onto_existing_view(monkeypatch):
    class _Repo:
        def find_existing_view_name(self, scope_key, fp):
            return "REGION_A"

    monkeypatch.setattr(
        "semabridge.repository.model_fingerprint_repository.ModelFingerprintRepository", _Repo
    )
    ctx = _dedup_context(_template_model("REGION_B", "SUM(Fact[Amount])"), batch_model_count=2)
    targets_snowflake._reuse_existing_view_for_structural_duplicate(None, ctx)
    assert ctx.sml_model.unique_name == "REGION_B"
    assert ctx.model_fingerprint_scope_key == "snowflake|DB|SCH|project=proj-1"
    assert ctx.model_structural_fingerprint


def test_single_model_reupload_still_reuses_its_view_within_its_project(monkeypatch):
    seen = {}

    class _Repo:
        def find_existing_view_name(self, scope_key, fp):
            seen["scope"] = scope_key
            return "ORIGINAL_UPLOAD"

    monkeypatch.setattr(
        "semabridge.repository.model_fingerprint_repository.ModelFingerprintRepository", _Repo
    )
    ctx = _dedup_context(_template_model("RENAMED_UPLOAD", "SUM(Fact[Amount])"), batch_model_count=1)
    targets_snowflake._reuse_existing_view_for_structural_duplicate(None, ctx)
    assert ctx.sml_model.unique_name == "ORIGINAL_UPLOAD"
    assert seen["scope"] == "snowflake|DB|SCH|project=proj-1"


# ---------------------------------------------------------------------------
# (d) a missing PBIX fails loudly instead of falling back to another file
# ---------------------------------------------------------------------------


def _pbix_cfg(folder: Path, models: list[str]) -> dict:
    return {"source": {"type": "pbix", "pbix_folder": str(folder), "models": models}, "targets": [{"type": "snowflake"}]}


def test_missing_pbix_in_single_model_project_raises_instead_of_using_another_file(tmp_path):
    (tmp_path / "Unrelated.pbix").write_bytes(b"x")
    with pytest.raises(ValidationError, match="PBIX file not found"):
        ses._build_sync_jobs(_pbix_cfg(tmp_path, ["Missing Model"]))


def test_missing_pbix_in_batch_is_a_per_model_failure(tmp_path):
    (tmp_path / "Region A.pbix").write_bytes(b"x")
    jobs, *_ = ses._build_sync_jobs(_pbix_cfg(tmp_path, ["Region A", "Region Missing"]))
    assert jobs[0]["pbix_path"].endswith("Region A.pbix")
    assert "resolution_error" not in jobs[0]
    assert jobs[1]["pbix_path"] is None
    assert jobs[1]["resolution_error"].startswith("PBIX file not found")

    result = ses._run_single_job(
        jobs[1], engine_source_type="pbix", target_type="snowflake", config={},
        config_path="", deploy_enabled=True, resolved_workspace_id="local",
    )
    assert result["status"] == "failed"
    assert any("PBIX file not found" in line for line in result["console"]["lines"])
    for key in ("model", "summary", "run_id", "dropped_entities", "missing_dims", "pbix_path",
                "view_name", "target_artifact_path", "duration_ms"):
        assert key in result


def test_ambiguous_fuzzy_match_fails_but_unique_fuzzy_match_resolves(tmp_path):
    (tmp_path / "Sales East.pbix").write_bytes(b"x")
    (tmp_path / "Sales West.pbix").write_bytes(b"x")
    (tmp_path / "Inventory 2024.pbix").write_bytes(b"x")
    jobs, *_ = ses._build_sync_jobs(_pbix_cfg(tmp_path, ["Sales", "Inventory"]))
    assert "ambiguous" in jobs[0]["resolution_error"]
    assert jobs[1]["pbix_path"].endswith("Inventory 2024.pbix")


def test_two_models_resolving_to_same_file_error_for_the_duplicate(tmp_path):
    target = tmp_path / "Region A.pbix"
    target.write_bytes(b"x")
    jobs, *_ = ses._build_sync_jobs(_pbix_cfg(tmp_path, [str(target), "Region A"]))
    assert jobs[0]["pbix_path"] == str(target.resolve())
    assert "already used by model" in jobs[1]["resolution_error"]
    assert jobs[0]["model_label"] != jobs[1]["model_label"]


def test_duplicate_model_labels_are_made_unique(tmp_path):
    jobs, *_ = ses._build_sync_jobs({"source": {"type": "fabric", "models": ["Sales", "Sales"]}, "targets": [{"type": "snowflake"}]})
    assert [j["model_label"] for j in jobs] == ["Sales", "Sales_2"]
    assert [j["dataset_id"] for j in jobs] == ["Sales", "Sales"]
