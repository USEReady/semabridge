"""REST API Router for Embedded SemaBridge AI Layer.

Provides zero-credit local semantic search and Natural Language to Governed SQL
without dependency on Snowflake Cortex or external credits.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from semabridge.ai.nl_query_engine import QueryResolutionPlan
from semabridge.ai.semantic_search import SearchHit
from semabridge.ontology.store import ontology_store

logger = logging.getLogger("semabridge.api.ai_router")

router = APIRouter(prefix="/api/ai", tags=["semabridge_ai"])


class AISearchRequest(BaseModel):
    query: str
    domain: Optional[str] = None
    item_type: Optional[str] = None  # 'CONCEPT', 'METRIC', 'ATTRIBUTE'
    limit: int = 10
    min_score: float = 0.15


class NLQueryRequest(BaseModel):
    prompt: str
    dialect: str = "SNOWFLAKE"  # 'SNOWFLAKE', 'DATABRICKS', 'DUCKDB'


@router.get("/status", response_model=Dict[str, Any])
def get_ai_status() -> Dict[str, Any]:
    """Retrieve indexing statistics and status of the embedded AI engine."""
    index = ontology_store.search_index
    fed = ontology_store.get_federated_ontology()

    return {
        "status": "ready",
        "engine": "SemaBridge Hybrid Vector & BM25",
        "total_indexed_documents": len(index.docs),
        "total_domains": len(fed.domains),
        "total_concepts": len(fed.concepts),
        "total_metrics": len(fed.metrics),
        "vector_dimensions": index.vector_dim,
        "credit_cost": "0 (Embedded Local AI)",
    }


@router.post("/search", response_model=List[SearchHit])
def search_semantic_layer(req: AISearchRequest) -> List[SearchHit]:
    """Search business concepts, metrics, formulas, and synonyms using hybrid vector + BM25 search."""
    if not req.query or not req.query.strip():
        raise HTTPException(status_code=400, detail="Search query cannot be empty.")

    hits = ontology_store.search_index.search(
        query=req.query,
        domain_filter=req.domain,
        item_type_filter=req.item_type,
        limit=req.limit,
        min_score=req.min_score,
    )
    return hits


@router.post("/nl-query", response_model=QueryResolutionPlan)
def resolve_natural_language_query(req: NLQueryRequest) -> QueryResolutionPlan:
    """Translate a natural language question into governed SQL using the enterprise ontology knowledge graph."""
    if not req.prompt or not req.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty.")

    engine = ontology_store.query_engine
    if not engine:
        raise HTTPException(status_code=500, detail="NL Query Engine not initialized.")

    plan = engine.resolve_query(req.prompt, dialect=req.dialect)
    return plan


@router.post("/reindex", response_model=Dict[str, Any])
def reindex_search_engine() -> Dict[str, Any]:
    """Manually trigger a full re-index of the semantic search engine."""
    ontology_store._refresh_index()
    return {
        "success": True,
        "indexed_documents": len(ontology_store.search_index.docs),
    }
