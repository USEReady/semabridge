import json
import os
from pathlib import Path

from semabridge.sml.canonicalizer import canonicalize_model, serialize_deterministic
from semabridge.sml.converter_wrapper import find_sml_converters, run_sml_converters


def build_minimal_sml_yaml(canonical: dict) -> str:
    """Create a tiny YAML-ish SML representation for our simple canonical model.

    This is intentionally minimal and only suitable for the example model used
    in the golden tests (tables with columns).
    """
    lines = []
    lines.append("unique_name: Example Model")
    lines.append("object_type: model")
    lines.append("")
    lines.append("datasets:")
    for t in canonical.get("tables", []):
        lines.append(f"  - unique_name: {t.get('name')}")
        lines.append("    columns:")
        for c in t.get("columns", []):
            lines.append(f"      - name: {c.get('name')}")
            lines.append(f"        datatype: {c.get('type')}")
    return "\n".join(lines)


def main():
    root = Path(__file__).resolve().parents[1]
    osi_path = root / "Tests" / "golden" / "sml" / "example_osi.json"
    out_dir = root / "out" / "sml_example"
    out_dir.mkdir(parents=True, exist_ok=True)

    osi = json.loads(osi_path.read_text())
    canonical = canonicalize_model(osi)

    sml_yaml = build_minimal_sml_yaml(canonical)
    sml_file = out_dir / "model.yaml"
    sml_file.write_text(sml_yaml)
    print(f"Wrote minimal SML to {sml_file}")

    exe = find_sml_converters()
    if not exe:
        print("sml-converters not found; integration demo complete. To run converters, install sml-converters (npm install -g sml-converters) and re-run this script.")
        return 0

    print("sml-converters found; running sml-to-cortex against generated SML...")
    proc = run_sml_converters(["sml-to-cortex", "-s", str(out_dir), "-o", str(out_dir / "cortex_out")])
    print(proc.stdout)
    if proc.returncode != 0:
        print("sml-converters returned non-zero exit code")
    else:
        print("sml-converters ran successfully; output in out/sml_example/cortex_out (if produced)")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
