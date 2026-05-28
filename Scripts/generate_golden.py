from semabridge.sml.canonicalizer import canonicalize_model, serialize_deterministic

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

print(serialize_deterministic(canonicalize_model(osi)))
