"""Regression tests for structural-duplicate view detection (GitHub #10 /
Prompt 3): a re-upload of the same underlying model under a different file
name (e.g. Probability.pbix vs. probablility.pbix, confirmed live in
Snowflake to otherwise create two separate views) must redeploy to the
already-deployed view instead of creating a duplicate one.
"""

from __future__ import annotations

from types import SimpleNamespace

from semabridge.repository.model_fingerprint_repository import ModelFingerprintRepository
from semabridge.utils.model_dedup import fingerprint_model
from semabridge.core.engine.targets import snowflake as targets_snowflake


def _make_sml(unique_name: str, revenue_col: str = "Revenue") -> SimpleNamespace:
    dataset = SimpleNamespace(
        unique_name="Fact",
        columns=[SimpleNamespace(unique_name=revenue_col), SimpleNamespace(unique_name="Cost")],
    )
    metric = SimpleNamespace(unique_name="Total Revenue", aggregation="sum", dataset="Fact")
    return SimpleNamespace(
        unique_name=unique_name,
        label=unique_name,
        datasets=[dataset],
        metrics=[metric],
        relationships=[],
    )


class TestModelFingerprintRepository:
    def test_new_fingerprint_has_no_existing_view(self):
        repo = ModelFingerprintRepository()
        assert repo.find_existing_view_name("snowflake|DB|SCHEMA", "fp-new-1") is None

    def test_record_then_find_round_trips(self):
        repo = ModelFingerprintRepository()
        repo.record_view_name(
            scope_key="snowflake|DB|SCHEMA",
            structural_fingerprint="fp-roundtrip",
            view_name="abc123_probability",
            source_name="abc123_probability",
            project_id="proj-1",
        )
        assert repo.find_existing_view_name("snowflake|DB|SCHEMA", "fp-roundtrip") == "abc123_probability"

    def test_different_scope_keys_do_not_collide(self):
        repo = ModelFingerprintRepository()
        repo.record_view_name(
            scope_key="snowflake|DB1|SCHEMA",
            structural_fingerprint="fp-scoped",
            view_name="view_in_db1",
        )
        assert repo.find_existing_view_name("snowflake|DB2|SCHEMA", "fp-scoped") is None
        assert repo.find_existing_view_name("snowflake|DB1|SCHEMA", "fp-scoped") == "view_in_db1"

    def test_re_recording_updates_the_view_name(self):
        repo = ModelFingerprintRepository()
        repo.record_view_name(scope_key="snowflake|DB|SCHEMA", structural_fingerprint="fp-update", view_name="first_name")
        repo.record_view_name(scope_key="snowflake|DB|SCHEMA", structural_fingerprint="fp-update", view_name="second_name")
        assert repo.find_existing_view_name("snowflake|DB|SCHEMA", "fp-update") == "second_name"


class TestFingerprintModelMatchesRealDuplicateCase:
    def test_same_structure_different_names_share_a_fingerprint(self):
        """Mirrors the confirmed live case: Probability.pbix / probablility.pbix
        produce structurally identical SML (same tables/columns/metrics) but
        different unique_name values from their different upload file names.
        """
        model_a = _make_sml(unique_name="6cc7b1e17b824f2ab9b02901ddc00a48_Probability")
        model_b = _make_sml(unique_name="a024b8eba20a4be78830f6479c9b048d_probablility")
        assert fingerprint_model(model_a) == fingerprint_model(model_b)

    def test_genuinely_different_models_do_not_share_a_fingerprint(self):
        model_a = _make_sml(unique_name="device_upload")
        model_b = SimpleNamespace(
            unique_name="competitive_marketing_upload",
            label="competitive_marketing_upload",
            datasets=[SimpleNamespace(unique_name="Sales", columns=[SimpleNamespace(unique_name="Region")])],
            metrics=[SimpleNamespace(unique_name="Total Sales", aggregation="sum", dataset="Sales")],
            relationships=[],
        )
        assert fingerprint_model(model_a) != fingerprint_model(model_b)


class TestReuseExistingViewForStructuralDuplicate:
    def test_rewrites_unique_name_when_fingerprint_matches_a_different_existing_view(self, monkeypatch):
        sml = _make_sml(unique_name="a024b8eba20a4be78830f6479c9b048d_probablility")
        context = SimpleNamespace(
            sml_model=sml,
            config=SimpleNamespace(snowflake=SimpleNamespace(database="SEMABRIDGE", schema_name="SEMABRIDGE_WORKSPACE")),
            model_structural_fingerprint=None,
            model_fingerprint_scope_key=None,
        )

        class _FakeRepo:
            def find_existing_view_name(self, scope_key, fp):
                assert scope_key == "snowflake|SEMABRIDGE|SEMABRIDGE_WORKSPACE"
                return "6cc7b1e17b824f2ab9b02901ddc00a48_Probability"

        monkeypatch.setattr(
            "semabridge.repository.model_fingerprint_repository.ModelFingerprintRepository",
            _FakeRepo,
        )

        targets_snowflake._reuse_existing_view_for_structural_duplicate(None, context)

        assert context.sml_model.unique_name == "6cc7b1e17b824f2ab9b02901ddc00a48_Probability"
        assert context.model_structural_fingerprint is not None
        assert context.model_fingerprint_scope_key == "snowflake|SEMABRIDGE|SEMABRIDGE_WORKSPACE"

    def test_leaves_unique_name_alone_when_no_match_found(self, monkeypatch):
        sml = _make_sml(unique_name="brand_new_upload")
        context = SimpleNamespace(
            sml_model=sml,
            config=SimpleNamespace(snowflake=SimpleNamespace(database="SEMABRIDGE", schema_name="SEMABRIDGE_WORKSPACE")),
            model_structural_fingerprint=None,
            model_fingerprint_scope_key=None,
        )

        class _FakeRepo:
            def find_existing_view_name(self, scope_key, fp):
                return None

        monkeypatch.setattr(
            "semabridge.repository.model_fingerprint_repository.ModelFingerprintRepository",
            _FakeRepo,
        )

        targets_snowflake._reuse_existing_view_for_structural_duplicate(None, context)

        assert context.sml_model.unique_name == "brand_new_upload"

    def test_never_raises_even_if_repository_lookup_fails(self, monkeypatch):
        sml = _make_sml(unique_name="brand_new_upload")
        context = SimpleNamespace(
            sml_model=sml,
            config=SimpleNamespace(snowflake=SimpleNamespace(database="SEMABRIDGE", schema_name="SEMABRIDGE_WORKSPACE")),
            model_structural_fingerprint=None,
            model_fingerprint_scope_key=None,
        )

        class _BrokenRepo:
            def __init__(self):
                raise RuntimeError("db unavailable")

        monkeypatch.setattr(
            "semabridge.repository.model_fingerprint_repository.ModelFingerprintRepository",
            _BrokenRepo,
        )

        # Must not raise -- dedup is best-effort, never a correctness requirement.
        targets_snowflake._reuse_existing_view_for_structural_duplicate(None, context)
        assert context.sml_model.unique_name == "brand_new_upload"
