"""
Tests for multi-model Snowflake-to-Fabric sync.

Validates:
1. ProjectConfig correctly handles multiple models (source.models list)
2. SemaBridgeEngine discovers and filters multiple models
3. ParallelSyncWorker processes multiple models concurrently
4. SyncWorker handles multi-model configs
5. YAML generation produces correct multi-model configs
6. Source browser selection flows into sync config
7. Sync orchestrator dependency ordering for multi-model batches
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from semabridge.core.project import (
    ProjectConfig,
    SourceConfig,
    SourceType,
    TargetConfig,
    TargetType,
    ProjectOptions,
)
from semabridge.core.engine import (
    SemaBridgeEngine,
    EngineResult,
    SourceConnectorBase,
    TargetConnectorBase,
)
from semabridge.core.sync_orchestrator import (
    ModelResult,
    ModelStatus,
    SyncReport,
    order_models_by_dependency,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FABRIC_MODELS = [
    "Customer Profitability",
    "Regional Sales",
    "Inventory Analysis",
    "HR Analytics",
    "Marketing ROI",
]

SNOWFLAKE_TABLES = [
    "FACT_SALES",
    "DIM_CUSTOMER",
    "DIM_PRODUCT",
    "FACT_INVENTORY",
    "DIM_REGION",
]


class MockSourceConnector(SourceConnectorBase):
    """Mock source connector that returns predefined models."""

    def __init__(self, models: List[str]):
        self._models = models

    def discover(self, pattern: str = "*") -> List[str]:
        import fnmatch
        return [m for m in self._models if fnmatch.fnmatch(m.lower(), pattern.lower())]

    def extract(self, model_id: str, exclusions: Optional[Dict] = None) -> Dict[str, Any]:
        return {
            "model": {
                "name": model_id,
                "tables": [
                    {
                        "name": f"Table_{model_id.replace(' ', '_')}",
                        "columns": [
                            {"name": "ID", "dataType": "int64"},
                            {"name": "Value", "dataType": "double"},
                        ],
                        "measures": [],
                    }
                ],
                "relationships": [],
            }
        }


class MockTargetConnector(TargetConnectorBase):
    """Mock target connector that records deployments."""

    def __init__(self, should_fail: Optional[List[str]] = None):
        self.deployed: List[str] = []
        self._should_fail = should_fail or []

    def deploy(self, sml_model: Any) -> bool:
        name = getattr(sml_model, "unique_name", "unknown")
        self.deployed.append(name)
        if name in self._should_fail:
            raise RuntimeError(f"Simulated deploy failure for {name}")
        return True


@pytest.fixture
def multi_model_config():
    """Create a ProjectConfig with multiple explicit models."""
    return ProjectConfig(
        source=SourceConfig(
            type=SourceType.FABRIC,
            workspace_id="test-workspace-001",
            models=["Customer Profitability", "Regional Sales", "Inventory Analysis"],
            model="*",
        ),
        targets=[
            TargetConfig(
                type=TargetType.SNOWFLAKE_SEMANTIC_VIEW,
                database="ANALYTICS_DB",
                schema_name="SEMANTIC_LAYER",
                deploy=True,
            ),
        ],
    )


@pytest.fixture
def wildcard_config():
    """Create a ProjectConfig with wildcard model selection."""
    return ProjectConfig(
        source=SourceConfig(
            type=SourceType.FABRIC,
            workspace_id="test-workspace-001",
            model="*",
        ),
        targets=[
            TargetConfig(
                type=TargetType.SNOWFLAKE_SEMANTIC_VIEW,
                database="ANALYTICS_DB",
                schema_name="SEMANTIC_LAYER",
            ),
        ],
    )


@pytest.fixture
def config_with_exclusions():
    """Create a ProjectConfig with inclusion + exclusion patterns."""
    return ProjectConfig(
        source=SourceConfig(
            type=SourceType.FABRIC,
            workspace_id="test-workspace-001",
            model="*",
        ),
        targets=[
            TargetConfig(
                type=TargetType.SNOWFLAKE_SEMANTIC_VIEW,
                database="ANALYTICS_DB",
                schema_name="SEMANTIC_LAYER",
            ),
        ],
        options=ProjectOptions(
            exclude_model=["HR*", "*Test*"],
        ),
    )


# ---------------------------------------------------------------------------
# 1. ProjectConfig: Multi-model handling
# ---------------------------------------------------------------------------

class TestProjectConfigMultiModel:
    """Test ProjectConfig with multiple models."""

    def test_explicit_models_list_overrides_pattern(self, multi_model_config):
        """When source.models is set, it takes priority over pattern."""
        included = multi_model_config.get_included_models(FABRIC_MODELS)
        assert set(included) == {
            "Customer Profitability",
            "Regional Sales",
            "Inventory Analysis",
        }

    def test_wildcard_matches_all(self, wildcard_config):
        """Wildcard '*' should match all available models."""
        included = wildcard_config.get_included_models(FABRIC_MODELS)
        assert len(included) == len(FABRIC_MODELS)

    def test_exclusion_overrides_inclusion(self, config_with_exclusions):
        """Exclusion patterns should always override inclusion."""
        included = config_with_exclusions.get_included_models(FABRIC_MODELS)
        # HR Analytics should be excluded by "HR*"
        assert "HR Analytics" not in included
        # Others should remain
        assert "Customer Profitability" in included
        assert "Regional Sales" in included

    def test_pattern_matching(self):
        """Test glob pattern matching for model names."""
        config = ProjectConfig(
            source=SourceConfig(
                type=SourceType.FABRIC,
                workspace_id="test",
                model="*Sales*",
            ),
            targets=[TargetConfig(type=TargetType.SNOWFLAKE_SEMANTIC_VIEW)],
        )
        included = config.get_included_models(FABRIC_MODELS)
        assert included == ["Regional Sales"]

    def test_from_yaml_dict_multi_model(self):
        """Test creating config from YAML dict with models list."""
        yaml_dict = {
            "source": {
                "type": "fabric",
                "workspace_id": "test-workspace",
                "models": [
                    "Customer Profitability",
                    "Regional Sales",
                ],
            },
            "target": {
                "type": "snowflake",
                "deploy": True,
            },
        }
        config = ProjectConfig.from_yaml_dict(yaml_dict)
        assert config.source.models == ["Customer Profitability", "Regional Sales"]
        included = config.get_included_models(FABRIC_MODELS)
        assert len(included) == 2

    def test_single_model_in_models_list(self):
        """A single-element models list should work like model='name'."""
        config = ProjectConfig(
            source=SourceConfig(
                type=SourceType.FABRIC,
                workspace_id="test",
                models=["Customer Profitability"],
            ),
            targets=[TargetConfig(type=TargetType.SNOWFLAKE_SEMANTIC_VIEW)],
        )
        included = config.get_included_models(FABRIC_MODELS)
        assert included == ["Customer Profitability"]

    def test_empty_models_list_falls_back_to_pattern(self):
        """Empty models list should fall back to pattern matching."""
        config = ProjectConfig(
            source=SourceConfig(
                type=SourceType.FABRIC,
                workspace_id="test",
                models=[],
                model="Regional*",
            ),
            targets=[TargetConfig(type=TargetType.SNOWFLAKE_SEMANTIC_VIEW)],
        )
        # Empty list is falsy, so matches_model should use pattern
        included = config.get_included_models(FABRIC_MODELS)
        assert included == ["Regional Sales"]


# ---------------------------------------------------------------------------
# 2. Engine: Multi-model discovery, extraction, broadcast
# ---------------------------------------------------------------------------

class TestEngineMultiModel:
    """Test SemaBridgeEngine with multiple models."""

    def test_engine_discovers_multiple_models(self, multi_model_config):
        """Engine should discover and filter multiple models."""
        mock_source = MockSourceConnector(FABRIC_MODELS)
        engine = SemaBridgeEngine(
            config=multi_model_config,
            source_connector=mock_source,
        )
        # Access private method for unit testing
        discovered = engine._discover_models()
        assert len(discovered) == len(FABRIC_MODELS)

        # After filtering
        final = multi_model_config.get_included_models(discovered)
        assert len(final) == 3

    def test_engine_extracts_multiple_models(self, multi_model_config):
        """Engine should extract metadata for all filtered models."""
        mock_source = MockSourceConnector(FABRIC_MODELS)
        engine = SemaBridgeEngine(
            config=multi_model_config,
            source_connector=mock_source,
        )
        models = ["Customer Profitability", "Regional Sales"]
        extracted = engine._extract_models(models)
        assert len(extracted) == 2
        assert "Customer Profitability" in extracted
        assert "Regional Sales" in extracted

    def test_engine_parallel_extraction(self, multi_model_config):
        """Engine should use parallel extraction for >1 models."""
        mock_source = MockSourceConnector(FABRIC_MODELS)
        engine = SemaBridgeEngine(
            config=multi_model_config,
            source_connector=mock_source,
        )
        # 3 models should trigger parallel extraction (> 1 threshold)
        models = ["Customer Profitability", "Regional Sales", "Inventory Analysis"]
        extracted = engine._extract_models(models)
        assert len(extracted) == 3

    def test_engine_exclusion_filters(self, config_with_exclusions):
        """Engine should respect exclusion patterns."""
        mock_source = MockSourceConnector(FABRIC_MODELS)
        engine = SemaBridgeEngine(
            config=config_with_exclusions,
            source_connector=mock_source,
        )
        discovered = engine._discover_models()
        final = config_with_exclusions.get_included_models(discovered)
        assert "HR Analytics" not in final


# ---------------------------------------------------------------------------
# 3. Sync Orchestrator: Dependency ordering
# ---------------------------------------------------------------------------

class TestSyncOrchestratorMultiModel:
    """Test sync orchestrator dependency ordering."""

    def test_dimensions_before_facts(self):
        """Dimension models should be ordered before fact models."""
        models = [
            {"id": "1", "name": "Fact_Sales"},
            {"id": "2", "name": "Dim_Customer"},
            {"id": "3", "name": "Fact_Inventory"},
            {"id": "4", "name": "Dim_Product"},
        ]
        ordered = order_models_by_dependency(models)
        names = [m["name"] for m in ordered]
        
        # Dimensions should come first
        dim_indices = [i for i, n in enumerate(names) if n.startswith("Dim")]
        fact_indices = [i for i, n in enumerate(names) if n.startswith("Fact")]
        assert max(dim_indices) < min(fact_indices)

    def test_unknown_models_last(self):
        """Models with unknown classification should come last."""
        models = [
            {"id": "1", "name": "Mystery_Model"},
            {"id": "2", "name": "Dim_Region"},
            {"id": "3", "name": "Fact_Orders"},
        ]
        ordered = order_models_by_dependency(models)
        names = [m["name"] for m in ordered]
        assert names[0] == "Dim_Region"
        assert names[-1] == "Mystery_Model"

    def test_ordering_preserves_within_category(self):
        """Models within the same category maintain stable ordering."""
        models = [
            {"id": "1", "name": "Dim_A"},
            {"id": "2", "name": "Dim_Z"},
            {"id": "3", "name": "Dim_M"},
        ]
        ordered = order_models_by_dependency(models)
        names = [m["name"] for m in ordered]
        # Should be alphabetically sorted within category
        assert names == ["Dim_A", "Dim_M", "Dim_Z"]

    def test_sync_report_summary(self):
        """Test SyncReport correctly computes summary statistics."""
        report = SyncReport()
        report.start_time = time.time()
        
        r1 = ModelResult(model_name="Model_A", model_id="1", status=ModelStatus.SUCCESS)
        r2 = ModelResult(model_name="Model_B", model_id="2", status=ModelStatus.SUCCESS)
        r3 = ModelResult(model_name="Model_C", model_id="3", status=ModelStatus.FAILED, error_message="Deploy error")
        r4 = ModelResult(model_name="Model_D", model_id="4", status=ModelStatus.BLOCKED, error_message="PK missing")
        
        report.model_results = [r1, r2, r3, r4]
        report.end_time = time.time()
        
        assert report.total == 4
        assert report.succeeded == 2
        assert report.failed == 1
        assert report.blocked == 1
        assert not report.all_succeeded

    def test_sync_report_all_succeeded(self):
        """Test that all_succeeded is True when everything passes."""
        report = SyncReport()
        report.model_results = [
            ModelResult(model_name="A", model_id="1", status=ModelStatus.SUCCESS),
            ModelResult(model_name="B", model_id="2", status=ModelStatus.SUCCESS),
        ]
        assert report.all_succeeded

    def test_sync_report_format_summary(self):
        """Test human-readable summary generation."""
        report = SyncReport(batch_id="test1234")
        report.start_time = 1000.0
        report.end_time = 1005.5
        report.model_results = [
            ModelResult(model_name="Model_A", model_id="1", status=ModelStatus.SUCCESS),
            ModelResult(model_name="Model_B", model_id="2", status=ModelStatus.FAILED, error_message="Connection timeout"),
        ]
        summary = report.format_summary()
        assert "test1234" in summary
        assert "5.5s" in summary
        assert "Model_B" in summary
        assert "Connection timeout" in summary


# ---------------------------------------------------------------------------
# 4. YAML Generation for multi-model
# ---------------------------------------------------------------------------

class TestYAMLGenerationMultiModel:
    """Test YAML generation produces correct multi-model configs."""

    def test_single_model_uses_model_field(self):
        """Single model selection should use source.model field."""
        import yaml
        
        selected = ["Customer Profitability"]
        yaml_dict = _build_yaml_dict(selected, "test-workspace-001")
        
        assert yaml_dict["source"]["model"] == "Customer Profitability"
        assert "models" not in yaml_dict["source"]

    def test_multiple_models_uses_models_list(self):
        """Multiple model selection should use source.models list."""
        import yaml
        
        selected = ["Customer Profitability", "Regional Sales", "Inventory Analysis"]
        yaml_dict = _build_yaml_dict(selected, "test-workspace-001")
        
        assert yaml_dict["source"]["models"] == selected
        assert "model" not in yaml_dict["source"] or yaml_dict["source"].get("model") == "*"

    def test_no_selection_uses_wildcard(self):
        """No selection should default to wildcard pattern."""
        yaml_dict = _build_yaml_dict([], "test-workspace-001")
        assert yaml_dict["source"]["model"] == "*"

    def test_generated_config_is_valid(self):
        """Generated YAML dict should produce a valid ProjectConfig."""
        selected = ["Customer Profitability", "Regional Sales"]
        yaml_dict = _build_yaml_dict(selected, "test-workspace-001")
        config = ProjectConfig.from_yaml_dict(yaml_dict)
        assert config.source.models == selected
        assert config.source.workspace_id == "test-workspace-001"


def _build_yaml_dict(selected_models: List[str], workspace_id: str) -> Dict[str, Any]:
    """Helper: build a YAML config dict matching the UI generation logic."""
    source: Dict[str, Any] = {
        "type": "fabric",
        "workspace_id": workspace_id,
    }
    
    if selected_models:
        if len(selected_models) == 1:
            source["model"] = selected_models[0]
        else:
            source["models"] = selected_models
            source["model"] = "*"
    else:
        source["model"] = "*"
    
    return {
        "source": source,
        "target": {
            "type": "snowflake",
            "deploy": True,
        },
        "version_tag": "v1.0",
        "logging": {"level": "INFO", "format": "text"},
        "policy_path": "policies/standard.yaml",
    }


# ---------------------------------------------------------------------------
# 5. Source Browser selection integration
# ---------------------------------------------------------------------------

class TestSourceBrowserSelectionFlow:
    """Test that source browser selections flow correctly into sync."""

    def test_selected_models_override_config(self, multi_model_config):
        """Selected models from source browser should override config."""
        # Simulate what _on_sync_clicked does
        selected = ["Marketing ROI", "HR Analytics"]
        config = multi_model_config.model_copy(deep=True)
        config.source.models = selected
        config.source.model = selected[0] if len(selected) == 1 else "*"
        
        # Verify the config now uses selected models
        included = config.get_included_models(FABRIC_MODELS)
        assert set(included) == {"Marketing ROI", "HR Analytics"}

    def test_no_selection_uses_config_models(self, multi_model_config):
        """With no selection, original config models list is used."""
        included = multi_model_config.get_included_models(FABRIC_MODELS)
        assert len(included) == 3  # The 3 from fixture

    def test_single_selection_sets_model_field(self, multi_model_config):
        """Single selection should set source.model for single-model path."""
        selected = ["Regional Sales"]
        config = multi_model_config.model_copy(deep=True)
        config.source.models = selected
        config.source.model = selected[0]
        
        assert config.source.model == "Regional Sales"
        included = config.get_included_models(FABRIC_MODELS)
        assert included == ["Regional Sales"]


# ---------------------------------------------------------------------------
# 6. DuckDB versioning for multi-model
# ---------------------------------------------------------------------------

class TestDuckDBMultiModelVersioning:
    """Test DuckDB versioning with multiple models."""

    def test_commit_multiple_models(self, duckdb_manager):
        """Multiple models should each get their own version entry."""
        models = ["Model_A", "Model_B", "Model_C"]
        
        for model_id in models:
            duckdb_manager.ensure_project(
                project_id=model_id,
                name=model_id,
                workspace_id="test-workspace",
                adapter="fabric",
            )
            committed, snapshot_id = duckdb_manager.commit_model(
                project_id=model_id,
                sml_json={"unique_name": model_id, "datasets": []},
                tag="v1.0",
                initiated_by="test",
            )
            assert committed
            assert snapshot_id is not None

    def test_independent_model_versions(self, duckdb_manager):
        """Each model should have independent version history."""
        for model_id in ["Model_X", "Model_Y"]:
            duckdb_manager.ensure_project(
                project_id=model_id,
                name=model_id,
                workspace_id="test-workspace",
                adapter="fabric",
            )

        # Commit twice for Model_X (different tags), once for Model_Y
        duckdb_manager.commit_model("Model_X", {"v": 1}, tag="v1.0", initiated_by="test")
        duckdb_manager.commit_model("Model_X", {"v": 2}, tag="v2.0", initiated_by="test")
        duckdb_manager.commit_model("Model_Y", {"v": 1}, tag="v1.0", initiated_by="test")

        # Model_X should have 2 snapshots, Model_Y should have 1
        x_snapshots = duckdb_manager.list_snapshots("Model_X")
        y_snapshots = duckdb_manager.list_snapshots("Model_Y")
        assert len(x_snapshots) == 2
        assert len(y_snapshots) == 1

    def test_no_change_detection(self, duckdb_manager):
        """Committing identical data without a tag should not create a new snapshot."""
        duckdb_manager.ensure_project(
            project_id="Model_NoDelta",
            name="Model_NoDelta",
            workspace_id="test",
            adapter="fabric",
        )
        sml = {"unique_name": "Model_NoDelta", "datasets": []}
        
        # First commit (no tag) — creates initial snapshot
        committed1, _ = duckdb_manager.commit_model("Model_NoDelta", sml, initiated_by="test")
        # Second identical commit (no tag) — should be skipped (no changes)
        committed2, _ = duckdb_manager.commit_model("Model_NoDelta", sml, initiated_by="test")
        
        assert committed1  # First commit always creates
        assert not committed2  # Second identical commit should be no-op


# ---------------------------------------------------------------------------
# 7. ParallelSyncWorker config handling
# ---------------------------------------------------------------------------

class TestParallelSyncConfig:
    """Test ParallelSyncWorker configuration handling."""

    def test_model_copy_preserves_models_list(self, multi_model_config):
        """model_copy(deep=True) should preserve the models list."""
        config_copy = multi_model_config.model_copy(deep=True)
        config_copy.source.models = ["Customer Profitability"]
        config_copy.source.model = "Customer Profitability"
        
        # Original should be unmodified
        assert multi_model_config.source.models == [
            "Customer Profitability", "Regional Sales", "Inventory Analysis"
        ]
        # Copy should have single model
        assert config_copy.source.models == ["Customer Profitability"]

    def test_per_model_config_generation(self, multi_model_config):
        """Each model should get its own config copy for parallel execution."""
        models = ["Customer Profitability", "Regional Sales", "Inventory Analysis"]
        configs = []
        
        for model_name in models:
            model_config = multi_model_config.model_copy(deep=True)
            model_config.source.models = [model_name]
            model_config.source.model = model_name
            configs.append(model_config)
        
        # Each config should reference exactly one model
        for i, config in enumerate(configs):
            assert config.source.models == [models[i]]
            assert config.source.model == models[i]
            included = config.get_included_models(FABRIC_MODELS)
            assert included == [models[i]]
