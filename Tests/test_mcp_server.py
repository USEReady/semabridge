import json
import pytest
from semabridge.mcp.server import SemaBridgeMCPServer


def test_mcp_initialize():
    server = SemaBridgeMCPServer()
    req = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {}
    })
    resp = json.loads(server.handle_request(req))
    assert resp["id"] == 1
    assert resp["result"]["protocolVersion"] == "2024-11-05"
    assert resp["result"]["serverInfo"]["name"] == "semabridge-mcp"


def test_mcp_tools_list():
    server = SemaBridgeMCPServer()
    req = json.dumps({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {}
    })
    resp = json.loads(server.handle_request(req))
    assert resp["id"] == 2
    tools = resp["result"]["tools"]
    tool_names = [t["name"] for t in tools]
    assert "list_semantic_models" in tool_names
    assert "get_model_ontology" in tool_names
    assert "search_business_concepts" in tool_names
    assert "get_metric_definition" in tool_names
    assert "build_semantic_query" in tool_names


def test_mcp_build_query_tool_call():
    server = SemaBridgeMCPServer()
    req = json.dumps({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "build_semantic_query",
            "arguments": {
                "model_name": "sales_model",
                "metrics": ["TOTAL_SALES", "GROSS_PROFIT"],
                "dimensions": ["REGION", "YEAR"],
                "filters": ["YEAR = 2026"]
            }
        }
    })
    resp = json.loads(server.handle_request(req))
    assert resp["id"] == 3
    content = json.loads(resp["result"]["content"][0]["text"])
    assert "SELECT REGION, YEAR, METRIC(\"TOTAL_SALES\") AS \"TOTAL_SALES\", METRIC(\"GROSS_PROFIT\") AS \"GROSS_PROFIT\" FROM SALES_MODEL_SEMANTIC WHERE YEAR = 2026 GROUP BY REGION, YEAR;" == content["sql"]
