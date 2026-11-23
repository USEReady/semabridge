"""Tests verifying regression_gate.py validation of known_failures.txt entries."""

import pytest
import sys
from pathlib import Path

# Add Scripts to sys.path to test regression gate validation directly
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "Scripts"))
from regression_gate import _load_known_failures


def test_regression_gate_rejects_whole_file_and_missing_reason(tmp_path):
    # 1. Whole file without '::'
    kf1 = tmp_path / "kf1.txt"
    kf1.write_text("Tests/test_whole_file.py # reason\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must match a single test name containing '::'"):
        _load_known_failures(kf1)

    # 2. Missing reason
    kf2 = tmp_path / "kf2.txt"
    kf2.write_text("Tests/test_file.py::test_func\n", encoding="utf-8")
    with pytest.raises(ValueError, match="reason required"):
        _load_known_failures(kf2)

    # 3. Empty reason after '#'
    kf3 = tmp_path / "kf3.txt"
    kf3.write_text("Tests/test_file.py::test_func # \n", encoding="utf-8")
    with pytest.raises(ValueError, match="reason required after '#'"):
        _load_known_failures(kf3)

    # 4. Pattern with wildcard
    kf4 = tmp_path / "kf4.txt"
    kf4.write_text("Tests/test_file.py::test_* # wildcard reason\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must match a single test name containing '::'"):
        _load_known_failures(kf4)

    # 5. Valid entry with single test and reason
    kf5 = tmp_path / "kf5.txt"
    kf5.write_text(
        "# Header comment\n"
        "\n"
        "Tests/test_file.py::test_func # genuine failure\n",
        encoding="utf-8",
    )
    loaded = _load_known_failures(kf5)
    assert loaded == {"Tests/test_file.py::test_func"}
