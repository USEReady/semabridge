"""Columns whose Snowflake name changes on the next sync.

The naming rule for dimensions changed ('Date' used to deploy as COL_DATE,
now DATE). An existing project silently switches on its next sync, which
breaks queries / reports using the old name. The dry run marks every column
row whose last deployed dimension/fact name differs from the name the sync
will deploy now::

    row["deployed_name_change"] = {"from": "COL_DATE", "to": "DATE"}

The last deployed names come from the project's last successfully deployed
DDL, ``output/reverse/<project>[/<physical_namespace>]/semantic_view.sql``
(written only when a sync succeeds, see core/engine/finalize.py).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from semabridge.utils.identifiers import IdentifierSanitizer

logger = logging.getLogger(__name__)

_IDENT = r'(?:"((?:[^"]|"")+)"|([A-Za-z_][A-Za-z0-9_$]*))'
# ALIAS.NAME AS ALIAS.PHYSICAL  (both sides quoted or bare)
_ENTRY_RE = re.compile(rf"{_IDENT}\s*\.\s*{_IDENT}\s+AS\s+{_IDENT}\s*\.\s*{_IDENT}", re.IGNORECASE)

# (table alias, deployed name, physical column)
DeployedColumn = Tuple[str, str, str]


def _safe_output_name(name: Any) -> str:
    """Same as ExecutionEngine._safe_output_name (output directory names)."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(name or "model"))
    safe = re.sub(r"_+", "_", safe).strip("._")
    return safe or "model"


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _balanced_block(text: str, keyword: str) -> str:
    m = re.search(rf"\b{keyword}\s*\(", text, re.I)
    if not m:
        return ""
    i, depth, in_sq, n = m.end(), 1, False, len(text)
    while i < n:
        ch = text[i]
        if in_sq:
            if ch == "'" and i + 1 < n and text[i + 1] == "'":
                i += 2
                continue
            if ch == "'":
                in_sq = False
        elif ch == "'":
            in_sq = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[m.end():i]
        i += 1
    return ""


def parse_deployed_columns(ddl: Optional[str]) -> List[DeployedColumn]:
    """Every DIMENSIONS / FACTS entry of a semantic-view DDL as
    (table alias, deployed name, physical column), all upper-case."""
    out: List[DeployedColumn] = []
    for clause in ("DIMENSIONS", "FACTS"):
        block = _balanced_block(ddl or "", clause)
        for m in _ENTRY_RE.finditer(block):
            g = [x.replace('""', '"') if x else x for x in m.groups()]
            alias = (g[0] or g[1] or "").strip()
            name = (g[2] or g[3] or "").strip()
            phys = (g[6] or g[7] or "").strip()
            if alias and name:
                out.append((alias.upper(), name.upper(), phys.upper()))
    return out


def load_last_deployed_ddl(project_id: Any, physical_namespace: Any = None, root: Optional[Path] = None) -> Optional[str]:
    """The DDL of the project's last successful deploy, or None."""
    if not str(project_id or "").strip():
        return None
    base = (root or Path("output")) / "reverse" / _safe_output_name(project_id)
    if physical_namespace:
        base = base / _safe_output_name(physical_namespace)
    path = base / "semantic_view.sql"
    try:
        return path.read_text(encoding="utf-8") if path.is_file() else None
    except OSError as exc:
        logger.debug("Could not read last deployed DDL %s: %s", path, exc)
        return None


def mark_deployed_name_changes(rows: Iterable[Dict[str, Any]], deployed_ddl: Optional[str]) -> bool:
    """Set ``deployed_name_change`` on every column row whose deployed name
    (in *deployed_ddl*) differs from its current ``target_name``.

    A row is matched to its deployed entry by table (row source_table vs the
    DDL table alias) and by physical column (``sanitize_column`` of the source
    name, the column SemaBridge reads) or, failing that, by the old automatic
    name (``to_alias``, e.g. COL_DATE). Rows that cannot be matched
    unambiguously are left alone. Returns True when deployed names were
    available (the DDL parsed to at least one column)."""
    deployed = parse_deployed_columns(deployed_ddl)
    if not deployed:
        return False
    sanitizer = IdentifierSanitizer(force_uppercase=True, suppress_reserved=True)
    by_table: Dict[str, List[DeployedColumn]] = {}
    for entry in deployed:
        by_table.setdefault(_norm(entry[0]), []).append(entry)

    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("entity_kind") or "").lower() not in ("column", "field"):
            continue
        source = str(row.get("source_name") or "").strip()
        target = str(row.get("target_name") or "").strip().upper()
        table_entries = by_table.get(_norm(row.get("source_table")))
        if not source or not target or not table_entries:
            continue
        names_in_table = {name for _a, name, _p in table_entries}
        if target in names_in_table:
            continue  # already deployed under this name
        physical = sanitizer.sanitize_column(source).upper()
        legacy = sanitizer.to_alias(source).upper()
        candidates = {name for _a, name, phys in table_entries if phys == physical}
        if len(candidates) != 1:
            candidates = {legacy} if legacy in names_in_table else set()
        if len(candidates) == 1:
            old = next(iter(candidates))
            if old != target:
                row["deployed_name_change"] = {"from": old, "to": target}
    return True


def annotate_dry_run_rows(
    rows: List[Dict[str, Any]],
    project_id: Any,
    physical_namespace: Any = None,
    root: Optional[Path] = None,
) -> bool:
    """Mark the dry-run rows of *project_id* against its last deployed DDL.
    Returns True when the deployed names were known (see the result's
    ``deployed_names_known``); never raises."""
    try:
        ddl = load_last_deployed_ddl(project_id, physical_namespace, root=root)
        return mark_deployed_name_changes(rows, ddl)
    except Exception as exc:  # noqa: BLE001 - advisory only, never fail a dry run
        logger.warning("Deployed-name check skipped for project %s: %s", project_id, exc)
        return False
