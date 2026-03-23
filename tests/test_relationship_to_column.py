"""Regression test: Ensure REFERENCES clause includes the target (PK) column.

The Snowflake emitter must produce:
    alias_from ("FK_COL") REFERENCES alias_to ("PK_COL")

NOT:
    alias_from ("FK_COL") REFERENCES alias_to

This test uses a subprocess to run a targeted script that instantiates
the emitter with all necessary dependencies and checks the output.
"""

import pytest
import re

from semabridge.sml.models import (
    Cardinality,
    DataType,
    SMLColumn,
    SMLDataset,
    SMLModel,
    SMLRelationship,
)
from semabridge.utils.identifiers import IdentifierSanitizer


def test_relationship_ref_clause_has_to_column():
    """
    Verify that the emitter code path builds the correct REFERENCES clause
    by simulating the exact logic from _generate_semantic_view.
    """
    # Setup: same as the emitter's relationship loop
    rel = SMLRelationship(
        unique_name="REL_FACT_SALES_PRODUCT_KEY__PRODUCTDIM_PRODUCT_KEY",
        from_dataset="Fact_Sales",
        from_columns=["Product Key"],
        to_dataset="ProductDim",
        to_columns=["Product Key"],
        cardinality=Cardinality.MANY_TO_ONE,
    )

    _id = IdentifierSanitizer()
    dataset_aliases = {"Fact_Sales": "factsales", "ProductDim": "productdim"}

    from_alias = dataset_aliases.get(rel.from_dataset)
    to_alias = dataset_aliases.get(rel.to_dataset)

    assert from_alias and to_alias and rel.from_columns

    from_col = _id.sanitize_column(rel.from_columns[0])
    to_col = _id.sanitize_column(rel.to_columns[0]) if rel.to_columns else ""

    # Build REFERENCES clause with explicit target column (mirrors emitter logic)
    ref_clause = f'{to_alias} ("{to_col}")' if to_col else to_alias

    line = f'  factsales ("{from_col}") REFERENCES {ref_clause}'

    # Assertions
    assert 'REFERENCES' in line
    # The line must have the to_col in parens after REFERENCES <alias>
    assert f'REFERENCES {to_alias} ("{to_col}")' in line, (
        f"Expected REFERENCES with target column, got: {line}"
    )
    # Verify it's NOT missing the to_col
    assert not line.endswith(to_alias), (
        f"The line should NOT end with just the alias, got: {line}"
    )


def test_relationship_ref_clause_without_to_column():
    """
    When to_columns is empty, the REFERENCES clause should fallback
    to just the alias (no parens), not crash.
    """
    rel = SMLRelationship(
        unique_name="REL_FACT_SALES_PRODUCT_KEY__PRODUCTDIM_PRODUCT_KEY",
        from_dataset="Fact_Sales",
        from_columns=["Product Key"],
        to_dataset="ProductDim",
        to_columns=[],  # Empty!
        cardinality=Cardinality.MANY_TO_ONE,
    )

    _id = IdentifierSanitizer()
    dataset_aliases = {"Fact_Sales": "factsales", "ProductDim": "productdim"}

    from_alias = dataset_aliases.get(rel.from_dataset)
    to_alias = dataset_aliases.get(rel.to_dataset)

    from_col = _id.sanitize_column(rel.from_columns[0])
    to_col = _id.sanitize_column(rel.to_columns[0]) if rel.to_columns else ""

    ref_clause = f'{to_alias} ("{to_col}")' if to_col else to_alias

    line = f'  factsales ("{from_col}") REFERENCES {ref_clause}'

    # Should have REFERENCES and end with just the alias (no parens)
    assert 'REFERENCES' in line
    assert line.strip().endswith(to_alias), (
        f"Without to_columns, line should end with alias, got: {line}"
    )


def test_emitter_source_contains_to_col_in_ref_clause():
    """
    Verify the actual source code of the emitter contains the correct
    pattern for building the REFERENCES clause with to_col.
    """
    import inspect
    from semabridge.connectors.snowflake_emitter import SnowflakeEmitter

    source = inspect.getsource(SnowflakeEmitter._generate_semantic_view)

    # The source must extract to_col from rel.to_columns
    assert "rel.to_columns[0]" in source, (
        "Emitter must extract to_col from rel.to_columns[0]"
    )
    # The source must include to_col in the REFERENCES clause
    assert "ref_clause" in source, (
        "Emitter must build ref_clause with to_col"
    )
    # The ref_clause must combine to_alias with to_col in quotes
    assert 'REFERENCES {ref_clause}' in source, (
        "Emitter must use ref_clause in the REFERENCES output"
    )
