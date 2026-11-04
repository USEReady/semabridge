#!/usr/bin/env python3
"""
validate_semantic_view.py - check a Snowflake semantic view's keys and relationships.

Catches the class of bug found in Sales & Returns (a relationship that REFERENCES a
table whose primary key was never declared), plus related problems, for ANY model.
No table names are hardcoded.

STATIC checks (from the DDL text only - works offline):
  S1  every RELATIONSHIP's referenced table has a PRIMARY KEY (or UNIQUE key) declared
  S2  the referenced column(s) match that PRIMARY KEY or a declared UNIQUE key
  S3  relationship column counts match (FK cols vs PK cols)
  S4  no self-references
  S5  no two relationships between the same pair of tables (ambiguous path)
  S6  no relationship cycles

LIVE checks (need a Snowflake connection):
  L1  declared PRIMARY KEY / UNIQUE columns are really unique and non-NULL
  L2  FK / PK columns exist in the physical tables
  L3  orphan FK values (FK value with no matching PK row)       [warning]
  L4  FK column is also unique  -> relationship is effectively 1:1 [info]
  L5  enriched views have the same row count as their base table (no fan-out)
  L6  optional --expect file: custom SQL assertions (e.g. QA totals)

Usage
  # offline: check a DDL you saved from a dry run or from GET_DDL
  python validate_semantic_view.py --ddl-file semantic_view.sql

  # live: fetch the DDL and run all checks
  python validate_semantic_view.py --view SEMABRIDGE.SALES_AND_RETURNS.<VIEW_NAME>
  python validate_semantic_view.py --view SEMABRIDGE.SALES_AND_RETURNS.<VIEW_NAME> --expect sales_returns_expect.json

Exit code: 0 = no failures, 1 = at least one FAIL.
"""
import argparse
import json
import os
import re
import sys
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
IDENT = r'(?:"[^"]+"|[A-Za-z_][\w$]*)'


# ----------------------------------------------------------------------------- parsing
def _unq(s):
    s = s.strip()
    return s[1:-1].replace('""', '"') if s.startswith('"') and s.endswith('"') else s.upper()


def _clause(ddl, name):
    """Return the text inside `name ( ... )` with balanced parentheses."""
    m = re.search(rf"\b{name}\s*\(", ddl, re.IGNORECASE)
    if not m:
        return ""
    depth, i = 1, m.end()
    while i < len(ddl) and depth:
        depth += {"(": 1, ")": -1}.get(ddl[i], 0)
        i += 1
    return ddl[m.end(): i - 1]


def _split_top(text):
    parts, depth, cur = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def _cols(text):
    return [_unq(c) for c in _split_top(text)]


def parse_ddl(ddl):
    """-> tables {alias: {"fqn":..., "pk":[...]}}, relationships [{name, from, from_cols, to, to_cols}]"""
    tables = {}
    for item in _split_top(_clause(ddl, "tables")):
        item = re.sub(r"\s+comment\s*=\s*'(?:[^']|'')*'", "", item, flags=re.I)
        item = re.sub(r"\s+with\s+synonyms\s*=\s*\([^)]*\)", "", item, flags=re.I)
        m = re.match(
            rf"^\s*(?:({IDENT})\s+as\s+)?((?:{IDENT}\s*\.\s*)*{IDENT})"
            rf"(?:\s+primary\s+key\s*\(([^)]*)\))?",
            item,
            re.I,
        )
        if not m:
            continue
        fqn = ".".join(_unq(p) for p in re.split(r"\s*\.\s*(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)", m.group(2)))
        alias = _unq(m.group(1)) if m.group(1) else fqn.split(".")[-1]
        uniques = [_cols(u) for u in re.findall(r"\bunique\s*\(([^)]*)\)", item, re.I)]
        tables[alias] = {"fqn": fqn, "pk": _cols(m.group(3)) if m.group(3) else [], "unique": uniques}
    rels = []
    for item in _split_top(_clause(ddl, "relationships")):
        m = re.match(
            rf"^\s*(?:({IDENT})\s+as\s+)?({IDENT})\s*\(([^)]*)\)\s+references\s+({IDENT})\s*(?:\(([^)]*)\))?",
            item,
            re.I,
        )
        if m:
            rels.append({
                "name": _unq(m.group(1)) if m.group(1) else "(unnamed)",
                "from": _unq(m.group(2)),
                "from_cols": _cols(m.group(3)),
                "to": _unq(m.group(4)),
                "to_cols": _cols(m.group(5)) if m.group(5) else [],
            })
    return tables, rels


# ----------------------------------------------------------------------------- reporting
class Report:
    def __init__(self):
        self.rows = []

    def add(self, level, check, subject, msg):
        self.rows.append((level, check, subject, msg))

    def print(self):
        order = {"FAIL": 0, "WARN": 1, "INFO": 2, "PASS": 3}
        for lvl, chk, subj, msg in sorted(self.rows, key=lambda r: (order[r[0]], r[1])):
            print(f"  {lvl:4}  {chk:3}  {subj:45.45}  {msg}")
        n = defaultdict(int)
        for r in self.rows:
            n[r[0]] += 1
        print(f"\n  PASS {n['PASS']}   FAIL {n['FAIL']}   WARN {n['WARN']}   INFO {n['INFO']}")
        return n["FAIL"]


# ----------------------------------------------------------------------------- static checks
def static_checks(tables, rels, rep):
    pairs = defaultdict(list)
    graph = defaultdict(set)
    for r in rels:
        subj = f"{r['name']}: {r['from']} -> {r['to']}"
        if r["to"] not in tables:
            rep.add("FAIL", "S1", subj, f"referenced table '{r['to']}' is not in TABLES")
            continue
        pk = tables[r["to"]]["pk"]
        keys = ([pk] if pk else []) + tables[r["to"]].get("unique", [])
        to_cols = r["to_cols"] or pk
        up = [c.upper() for c in to_cols]
        if not keys:
            rep.add(
                "FAIL",
                "S1",
                subj,
                f"'{r['to']}' is referenced but has NO PRIMARY KEY declared "
                f"(Snowflake: 'has no primary key')",
            )
        elif not any(up == [c.upper() for c in k] for k in keys):
            rep.add(
                "FAIL",
                "S2",
                subj,
                f"references {to_cols} but declared keys on '{r['to']}' are {keys} "
                f"(declare UNIQUE ({', '.join(to_cols)}) or reference the PRIMARY KEY)",
            )
        else:
            rep.add("PASS", "S1", subj, f"key {to_cols} declared on referenced side")
        if len(r["from_cols"]) != len(to_cols):
            rep.add("FAIL", "S3", subj, f"{len(r['from_cols'])} FK column(s) vs {len(to_cols)} PK column(s)")
        if r["from"] == r["to"]:
            rep.add("FAIL", "S4", subj, "self-referencing relationship")
        pairs[frozenset((r["from"], r["to"]))].append(r["name"])
        graph[r["from"]].add(r["to"])
    for pair, names in pairs.items():
        if len(names) > 1:
            rep.add(
                "FAIL",
                "S5",
                " / ".join(sorted(pair)),
                f"{len(names)} relationships between the same tables ({', '.join(names)}) - ambiguous path",
            )
    # S6 cycles (DFS)
    state = {}

    def dfs(n, path):
        state[n] = 1
        for m in graph.get(n, ()):
            if state.get(m) == 1:
                rep.add("FAIL", "S6", " -> ".join(path + [m]), "relationship cycle")
            elif not state.get(m):
                dfs(m, path + [m])
        state[n] = 2

    for n in list(graph):
        if not state.get(n):
            dfs(n, [n])


# ----------------------------------------------------------------------------- live checks
def connect(args):
    import snowflake.connector

    path = args.config or os.path.join(SCRIPT_DIR, "snowflake_config.json")
    cfg = json.load(open(path)) if os.path.exists(path) else {}
    p = {k: cfg[k] for k in ("account", "user", "warehouse", "database", "role") if cfg.get(k)}
    key_path = cfg.get("private_key_path")
    if key_path and os.path.exists(key_path):
        from cryptography.hazmat.primitives import serialization

        pw = os.environ.get("SF_PRIVATE_KEY_PASSPHRASE")
        key = serialization.load_pem_private_key(open(key_path, "rb").read(), password=pw.encode() if pw else None)
        p["private_key"] = key.private_bytes(
            serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    else:
        p["authenticator"] = cfg.get("authenticator", "externalbrowser")
    return snowflake.connector.connect(**p)


def q(fqn):
    return ".".join(f'"{p}"' for p in fqn.split("."))


def scalar(cur, sql):
    cur.execute(sql)
    return cur.fetchone()


def live_checks(cur, tables, rels, rep):
    cols_cache = {}

    def columns(fqn):
        if fqn not in cols_cache:
            try:
                cur.execute(f"DESCRIBE TABLE {q(fqn)}")
                cols_cache[fqn] = {r[0].upper() for r in cur.fetchall()}
            except Exception:
                try:
                    cur.execute(f"DESCRIBE VIEW {q(fqn)}")
                    cols_cache[fqn] = {r[0].upper() for r in cur.fetchall()}
                except Exception as exc:
                    cols_cache[fqn] = None
                    rep.add("FAIL", "L2", fqn, f"cannot describe object: {exc}")
        return cols_cache[fqn]

    # L1 + L2 on every declared primary key
    for alias, t in tables.items():
        for key in ([t["pk"]] if t["pk"] else []) + t.get("unique", []):
            have = columns(t["fqn"])
            if have is None:
                break
            missing = [c for c in key if c.upper() not in have]
            if missing:
                rep.add("FAIL", "L2", alias, f"key column(s) {missing} not in {t['fqn']}")
                continue
            cl = ", ".join(f'"{c}"' for c in key)
            nn = " AND ".join(f'"{c}" IS NOT NULL' for c in key)
            total, distinct, nulls = scalar(
                cur,
                f"SELECT COUNT(*), COUNT(DISTINCT {cl}), "
                f"COUNT_IF(NOT ({nn})) FROM {q(t['fqn'])}",
            )
            if total != distinct or nulls:
                rep.add(
                    "FAIL",
                    "L1",
                    alias,
                    f"key {key} NOT unique: {total:,} rows, {distinct:,} distinct, "
                    f"{nulls:,} NULL -> joins will inflate totals",
                )
            else:
                rep.add("PASS", "L1", alias, f"key {key} unique ({total:,} rows)")

    # L2-L4 per relationship
    for r in rels:
        if r["from"] not in tables or r["to"] not in tables:
            continue
        subj = f"{r['name']}: {r['from']} -> {r['to']}"
        f_fqn, t_fqn = tables[r["from"]]["fqn"], tables[r["to"]]["fqn"]
        to_cols = r["to_cols"] or tables[r["to"]]["pk"]
        fh, th = columns(f_fqn), columns(t_fqn)
        if fh is None or th is None:
            continue
        miss = [c for c in r["from_cols"] if c.upper() not in fh] + [c for c in to_cols if c.upper() not in th]
        if miss:
            rep.add("FAIL", "L2", subj, f"column(s) not found: {miss}")
            continue
        on = " AND ".join(f'f."{a}" = t."{b}"' for a, b in zip(r["from_cols"], to_cols))
        fnn = " AND ".join(f'f."{a}" IS NOT NULL' for a in r["from_cols"])
        orphans = scalar(
            cur,
            f"SELECT COUNT(*) FROM {q(f_fqn)} f LEFT JOIN {q(t_fqn)} t ON {on} "
            f'WHERE {fnn} AND t."{to_cols[0]}" IS NULL',
        )[0]
        if orphans:
            rep.add("WARN", "L3", subj, f"{orphans:,} FK value(s) with no matching row in {r['to']}")
        fl = ", ".join(f'"{c}"' for c in r["from_cols"])
        tot, dist = scalar(cur, f"SELECT COUNT(*), COUNT(DISTINCT {fl}) FROM {q(f_fqn)}")
        if tot and tot == dist:
            rep.add(
                "INFO",
                "L4",
                subj,
                "FK is also unique -> effectively ONE-TO-ONE "
                "(check the direction: the fact table should be the FROM side)",
            )

    # L5 enriched views vs base table
    for alias, t in tables.items():
        name = t["fqn"].split(".")[-1]
        if not name.upper().endswith("_ENRICHED"):
            continue
        base = ".".join(t["fqn"].split(".")[:-1] + [name[:-len("_ENRICHED")]])
        try:
            v = scalar(cur, f"SELECT COUNT(*) FROM {q(t['fqn'])}")[0]
            b = scalar(cur, f"SELECT COUNT(*) FROM {q(base)}")[0]
        except Exception as exc:
            rep.add("WARN", "L5", alias, f"could not compare with base table: {exc}")
            continue
        if v == b:
            rep.add("PASS", "L5", alias, f"enriched view rows = base rows ({v:,})")
        else:
            rep.add("FAIL", "L5", alias, f"enriched view {v:,} rows vs base {b:,} rows (fan-out or lost rows)")


def expect_checks(cur, path, rep):
    """JSON list of {"name":..., "sql":..., "expect": <value or [values]>, "tolerance": 0.01}"""
    for item in json.load(open(path)):
        try:
            cur.execute(item["sql"])
            got = list(cur.fetchone() or [])
        except Exception as exc:
            rep.add("FAIL", "L6", item["name"], f"query failed: {exc}")
            continue
        exp = item["expect"] if isinstance(item["expect"], list) else [item["expect"]]
        tol = float(item.get("tolerance", 0.01))
        ok = len(got) >= len(exp) and all(
            (abs(float(g) - float(e)) <= tol if isinstance(e, (int, float)) else str(g) == str(e))
            for g, e in zip(got, exp)
        )
        rep.add("PASS" if ok else "FAIL", "L6", item["name"], f"got {got[:len(exp)]}, expected {exp}")


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--ddl-file", help="semantic view DDL saved to a file (offline, static checks only)")
    src.add_argument("--view", help="fully qualified semantic view name (live)")
    ap.add_argument("--expect", help="JSON file with extra SQL assertions (live only)")
    ap.add_argument("--config", help="snowflake_config.json (default: next to this script)")
    args = ap.parse_args()

    rep, cur = Report(), None
    if args.ddl_file:
        ddl = open(args.ddl_file, encoding="utf-8").read()
    else:
        conn = connect(args)
        cur = conn.cursor()
        ddl = scalar(cur, f"SELECT GET_DDL('SEMANTIC_VIEW', '{args.view}')")[0]

    tables, rels = parse_ddl(ddl)
    print(f"\nParsed {len(tables)} tables, {len(rels)} relationships\n")
    if not tables:
        print("  Could not parse a TABLES clause - is this a CREATE SEMANTIC VIEW statement?")
        sys.exit(1)
    static_checks(tables, rels, rep)
    if cur:
        live_checks(cur, tables, rels, rep)
        if args.expect:
            expect_checks(cur, args.expect, rep)
    failures = rep.print()
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
