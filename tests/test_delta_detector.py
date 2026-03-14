"""Tests for Mandate 5: Delta Detector — change detection for hyperscaling."""

import json
import pytest
from pathlib import Path
from semabridge.core.concurrency.delta_detector import DeltaDetector, DeltaStrategy


@pytest.fixture
def snapshot_dir(tmp_path):
    """Create a temporary snapshot directory."""
    sml_dir = tmp_path / "sml"
    sml_dir.mkdir()
    return sml_dir


@pytest.fixture
def sample_models():
    return [
        {"unique_name": "ModelA", "version": "1.0", "tables": ["t1", "t2"]},
        {"unique_name": "ModelB", "version": "1.0", "tables": ["t3"]},
        {"unique_name": "ModelC", "version": "2.0", "tables": ["t4", "t5"]},
    ]


class TestDeltaDetectorHash:
    """Test hash-based delta detection."""

    def test_first_run_all_changed(self, snapshot_dir, sample_models):
        detector = DeltaDetector(strategy="hash", snapshot_dir=snapshot_dir)
        changed = detector.detect(sample_models)
        assert set(changed) == {"ModelA", "ModelB", "ModelC"}

    def test_second_run_no_changes(self, snapshot_dir, sample_models):
        detector = DeltaDetector(strategy="hash", snapshot_dir=snapshot_dir)
        detector.detect(sample_models)

        # Second run — same models, no changes
        detector2 = DeltaDetector(strategy="hash", snapshot_dir=snapshot_dir)
        changed = detector2.detect(sample_models)
        assert changed == []

    def test_detects_modified_model(self, snapshot_dir, sample_models):
        detector = DeltaDetector(strategy="hash", snapshot_dir=snapshot_dir)
        detector.detect(sample_models)

        # Modify one model
        sample_models[1]["version"] = "2.0"

        detector2 = DeltaDetector(strategy="hash", snapshot_dir=snapshot_dir)
        changed = detector2.detect(sample_models)
        assert changed == ["ModelB"]

    def test_detects_new_model(self, snapshot_dir, sample_models):
        detector = DeltaDetector(strategy="hash", snapshot_dir=snapshot_dir)
        detector.detect(sample_models)

        sample_models.append({"unique_name": "ModelD", "version": "1.0"})

        detector2 = DeltaDetector(strategy="hash", snapshot_dir=snapshot_dir)
        changed = detector2.detect(sample_models)
        assert changed == ["ModelD"]


class TestDeltaDetectorManifest:
    """Test manifest persistence."""

    def test_manifest_created(self, snapshot_dir, sample_models):
        detector = DeltaDetector(strategy="hash", snapshot_dir=snapshot_dir)
        detector.detect(sample_models)
        assert (snapshot_dir / "_delta_manifest.json").exists()

    def test_manifest_content(self, snapshot_dir, sample_models):
        detector = DeltaDetector(strategy="hash", snapshot_dir=snapshot_dir)
        detector.detect(sample_models)

        manifest = json.loads(
            (snapshot_dir / "_delta_manifest.json").read_text()
        )
        assert "ModelA" in manifest
        assert "ModelB" in manifest
        assert len(manifest["ModelA"]) == 64  # SHA-256 hex length

    def test_force_full_clears_manifest(self, snapshot_dir, sample_models):
        detector = DeltaDetector(strategy="hash", snapshot_dir=snapshot_dir)
        detector.detect(sample_models)
        assert (snapshot_dir / "_delta_manifest.json").exists()

        detector.force_full()
        assert not (snapshot_dir / "_delta_manifest.json").exists()


class TestDeltaStrategy:
    """Test DeltaStrategy enum."""

    def test_hash_value(self):
        assert DeltaStrategy.HASH.value == "hash"

    def test_git_value(self):
        assert DeltaStrategy.GIT.value == "git"

    def test_hybrid_value(self):
        assert DeltaStrategy.HYBRID.value == "hybrid"
