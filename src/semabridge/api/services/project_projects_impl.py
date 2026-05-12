import json
import os
import re
import time as _time
from pathlib import Path
from typing import Any, Dict, List

import yaml
from fastapi import HTTPException, Query
from starlette.responses import Response

from semabridge.api.services.project_shared import (
    _compat_clean_project_name,
    _compat_default_project_yaml,
    _compat_ensure_loaded,
    _compat_load_project_yaml_text,
    _compat_load_repo_yaml_text,
    _compat_mappings,
    _compat_now_iso,
    _compat_project_configs,
    _compat_project_schedules,
    _compat_project_snapshots,
    _compat_project_payload,
    _compat_project_runs,
    _compat_project_yaml_path,
    _compat_projects,
    _compat_projects_dir,
    _compat_run_snapshots,
    _compat_save_project_yaml_text,
    _compat_save_store,
    _compat_snapshot_groups,
    _compat_store_loaded,
    _compat_folders,
    db_manager,
    logger,
)

def _clear_project_mapping_cache(project_id: str) -> None:
    """Clear compat mapping cache entries for a project after config creation/save."""
    pid = str(project_id or "").strip()
    if not pid:
        return
    removed = 0
    for mapping_id, mapping in list(_compat_mappings.items()):
        if not isinstance(mapping, dict):
            continue
        if str(mapping.get("project_id") or "").strip() != pid:
            continue
        _compat_mappings.pop(mapping_id, None)
        removed += 1
    if removed:
        logger.info("Cleared %s cached mapping row(s) for project %s after config write", removed, pid)


def _project_display_name_from_cfg(project_cfg: Dict[str, Any], fallback: str) -> str:
    if not isinstance(project_cfg, dict):
        return fallback
    meta = project_cfg.get("project_metadata") if isinstance(project_cfg.get("project_metadata"), dict) else {}
    return _compat_clean_project_name(
        project_cfg.get("display_name") or meta.get("display_name") or meta.get("name") or project_cfg.get("project_name"),
        fallback,
    )


def _normalize_model_manifest(project_id: str, parsed: Dict[str, Any]) -> Dict[str, Any]:
    raw_manifest = parsed.get("model_manifest")
    manifest = raw_manifest if isinstance(raw_manifest, dict) else {}

    manifest_version = str(manifest.get("manifest_version") or "1").strip()
    if manifest_version != "1":
        raise HTTPException(status_code=400, detail="model_manifest.manifest_version must be '1'")

    topology_layer = str(manifest.get("topology_layer") or "spoke").strip().lower()
    if topology_layer not in {"hub", "spoke"}:
        raise HTTPException(status_code=400, detail="model_manifest.topology_layer must be 'hub' or 'spoke'")

    dependencies = manifest.get("dependencies")
    if dependencies is None:
        normalized_dependencies: List[str] = []
    elif isinstance(dependencies, list):
        normalized_dependencies = []
        for dep in dependencies:
            if not isinstance(dep, str):
                raise HTTPException(status_code=400, detail="model_manifest.dependencies entries must be strings")
            dep_id = dep.strip()
            if dep_id:
                normalized_dependencies.append(dep_id)
    else:
        raise HTTPException(status_code=400, detail="model_manifest.dependencies must be a list of strings")

    dependency_pins_raw = manifest.get("dependency_pins")
    if dependency_pins_raw is None:
        dependency_pins: Dict[str, str] = {}
    elif isinstance(dependency_pins_raw, dict):
        dependency_pins = {}
        for dep_id, dep_ver in dependency_pins_raw.items():
            key = str(dep_id).strip()
            val = str(dep_ver).strip()
            if not key:
                continue
            if not val:
                raise HTTPException(status_code=400, detail="model_manifest.dependency_pins values must be non-empty strings")
            dependency_pins[key] = val
    else:
        raise HTTPException(status_code=400, detail="model_manifest.dependency_pins must be an object map")

    published_versions_raw = manifest.get("published_contract_versions")
    if published_versions_raw is None:
        normalized_published_versions: List[str] = []
    elif isinstance(published_versions_raw, list):
        normalized_published_versions = []
        for item in published_versions_raw:
            ver = str(item).strip()
            if not ver:
                continue
            normalized_published_versions.append(ver)
    else:
        raise HTTPException(status_code=400, detail="model_manifest.published_contract_versions must be a list of strings")

    contract_version = str(manifest.get("contract_version") or "1.0.0").strip() or "1.0.0"
    if contract_version not in normalized_published_versions:
        normalized_published_versions.append(contract_version)
    normalized_published_versions = sorted(set(normalized_published_versions))

    return {
        "manifest_version": manifest_version,
        "model_id": str(manifest.get("model_id") or project_id).strip() or project_id,
        "topology_layer": topology_layer,
        "owner": str(manifest.get("owner") or "unknown").strip() or "unknown",
        "contract_id": str(manifest.get("contract_id") or f"contract.{project_id}").strip() or f"contract.{project_id}",
        "contract_version": contract_version,
        "published_contract_versions": normalized_published_versions,
        "dependency_pins": dependency_pins,
        "deprecation_date": str(manifest.get("deprecation_date") or "").strip(),
        "dependencies": normalized_dependencies,
    }


def _normalize_project_config_yaml(project_id: str, yaml_text: str, default_name: str) -> str:
    try:
        parsed = yaml.safe_load(yaml_text) or {}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid YAML content: {exc}")

    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="Project YAML must be an object mapping")

    parsed_project_id = str(parsed.get("project_id") or "").strip()
    if parsed_project_id and parsed_project_id != project_id:
        raise HTTPException(status_code=400, detail="project_id in YAML must match route project_id")

    display_name = _project_display_name_from_cfg(parsed, default_name)
    if not display_name:
        display_name = default_name

    parsed["project_id"] = project_id
    parsed["display_name"] = display_name
    if not str(parsed.get("project_name") or "").strip():
        parsed["project_name"] = display_name or default_name
    parsed["model_manifest"] = _normalize_model_manifest(project_id, parsed)
    return yaml.safe_dump(parsed, sort_keys=False, allow_unicode=False)


def _project_discovery_entry(project_id: str, file_path: Path, project_cfg: Dict[str, Any]) -> Dict[str, Any]:
    source = project_cfg.get("source") if isinstance(project_cfg.get("source"), dict) else {}
    target = project_cfg.get("target") if isinstance(project_cfg.get("target"), dict) else {}
    if not target and isinstance(project_cfg.get("targets"), list) and project_cfg.get("targets"):
        first_target = project_cfg.get("targets")[0]
        if isinstance(first_target, dict):
            target = first_target

    display_name = _project_display_name_from_cfg(project_cfg, project_id)
    return {
        "id": project_id,
        "project_id": project_id,
        "name": display_name,
        "display_name": display_name,
        "file_name": file_path.name,
        "file_path": str(file_path.resolve()).replace('\\', '/'),
        "source": source.get("type", "fabric"),
        "target_type": target.get("type", "snowflake"),
        "workspace_id": source.get("workspace_id", ""),
    }


def _extract_manifest_dependencies(project_id: str, project_cfg: Dict[str, Any]) -> List[str]:
    if not isinstance(project_cfg, dict):
        return []
    manifest = project_cfg.get("model_manifest")
    if not isinstance(manifest, dict):
        return []
    dependencies = manifest.get("dependencies")
    if not isinstance(dependencies, list):
        return []
    normalized: List[str] = []
    for dep in dependencies:
        dep_id = str(dep).strip()
        if dep_id and dep_id != project_id:
            normalized.append(dep_id)
    return sorted(set(normalized))


def _collect_project_dependency_rows() -> Dict[str, Dict[str, Any]]:
    rows: Dict[str, Dict[str, Any]] = {}
    projects_dir = _compat_projects_dir()
    if projects_dir.exists() and projects_dir.is_dir():
        for file_path in sorted(projects_dir.glob("*.y*ml")):
            pid = file_path.stem.strip()
            if not pid:
                continue
            try:
                project_cfg = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            if not isinstance(project_cfg, dict):
                continue
            rows[pid] = {
                "project_id": pid,
                "dependencies": _extract_manifest_dependencies(pid, project_cfg),
                "source": "project_yaml",
            }

    for pid, project in _compat_projects.items():
        project_id = str(pid or "").strip()
        if not project_id:
            continue
        if project_id in rows:
            continue
        rows[project_id] = {
            "project_id": project_id,
            "dependencies": [],
            "source": "compat_memory",
        }
    return rows


def _detect_dependency_cycles(rows: Dict[str, Dict[str, Any]]) -> List[List[str]]:
    adjacency: Dict[str, List[str]] = {}
    for pid, row in rows.items():
        deps = [d for d in row.get("dependencies", []) if d in rows]
        adjacency[pid] = sorted(set(deps))

    cycles: List[List[str]] = []
    seen: set[str] = set()

    for start in sorted(adjacency.keys()):
        stack: List[tuple[str, List[str], set[str]]] = [(start, [start], {start})]
        while stack:
            node, path, path_set = stack.pop()
            for nxt in adjacency.get(node, []):
                if nxt == start and len(path) > 1:
                    cycle_nodes = path + [start]
                    ring = cycle_nodes[:-1]
                    min_idx = min(range(len(ring)), key=lambda i: ring[i])
                    normalized = tuple(ring[min_idx:] + ring[:min_idx] + [ring[min_idx]])
                    cycle_key = "->".join(normalized)
                    if cycle_key not in seen:
                        seen.add(cycle_key)
                        cycles.append(list(normalized))
                    continue
                if nxt in path_set:
                    continue
                stack.append((nxt, path + [nxt], path_set | {nxt}))
    return sorted(cycles, key=lambda c: "->".join(c))


async def get_project_dependency_graph_compat(strict_cycles: bool = False) -> Dict[str, Any]:
    _compat_ensure_loaded()
    rows = _collect_project_dependency_rows()

    nodes = [{"id": pid, "type": "project"} for pid in sorted(rows.keys())]
    edges: List[Dict[str, str]] = []
    seen_edges: set[str] = set()

    for pid, row in rows.items():
        for dep in row.get("dependencies", []):
            if dep not in rows:
                nodes.append({"id": dep, "type": "external"})
            edge_id = f"{pid}->{dep}"
            if edge_id in seen_edges:
                continue
            edges.append({"id": edge_id, "source": pid, "target": dep})
            seen_edges.add(edge_id)

    # keep node list deterministic and unique even with external deps
    unique_nodes: Dict[str, Dict[str, str]] = {}
    for node in nodes:
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            continue
        existing = unique_nodes.get(node_id)
        if existing and existing.get("type") == "project":
            continue
        unique_nodes[node_id] = {"id": node_id, "type": str(node.get("type") or "project")}

    cycles = _detect_dependency_cycles(rows)
    if strict_cycles and cycles:
        raise HTTPException(status_code=409, detail={"message": "Dependency cycle detected", "cycles": cycles})

    return {
        "nodes": sorted(unique_nodes.values(), key=lambda n: n["id"]),
        "edges": sorted(edges, key=lambda e: e["id"]),
        "project_count": len([n for n in unique_nodes.values() if n.get("type") == "project"]),
        "edge_count": len(edges),
        "cycles": cycles,
    }


async def get_project_dependency_impact_compat(project_id: str) -> Dict[str, Any]:
    _compat_ensure_loaded()
    target_id = str(project_id or "").strip()
    if not target_id:
        raise HTTPException(status_code=400, detail="project_id is required")

    rows = _collect_project_dependency_rows()
    if target_id not in rows:
        raise HTTPException(status_code=404, detail="Project not found")

    upstream = set(rows.get(target_id, {}).get("dependencies", []))
    reverse: Dict[str, List[str]] = {}
    for pid, row in rows.items():
        for dep in row.get("dependencies", []):
            reverse.setdefault(dep, []).append(pid)
    downstream = set(reverse.get(target_id, []))

    return {
        "project_id": target_id,
        "upstream_dependencies": sorted(upstream),
        "downstream_dependents": sorted(downstream),
    }


def _transitive_dependents(rows: Dict[str, Dict[str, Any]], target_id: str) -> List[str]:
    reverse: Dict[str, List[str]] = {}
    for pid, row in rows.items():
        for dep in row.get("dependencies", []):
            reverse.setdefault(dep, []).append(pid)

    seen: set[str] = set()
    stack: List[str] = list(sorted(reverse.get(target_id, [])))
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        for nxt in sorted(reverse.get(node, [])):
            if nxt not in seen:
                stack.append(nxt)
    return sorted(seen)


def _load_project_config_dict(project_id: str) -> Dict[str, Any]:
    yaml_text = _compat_load_project_yaml_text(project_id)
    if not str(yaml_text or "").strip():
        return {}
    try:
        parsed = yaml.safe_load(yaml_text) or {}
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _classify_contract_change(current_manifest: Dict[str, Any], proposed_manifest: Dict[str, Any]) -> Dict[str, Any]:
    current_id = str(current_manifest.get("contract_id") or "").strip()
    current_ver = str(current_manifest.get("contract_version") or "").strip()
    proposed_id = str(proposed_manifest.get("contract_id") or "").strip()
    proposed_ver = str(proposed_manifest.get("contract_version") or "").strip()

    breaking_reasons: List[str] = []
    non_breaking_reasons: List[str] = []

    if proposed_id and current_id and proposed_id != current_id:
        breaking_reasons.append(f"contract_id changed: {current_id} -> {proposed_id}")
    if proposed_ver and current_ver and proposed_ver != current_ver:
        major_current = (current_ver.split(".") + ["0"])[0]
        major_proposed = (proposed_ver.split(".") + ["0"])[0]
        if major_proposed != major_current:
            breaking_reasons.append(f"major contract_version changed: {current_ver} -> {proposed_ver}")
        else:
            non_breaking_reasons.append(f"contract_version changed: {current_ver} -> {proposed_ver}")

    current_deps = set(current_manifest.get("dependencies") or [])
    proposed_deps = set(proposed_manifest.get("dependencies") or [])
    removed_deps = sorted(current_deps - proposed_deps)
    added_deps = sorted(proposed_deps - current_deps)
    if removed_deps:
        breaking_reasons.append(f"dependencies removed: {', '.join(removed_deps)}")
    if added_deps:
        non_breaking_reasons.append(f"dependencies added: {', '.join(added_deps)}")

    return {
        "is_breaking": bool(breaking_reasons),
        "breaking_reasons": breaking_reasons,
        "non_breaking_reasons": non_breaking_reasons,
    }


async def preflight_project_contract_change_compat(project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    _compat_ensure_loaded()
    target_id = str(project_id or "").strip()
    if not target_id:
        raise HTTPException(status_code=400, detail="project_id is required")

    rows = _collect_project_dependency_rows()
    if target_id not in rows:
        raise HTTPException(status_code=404, detail="Project not found")

    current_cfg = _load_project_config_dict(target_id)
    current_manifest = _normalize_model_manifest(target_id, current_cfg)

    requested = payload if isinstance(payload, dict) else {}
    proposed_root = {"model_manifest": requested.get("model_manifest") if isinstance(requested.get("model_manifest"), dict) else {}}
    # Merge defaults from current so omitted fields remain stable
    merged_manifest = dict(current_manifest)
    merged_manifest.update(proposed_root["model_manifest"])
    proposed_manifest = _normalize_model_manifest(target_id, {"model_manifest": merged_manifest})

    classification = _classify_contract_change(current_manifest, proposed_manifest)
    direct_dependents = sorted([pid for pid, row in rows.items() if target_id in set(row.get("dependencies", []))])
    transitive = _transitive_dependents(rows, target_id)

    deprecation_date = str(proposed_manifest.get("deprecation_date") or requested.get("deprecation_date") or "").strip()
    if classification["is_breaking"] and not deprecation_date:
        classification["breaking_reasons"].append("deprecation_date is required for breaking contract changes")
        classification["is_breaking"] = True

    policy_mode = str(requested.get("policy_mode") or "warn").strip().lower()
    if policy_mode not in {"warn", "enforce"}:
        raise HTTPException(status_code=400, detail="policy_mode must be 'warn' or 'enforce'")

    required_actions: List[str] = []
    if classification["is_breaking"]:
        required_actions.append("publish_new_contract_version")
        required_actions.append("notify_downstream_consumers")
        if not deprecation_date:
            required_actions.append("set_deprecation_date")
    if transitive:
        required_actions.append("review_transitive_dependents")
    if direct_dependents:
        required_actions.append("validate_direct_dependents")
    required_actions = sorted(set(required_actions))

    allow_merge = not (policy_mode == "enforce" and classification["is_breaking"])

    return {
        "project_id": target_id,
        "current_manifest": current_manifest,
        "proposed_manifest": proposed_manifest,
        "policy_mode": policy_mode,
        "allow_merge": allow_merge,
        "required_actions": required_actions,
        "is_breaking": classification["is_breaking"],
        "breaking_reasons": classification["breaking_reasons"],
        "non_breaking_reasons": classification["non_breaking_reasons"],
        "direct_downstream_dependents": direct_dependents,
        "transitive_downstream_dependents": transitive,
    }


async def get_project_dependency_resolution_compat(project_id: str) -> Dict[str, Any]:
    _compat_ensure_loaded()
    target_id = str(project_id or "").strip()
    if not target_id:
        raise HTTPException(status_code=400, detail="project_id is required")

    rows = _collect_project_dependency_rows()
    if target_id not in rows:
        raise HTTPException(status_code=404, detail="Project not found")

    target_cfg = _load_project_config_dict(target_id)
    target_manifest = _normalize_model_manifest(target_id, target_cfg)
    dependency_pins = target_manifest.get("dependency_pins") if isinstance(target_manifest.get("dependency_pins"), dict) else {}

    resolutions: List[Dict[str, Any]] = []
    for dep_id in sorted(rows.get(target_id, {}).get("dependencies", [])):
        dep_cfg = _load_project_config_dict(dep_id)
        dep_manifest = _normalize_model_manifest(dep_id, dep_cfg) if dep_cfg else {
            "contract_version": "",
            "published_contract_versions": [],
        }
        available_versions = [
            str(v).strip()
            for v in (dep_manifest.get("published_contract_versions") or [])
            if str(v).strip()
        ]
        current_version = str(dep_manifest.get("contract_version") or "").strip()
        if current_version and current_version not in available_versions:
            available_versions.append(current_version)
        available_versions = sorted(set(available_versions))

        pinned = str(dependency_pins.get(dep_id) or "").strip()
        if pinned and pinned in available_versions:
            resolved = pinned
            status = "pinned"
        elif pinned and pinned not in available_versions:
            resolved = current_version
            status = "unresolved_pin"
        else:
            resolved = current_version
            status = "implicit"

        resolutions.append({
            "dependency_project_id": dep_id,
            "available_versions": available_versions,
            "pinned_version": pinned or None,
            "resolved_version": resolved or None,
            "status": status,
        })

    return {
        "project_id": target_id,
        "dependency_resolutions": resolutions,
    }


async def premerge_validate_projects_compat(payload: Dict[str, Any]) -> Dict[str, Any]:
    _compat_ensure_loaded()
    body = payload if isinstance(payload, dict) else {}
    project_ids_raw = body.get("project_ids")
    if not isinstance(project_ids_raw, list) or not project_ids_raw:
        raise HTTPException(status_code=400, detail="project_ids (non-empty list) is required")

    project_ids = [str(pid).strip() for pid in project_ids_raw if str(pid).strip()]
    if not project_ids:
        raise HTTPException(status_code=400, detail="project_ids must contain valid values")

    policy_mode = str(body.get("policy_mode") or "warn").strip().lower()
    if policy_mode not in {"warn", "enforce"}:
        raise HTTPException(status_code=400, detail="policy_mode must be 'warn' or 'enforce'")

    strict_cycles = bool(body.get("strict_cycles", True))
    proposed_manifests = body.get("proposed_manifests") if isinstance(body.get("proposed_manifests"), dict) else {}

    cycle_payload = await get_project_dependency_graph_compat(strict_cycles=False)
    cycles = cycle_payload.get("cycles", [])

    project_results: List[Dict[str, Any]] = []
    for pid in project_ids:
        req = {
            "policy_mode": policy_mode,
            "model_manifest": proposed_manifests.get(pid) if isinstance(proposed_manifests.get(pid), dict) else {},
        }
        project_results.append(await preflight_project_contract_change_compat(pid, req))

    blocking_reasons: List[str] = []
    if strict_cycles and cycles:
        blocking_reasons.append("dependency_cycles_detected")
    if any(not bool(item.get("allow_merge")) for item in project_results):
        blocking_reasons.append("contract_policy_block")

    allow_merge = len(blocking_reasons) == 0
    return {
        "allow_merge": allow_merge,
        "policy_mode": policy_mode,
        "strict_cycles": strict_cycles,
        "cycles": cycles,
        "project_results": project_results,
        "blocking_reasons": blocking_reasons,
    }


async def list_project_discovery_compat():
    _compat_ensure_loaded()
    entries: List[Dict[str, Any]] = []
    projects_dir = _compat_projects_dir()
    if not projects_dir.exists() or not projects_dir.is_dir():
        return entries

    for file_path in sorted(projects_dir.glob("*.y*ml")):
        project_id = file_path.stem.strip()
        if not project_id:
            continue
        try:
            project_cfg = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
            if not isinstance(project_cfg, dict):
                continue
            entries.append(_project_discovery_entry(project_id, file_path, project_cfg))
        except Exception as exc:
            logger.warning("Failed to load project discovery entry %s: %s", file_path, exc)
    return entries


async def list_projects_compat():
    """Compatibility: newfrontend expects a projects collection."""
    _compat_ensure_loaded()
    deduped: Dict[str, Dict[str, Any]] = {}
    
    # Load modular projects from Config/projects (or config/projects)
    projects_dir = _compat_projects_dir()
    if projects_dir.exists() and projects_dir.is_dir():
        for file_path in sorted(projects_dir.glob("*.y*ml")):
            pid = file_path.stem
            try:
                project_cfg = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
                if not isinstance(project_cfg, dict):
                    continue
                deduped[pid] = _project_discovery_entry(pid, file_path, project_cfg)
            except Exception as e:
                logger.warning(f"Failed to load project config {file_path}: {e}")

    for p in _compat_projects.values():
        pid = str(p.get("id") or p.get("project_id") or "").strip()
        if not pid or pid in deduped:
            continue
        
        # Filter out auto-generated test projects
        project_name = str(p.get("name") or "").strip().lower()
        is_test = (
            project_name in ("test", "teste", "test project") or
            project_name == "fabricmodel"  # Auto-generated default
        )
        if is_test:
            # Skip test/auto-generated projects
            continue
        
        current = deduped.get(pid)
        if not current:
            deduped[pid] = p
            continue
        cur_ts = str(current.get("updated_at") or current.get("created_at") or "")
        new_ts = str(p.get("updated_at") or p.get("created_at") or "")
        if new_ts >= cur_ts:
            deduped[pid] = p

    return list(deduped.values())


async def create_project_compat(request: dict):
    """Compatibility: create in-memory project for UI continuity."""
    _compat_ensure_loaded()
    payload = request or {}
    payload_name = _compat_clean_project_name(payload.get("name"), "")
    src = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    src_type = (src.get("type") or payload.get("source_type") or "fabric").strip().lower()
    ws_id = str(src.get("workspace_id") or payload.get("workspace_id") or "").strip()

    # Always create a distinct project record for create requests.
    # Projects that target the same semantic model/workspace must not share
    # mapping state implicitly, because each project can have different naming.

    safe_name = re.sub(r'[^a-zA-Z0-9_]+', '-', payload_name.lower()).strip('-')
    default_id = f"proj-{safe_name}" if safe_name else f"proj-{int(_time.time() * 1000)}"
    requested_id = str(payload.get("id") or payload.get("project_id") or default_id).strip()
    project_id = requested_id or default_id
    if project_id in _compat_projects:
        # Avoid reusing an existing project's mapping cache/state when the user
        # creates another project with the same semantic model/name.
        project_id = f"{project_id}-{int(_time.time() * 1000)}"
    project = _compat_project_payload(project_id, payload)
    _compat_projects[project_id] = project

    config_yaml = payload.get("config_yaml")
    if isinstance(config_yaml, str) and config_yaml.strip():
        normalized_yaml = _normalize_project_config_yaml(project_id, config_yaml, project.get("name") or project_id)
        _compat_project_configs[project_id] = normalized_yaml
        try:
            _compat_save_project_yaml_text(project_id, normalized_yaml)
            _clear_project_mapping_cache(project_id)
        except Exception as exc:
            logger.warning("Failed to persist project config for %s: %s", project_id, exc)
    else:
        project_yaml = _compat_load_project_yaml_text(project_id)
        repo_yaml = _compat_load_repo_yaml_text()
        selected_yaml = project_yaml or repo_yaml or _compat_default_project_yaml(project)
        normalized_yaml = _normalize_project_config_yaml(project_id, selected_yaml, project.get("name") or project_id)
        _compat_project_configs.setdefault(project_id, normalized_yaml)
        try:
            _compat_save_project_yaml_text(project_id, normalized_yaml)
            _clear_project_mapping_cache(project_id)
        except Exception as exc:
            logger.warning("Failed to initialize project config file for %s: %s", project_id, exc)

    _compat_project_runs.setdefault(project_id, [])
    _compat_save_store()
    return project


async def get_project_compat(project_id: str):
    _compat_ensure_loaded()
    project = _compat_projects.get(project_id)
    if not project:
        # Compatibility upsert for stale in-memory cache after reload.
        project = _compat_project_payload(project_id, {
            "id": project_id,
            "name": f"Recovered {project_id}",
            "source": {"type": "fabric"},
            "target": {"type": "snowflake"},
        })
        _compat_projects[project_id] = project
        _compat_save_store()
    return project


async def patch_project_compat(project_id: str, payload: dict):
    _compat_ensure_loaded()
    project = _compat_projects.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    for key in ("name", "display_name", "description", "folder_id", "status"):
        if key in (payload or {}):
            project[key] = payload.get(key)

    if payload.get("name") and not payload.get("display_name"):
        project["display_name"] = payload.get("name")
        project["name"] = payload.get("name")
    elif payload.get("display_name") and not payload.get("name"):
        project["name"] = payload.get("display_name")

    if isinstance(payload.get("source"), dict):
        project["source"] = payload["source"].get("type") or project.get("source")
        project["adapter"] = project["source"]
        if "workspace_id" in payload["source"]:
            project["workspace_id"] = payload["source"].get("workspace_id")
        if "connection_tag" in payload["source"]:
            project["connection_tag"] = payload["source"].get("connection_tag")

    if isinstance(payload.get("target"), dict):
        project["target_type"] = payload["target"].get("type") or project.get("target_type")

    if isinstance(payload.get("targets"), list) and payload.get("targets"):
        first_target = payload["targets"][0] if isinstance(payload["targets"][0], dict) else {}
        if first_target.get("type"):
            project["target_type"] = first_target.get("type")
        if first_target.get("connection_tag"):
            project["connection_tag"] = first_target.get("connection_tag")

    if payload.get("connection_tag"):
        project["connection_tag"] = payload.get("connection_tag")

    project["updated_at"] = _compat_now_iso()
    _compat_projects[project_id] = project
    _compat_save_store()
    return project


async def delete_project_compat(project_id: str):
    _compat_ensure_loaded()
    _compat_projects.pop(project_id, None)
    _compat_project_configs.pop(project_id, None)
    _compat_project_runs.pop(project_id, None)
    _compat_save_store()
    return Response(status_code=204)


async def get_project_config_compat(project_id: str, prefer_repo: bool = Query(default=False)):
    _compat_ensure_loaded()
    project = _compat_projects.get(project_id)
    if not project:
        # Compatibility upsert: keep UI editable even if project cache was reset.
        project = _compat_project_payload(project_id, {
            "id": project_id,
            "name": f"Recovered {project_id}",
            "source": {"type": "fabric"},
            "target": {"type": "snowflake"},
        })
        _compat_projects[project_id] = project
    # IMPORTANT: default behavior prefers per-project config so "Copy Presets"
    # can load different YAMLs for different projects. Some UI flows (for
    # example Model Mapping) can opt into prefer_repo=True to reflect the
    # workspace semabridge.yaml as the single source of truth.
    repo_yaml = _compat_load_repo_yaml_text()
    prefer_repo_flag = prefer_repo if isinstance(prefer_repo, bool) else False
    project_yaml = _compat_load_project_yaml_text(project_id)
    if prefer_repo_flag and repo_yaml:
        yaml_text = repo_yaml
        _compat_project_configs[project_id] = repo_yaml
    else:
        yaml_text = project_yaml or _compat_project_configs.get(project_id) or repo_yaml or _compat_default_project_yaml(project)
        yaml_text = _normalize_project_config_yaml(project_id, yaml_text, project.get("name") or project_id)
        if not project_yaml and yaml_text:
            try:
                _compat_save_project_yaml_text(project_id, yaml_text)
            except Exception as exc:
                logger.warning("Failed to persist hydrated project config for %s: %s", project_id, exc)
    _compat_project_configs[project_id] = yaml_text
    return {
        "project_id": project_id,
        "config_yaml": yaml_text,
        "yaml_path": str(_compat_project_yaml_path(project_id).resolve()).replace('\\\\', '/'),
    }


async def save_project_config_compat(project_id: str, payload: dict):
    _compat_ensure_loaded()
    project = _compat_projects.get(project_id)
    if not project:
        # Compatibility upsert: allow saving config even when only project_id is known.
        project = _compat_project_payload(project_id, {
            "id": project_id,
            "name": f"Recovered {project_id}",
            "source": {"type": "fabric"},
            "target": {"type": "snowflake"},
        })
        _compat_projects[project_id] = project
    yaml_text = str((payload or {}).get("config_yaml") or "").strip()
    if not yaml_text:
        raise HTTPException(status_code=400, detail="config_yaml is required")
    yaml_text = _normalize_project_config_yaml(project_id, yaml_text, project.get("name") or project_id)
    _compat_project_configs[project_id] = yaml_text
    try:
        _compat_save_project_yaml_text(project_id, yaml_text)
        _clear_project_mapping_cache(project_id)
    except Exception as exc:
        logger.warning("Failed to persist project config for %s: %s", project_id, exc)
    project["updated_at"] = _compat_now_iso()
    _compat_save_store()
    return {
        "status": "saved",
        "project_id": project_id,
        "yaml_path": str(_compat_project_yaml_path(project_id).resolve()).replace('\\\\', '/'),
        "warnings": [],
    }


def _extract_snapshot_connectors(snapshot_obj: Any) -> List[str]:
    """Extract source/target connector names from snapshot SML payload."""
    connectors: List[str] = []
    sml = getattr(snapshot_obj, "sml_blob", {}) or {}
    if not isinstance(sml, dict):
        return connectors

    for key in ("source_type", "source", "adapter", "connector"):
        val = sml.get(key)
        if isinstance(val, str) and val.strip():
            connectors.append(val.strip())
            break

    target_val = sml.get("target_type") or sml.get("target")
    if isinstance(target_val, str) and target_val.strip():
        connectors.append(target_val.strip())
    elif isinstance(sml.get("targets"), list):
        for tgt in sml.get("targets"):
            if isinstance(tgt, dict):
                t = str(tgt.get("type") or "").strip()
                if t:
                    connectors.append(t)

    deduped: List[str] = []
    seen = set()
    for c in connectors:
        key = c.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
    return deduped


def _snapshot_graph_payload(
    snapshot_obj: Any,
    model_name: str,
    include_system_tables: bool = False,
    include_column_lineage: bool = False,
) -> Dict[str, Any]:
    """Build React-Flow compatible graph payload from a snapshot object."""
    snapshot_id = getattr(snapshot_obj, "snapshot_id", "")
    sml = getattr(snapshot_obj, "sml_blob", {}) or {}
    if not isinstance(sml, dict):
        sml = {}

    def _safe_id(raw: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_\-:.]", "_", str(raw or "unknown"))

    system_prefixes = (
        "information_schema.",
        "pg_",
        "sqlite_",
        "duckdb_",
        "sys.",
        "__",
    )

    def _is_system_table(table_name: str) -> bool:
        val = str(table_name or "").strip().lower()
        if not val:
            return False
        return val.startswith(system_prefixes)

    def _qualify(schema_name: str, table_name: str) -> str:
        schema_name = str(schema_name or "").strip()
        table_name = str(table_name or "").strip()
        if not table_name:
            return ""
        if "." in table_name:
            return table_name
        if schema_name:
            return f"{schema_name}.{table_name}"
        return table_name

    def _card_symbol(card: str) -> str:
        c = str(card or "").strip().lower().replace("_", "-")
        mapping = {
            "one-to-one": "1:1",
            "one-to-many": "1:*",
            "many-to-one": "*:1",
            "many-to-many": "*:*",
        }
        return mapping.get(c, c or "?:?")

    def _column_label(column: Any, fallback: str) -> str:
        if isinstance(column, dict):
            for key in ("name", "column_name", "unique_name", "label", "field"):
                value = str(column.get(key) or "").strip()
                if value:
                    return value
        value = str(column or "").strip()
        return value or fallback

    detected_model_id = str(getattr(snapshot_obj, "project_id", "") or model_name or "model").strip()
    detected_model_label = str(sml.get("model_name") or detected_model_id or "model").strip()
    model_node_id = f"model-{_safe_id(detected_model_id)}"

    nodes: List[Dict[str, Any]] = [{
        "id": model_node_id,
        "type": "modelNode",
        "position": {"x": 400, "y": 150},
        "data": {
            "label": detected_model_label,
            "model_id": detected_model_id,
            "workspace_id": str(sml.get("workspace_id") or ""),
            "description": str(sml.get("description") or ""),
            "status": "valid",
            "nodeType": "model",
        },
    }]
    edges: List[Dict[str, Any]] = []

    table_nodes: Dict[str, str] = {}
    table_positions: Dict[str, int] = {}
    column_nodes: Dict[str, str] = {}
    excluded_system_tables = 0
    included_columns = 0
    included_column_relationships = 0

    datasets = sml.get("datasets", [])
    if not isinstance(datasets, list):
        datasets = []

    # Fallback: extract models array directly if present
    sml_models = sml.get("models", [])
    if isinstance(sml_models, list):
        for entry in sml_models:
            if isinstance(entry, dict):
                datasets.append(entry)
            elif isinstance(entry, str):
                datasets.append({"name": entry, "table": entry})

    # Fallback: extract models from source block
    source_obj = sml.get("source", {})
    if isinstance(source_obj, dict):
        src_models = source_obj.get("models", [])
        if isinstance(src_models, list):
            for entry in src_models:
                if isinstance(entry, dict):
                    datasets.append(entry)
                elif isinstance(entry, str):
                    datasets.append({"name": entry, "table": entry})

    entities = sml.get("entities", {})
    if isinstance(entities, dict):
        for ent_name, ent_def in entities.items():
            if not isinstance(ent_def, dict):
                continue
            datasets.append({
                "name": ent_name,
                "table": ent_def.get("table") or ent_name,
                "schema": ent_def.get("schema") or ent_def.get("database_schema") or "PUBLIC",
                "source_type": ent_def.get("source_type") or "snapshot",
                "columns": ent_def.get("columns") or [],
            })

    for idx, ds in enumerate(datasets):
        if not isinstance(ds, dict):
            continue
        schema = str(ds.get("source_schema") or ds.get("schema") or ds.get("database_schema") or "PUBLIC")
        table = str(
            ds.get("source_table")
            or ds.get("table")
            or ds.get("name")
            or ds.get("unique_name")
            or ds.get("label")
            or f"table_{idx}"
        )
        qualified = _qualify(schema, table)

        if qualified in table_nodes and table.startswith("unknown"):
            qualified = _qualify(schema, f"{table}_{idx + 1}")

        if not include_system_tables and _is_system_table(qualified):
            excluded_system_tables += 1
            continue

        if qualified in table_nodes:
            continue

        table_node_id = f"table-{_safe_id(qualified)}"
        table_nodes[qualified] = table_node_id
        table_nodes.setdefault(table, table_node_id)
        table_positions[qualified] = 120 + (len(table_nodes) - 1) * 260
        columns = ds.get("columns") if isinstance(ds.get("columns"), list) else []

        nodes.append({
            "id": table_node_id,
            "type": "tableNode",
            "position": {"x": table_positions[qualified], "y": 0},
            "data": {
                "label": qualified,
                "table_name": table,
                "schema": schema,
                "source_type": str(ds.get("source_type") or "snapshot"),
                "columns": columns,
                "model_id": detected_model_id,
                "nodeType": "table",
                "status": "valid",
            },
        })

        edges.append({
            "id": f"e-{table_node_id}-{model_node_id}",
            "source": table_node_id,
            "target": model_node_id,
            "animated": True,
            "style": {"stroke": "#22C55E"},
        })

        if include_column_lineage:
            column_items: List[tuple[str, int, Any]] = []
            for column_index, column in enumerate(columns):
                column_name = _column_label(column, f"column_{column_index + 1}")
                if not column_name:
                    continue
                column_items.append((column_name.lower(), column_index, column))

            for column_index, (_, original_index, column) in enumerate(sorted(column_items, key=lambda item: (item[0], item[1]))):
                column_name = _column_label(column, f"column_{original_index + 1}")
                column_key = f"{qualified}::{column_name}".lower()
                if column_key in column_nodes:
                    continue

                column_node_id = f"column-{_safe_id(qualified)}-{_safe_id(column_name)}"
                column_nodes[column_key] = column_node_id
                column_nodes.setdefault(f"{table}::{column_name}".lower(), column_node_id)
                included_columns += 1
                nodes.append({
                    "id": column_node_id,
                    "type": "columnNode",
                    "position": {"x": table_positions.get(qualified, 120), "y": 120 + column_index * 72},
                    "data": {
                        "label": column_name,
                        "column_name": column_name,
                        "table_name": qualified,
                        "schema": schema,
                        "source_type": str(ds.get("source_type") or "snapshot"),
                        "nodeType": "column",
                        "status": str(column.get("status") or "valid") if isinstance(column, dict) else "valid",
                        "expression": str(column.get("expression") or "") if isinstance(column, dict) else "",
                        "model_id": detected_model_id,
                    },
                })
                edges.append({
                    "id": f"e-{table_node_id}-{column_node_id}",
                    "source": table_node_id,
                    "target": column_node_id,
                    "animated": False,
                    "style": {"stroke": "#94A3B8", "strokeDasharray": "4 4"},
                })

    measures = sml.get("metrics", sml.get("measures", []))
    if not isinstance(measures, list):
        measures = []

    for midx, measure in enumerate(measures):
        if not isinstance(measure, dict):
            continue
        measure_name = str(measure.get("name") or measure.get("unique_name") or measure.get("label") or f"measure_{midx}")
        measure_node_id = f"measure-{_safe_id(detected_model_id)}-{midx}"
        nodes.append({
            "id": measure_node_id,
            "type": "measureNode",
            "position": {"x": 120 + midx * 260, "y": 320},
            "data": {
                "label": measure_name,
                "expression": str(measure.get("expression") or ""),
                "data_type": str(measure.get("data_type") or measure.get("format_string") or ""),
                "parent_model": detected_model_id,
                "model_id": detected_model_id,
                "nodeType": "measure",
            },
        })
        edges.append({
            "id": f"e-{model_node_id}-{measure_node_id}",
            "source": model_node_id,
            "target": measure_node_id,
            "animated": False,
            "style": {"stroke": "#EAB308"},
        })

    relationships = sml.get("relationships", [])
    if not isinstance(relationships, list):
        relationships = []

    for ridx, rel in enumerate(relationships):
        if not isinstance(rel, dict):
            continue

        from_schema = rel.get("from_schema") or rel.get("source_schema") or ""
        to_schema = rel.get("to_schema") or rel.get("target_schema") or ""
        from_table = rel.get("from_table") or rel.get("from_model") or rel.get("from") or rel.get("source")
        to_table = rel.get("to_table") or rel.get("to_model") or rel.get("to") or rel.get("target")

        from_key = _qualify(from_schema, str(from_table or "")).strip()
        to_key = _qualify(to_schema, str(to_table or "")).strip()

        if not from_key or not to_key:
            continue
        if (not include_system_tables) and (_is_system_table(from_key) or _is_system_table(to_key)):
            continue

        if from_key not in table_nodes:
            nid = f"table-{_safe_id(from_key)}"
            table_nodes[from_key] = nid
            nodes.append({
                "id": nid,
                "type": "tableNode",
                "position": {"x": 120 + (len(table_nodes) - 1) * 260, "y": 0},
                "data": {
                    "label": from_key,
                    "table_name": from_key,
                    "schema": from_schema or "PUBLIC",
                    "source_type": "snapshot",
                    "columns": [],
                    "model_id": detected_model_id,
                    "nodeType": "table",
                    "status": "broken",
                },
            })

        if to_key not in table_nodes:
            nid = f"table-{_safe_id(to_key)}"
            table_nodes[to_key] = nid
            nodes.append({
                "id": nid,
                "type": "tableNode",
                "position": {"x": 120 + (len(table_nodes) - 1) * 260, "y": 0},
                "data": {
                    "label": to_key,
                    "table_name": to_key,
                    "schema": to_schema or "PUBLIC",
                    "source_type": "snapshot",
                    "columns": [],
                    "model_id": detected_model_id,
                    "nodeType": "table",
                    "status": "broken",
                },
            })

        cardinality = str(rel.get("cardinality") or rel.get("relationship_type") or rel.get("type") or "many-to-one")
        from_col = str(rel.get("from_column") or (rel.get("from_columns") or [""])[0] or rel.get("join_key") or "")
        to_col = str(rel.get("to_column") or (rel.get("to_columns") or [""])[0] or "")
        join_label = f"{from_col} → {to_col}" if from_col and to_col else from_col

        edges.append({
            "id": f"rel-{ridx}-{table_nodes[from_key]}-{table_nodes[to_key]}",
            "source": table_nodes[from_key],
            "target": table_nodes[to_key],
            "label": f"{_card_symbol(cardinality)}{f' • {join_label}' if join_label else ''}",
            "type": "smoothstep",
            "style": {"stroke": "#818CF8", "strokeDasharray": "6 3"},
            "data": {
                "cardinality": cardinality,
                "from_column": from_col,
                "to_column": to_col,
            },
        })

        if include_column_lineage and from_col and to_col:
            source_col_id = column_nodes.get(f"{from_key}::{from_col}".lower())
            target_col_id = column_nodes.get(f"{to_key}::{to_col}".lower())
            if not source_col_id and from_table:
                source_col_id = column_nodes.get(f"{str(from_table).strip()}::{from_col}".lower())
            if not target_col_id and to_table:
                target_col_id = column_nodes.get(f"{str(to_table).strip()}::{to_col}".lower())
            if source_col_id and target_col_id:
                included_column_relationships += 1
                edges.append({
                    "id": f"colrel-{ridx}-{source_col_id}-{target_col_id}",
                    "source": source_col_id,
                    "target": target_col_id,
                    "label": f"{_card_symbol(cardinality)}{f' • {join_label}' if join_label else ''}",
                    "type": "smoothstep",
                    "style": {"stroke": "#6366F1", "strokeDasharray": "3 2"},
                    "data": {
                        "cardinality": cardinality,
                        "from_column": from_col,
                        "to_column": to_col,
                        "nodeType": "column_relationship",
                    },
                })

    return {
        "nodes": nodes,
        "edges": edges,
        "snapshot_id": snapshot_id,
        "model": detected_model_id,
        "model_label": detected_model_label,
        "project_id": detected_model_id,
        "timestamp": getattr(snapshot_obj, "timestamp", None),
        "version_tag": getattr(snapshot_obj, "version_tag", None),
        "run_id": getattr(snapshot_obj, "run_id", None),
        "status": getattr(snapshot_obj, "status", "success"),
        "connectors": _extract_snapshot_connectors(snapshot_obj),
        "meta": {
            "tables_included": len(table_nodes),
            "columns_included": included_columns,
            "column_relationships_included": included_column_relationships,
            "system_tables_excluded": excluded_system_tables,
            "include_system_tables": include_system_tables,
        },
    }


async def graph_snapshots_compat(model_name: str):
    """Snapshot history for Explore time-machine (newest first).
    
    When model_name='__all__', returns snapshots from ALL projects.
    Otherwise returns snapshots for a specific project.
    """
    try:
        logger.info("[Explore] Snapshot list requested model=%s", model_name)
        def _snap_attr(snap: Any, key: str, default: Any = None) -> Any:
            if isinstance(snap, dict):
                return snap.get(key, default)
            return getattr(snap, key, default)
        def _semantic_names(snap: Any) -> List[str]:
            # Rich extraction when full snapshot (with sml_blob) is available.
            blob = _snap_attr(snap, "sml_blob", {}) or {}
            if not isinstance(blob, dict):
                blob = {}
            names: List[str] = []
            primary = str(blob.get("model_name") or blob.get("display_name") or "").strip()
            if primary:
                names.append(primary)
            source = blob.get("source") if isinstance(blob.get("source"), dict) else {}
            src_models = source.get("models") if isinstance(source, dict) else []
            if isinstance(src_models, list):
                for entry in src_models:
                    if isinstance(entry, str):
                        value = str(entry).strip()
                    elif isinstance(entry, dict):
                        value = str(entry.get("name") or entry.get("model") or entry.get("table") or "").strip()
                    else:
                        value = ""
                    if value and value not in names:
                        names.append(value)
            # Lightweight rows fallback.
            for key in ("model_label", "semantic_model", "model_name"):
                value = str(_snap_attr(snap, key, "") or "").strip()
                if value and value not in names:
                    names.append(value)
            return names
        
        # Handle __all__ specially to query all projects.
        # Keep this bounded so Explore remains responsive on large histories.
        snapshot_limit = int(os.getenv("SEMABRIDGE_GRAPH_SNAPSHOTS_LIMIT", "500"))
        snapshot_limit = max(50, min(snapshot_limit, 2000))
        if model_name == '__all__':
            if hasattr(db_manager, "list_all_snapshots_meta"):
                snapshots = db_manager.list_all_snapshots_meta(limit=snapshot_limit)
            else:
                snapshots = db_manager.list_all_snapshots(limit=snapshot_limit)
        else:
            # For project-scoped queries, use full snapshots to surface semantic model names.
            snapshots = db_manager.list_snapshots(model_name, limit=snapshot_limit)
            if not snapshots:
                # Backward-compat resolver:
                # Older runs may have committed snapshots under project display name
                # (e.g., "Client Data") instead of canonical project_id (e.g., "proj-client_sf").
                alias_candidates: List[str] = []

                # Deterministic aliases derived from the requested model id.
                # This covers common cases where snapshots were committed under
                # a bare name ("sales") while Explore requests prefixed ids
                # ("proj-sales" / "preview-...").
                requested = str(model_name or "").strip()
                if requested:
                    alias_candidates.append(requested)
                    lower_requested = requested.lower()
                    for prefix in ("proj-", "preview-"):
                        if lower_requested.startswith(prefix):
                            stripped = requested[len(prefix):].strip()
                            if stripped and stripped not in alias_candidates:
                                alias_candidates.append(stripped)
                        else:
                            prefixed = f"{prefix}{requested}"
                            if prefixed not in alias_candidates:
                                alias_candidates.append(prefixed)

                project_row = _compat_projects.get(model_name) if isinstance(_compat_projects.get(model_name), dict) else {}
                if project_row:
                    for key in ("name", "display_name", "project_name"):
                        val = str(project_row.get(key) or "").strip()
                        if val and val not in alias_candidates:
                            alias_candidates.append(val)
                try:
                    cfg_text = str(_compat_project_configs.get(model_name) or "").strip()
                    if cfg_text:
                        cfg = yaml.safe_load(cfg_text) or {}
                        if isinstance(cfg, dict):
                            cfg_name = str(cfg.get("project_name") or "").strip()
                            if cfg_name and cfg_name not in alias_candidates:
                                alias_candidates.append(cfg_name)
                except Exception:
                    pass

                for alias in alias_candidates:
                    alias_snaps = db_manager.list_snapshots(alias, limit=snapshot_limit)
                    if alias_snaps:
                        logger.info(
                            "[Explore] Snapshot list alias-resolved model=%s alias=%s count=%s",
                            model_name,
                            alias,
                            len(alias_snaps),
                        )
                        snapshots = alias_snaps
                        break
        
        logger.info("[Explore] Snapshot list resolved model=%s count=%s", model_name, len(snapshots or []))
        rows = []
        for s in snapshots:
            names = _semantic_names(s)
            project_id = _snap_attr(s, "project_id")
            rows.append({
                "snapshot_id": _snap_attr(s, "snapshot_id"),
                "timestamp": _snap_attr(s, "timestamp"),
                "version_tag": _snap_attr(s, "version_tag") or f"v{str(_snap_attr(s, 'snapshot_id') or '')[:8]}",
                "status": _snap_attr(s, "status", "success") or "success",
                "duration_ms": _snap_attr(s, "duration_ms", 0) or 0,
                "project_id": project_id,
                "model_id": project_id,
                "model_name": project_id,
                "model_label": (names[0] if names else project_id),
                "semantic_models": names,
                "snapshot_scope": project_id,
                "run_id": _snap_attr(s, "run_id"),
                "initiated_by": _snap_attr(s, "initiated_by"),
                "connectors": _extract_snapshot_connectors(s),
            })
        return rows
    except Exception as exc:
        logger.debug("Failed to list snapshots for %s: %s", model_name, exc)
        return []


async def graph_snapshot_compat(
    model_name: str,
    snapshot_id: str,
    include_system_tables: bool = False,
    include_column_lineage: bool = False,
):
    """Load graph for a single snapshot."""
    try:
        logger.info(
            "[Explore] Snapshot graph requested model=%s snapshot_id=%s include_system_tables=%s",
            model_name,
            snapshot_id,
            include_system_tables,
        )
        snapshot = db_manager.get_snapshot(snapshot_id)
        if not snapshot:
            logger.warning("Snapshot %s not found", snapshot_id)
            return {"nodes": [], "edges": [], "snapshot_id": snapshot_id, "model": model_name}
        payload = _snapshot_graph_payload(snapshot, model_name, include_system_tables, include_column_lineage)
        logger.info(
            "[Explore] Snapshot graph ready model=%s snapshot_id=%s nodes=%s edges=%s",
            model_name,
            snapshot_id,
            len(payload.get("nodes") or []),
            len(payload.get("edges") or []),
        )
        return payload
    except Exception as exc:
        logger.debug("Failed to load snapshot graph %s: %s", snapshot_id, exc)
        return {"nodes": [], "edges": [], "snapshot_id": snapshot_id, "model": model_name}


async def compare_graph_snapshots_compat(
    model_name: str,
    from_snapshot_id: str,
    to_snapshot_id: str,
    include_system_tables: bool = False,
    include_column_lineage: bool = False,
):
    """Compare two snapshots and return graph diff + tabular change details."""
    try:
        snap_from = db_manager.get_snapshot(from_snapshot_id)
        snap_to = db_manager.get_snapshot(to_snapshot_id)

        if not snap_from or not snap_to:
            return {
                "summary": {"added": 0, "removed": 0, "modified": 0, "unchanged": 0},
                "changes": [],
                "relationships": [],
                "styled_graph": {"nodes": [], "edges": []},
            }

        from_graph = _snapshot_graph_payload(snap_from, model_name, include_system_tables, include_column_lineage)
        to_graph = _snapshot_graph_payload(snap_to, model_name, include_system_tables, include_column_lineage)

        from_nodes = from_graph.get("nodes", [])
        to_nodes = to_graph.get("nodes", [])
        from_edges = from_graph.get("edges", [])
        to_edges = to_graph.get("edges", [])

        def _node_key(n: Dict[str, Any]) -> str:
            d = n.get("data", {}) if isinstance(n.get("data"), dict) else {}
            return f"{d.get('nodeType', '')}:{str(d.get('label') or n.get('id') or '')}"

        def _edge_key(e: Dict[str, Any], node_map: Dict[str, Dict[str, Any]]) -> str:
            src = str(e.get("source") or "")
            tgt = str(e.get("target") or "")
            d_src = (node_map.get(src, {}).get("data") or {}) if isinstance(node_map.get(src, {}), dict) else {}
            d_tgt = (node_map.get(tgt, {}).get("data") or {}) if isinstance(node_map.get(tgt, {}), dict) else {}
            src_lbl = str(d_src.get("label") or src)
            tgt_lbl = str(d_tgt.get("label") or tgt)
            rel_lbl = str(e.get("label") or "")
            return f"{src_lbl}->{tgt_lbl}:{rel_lbl}"

        from_node_map = {_node_key(n): n for n in from_nodes}
        to_node_map = {_node_key(n): n for n in to_nodes}
        from_id_map = {str(n.get("id")): n for n in from_nodes}
        to_id_map = {str(n.get("id")): n for n in to_nodes}
    except Exception as e:
        logger.error(f"Error comparing snapshots: {str(e)}")
        return {
            "summary": {"added": 0, "removed": 0, "modified": 0, "unchanged": 0},
            "changes": [],
            "relationships": [],
            "styled_graph": {"nodes": [], "edges": []},
        }

        changes: List[Dict[str, Any]] = []

        added_keys = sorted(set(to_node_map.keys()) - set(from_node_map.keys()))
        removed_keys = sorted(set(from_node_map.keys()) - set(to_node_map.keys()))
        common_keys = sorted(set(from_node_map.keys()) & set(to_node_map.keys()))

        for k in added_keys:
            n = to_node_map[k]
            d = n.get("data", {}) if isinstance(n.get("data"), dict) else {}
            changes.append({
                "change_type": "added",
                "entity_type": d.get("nodeType") or "node",
                "entity_name": d.get("label") or n.get("id"),
                "details": "Entity added",
                "from_value": None,
                "to_value": d,
            })

        for k in removed_keys:
            n = from_node_map[k]
            d = n.get("data", {}) if isinstance(n.get("data"), dict) else {}
            changes.append({
                "change_type": "removed",
                "entity_type": d.get("nodeType") or "node",
                "entity_name": d.get("label") or n.get("id"),
                "details": "Entity removed",
                "from_value": d,
                "to_value": None,
            })

        for k in common_keys:
            n1 = from_node_map[k]
            n2 = to_node_map[k]
            d1 = n1.get("data", {}) if isinstance(n1.get("data"), dict) else {}
            d2 = n2.get("data", {}) if isinstance(n2.get("data"), dict) else {}
            left = {
                "columns": d1.get("columns") or [],
                "expression": d1.get("expression") or "",
                "data_type": d1.get("data_type") or "",
                "status": d1.get("status") or "",
            }
            right = {
                "columns": d2.get("columns") or [],
                "expression": d2.get("expression") or "",
                "data_type": d2.get("data_type") or "",
                "status": d2.get("status") or "",
            }
            if json.dumps(left, sort_keys=True, default=str) != json.dumps(right, sort_keys=True, default=str):
                changes.append({
                    "change_type": "modified",
                    "entity_type": d2.get("nodeType") or d1.get("nodeType") or "node",
                    "entity_name": d2.get("label") or d1.get("label") or n2.get("id"),
                    "details": "Schema/metric definition changed",
                    "from_value": left,
                    "to_value": right,
                })
            else:
                changes.append({
                    "change_type": "unchanged",
                    "entity_type": d2.get("nodeType") or d1.get("nodeType") or "node",
                    "entity_name": d2.get("label") or d1.get("label") or n2.get("id"),
                    "details": "No changes detected",
                    "from_value": left,
                    "to_value": right,
                })

        from_edge_map = {_edge_key(e, from_id_map): e for e in from_edges}
        to_edge_map = {_edge_key(e, to_id_map): e for e in to_edges}
        rel_changes: List[Dict[str, Any]] = []

        for k in sorted(set(to_edge_map.keys()) - set(from_edge_map.keys())):
            rel_changes.append({"change_type": "added", "relationship": k})
        for k in sorted(set(from_edge_map.keys()) - set(to_edge_map.keys())):
            rel_changes.append({"change_type": "removed", "relationship": k})

        # Build styled graph (base = to_graph), append removed entities as ghost nodes/edges
        styled_nodes = []
        for n in to_nodes:
            key = _node_key(n)
            status = "unchanged"
            if key in added_keys:
                status = "added"
            elif any(c.get("change_type") == "modified" and c.get("entity_name") == (n.get("data", {}) or {}).get("label") for c in changes):
                status = "modified"
            nn = dict(n)
            nn["data"] = {**(n.get("data") if isinstance(n.get("data"), dict) else {}), "diffStatus": status}
            styled_nodes.append(nn)

        for k in removed_keys:
            n = from_node_map[k]
            ghost = dict(n)
            ghost["id"] = f"removed-{n.get('id')}"
            ghost["data"] = {**(n.get("data") if isinstance(n.get("data"), dict) else {}), "diffStatus": "removed"}
            styled_nodes.append(ghost)

        styled_edges = []
        for e in to_edges:
            key = _edge_key(e, to_id_map)
            status = "added" if key not in from_edge_map else "unchanged"
            ee = dict(e)
            ee["data"] = {**(e.get("data") if isinstance(e.get("data"), dict) else {}), "diffStatus": status}
            styled_edges.append(ee)

        for k in sorted(set(from_edge_map.keys()) - set(to_edge_map.keys())):
            e = dict(from_edge_map[k])
            src = str(e.get("source") or "")
            tgt = str(e.get("target") or "")
            if src in from_id_map and _node_key(from_id_map[src]) in removed_keys:
                e["source"] = f"removed-{src}"
            if tgt in from_id_map and _node_key(from_id_map[tgt]) in removed_keys:
                e["target"] = f"removed-{tgt}"
            e["id"] = f"removed-{e.get('id') or k}"
            e["data"] = {**(e.get("data") if isinstance(e.get("data"), dict) else {}), "diffStatus": "removed"}
            styled_edges.append(e)

        summary = {
            "added": len([c for c in changes if c.get("change_type") == "added"]),
            "removed": len([c for c in changes if c.get("change_type") == "removed"]),
            "modified": len([c for c in changes if c.get("change_type") == "modified"]),
            "unchanged": len([c for c in changes if c.get("change_type") == "unchanged"]),
            "relationships_added": len([r for r in rel_changes if r.get("change_type") == "added"]),
            "relationships_removed": len([r for r in rel_changes if r.get("change_type") == "removed"]),
        }

        return {
            "summary": summary,
            "changes": changes,
            "relationships": rel_changes,
            "styled_graph": {
                "nodes": styled_nodes,
                "edges": styled_edges,
                "meta": {
                    "from_snapshot_id": from_snapshot_id,
                    "to_snapshot_id": to_snapshot_id,
                    "include_system_tables": include_system_tables,
                },
            },
        }
    except Exception as e:
        logger.debug(f"Error comparing snapshots: {str(e)}")
        return {
            "summary": {"added": 0, "removed": 0, "modified": 0, "unchanged": 0},
            "changes": [],
            "relationships": [],
            "styled_graph": {"nodes": [], "edges": []},
        }
