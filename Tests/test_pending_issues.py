#!/usr/bin/env python3
"""
test_pending_issues.py - one generic test for every task group in "SemaBridge final pending report".
No model, table or column names are hardcoded; unit tests use random names on every run.

MODES
  ddl   offline, any model: generated DDL (+ the model's .pbix for DAX lineage / column metadata)
        uv run python test_pending_issues.py ddl --pbix A.pbix --ddl A.sql [--pbix B.pbix --ddl B.sql ...]
              [--ddl2 A_run2.sql ...]   second run of the same models (naming stability, T9)
              [--log deploy.log]        deploy log (single-pass remediation, T2)
  unit  the repo's own translator / sanitizer, random names, results evaluated in DuckDB
        uv run python test_pending_issues.py unit --repo . [--seed 7]
  code  static checks on src/ (dead precompute path, decisions by SQL text, name-based date detection)
        uv run python test_pending_issues.py code --repo .
  all   ddl + unit + code (give the ddl arguments too)

CHECKS (report task -> check id)
  T1  Anchor metrics by DAX lineage (incl. NULL metrics; ALL/ISINSCOPE/RANKX/SELECTEDVALUE ignored)        ddl
  T2  Single-pass deploy, Snowflake error kept in COMMENT; every NULL has a reason                         ddl
  T3  Inactive-relationship alias tables declare the parent table's dimensions                            ddl
  T4  DAX BLANK in + and - (BLANK+5=5, BLANK-5=-5, BLANK+BLANK=BLANK)                                    unit
  T5  COL_ prefix only for real reserved words                                                            unit + ddl
  T6  Metrics built on window metrics carry the required dimensions in their COMMENT                     ddl
  T7  No false 'no date link' reason when the fact has an active date relationship                       ddl
  T8  Pre-deploy: REFERENCES target is PRIMARY KEY/UNIQUE; EXCLUDING names are declared dimensions       ddl
  T9  Stable names between runs (view, tables, dimensions, metrics)                                       ddl (--ddl2)
  T10 Helper columns / window helper metrics PRIVATE; real ALL_* columns stay visible                     ddl
  T11 Short reasons, no column catalogs, no quoted identifiers, single escaping in COMMENTs               ddl
  T12 No dead share-of-total precompute path; no decisions by searching SQL text                          code
  R*  regression guards (row counts THEN 1, time-intelligence date column from the DAX table)            unit
Exit code 1 if any FAIL.
"""
import argparse, os, random, re, string, sys, warnings
from collections import defaultdict
warnings.filterwarnings("ignore")

RESULTS = []          # (check, status, where, message)


def rec(check, status, where, msg):
    RESULTS.append((check, status, where, msg))
    print(f"  {status:4} {check:4} {where[:38]:38} {msg}")


_REP = {"%": "PCT", "$": "DOL", "&": "AND", "#": "NUM"}
norm = lambda s: re.sub(r"[^A-Z0-9]", "", "".join(_REP.get(c, c) for c in str(s)).upper())
U = lambda s: s.strip().strip('"').upper()
NULLRE = re.compile(r"^\(?\s*(CAST\s*\(\s*NULL\s+AS\s+\w+\s*\)|NULL)\s*\)?$", re.I)


# =================================================================== DDL parsing
def block(text, word):
    m = re.search(rf"\b{word}\s*\(", text, re.I)
    if not m:
        return ""
    d, i, q = 1, m.end(), False
    while i < len(text) and d:
        ch = text[i]
        if ch == "'":
            q = not q
        elif not q:
            d += {"(": 1, ")": -1}.get(ch, 0)
        i += 1
    return text[m.end():i - 1]


def split_top(s):
    out, d, cur, q = [], 0, [], False
    for ch in s:
        if ch == "'":
            q = not q
        if not q:
            d += {"(": 1, ")": -1}.get(ch, 0)
            if ch == "," and d == 0:
                out.append("".join(cur)); cur = []; continue
        cur.append(ch)
    out.append("".join(cur))
    return [x.strip() for x in out if x.strip()]


class DDL:
    def __init__(self, text):
        self.text = text
        m = re.search(r"semantic\s+view\s+([\w.\"$]+)", text, re.I)
        self.view = U(m.group(1).split(".")[-1]) if m else ""
        self.tables, self.keys = {}, defaultdict(set)
        for t in split_top(block(text, "tables")):
            m = re.match(r'(?:("?[\w$]+"?)\s+as\s+)?([\w."$]+)(.*)$', t, re.I | re.S)
            if not m:
                continue
            phys = U(m.group(2).split(".")[-1]); al = U(m.group(1) or phys)
            self.tables[al] = phys
            for km in re.finditer(r"(?:primary\s+key|unique)\s*\(([^)]*)\)", m.group(3), re.I):
                self.keys[al] |= {U(c) for c in km.group(1).split(",")}
        self.rels = []
        for r in split_top(block(text, "relationships")):
            m = re.search(r'("?\w+"?)\s*\(([^)]*)\)\s*references\s+("?\w+"?)\s*\(([^)]*)\)', r, re.I)
            if m:
                self.rels.append((U(m.group(1)), [U(c) for c in m.group(2).split(",")], U(m.group(3)), [U(c) for c in m.group(4).split(",")]))
        self.dims, self.private_facts = {}, set()
        for d in split_top(block(text, "dimensions")):
            m = re.match(r'(PRIVATE\s+)?("?\w+"?)\s*\.\s*("?\w+"?)\s+as\s+("?\w+"?)\s*\.\s*("?[\w$]+"?)', d, re.I)
            if m:
                self.dims[f"{U(m.group(2))}.{U(m.group(3))}"] = (U(m.group(5)), bool(m.group(1)))
        for f in split_top(block(text, "facts")):
            m = re.match(r'(PRIVATE\s+)?("?\w+"?)\s*\.\s*("?\w+"?)\s+as\s+("?\w+"?)\s*\.\s*("?[\w$]+"?)', f, re.I)
            if m and m.group(1):
                self.private_facts.add(f"{U(m.group(2))}.{U(m.group(5))}")
        self.metrics = {}
        for line in split_top(block(text, "metrics")):
            m = re.match(r'(PRIVATE\s+)?("?\w+"?)\s*\.\s*("?\w+"?)\s+(?:non\s+additive\s+by\s*\([^)]*\)\s+)?as\s+(.*)$', line, re.I | re.S)
            if not m:
                continue
            body = m.group(4)
            cm = re.search(r"\bCOMMENT\s*=\s*'((?:[^']|'')*)'", body, re.I)
            expr = re.sub(r"\s+COMMENT\s*=\s*'(?:[^']|'')*'", "", body, flags=re.I)
            expr = re.sub(r"\s+WITH\s+SYNONYMS\s*=?\s*\([^)]*\)", "", expr, flags=re.I).strip()
            self.metrics[U(m.group(3))] = dict(table=U(m.group(2)), expr=expr, comment=cm.group(1) if cm else "",
                                               raw_comment=cm.group(0) if cm else "", private=bool(m.group(1)))

    def is_null(self, name):
        return bool(NULLRE.match(self.metrics[name]["expr"]))

    def alias_for(self, dax_table):
        k = norm(dax_table)
        for al, phys in self.tables.items():
            if norm(al) == k or norm(re.sub(r"_ENRICHED$", "", phys)) == k:
                return al
        return None


def windows(expr):
    """(argument, excluded dims) for every aggregate OVER (PARTITION BY EXCLUDING ...)"""
    out, s = [], re.sub(r"'(?:[^']|'')*'", "''", expr)
    for m in re.finditer(r"\b(SUM|AVG|COUNT|MIN|MAX)\s*\(", s, re.I):
        d, i = 1, m.end()
        while i < len(s) and d:
            d += {"(": 1, ")": -1}.get(s[i], 0); i += 1
        w = re.match(r"\s*OVER\s*\(\s*PARTITION\s+BY\s+EXCLUDING\s+([^)]*)\)", s[i:], re.I)
        if w:
            dims = tuple(sorted(f"{U(a)}.{U(b)}" for a, b in re.findall(r'"?(\w+)"?\s*\.\s*"?(\w+)"?', w.group(1))))
            out.append((s[m.end():i - 1].strip(), dims))
    return out


# =================================================================== .pbix side
class PBIX:
    def __init__(self, path):
        from pbixray import PBIXRay
        px = PBIXRay(path)
        md = px.dax_measures
        self.measures = {r["Name"]: str(r["Expression"] or "") for _, r in md.iterrows()}
        self.home = {r["Name"]: r["TableName"] for _, r in md.iterrows()}
        sc = px.schema
        self.columns = defaultdict(dict)
        for _, r in sc.iterrows():
            t = str(r["TableName"])
            if t.startswith(("LocalDateTable_", "DateTableTemplate_")):
                continue
            self.columns[t][str(r["ColumnName"])] = str(r["PandasDataType"])
        self.rels = [(r["FromTableName"], r["FromColumnName"], r["ToTableName"], r["ToColumnName"], bool(r["IsActive"]))
                     for _, r in px.relationships.iterrows()]


def args_of(expr, start):
    d, i = 1, start
    while i < len(expr) and d:
        d += {"(": 1, ")": -1}.get(expr[i], 0); i += 1
    return expr[start:i - 1], i


def split_args(s):
    out, d, cur, q = [], 0, [], None
    for ch in s:
        if q:
            cur.append(ch); q = None if ch == q else q; continue
        if ch in "\"'":
            q = ch
        elif ch in "([":
            d += 1
        elif ch in ")]":
            d -= 1
        elif ch == "," and d == 0:
            out.append("".join(cur).strip()); cur = []; continue
        cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    return out


_TBL = r"\s*(?:'([^']+)'|([A-Za-z_][\w ]*?))"
IGNORE_FUNCS = ("ALL", "ALLEXCEPT", "ALLSELECTED", "REMOVEFILTERS", "ISINSCOPE", "SELECTEDVALUE", "VALUES",
                "HASONEVALUE", "ISFILTERED", "TREATAS", "USERELATIONSHIP")


def lineage(pb, measure, seen=None):
    """tables whose columns the measure aggregates (follows measure references)"""
    seen = seen or set()
    if measure in seen or measure not in pb.measures:
        return set()
    seen.add(measure)
    dax = re.sub(r"(--|//)[^\n]*", "", pb.measures[measure])
    for f in IGNORE_FUNCS:                       # filter / selector arguments never decide the anchor
        while True:
            m = re.search(rf"\b{f}\s*\(", dax, re.I)
            if not m:
                break
            _, end = args_of(dax, m.end())
            dax = dax[:m.start()] + "0" + dax[end:]
    # CALCULATE(<expr>, <filters>...) and FILTER(<table>, <predicate>): only <expr> / <table> decide the anchor;
    # aggregates inside filters (e.g. 'Date'[Period] = MAX('Date'[Period])) are conditions, not what is measured
    for f in ("CALCULATE", "CALCULATETABLE", "FILTER"):
        pos = 0
        while True:
            m = re.compile(rf"\b{f}\s*\(", re.I).search(dax, pos)
            if not m:
                break
            inner, end = args_of(dax, m.end())
            parts = split_args(inner)
            keep = parts[0] if parts else ""
            dax = dax[:m.end()] + keep + ")" + dax[end:]
            pos = m.end()
    # RANKX(<table>, <expr>): the table is the ranking set, the expression decides
    dax = re.sub(r"\bRANKX\s*\(\s*[^,]+,", "RANKX(0,", dax, flags=re.I)
    out = set()
    for m in re.finditer(r"\b(SUM|AVERAGE|MIN|MAX|COUNT|COUNTA|COUNTBLANK|DISTINCTCOUNT|MEDIAN)\s*\(", dax, re.I):
        a, _ = args_of(dax, m.end())
        t = re.match(r"\s*(?:'([^']+)'|([A-Za-z_][\w]*))\s*\[", a)
        if t:
            out.add(t.group(1) or t.group(2))
        elif re.match(r"\s*\[", a):
            out.add(pb.home[measure])
    for m in re.finditer(r"\b(COUNTROWS|SUMX|AVERAGEX|MINX|MAXX|COUNTX|COUNTAX|FILTER|KEEPFILTERS)\s*\(", dax, re.I):
        a, _ = args_of(dax, m.end())
        first = split_args(a)[0] if a.strip() else ""
        t = re.match(r"\s*(?:'([^']+)'|([A-Za-z_][\w ]*))\s*$", first)
        if t:
            out.add((t.group(1) or t.group(2)).strip())
    for m in re.finditer(r"(?<![\w'\]])\[([^\]]+)\]", dax):
        if m.group(1) in pb.measures:
            out |= lineage(pb, m.group(1), seen)
    return out


# =================================================================== ddl checks
def check_ddl(pbix_path, ddl_path, ddl2_path=None, log_path=None):
    d = DDL(open(ddl_path, encoding="utf-8", errors="ignore").read())
    pb = PBIX(pbix_path) if pbix_path else None
    where = os.path.basename(ddl_path)
    print(f"\n=== DDL {where}" + (f"  (pbix {os.path.basename(pbix_path)})" if pbix_path else ""))
    mnorm = {norm(k): k for k in d.metrics}

    # ---- T1 anchoring by lineage
    if pb:
        bad = 0
        for meas in pb.measures:
            key = mnorm.get(norm(meas))
            if not key:
                continue
            tabs = {d.alias_for(t) for t in lineage(pb, meas)} - {None}
            if tabs and d.metrics[key]["table"] not in tabs:
                bad += 1
                rec("T1", "FAIL", key, f"anchored on {d.metrics[key]['table']}, DAX lineage aggregates {sorted(tabs)}")
        if not bad:
            rec("T1", "PASS", where, "every metric anchored on a table its DAX aggregates")

    # ---- T1b granularity: a metric's table must reach (many -> one) every table whose columns it aggregates
    reach = defaultdict(set)
    for a, _, b, _ in d.rels:
        reach[a].add(b)
    def reachable(src):
        seen, st = {src}, [src]
        while st:
            n = st.pop()
            for x in reach.get(n, ()):
                if x not in seen:
                    seen.add(x); st.append(x)
        return seen
    for k, v in d.metrics.items():
        if d.is_null(k):
            continue
        agg = set()
        for m in re.finditer(r"\b(SUM|AVG|COUNT|MIN|MAX|MEDIAN)\s*\(", v["expr"], re.I):
            a, _ = args_of(v["expr"], m.end())
            agg |= {U(t) for t, c in re.findall(r'"?(\w+)"?\s*\.\s*"?(\w+)"?', a) if U(t) in d.tables and U(c) not in d.metrics}
        bad = sorted(t for t in agg if t not in reachable(v["table"]))
        if bad:
            rec("T1", "FAIL", f"{v['table']}.{k}", f"aggregates {bad}, not reachable from {v['table']} (Snowflake 010219: metric on the 'one' side)")

    # ---- T2 reasons + remediation
    no_reason = [k for k in d.metrics if d.is_null(k) and not d.metrics[k]["comment"].strip()]
    for k in no_reason:
        rec("T2", "FAIL", k, "NULL without a reason COMMENT")
    if log_path and os.path.exists(log_path):
        log = open(log_path, encoding="utf-8", errors="ignore").read()
        passes = re.findall(r"pass (\d+): fixing [^']*'([^']+)'", log)
        for n, ident in passes:
            name = U(ident.split(".")[-1])
            c = d.metrics.get(name, {}).get("comment", "")
            kept = bool(re.search(r"\b\d{6}\b|SQL compilation error|invalid", c, re.I))
            rec("T2", "FAIL", ident, f"remediated in deploy pass {n} (should be caught pre-deploy)"
                + ("" if kept else "; Snowflake error not kept in COMMENT"))
        if not passes:
            rec("T2", "PASS", where, "no deploy remediation passes in the log")
    if not no_reason:
        rec("T2", "PASS", where, "every NULL metric has a reason")

    # ---- T3 alias dimensions
    by_phys = defaultdict(list)
    for al, phys in d.tables.items():
        by_phys[phys].append(al)
    for phys, als in by_phys.items():
        if len(als) < 2:
            continue
        base = next((a for a in als if a == phys or d.alias_for(phys) == a), None) or min(als, key=len)
        base_cols = {c for k, (c, _) in d.dims.items() if k.startswith(base + ".")}
        for al in als:
            if al == base:
                continue
            cols = {c for k, (c, _) in d.dims.items() if k.startswith(al + ".")}
            missing = base_cols - cols
            if not cols:
                rec("T3", "FAIL", al, f"alias of {base} declares no dimensions (base has {len(base_cols)})")
            elif missing:
                rec("T3", "FAIL", al, f"missing {len(missing)} of {base}'s dimension columns, e.g. {sorted(missing)[:4]}")
            else:
                rec("T3", "PASS", al, f"declares all {len(base_cols)} dimension columns of {base}")

    # ---- T5 (ddl side) ordinary columns not renamed
    if pb:
        for t, cols in pb.columns.items():
            al = d.alias_for(t)
            if not al:
                continue
            for c in cols:
                s = re.sub(r"[^A-Z0-9]+", "_", "".join(_REP.get(ch, ch) for ch in c.upper())).strip("_")
                dim_names = {k.split(".", 1)[1] for k in d.dims if k.startswith(al + ".")}
                if f"COL_{s}" in dim_names and s not in SNOWFLAKE_RESERVED:
                    rec("T5", "WARN", f"{al}.COL_{s}", f"'{c}' renamed with COL_ but {s} is not a Snowflake reserved word "
                        "(changing an established name renames existing dimensions - decide per name, compare with baseline)")

    # ---- T6 required dimensions propagate
    wmet = {k: sorted({x for _, dims in windows(v["expr"]) for x in dims}) for k, v in d.metrics.items() if windows(v["expr"])}
    for k, v in d.metrics.items():
        req = set(wmet.get(k, []))
        for w, dims in wmet.items():
            if w != k and re.search(rf'\b"?{re.escape(w)}"?\b', v["expr"]):
                req |= set(dims)
        if not req:
            continue
        com = v["comment"].replace('"', "").upper()
        missing = [x for x in sorted(req) if x not in com]
        rec("T6", "FAIL" if missing else "PASS", k,
            f"COMMENT missing required dimension(s) {missing}" if missing else f"COMMENT names required {sorted(req)}")

    # ---- T7 no false 'no date link'
    if pb:
        def san(c):
            return re.sub(r"[^A-Z0-9]+", "_", "".join(_REP.get(ch, ch) for ch in c.upper())).strip("_")
        date_targets = set()
        for a, ac, b, bc in d.rels:                      # DDL relationships + column types from the .pbix
            for t, cols in pb.columns.items():
                if d.alias_for(t) != b:
                    continue
                for c, dt in cols.items():
                    if {san(c), f"COL_{san(c)}"} & set(bc) and "datetime" in dt:
                        date_targets.add((a, b))
        for k, v in d.metrics.items():
            if d.is_null(k) and re.search(r"date link", v["comment"], re.I):
                linked = [t for f, t in date_targets if f == v["table"]]
                rec("T7", "FAIL" if linked else "PASS", k,
                    f"says 'no date link' but {v['table']} has an active date relationship to {linked}" if linked else "no date relationship - reason plausible")

    # ---- T8 keys + EXCLUDING dims
    n_before = len(RESULTS)
    for a, ac, b, bc in d.rels:
        miss = [c for c in bc if c not in d.keys.get(b, set())]
        if miss:
            rec("T8", "FAIL", f"{a}->{b}", f"referenced {miss} is not PRIMARY KEY / UNIQUE on {b}")
    for k, v in d.metrics.items():
        for _, dims in windows(v["expr"]):
            for x in dims:
                if x not in d.dims:
                    rec("T8", "FAIL", k, f"EXCLUDING {x} is not a declared dimension")
    if not any(r[0] == "T8" and r[1] == "FAIL" for r in RESULTS[n_before:]):
        rec("T8", "PASS", where, "keys cover every REFERENCES target; EXCLUDING names declared")

    # ---- T9 naming stability
    if ddl2_path:
        d2 = DDL(open(ddl2_path, encoding="utf-8", errors="ignore").read())
        diffs = []
        if d.view != d2.view:
            diffs.append(f"view {d.view} -> {d2.view}")
        for label, a1, a2 in (("table", set(d.tables), set(d2.tables)), ("dimension", set(d.dims), set(d2.dims)),
                              ("metric", {f"{v['table']}.{k}" for k, v in d.metrics.items()},
                               {f"{v['table']}.{k}" for k, v in d2.metrics.items()})):
            if a1 != a2:
                diffs.append(f"{label}s: -{sorted(a1 - a2)[:3]} +{sorted(a2 - a1)[:3]}")
        rec("T9", "FAIL" if diffs else "PASS", where, "; ".join(diffs) if diffs else "names identical between runs")

    # ---- T10 helpers private, ALL_* real columns visible
    if pb:
        real = defaultdict(set)
        for t, cols in pb.columns.items():
            al = d.alias_for(t)
            if al:
                for c in cols:
                    s = re.sub(r"[^A-Z0-9]+", "_", "".join(_REP.get(ch, ch) for ch in c.upper())).strip("_")
                    real[al] |= {s, f"COL_{s}"}
        for key, (col, private) in d.dims.items():
            al = key.split(".")[0]
            if al in real and col not in real[al] and not private:
                rec("T10", "FAIL", key, "helper column (not in the Power BI table) is a public dimension - emit as PRIVATE")
        for t, cols in pb.columns.items():
            al = d.alias_for(t)
            for c in cols:
                s = re.sub(r"[^A-Z0-9]+", "_", c.upper()).strip("_")
                if al and s.startswith("ALL_") and not any(v[0] in (s, f"COL_{s}") for k, v in d.dims.items() if k.startswith(al + ".")):
                    rec("T10", "FAIL", f"{al}.{s}", "real column starting with ALL_ is hidden")
    for k, v in d.metrics.items():
        for arg, _ in windows(v["expr"]):
            m = re.fullmatch(r'(?:\(\s*)*"?(\w+)"?\s*\.\s*"?(\w+)"?(?:\s*\))*(?:\s*::\s*\w+)?', arg)
            if m and U(m.group(2)) in d.metrics and not d.metrics[U(m.group(2))]["private"]:
                helper = U(m.group(2))
                is_source_measure = pb is not None and norm(helper) in {norm(x) for x in pb.measures}
                if not is_source_measure:
                    rec("T10", "FAIL", helper, f"window helper metric used by {k} is public - emit as PRIVATE")

    # ---- T11 comment quality
    seen_bad = 0
    for k, v in d.metrics.items():
        c, raw = v["comment"], v["raw_comment"]
        probs = []
        if len(c) > 250:
            probs.append(f"{len(c)} chars")
        if re.search(r"available columns", c, re.I):
            probs.append("column catalog")
        if '"' in c:
            probs.append("quoted identifiers")
        if "''''" in raw:
            probs.append("escaped twice")
        if re.search(r"could not be translated to SQL at DDL-emission time", c, re.I):
            probs.append("generic reason")
        if probs:
            seen_bad += 1
            rec("T11", "FAIL", k, ", ".join(probs))
    if not seen_bad:
        rec("T11", "PASS", where, "all comments short and clean")


SNOWFLAKE_RESERVED = set("""ACCOUNT ALL ALTER AND ANY AS BETWEEN BY CASE CAST CHECK COLUMN CONNECT CONNECTION CONSTRAINT CREATE
CROSS CURRENT CURRENT_DATE CURRENT_TIME CURRENT_TIMESTAMP CURRENT_USER DATABASE DELETE DISTINCT DROP ELSE EXISTS FALSE
FOLLOWING FOR FROM FULL GRANT GROUP GSCLUSTER HAVING ILIKE IN INCREMENT INNER INSERT INTERSECT INTO IS ISSUE JOIN LATERAL
LEFT LIKE LOCALTIME LOCALTIMESTAMP MINUS NATURAL NOT NULL OF ON OR ORDER ORGANIZATION QUALIFY REGEXP REVOKE RIGHT RLIKE
ROW ROWS SAMPLE SCHEMA SELECT SET SOME START TABLE TABLESAMPLE THEN TO TRIGGER TRUE TRY_CAST UNION UNIQUE UPDATE USING
VALUES VIEW WHEN WHENEVER WHERE WINDOW WITH""".split())
# Verify this list against the current Snowflake 'Reserved & limited keywords' page before relying on it.


# =================================================================== unit checks (repo translator, random names)
def rnd(rng, prefix):
    return prefix + "".join(rng.choice(string.ascii_uppercase) for _ in range(5))


def to_duckdb(sql):
    def rew(s, name, fn):
        out, pos = [], 0
        for m in re.finditer(rf"\b{name}\s*\(", s, re.I):
            if m.start() < pos:
                continue
            inner, end = args_of(s, m.end())
            out.append(s[pos:m.start()]); out.append(fn(rew(inner, name, fn))); pos = end
        out.append(s[pos:])
        return "".join(out)
    sql = rew(sql, "IFF", lambda a: "(CASE WHEN {} THEN {} ELSE {} END)".format(*split_args(a)[:3]))
    sql = rew(sql, "TRY_TO_NUMBER", lambda a: f"TRY_CAST({a} AS DOUBLE)")
    sql = rew(sql, "DIV0NULL", lambda a: "(CASE WHEN COALESCE({1},0)=0 THEN 0 ELSE ({0})/({1}) END)".format(*split_args(a)[:2]))
    return re.sub(r"::\s*FLOAT\b", "::DOUBLE", sql, flags=re.I)


def unit(repo, seed):
    print(f"\n=== UNIT (repo {repo}, seed {seed})")
    sys.path.insert(0, os.path.join(os.path.abspath(repo), "src"))
    try:
        from semabridge.converter.dax_ast_parser import try_ast_translate
    except Exception as exc:
        rec("UNIT", "SKIP", "import", f"cannot import translator: {exc}"); return
    try:
        import duckdb
    except Exception:
        rec("UNIT", "SKIP", "duckdb", "pip install duckdb"); return
    rng = random.Random(seed)
    T, A = rnd(rng, "Fact_"), rnd(rng, "FACT")             # DAX table name, SQL alias
    X, F = rnd(rng, "Amt"), rnd(rng, "Flag")
    xs, fs = X.upper(), F.upper()
    m_a = f'SUM(CASE WHEN {A}."{fs}" = \'a\' THEN {A}."{xs}" END)'
    m_b = f'SUM(CASE WHEN {A}."{fs}" = \'b\' THEN {A}."{xs}" END)'
    msql = {"MA": m_a, "MB": m_b}
    con = duckdb.connect()

    def run(dax, data):
        sql = try_ast_translate(dax, table_alias=A, measure_sql_map=msql, known_measure_names=set(msql),
                                primary_table_name=T)
        if not sql:
            return "NO SQL", None
        con.execute(f'CREATE OR REPLACE TABLE {A} ("{fs}" VARCHAR, "{xs}" DOUBLE)')
        if data:
            con.executemany(f"INSERT INTO {A} VALUES (?, ?)", data)
        try:
            return sql, con.execute(f"SELECT {to_duckdb(sql)} FROM {A}").fetchone()[0]
        except Exception as exc:
            return sql, f"ERROR {str(exc).splitlines()[0][:80]}"

    # T4 BLANK semantics (DAX: BLANK + 5 = 5, BLANK - 5 = -5, BLANK + BLANK = BLANK)
    cases = [("[MA] + [MB]", [("a", 5.0)], 5.0), ("[MA] + [MB]", [("b", 5.0)], 5.0),
             ("[MA] - [MB]", [("b", 5.0)], -5.0), ("[MA] - [MB] + [MA]", [("b", 2.0)], -2.0),
             ("[MA] + [MB]", [("c", 1.0)], None)]
    for dax, data, exp in cases:
        sql, got = run(dax, data)
        ok = (got is None and exp is None) or (isinstance(got, (int, float)) and exp is not None and abs(got - exp) < 1e-9)
        rec("T4", "PASS" if ok else "FAIL", dax, f"rows {data} -> {got} (DAX {exp})" + ("" if ok else f" | SQL {sql[:90]}"))

    # T5 sanitizer: ordinary words unchanged, real reserved words handled
    try:
        from semabridge.utils.naming import sanitize_column
        for w in ("Sex", "Age", "Region", "Status", "Name", "Type", "Amount"):
            s = sanitize_column(w)
            rec("T5", "PASS" if s == w.upper() else "FAIL", w, f"-> {s}")
        for w in ("Select", "Order", "Group", "Table", "Where"):
            s = sanitize_column(w)
            rec("T5", "PASS" if s != w.upper() or s.startswith("COL_") else "WARN", w, f"-> {s} (reserved: must be prefixed or always quoted)")
    except Exception as exc:
        rec("T5", "SKIP", "sanitize_column", str(exc))

    # R1 regression: filtered row count counts rows
    sql = try_ast_translate(f"CALCULATE(COUNTROWS('{T}'), '{T}'[{F}] = \"a\")", table_alias=A, primary_table_name=T) or ""
    then_col = re.search(r"THEN\s+(?!1\b)[\w\".]+\s+END", sql)
    rec("R1", "FAIL" if (not sql or then_col) else "PASS", "COUNTROWS filter", sql[:100] or "no SQL")

    # R2 regression: time-intelligence date column taken from the DAX table (random calendar name)
    cal, cal_al = rnd(rng, "Cal_"), None
    cal_al = rnd(rng, "CAL")
    sql = try_ast_translate(f"CALCULATE(SUM('{T}'[{X}]), SAMEPERIODLASTYEAR('{cal}'[Day]))", table_alias=A,
                            primary_table_name=T, table_aliases={T: A, cal: cal_al}) or ""
    ok = bool(sql) and f'{cal_al}."DAY"'.upper() in sql.upper()
    rec("R2", "PASS" if ok else "FAIL", f"calendar '{cal}'", sql[:110] or "no SQL (calendar name not recognized)")


# =================================================================== code checks
def code(repo):
    print(f"\n=== CODE {repo}")
    src = os.path.join(repo, "src")
    pats = [
        ("T12", "FAIL", r"SEMABRIDGE_ENRICH_WINDOW_METRICS|_rerender_window_enriched_metrics|\bALL_COUNT\b", "old share-of-total precompute path still present"),
        ("T12", "WARN", r"""["']PARTITION BY EXCLUDING["']\s+in\b|["']OVER["']\s+in\b|in\s+\w*sql\w*\.upper\(\)""", "decision made by searching SQL text (carry is_window metadata instead)"),
        ("CAL", "FAIL", r"_DEFAULT_DATE_TABLE_NAMES|_is_date_table_name|date_table_names\s*[:=]\s*\[", "date tables detected by name (use metadata + the DAX table)"),
        ("HARD", "WARN", r"""["'](?:CASE_CREATED_ON|SALES_OWNER|TO_PCT|NET_SALES|COL_VALUE|STORE_STORE|YEAR_YEAR)["']""", "model-specific identifier literal"),
    ]
    found = defaultdict(int)
    for root, _, files in os.walk(src):
        for f in files:
            if not f.endswith(".py"):
                continue
            p = os.path.join(root, f)
            for i, line in enumerate(open(p, encoding="utf-8", errors="ignore"), 1):
                if line.lstrip().startswith("#"):
                    continue
                for cid, lvl, rx, msg in pats:
                    if re.search(rx, line) and found[(cid, msg)] < 8:
                        found[(cid, msg)] += 1
                        rec(cid, lvl, f"{os.path.relpath(p, repo)}:{i}", msg)
    for cid, lvl, rx, msg in pats:
        if not found[(cid, msg)]:
            rec(cid, "PASS", "src", f"no hits: {msg}")


# =================================================================== main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["ddl", "unit", "code", "all"])
    ap.add_argument("--pbix", action="append", default=[]); ap.add_argument("--ddl", action="append", default=[])
    ap.add_argument("--ddl2", action="append", default=[]); ap.add_argument("--log")
    ap.add_argument("--repo", default="."); ap.add_argument("--seed", type=int, default=random.randrange(10 ** 6))
    a = ap.parse_args()
    if a.mode in ("ddl", "all"):
        if not a.ddl:
            sys.exit("ddl mode needs --ddl (and --pbix for lineage / column checks)")
        for i, ddl in enumerate(a.ddl):
            check_ddl(a.pbix[i] if i < len(a.pbix) else None, ddl, a.ddl2[i] if i < len(a.ddl2) else None, a.log)
    if a.mode in ("unit", "all"):
        unit(a.repo, a.seed)
    if a.mode in ("code", "all"):
        code(a.repo)
    print("\n=== SUMMARY")
    by = defaultdict(lambda: defaultdict(int))
    for cid, st, _, _ in RESULTS:
        by[cid][st] += 1
    for cid in sorted(by):
        print(f"  {cid:5} " + "  ".join(f"{k}={v}" for k, v in sorted(by[cid].items())))
    fails = sum(1 for r in RESULTS if r[1] == "FAIL")
    print(f"\n{fails} FAIL" if fails else "\nALL PASS")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
