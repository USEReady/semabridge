"""Join-key types: can the two sides of a join be compared on Databricks?

Types come from Unity Catalog for real tables, from the stub definition for
stubs (3.4 gives a stub's key the type of the column it joins to), and from
the Power BI model when there is no table plan.

  same family               -> join as is
  text and number           -> compare as text (CAST(number AS STRING)): never fails
  date-time and date        -> join as is, with a warning: only midnight matches,
                               exactly as in Power BI
  anything else             -> not joinable (date vs number, boolean vs text, ...)
"""

from __future__ import annotations

from typing import Optional, Tuple

STRING, NUMBER, DATE, TIMESTAMP, BOOLEAN, OTHER = "string", "number", "date", "timestamp", "boolean", "other"

OK, CAST, WARN, BLOCK = "ok", "cast", "warn", "block"

_NUMERIC = ("INT", "BIGINT", "SMALLINT", "TINYINT", "LONG", "SHORT", "BYTE", "DECIMAL", "NUMERIC",
            "DOUBLE", "FLOAT", "REAL", "NUMBER")


def family(type_name: Optional[str]) -> Optional[str]:
    """The comparison family of a Databricks or Power BI (SML) type, None if unknown."""
    if not type_name:
        return None
    t = str(type_name).strip().upper()
    if t in ("", "UNKNOWN", "VARIANT"):
        return None
    if t.startswith(("STRING", "VARCHAR", "CHAR", "TEXT")):
        return STRING
    if t in ("INTEGER",) or t.startswith(_NUMERIC):
        return NUMBER
    if t.startswith("TIMESTAMP") or t == "DATETIME":
        return TIMESTAMP
    if t == "DATE":
        return DATE
    if t in ("BOOLEAN", "BOOL"):
        return BOOLEAN
    return OTHER


def decide(left: Optional[str], right: Optional[str]) -> Tuple[str, Optional[str]]:
    """(OK | CAST | WARN | BLOCK, the side to cast to STRING: 'left' / 'right' / None)."""
    a, b = family(left), family(right)
    if a is None or b is None or a == b:
        return OK, None
    if {a, b} == {STRING, NUMBER}:
        return CAST, "left" if a == NUMBER else "right"
    if {a, b} == {DATE, TIMESTAMP}:
        return WARN, None
    return BLOCK, None
