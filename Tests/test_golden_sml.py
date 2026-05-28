import json
from pathlib import Path

from semabridge.sml.canonicalizer import canonicalize_model, serialize_deterministic


def test_golden_example_matches_canonical():
    root = Path(__file__).resolve().parent / "golden" / "sml"
    osi = json.loads((root / "example_osi.json").read_text())
    golden = json.loads((root / "example_canonical.json").read_text())

    produced = canonicalize_model(osi)

    assert serialize_deterministic(produced) == serialize_deterministic(golden)
