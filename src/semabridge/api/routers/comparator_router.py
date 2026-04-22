"""
Semantic Comparator API Router
==============================
Provides YAML parsing, structural diff (Level 1), and LLM semantic comparison (Level 2)
for semantic model files in OSI, SML, TSML, and Snowflake YAML dialects.

Industry patterns applied:
  - Format detection: structural-marker sniffing (OSI v1.0 spec, Yamale approach)
  - Diff algorithm: set-theoretic comparison with field-level change tracking (dbt state:modified)
  - LLM integration: FastAPI optional Dependency Injection (not module-level singleton)
  - LLM prompt: LLM-as-a-Judge with XML delimiters + JSON contract + validate-and-repair
  - Error handling: 503 for missing provider key (not 422 or 500)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any

import yaml
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/comparator", tags=["Comparator"])

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class CompareRequest(BaseModel):
    file1_name: str
    file1_content: str
    file2_name: str
    file2_content: str


class SemanticCompareRequest(BaseModel):
    metric1_name: str
    metric1_definition: str
    metric2_name: str
    metric2_definition: str
    provider: str = "google"
    model: str = "gemini-1.5-flash"


# ---------------------------------------------------------------------------
# LLM routing — direct SDK calls, no aisuite import overhead
# ---------------------------------------------------------------------------
#
# Architecture: every provider supported here uses the OpenAI-compatible API
# (openai SDK + provider-specific base_url). Only Anthropic uses its own SDK.
# This avoids importing aisuite[all] which pulls in 20+ heavy SDKs (torch,
# google-cloud-aiplatform, etc.) and hangs the backend on every request.
#
# Provider → (env_var_name, base_url | None)
_OPENAI_COMPAT_PROVIDERS: dict[str, tuple[str, str | None]] = {
    "openai":     ("OPENAI_API_KEY",     None),                                                           # default OpenAI endpoint
    "groq":       ("GROQ_API_KEY",       "https://api.groq.com/openai/v1"),
    "cerebras":   ("CEREBRAS_API_KEY",   "https://api.cerebras.ai/v1"),
    "mistral":    ("MISTRAL_API_KEY",    "https://api.mistral.ai/v1"),
    "sambanova":  ("SAMBANOVA_API_KEY",  "https://api.sambanova.ai/v1"),
    "openrouter": ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1"),
    "google":     ("GOOGLE_API_KEY",     "https://generativelanguage.googleapis.com/v1beta/openai/"),
}

_ALL_PROVIDER_KEYS: list[str] = [
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY",
    "MISTRAL_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY",
    "SAMBANOVA_API_KEY", "OPENROUTER_API_KEY",
]

# Sentinel returned when at least one key is configured.
# The actual client is created per-request inside semantic_compare so
# no heavy imports happen at dependency-injection time.
_LLM_SENTINEL = object()


def _get_ai_client() -> object | None:
    """
    FastAPI dependency: fast sentinel check — no SDK imports, no blocking I/O.

    Returns the _LLM_SENTINEL object if at least one provider API key is
    configured in the environment, otherwise returns None triggering a 503.

    Returns:
        _LLM_SENTINEL if any provider key is set, None otherwise.
    """
    if any(os.environ.get(k) for k in _ALL_PROVIDER_KEYS):
        return _LLM_SENTINEL
    return None


def _call_llm(
    provider: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.0,
) -> str:
    """
    Route an LLM chat-completion request to the correct provider SDK.

    Uses the OpenAI SDK (with provider-specific base_url) for all OpenAI-
    compatible providers, and the Anthropic SDK for Anthropic directly.

    Args:
        provider: Provider key (e.g. 'groq', 'google', 'anthropic').
        model: Model name as expected by the provider API.
        messages: List of {"role": ..., "content": ...} dicts.
        temperature: Sampling temperature (0.0 = deterministic).

    Returns:
        The raw text content of the LLM response.

    Raises:
        HTTPException: 503 if the required env key is missing,
                       400 if the provider is unknown.
    """
    # ── Anthropic (uses its own SDK, not OpenAI-compatible) ────────────────
    if provider == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY is not set in your .env file.")
        from anthropic import Anthropic  # lightweight, fast import
        client = Anthropic(api_key=api_key)
        system_text = next((m["content"] for m in messages if m["role"] == "system"), "")
        user_text   = next((m["content"] for m in messages if m["role"] == "user"),   "")
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            system=system_text,
            messages=[{"role": "user", "content": user_text}],
            temperature=temperature,
        )
        return response.content[0].text.strip()

    # ── All OpenAI-compatible providers ────────────────────────────────────
    if provider not in _OPENAI_COMPAT_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown LLM provider: '{provider}'.")

    env_key, base_url = _OPENAI_COMPAT_PROVIDERS[provider]
    api_key = os.environ.get(env_key)
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail=f"{env_key} is not set in your .env file.",
        )

    from openai import OpenAI  # already installed, fast import, no heavy SDKs
    client_kwargs: dict[str, str] = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)

    response = client.chat.completions.create(
        model=model,
        messages=messages,  # type: ignore[arg-type]
        temperature=temperature,
    )
    return response.choices[0].message.content.strip()

# ---------------------------------------------------------------------------
# Semantic Parsers (Adapter Pattern)
# ---------------------------------------------------------------------------

from abc import ABC, abstractmethod

def _empty_normalized() -> dict:
    return {"format": "unknown", "tables": {}, "columns": {}, "metrics": {}, "relationships": {}}

class BaseSemanticAdapter(ABC):
    @classmethod
    @abstractmethod
    def handles(cls, data: dict) -> bool:
        pass
        
    @classmethod
    @abstractmethod
    def parse(cls, data: dict) -> dict:
        pass

class OsiAdapter(BaseSemanticAdapter):
    @classmethod
    def handles(cls, data: dict) -> bool:
        return "datasets" in data and "metrics" in data
        
    @classmethod
    def parse(cls, data: dict) -> dict:
        norm = _empty_normalized()
        norm["format"] = "OSI"
        for ds in data.get("datasets", []):
            if not isinstance(ds, dict): continue
            tbl_name = ds.get("unique_name", ds.get("name", "Unknown"))
            cols = ds.get("columns", [])
            norm["tables"][tbl_name] = {
                "name": tbl_name, "column_count": len(cols), "metric_count": 0, "relationship_count": 0
            }
            for col in cols:
                if not isinstance(col, dict): continue
                col_name = col.get("unique_name", col.get("name", "Unknown"))
                norm["columns"][f"{tbl_name}.{col_name}"] = {
                    "name": col_name, "table": tbl_name, "type": col.get("data_type", col.get("type", "Unknown")), "is_key": col.get("is_key", False)
                }

        for m in data.get("metrics", []):
            if not isinstance(m, dict): continue
            m_name = m.get("unique_name", m.get("name", "Unknown"))
            m_table = m.get("dataset", m.get("table", "Unknown"))
            m_expr = m.get("expression", m.get("expr", m.get("source_column", "Unknown")))
            m_agg = m.get("aggregation", "")
            definition = f"{m_agg}({m_expr})" if m_agg else str(m_expr)
            if m_table in norm["tables"]: norm["tables"][m_table]["metric_count"] += 1
            norm["metrics"][f"{m_table}.{m_name}"] = {"name": m_name, "table": m_table, "definition": definition, "description": m.get("description", "")}

        for r in data.get("relationships", []):
            if not isinstance(r, dict): continue
            r_name = r.get("unique_name", r.get("name", "Unknown"))
            left = r.get("from_dataset", r.get("left_table", "Unknown"))
            right = r.get("to_dataset", r.get("right_table", "Unknown"))
            l_cols = r.get("from_columns", r.get("left_columns", ["Unknown"]))
            r_cols = r.get("to_columns", r.get("right_columns", ["Unknown"]))
            if left in norm["tables"]: norm["tables"][left]["relationship_count"] += 1
            norm["relationships"][r_name] = {
                "name": r_name, "left_table": left, "right_table": right,
                "left_column": l_cols[0] if isinstance(l_cols, list) else str(l_cols),
                "right_column": r_cols[0] if isinstance(r_cols, list) else str(r_cols),
                "cardinality": r.get("cardinality", "Unknown")
            }
        return norm

class TsmlAdapter(BaseSemanticAdapter):
    @classmethod
    def handles(cls, data: dict) -> bool:
        model = data.get("model", {})
        return isinstance(model, dict) and "tables" in model

    @classmethod
    def parse(cls, data: dict) -> dict:
        norm = _empty_normalized()
        norm["format"] = "TSML"
        model = data.get("model", {})
        for tbl in model.get("tables", []):
            if not isinstance(tbl, dict): continue
            tbl_name = tbl.get("name", "Unknown")
            cols, measures = tbl.get("columns", []), tbl.get("measures", [])
            norm["tables"][tbl_name] = {
                "name": tbl_name, "column_count": len(cols), "metric_count": len(measures), "relationship_count": 0
            }
            for col in cols:
                if not isinstance(col, dict): continue
                col_name = col.get("name", "Unknown")
                norm["columns"][f"{tbl_name}.{col_name}"] = {"name": col_name, "table": tbl_name, "type": col.get("dataType", col.get("type", "Unknown")), "is_key": col.get("isKey", False)}
            for m in measures:
                if not isinstance(m, dict): continue
                m_name = m.get("name", "Unknown")
                norm["metrics"][f"{tbl_name}.{m_name}"] = {"name": m_name, "table": tbl_name, "definition": str(m.get("expression", "Unknown")), "description": m.get("description", "")}
        
        for r in model.get("relationships", []):
            if not isinstance(r, dict): continue
            left = r.get("fromTable", "Unknown")
            if left in norm["tables"]: norm["tables"][left]["relationship_count"] += 1
            r_name = r.get("name", f"{left}-{r.get('toTable')}")
            norm["relationships"][r_name] = {"name": r_name, "left_table": left, "right_table": r.get("toTable", "Unknown"), "left_column": r.get("fromColumn", "Unknown"), "right_column": r.get("toColumn", "Unknown"), "cardinality": r.get("crossFilteringBehavior", "Unknown")}
        return norm

class SnowflakeAdapter(BaseSemanticAdapter):
    @classmethod
    def handles(cls, data: dict) -> bool:
        if "semantic_model" in data: return True
        tables = data.get("tables", [])
        return isinstance(tables, list) and tables and any(isinstance(t, dict) and ("base_table" in t or "facts" in t) for t in tables)

    @classmethod
    def parse(cls, data: dict) -> dict:
        norm = _empty_normalized()
        norm["format"] = "SNOWFLAKE"
        root = data.get("semantic_model", data)
        for tbl in root.get("tables", []):
            if not isinstance(tbl, dict): continue
            tbl_name = tbl.get("name", "Unknown")
            # Cortex Analyst uses 'dimensions' + 'facts'; some dialects use 'columns'
            dimensions = tbl.get("dimensions", [])
            facts = tbl.get("facts", [])
            columns = tbl.get("columns", [])
            all_cols = dimensions + facts + columns
            # Cortex Analyst stores measures under 'measures'; older formats use 'metrics'
            all_measures = tbl.get("measures", tbl.get("metrics", []))
            norm["tables"][tbl_name] = {
                "name": tbl_name,
                "column_count": len(all_cols),
                "metric_count": len(all_measures),
                "relationship_count": 0
            }
            for col in all_cols:
                if not isinstance(col, dict): continue
                col_name = col.get("name", "Unknown")
                # Cortex Analyst uses 'expr' (column expression), not 'data_type'
                dtype = col.get("data_type", col.get("datatype", col.get("type", "expr")))
                norm["columns"][f"{tbl_name}.{col_name}"] = {
                    "name": col_name,
                    "table": tbl_name,
                    "type": dtype,
                    "is_key": col_name in (tbl.get("primary_key") or [])
                }
            for m in all_measures:
                if not isinstance(m, dict): continue
                m_name = m.get("name", "Unknown")
                m_expr = m.get("expr", m.get("expression", m.get("sql", "NULL")))
                m_desc = m.get("description", "")
                norm["metrics"][f"{tbl_name}.{m_name}"] = {
                    "name": m_name,
                    "table": tbl_name,
                    "definition": str(m_expr),
                    "description": m_desc
                }
        
        for r in root.get("relationships", []):
            if not isinstance(r, dict): continue
            left = r.get("left_table", "Unknown")
            if left in norm["tables"]: norm["tables"][left]["relationship_count"] += 1
            l_cols, r_cols = r.get("left_columns", ["Unknown"]), r.get("right_columns", ["Unknown"])
            r_name = r.get("name", f"{left}-{r.get('right_table')}")
            norm["relationships"][r_name] = {"name": r_name, "left_table": left, "right_table": r.get("right_table", "Unknown"), "left_column": l_cols[0] if isinstance(l_cols, list) else str(l_cols), "right_column": r_cols[0] if isinstance(r_cols, list) else str(r_cols), "cardinality": r.get("relationship_type", "Unknown")}
        return norm

class AtScaleAdapter(BaseSemanticAdapter):
    """Adapter for the AtScale SML open standard (github.com/semanticdatalayer/SML).

    The official industry SML standard (Apache license, Sept 2024) uses a
    multi-file architecture where each YAML file has an ``object_type`` root key:
      - ``dataset``: physical table + columns
      - ``model``: relationships + metric/dimension references
      - ``metric``: aggregation definition (calculation_method + dataset + column)
      - ``catalog``: metadata container
      - ``dimension``: hierarchy definition
      - ``calculation``: MDX/expression definition
      - ``connection``: database connection config

    When users upload a single file, we parse that one object. When they
    upload a consolidated file (all objects merged), we parse each section.
    """

    KNOWN_OBJECT_TYPES = {
        "dataset", "model", "metric", "catalog",
        "dimension", "calculation", "connection", "row_security",
    }

    @classmethod
    def handles(cls, data: dict) -> bool:
        obj_type = str(data.get("object_type", "")).lower().strip()
        return obj_type in cls.KNOWN_OBJECT_TYPES

    @classmethod
    def parse(cls, data: dict) -> dict:
        norm = _empty_normalized()
        norm["format"] = "ATSCALE_SML"
        obj_type = str(data.get("object_type", "")).lower().strip()

        if obj_type == "dataset":
            cls._parse_dataset(data, norm)
        elif obj_type == "model":
            cls._parse_model(data, norm)
        elif obj_type == "metric":
            cls._parse_metric(data, norm)
        # catalog, connection, row_security → metadata-only, no tables/columns

        return norm

    @classmethod
    def _parse_dataset(cls, data: dict, norm: dict) -> None:
        """Parse an AtScale dataset object (physical table + columns)."""
        name = data.get("label", data.get("unique_name", "Unknown"))
        columns = data.get("columns", [])
        norm["tables"][name] = {
            "name": name,
            "column_count": len(columns),
            "metric_count": 0,
            "relationship_count": 0,
        }
        for col in columns:
            if not isinstance(col, dict):
                continue
            cname = col.get("name", "Unknown")
            dtype = col.get("data_type", col.get("type", "Unknown"))
            norm["columns"][f"{name}.{cname}"] = {
                "name": cname,
                "table": name,
                "type": dtype,
                "is_key": False,
            }

    @classmethod
    def _parse_model(cls, data: dict, norm: dict) -> None:
        """Parse an AtScale model object (relationships + metric/dimension refs).

        AtScale model relationships use:
          from: {dataset: ..., join_columns: [...]}
          to: {dimension: ..., level: ...}
        """
        model_name = data.get("label", data.get("unique_name", "Model"))

        for rel in data.get("relationships", []):
            if not isinstance(rel, dict):
                continue
            r_name = rel.get("unique_name", "Unknown")
            from_info = rel.get("from", {})
            to_info = rel.get("to", {})
            from_ds = from_info.get("dataset", "Unknown")
            to_target = to_info.get("dimension", to_info.get("dataset", "Unknown"))
            join_cols = from_info.get("join_columns", [])

            # Ensure source table exists in the tables dict
            if from_ds not in norm["tables"]:
                norm["tables"][from_ds] = {
                    "name": from_ds, "column_count": 0,
                    "metric_count": 0, "relationship_count": 0,
                }
            norm["tables"][from_ds]["relationship_count"] += 1

            norm["relationships"][r_name] = {
                "name": r_name,
                "left_table": from_ds,
                "right_table": to_target,
                "left_column": join_cols[0] if join_cols else "Unknown",
                "right_column": to_info.get("level", "Unknown"),
                "cardinality": rel.get("role_play", "Unknown"),
            }

        # Metrics in model files are references (unique_name + folder)
        for m in data.get("metrics", []):
            if isinstance(m, dict):
                mname = m.get("unique_name", m.get("name", "Unknown"))
                folder = m.get("folder", "")
                norm["metrics"][mname] = {
                    "name": mname,
                    "table": model_name,
                    "definition": folder,
                    "description": "",
                }
            elif isinstance(m, str):
                norm["metrics"][m] = {
                    "name": m,
                    "table": model_name,
                    "definition": "",
                    "description": "",
                }

        # Dimensions in model files are string references
        for d in data.get("dimensions", []):
            if isinstance(d, str):
                dim_name = d
                if dim_name not in norm["tables"]:
                    norm["tables"][dim_name] = {
                        "name": dim_name, "column_count": 0,
                        "metric_count": 0, "relationship_count": 0,
                    }

    @classmethod
    def _parse_metric(cls, data: dict, norm: dict) -> None:
        """Parse an AtScale metric object (aggregation definition)."""
        name = data.get("label", data.get("unique_name", "Unknown"))
        dataset = data.get("dataset", "Unknown")
        column = data.get("column", "Unknown")
        method = data.get("calculation_method", "Unknown")

        if dataset not in norm["tables"]:
            norm["tables"][dataset] = {
                "name": dataset, "column_count": 0,
                "metric_count": 0, "relationship_count": 0,
            }
        norm["tables"][dataset]["metric_count"] += 1
        norm["metrics"][name] = {
            "name": name,
            "table": dataset,
            "definition": f"{method}({column})",
            "description": data.get("description", ""),
        }


class CubeAdapter(BaseSemanticAdapter):
    @classmethod
    def handles(cls, data: dict) -> bool:
        return "cubes" in data or "views" in data or "cube" in data or "view" in data

    @classmethod
    def parse(cls, data: dict) -> dict:
        norm = _empty_normalized()
        norm["format"] = "CUBE"
        cubes = data.get("cubes", data.get("views", []))
        if isinstance(data.get("cube"), dict): cubes = [data["cube"]]
        if isinstance(data.get("view"), dict): cubes = [data["view"]]
        for c in cubes:
            if not isinstance(c, dict): continue
            tbl_name = c.get("name", "Unknown")
            norm["tables"][tbl_name] = {"name": tbl_name, "column_count": 0, "metric_count": 0, "relationship_count": 0}
            for d in c.get("dimensions", []):
                d_name = d.get("name", "Unknown")
                norm["tables"][tbl_name]["column_count"] += 1
                norm["columns"][f"{tbl_name}.{d_name}"] = {"name": d_name, "table": tbl_name, "type": d.get("type", "string"), "is_key": str(d.get("primary_key")).lower()=="true"}
            for m in c.get("measures", []):
                m_name = m.get("name", "Unknown")
                norm["tables"][tbl_name]["metric_count"] += 1
                norm["metrics"][f"{tbl_name}.{m_name}"] = {"name": m_name, "table": tbl_name, "definition": str(m.get("sql", "Unknown")), "description": m.get("description", "")}
            for j in c.get("joins", []):
                j_name = j.get("name", "Unknown")
                norm["tables"][tbl_name]["relationship_count"] += 1
                norm["relationships"][j_name] = {"name": j_name, "left_table": tbl_name, "right_table": j_name, "left_column": "Unknown", "right_column": "Unknown", "cardinality": j.get("relationship", "Unknown")}
        return norm

class DbtAdapter(BaseSemanticAdapter):
    @classmethod
    def handles(cls, data: dict) -> bool:
        if "semantic_models" in data: return True
        metrics = data.get("metrics", [])
        if isinstance(metrics, list) and metrics:
            # Check if metrics look like dbt MetricFlow (they often have 'type_params')
            return any(isinstance(m, dict) and "type_params" in m for m in metrics)
        return False

    @classmethod
    def parse(cls, data: dict) -> dict:
        norm = _empty_normalized()
        norm["format"] = "DBT"
        for sm in data.get("semantic_models", []):
            if not isinstance(sm, dict): continue
            tbl_name = sm.get("name", "Unknown")
            norm["tables"][tbl_name] = {"name": tbl_name, "column_count": 0, "metric_count": 0, "relationship_count": 0}
            for d in sm.get("dimensions", []) + sm.get("entities", []):
                d_name = d.get("name", "Unknown")
                norm["tables"][tbl_name]["column_count"] += 1
                norm["columns"][f"{tbl_name}.{d_name}"] = {"name": d_name, "table": tbl_name, "type": d.get("type", "Unknown"), "is_key": d.get("type") == "primary"}
            for mea in sm.get("measures", []):
                m_name = mea.get("name", "Unknown")
                norm["tables"][tbl_name]["metric_count"] += 1
                norm["metrics"][f"{tbl_name}.{m_name}"] = {"name": m_name, "table": tbl_name, "definition": str(mea.get("expr", "Unknown")), "description": mea.get("description", "")}
        # Standalone metrics inside dbt Metricflow
        for m in data.get("metrics", []):
            if not isinstance(m, dict): continue
            m_name = m.get("name", "Unknown")
            if "semantic_models" not in data: # It's just a metric file
                tbl_name = m.get("type_params", {}).get("measure", {}).get("name", "Unknown")
                if tbl_name not in norm["tables"]:
                    norm["tables"][tbl_name] = {"name": tbl_name, "column_count": 0, "metric_count": 0, "relationship_count": 0}
                norm["tables"][tbl_name]["metric_count"] += 1
                expr = m.get("type_params", {}).get("expr", m.get("expression", m.get("expr", m.get("sql", m.get("description", "Unknown")))))
                norm["metrics"][f"{tbl_name}.{m_name}"] = {"name": m_name, "table": tbl_name, "definition": str(expr)}
        return norm

class SmlAdapter(BaseSemanticAdapter):
    @classmethod
    def handles(cls, data: dict) -> bool:
        root = data.get("datasets", data.get("model", data))
        if isinstance(root, list):
            return any(isinstance(r, dict) and ("metrics" in r or "measures" in r) for r in root)
        if isinstance(root, dict):
            return "metrics" in root or "measures" in root
        return False

    @classmethod
    def parse(cls, data: dict) -> dict:
        norm = _empty_normalized()
        norm["format"] = "SML"
        root = data.get("datasets", data.get("model", data))
        if not isinstance(root, list):
            if isinstance(root, dict) and "tables" in root:
                root = root["tables"]
            elif isinstance(root, dict) and "datasets" in root:
                root = root["datasets"]
            else:
                root = [root]

        for ds in root:
            if not isinstance(ds, dict): continue
            tbl_name = ds.get("unique_name", ds.get("name", "Unknown"))
            cols = ds.get("columns", [])
            norm["tables"][tbl_name] = {"name": tbl_name, "column_count": len(cols), "metric_count": 0, "relationship_count": 0}
            for col in cols:
                if not isinstance(col, dict): continue
                col_name = col.get("unique_name", col.get("name", "Unknown"))
                norm["columns"][f"{tbl_name}.{col_name}"] = {"name": col_name, "table": tbl_name, "type": col.get("data_type", "Unknown"), "is_key": col.get("is_key", False)}

            metrics = ds.get("metrics", ds.get("measures", []))
            if isinstance(metrics, dict): metrics = [{"name": k, **(v if isinstance(v, dict) else {})} for k, v in metrics.items()]
            for m in metrics:
                if not isinstance(m, dict): continue
                m_name = m.get("unique_name", m.get("name", "Unknown"))
                norm["tables"][tbl_name]["metric_count"] += 1
                norm["metrics"][f"{tbl_name}.{m_name}"] = {"name": m_name, "table": tbl_name, "definition": str(m.get("expression", m.get("expr", m.get("sql", "Unknown")))), "description": m.get("description", "")}
                
            for r in ds.get("relationships", []):
                if not isinstance(r, dict): continue
                r_name = r.get("name", r.get("unique_name", f"rel_{tbl_name}"))
                right = r.get("to", r.get("right_table", r.get("destination", "Unknown")))
                norm["tables"][tbl_name]["relationship_count"] += 1
                norm["relationships"][r_name] = {"name": r_name, "left_table": tbl_name, "right_table": right, "left_column": r.get("on", "Unknown"), "right_column": r.get("right_column", "Unknown"), "cardinality": r.get("cardinality", "Unknown")}

        return norm


class SemabridgeDDLAdapter(BaseSemanticAdapter):
    """Adapter for Semabridge's own DDL audit export (semantic_view_ddl.yaml / *_SML.yaml).

    This format is generated by ``snowflake_emitter.deploy()`` (Step 3b) and contains:
      - ``metadata``: model_name, path='SML', generated_at, ddl_count
      - ``relationships``: structured list with from_dataset/to_dataset/from_columns/to_columns
      - ``ddl_statements``: list of raw Snowflake DDL strings containing
        ``TABLES (...)``, ``DIMENSIONS (...)``, ``MEASURES (...)`` clauses

    The adapter parses the DDL string using regex to extract tables, columns, and metrics.
    """

    @classmethod
    def handles(cls, data: dict) -> bool:
        meta = data.get("metadata", {})
        return (
            isinstance(meta, dict)
            and meta.get("path") == "SML"
            and "ddl_statements" in data
        )

    @classmethod
    def parse(cls, data: dict) -> dict:
        norm = _empty_normalized()
        norm["format"] = "SEMABRIDGE_SML"

        # --- 1. Extract relationships from structured YAML ---
        for rel in data.get("relationships", []):
            if not isinstance(rel, dict):
                continue
            r_name = rel.get("name", "Unknown")
            from_ds = rel.get("from_dataset", "Unknown")
            to_ds = rel.get("to_dataset", "Unknown")
            from_cols = rel.get("from_columns", [])
            to_cols = rel.get("to_columns", [])
            norm["relationships"][r_name] = {
                "name": r_name,
                "left_table": from_ds,
                "right_table": to_ds,
                "left_column": from_cols[0] if from_cols else "Unknown",
                "right_column": to_cols[0] if to_cols else "Unknown",
                "cardinality": str(rel.get("cardinality", "Unknown")),
            }
            # Track relationship counts per table
            if from_ds not in norm["tables"]:
                norm["tables"][from_ds] = {"name": from_ds, "column_count": 0, "metric_count": 0, "relationship_count": 0}
            norm["tables"][from_ds]["relationship_count"] += 1

        # --- 2. Parse DDL strings for TABLES, DIMENSIONS, MEASURES ---
        for ddl in data.get("ddl_statements", []):
            if not isinstance(ddl, str):
                continue
            cls._parse_ddl_string(ddl, norm)

        return norm

    @classmethod
    def _parse_ddl_string(cls, ddl: str, norm: dict) -> None:
        """Parse a Snowflake CREATE SEMANTIC VIEW DDL to extract tables, columns, metrics.

        The DDL follows the pattern:
            CREATE ... SEMANTIC VIEW ...
            TABLES ( ALIAS AS "DB"."SCHEMA"."TABLE" PRIMARY KEY (...), ... )
            RELATIONSHIPS ( ... )
            DIMENSIONS ( ALIAS."COL" AS ALIAS."COL", ... )
            MEASURES ( ALIAS."METRIC" AS SUM(ALIAS."COL"), ... )
        """
        # Extract TABLES block: find table aliases
        # The TABLES block ends when RELATIONSHIPS, DIMENSIONS, MEASURES, or METRICS starts
        tables_match = re.search(r'TABLES\s*\((.*?)\)\s*(?:RELATIONSHIPS|DIMENSIONS|MEASURES|METRICS)', ddl, re.DOTALL | re.IGNORECASE)
        if tables_match:
            tables_block = tables_match.group(1)
            # Match patterns like: ALIAS AS "DB"."SCHEMA"."TABLE" PRIMARY KEY (...)
            # or simpler: ALIAS AS "DB"."SCHEMA"."TABLE"
            for m in re.finditer(
                r'(\w+)\s+AS\s+"[^"]*"\."[^"]*"\."([^"]*)"',
                tables_block
            ):
                alias = m.group(1)
                physical_table = m.group(2)
                if alias not in norm["tables"]:
                    norm["tables"][alias] = {
                        "name": alias,
                        "column_count": 0,
                        "metric_count": 0,
                        "relationship_count": 0,
                    }

        # Extract DIMENSIONS block → columns
        # Allow termination by MEASURES/METRICS block, or by `);` or `)` at end
        dims_match = re.search(r'DIMENSIONS\s*\((.*?)(?:\)\s*(?:MEASURES|METRICS|;|$))', ddl, re.DOTALL | re.IGNORECASE)
        if dims_match:
            dims_block = dims_match.group(1)
            # Match patterns like: ALIAS."COL_NAME" AS ALIAS."COL_NAME"
            for m in re.finditer(r'(\w+)\."([^"]+)"\s+AS\s+\w+\."[^"]+"', dims_block):
                table_alias = m.group(1)
                col_name = m.group(2)
                if table_alias not in norm["tables"]:
                    norm["tables"][table_alias] = {"name": table_alias, "column_count": 0, "metric_count": 0, "relationship_count": 0}
                norm["tables"][table_alias]["column_count"] += 1
                norm["columns"][f"{table_alias}.{col_name}"] = {
                    "name": col_name,
                    "table": table_alias,
                    "type": "expr",
                }

        # Extract MEASURES/METRICS block → metrics
        # Snowflake DDL can use either MEASURES or METRICS keyword
        measures_match = re.search(r'(?:MEASURES|METRICS)\s*\((.*?)(?:\)\s*;|\)\s*$)', ddl, re.DOTALL | re.IGNORECASE)
        if measures_match:
            measures_block = measures_match.group(1)
            # Parse each metric line: ALIAS."METRIC_NAME" AS expression
            # Split by line and process each individually for robustness
            for line in measures_block.split('\n'):
                line = line.strip().rstrip(',')
                if not line:
                    continue
                line_match = re.match(r'(\w+)\."([^"]+)"\s+AS\s+(.*)', line)
                if line_match:
                    table_alias = line_match.group(1)
                    metric_name = line_match.group(2)
                    expression = line_match.group(3).strip().rstrip(',')
                    if table_alias not in norm["tables"]:
                        norm["tables"][table_alias] = {"name": table_alias, "column_count": 0, "metric_count": 0, "relationship_count": 0}
                    norm["tables"][table_alias]["metric_count"] += 1
                    norm["metrics"][f"{table_alias}.{metric_name}"] = {
                        "name": metric_name,
                        "table": table_alias,
                        "definition": expression,
                        "description": "",
                    }

        # If no TABLES/DIMENSIONS/MEASURES were found, the DDL might have a
        # different structure. Fall back to extracting table names from relationships.
        if not norm["tables"]:
            for rel_data in norm["relationships"].values():
                for tbl in [rel_data["left_table"], rel_data["right_table"]]:
                    if tbl not in norm["tables"]:
                        norm["tables"][tbl] = {"name": tbl, "column_count": 0, "metric_count": 0, "relationship_count": 0}


class GenericAdapter(BaseSemanticAdapter):
    @classmethod
    def handles(cls, data: dict) -> bool:
        return True

    @classmethod
    def parse(cls, data: dict) -> dict:
        norm = _empty_normalized()
        norm["format"] = "GENERIC"

        def _recursive_extract(node, current_table=None):
            if isinstance(node, dict):
                k_lower = {k.lower(): v for k, v in node.items()}
                
                tables_arr = k_lower.get("tables", k_lower.get("datasets", k_lower.get("entities", k_lower.get("models"))))
                if isinstance(tables_arr, list):
                    for tbl in tables_arr:
                        if isinstance(tbl, dict):
                            tname = tbl.get("name", tbl.get("unique_name", "Unknown"))
                            if tname not in norm["tables"]:
                                norm["tables"][tname] = {"name": tname, "column_count": 0, "metric_count": 0, "relationship_count": 0}
                            _recursive_extract(tbl, tname)

                # Fallback: If we find columns or metrics but have no current_table, assign to a global Root table
                cols_arr = k_lower.get("columns", k_lower.get("dimensions", k_lower.get("fields")))
                mets_arr = k_lower.get("metrics", k_lower.get("measures"))
                
                if (cols_arr or mets_arr) and not current_table:
                    current_table = node.get("name", node.get("unique_name", "Root_Entity"))
                    if current_table not in norm["tables"]:
                        norm["tables"][current_table] = {"name": current_table, "column_count": 0, "metric_count": 0, "relationship_count": 0}

                if isinstance(cols_arr, list) and current_table:
                    for col in cols_arr:
                        if isinstance(col, dict):
                            cname = col.get("name", col.get("unique_name", "Unknown"))
                            norm["tables"][current_table]["column_count"] += 1
                            norm["columns"][f"{current_table}.{cname}"] = {"name": cname, "table": current_table, "type": str(col.get("type", col.get("data_type", "Unknown")))}
                elif isinstance(cols_arr, dict) and current_table:
                    for cname, cdef in cols_arr.items():
                        norm["tables"][current_table]["column_count"] += 1
                        norm["columns"][f"{current_table}.{cname}"] = {"name": cname, "table": current_table, "type": "Unknown"}

                if isinstance(mets_arr, list) and current_table:
                    for met in mets_arr:
                        if isinstance(met, dict):
                            mname = met.get("name", met.get("unique_name", "Unknown"))
                            norm["tables"][current_table]["metric_count"] += 1
                            norm["metrics"][f"{current_table}.{mname}"] = {"name": mname, "table": current_table, "definition": str(met.get("expr", met.get("expression", met.get("sql", "Unknown")))), "description": ""}
                elif isinstance(mets_arr, dict) and current_table:
                    for mname, mdef in mets_arr.items():
                        mexpr = str(mdef.get("expr", mdef.get("formula", "Unknown"))) if isinstance(mdef, dict) else str(mdef)
                        norm["tables"][current_table]["metric_count"] += 1
                        norm["metrics"][f"{current_table}.{mname}"] = {"name": mname, "table": current_table, "definition": mexpr, "description": ""}
                        
                for v in node.values():
                    if isinstance(v, (dict, list)):
                        _recursive_extract(v, current_table)
            elif isinstance(node, list):
                for item in node:
                    _recursive_extract(item, current_table)

        _recursive_extract(data)
        
        if not norm["tables"]:
            norm["tables"]["Unknown"] = {"name": "Unknown", "column_count": len(norm["columns"]), "metric_count": len(norm["metrics"]), "relationship_count": 0}
            
        return norm

class SemanticRegistry:
    ADAPTERS = [
        OsiAdapter,
        TsmlAdapter,
        SnowflakeAdapter,
        AtScaleAdapter,
        CubeAdapter,
        DbtAdapter,
        SmlAdapter,
        SemabridgeDDLAdapter,
        GenericAdapter
    ]

    @classmethod
    def parse_to_normalized(cls, content: str) -> dict:
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid YAML: {exc}") from exc

        if not isinstance(data, dict):
            raise ValueError("YAML root must be a mapping/object (got a list or scalar).")

        for adapter in cls.ADAPTERS:
            if adapter.handles(data):
                return adapter.parse(data)
        
        return GenericAdapter.parse(data)

def parse_yaml_to_normalized(content: str) -> dict:
    """
    Public API wrapper for backwards compatibility with endpoints.
    """
    return SemanticRegistry.parse_to_normalized(content)


# ---------------------------------------------------------------------------
# Diff engine (dbt state:modified field-level comparison pattern)
# ---------------------------------------------------------------------------

def _fingerprint(obj: dict) -> str:
    """
    Deterministic content hash of an entity dict.

    Industry pattern: dbt manifest fingerprinting — compare structural content,
    not serialised string equality, to avoid key-ordering false-positives.

    Args:
        obj: Entity dictionary to hash.

    Returns:
        MD5 hex digest of the JSON-serialised object with sorted keys.
    """
    serialised = json.dumps(
        {k: v for k, v in obj.items() if not k.startswith("_")},
        sort_keys=True,
        default=str,
    )
    return hashlib.md5(serialised.encode()).hexdigest()


def compare_entities(dict1: dict, dict2: dict, entity_type: str = "generic") -> list:
    """
    Set-theoretic diff between two entity dicts.

    Per the spec, 'modified' entities are split into two filter-able states:
      modified_in_1 : the File 1 (old) version of the entity
      modified_in_2 : the File 2 (new) version of the entity

    This allows users to filter to see exactly what File 1 had vs what File 2 changed it to.

    Full status taxonomy:
      identical     : same fingerprint in both files
      only_in_1     : present in file 1, absent from file 2
      only_in_2     : present in file 2, absent from file 1
      modified_in_1 : entity exists in both, but File 1's version (the 'before' state)
      modified_in_2 : entity exists in both, but File 2's version (the 'after' state)

    For 'metrics' entity type, also stores '_old_definition' / '_new_definition'
    as isolated strings to feed directly into the LLM compare endpoint.

    Args:
        dict1:       Entities from file 1, keyed by entity id.
        dict2:       Entities from file 2, keyed by entity id.
        entity_type: 'tables' | 'columns' | 'metrics' | 'relationships'

    Returns:
        List of entity dicts, each annotated with '_diff_status', '_id',
        and '_changes' (for modified entities).
    """
    all_keys = set(dict1.keys()) | set(dict2.keys())
    results: list[dict] = []

    for key in sorted(all_keys):
        in_1 = key in dict1
        in_2 = key in dict2

        if in_1 and not in_2:
            item = dict1[key].copy()
            item["_diff_status"] = "only_in_1"
            item["_id"] = key
            results.append(item)

        elif in_2 and not in_1:
            item = dict2[key].copy()
            item["_diff_status"] = "only_in_2"
            item["_id"] = key
            results.append(item)

        else:
            e1, e2 = dict1[key], dict2[key]

            if _fingerprint(e1) == _fingerprint(e2):
                item = e2.copy()
                item["_diff_status"] = "identical"
                item["_id"] = key
                results.append(item)
            else:
                # Field-level change tracking (dbt manifest approach)
                all_fields = set(e1.keys()) | set(e2.keys())
                changes = [
                    {
                        "field": field,
                        "old_value": e1.get(field),
                        "new_value": e2.get(field),
                    }
                    for field in sorted(all_fields)
                    if not field.startswith("_") and e1.get(field) != e2.get(field)
                ]

                # Spec requirement: produce two separate filter-able rows per modified entity.
                # modified_in_1 = what File 1 had (the 'before' state)
                # modified_in_2 = what File 2 has (the 'after' state)

                item_f1 = e1.copy()
                item_f1["_diff_status"] = "modified_in_1"
                item_f1["_id"] = key + "::f1"   # unique per row; ::f1 suffix lets the UI share one LLM result per base key
                item_f1["_base_id"] = key        # base name used to correlate both rows for LLM state
                item_f1["_changes"] = changes

                item_f2 = e2.copy()
                item_f2["_diff_status"] = "modified_in_2"
                item_f2["_id"] = key + "::f2"   # unique per row
                item_f2["_base_id"] = key        # same base key → shared LLM verdict between both cards
                item_f2["_changes"] = changes

                # Isolated definition strings for LLM endpoint (no stringified dicts)
                if entity_type == "metrics":
                    item_f1["_old_definition"] = e1.get("definition", "")
                    item_f1["_new_definition"] = e2.get("definition", "")
                    item_f2["_old_definition"] = e1.get("definition", "")
                    item_f2["_new_definition"] = e2.get("definition", "")

                results.append(item_f1)
                results.append(item_f2)

    return results


# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------

@router.post("/parse")
async def parse_single_yaml(file: UploadFile = File(...)):
    """
    Parse a single YAML file and return statistics.

    Accepts any of: OSI, SML, TSML, Snowflake Cortex semantic YAML.
    Auto-detects format via structural-marker sniffing.

    Args:
        file: Uploaded YAML file (must end in .yaml or .yml).

    Returns:
        dict with 'format', 'summary', 'tables', 'columns', 'metrics', 'relationships'.
    """
    if not file.filename or not file.filename.endswith((".yaml", ".yml")):
        raise HTTPException(status_code=400, detail="Only .yaml / .yml files are accepted.")

    raw = await file.read()
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded.")

    try:
        norm = parse_yaml_to_normalized(content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "format": norm["format"],
        "summary": {
            "total_tables": len(norm["tables"]),
            "total_columns": len(norm["columns"]),
            "total_metrics": len(norm["metrics"]),
            "total_relationships": len(norm["relationships"]),
        },
        "tables": list(norm["tables"].values()),
        "columns": list(norm["columns"].values()),
        "metrics": list(norm["metrics"].values()),
        "relationships": list(norm["relationships"].values()),
    }


@router.post("/compare")
async def compare_yamls(req: CompareRequest):
    """
    Compare two YAML files and return a Level 1 structural diff.

    Each entity in the response is tagged with '_diff_status':
      'identical' | 'only_in_1' | 'only_in_2' | 'modified'

    For 'modified' metrics, '_old_definition' and '_new_definition' are also
    returned as isolated strings (ready for the /compare-semantic endpoint).

    Args:
        req: CompareRequest with file1/file2 names and YAML content strings.

    Returns:
        dict with file names, per-entity-type diff lists, and summary counts.
    """
    try:
        norm1 = parse_yaml_to_normalized(req.file1_content)
        norm2 = parse_yaml_to_normalized(req.file2_content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    tables_diff = compare_entities(norm1["tables"], norm2["tables"], "tables")
    columns_diff = compare_entities(norm1["columns"], norm2["columns"], "columns")
    metrics_diff = compare_entities(norm1["metrics"], norm2["metrics"], "metrics")
    rels_diff = compare_entities(norm1["relationships"], norm2["relationships"], "relationships")

    def _count(items: list, status: str) -> int:
        return sum(1 for i in items if i.get("_diff_status") == status)

    all_items = tables_diff + columns_diff + metrics_diff + rels_diff

    return {
        "file1_name": req.file1_name,
        "file1_format": norm1["format"],
        "file2_name": req.file2_name,
        "file2_format": norm2["format"],
        "summary": {
            "identical": _count(all_items, "identical"),
            "only_in_1": _count(all_items, "only_in_1"),
            "only_in_2": _count(all_items, "only_in_2"),
            "modified_in_1": _count(all_items, "modified_in_1"),
            "modified_in_2": _count(all_items, "modified_in_2"),
            "total": len(all_items),
        },
        "tables": tables_diff,
        "columns": columns_diff,
        "metrics": metrics_diff,
        "relationships": rels_diff,
    }


@router.post("/compare-semantic")
def semantic_compare(
    req: SemanticCompareRequest,
    ai_client=Depends(_get_ai_client),
):
    """
    Level 2 semantic comparison using LLM-as-a-Judge via AISuite.

    Industry pattern:
      - LLM-as-a-Judge (Monte Carlo, DataHub, DeepEval)
      - XML-delimited prompt to prevent instruction blending
      - Strict JSON contract with validate-and-repair fallback
      - Provider/model configurable (not hard-coded)
      - 503 returned for missing API key (semantically correct vs 422/500)

    Args:
        req: SemanticCompareRequest with definitions + optional provider/model.
        ai_client: Injected AISuite client (None if no key is configured).

    Returns:
        dict with 'verdict', 'confidence', 'reasoning', 'key_differences', 'model_used'.
    """
    if ai_client is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "LLM service unavailable. Please configure at least one provider API key in your .env: "
                "GOOGLE_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, MISTRAL_API_KEY, GROQ_API_KEY, "
                "CEREBRAS_API_KEY, SAMBANOVA_API_KEY, or OPENROUTER_API_KEY."
            ),
        )

    # model_id is used only for logging and the model_used response field.
    model_id = f"{req.provider}:{req.model}"

    # LLM-as-a-Judge: XML delimiters + strict JSON output contract (2025 prompt engineering standard)
    system_prompt = (
        "You are a senior data engineering expert evaluating semantic model metric definitions. "
        "Always respond with strictly valid JSON only. Do not include markdown, code fences, or explanatory text."
    )

    user_prompt = f"""Compare these two metric definitions to determine if they represent the same business calculation.

<metric_a>
Name: {req.metric1_name}
Expression: {req.metric1_definition}
</metric_a>

<metric_b>
Name: {req.metric2_name}
Expression: {req.metric2_definition}
</metric_b>

Respond with ONLY this JSON structure — no other text:
{{
  "verdict": "EQUIVALENT" | "DIFFERENT" | "PARTIAL",
  "confidence": <float 0.0-1.0>,
  "reasoning": "<one concise sentence>",
  "key_differences": ["<diff 1>", "<diff 2>"]
}}

Rules:
- EQUIVALENT: Mathematically/logically identical result despite different syntax (e.g. SUM(a+b) vs SUM(a)+SUM(b))
- DIFFERENT: Fundamentally different calculations or business meaning
- PARTIAL: Same intent but different scope, filters, or granularity
- confidence: your certainty in the verdict (0.9+ = highly certain, <0.6 = uncertain)
- key_differences: empty array [] if EQUIVALENT"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_prompt},
    ]

    try:
        raw_content = _call_llm(req.provider, req.model, messages, temperature=0.0)

        # Validate-and-repair fallback (industry best practice for LLM JSON output)
        try:
            parsed = json.loads(raw_content)
        except json.JSONDecodeError:
            # LLM may wrap JSON in markdown code fences — strip them
            match = re.search(r"\{.*\}", raw_content, re.DOTALL)
            if match:
                parsed = json.loads(match.group())
            else:
                # Absolute fallback: return raw content as reasoning
                parsed = {
                    "verdict": "DIFFERENT",
                    "confidence": 0.0,
                    "reasoning": raw_content[:500],
                    "key_differences": [],
                }

        verdict = parsed.get("verdict", "DIFFERENT").upper()
        return {
            "verdict": verdict,
            "is_semantically_identical": verdict == "EQUIVALENT",
            "confidence": float(parsed.get("confidence", 0.0)),
            "reasoning": str(parsed.get("reasoning", "")),
            "key_differences": parsed.get("key_differences", []),
            "model_used": model_id,
        }

    except HTTPException:
        raise  # re-raise 503/400 from _call_llm directly
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"LLM returned unparseable response: {exc}") from exc
    except Exception as exc:
        logger.error("LLM comparison failed [model=%s]: %s", model_id, exc)
        raise HTTPException(status_code=500, detail=f"LLM comparison failed: {exc}") from exc

