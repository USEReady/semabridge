#!/usr/bin/env python3
"""
run_phase_a.py - Phase A (read-only) for SemaBridge relationships, all models at once.

Steps
  1. Find model pairs: generated DDL (output/debug/debug_generated_sql_<model>.sql)
     + the uploaded .pbix (SemaBridge upload folder or --pbix-dir), or use --batch.
  2. Reconcile every source relationship with the DDL (relationship_inventory.reconcile).
  3. Save inventory.csv, summary.csv and baseline.json (or compare with an existing baseline).
  4. Trace every MISSING / key-less relationship through:
       - the runtime trace written by semabridge_trace.py (builder input, aliases,
         relationship_target_alias, emitted lines, report-only resolver decision), and
       - SemaBridge log lines mentioning those tables (Skipping relationship, aliases, drops...).
  5. Write phase_a_report.md with the root-cause stage for each problem relationship.

Nothing in SemaBridge or Snowflake is changed.

Usage (from the SemaBridge repo root)
  uv run python phase_a\\run_phase_a.py
  uv run python phase_a\\run_phase_a.py --pbix-dir "C:\\path\\to\\pbix" --logs logs --out phase_a_results
  uv run python phase_a\\run_phase_a.py --batch batch.csv
"""
import argparse
import csv
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from relationship_inventory import reconcile, is_clean, sb_table  # noqa: E402

LOG_PATTERNS = re.compile(
    r"Skipping relationship|relationship_target_alias|Including inactive|Unresolvable|"
    r"FINAL REL|drop|Dropped|not PK-safe|REFERENCES|relationship", re.IGNORECASE)


def norm(name):
    s = os.path.splitext(os.path.basename(name))[0]
    s = re.sub(r"^debug_generated_sql_", "", s, flags=re.I)
    s = re.sub(r"^[0-9a-f]{32}_", "", s, flags=re.I)          # SemaBridge upload prefix
    return re.sub(r"[^a-z0-9]", "", s.lower())


def discover(ddl_dir, pbix_dirs):
    pbix = {}
    for d in pbix_dirs:
        for p in glob.glob(os.path.join(d, "**", "*.pbix"), recursive=True):
            k = norm(p)
            if k not in pbix or os.path.getmtime(p) > os.path.getmtime(pbix[k]):
                pbix[k] = p                                    # newest upload wins
    jobs, unmatched = [], []
    for ddl in sorted(glob.glob(os.path.join(ddl_dir, "debug_generated_sql_*.sql"))):
        k = norm(ddl)
        if k in pbix:
            jobs.append((os.path.splitext(os.path.basename(ddl))[0].replace("debug_generated_sql_", ""), pbix[k], ddl))
        else:
            unmatched.append(ddl)
    return jobs, unmatched


def load_traces(trace_dir):
    path = os.path.join(trace_dir, "relationship_trace.jsonl")
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def trace_for(traces, src_rel):
    """Latest trace record for this source relationship (matched by sanitized dataset names)."""
    m = re.match(r"(.+?)\[(.+?)\] -> (.+?)\[(.+?)\]$", src_rel)
    if not m:
        return None, None
    f, fc, t, tc = m.groups()
    for entry in reversed(traces):
        names = set(entry.get("datasets", []))
        for r in entry.get("relationships", []):
            names.update([str(r.get("from_dataset")), str(r.get("to_dataset"))])
        ds = {sb_table(d): d for d in names}
        if sb_table(f) not in ds or sb_table(t) not in ds:
            continue
        for r in entry.get("relationships", []):
            if sb_table(str(r["from_dataset"])) == sb_table(f) and sb_table(str(r["to_dataset"])) == sb_table(t) \
                    and [c.lower() for c in r["from_columns"]] == [fc.lower()]:
                return entry, r
        return entry, None                 # model traced, relationship never reached the builder
    return None, None


def log_evidence(log_files, src_rel, limit=8):
    m = re.match(r"(.+?)\[(.+?)\] -> (.+?)\[(.+?)\]$", src_rel)
    if not m:
        return []
    f, fc, t, tc = m.groups()
    f_tok = {sb_table(f).lower(), f.lower()}
    t_tok = {sb_table(t).lower(), t.lower()}
    c_tok = {fc.lower(), tc.lower(), fc.lower().replace(" ", ""), tc.lower().replace(" ", "")}
    hits = []
    for lf in log_files:
        try:
            with open(lf, encoding="utf-8", errors="ignore") as fh:
                for i, line in enumerate(fh, 1):
                    if not LOG_PATTERNS.search(line):
                        continue
                    low = line.lower()
                    if not any(tok in low for tok in t_tok):
                        continue
                    rest = low
                    for tok in sorted(t_tok, key=len, reverse=True):   # 'Product' inside 'Associated Product'
                        rest = rest.replace(tok, " ")
                    if any(tok in rest for tok in f_tok) and any(tok in low for tok in c_tok):
                        hits.append(f"{os.path.basename(lf)}:{i}: {line.strip()[:220]}")
        except Exception:
            continue
    return hits[-limit:]


def classify(row, entry, rec):
    if row["outcome"] == "MISSING":
        if entry is None:
            return "NO TRACE", "Run a dry run through semabridge_trace.py for this model, then re-run."
        if rec is None:
            return ("DROPPED BEFORE BUILDER",
                    "Relationship never reached _build_relationships: lost in extraction / OSI->SML / "
                    "relationship normalisation. Check the SML model.yaml relationships list.")
        if not rec.get("emitted"):
            return ("DROPPED IN BUILDER",
                    f"Reached _build_relationships but no line emitted. from_alias={rec.get('from_alias')}, "
                    f"to_alias={rec.get('to_alias')}, target_alias_remap={rec.get('target_alias_remap')}, "
                    f"resolved cols {rec.get('resolved_from_columns')}->{rec.get('resolved_to_columns')}. "
                    f"Resolver: {rec.get('plan_decision')} ({rec.get('plan_reason')})")
        return ("DROPPED AFTER BUILDER",
                f"Builder emitted: {rec.get('emitted_line')} - removed later (sanitiser / remediation / "
                f"pre-deploy check). Compare with the saved DDL.")
    if row["key_on_referenced_side"] == "NO":
        why = f" Resolver: {rec.get('plan_decision')} ({rec.get('plan_reason')})" if rec else ""
        return "EMITTED WITHOUT KEY", f"{row['note']}.{why}"
    return "", ""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ddl-dir", default=os.path.join("output", "debug"))
    ap.add_argument("--pbix-dir", action="append", default=[],
                    help="folder(s) with .pbix files (default: SemaBridge upload folder)")
    ap.add_argument("--batch", help="CSV model,pbix,ddl instead of auto-discovery")
    ap.add_argument("--logs", default="logs", help="SemaBridge log folder")
    ap.add_argument("--trace-dir", default=os.path.join("output", "trace"))
    ap.add_argument("--out", default="phase_a_results")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    if args.batch:
        with open(args.batch, newline="", encoding="utf-8") as fh:
            jobs = [(r["model"], r["pbix"], r["ddl"]) for r in csv.DictReader(fh)]
        unmatched = []
    else:
        pbix_dirs = args.pbix_dir or [os.path.join(os.environ.get("LOCALAPPDATA", ""), "Temp", "semabridge", "uploads")]
        jobs, unmatched = discover(args.ddl_dir, pbix_dirs)
    if not jobs:
        sys.exit("No model pairs found. Pass --pbix-dir / --ddl-dir, or --batch.")

    traces = load_traces(args.trace_dir)
    log_files = sorted(glob.glob(os.path.join(args.logs, "**", "*.log"), recursive=True))
    inv_rows, summaries, results, problems = [], [], {}, []

    for model, pbix, ddl in jobs:
        try:
            rows, extra, summary = reconcile(pbix, open(ddl, encoding="utf-8").read())
        except Exception as exc:
            summaries.append({"model": model, "error": str(exc)})
            continue
        results[model] = rows
        summaries.append({"model": model, **summary, "clean": is_clean(rows)})
        for r in rows:
            entry, rec = trace_for(traces, r["source_relationship"])
            stage, detail = classify(r, entry, rec)
            row = {"model": model, **r, "root_cause_stage": stage, "root_cause_detail": detail}
            inv_rows.append(row)
            if stage:
                problems.append({**row, "log_evidence": log_evidence(log_files, r["source_relationship"])})
        for d in extra:
            inv_rows.append({"model": model, "source_relationship": "(not in .pbix)", "outcome": "EXTRA",
                             "ddl_relationship": f"{d['name']}: {d['from']} -> {d['to']}"})

    # ---- files -------------------------------------------------------------------------------------
    fields = ["model", "source_relationship", "active", "cardinality", "cross_filter", "outcome",
              "ddl_relationship", "key_on_referenced_side", "note", "root_cause_stage", "root_cause_detail"]
    with open(os.path.join(args.out, "inventory.csv"), "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore"); wr.writeheader(); wr.writerows(inv_rows)
    keys = sorted({k for s in summaries for k in s}, key=lambda k: (k != "model", k))
    with open(os.path.join(args.out, "summary.csv"), "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=keys); wr.writeheader(); wr.writerows(summaries)

    base_path = os.path.join(args.out, "baseline.json")
    changes = []
    if os.path.exists(base_path):
        base = json.load(open(base_path, encoding="utf-8"))
        for model, rows in results.items():
            old = {r["source_relationship"]: r for r in base.get(model, [])}
            for r in rows:
                o = old.get(r["source_relationship"])
                if o and (o["outcome"], o["key_on_referenced_side"]) != (r["outcome"], r["key_on_referenced_side"]):
                    changes.append(f"{model}: {r['source_relationship']}: "
                                   f"{o['outcome']}/{o['key_on_referenced_side'] or '-'} -> "
                                   f"{r['outcome']}/{r['key_on_referenced_side'] or '-'}")
        json.dump(results, open(os.path.join(args.out, "latest.json"), "w", encoding="utf-8"), indent=2)
    else:
        json.dump(results, open(base_path, "w", encoding="utf-8"), indent=2)

    # ---- report ------------------------------------------------------------------------------------
    rep = ["# Phase A relationship report", "",
           f"Models: {len(jobs)} | traces loaded: {len(traces)} | log files: {len(log_files)}", "",
           "## Summary", "", "| Model | Source | DDL | Emitted | Missing | Key missing | Clean |",
           "| --- | --- | --- | --- | --- | --- | --- |"]
    for s in summaries:
        if "error" in s:
            rep.append(f"| {s['model']} | ERROR: {s['error']} | | | | | |")
        else:
            em = s.get("EMITTED", 0) + s.get("EMITTED_REVERSED", 0)
            rep.append(f"| {s['model']} | {s['source']} | {s['ddl_relationships']} | {em} | {s.get('MISSING', 0)} "
                       f"| {s['key_missing']} | {'yes' if s['clean'] else 'NO'} |")
    rep += ["", "## Problem relationships and root-cause stage", ""]
    if not problems:
        rep.append("None.")
    for p in problems:
        rep += [f"### {p['model']}: {p['source_relationship']}", "",
                f"- Source: active={p['active']}, cardinality={p['cardinality']}, outcome={p['outcome']}",
                f"- Stage: **{p['root_cause_stage']}**",
                f"- Detail: {p['root_cause_detail']}"]
        if p["log_evidence"]:
            rep += ["- Log evidence:", "", "```"] + p["log_evidence"] + ["```"]
        rep.append("")
    if changes:
        rep += ["## Changes vs baseline", ""] + [f"- {c}" for c in changes] + [""]
    if unmatched:
        rep += ["## DDL files without a matching .pbix", ""] + [f"- {u}" for u in unmatched]
    with open(os.path.join(args.out, "phase_a_report.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(rep) + "\n")

    print("\n".join(rep))
    print(f"\nWritten to {os.path.abspath(args.out)}: inventory.csv, summary.csv, "
          f"{'latest.json (baseline kept)' if changes or os.path.exists(os.path.join(args.out, 'latest.json')) else 'baseline.json'}, phase_a_report.md")


if __name__ == "__main__":
    main()
