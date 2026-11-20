"""Characterization artifacts for the REAL sample reports: Examples/**/*.pbix.

Each .pbix runs the real extraction (LocalPBIXConnector, which needs pbixray's
native decoder for binary DataModel files) and the real PBIX conversion + DDL,
normalized like _build.py. Snapshots live in snapshots/real_pbix/<slug>.*

They are generated and checked on Linux CI (.github/workflows/characterization.yml);
machines whose policy blocks pbixray skip locally unless
SEMABRIDGE_REQUIRE_REAL_PBIX=1 (set in CI), where a skip is a failure.

    python Tests/characterization/_build_real_pbix.py            # write all
    python Tests/characterization/_build_real_pbix.py --missing  # write only missing ones
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Dict, List

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("characterization_build", _HERE / "_build.py")
_build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_build)  # type: ignore[union-attr]

REPO_ROOT = _build.REPO_ROOT
SNAPSHOT_DIR = _HERE / "snapshots" / "real_pbix"
KINDS = ("osi.json", "sml.json", "ddl.sql")


def pbix_samples() -> List[Path]:
    return sorted((REPO_ROOT / "Examples").rglob("*.pbix"), key=lambda p: p.as_posix().lower())


def slug(path: Path) -> str:
    rel = path.relative_to(REPO_ROOT / "Examples").with_suffix("").as_posix()
    return re.sub(r"[^A-Za-z0-9]+", "_", rel).strip("_").lower()


def pbixray_available() -> str:
    """'' when pbixray's native decoder loads, else the reason it does not."""
    try:
        import pbixray  # noqa: F401
        from semabridge.connectors.pbixray_compat import get_pbixray

        if get_pbixray() is None:
            return "pbixray native decoder blocked or not installed"
    except Exception as exc:  # noqa: BLE001 - reported as the skip reason
        return f"pbixray unavailable: {exc}"
    return ""


def build_one(pbix: Path) -> Dict[str, str]:
    from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
    from semabridge.core.engine.conversion.pbix import _convert_pbix_to_sml
    from semabridge.core.source_format import from_pbix_tmsl

    connector = LocalPBIXConnector({"pbix_path": str(pbix)})
    discovered = connector.discover()
    tmsl = discovered.get("raw_tmsl") or connector.extract()
    if not tmsl:
        raise RuntimeError(f"Could not extract DataModelSchema from {pbix}")
    sf = from_pbix_tmsl(
        project_id=_build.PROJECT_ID, run_id="run-characterization", tmsl=tmsl,
        pbix_path=(Path("Examples") / pbix.relative_to(REPO_ROOT / "Examples")).as_posix(),
        field_aliases=discovered.get("field_aliases", []),
        unresolved_report_field_references=discovered.get("unresolved_report_field_references", []),
        ambiguous_report_aliases=discovered.get("ambiguous_report_aliases", []),
    )
    ctx = _build._context("pbix", sf)
    sml = _convert_pbix_to_sml(_build._ENGINE_SELF, ctx)
    return {"osi.json": _build._dump(ctx.osi_model), "sml.json": _build._dump(sml), "ddl.sql": _build._ddl(sml)}


def snapshot_path(pbix: Path, kind: str) -> Path:
    return SNAPSHOT_DIR / f"{slug(pbix)}.{kind}"


def write(missing_only: bool = False) -> List[Path]:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    for pbix in pbix_samples():
        if missing_only and all(snapshot_path(pbix, k).exists() for k in KINDS):
            continue
        for kind, text in build_one(pbix).items():
            p = snapshot_path(pbix, kind)
            p.write_text(text, encoding="utf-8", newline="\n")
            written.append(p)
    return written


if __name__ == "__main__":
    reason = pbixray_available()
    if reason:
        sys.exit(reason)
    import semabridge

    # Provenance: CI generates these from the BASE commit's src/ (PYTHONPATH),
    # so print which semabridge produced them.
    print(f"semabridge loaded from: {Path(semabridge.__file__).resolve().parent}")
    paths = write(missing_only="--missing" in sys.argv)
    print(f"{len(paths)} snapshot file(s) written to {SNAPSHOT_DIR}")
    for p in paths:
        print(" ", p.relative_to(REPO_ROOT).as_posix())
