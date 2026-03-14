"""
IdentifierNormalizer — normalize and validate semantic identifiers.

Provides:
- Identifier normalization (case handling, special char replacement)
- Column existence validation
- Alias lookup building and resolution
- DAX expression qualifier stripping
- Table reference validation

Works in conjunction with IdentifierSanitizer for unified identifier hygiene.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

from semabridge.utils.identifiers import IdentifierSanitizer


class IdentifierNormalizer:
    """
    Normalizes and validates semantic identifiers using a configured sanitizer.
    """

    def __init__(self, sanitizer: IdentifierSanitizer):
        """
        Initialize the normalizer with a sanitizer.

        Args:
            sanitizer: Configured IdentifierSanitizer instance
        """
        self.sanitizer = sanitizer

    def normalize_identifier(
        self,
        identifier: str,
        quoted: bool = False,
    ) -> str:
        """
        Normalize an identifier (case, special chars, DAX qualifiers).

        Steps:
        1. Strip DAX qualifiers ('Table'[Column] → Column)
        2. Strip brackets and quotes
        3. Normalize case and special chars
        4. Handle quoted identifiers

        Args:
            identifier: Raw identifier string
            quoted: If True, preserve original casing (quoted identifier)

        Returns:
            Normalized identifier
        """
        if not identifier:
            return "UNKNOWN"

        # Step 1: Strip DAX qualifiers
        identifier = self._strip_dax_qualifiers(identifier)

        # Step 2: Strip brackets and quotes if not quoted
        if not quoted:
            identifier = identifier.strip("[]'\"")

        # Step 3: Normalize
        if quoted:
            # Preserve original casing for quoted identifiers
            return identifier
        else:
            # Use sanitizer to normalize
            return self.sanitizer.sanitize_column(identifier)

    def validate_column_exists(
        self,
        available_columns: Set[str],
        column_name: str,
    ) -> bool:
        """
        Validate if a column exists in the available set.

        Handles:
        - Case-insensitive matching (standardizes to uppercase)
        - Dot notation stripping (TABLE.COLUMN → COLUMN)

        Args:
            available_columns: Set of available column names (typically uppercase)
            column_name: Column to validate

        Returns:
            True if column exists, False otherwise
        """
        if not column_name:
            return False

        # Normalize the column name
        normalized = self.sanitizer.sanitize_column(column_name)

        # Check if it exists (case-insensitive)
        return normalized in available_columns or normalized.casefold() in {c.casefold() for c in available_columns}

    def build_alias_lookup(
        self,
        datasets: List[Any],
        alias_map: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """Build a comprehensive reverse-lookup map for alias resolution.

        Maps every plausible raw-name variant of a dataset to the
        sanitized alias declared in the TABLES clause, including the
        alias itself.  This ensures reserved-word-prefixed aliases
        (e.g. ``L_TABLE``) are discoverable from expression rewriting.

        Args:
            datasets: List of dataset objects
            alias_map: ``{dataset.unique_name: alias}`` mapping

        Returns:
            ``{UPPERCASE_VARIANT: alias}`` dict.
        """
        lookup: Dict[str, str] = {}

        if alias_map is None:
            alias_map = {}

        for ds_name, alias in alias_map.items():
            # Primary: exact unique_name (UPPERCASE)
            lookup[ds_name.upper()] = alias
            # The alias itself (handles L_TABLE → L_TABLE)
            lookup[alias.upper()] = alias
            # source_table variant
            for ds in datasets:
                if getattr(ds, 'unique_name', '') == ds_name:
                    if hasattr(ds, "source_table") and ds.source_table:
                        lookup[ds.source_table.upper()] = alias
                        safe_table = self.sanitizer.sanitize_table_name(ds.source_table)
                        lookup[safe_table] = alias
                    break

        return lookup

    def resolve_alias(
        self,
        alias: str,
        alias_lookup: Dict[str, str],
    ) -> Optional[str]:
        """
        Resolve an alias to its canonical name.

        Args:
            alias: Alias to resolve
            alias_lookup: Alias lookup dictionary

        Returns:
            Canonical name or None if not found
        """
        if not alias:
            return None

        # Try exact match first (already normalized)
        if alias in alias_lookup:
            return alias_lookup[alias]

        # Try normalized version
        normalized = self.sanitizer.sanitize_alias(alias)
        return alias_lookup.get(normalized)

    def detect_casing_mismatch(
        self,
        declared_name: str,
        actual_name: str,
    ) -> bool:
        """
        Detect if there's a casing mismatch between declared and actual names.

        Args:
            declared_name: Name declared in semantic model
            actual_name: Name from physical source

        Returns:
            True if names differ only in casing
        """
        if not declared_name or not actual_name:
            return False

        # Remove special chars and compare
        decl_norm = re.sub(r"[^A-Za-z0-9]", "", declared_name).upper()
        actual_norm = re.sub(r"[^A-Za-z0-9]", "", actual_name).upper()

        return decl_norm == actual_norm and declared_name != actual_name

    def _strip_dax_qualifiers(self, identifier: str) -> str:
        """
        Strip DAX-style qualifiers from identifier.

        Examples:
            'Table'[Column] → Column
            [Column] → Column
            'Column' → Column

        Args:
            identifier: Identifier possibly with DAX qualifiers

        Returns:
            Identifier without DAX qualifiers
        """
        if not identifier:
            return ""

        # Pattern: 'Table'[Column] or similar
        match = re.search(r"\[([^\]]+)\]", identifier)
        if match:
            return match.group(1)

        # Pattern: quoted table name alone
        identifier = identifier.strip("'\"[]")

        return identifier
