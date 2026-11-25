"""A readable Markdown rendering of a plan (PlannerResult.as_dict())."""

from __future__ import annotations


def _cell(text) -> str:
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


def render(plan: dict, title: str) -> str:
    s = plan["summary"]
    st = plan["settings"]
    out = [f"# Databricks metric-view plan: {title}", ""]
    out.append(f"Mode **{st['calculate_mode']}** · view mode {st['view_mode']} · capabilities "
               f"{', '.join(st['capabilities']) or 'none'}")
    out.append("")
    out.append(f"**{s['views']} views · {s['measures_placed']} measures placed "
               f"({s['approximate']} approximate, {s['composed']} composed) · {s['measures_left_out']} left out**")
    out.append("")
    for v in plan["views"]:
        out.append(f"## {v['name']}  (source: {v['source_dataset']}, YAML {v['version']}, {v['readiness']})")
        out.append("")
        if v["joins"]:
            out.append("| Join | Table | On | Cardinality | Why | Readiness |")
            out.append("|---|---|---|---|---|---|")
            for j in v["joins"]:
                on = j.get("on_expr") or ", ".join(f"{j['parent']}.{a} = {b}" for a, b in j["on"])
                out.append(f"| {j['name']} | {j['dataset']} | `` {_cell(on)} `` | {j['cardinality']} | {j['reason']} | {j['readiness']} |")
            out.append("")
        out.append(f"**Fields ({len(v['fields'])}):** " + ", ".join(
            f"{f['name']}{'*' if f['source_verification'] == 'unverified' else ''}" for f in v["fields"]))
        out.append("")
        out.append("| Measure | Power BI name | Placement | Status | Readiness | Expression |")
        out.append("|---|---|---|---|---|---|")
        for m in v["measures"]:
            out.append(f"| {m['name']} | {_cell(m['source_name'])} | {m['placement']} | {m['semantic_status']}"
                       f"{' / unverified' if m['source_verification'] == 'unverified' else ''} | {m['readiness']} | "
                       f"`` {_cell(m['expr'])} `` |")
        warned = [m for m in v["measures"] if m["warnings"]]
        if warned:
            out.append("")
            out.append("Warnings:")
            for m in warned:
                out.append(f"- **{m['name']}**: " + "; ".join(m["warnings"]))
        if v["notes"]:
            out.append("")
            out.append("Notes:")
            out += [f"- {n}" for n in v["notes"]]
        out.append("")
        out.append(f"Checks for 3.9: {len(v['checks'])} (" + ", ".join(sorted({c['kind'] for c in v['checks']})) + ")")
        out.append("")
    if plan["left_out"]:
        out.append("## Left out")
        out.append("")
        out.append("| Measure | Reason | Detail | Root cause |")
        out.append("|---|---|---|---|")
        for lo in plan["left_out"]:
            out.append(f"| {_cell(lo['measure'])} | {lo['reason']} | {_cell(lo['detail'])[:160]} | {_cell(lo['root_cause'])} |")
        out.append("")
    if plan["renames"]:
        out.append("## Renames")
        out.append("")
        out += [f"- {r['view']}: {r['kind']} {r['original']} → {r['final']} ({r['why']})" for r in plan["renames"]]
        out.append("")
    if plan["notes"]:
        out.append("## Notes")
        out.append("")
        out += [f"- {n}" for n in plan["notes"]]
    return "\n".join(out) + "\n"
