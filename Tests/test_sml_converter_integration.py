import pytest

from semabridge.sml.converter_wrapper import find_sml_converters, run_sml_converters


def test_sml_converters_available():
    exe = find_sml_converters()
    if not exe:
        pytest.skip("sml-converters not installed; skipping integration test")

    # call with --version to verify it runs
    proc = run_sml_converters(["--version"])
    assert proc.returncode == 0
    assert proc.stdout.strip() != ""
