"""Reverse Sync: Data Layer (Snowflake Semantic View / Ontology) -> BI (Power BI) Upgrade-Only."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("semabridge.sync.reverse_sync")


def _sql_to_dax_hint(sql_expr: str, table_hint: str = "Fact") -> str:
    """Translate common Snowflake SQL metric expressions into Power BI DAX."""
    expr = (sql_expr or "").strip()
    if not expr:
        return "0"
    # Already looks like DAX Table[Col]
    if "[" in expr and "]" in expr:
        return expr

    m_distinct = re.match(r"^\s*COUNT\s*\(\s*DISTINCT\s+([A-Za-z0-9_.\"]+)\s*\)\s*$", expr, re.IGNORECASE)
    if m_distinct:
        col = m_distinct.group(1).replace('"', "").split(".")[-1]
        return f"DISTINCTCOUNT('{table_hint}'[{col}])"

    for sql_fn, dax_fn in (
        ("SUM", "SUM"),
        ("AVG", "AVERAGE"),
        ("COUNT", "COUNT"),
        ("MIN", "MIN"),
        ("MAX", "MAX"),
    ):
        m = re.match(rf"^\s*{sql_fn}\s*\(\s*([A-Za-z0-9_.\"]+)\s*\)\s*$", expr, re.IGNORECASE)
        if m:
            parts = m.group(1).replace('"', "").split(".")
            tbl = parts[-2] if len(parts) >= 2 else table_hint
            col = parts[-1]
            return f"{dax_fn}('{tbl}'[{col}])"

    return expr


def _read_tmdl_model(pbip_dir: Path) -> Dict[str, Any]:
    """Scan a PBIP/TMDL folder for existing measures, descriptions, and relationships."""
    measures: Dict[str, Dict[str, str]] = {}
    relationships: set[str] = set()
    if not pbip_dir.exists():
        return {"measures": measures, "relationships": relationships}

    for tmdl in pbip_dir.rglob("*.tmdl"):
        try:
            text = tmdl.read_text(encoding="utf-8")
        except Exception:
            continue
        for match in re.finditer(r"^\s*measure\s+['\"]?([^'\"=\r\n]+)['\"]?\s*=\s*(.+)$", text, re.MULTILINE):
            m_name = match.group(1).strip()
            measures[m_name.lower()] = {
                "name": m_name,
                "expression": match.group(2).strip(),
                "file": str(tmdl),
            }
        for rmatch in re.finditer(r"fromColumn:\s*([^\r\n]+).*?toColumn:\s*([^\r\n]+)", text, re.DOTALL):
            relationships.add(f"{rmatch.group(1).strip().lower()}->{rmatch.group(2).strip().lower()}")

    return {"measures": measures, "relationships": relationships}


def _resolve_sf_config(
    project_id: Optional[str],
    account_id: Optional[str],
    owner_id: Optional[str],
):
    from semabridge.ai.project_connection import _build_from_account, resolve_project_warehouse_config
    from semabridge.core.settings import reload_settings

    if account_id:
        base = reload_settings().snowflake
        built = _build_from_account(account_id, "SNOWFLAKE", base, {})
        if built is not None:
            return built
    return resolve_project_warehouse_config(project_id, "SNOWFLAKE", owner_id=owner_id)


def list_snowflake_semantic_views(
    *,
    account_id: Optional[str] = None,
    project_id: Optional[str] = None,
    owner_id: Optional[str] = None,
) -> Dict[str, Any]:
    """List deployed Snowflake Semantic Views on the selected connected account."""
    from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs

    views: List[Dict[str, str]] = []
    try:
        sf = _resolve_sf_config(project_id, account_id, owner_id)
        if sf is None:
            return {"snowflake_connected": False, "error": "No Snowflake connection configured.", "views": []}
        kw = get_snowflake_connect_kwargs(sf)
        import snowflake.connector

        conn = snowflake.connector.connect(**kw)
        try:
            cur = conn.cursor()
            db = getattr(sf, "database", None)
            sch = getattr(sf, "schema_name", None)
            clause = f' IN SCHEMA "{db}"."{sch}"' if (db and sch) else (f' IN DATABASE "{db}"' if db else "")
            cur.execute(f"SHOW SEMANTIC VIEWS{clause}")
            cols = [c[0].lower() for c in cur.description] if cur.description else []
            for row in cur.fetchall():
                rec = dict(zip(cols, row))
                v_db = str(rec.get("database_name") or db or "").strip()
                v_sch = str(rec.get("schema_name") or sch or "").strip()
                v_name = str(rec.get("name") or "").strip()
                if not v_name:
                    continue
                fqn = ".".join(p for p in (v_db, v_sch, v_name) if p)
                views.append({
                    "fqn": fqn,
                    "name": v_name,
                    "database_name": v_db,
                    "schema_name": v_sch,
                })
            return {"snowflake_connected": True, "error": None, "views": views}
        finally:
            conn.close()
    except Exception as exc:
        return {"snowflake_connected": False, "error": str(exc), "views": views}


def _fetch_snowflake_semantics(
    project_id: Optional[str],
    semantic_view: Optional[str],
    owner_id: Optional[str],
    account_id: Optional[str] = None,
) -> tuple[List[Dict[str, Any]], List[Dict[str, str]], bool, Optional[str]]:
    """Read metrics, dimensions, descriptions, synonyms, and relationships from Snowflake Semantic Views."""
    from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs

    metrics: List[Dict[str, Any]] = []
    relationships: List[Dict[str, str]] = []
    try:
        sf = _resolve_sf_config(project_id, account_id, owner_id)
        if sf is None:
            return metrics, relationships, False, "No Snowflake connection configured."
        kw = get_snowflake_connect_kwargs(sf)
        import snowflake.connector

        conn = snowflake.connector.connect(**kw)
        try:
            cur = conn.cursor()
            views = [semantic_view.strip()] if (semantic_view and semantic_view.strip()) else []
            if not views:
                db = getattr(sf, "database", None)
                sch = getattr(sf, "schema_name", None)
                clause = f' IN SCHEMA "{db}"."{sch}"' if (db and sch) else (f' IN DATABASE "{db}"' if db else "")
                cur.execute(f"SHOW SEMANTIC VIEWS{clause}")
                cols = [c[0].lower() for c in cur.description] if cur.description else []
                for row in cur.fetchall()[:5]:
                    rec = dict(zip(cols, row))
                    v_db = rec.get("database_name") or db or ""
                    v_sch = rec.get("schema_name") or sch or ""
                    v_name = rec.get("name") or ""
                    if v_name:
                        views.append(".".join(p for p in (v_db, v_sch, v_name) if p))

            for v in views:
                try:
                    cur.execute(f"DESCRIBE SEMANTIC VIEW {v}")
                    dcols = [c[0].lower() for c in cur.description] if cur.description else []
                    grouped: Dict[str, Dict[str, Any]] = {}
                    for dr in cur.fetchall():
                        rec = dict(zip(dcols, dr))
                        kind = str(rec.get("object_kind") or rec.get("kind") or "").upper()
                        obj_name = str(rec.get("object_name") or rec.get("name") or "").strip()
                        prop = str(rec.get("property") or "").upper()
                        val = str(rec.get("property_value") or "").strip()
                        if kind in ("METRIC", "METRICS", "") and obj_name:
                            entry = grouped.setdefault(
                                obj_name,
                                {"name": obj_name, "view": v, "expression": "", "description": "", "synonyms": []},
                            )
                            if prop == "EXPRESSION":
                                entry["expression"] = val
                            elif prop == "COMMENT":
                                entry["description"] = val
                            elif prop == "SYNONYMS" and val:
                                entry["synonyms"] = [s.strip().strip("'\"") for s in val.strip("[]").split(",") if s.strip()]
                        elif kind in ("RELATIONSHIP", "RELATIONSHIPS") and obj_name:
                            relationships.append({"name": obj_name, "view": v, "property": prop, "value": val})
                    metrics.extend(grouped.values())
                except Exception:
                    pass
            return metrics, relationships, True, None
        finally:
            conn.close()
    except Exception as exc:
        return metrics, relationships, False, str(exc)


def _build_tmsl_upgrade_script(upgrades: List[Dict[str, Any]], database_name: str = "SemanticModel") -> str:
    """Build an additive Power BI TMSL createOrReplace script for new/updated measures."""
    measures = []
    for u in upgrades:
        if u.get("action") in ("ADD_MEASURE", "UPDATE_DESCRIPTION"):
            measures.append({
                "name": u["metric_name"],
                "expression": u.get("dax_expression") or u.get("expression") or "0",
                "description": u.get("description") or "",
            })
    payload = {
        "createOrReplace": {
            "object": {"database": database_name},
            "measures": measures,
        }
    }
    return json.dumps(payload, indent=2)


def preview_reverse_upgrades(
    *,
    project_id: Optional[str] = None,
    account_id: Optional[str] = None,
    semantic_view: Optional[str] = None,
    pbip_path: Optional[str] = None,
    owner_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute upgrade-only additions/metadata updates from Snowflake Data Layer -> Power BI."""
    from semabridge.ontology.store import ontology_store

    sf_items, sf_rels, sf_connected, sf_error = _fetch_snowflake_semantics(
        project_id, semantic_view, owner_id, account_id=account_id
    )
    if not sf_items:
        fed = ontology_store.get_federated_ontology()
        for dom in fed.domains.values():
            if project_id and getattr(dom, "source_project_id", None) != project_id:
                continue
            for m in dom.metrics:
                sf_items.append({
                    "name": m.name,
                    "view": getattr(dom, "semantic_view_name", None) or dom.domain,
                    "expression": m.sql_expression or m.dax_expression or "",
                    "dax_expression": m.dax_expression or _sql_to_dax_hint(m.sql_expression or ""),
                    "description": m.description or "",
                    "synonyms": list(getattr(m, "synonyms", None) or []),
                })

    bi_state = _read_tmdl_model(Path(pbip_path)) if pbip_path else {"measures": {}, "relationships": set()}
    existing_bi = bi_state["measures"]

    upgrades: List[Dict[str, Any]] = []
    for item in sf_items:
        mname = item["name"]
        key = mname.lower()
        dax_expr = item.get("dax_expression") or _sql_to_dax_hint(item.get("expression", ""))
        if key not in existing_bi:
            upgrades.append({
                "action": "ADD_MEASURE",
                "metric_name": mname,
                "source_view": item.get("view", ""),
                "expression": item.get("expression", ""),
                "dax_expression": dax_expr,
                "description": item.get("description", ""),
                "synonyms": item.get("synonyms", []),
                "safe_additive": True,
            })
        else:
            if item.get("description"):
                upgrades.append({
                    "action": "UPDATE_DESCRIPTION",
                    "metric_name": mname,
                    "source_view": item.get("view", ""),
                    "expression": item.get("expression", ""),
                    "dax_expression": dax_expr,
                    "description": item.get("description", ""),
                    "safe_additive": True,
                })
            if item.get("synonyms"):
                upgrades.append({
                    "action": "ADD_SYNONYM",
                    "metric_name": mname,
                    "source_view": item.get("view", ""),
                    "synonyms": item.get("synonyms", []),
                    "safe_additive": True,
                })

    return {
        "snowflake_connected": sf_connected,
        "snowflake_error": sf_error,
        "pbip_path": pbip_path,
        "existing_bi_measures": len(existing_bi),
        "snowflake_relationships": len(sf_rels),
        "upgrade_count": len(upgrades),
        "upgrades": upgrades,
        "tmsl_script": _build_tmsl_upgrade_script(upgrades),
    }


def apply_reverse_upgrades(
    *,
    pbip_path: str,
    upgrades: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Append approved upgrade-only measures/synonyms into a PBIP/TMDL model (never deletes existing items)."""
    pbip_dir = Path(pbip_path)
    if not pbip_dir.exists():
        raise FileNotFoundError(f"PBIP/TMDL directory not found: {pbip_path}")

    tmdl_files = sorted(pbip_dir.rglob("*.tmdl"))
    if not tmdl_files:
        raise ValueError(f"No .tmdl files found under {pbip_path}")

    target_file = next((f for f in tmdl_files if "model" not in f.name.lower()), tmdl_files[0])
    existing = _read_tmdl_model(pbip_dir)["measures"]
    content = target_file.read_text(encoding="utf-8")

    applied: List[str] = []
    skipped: List[str] = []
    blocks: List[str] = []
    for up in upgrades:
        name = str(up.get("metric_name") or "").strip()
        if not name:
            continue
        if up.get("action") == "ADD_MEASURE" and name.lower() not in existing:
            expr = str(up.get("dax_expression") or up.get("expression") or "0").strip()
            desc = str(up.get("description") or "").replace("\n", " ").strip()
            block = f"\n\t/// {desc}\n" if desc else "\n"
            block += f"\tmeasure '{name}' = {expr}\n"
            blocks.append(block)
            applied.append(name)
        else:
            skipped.append(name)

    if blocks:
        target_file.write_text(content.rstrip() + "\n" + "".join(blocks), encoding="utf-8")

    return {
        "success": True,
        "target_file": str(target_file),
        "applied_count": len(applied),
        "applied": applied,
        "skipped": skipped,
    }
