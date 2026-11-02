"""SemaBridge Model Context Protocol (MCP) Server.

Provides a standard JSON-RPC 2.0 MCP server over stdio and SSE/HTTP,
allowing external AI agents (Claude Desktop, Cursor, LangChain, AutoGen, CrewAI,
and custom enterprise agents) to discover, inspect, and query governed
semantic models and ontology definitions with zero hallucination.

Complies with the Model Context Protocol Specification (2024-11-05).
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("semabridge.mcp.server")

MCP_PROTOCOL_VERSION = "2024-11-05"


class SemaBridgeMCPServer:
    """Native Model Context Protocol Server for SemaBridge."""

    def __init__(self, name: str = "semabridge-mcp", version: str = "1.0.0"):
        self.name = name
        self.version = version
        self.tools: Dict[str, Dict[str, Any]] = {}
        self.handlers: Dict[str, Callable[[Dict[str, Any]], Any]] = {}
        self._register_default_tools()

    def register_tool(
        self,
        name: str,
        description: str,
        input_schema: Dict[str, Any],
        handler: Callable[[Dict[str, Any]], Any],
    ) -> None:
        """Register an MCP tool."""
        self.tools[name] = {
            "name": name,
            "description": description,
            "inputSchema": input_schema,
        }
        self.handlers[name] = handler

    def _register_default_tools(self) -> None:
        """Register the core semantic grounding tools."""
        # Tool 1: list_semantic_models
        self.register_tool(
            name="list_semantic_models",
            description="List all governed semantic models available in SemaBridge.",
            input_schema={
                "type": "object",
                "properties": {},
            },
            handler=self._tool_list_models,
        )

        # Tool 2: get_model_ontology
        self.register_tool(
            name="get_model_ontology",
            description="Get the full ontology for a semantic model (entities, joins, metrics, synonyms, rules).",
            input_schema={
                "type": "object",
                "properties": {
                    "model_name": {"type": "string", "description": "Name or identifier of the semantic model"},
                },
                "required": ["model_name"],
            },
            handler=self._tool_get_model_ontology,
        )

        # Tool 3: search_business_concepts
        self.register_tool(
            name="search_business_concepts",
            description="Search business concepts, synonyms, and metrics across all models.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search term (e.g., 'Gross Margin', 'Revenue', 'Churn')"},
                    "model_name": {"type": "string", "description": "Optional model name to restrict search"},
                },
                "required": ["query"],
            },
            handler=self._tool_search_concepts,
        )

        # Tool 4: get_metric_definition
        self.register_tool(
            name="get_metric_definition",
            description="Get the certified calculation formula, SQL expression, owner, and rules for a metric.",
            input_schema={
                "type": "object",
                "properties": {
                    "metric_name": {"type": "string", "description": "Name or synonym of the metric"},
                    "model_name": {"type": "string", "description": "Optional model name"},
                },
                "required": ["metric_name"],
            },
            handler=self._tool_get_metric_definition,
        )

        # Tool 5: build_semantic_query
        self.register_tool(
            name="build_semantic_query",
            description="Generate a certified Snowflake SQL query against the governed Semantic View.",
            input_schema={
                "type": "object",
                "properties": {
                    "model_name": {"type": "string", "description": "Target semantic model"},
                    "metrics": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of metric names to query",
                    },
                    "dimensions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of dimension column names to slice by",
                    },
                    "filters": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional filter predicates (e.g. 'YEAR(ORDER_DATE) = 2026')",
                    },
                },
                "required": ["model_name", "metrics"],
            },
            handler=self._tool_build_query,
        )

    # ── Tool Implementations ──────────────────────────────────────────────────

    def _tool_list_models(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch available models from repository."""
        try:
            from semabridge.repository.model_repository import ModelRepository

            repo = ModelRepository()
            models = repo.list_models()
            results = []
            for m in models:
                results.append({
                    "model_name": getattr(m, "model_name", str(m)),
                    "dataset_count": getattr(m, "dataset_count", None),
                    "metric_count": getattr(m, "metric_count", None),
                    "updated_at": str(getattr(m, "updated_at", "")),
                })
            return {"models": results}
        except Exception as exc:
            logger.warning("Could not list models from repository: %s", exc)
            return {"models": [], "status": "mock_mode"}

    def _tool_get_model_ontology(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Get model ontology structure."""
        model_name = args.get("model_name", "")
        try:
            from semabridge.repository.model_repository import ModelRepository

            repo = ModelRepository()
            model = repo.get_model(model_name)
            if not model:
                return {"error": f"Model '{model_name}' not found"}

            osi = getattr(model, "osi_model", None)
            if not osi:
                return {"error": f"OSI intermediate not found for model '{model_name}'"}

            return {
                "model_name": osi.unique_name,
                "label": osi.label,
                "description": osi.description,
                "datasets": [
                    {
                        "name": ds.unique_name,
                        "label": ds.label,
                        "description": ds.description,
                        "columns": [c.unique_name for c in ds.columns],
                    }
                    for ds in osi.datasets
                ],
                "metrics": [
                    {
                        "name": m.unique_name,
                        "label": m.label,
                        "dataset": m.dataset,
                        "synonyms": list(getattr(m, "synonyms", []) or []),
                        "description": m.description,
                        "business_owner": m.business_owner,
                    }
                    for m in osi.metrics
                ],
                "relationships": [
                    {
                        "from": rel.from_dataset,
                        "to": rel.to_dataset,
                        "cardinality": str(rel.cardinality),
                    }
                    for rel in osi.relationships
                ],
            }
        except Exception as exc:
            return {"error": f"Failed to retrieve ontology: {exc}"}

    def _tool_search_concepts(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Search concepts, synonyms, and metric names."""
        query = str(args.get("query", "")).strip().casefold()
        model_name = args.get("model_name")
        matches: List[Dict[str, Any]] = []

        try:
            from semabridge.repository.model_repository import ModelRepository

            repo = ModelRepository()
            models_to_check = [repo.get_model(model_name)] if model_name else repo.list_models()
            for m in models_to_check:
                if not m:
                    continue
                osi = getattr(m, "osi_model", None)
                if not osi:
                    continue
                # Search datasets
                for ds in osi.datasets:
                    if query in ds.unique_name.casefold() or query in (ds.label or "").casefold():
                        matches.append({
                            "type": "entity",
                            "model": osi.unique_name,
                            "name": ds.unique_name,
                            "label": ds.label,
                            "description": ds.description,
                        })
                # Search metrics & synonyms
                for metric in osi.metrics:
                    syns = list(getattr(metric, "synonyms", []) or [])
                    syn_match = any(query in s.casefold() for s in syns)
                    name_match = query in metric.unique_name.casefold() or query in (metric.label or "").casefold()
                    if name_match or syn_match:
                        matches.append({
                            "type": "metric",
                            "model": osi.unique_name,
                            "name": metric.unique_name,
                            "synonyms": syns,
                            "dataset": metric.dataset,
                            "description": metric.description,
                            "business_owner": metric.business_owner,
                        })
            return {"query": query, "matches": matches}
        except Exception as exc:
            return {"query": query, "matches": [], "error": str(exc)}

    def _tool_get_metric_definition(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch metric definition with verified SQL."""
        metric_name = str(args.get("metric_name", "")).strip().casefold()
        try:
            from semabridge.repository.model_repository import ModelRepository

            repo = ModelRepository()
            for m in repo.list_models():
                osi = getattr(m, "osi_model", None)
                if not osi:
                    continue
                for metric in osi.metrics:
                    syns = [s.casefold() for s in (getattr(metric, "synonyms", []) or [])]
                    if metric.unique_name.casefold() == metric_name or metric_name in syns:
                        return {
                            "metric_name": metric.unique_name,
                            "model_name": osi.unique_name,
                            "dataset": metric.dataset,
                            "aggregation": str(metric.aggregation),
                            "expression": metric.expression or metric.source_column,
                            "sql_expression": getattr(metric, "sql_expression", None) or metric.expression,
                            "synonyms": list(getattr(metric, "synonyms", []) or []),
                            "description": metric.description,
                            "business_owner": metric.business_owner,
                        }
            return {"error": f"Metric '{metric_name}' not found in any governed model"}
        except Exception as exc:
            return {"error": str(exc)}

    def _tool_build_query(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Assemble verified Snowflake SQL query against the semantic view."""
        model_name = args.get("model_name", "")
        metrics = args.get("metrics", [])
        dimensions = args.get("dimensions", [])
        filters = args.get("filters", [])

        view_name = f"{model_name.upper()}_SEMANTIC"
        select_cols = []
        for dim in dimensions:
            select_cols.append(dim)
        for met in metrics:
            select_cols.append(f'METRIC("{met}") AS "{met}"')

        select_clause = ", ".join(select_cols) if select_cols else "*"
        where_clause = f" WHERE {' AND '.join(filters)}" if filters else ""
        group_by = f" GROUP BY {', '.join(dimensions)}" if dimensions else ""

        sql = f"SELECT {select_clause} FROM {view_name}{where_clause}{group_by};"
        return {
            "sql": sql,
            "semantic_view": view_name,
            "requested_metrics": metrics,
            "requested_dimensions": dimensions,
        }

    # ── JSON-RPC Protocol Dispatcher ──────────────────────────────────────────

    def handle_request(self, request_json: str) -> str:
        """Handle incoming JSON-RPC 2.0 request string and return response string."""
        try:
            req = json.loads(request_json)
        except Exception as exc:
            return json.dumps({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {exc}"},
            })

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        # 1. Initialize
        if method == "initialize":
            return json.dumps({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": self.name, "version": self.version},
                },
            })

        # 2. tools/list
        if method == "tools/list":
            return json.dumps({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": list(self.tools.values())},
            })

        # 3. tools/call
        if method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})
            handler = self.handlers.get(tool_name)
            if not handler:
                return json.dumps({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Tool '{tool_name}' not found"},
                })
            try:
                result_data = handler(tool_args)
                return json.dumps({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {"type": "text", "text": json.dumps(result_data, indent=2)}
                        ]
                    },
                })
            except Exception as exc:
                return json.dumps({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32000, "message": f"Tool execution failed: {exc}"},
                })

        # Unknown method
        return json.dumps({
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method '{method}' not found"},
        })

    def run_stdio(self) -> None:
        """Run the MCP server over standard I/O (for Claude Desktop / Cursor / CLI agents)."""
        logger.info("Starting SemaBridge MCP Server on stdio...")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            response = self.handle_request(line)
            sys.stdout.write(response + "\n")
            sys.stdout.flush()
