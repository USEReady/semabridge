"""Issue: the view name changes between runs, leaving two views for one model.
Expect: two runs of the same model (--ddl and --ddl2) produce the same view name."""
from common import Result, cli, load_ddl
NAME = "T23 view name stable between runs"
def check(sv, pbix, args):
    r = Result(NAME)
    if not args.ddl2: r.info(f"view name: {sv.name} (give --ddl2 from another run to compare)"); return r
    other = load_ddl(args.ddl2)
    if other.name != sv.name: r.fail(f"{sv.name} vs {other.name}: two views would exist")
    else: r.ok(f"same name: {sv.name}")
    return r
if __name__ == "__main__":
    cli(check, NAME, extra=[(["--ddl2"], {})])
