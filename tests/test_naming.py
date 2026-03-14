"""
Tests for the centralized Snowflake naming utility.

Validates that all naming levels (physical, alias, column, label)
behave consistently and handle edge cases.
"""

import pytest
from semabridge.utils.naming import (
    camel_to_snake,
    to_physical_name,
    to_alias,
    sanitize_column,
    sanitize_semantic_label,
    validate_semantic_view_sql,
    build_alias_rewrite_map,
    extract_prefixes_from_expressions,
    infer_override_alias_map,
    sanitize_sql_expression,
)


class TestCamelToSnake:
    """Unit tests for CamelCase → snake_case conversion."""

    @pytest.mark.parametrize(
        "input_name,expected",
        [
            ("SalesFact", "sales_fact"),
            ("CustomerDim", "customer_dim"),
            ("Product", "product"),
            ("ABCProduct", "abc_product"),
            ("SALES_FACT", "sales_fact"),
            ("Fact_Sales", "fact_sales"),
            ("CustomerDimV2", "customer_dim_v2"),
            ("simpleword", "simpleword"),
            ("IOError", "io_error"),
        ],
    )
    def test_camel_to_snake(self, input_name: str, expected: str) -> None:
        assert camel_to_snake(input_name) == expected


class TestToPhysicalName:
    """Tests for logical → UPPER_SNAKE_CASE physical table name."""

    @pytest.mark.parametrize(
        "input_name,expected",
        [
            ("SalesFact", "SALES_FACT"),
            ("CustomerDim", "CUSTOMER_DIM"),
            ("Product", "PRODUCT"),
            ("Fact_Sales", "FACT_SALES"),
            ("Customer Dim", "CUSTOMER_DIM"),
            ("customer-dim", "CUSTOMER_DIM"),
            ("DIM_DATE", "DIM_DATE"),
            ("ABCProduct", "ABC_PRODUCT"),
            ("", ""),
        ],
    )
    def test_to_physical_name(self, input_name: str, expected: str) -> None:
        assert to_physical_name(input_name) == expected


class TestToAlias:
    """Tests for logical → lowercase SQL alias."""

    @pytest.mark.parametrize(
        "input_name,expected",
        [
            ("SalesFact", "salesfact"),
            ("CustomerDim", "customerdim"),
            ("Product", "product"),
            ("Fact_Sales", "factsales"),
            ("Date", "l_date"),          # Reserved word
            ("Table", "l_table"),        # Reserved word
            ("123abc", "t123abc"),        # Leading digit
        ],
    )
    def test_to_alias(self, input_name: str, expected: str) -> None:
        assert to_alias(input_name) == expected


class TestSanitizeColumn:
    """Tests for column name sanitization."""

    @pytest.mark.parametrize(
        "input_name,expected",
        [
            ("Revenue", "REVENUE"),
            ("Product Key", "PRODUCT_KEY"),
            ("Country/Region", "COUNTRY_REGION"),
            ("[BracketedName]", "BRACKETEDNAME"),
            ("dot.name", "DOT_NAME"),
            ("back\\slash", "BACK_SLASH"),
            ("", "UNKNOWN"),
        ],
    )
    def test_sanitize_column(self, input_name: str, expected: str) -> None:
        assert sanitize_column(input_name, force_uppercase=True) == expected

    def test_sanitize_column_preserve_case(self) -> None:
        assert sanitize_column("Revenue", force_uppercase=False) == "Revenue"


class TestSanitizeSemanticLabel:
    """Tests for semantic label sanitization (same rules as column)."""

    def test_label_same_as_column(self) -> None:
        assert sanitize_semantic_label("Product Key") == "PRODUCT_KEY"
        assert sanitize_semantic_label("Country/Region") == "COUNTRY_REGION"


class TestNamingConsistency:
    """Cross-level consistency: alias used in SQL must be derivable from any name."""

    def test_alias_and_physical_derive_from_same_name(self) -> None:
        """Physical and alias should both be derivable from the logical name."""
        logical = "SalesFact"
        physical = to_physical_name(logical)
        alias = to_alias(logical)

        assert physical == "SALES_FACT"
        assert alias == "salesfact"

        # The alias must NOT equal the physical name (they serve different roles)
        assert alias != physical.lower()

    def test_generated_sql_pattern(self) -> None:
        """Simulate the expected SQL pattern: physical AS alias, alias.col."""
        logical = "CustomerDim"
        physical = to_physical_name(logical)
        alias = to_alias(logical)
        col = sanitize_column("Customer Name")

        from_clause = f'{physical} AS {alias}'
        col_ref = f'{alias}."{col}"'

        assert from_clause == "CUSTOMER_DIM AS customerdim"
        assert col_ref == 'customerdim."CUSTOMER_NAME"'


class TestValidateSemanticViewSQL:
    """Tests for pre-deployment SQL validation (Step 7)."""

    def _make_ddl(self, *, tables: str = "", dims: str = "", metrics: str = "") -> str:
        """Build a minimal Semantic View DDL for testing."""
        parts = ["CREATE OR REPLACE SEMANTIC VIEW db.SCHEMA.test_semantic"]
        if tables:
            parts.append(f"TABLES (\n{tables}\n)")
        if dims:
            parts.append(f"DIMENSIONS (\n{dims}\n)")
        if metrics:
            parts.append(f"METRICS (\n{metrics}\n)")
        return "\n".join(parts) + ";"

    def test_valid_ddl_passes(self) -> None:
        ddl = self._make_ddl(
            tables='  salesfact AS db.SCHEMA."SALES_FACT" PRIMARY KEY ("ID")',
            dims='  salesfact."REVENUE" AS "REVENUE"',
            metrics='  salesfact."TOTAL" AS SUM(salesfact."REVENUE")',
        )
        result = validate_semantic_view_sql(
            ddl=ddl,
            dataset_aliases={"SalesFact": "salesfact"},
        )
        assert result.valid

    def test_uppercase_alias_dot_unquoted_flagged(self) -> None:
        """SALESFACT.SCORE (uppercase alias, unquoted col) must be flagged."""
        ddl = self._make_ddl(
            tables='  salesfact AS db.SCHEMA."SALES_FACT" PRIMARY KEY ("ID")',
            dims='  SALESFACT.SCORE AS "SCORE"',
        )
        result = validate_semantic_view_sql(
            ddl=ddl,
            dataset_aliases={"SalesFact": "salesfact"},
        )
        assert not result.valid
        assert any("SALESFACT" in e for e in result.errors)

    def test_logical_name_leak_warned(self) -> None:
        """If the original CamelCase logical name appears, emit a warning."""
        ddl = self._make_ddl(
            tables='  salesfact AS db.SCHEMA."SALES_FACT" PRIMARY KEY ("ID")',
            dims='  SalesFact."REVENUE" AS "REVENUE"',
        )
        result = validate_semantic_view_sql(
            ddl=ddl,
            dataset_aliases={"SalesFact": "salesfact"},
        )
        assert any("SalesFact" in w for w in result.warnings)

    def test_undeclared_alias_flagged(self) -> None:
        """Alias used in METRICS but not in TABLES must be flagged."""
        ddl = self._make_ddl(
            tables='  salesfact AS db.SCHEMA."SALES_FACT" PRIMARY KEY ("ID")',
            metrics='  ghostalias."TOTAL" AS SUM(ghostalias."REVENUE")',
        )
        result = validate_semantic_view_sql(
            ddl=ddl,
            dataset_aliases={"SalesFact": "salesfact"},
        )
        assert not result.valid
        assert any("ghostalias" in e for e in result.errors)

    def test_physical_table_name_as_prefix_flagged(self) -> None:
        """SALES_FACT.SCORE (physical name, not alias) must be flagged."""
        ddl = self._make_ddl(
            tables='  salesfact AS db.SCHEMA."SALES_FACT" PRIMARY KEY ("ID")',
            dims='  SALES_FACT.SCORE AS "SCORE"',
        )
        result = validate_semantic_view_sql(
            ddl=ddl,
            dataset_aliases={"SalesFact": "salesfact"},
        )
        assert not result.valid
        assert any("SALES_FACT" in e for e in result.errors)

    def test_database_name_as_prefix_flagged(self) -> None:
        """ANALYTICS_DB.SCORE must be flagged when db name is known."""
        ddl = self._make_ddl(
            tables='  salesfact AS ANALYTICS_DB.SCHEMA."SALES_FACT" PRIMARY KEY ("ID")',
            metrics='  salesfact."TOTAL" AS SUM(ANALYTICS_DB.SCORE)',
        )
        result = validate_semantic_view_sql(
            ddl=ddl,
            dataset_aliases={"SalesFact": "salesfact"},
            database="ANALYTICS_DB",
            schema_name="SCHEMA",
        )
        assert not result.valid
        assert any("ANALYTICS_DB" in e for e in result.errors)


class TestBuildAliasRewriteMap:
    """Tests for the comprehensive alias rewrite map builder."""

    def test_basic_rewrite_entries(self) -> None:
        """Each logical name produces uppercase-alpha, physical, and logical keys."""
        aliases = {"SalesFact": "salesfact", "CustomerDim": "customerdim"}
        rmap = build_alias_rewrite_map(aliases)

        # Uppercase-alphanumeric
        assert rmap["SALESFACT"] == "salesfact"
        assert rmap["CUSTOMERDIM"] == "customerdim"

        # Physical table name
        assert rmap["SALES_FACT"] == "salesfact"
        assert rmap["CUSTOMER_DIM"] == "customerdim"

    def test_database_and_schema_entries(self) -> None:
        """Database and schema names map to empty string (reject signal)."""
        aliases = {"SalesFact": "salesfact"}
        rmap = build_alias_rewrite_map(
            aliases, database="ANALYTICS_DB", schema_name="SEMANTIC_LAYER"
        )
        assert rmap["ANALYTICS_DB"] == ""
        assert rmap["SEMANTIC_LAYER"] == ""

    def test_no_duplicate_when_same_alias(self) -> None:
        """Short names where alpha == alias should not create circular entries."""
        aliases = {"product": "product"}
        rmap = build_alias_rewrite_map(aliases)
        # 'product' alias == lowercase logical, so only PRODUCT (physical) should be present
        assert "PRODUCT" in rmap
        assert rmap["PRODUCT"] == "product"

    def test_extra_prefix_map_merged(self) -> None:
        """User-declared short aliases in extra_prefix_map are added with highest priority."""
        aliases = {"Fact_Sales": "factsales"}
        rmap = build_alias_rewrite_map(
            aliases,
            extra_prefix_map={"FACT": "factsales", "CALENDAR": "calendardim"},
        )
        assert rmap["FACT"] == "factsales"
        assert rmap["CALENDAR"] == "calendardim"

    def test_extra_prefix_map_overrides_derived(self) -> None:
        """extra_prefix_map takes precedence over auto-derived entries."""
        aliases = {"SalesFact": "salesfact"}
        # Override the auto-derived SALESFACT → salesfact with a custom alias
        rmap = build_alias_rewrite_map(
            aliases,
            extra_prefix_map={"SALESFACT": "sf_override"},
        )
        assert rmap["SALESFACT"] == "sf_override"


class TestExtractPrefixesFromExpressions:
    """Tests for extract_prefixes_from_expressions()."""

    def test_single_prefix(self) -> None:
        result = extract_prefixes_from_expressions(['SUM(FACT."REVENUE")'])
        assert "FACT" in result

    def test_multiple_prefixes_in_one_expr(self) -> None:
        expr = 'LAG(SUM(FACT."REVENUE"), 12) OVER (PARTITION BY CALENDAR."YEAR")'
        result = extract_prefixes_from_expressions([expr])
        assert "FACT" in result
        assert "CALENDAR" in result

    def test_multiple_expressions(self) -> None:
        exprs = ['SUM(FACT."REV")', 'MIN(PRODUCT."NAME")']
        result = extract_prefixes_from_expressions(exprs)
        assert "FACT" in result
        assert "PRODUCT" in result

    def test_no_uppercase_prefix(self) -> None:
        result = extract_prefixes_from_expressions(['SUM(salesfact."REV")'])
        # lowercase alias should still be found by the regex
        assert "SALESFACT" in result  # regex is case-insensitive → uppercased

    def test_empty_list(self) -> None:
        assert extract_prefixes_from_expressions([]) == set()

    def test_none_in_list(self) -> None:
        assert extract_prefixes_from_expressions([None]) == set()


class TestInferOverrideAliasMap:
    """Tests for infer_override_alias_map()."""

    def test_exact_uc_alpha_match(self) -> None:
        aliases = {"SalesFact": "salesfact"}
        result = infer_override_alias_map({"SALESFACT"}, aliases)
        assert result["SALESFACT"] == "salesfact"

    def test_exact_physical_match(self) -> None:
        aliases = {"SalesFact": "salesfact"}
        result = infer_override_alias_map({"SALES_FACT"}, aliases)
        assert result["SALES_FACT"] == "salesfact"

    def test_substring_match_prefix(self) -> None:
        """'FACT' is a substring of 'FACT_SALES' — should match."""
        aliases = {"Fact_Sales": "factsales"}
        result = infer_override_alias_map({"FACT"}, aliases)
        assert result.get("FACT") == "factsales"

    def test_unresolvable_prefix_absent(self) -> None:
        aliases = {"SalesFact": "salesfact"}
        result = infer_override_alias_map({"UNKNOWNXYZ"}, aliases)
        assert "UNKNOWNXYZ" not in result

    def test_empty_prefix_set(self) -> None:
        aliases = {"SalesFact": "salesfact"}
        assert infer_override_alias_map(set(), aliases) == {}


class TestSanitizeSqlExpression:
    """Tests for the shared sanitize_sql_expression() utility."""

    ALIASES = {"SalesFact": "salesfact", "CustomerDim": "customerdim"}

    def test_bracket_to_quoted(self) -> None:
        result = sanitize_sql_expression("SUM([Score])", self.ALIASES)
        assert '"SCORE"' in result
        assert "[Score]" not in result

    def test_uppercase_alias_bare_col(self) -> None:
        result = sanitize_sql_expression("SUM(SALESFACT.SCORE)", self.ALIASES)
        assert 'salesfact."SCORE"' in result
        assert "SALESFACT" not in result

    def test_uppercase_alias_quoted_col(self) -> None:
        result = sanitize_sql_expression('SUM(SALESFACT."Score")', self.ALIASES)
        assert 'salesfact."SCORE"' in result
        assert "SALESFACT" not in result

    def test_extra_prefix_map_resolves_short_alias(self) -> None:
        """FACT.REVENUE → factsales."REVENUE" via extra_prefix_map."""
        aliases = {"Fact_Sales": "factsales"}
        result = sanitize_sql_expression(
            'SUM(FACT."REVENUE")',
            aliases,
            extra_prefix_map={"FACT": "factsales"},
        )
        assert 'factsales."REVENUE"' in result
        assert "FACT." not in result

    def test_three_part_name_stripped(self) -> None:
        """DB.SCHEMA.col → \"COL\" (alias unknown, column quoted)."""
        result = sanitize_sql_expression(
            "SUM(ANALYTICS_DB.SCHEMA.SCORE)",
            self.ALIASES,
            database="ANALYTICS_DB",
            schema_name="SCHEMA",
        )
        assert "ANALYTICS_DB" not in result
        assert "SCHEMA." not in result
        assert '"SCORE"' in result

    def test_database_prefix_stripped(self) -> None:
        """ANALYTICS_DB.SCORE → \"SCORE\" (db name stripped, column quoted)."""
        result = sanitize_sql_expression(
            "SUM(ANALYTICS_DB.SCORE)",
            self.ALIASES,
            database="ANALYTICS_DB",
        )
        assert "ANALYTICS_DB" not in result

    def test_safety_net_lowercase_alias_bare_col(self) -> None:
        """salesfact.SCORE → salesfact.\"SCORE\" (safety-net step)."""
        result = sanitize_sql_expression("SUM(salesfact.SCORE)", self.ALIASES)
        assert 'salesfact."SCORE"' in result

    def test_complex_window_function(self) -> None:
        expr = (
            'LAG(SUM(FACT."REVENUE"), 12) OVER '
            '(PARTITION BY PRODUCT."PRODUCT_KEY" ORDER BY CALENDAR."YEARPERIOD")'
        )
        aliases = {
            "Fact_Sales": "factsales",
            "ProductDim": "productdim",
            "CalendarDim": "calendardim",
        }
        extra = {"FACT": "factsales", "PRODUCT": "productdim", "CALENDAR": "calendardim"}
        result = sanitize_sql_expression(expr, aliases, extra_prefix_map=extra)
        assert "FACT." not in result
        assert "PRODUCT." not in result
        assert "CALENDAR." not in result
        assert 'factsales."REVENUE"' in result
        assert 'productdim."PRODUCT_KEY"' in result
        assert 'calendardim."YEARPERIOD"' in result

    def test_empty_expr_passthrough(self) -> None:
        assert sanitize_sql_expression("", self.ALIASES) == ""

    def test_none_expr_passthrough(self) -> None:
        assert sanitize_sql_expression(None, self.ALIASES) is None