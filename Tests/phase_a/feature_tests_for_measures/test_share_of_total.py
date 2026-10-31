#!/usr/bin/env python3
"""
test_share_of_total.py - shows, on real .pbix data, how a share-of-total measure behaves:

  DAX        DIVIDE(COUNT, CALCULATE(COUNT, ALL(<share dimension>)))   -> removes ONLY the share dimension's
             filter; every other filter/grouping (year, agent, ...) stays in the denominator
  CURRENT    SemaBridge enriched view: COUNT(*) / MAX(ALL_COUNT) with ALL_COUNT = COUNT(*) OVER () precomputed
             once for the whole table -> ignores every other filter
  WINDOW     query-time window: COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY <the other grouping columns>)
             computed inside the query, after its filters -> what a semantic-view window metric would do

For each scenario it prints the DAX value, the CURRENT value and the WINDOW value per group, whether the shares
of each context add up to 100%, and PASS/FAIL per method. With --ddl it also checks how the generated view
handles the share-of-total metrics (NULL + reason, or labelled approximation, helper column hidden).

Generic: any fact table, any share dimension (fact column or a column of a related table), any context column.
Defaults reproduce the AI Sample measures "Cases % by Product" and "Cases % by Subject".

usage:
  python test_share_of_total.py --pbix "Artificial Intelligence Sample.pbix" [--ddl <view ddl>.sql]
  python test_share_of_total.py --pbix X.pbix --fact Sales --share "Product.Category" --context Year:Order Date
needs: pip install duckdb pandas pbixray
"""
import argparse, re, sys, warnings
warnings.filterwarnings("ignore")


def load(px, name):
    df = px.get_table(name).copy()
    df.columns = [re.sub(r"[^A-Za-z0-9]", "_", c).upper() for c in df.columns]
    return df


def col(name):
    return re.sub(r"[^A-Za-z0-9]", "_", name).upper()


def build(px, fact, share, contexts):
    """Return one DataFrame with fact rows + SHARE + CONTEXT_* columns, joining a related table if needed."""
    import pandas as pd
    F = load(px, fact)
    if "." in share:
        dim, dcol = share.split(".", 1)
        rel = px.relationships
        r = rel[((rel.FromTableName == fact) & (rel.ToTableName == dim))]
        if r.empty:
            sys.exit(f"no relationship {fact} -> {dim}")
        r = r.iloc[0]
        D = load(px, dim)[[col(r.ToColumnName), col(dcol)]].rename(columns={col(dcol): "SHARE"})
        F = F.merge(D, left_on=col(r.FromColumnName), right_on=col(r.ToColumnName), how="left")
    else:
        F["SHARE"] = F[col(share)]
    for c in contexts:
        label, _, source = c.partition(":")
        if label.upper() == "YEAR":
            F["CTX_YEAR"] = pd.to_datetime(F[col(source)]).dt.year
        else:
            F["CTX_" + col(label)] = F[col(source or label)]
    return F


def scenarios(F, contexts):
    out = [("no filter, by share dimension", None, [])]
    for c in contexts:
        label = c.partition(":")[0]
        k = "CTX_" + col(label)
        if label.upper() == "YEAR":
            yr = int(F[k].max())
            out.append((f"filter {label} = {yr}", (k, yr), []))
        else:
            out.append((f"grouped by {label} and share dimension", None, [k]))
    return out


def run(F, title, flt, group_ctx):
    import duckdb
    con = duckdb.connect()
    con.register("f", F)
    where = f"WHERE {flt[0]} = {flt[1]}" if flt else ""
    gcols = ", ".join(group_ctx + ["SHARE"])
    part = f"PARTITION BY {', '.join(group_ctx)}" if group_ctx else ""
    # DAX semantics computed independently in pandas
    sub = F[F[flt[0]] == flt[1]] if flt else F
    keys = group_ctx + ["SHARE"]
    num = sub.groupby(keys, dropna=False).size().rename("n").reset_index()
    if group_ctx:
        den = sub.groupby(group_ctx, dropna=False).size().rename("d").reset_index()
        dax = num.merge(den, on=group_ctx)
    else:
        dax = num.assign(d=len(sub))
    dax["DAX"] = dax.n / dax.d
    # CURRENT: whole-table total precomputed in the view (ignores the filter)
    total = len(F)
    # WINDOW: query-time window after the filter
    q = f"""SELECT {gcols}, COUNT(*) AS n, COUNT(*) / NULLIF(SUM(COUNT(*)) OVER ({part}), 0) AS WINDOW
            FROM f {where} GROUP BY {gcols}"""
    win = con.execute(q).df()
    res = dax.merge(win[keys + ["WINDOW"]], on=keys, how="left")
    res["CURRENT"] = res.n / total
    res = res.sort_values(keys).reset_index(drop=True)
    ctx_sums = res.groupby(group_ctx)[["DAX", "CURRENT", "WINDOW"]].sum() if group_ctx else res[["DAX", "CURRENT", "WINDOW"]].sum().to_frame().T
    ok_cur = (res.CURRENT - res.DAX).abs().max() < 1e-9
    ok_win = (res.WINDOW - res.DAX).abs().max() < 1e-9
    print(f"\n--- {title} ---")
    show = res.head(6)
    for _, r in show.iterrows():
        lab = " / ".join(str(r[k]) for k in keys)
        print(f"  {lab[:40]:40} DAX {r.DAX:8.2%}   CURRENT {r.CURRENT:8.2%}   WINDOW {r.WINDOW:8.2%}")
    if len(res) > 6:
        print(f"  ... {len(res) - 6} more groups")
    print(f"  shares per context add up to:  DAX {ctx_sums.DAX.min():.0%}..{ctx_sums.DAX.max():.0%}   "
          f"CURRENT {ctx_sums.CURRENT.min():.1%}..{ctx_sums.CURRENT.max():.1%}   WINDOW {ctx_sums.WINDOW.min():.0%}..{ctx_sums.WINDOW.max():.0%}")
    print(f"  CURRENT (precomputed OVER ()): {'PASS' if ok_cur else 'FAIL'}   "
          f"max diff {(res.CURRENT - res.DAX).abs().max():.2%}")
    print(f"  WINDOW  (query-time):          {'PASS' if ok_win else 'FAIL'}")
    return ok_cur, ok_win


def check_ddl(path):
    text = open(path, encoding="utf-8").read()
    print(f"\n=== generated view: {path} ===")
    metrics = re.findall(r'(\w+)\."?(\w+)"?\s+as\s+([^\n]*?/\s*NULLIF\(\s*MAX\(\s*\w+\."?(\w*ALL\w*)"?\s*\)[^\n]*)', text, re.I)
    helper_dims = set(re.findall(r'\w+\."?(\w*ALL_\w*|__SB_\w+)"?\s+as\s+\w+\.', text, re.I))
    nulls = re.findall(r'(\w+)\."?(\w*(?:PCT|SHARE)\w*)"?\s+as\s+CAST\(NULL AS DOUBLE\)([^\n]*)', text, re.I)
    if not metrics:
        print("  no precomputed share-of-total metrics (MAX(<ALL_ column>)) found")
    for t, m, expr, colname in metrics:
        has_comment = re.search(r"COMMENT\s*=\s*'[^']*filter", expr, re.I)
        print(f"  {t}.{m}: precomputed whole-table total ({colname}) -> "
              + ("labelled 'ignores other filters' (opt-in OK)" if has_comment else "NO comment -> FAIL (silently approximate)"))
    if helper_dims:
        print(f"  helper column(s) exposed as dimensions: {', '.join(sorted(helper_dims))} -> FAIL (hide them)")
    for t, m, rest in nulls:
        print(f"  {t}.{m}: NULL" + (" + reason" if "COMMENT" in rest.upper() else " (no reason in view)"))
    ok = all(re.search(r"COMMENT\s*=\s*'[^']*filter", e, re.I) for _, _, e, _ in metrics) and not helper_dims
    print(f"  issue 3 status in this view: {'RESOLVED' if ok else 'NOT RESOLVED'}")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pbix", required=True)
    ap.add_argument("--fact", default="Cases")
    ap.add_argument("--share", action="append", help="share dimension: column of the fact, or Table.Column of a related table")
    ap.add_argument("--context", action="append", help="other filter/grouping: Label:Column (Year:<date column> = filter latest year)")
    ap.add_argument("--ddl")
    a = ap.parse_args()
    from pbixray import PBIXRay
    px = PBIXRay(a.pbix)
    shares = a.share or ["Products.Product", "Subject"]
    contexts = a.context or ["Year:Case Created On", "Agent:Agent"]
    all_cur, all_win = True, True
    for s in shares:
        print(f"\n================ share of total by {s}  (DAX: DIVIDE(count, CALCULATE(count, ALL({s.split('.')[0]})))) ================")
        F = build(px, a.fact, s, contexts)
        for title, flt, grp in scenarios(F, contexts):
            c, w = run(F, title, flt, grp)
            all_cur &= c; all_win &= w
    print(f"\nSUMMARY  precomputed OVER () matches DAX in all scenarios: {all_cur}   query-time window matches DAX: {all_win}")
    ok = True
    if a.ddl:
        ok = check_ddl(a.ddl)
    sys.exit(0 if (all_win and ok) else 1)


if __name__ == "__main__":
    main()
