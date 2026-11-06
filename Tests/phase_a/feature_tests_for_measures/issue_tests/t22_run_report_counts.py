"""Issue: run report counts come from the dry run, not from the deployed view.
Expect: '--converted N' from the run report equals the number of metrics with SQL in the deployed DDL."""
from common import Result, cli
NAME = "T22 run report counts match the deployed view"
def check(sv, pbix, args):
    r = Result(NAME)
    with_sql = sum(1 for m in sv.metrics if not m.is_null and not m.private)
    nulls = sum(1 for m in sv.metrics if m.is_null)
    r.info(f"deployed view: {with_sql} measures with SQL, {nulls} NULL")
    if args.converted is not None:
        if args.converted != with_sql: r.fail(f"run report says {args.converted} converted, view has {with_sql}")
        else: r.ok("run report matches the view")
    return r
if __name__ == "__main__":
    cli(check, NAME, extra=[(["--converted"], {"type": int})])
