"""Power BI measure and column names as the model itself spells them.

SemaBridge stores identifier-safe names (``Total_VanArsdel_Units_YTD``,
``PRODUCT.MANUFACTURER``); a DAX query must use the model's own names
(``Total VanArsdel Units YTD``, ``'Product'[Manufacturer]``). The model is asked
once per check with ``INFO.VIEW.MEASURES()`` / ``INFO.VIEW.COLUMNS()``, which
need only read access. Any failure keeps the stored names unchanged.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MEASURES_QUERY = 'EVALUATE SELECTCOLUMNS(INFO.VIEW.MEASURES(), "Name", [Name])'
COLUMNS_QUERY = 'EVALUATE SELECTCOLUMNS(INFO.VIEW.COLUMNS(), "Table", [Table], "Name", [Name])'


def _norm(name: Any) -> str:
    return re.sub(r"[^0-9a-z]+", "_", str(name or "").strip().strip("'[]\"`").lower()).strip("_")


def _field(row: Dict[str, Any], key: str) -> str:
    for k, v in row.items():
        if str(k).strip("[]").split("[")[-1].strip("]").lower() == key.lower():
            return str(v or "")
    return ""


def resolve(
    run: Callable[[str], List[Dict[str, Any]]],
    metric_name: str,
    dimensions: List[str],
) -> Tuple[str, List[str], bool]:
    """(measure, dimensions, resolved) using the model's own names.

    ``run`` executes a DAX query on the project's model and returns its rows.
    """
    measure, dims, resolved = metric_name, list(dimensions or []), False
    try:
        names = [_field(r, "Name") for r in run(MEASURES_QUERY)]
        by_norm = {_norm(n): n for n in names if n}
        hit = by_norm.get(_norm(metric_name)) or by_norm.get(_norm(str(metric_name).split(".")[-1]))
        if hit:
            measure, resolved = hit, True
    except Exception as exc:  # noqa: BLE001 -- best-effort
        logger.info("Could not list the Power BI model's measures: %s", exc)

    if dims:
        try:
            cols = [(_field(r, "Table"), _field(r, "Name")) for r in run(COLUMNS_QUERY)]
            out = []
            for d in dims:
                parts = str(d).split(".", 1)
                table, col = (parts[0], parts[1]) if len(parts) == 2 else ("", parts[0])
                match = next(
                    (f"{t}.{c}" for t, c in cols
                     if _norm(c) == _norm(col) and (not table or _norm(t) == _norm(table))),
                    None,
                )
                out.append(match or d)
                resolved = resolved or bool(match)
            dims = out
        except Exception as exc:  # noqa: BLE001
            logger.info("Could not list the Power BI model's columns: %s", exc)
    return measure, dims, resolved


def resolve_for_project(fabric_target: Any, metric_name: str, dimensions: List[str]) -> Tuple[str, List[str]]:
    """Resolve against a Fabric project's model; unchanged names when it is unreachable."""
    if fabric_target is None:
        return metric_name, list(dimensions or [])
    from semabridge.sources.base import get_source_adapter

    adapter = get_source_adapter("fabric")
    measure, dims, _ = resolve(lambda q: adapter.execute_dax(fabric_target, q)["rows"], metric_name, dimensions)
    return measure, dims
