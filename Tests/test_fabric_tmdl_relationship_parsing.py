import base64

from semabridge.connectors.fabric_extractor import FabricExtractor


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def test_tmdl_relationship_block_fromcolumn_tocolumn_keeps_endpoints():
    extractor = FabricExtractor.__new__(FabricExtractor)
    parts = [
        {
            "path": "definition/model.tmdl",
            "payloadType": "InlineBase64",
            "payload": _b64("model CUSTOMER_PROFITABILITY_SEMANTIC"),
        },
        {
            "path": "definition/tables/FACT.tmdl",
            "payloadType": "InlineBase64",
            "payload": _b64("table FACT\n  column CUSTOMER_KEY\n"),
        },
        {
            "path": "definition/tables/DIM_CUSTOMER.tmdl",
            "payloadType": "InlineBase64",
            "payload": _b64("table DIM_CUSTOMER\n  column CUSTOMER_KEY\n"),
        },
        {
            "path": "definition/relationships.tmdl",
            "payloadType": "InlineBase64",
            "payload": _b64(
                "\n".join(
                    [
                        "relationship rel_inline",
                        "  fromTable: FACT",
                        "  fromColumn: CUSTOMER_KEY",
                        "  toTable: DIM_CUSTOMER",
                        "  toColumn: CUSTOMER_KEY",
                        "",
                        "relationship rel_block_form",
                        "  fromColumn: 'FACT'[CUSTOMER_KEY]",
                        "  toColumn: 'DIM_CUSTOMER'[CUSTOMER_KEY]",
                    ]
                )
            ),
        },
    ]

    parsed = extractor._parse_tmdl_package_parts(parts)
    assert parsed is not None
    relationships = parsed["model"]["relationships"]
    assert len(relationships) == 2
    assert any(
        r.get("fromTable") == "FACT"
        and r.get("fromColumn") == "CUSTOMER_KEY"
        and r.get("toTable") == "DIM_CUSTOMER"
        and r.get("toColumn") == "CUSTOMER_KEY"
        for r in relationships
    )


def test_tmdl_relationships_are_collected_from_multiple_relationship_files():
    extractor = FabricExtractor.__new__(FabricExtractor)
    parts = [
        {
            "path": "definition/model.tmdl",
            "payloadType": "InlineBase64",
            "payload": _b64("model CUSTOMER_PROFITABILITY_SEMANTIC"),
        },
        {
            "path": "definition/tables/FACT.tmdl",
            "payloadType": "InlineBase64",
            "payload": _b64("table FACT\n  column KEY_1\n  column KEY_2\n"),
        },
        {
            "path": "definition/tables/DIM_A.tmdl",
            "payloadType": "InlineBase64",
            "payload": _b64("table DIM_A\n  column KEY_1\n  column KEY_2\n"),
        },
        {
            "path": "definition/relationships.tmdl",
            "payloadType": "InlineBase64",
            "payload": _b64(
                "\n".join(
                    [
                        "relationship rel_1",
                        "  fromTable: FACT",
                        "  fromColumn: KEY_1",
                        "  toTable: DIM_A",
                        "  toColumn: KEY_1",
                    ]
                )
            ),
        },
        {
            "path": "definition/relationships_extra.tmdl",
            "payloadType": "InlineBase64",
            "payload": _b64(
                "\n".join(
                    [
                        "relationship rel_2",
                        "  fromTable: FACT",
                        "  fromColumn: KEY_2",
                        "  toTable: DIM_A",
                        "  toColumn: KEY_2",
                    ]
                )
            ),
        },
    ]

    parsed = extractor._parse_tmdl_package_parts(parts)
    assert parsed is not None
    relationships = parsed["model"]["relationships"]
    assert len(relationships) == 2


def test_tmdl_relationships_declared_in_model_tmdl_are_collected():
    extractor = FabricExtractor.__new__(FabricExtractor)
    parts = [
        {
            "path": "definition/model.tmdl",
            "payloadType": "InlineBase64",
            "payload": _b64(
                "\n".join(
                    [
                        "model CUSTOMER_PROFITABILITY_SEMANTIC",
                        "relationship rel_model_file",
                        "  fromTable: FACT",
                        "  fromColumn: KEY_1",
                        "  toTable: DIM_A",
                        "  toColumn: KEY_1",
                    ]
                )
            ),
        },
        {
            "path": "definition/tables/FACT.tmdl",
            "payloadType": "InlineBase64",
            "payload": _b64("table FACT\n  column KEY_1\n  column KEY_2\n"),
        },
        {
            "path": "definition/tables/DIM_A.tmdl",
            "payloadType": "InlineBase64",
            "payload": _b64("table DIM_A\n  column KEY_1\n  column KEY_2\n"),
        },
        {
            "path": "definition/relationships.tmdl",
            "payloadType": "InlineBase64",
            "payload": _b64(
                "\n".join(
                    [
                        "relationship rel_relationship_file",
                        "  fromTable: FACT",
                        "  fromColumn: KEY_2",
                        "  toTable: DIM_A",
                        "  toColumn: KEY_2",
                    ]
                )
            ),
        },
    ]

    parsed = extractor._parse_tmdl_package_parts(parts)
    assert parsed is not None
    relationships = parsed["model"]["relationships"]
    assert len(relationships) == 2
