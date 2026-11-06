"""Shared helpers for the issue tests: parse a generated semantic-view DDL and (optionally) a .pbix.

Every test file exposes check(sv, pbix, args) -> Result and can be run on its own:
    python t05_dateadd_amount.py --ddl view.sql --pbix model.pbix
or all together with run_all.py.
Nothing here depends on table or column names of a specific model.
"""
import argparse, re, sys
from dataclasses import dataclass, field

# ---------------------------------------------------------------- results
class Result:
    def __init__(self, name):
        self.name, self.items = name, []
    def add(self, status, msg): self.items.append((status, msg))
    def fail(self, m): self.add("FAIL", m)
    def warn(self, m): self.add("WARN", m)
    def ok(self, m): self.add("PASS", m)
    def info(self, m): self.add("INFO", m)
    @property
    def status(self):
        s = {x for x, _ in self.items}
        return "FAIL" if "FAIL" in s else "WARN" if "WARN" in s else "PASS" if "PASS" in s else "INFO"
    def show(self, limit=40):
        print(f"\n=== {self.name}: {self.status}")
        for st, m in self.items[:limit]:
            print(f"  {st:4}  {m}")
        if len(self.items) > limit:
            print(f"  ... {len(self.items) - limit} more")

# ---------------------------------------------------------------- text helpers
def norm(s):
    """Name normalisation close to SemaBridge's sanitiser (for matching only)."""
    s = str(s or "").replace("%", " PCT ").replace("#", " NUM ").replace("$", " DOL ").replace("<=", " LTE ").replace(">=", " GTE ").replace("<", " LT ").replace(">", " GT ")
    s = re.sub(r"[^0-9A-Za-z]+", "_", s).strip("_").upper()
    return re.sub(r"_+", "_", s)

def squash(s):
    return re.sub(r"[^0-9A-Z]", "", norm(s))

def split_top(text, sep=","):
    """Split on sep at paren depth 0, ignoring quoted text."""
    out, depth, cur, q = [], 0, [], None
    i = 0
    while i < len(text):
        c = text[i]
        if q:
            cur.append(c)
            if c == q:
                if i + 1 < len(text) and text[i + 1] == q:   # escaped quote '' or ""
                    cur.append(text[i + 1]); i += 1
                else:
                    q = None
        elif c in "'\"":
            q = c; cur.append(c)
        elif c == "(":
            depth += 1; cur.append(c)
        elif c == ")":
            depth -= 1; cur.append(c)
        elif c == sep and depth == 0:
            out.append("".join(cur)); cur = []
        else:
            cur.append(c)
        i += 1
    if "".join(cur).strip():
        out.append("".join(cur))
    return [x.strip() for x in out if x.strip()]

def section(ddl, name):
    """Body of TABLES ( ... ), RELATIONSHIPS ( ... ) etc."""
    m = re.search(rf"\b{name}\s*\(", ddl, re.I)
    if not m:
        return ""
    i, depth, q = m.end(), 1, None
    start = i
    while i < len(ddl) and depth:
        c = ddl[i]
        if q:
            if c == q:
                if i + 1 < len(ddl) and ddl[i + 1] == q: i += 1
                else: q = None
        elif c in "'\"": q = c
        elif c == "(": depth += 1
        elif c == ")": depth -= 1
        i += 1
    return ddl[start:i - 1]

def unq(s):
    return str(s or "").strip().strip('"').upper()

# ---------------------------------------------------------------- DDL model
@dataclass
class Metric:
    table: str; name: str; expr: str; comment: str = ""; private: bool = False; non_additive: str = ""
    @property
    def is_null(self): return bool(re.fullmatch(r"\(?\s*CAST\s*\(\s*NULL\s+AS\s+\w+\s*\)\s*\)?", self.expr.strip(), re.I))

@dataclass
class Dim:
    table: str; name: str; expr: str; private: bool = False
    @property
    def column(self):
        m = re.search(r'\.\s*"?([^".\s]+)"?\s*$', self.expr)
        return unq(m.group(1)) if m else unq(self.expr)

@dataclass
class SV:
    name: str
    tables: dict = field(default_factory=dict)      # alias -> {"source","pk","unique"}
    rels: list = field(default_factory=list)        # (name, from, fcols, to, tcols)
    dims: list = field(default_factory=list)
    metrics: list = field(default_factory=list)
    def metric(self, name):
        n = squash(name)
        for m in self.metrics:
            if squash(m.name) == n:
                return m
        return None
    def related(self, a, b):
        return any(f == a and t == b for _, f, _, t, _ in self.rels)

_ENTRY = re.compile(r'^(PRIVATE\s+)?"?([\w$]+)"?\."?([\w$]+)"?\s+(NON\s+ADDITIVE\s+BY\s*\((.*?)\)\s+)?AS\s+(.*)$', re.I | re.S)

def _strip_comment(rest):
    m = re.search(r"\s+COMMENT\s*=\s*'((?:[^']|'')*)'\s*$", rest, re.I | re.S)
    if m:
        return rest[:m.start()].strip(), m.group(1).replace("''", "'")
    return rest.strip(), ""

def load_ddl(path):
    ddl = open(path, encoding="utf-8", errors="replace").read()
    vm = re.search(r"SEMANTIC\s+VIEW\s+([\w.\"$]+)", ddl, re.I)
    sv = SV(name=unq(vm.group(1).split(".")[-1]) if vm else path)
    for e in split_top(section(ddl, "TABLES")):
        m = re.match(r'^(?:"?([\w$]+)"?\s+AS\s+)?([\w."$]+)(.*)$', e, re.I | re.S)
        if not m: continue
        src = m.group(2)
        alias = unq(m.group(1) or src.split(".")[-1])
        rest = m.group(3)
        pk = re.search(r"PRIMARY\s+KEY\s*\(([^)]*)\)", rest, re.I)
        uq = re.findall(r"UNIQUE\s*\(([^)]*)\)", rest, re.I)
        sv.tables[alias] = {"source": unq(src.split(".")[-1]), "pk": [unq(x) for x in pk.group(1).split(",")] if pk else [],
                            "unique": [[unq(x) for x in u.split(",")] for u in uq]}
    for e in split_top(section(ddl, "RELATIONSHIPS")):
        m = re.match(r'^"?([\w$]+)"?\s+AS\s+"?([\w$]+)"?\s*\(([^)]*)\)\s+REFERENCES\s+"?([\w$]+)"?\s*\(([^)]*)\)', e, re.I)
        if m:
            sv.rels.append((unq(m.group(1)), unq(m.group(2)), [unq(x) for x in m.group(3).split(",")],
                            unq(m.group(4)), [unq(x) for x in m.group(5).split(",")]))
    for e in split_top(section(ddl, "DIMENSIONS")):
        m = _ENTRY.match(e)
        if m:
            sv.dims.append(Dim(unq(m.group(2)), unq(m.group(3)), m.group(6).strip(), bool(m.group(1))))
    for e in split_top(section(ddl, "METRICS")):
        m = _ENTRY.match(e)
        if m:
            expr, com = _strip_comment(m.group(6))
            sv.metrics.append(Metric(unq(m.group(2)), unq(m.group(3)), expr, com, bool(m.group(1)), (m.group(5) or "").strip()))
    return sv

# ---------------------------------------------------------------- PBIX model
@dataclass
class PBIX:
    measures: list    # dicts: table, name, dax
    rels: list        # dicts: from_t, from_c, to_t, to_c, active, card
    columns: dict     # table -> set(columns)
    def measure_table(self, name):
        for m in self.measures:
            if m["name"] == name: return m["table"]

def load_pbix(path):
    import warnings; warnings.filterwarnings("ignore")
    from pbixray import PBIXRay
    p = PBIXRay(path)
    hidden = ("LocalDateTable", "DateTableTemplate")
    ms = [{"table": r.TableName, "name": r.Name, "dax": " ".join(str(r.Expression).split())}
          for _, r in p.dax_measures.iterrows()]
    rels = []
    for _, r in p.relationships.iterrows():
        if str(r.FromTableName).startswith(hidden) or str(r.ToTableName).startswith(hidden): continue
        rels.append({"from_t": r.FromTableName, "from_c": r.FromColumnName, "to_t": r.ToTableName, "to_c": r.ToColumnName,
                     "active": bool(r.get("IsActive", True)), "card": str(r.get("Cardinality", ""))})
    cols = {}
    for _, r in p.schema.iterrows():
        if str(r.TableName).startswith(hidden): continue
        cols.setdefault(r.TableName, set()).add(r.ColumnName)
    try:
        for _, r in p.dax_columns.iterrows():
            cols.setdefault(r.TableName, set()).add(r.ColumnName)
    except Exception:
        pass
    return PBIX(ms, rels, cols)

def alias_for(sv, pbix_table):
    """DDL alias of a Power BI table (by normalised name or source)."""
    n = squash(pbix_table)
    for a, t in sv.tables.items():
        if squash(a) in (n, "COL" + n) or squash(t["source"]).replace("ENRICHED", "") == n:
            return a
    return None

# ---------------------------------------------------------------- DAX helpers
def func_calls(dax, fname):
    """Arguments of every call to fname(...) in a DAX string -> list of arg lists."""
    out = []
    for m in re.finditer(rf"\b{fname}\s*\(", dax, re.I):
        i, depth, q = m.end(), 1, None
        start = i
        while i < len(dax) and depth:
            c = dax[i]
            if q:
                if c == q: q = None
            elif c in "\"'": q = c
            elif c == "(": depth += 1
            elif c == ")": depth -= 1
            i += 1
        out.append(split_top(dax[start:i - 1]))
    return out

def measure_refs(dax):
    """Names in [..] that are not preceded by a table (measure references)."""
    return [x for x in re.findall(r"(?<![\w'\]])\[([^\]]+)\]", dax)]

# ---------------------------------------------------------------- CLI
def cli(check, name, needs_pbix=False, extra=None):
    ap = argparse.ArgumentParser(description=name)
    ap.add_argument("--ddl", required=True, help="generated semantic view DDL (.sql)")
    ap.add_argument("--pbix", required=needs_pbix, help="Power BI file of the same model")
    for a in (extra or []):
        ap.add_argument(*a[0], **a[1])
    args = ap.parse_args()
    sv = load_ddl(args.ddl)
    pb = load_pbix(args.pbix) if args.pbix else None
    r = check(sv, pb, args)
    r.show()
    sys.exit(1 if r.status == "FAIL" else 0)
