"""Run the ontology test scripts in order.

    uv run python ontology_tests/run_all.py --project proj-artificial-intelligence-sample
    uv run python ontology_tests/run_all.py --project <id> --questions ontology_tests/fixtures/questions_example.json --execute

Run from the repo root with the SemaBridge API running.
"""
import subprocess
import sys
from pathlib import Path

here = Path(__file__).parent
args = sys.argv[1:]


def opt(name):
    return args[args.index(name) + 1] if name in args else None


project, questions = opt("--project"), opt("--questions")
common = [a for a in ("--base", "--token") if a in args for a in (a, opt(a))]
steps = [
    ["o01_loader_root_cause.py"],
    ["o02_projects_and_osi_files.py"] + (["--project", project] if project else []),
    (["o03_generate_from_osi_file.py", "--project", project] + (["--keep"] if questions else [])) if project else None,
    ["o04_upload_rules.py"],
    ["o05_collisions.py"],
    ["o06_validate_save_rename.py"],
    ["o07_project_attach_preview.py", "--project", project] if project else None,
    (["o08_ai_studio.py", "--questions", questions] + (["--project", project] if project else [])
     + (["--execute"] if "--execute" in args else [])) if questions else None,
    ["o10_multi_model_validation.py"],
]
results = []
for step in filter(None, steps):
    extra = common if step[0] != "o01_loader_root_cause.py" else []
    rc = subprocess.call([sys.executable, str(here / step[0]), *step[1:], *extra])
    results.append((step[0], rc))
print("\n=== SUMMARY ===")
for name, rc in results:
    print(f"  {'PASS' if rc == 0 else 'FAIL'}  {name}")
sys.exit(1 if any(rc for _, rc in results) else 0)
