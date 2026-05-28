from semabridge.sml.fabric_adapter import fabric_to_osi, fabric_from_osi
from semabridge.sml.canonicalizer import canonicalize_model, serialize_deterministic


def test_fabric_metrics_roundtrip():
    fabric = {
        "datasets": [
            {"name": "orders", "fields": [{"name": "order_id", "type": "bigint"}]},
        ],
        "metrics": [
            {"name": "order_count", "expression": "count(order_id)", "type": "count"}
        ],
    }

    osi = fabric_to_osi(fabric)
    canonical = canonicalize_model(osi)

    s = serialize_deterministic(canonical)
    # canonical should include the measure
    assert '"name":"order_count"' in s

    # round-trip back to fabric-like structure and verify metrics preserved
    fabric2 = fabric_from_osi(osi)
    assert "metrics" in fabric2
    assert any(m["name"] == "order_count" for m in fabric2["metrics"])
