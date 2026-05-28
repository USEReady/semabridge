import json

from semabridge.sml.canonicalizer import canonicalize_model, serialize_deterministic


def test_canonicalizer_deterministic():
    osi = {
        "tables": [
            {
                "name": "users",
                "columns": [
                    {"name": "email", "type": "string"},
                    {"name": "id", "type": "int"},
                ],
            }
        ]
    }

    c1 = canonicalize_model(osi)
    c2 = canonicalize_model(osi)
    # structure stability
    assert c1 == c2

    s1 = serialize_deterministic(c1)
    s2 = serialize_deterministic(c2)
    # byte-for-byte deterministic serialization
    assert s1 == s2

    # basic shape checks
    assert "tables" in c1
    assert len(c1["tables"]) == 1
    t = c1["tables"][0]
    assert t["name"] == "users"
    assert all("name" in col and "type" in col for col in t["columns"])
