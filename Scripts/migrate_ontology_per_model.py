"""Split a multi-model project's merged ontology domain into one domain per model.

Usage:
    .venv/bin/python Scripts/migrate_ontology_per_model.py --project proj-multi_sync \
        [--ontology-dir Config/ontologies] [--report out.json] [--dry-run]

Each model's domain is rebuilt from that model's latest successful snapshot
(Postgres ``snapshots``); user-authored fields of the merged domain
(descriptions, synonyms, business rules, stewards, tags, origin "user") are
carried by name; user items no model defines go to a project "shared" domain.
Same-named items of several models are reported as clashes. Idempotent: a
project without a merged domain is reported as "already migrated".
Back up the ontology directory before running.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", action="append", required=True, help="project id (repeatable)")
    parser.add_argument("--ontology-dir", default="Config/ontologies")
    parser.add_argument("--config-dir", default=None, help="project YAML dir (default Config/projects)")
    parser.add_argument("--report", default=None, help="write the JSON report here")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv

        load_dotenv(".env")
    except Exception:
        pass

    from semabridge.ontology.per_model import migrate_project_to_per_model
    from semabridge.ontology.project_models import project_models
    from semabridge.ontology.store import DomainOntologyStore

    store = DomainOntologyStore(storage_dir=Path(args.ontology_dir))
    results = []
    for pid in args.project:
        models = project_models(pid, config_dir=Path(args.config_dir) if args.config_dir else None)
        results.append(migrate_project_to_per_model(pid, store, models, dry_run=args.dry_run))
    store.reload_from_disk()
    out = {"results": results, "load_errors": store.load_errors}
    text = json.dumps(out, indent=2, default=str)
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
    print(text)
    return 1 if store.load_errors else 0


if __name__ == "__main__":
    sys.exit(main())
