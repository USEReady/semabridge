from semabridge.sml.canonicalizer import canonicalize_model, serialize_deterministic
from semabridge.sml.osi_adapter import osi_from_canonical, canonical_from_osi


def test_roundtrip_canonical_osi():
    osi = {
        "tables": [
            {
                "name": "orders",
                "columns": [
                    {"name": "order_id", "type": "int"},
                    {"name": "user_id", "type": "int"},
                ],
            },
            {
                "name": "users",
                "columns": [
                    {"name": "id", "type": "int"},
                    {"name": "email", "type": "string"},
                ],
            },
        ]
    }

    canonical = canonical_from_osi(osi)
    osi_back = osi_from_canonical(canonical)

    canonical2 = canonical_from_osi(osi_back)

    # canonical representations must be stable across the round trip
    assert serialize_deterministic(canonical) == serialize_deterministic(canonical2)

    # ensure names preserved
    names_orig = {t["name"] for t in osi.get("tables", [])}
    names_back = {t["name"] for t in osi_back.get("tables", [])}
    assert names_orig == names_back
