from semabridge.sml.fabric_adapter import fabric_to_osi, fabric_from_osi
from semabridge.sml.canonicalizer import canonicalize_model, serialize_deterministic


def test_fabric_adapter_roundtrip():
    fabric = {
        "datasets": [
            {
                "name": "users",
                "fields": [
                    {"name": "id", "type": "integer"},
                    {"name": "email", "type": "varchar"},
                ],
            },
            {
                "name": "orders",
                "fields": [
                    {"name": "order_id", "type": "bigint"},
                    {"name": "user_id", "type": "integer"},
                ],
            },
        ]
    }

    # add a relationship between orders.user_id -> users.id
    fabric["relationships"] = [
        {
            "from_dataset": "orders",
            "from_field": "user_id",
            "to_dataset": "users",
            "to_field": "id",
            "cardinality": "many-to-one",
        }
    ]

    osi = fabric_to_osi(fabric)
    canonical = canonicalize_model(osi)

    # serialize deterministically and ensure expected names present
    s = serialize_deterministic(canonical)
    assert '"name":"users"' in s
    assert '"name":"orders"' in s

    # round-trip back to fabric-like structure and verify relationships preserved
    fabric2 = fabric_from_osi(osi)
    assert any(d["name"] == "users" for d in fabric2["datasets"]) 
    assert any(d["name"] == "orders" for d in fabric2["datasets"]) 
    assert "relationships" in fabric2
    rel = fabric2["relationships"][0]
    assert rel["from_dataset"] == "orders"
    assert rel["to_dataset"] == "users"
    
