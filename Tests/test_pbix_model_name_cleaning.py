import pytest
from pathlib import Path
from semabridge.utils.identifiers import clean_pbix_model_name


def test_pbix_model_name_32_hex_uuid_prefix():
    """Case 1: Standard uploaded PBIX file with 32-char hex UUID prefix and spaces."""
    filename = "d248e057de5a406686320df8758528fd_Competitive Marketing Analysis.pbix"
    path = Path("C:/tmp/uploads") / filename

    assert clean_pbix_model_name(filename) == "Competitive Marketing Analysis"
    assert clean_pbix_model_name(path) == "Competitive Marketing Analysis"


def test_pbix_model_name_32_hex_uuid_with_underscores_and_digits():
    """Case 2: 32-char hex UUID prefix with internal underscores and digits in real filename."""
    filename = "9da23522540f484e844e2a411222ef65_Sales_Data_2026_V2.pbix"
    path = Path("/tmp/semabridge/projects/proj123") / filename

    assert clean_pbix_model_name(filename) == "Sales_Data_2026_V2"
    assert clean_pbix_model_name(path) == "Sales_Data_2026_V2"


def test_pbix_model_name_no_hash_prefix():
    """Case 3: Non-uploaded local file path without any hash prefix."""
    filename = "Financial_Report.pbix"
    path = Path("D:/models/Financial_Report.pbix")

    assert clean_pbix_model_name(filename) == "Financial_Report"
    assert clean_pbix_model_name(path) == "Financial_Report"


def test_pbix_model_name_short_non_uuid_prefix():
    """Case 4: Short prefix or non-UUID prefix that must not be stripped."""
    filename = "test_sample.pbix"
    path = Path("test_sample.pbix")

    assert clean_pbix_model_name(filename) == "test_sample"
    assert clean_pbix_model_name(path) == "test_sample"

    # Also test an 8-char prefix (not 32 hex)
    filename8 = "a1b2c3d4_my_model.pbix"
    assert clean_pbix_model_name(filename8) == "a1b2c3d4_my_model"


def test_pbix_model_name_edge_cases():
    """Edge cases: empty inputs, stems without extension."""
    assert clean_pbix_model_name("") == ""
    assert clean_pbix_model_name(None) == ""
    # Pure stem input without .pbix extension
    assert clean_pbix_model_name("11223344556677889900aabbccddeeff_MyModel") == "MyModel"


def test_single_file_pbix_sync_model_naming():
    """Single-file PBIX sync (project 1443-test) must deploy under its PBIX filename, not project ID."""
    import time
    from types import SimpleNamespace
    from semabridge.core.engine.context import RunContext
    from semabridge.core.source_format import from_pbix_tmsl
    from semabridge.core.execution_engine import ExecutionEngine
    from semabridge.utils.name_translator import get_target_deployment_name

    pbix_filename = "5e6a0eb9d11a444d88d15702ef3723d7_Competitive Marketing Analysis.pbix"
    project_id = "1443-test"
    dummy_tmsl = {"model": {"name": "Model", "tables": []}}

    sf = from_pbix_tmsl(
        project_id=project_id,
        run_id="run-1",
        tmsl=dummy_tmsl,
        pbix_path=pbix_filename,
    )

    ctx = RunContext(
        project_id=project_id,
        run_id="run-1",
        config=SimpleNamespace(),
        start_time=time.time(),
        source_type="pbix",
        source_format=sf,
    )

    engine = ExecutionEngine()
    sml = engine._convert_pbix_to_sml(ctx)

    assert sml.unique_name == "Competitive Marketing Analysis"
    assert sml.unique_name != project_id

    # Verify target deployment view name resolution
    assert get_target_deployment_name(sml.unique_name, "snowflake") == "COMPETITIVE_MARKETING_ANALYSIS_SEMANTIC"


def test_pbix_naming_cross_entry_point_consistency():
    """All PBIX naming entry points must resolve to the identical final view name."""
    import time
    from types import SimpleNamespace
    from semabridge.core.engine.context import RunContext
    from semabridge.core.source_format import from_pbix_tmsl
    from semabridge.core.execution_engine import ExecutionEngine
    from semabridge.utils.name_translator import get_target_deployment_name

    pbix_filename = "5e6a0eb9d11a444d88d15702ef3723d7_Competitive Marketing Analysis.pbix"
    project_id = "1443-test"
    dummy_tmsl = {"model": {"name": "Model", "tables": []}}

    # 1. SourceFormat dataset_name
    sf = from_pbix_tmsl(
        project_id=project_id,
        run_id="run-1",
        tmsl=dummy_tmsl,
        pbix_path=pbix_filename,
    )
    name_1 = get_target_deployment_name(sf.dataset_name, "snowflake")

    # 2. ExecutionEngine _convert_pbix_to_sml
    ctx = RunContext(
        project_id=project_id,
        run_id="run-1",
        config=SimpleNamespace(),
        start_time=time.time(),
        source_type="pbix",
        source_format=sf,
    )
    engine = ExecutionEngine()
    sml = engine._convert_pbix_to_sml(ctx)
    name_2 = get_target_deployment_name(sml.unique_name, "snowflake")

    # 3. Direct clean_pbix_model_name
    name_3 = get_target_deployment_name(clean_pbix_model_name(pbix_filename), "snowflake")

    assert name_1 == "COMPETITIVE_MARKETING_ANALYSIS_SEMANTIC"
    assert name_2 == "COMPETITIVE_MARKETING_ANALYSIS_SEMANTIC"
    assert name_3 == "COMPETITIVE_MARKETING_ANALYSIS_SEMANTIC"
    assert name_1 == name_2 == name_3
