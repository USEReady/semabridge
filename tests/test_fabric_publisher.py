"""Tests for FabricPublisher payload preflight validation."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest

from semabridge.connectors.fabric_publisher import FabricPublisher, PublishError


class _DummySecret:
    def get_secret_value(self) -> str:
        return "dummy"


def _make_publisher() -> FabricPublisher:
    config = SimpleNamespace(
        workspace_id="ws1",
        client_id="client",
        tenant_id="tenant",
        client_secret=_DummySecret(),
    )
    return FabricPublisher(config=config)


def _make_payload(relationships: list[dict[str, str]]) -> dict[str, object]:
    model_bim = {
        "name": "REL_TEST_A_COL__TEST_B_COL",
        "model": {
            "tables": [],
            "relationships": relationships,
        },
    }
    model_bim_b64 = base64.b64encode(json.dumps(model_bim).encode("utf-8")).decode("ascii")
    return {
        "definition": {
            "parts": [
                {
                    "path": "model.bim",
                    "payload": model_bim_b64,
                    "payloadType": "InlineBase64",
                }
            ]
        }
    }


def test_validate_full_definition_payload_accepts_clean_relationships() -> None:
    publisher = _make_publisher()
    payload = _make_payload(
        [
            {
                "name": "REL_FACT_PRODUCT_ID__PRODUCT_ID",
                "fromTable": "FACT",
                "fromColumn": "PRODUCT_ID",
                "toTable": "PRODUCT",
                "toColumn": "ID",
            },
            {
                "name": "REL_FACT_CALENDAR_ID__CALENDAR_ID",
                "fromTable": "FACT",
                "fromColumn": "CALENDAR_ID",
                "toTable": "CALENDAR",
                "toColumn": "ID",
            },
        ]
    )

    stats = publisher._validate_full_definition_payload(payload)

    assert stats["total"] == 2
    assert stats["unique_endpoints"] == 2
    assert stats["duplicates"] == 0


def test_validate_full_definition_payload_rejects_sys_relationship_names() -> None:
    publisher = _make_publisher()
    payload = _make_payload(
        [
            {
                "name": "SYS_RELATIONSHIP_abc123",
                "fromTable": "FACT",
                "fromColumn": "PRODUCT_ID",
                "toTable": "PRODUCT",
                "toColumn": "ID",
            }
        ]
    )

    with pytest.raises(PublishError, match="system relationship name"):
        publisher._validate_full_definition_payload(payload)


def test_validate_full_definition_payload_rejects_duplicate_endpoints() -> None:
    publisher = _make_publisher()
    payload = _make_payload(
        [
            {
                "name": "REL_FACT_PRODUCT_ID__PRODUCT_ID",
                "fromTable": "FACT",
                "fromColumn": "PRODUCT_ID",
                "toTable": "PRODUCT",
                "toColumn": "ID",
            },
            {
                "name": "REL_FACT_PRODUCT_ID__PRODUCT_ID",
                "fromTable": "FACT",
                "fromColumn": "PRODUCT_ID",
                "toTable": "PRODUCT",
                "toColumn": "ID",
            },
        ]
    )

    with pytest.raises(PublishError, match="duplicate relationship endpoints"):
        publisher._validate_full_definition_payload(payload)


def test_extract_model_bim_payload_rejects_missing_part() -> None:
    publisher = _make_publisher()
    payload = {"definition": {"parts": []}}

    with pytest.raises(PublishError, match="model.bim part not found"):
        publisher._extract_model_bim_payload(payload)


def test_validate_full_definition_payload_rejects_non_deterministic_name() -> None:
    publisher = _make_publisher()
    payload = _make_payload(
        [
            {
                "name": "RANDOM_NAME_123",
                "fromTable": "FACT",
                "fromColumn": "PRODUCT_ID",
                "toTable": "PRODUCT",
                "toColumn": "ID",
            }
        ]
    )

    with pytest.raises(PublishError, match="non-deterministic relationship name"):
        publisher._validate_full_definition_payload(payload)


def test_resolve_workspace_id_returns_guid_as_is() -> None:
    publisher = _make_publisher()
    guid = "12345678-1234-1234-1234-1234567890ab"

    assert publisher.resolve_workspace_id(guid) == guid


def test_resolve_workspace_id_matches_display_name() -> None:
    publisher = _make_publisher()
    publisher.list_workspaces = lambda: [
        {"id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "displayName": "SemaBridge Workspace"}
    ]

    assert publisher.resolve_workspace_id("SemaBridge Workspace") == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
