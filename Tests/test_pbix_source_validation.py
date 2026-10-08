from __future__ import annotations

import pytest

from semabridge.api.services import pbix_source_validation as psv
from semabridge.domain.exceptions import ValidationError


def test_noop_for_non_pbix_source_type():
    # fabric/snowflake `source.models` holds model *names*, not file paths —
    # must never be rejected by this pbix-only validator.
    psv.validate_pbix_model_list(["ModelA", "not-a-path-at-all"], source_type="fabric")
    psv.validate_pbix_model_list(["SomeView"], source_type="snowflake")


def test_noop_for_empty_or_missing_models():
    psv.validate_pbix_model_list(None, source_type="pbix")
    psv.validate_pbix_model_list([], source_type="pbix")


def test_noop_for_single_legacy_pbix_path_flow():
    # Today's single-file flow never populates `models` — confirms this
    # validator can be called unconditionally without affecting it.
    psv.validate_pbix_model_list(None, source_type="pbix")


def test_accepts_valid_full_paths():
    psv.validate_pbix_model_list(
        ["C:/Reports/Sales Report.pbix", "C:/Reports/Marketing Analysis.pbix"],
        source_type="pbix",
    )


def test_accepts_source_type_case_insensitively():
    psv.validate_pbix_model_list(["C:/Reports/Sales.pbix"], source_type="PBIX")


def test_rejects_more_than_max_files():
    files = [f"C:/Reports/Report{i}.pbix" for i in range(psv.MAX_PBIX_FILES + 1)]
    with pytest.raises(ValidationError, match="Up to 10 PBIX files"):
        psv.validate_pbix_model_list(files, source_type="pbix")


def test_rejects_non_list_models():
    with pytest.raises(ValidationError, match="must be a list"):
        psv.validate_pbix_model_list("C:/Reports/Sales.pbix", source_type="pbix")  # type: ignore[arg-type]


def test_rejects_empty_string_entry():
    with pytest.raises(ValidationError, match="non-empty file path"):
        psv.validate_pbix_model_list(["C:/Reports/Sales.pbix", "  "], source_type="pbix")


def test_rejects_non_pbix_extension():
    with pytest.raises(ValidationError, match="does not look like a .pbix file"):
        psv.validate_pbix_model_list(["C:/Reports/Sales.xlsx"], source_type="pbix")


def test_rejects_bare_model_name_stem_not_a_path():
    # This is exactly the shape today's fabric/snowflake flow sends — must be
    # rejected for pbix so a caller can't accidentally send stems instead of
    # full paths for the new multi-file feature.
    with pytest.raises(ValidationError, match="does not look like a .pbix file"):
        psv.validate_pbix_model_list(["SalesReport"], source_type="pbix")


def test_rejects_duplicate_paths():
    with pytest.raises(ValidationError, match="Duplicate file selected"):
        psv.validate_pbix_model_list(
            ["C:/Reports/Sales.pbix", "C:/Reports/Sales.pbix"],
            source_type="pbix",
        )


def test_max_pbix_files_matches_sync_execution_service_cap():
    # These two constants are intentionally duplicated (see module docstring)
    # to avoid an api.services -> api.services import ordering risk; this test
    # is what actually keeps them from drifting apart.
    from semabridge.api.services.sync_execution_service import MAX_BATCH_MODELS

    assert psv.MAX_PBIX_FILES == MAX_BATCH_MODELS
