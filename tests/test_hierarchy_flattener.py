"""
Tests for HierarchyFlattener.

Validates DAX query generation for parent-child hierarchy flattening
and the post-processing of results.
"""

import pytest

from semabridge.converter.hierarchy_flattener import HierarchyFlattener


@pytest.fixture
def flattener() -> HierarchyFlattener:
    return HierarchyFlattener()


# ─────────────────────────────────────────────────────────────────────────────
# DAX Query Generation
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildFlatteningQuery:
    """Tests for build_flattening_query DAX output."""

    def test_basic_query_structure(self, flattener: HierarchyFlattener) -> None:
        query = flattener.build_flattening_query(
            table_name="Employee",
            id_column="EmployeeKey",
            parent_column="ManagerKey",
            max_depth=3,
        )

        assert "EVALUATE" in query
        assert "ADDCOLUMNS" in query
        assert "PATH(" in query
        assert "'Employee'[EmployeeKey]" in query
        assert "'Employee'[ManagerKey]" in query
        assert "PATHLENGTH" in query

    def test_max_depth_controls_levels(self, flattener: HierarchyFlattener) -> None:
        query = flattener.build_flattening_query(
            table_name="Account",
            id_column="AccountID",
            parent_column="ParentAccountID",
            max_depth=5,
        )

        assert '"Level1_Key"' in query
        assert '"Level5_Key"' in query
        assert '"Level6_Key"' not in query  # beyond max_depth

    def test_label_column_adds_lookupvalue(self, flattener: HierarchyFlattener) -> None:
        query = flattener.build_flattening_query(
            table_name="Employee",
            id_column="EmployeeKey",
            parent_column="ManagerKey",
            label_column="EmployeeName",
            max_depth=3,
        )

        assert "LOOKUPVALUE" in query
        assert '"Level1_Name"' in query
        assert '"Level3_Name"' in query

    def test_no_label_column_omits_lookupvalue(
        self, flattener: HierarchyFlattener
    ) -> None:
        query = flattener.build_flattening_query(
            table_name="Employee",
            id_column="EmployeeKey",
            parent_column="ManagerKey",
            max_depth=3,
        )

        assert "LOOKUPVALUE" not in query
        assert '"Level1_Name"' not in query

    def test_default_max_depth_is_10(self, flattener: HierarchyFlattener) -> None:
        query = flattener.build_flattening_query(
            table_name="Org",
            id_column="ID",
            parent_column="ParentID",
        )

        assert '"Level10_Key"' in query
        assert '"Level11_Key"' not in query


# ─────────────────────────────────────────────────────────────────────────────
# Result Post-Processing
# ─────────────────────────────────────────────────────────────────────────────

class TestFlattenResults:
    """Tests for flatten_results post-processing."""

    def test_removes_path_column(self, flattener: HierarchyFlattener) -> None:
        rows = [
            {"__Path": "1|2|3", "HierarchyDepth": 3, "Level1_Key": 1},
        ]
        result = flattener.flatten_results(rows, max_depth=3)
        assert "__Path" not in result[0]

    def test_nullifies_beyond_depth(self, flattener: HierarchyFlattener) -> None:
        rows = [
            {
                "HierarchyDepth": 2,
                "Level1_Key": 1,
                "Level2_Key": 2,
                "Level3_Key": 99,  # beyond actual depth
            },
        ]
        result = flattener.flatten_results(rows, max_depth=3)
        assert result[0]["Level1_Key"] == 1
        assert result[0]["Level2_Key"] == 2
        assert result[0]["Level3_Key"] is None  # cleared

    def test_handles_empty_input(self, flattener: HierarchyFlattener) -> None:
        result = flattener.flatten_results([], max_depth=5)
        assert result == []

    def test_preserves_rows_within_depth(self, flattener: HierarchyFlattener) -> None:
        rows = [
            {"HierarchyDepth": 3, "Level1_Key": "A", "Level2_Key": "B", "Level3_Key": "C"},
        ]
        result = flattener.flatten_results(rows, max_depth=3)
        assert result[0]["Level1_Key"] == "A"
        assert result[0]["Level2_Key"] == "B"
        assert result[0]["Level3_Key"] == "C"


# ─────────────────────────────────────────────────────────────────────────────
# Parent-Child Detection
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectParentChild:
    """Tests for detect_parent_child_columns static method."""

    def test_detects_parent_prefix(self) -> None:
        columns = [
            {"name": "AccountID"},
            {"name": "ParentAccountID"},
            {"name": "AccountName"},
        ]
        results = HierarchyFlattener.detect_parent_child_columns(columns)
        assert len(results) >= 1
        found = any(
            r["id_column"] == "AccountID" and r["parent_column"] == "ParentAccountID"
            for r in results
        )
        assert found, f"Expected AccountID/ParentAccountID pair, got {results}"

    def test_no_matches_returns_empty(self) -> None:
        columns = [
            {"name": "ProductID"},
            {"name": "ProductName"},
            {"name": "Category"},
        ]
        results = HierarchyFlattener.detect_parent_child_columns(columns)
        assert results == []
