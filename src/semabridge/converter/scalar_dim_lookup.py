"""Pattern matcher for DAX scalar dimension lookup.

Detects DAX patterns of the form:
  VAR x = CALCULATE(MAX|MIN(Dim[c]), Dim[k] = <scalar>)
  ...
  RETURN CALCULATE(<agg>(Fact[measure]), Fact[c] <op> x)

Generates helper name: _CURRENT_<c>
Extracts: Dim, c, k, aggregate (MAX/MIN), and comparison operator (<op>) directly from the DAX AST/syntax
with zero name-matching on calendar, date, or time columns.
"""

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ScalarDimLookupPattern:
    """Represents a DAX scalar dimension lookup pattern."""
    var_name: str
    agg: str  # MAX or MIN
    dim_table: Optional[str]
    dim_col: str  # c
    key_col: str  # k
    op: str  # <, <=, >, >=, =, <>
    measure_agg: str  # SUM, AVG, COUNT, etc.
    measure_table: Optional[str]
    measure_col: str

    @property
    def helper_name(self) -> str:
        """Helper name = _CURRENT_<c> (Requirement 2)."""
        clean_c = re.sub(r'[^A-Za-z0-9_]', '_', self.dim_col).upper()
        return f"_CURRENT_{clean_c}"


def _clean_dax_comments(dax: str) -> str:
    """Remove single-line comments (-- and //) from DAX."""
    no_dash = re.sub(r'--[^\r\n]*', '', dax)
    return re.sub(r'//[^\r\n]*', '', no_dash)


def match_scalar_dim_lookup_pattern(dax: Optional[str]) -> Optional[ScalarDimLookupPattern]:
    """Parse DAX to detect scalar dimension lookup pattern.
    
    Pattern:
      VAR x = CALCULATE(MAX|MIN(Dim[c]), Dim[k] = <scalar>)
      ...
      RETURN CALCULATE(<agg>(Fact[meas]), Dim_or_Fact[c] <op> x)
    """
    if not dax or not isinstance(dax, str):
        return None

    clean = _clean_dax_comments(dax)

    # 1. Match the VAR definition: VAR <x> = CALCULATE((MAX|MIN)(Dim[c]), Dim[k] = <scalar>)
    var_regex = re.compile(
        r'\bVAR\s+([A-Za-z0-9_]+)\s*=\s*CALCULATE\s*\(\s*(MAX|MIN)\s*\(\s*'
        r'(?:[\'\"]([^\'\"]+)[\'\"]\s*\[|([A-Za-z0-9_ ]+)\s*\[|\[)\s*([^\]]+)\]\s*\)\s*,\s*'
        r'(?:[\'\"]([^\'\"]+)[\'\"]\s*\[|([A-Za-z0-9_ ]+)\s*\[|\[)\s*([^\]]+)\]\s*=\s*'
        r'([A-Za-z0-9_\[\]]+(?:\(\))?)\s*\)',
        re.IGNORECASE,
    )

    vm = var_regex.search(clean)
    if not vm:
        return None

    var_name = vm.group(1).strip()
    agg = vm.group(2).strip().upper()
    dim_table = (vm.group(3) or vm.group(4) or "").strip() or None
    dim_col = vm.group(5).strip()
    key_table = (vm.group(6) or vm.group(7) or "").strip() or None
    key_col = vm.group(8).strip()
    scalar_ref = vm.group(9).strip()

    # 2. Match the RETURN block: RETURN CALCULATE(<agg>(Fact[meas]), [c] <op> <var_name>)
    return_match = re.search(r'\bRETURN\b\s+(.+)$', clean, re.IGNORECASE | re.DOTALL)
    return_block = return_match.group(1).strip() if return_match else clean

    # Look for CALCULATE(<agg>(Fact[col]), ... <c> <op> <var_name>)
    # Op can be <, <=, >, >=, =, <>
    ret_regex = re.compile(
        rf'CALCULATE\s*\(\s*([A-Za-z]+)\s*\(\s*'
        rf'(?:[\'\"]([^\'\"]+)[\'\"]\s*\[|([A-Za-z0-9_ ]+)\s*\[|\[)\s*([^\]]+)\]\s*\)\s*,\s*'
        rf'(?:[\'\"]([^\'\"]+)[\'\"]\s*\[|([A-Za-z0-9_ ]+)\s*\[|\[)\s*([^\]]+)\]\s*'
        rf'(<=|>=|<>|=|<|>)\s*{re.escape(var_name)}\s*\)',
        re.IGNORECASE,
    )

    rm = ret_regex.search(return_block)
    if not rm:
        return None

    measure_agg = rm.group(1).strip().upper()
    measure_table = (rm.group(2) or rm.group(3) or "").strip() or None
    measure_col = rm.group(4).strip()
    pred_table = (rm.group(5) or rm.group(6) or "").strip() or None
    pred_col = rm.group(7).strip()
    op = rm.group(8).strip()

    # Verify that the predicate column matches the column looked up in Dim[c]
    if pred_col.casefold() != dim_col.casefold():
        return None

    return ScalarDimLookupPattern(
        var_name=var_name,
        agg=agg,
        dim_table=dim_table,
        dim_col=dim_col,
        key_col=key_col,
        op=op,
        measure_agg=measure_agg,
        measure_table=measure_table,
        measure_col=measure_col,
    )

