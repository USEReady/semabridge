"""
Repository Map API Router.

Provides:
  GET  /api/repo/tree           — full directory + file tree
  GET  /api/repo/models         — parsed semantic models with dependency info
  GET  /api/repo/models/{id}/graph — graph nodes + edges for a model
  GET  /api/repo/file           — raw file content by path
  POST /api/repo/sync           — re-parse repository, return git info
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query

from semabridge.utils.logger import get_logger
from semabridge.repository.model_repository import ModelRepository
from semabridge.api.deps import get_model_repository

logger = get_logger(__name__)

router = APIRouter(prefix="/api/repo", tags=["repository-map"])

# -------------------------------------------------------
# Internal helpers
# -------------------------------------------------------

# Simple in-memory cache invalidated by /sync
_cache: Dict[str, Any] = {
    "tree": None,
    "models": None,
    "last_synced": None,
    "git_branch": None,
    "git_commit": None,
}

EXCLUDED_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv",
    ".pytest_cache", "dist", ".mypy_cache", ".ruff_cache",
    ".agent", ".agents", "_agent", "_agents", ".benchmarks",
}

ICON_MAP = {
    ".py": "python",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".sql": "sql",
    ".md": "markdown",
    ".toml": "config",
    ".cfg": "config",
    ".ini": "config",
    ".txt": "text",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".css": "css",
    ".html": "html",
}


def _get_repo_root() -> Path:
    """Resolve project root (where pyproject.toml lives)."""
    env_val = os.environ.get("SEMABRIDGE_REPO_ROOT")
    if env_val:
        return Path(env_val).expanduser().resolve()

    # Walk up from this file until we find pyproject.toml
    current = Path(__file__).resolve().parent
    for _ in range(10):
        if (current / "pyproject.toml").exists():
            return current
        current = current.parent

    return Path.cwd()


def _resolve_models_path() -> Path:
    """Resolve the models directory."""
    env_val = os.environ.get("SEMABRIDGE_LOCAL_MODELS_PATH")
    if env_val:
        return Path(env_val).expanduser().resolve()

    try:
        sema_yaml = _get_repo_root() / "config" / "semabridge.yaml"
        if not sema_yaml.exists():
            sema_yaml = _get_repo_root() / "semabridge.yaml"
        if sema_yaml.exists():
            cfg = yaml.safe_load(sema_yaml.read_text(encoding="utf-8"))
            raw = (cfg or {}).get("source", {}).get("repository_path")
            if raw:
                return Path(raw).expanduser().resolve()
    except Exception:
        pass

    return _get_repo_root()


def _build_tree_node(path: Path, rel_root: Path) -> Optional[Dict[str, Any]]:
    """Recursively build a tree node for a file or directory."""
    name = path.name
    if name in EXCLUDED_DIRS:
        return None

    rel_path = str(path.relative_to(rel_root)).replace("\\", "/")

    if path.is_file():
        try:
            stat = path.stat()
        except OSError:
            return None

        suffix = path.suffix.lower()
        return {
            "name": name,
            "path": rel_path,
            "type": "file",
            "icon": ICON_MAP.get(suffix, "file"),
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            ).isoformat(),
        }

    if path.is_dir():
        children: List[Dict[str, Any]] = []
        try:
            entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except PermissionError:
            entries = []

        for child in entries:
            node = _build_tree_node(child, rel_root)
            if node is not None:
                children.append(node)

        return {
            "name": name,
            "path": rel_path,
            "type": "directory",
            "icon": "folder",
            "children": children,
        }

    return None


def _parse_semantic_model_from_dict(
    model_id: str, data: Dict[str, Any], workspace_id: str
) -> Optional[Dict[str, Any]]:
    """Parse a semantic model straight from a DuckDB snapshot dictionary.

    Returns a normalized model dict or None if parsing fails.
    """
    if not isinstance(data, dict):
        return None

    # Check if this is actually a semantic model
    if not any(k in data for k in ("datasets", "metrics", "measures", "model_name")):
        return None

    model_name = (
        data.get("model_name")
        or data.get("label")
        or data.get("unique_name")
        or model_id
    )
    workspace_id = data.get("workspace_id", workspace_id)

    # Extract datasets / source tables
    datasets = data.get("datasets", [])
    if isinstance(datasets, dict):
        datasets = list(datasets.values())
    if not isinstance(datasets, list):
        datasets = []

    source_tables: List[Dict[str, Any]] = []
    seen_tables: set[tuple[str, str]] = set()
    used_table_names: set[str] = set()
    for idx, ds in enumerate(datasets):
        if not isinstance(ds, dict):
            continue

        table_name = (
            ds.get("source_table")
            or ds.get("table")
            or ds.get("name")
            or ds.get("unique_name")
            or ds.get("label")
            or ""
        )
        table_name = str(table_name).strip()
        if not table_name:
            table_name = f"unknown_{idx + 1}"
        if table_name in used_table_names:
            table_name = f"{table_name}_{idx + 1}"
        used_table_names.add(table_name)

        display_name = (
            ds.get("label")
            or ds.get("name")
            or ds.get("unique_name")
            or table_name
        )

        raw_columns = ds.get("columns", [])
        if not isinstance(raw_columns, list):
            raw_columns = []
        columns = []
        for c in raw_columns:
            if isinstance(c, dict):
                col_name = c.get("name") or c.get("unique_name") or c.get("column") or ""
                columns.append({
                    "name": col_name,
                    "data_type": c.get("data_type") or c.get("type") or c.get("source_type"),
                })

        schema_name = str(ds.get("source_schema") or ds.get("schema") or ds.get("database_schema") or "PUBLIC")
        dedupe_key = (schema_name, table_name)
        if dedupe_key in seen_tables:
            continue
        seen_tables.add(dedupe_key)

        source_tables.append({
            "name": display_name,
            "table": table_name,
            "schema": schema_name,
            "source_type": ds.get("source_type", data.get("source_type", "snowflake")),
            "columns": columns,
        })

    # Extract measures / metrics
    measures: List[Dict[str, Any]] = []
    for m in data.get("metrics", data.get("measures", [])):
        if not isinstance(m, dict):
            continue
        measures.append({
            "name": m.get("name") or m.get("unique_name") or m.get("label") or "unnamed",
            "expression": m.get("expression", ""),
            "data_type": m.get("data_type", m.get("format_string", "")),
        })

    # Extract relationships
    relationships: List[Dict[str, Any]] = []
    for r in data.get("relationships", []):
        if not isinstance(r, dict):
            continue
        cardinality = str(
            r.get("cardinality")
            or r.get("relationship_type")
            or r.get("type")
            or "many-to-one"
        )
        from_cols = r.get("from_columns") if isinstance(r.get("from_columns"), list) else []
        to_cols = r.get("to_columns") if isinstance(r.get("to_columns"), list) else []
        relationships.append({
            "from_model": r.get("from_table") or r.get("from_dataset") or r.get("from") or "",
            "to_model": r.get("to_table") or r.get("to_dataset") or r.get("to") or "",
            "from_column": r.get("from_column") or (from_cols[0] if from_cols else ""),
            "to_column": r.get("to_column") or (to_cols[0] if to_cols else ""),
            "join_key": r.get("from_column") or (from_cols[0] if from_cols else ""),
            "cardinality": cardinality,
        })

    # Check for broken references (tables that aren't in datasets)
    all_table_names = {t["table"] for t in source_tables}
    status = "valid"
    for rel in relationships:
        if rel["from_model"] and rel["from_model"] not in all_table_names:
            status = "broken"
            break
        if rel["to_model"] and rel["to_model"] not in all_table_names:
            status = "broken"
            break

    return {
        "model_id": model_id,
        "model_name": model_name,
        "workspace_id": workspace_id,
        "description": data.get("description", ""),
        "source_tables": source_tables,
        "measures": measures,
        "relationships": relationships,
        "status": status,
        "file_path": f"duckdb://{model_id}",
    }


def _get_all_models(repo: ModelRepository) -> List[Dict[str, Any]]:
    """Parse all semantic model files from DuckDB."""
    results: List[Dict[str, Any]] = []

    conn = repo._get_connection()
    try:
        rows = conn.execute("""
            WITH RankedVersions AS (
                SELECT model_id, workspace_id, snapshot, created_at,
                       ROW_NUMBER() OVER(PARTITION BY model_id ORDER BY created_at DESC) as rn
                FROM model_versions
            )
            SELECT model_id, workspace_id, snapshot
            FROM RankedVersions
            WHERE rn = 1
        """).fetchall()
        
        for row in rows:
            model_id = row[0]
            ws_id = row[1]
            snapshot_data = row[2]
            
            if isinstance(snapshot_data, str):
                try:
                    data = json.loads(snapshot_data)
                except Exception:
                    data = {}
            else:
                data = snapshot_data or {}
                
            model = _parse_semantic_model_from_dict(model_id, data, ws_id)
            if model:
                results.append(model)
    finally:
        conn.close()

    return results


def _build_graph(
    models: List[Dict[str, Any]], model_filter: Optional[str] = None
) -> Dict[str, Any]:
    """Build React-Flow-compatible nodes + edges from parsed models."""
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []

    def _card_symbol(card: str) -> str:
        c = str(card or "").strip().lower().replace("_", "-")
        mapping = {
            "one-to-one": "1:1",
            "one-to-many": "1:*",
            "many-to-one": "*:1",
            "many-to-many": "*:*",
        }
        return mapping.get(c, c or "?:?")

    filtered = models
    if model_filter:
        filtered = [m for m in models if m["model_id"] == model_filter]

    y_offset = 0
    for idx, model in enumerate(filtered):
        mid = model["model_id"]
        is_broken = model["status"] == "broken"

        # Model node (center row)
        model_node_id = f"model-{mid}"
        nodes.append({
            "id": model_node_id,
            "type": "modelNode",
            "position": {"x": 400, "y": y_offset + 150},
            "data": {
                "label": model["model_name"],
                "model_id": mid,
                "workspace_id": model["workspace_id"],
                "description": model["description"],
                "status": model["status"],
                "measures": model["measures"],
                "source_tables": model["source_tables"],
                "nodeType": "model",
            },
        })

        # Table nodes (top row)
        table_names = set()
        table_node_ids = set()
        for tidx, table in enumerate(model.get("source_tables", [])):
            table_node_id = f"table-{mid}-{table['table']}"
            table_names.add(table["table"])
            table_node_ids.add(table_node_id)
            nodes.append({
                "id": table_node_id,
                "type": "tableNode",
                "position": {"x": 150 + tidx * 300, "y": y_offset},
                "data": {
                    "label": table["table"],
                    "table_name": table["table"],
                    "schema": table["schema"],
                    "source_type": table["source_type"],
                    "columns": table["columns"],
                    "nodeType": "table",
                    "status": "valid",
                },
            })
            # Edge: table → model
            edges.append({
                "id": f"e-{table_node_id}-{model_node_id}",
                "source": table_node_id,
                "target": model_node_id,
                "animated": True,
                "style": {"stroke": "#22C55E"},
            })

        # Measure nodes (bottom row)
        for midx, measure in enumerate(model.get("measures", [])):
            measure_node_id = f"measure-{mid}-{midx}"
            nodes.append({
                "id": measure_node_id,
                "type": "measureNode",
                "position": {"x": 150 + midx * 300, "y": y_offset + 320},
                "data": {
                    "label": measure["name"],
                    "expression": measure["expression"],
                    "data_type": measure["data_type"],
                    "parent_model": mid,
                    "nodeType": "measure",
                },
            })
            # Edge: model → measure
            edges.append({
                "id": f"e-{model_node_id}-{measure_node_id}",
                "source": model_node_id,
                "target": measure_node_id,
                "animated": False,
                "style": {"stroke": "#EAB308"},
            })

        # Inter-table relationship edges
        for rel in model.get("relationships", []):
            from_table = rel['from_model']
            to_table = rel['to_model']

            # Create placeholder broken nodes for missing relationship endpoints
            for missing in [from_table, to_table]:
                if missing and missing not in table_names:
                    missing_node_id = f"table-{mid}-{missing}"
                    if missing_node_id not in table_node_ids:
                        table_node_ids.add(missing_node_id)
                        nodes.append({
                            "id": missing_node_id,
                            "type": "tableNode",
                            "position": {"x": 150 + len(table_node_ids) * 220, "y": y_offset + 20},
                            "data": {
                                "label": missing,
                                "table_name": missing,
                                "schema": "UNKNOWN",
                                "source_type": "unknown",
                                "columns": [],
                                "nodeType": "table",
                                "status": "broken",
                            },
                        })

            from_id = f"table-{mid}-{rel['from_model']}"
            to_id = f"table-{mid}-{rel['to_model']}"
            card = rel.get("cardinality") or "many-to-one"
            from_col = str(rel.get("from_column") or rel.get("join_key") or "")
            to_col = str(rel.get("to_column") or "")
            join_label = f"{from_col} → {to_col}" if from_col and to_col else from_col
            edges.append({
                "id": f"rel-{from_id}-{to_id}",
                "source": from_id,
                "target": to_id,
                "label": f"{_card_symbol(card)}{f' • {join_label}' if join_label else ''}",
                "type": "smoothstep",
                "style": {"stroke": "#818CF8", "strokeDasharray": "6 3"},
                "data": {
                    "cardinality": card,
                    "from_column": from_col,
                    "to_column": to_col,
                },
            })

        y_offset += 500

    return {"nodes": nodes, "edges": edges}


def _git_info(repo_root: Path) -> Dict[str, Optional[str]]:
    """Return current git branch and short commit hash, or None."""
    branch: Optional[str] = None
    commit: Optional[str] = None
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(repo_root),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo_root),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        pass
    return {"branch": branch, "commit": commit}


# -------------------------------------------------------
# Routes
# -------------------------------------------------------

@router.get("/tree")
async def get_repo_tree():
    """REQ-MAP-001: Return full directory + file tree of the repository."""
    # Client-facing tree should be scoped to semantic models path so
    # internal repository artifacts (frontend, docs, test files, etc.)
    # are not exposed in Explore UI.
    tree_root = _resolve_models_path()
    if not tree_root.exists():
        tree_root = _get_repo_root()

    if not tree_root.exists():
        raise HTTPException(status_code=404, detail="Repository root not found")

    tree = _build_tree_node(tree_root, tree_root)
    if tree is None:
        raise HTTPException(status_code=500, detail="Failed to build tree")

    return {
        "root": tree,
        "last_synced": _cache.get("last_synced"),
        "git_branch": _cache.get("git_branch"),
        "git_commit": _cache.get("git_commit"),
    }


@router.get("/models")
async def get_repo_models(
    workspace_id: str = Query("", description="Filter by workspace"),
    repo: ModelRepository = Depends(get_model_repository),
):
    """REQ-MAP-002 / REQ-MAP-007: Return all parsed semantic models."""
    models = _get_all_models(repo)

    if workspace_id:
        models = [m for m in models if m["workspace_id"] == workspace_id]

    return {
        "models": models,
        "total": len(models),
        "last_synced": _cache.get("last_synced"),
    }


@router.get("/models/{model_id}/graph")
async def get_model_graph(
    model_id: str,
    repo: ModelRepository = Depends(get_model_repository),
):
    """REQ-MAP-002: Graph nodes + edges for a specific model."""
    models = _get_all_models(repo)

    if model_id != "__all__":
        filtered = [m for m in models if m["model_id"] == model_id]
        if not filtered:
            raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")
    else:
        filtered = models

    graph = _build_graph(filtered, model_filter=None if model_id == "__all__" else model_id)
    return graph


@router.get("/file")
async def get_file_content(path: str = Query(..., description="Relative file path")):
    """REQ-MAP-001: Return raw file content for preview."""
    file_root = _resolve_models_path()
    if not file_root.exists():
        file_root = _get_repo_root()

    target = (file_root / path).resolve()

    # Security: ensure path stays within repo
    if not str(target).startswith(str(file_root)):
        raise HTTPException(status_code=403, detail="Access denied: path outside repository")

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = "(binary file — cannot display)"

    suffix = target.suffix.lower()
    return {
        "path": path,
        "name": target.name,
        "content": content,
        "language": ICON_MAP.get(suffix, "text"),
        "size": target.stat().st_size,
        "modified": datetime.fromtimestamp(
            target.stat().st_mtime, tz=timezone.utc
        ).isoformat(),
    }


@router.post("/sync")
async def sync_repository(
    repo: ModelRepository = Depends(get_model_repository),
):
    """REQ-MAP-008: Re-parse repository and update cached data."""
    repo_root = _get_repo_root()
    git = _git_info(repo_root)

    _cache["last_synced"] = datetime.now(timezone.utc).isoformat()
    _cache["git_branch"] = git["branch"]
    _cache["git_commit"] = git["commit"]
    _cache["tree"] = None  # Invalidate tree
    _cache["models"] = None  # Invalidate models

    models = _get_all_models(repo)

    return {
        "status": "synced",
        "models_count": len(models),
        "last_synced": _cache["last_synced"],
        "git_branch": git["branch"],
        "git_commit": git["commit"],
    }


# -------------------------------------------------------
# DuckDB Snapshot Explorer
# -------------------------------------------------------


@router.get("/snapshots/tree")
async def get_snapshot_tree(
    repo: ModelRepository = Depends(get_model_repository),
):
    """Return a virtual file-tree built from DuckDB model_versions snapshots.

    Groups models by workspace_id for a logical hierarchy.
    """
    conn = repo._get_connection()
    try:
        rows = conn.execute("""
            WITH Latest AS (
                SELECT model_id, workspace_id, snapshot, created_at,
                       ROW_NUMBER() OVER (PARTITION BY model_id ORDER BY created_at DESC) AS rn
                FROM model_versions
            )
            SELECT model_id, workspace_id, created_at
            FROM Latest
            WHERE rn = 1
            ORDER BY workspace_id, model_id
        """).fetchall()

        workspace_groups: Dict[str, list] = {}
        for model_id, ws_id, created_at in rows:
            ws_key = ws_id or "default"
            workspace_groups.setdefault(ws_key, []).append({
                "name": f"{model_id}.yaml",
                "path": f"snapshots/{ws_key}/{model_id}",
                "type": "file",
                "icon": "yaml",
                "model_id": model_id,
                "modified": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
            })

        children = []
        for ws_key, files in workspace_groups.items():
            children.append({
                "name": ws_key,
                "path": f"snapshots/{ws_key}",
                "type": "directory",
                "icon": "folder",
                "children": files,
            })

        return {
            "root": {
                "name": "DuckDB Snapshots",
                "path": "snapshots",
                "type": "directory",
                "icon": "folder",
                "children": children,
            }
        }
    except Exception as e:
        logger.error(f"Snapshot tree failed: {e}")
        return {"root": {"name": "DuckDB Snapshots", "path": "snapshots", "type": "directory", "icon": "folder", "children": []}}
    finally:
        conn.close()


@router.get("/snapshots/file")
async def get_snapshot_file(
    model_id: str = Query(..., description="Model ID to fetch snapshot for"),
    repo: ModelRepository = Depends(get_model_repository),
):
    """Return the latest DuckDB snapshot content for a model as YAML text."""
    conn = repo._get_connection()
    try:
        row = conn.execute("""
            SELECT snapshot, created_at
            FROM model_versions
            WHERE model_id = ?
            ORDER BY created_at DESC
            LIMIT 1
        """, [model_id]).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail=f"No snapshot found for model: {model_id}")

        snapshot_data = row[0]
        created_at = row[1]

        if isinstance(snapshot_data, str):
            try:
                data = json.loads(snapshot_data)
            except Exception:
                data = snapshot_data
        else:
            data = snapshot_data or {}

        # Convert to YAML for display
        content = yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)

        return {
            "path": f"duckdb://{model_id}",
            "name": f"{model_id}.yaml",
            "content": content,
            "language": "yaml",
            "modified": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Snapshot file fetch failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()
