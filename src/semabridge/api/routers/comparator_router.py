from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import yaml
import json
import os
import aisuite as ai

router = APIRouter(prefix="/api/comparator", tags=["Comparator"])

# Initialize AISuite client
# It will automatically pick up GOOGLE_API_KEY from environment variables.
ai_client = ai.Client()

def parse_yaml_to_normalized(content: str) -> Dict[str, Any]:
    """
    Parses various Semantic Model YAML definitions (OSI, SML, TSML, Snowflake YAML)
    and normalizes them into a standard schema for comparison.
    """
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML file: {exc}")

    normalized = {
        "tables": {},
        "columns": {},
        "metrics": {},
        "relationships": {}
    }

    if not isinstance(data, dict):
        return normalized

    # Very naive normalization attempt - covering Snowflake / generic semantic view approaches
    # Snowflake semantic views / DBT semantic layer / generic semantic view approaches / Fabric SML
    root = data.get("semantic_model", data)
    tables = root.get("tables", root.get("semantic_models", root.get("collections", root.get("entities", root.get("models", root.get("datasets", []))))))
    if not tables and isinstance(data, list):
         tables = data
    
    if isinstance(tables, list):
        for tbl in tables:
            if not isinstance(tbl, dict): continue
            tbl_name = tbl.get("name", tbl.get("unique_name", tbl.get("model", "Unknown")))
            cols = tbl.get("columns", tbl.get("dimensions", tbl.get("fields", tbl.get("entities", []))))
            # Store table stats
            normalized["tables"][tbl_name] = {
                "name": tbl_name,
                "column_count": len(cols),
                "metric_count": 0, # Will be calculated
                "relationship_count": 0 # Will be calculated
            }
            # Store columns
            for col in cols:
                if not isinstance(col, dict): continue
                col_name = col.get("name", col.get("unique_name", "Unknown"))
                dtype = col.get("data_type", col.get("type", "Unknown"))
                col_key = f"{tbl_name}.{col_name}"
                normalized["columns"][col_key] = {
                    "name": col_name,
                    "table": tbl_name,
                    "type": dtype
                }
            
            # Extract metrics inside the table
            tbl_metrics = tbl.get("metrics", tbl.get("measures", []))
            if isinstance(tbl_metrics, list):
                for m in tbl_metrics:
                    if not isinstance(m, dict): continue
                    m_name = m.get("name", m.get("unique_name", "Unknown"))
                    m_expr = m.get("expr", m.get("expression", m.get("sql", "Unknown")))
                    m_key = f"{tbl_name}.{m_name}"
                    normalized["tables"][tbl_name]["metric_count"] += 1
                    normalized["metrics"][m_key] = {
                        "name": m_name,
                        "table": tbl_name,
                        "definition": str(m_expr)
                    }
            elif isinstance(tbl_metrics, dict):
                for m_name, m_data in tbl_metrics.items():
                    m_expr = m_data.get("expr", m_data.get("expression", m_data.get("sql", "Unknown"))) if isinstance(m_data, dict) else str(m_data)
                    m_key = f"{tbl_name}.{m_name}"
                    normalized["tables"][tbl_name]["metric_count"] += 1
                    normalized["metrics"][m_key] = {
                        "name": m_name,
                        "table": tbl_name,
                        "definition": str(m_expr)
                    }

            # Extract relationships inside the table
            tbl_rels = tbl.get("relationships", tbl.get("joins", []))
            if isinstance(tbl_rels, list):
                for r in tbl_rels:
                    if not isinstance(r, dict): continue
                    r_name = r.get("name", f"join_{tbl_name}")
                    right_table = r.get("right_table", r.get("destination", r.get("to", "Unknown")))
                    l_col = r.get("left_column", r.get("on", "Unknown"))
                    r_col = r.get("right_column", "Unknown")
                    card = r.get("cardinality", r.get("type", r.get("relationship_type", "Unknown")))
                    normalized["tables"][tbl_name]["relationship_count"] += 1
                    r_key = f"{tbl_name}_{r_name}"
                    normalized["relationships"][r_key] = {
                        "name": r_name,
                        "left_table": tbl_name,
                        "right_table": right_table,
                        "left_column": l_col,
                        "right_column": r_col,
                        "cardinality": card
                    }
    elif isinstance(tables, dict):
        # Alternative table format
        for tbl_name, tbl_data in tables.items():
            if not isinstance(tbl_data, dict): continue
            cols = tbl_data.get("columns", tbl_data.get("dimensions", tbl_data.get("fields", tbl_data.get("entities", {}))))
            if isinstance(cols, dict):
                col_count = len(cols)
                for col_name, col_def in cols.items():
                    dtype = col_def.get("data_type", col_def.get("type", "Unknown")) if isinstance(col_def, dict) else "Unknown"
                    col_key = f"{tbl_name}.{col_name}"
                    normalized["columns"][col_key] = {
                        "name": col_name,
                        "table": tbl_name,
                        "type": dtype
                    }
            elif isinstance(cols, list):
                col_count = len(cols)
                for col in cols:
                    col_name = col.get("name", col.get("unique_name", "Unknown"))
                    dtype = col.get("data_type", col.get("type", "Unknown"))
                    col_key = f"{tbl_name}.{col_name}"
                    normalized["columns"][col_key] = {
                        "name": col_name,
                        "table": tbl_name,
                        "type": dtype
                    }
            else:
                col_count = 0
                
            # Extract metrics inside the dict table
            m_count = 0
            tbl_metrics = tbl_data.get("metrics", tbl_data.get("measures", []))
            if isinstance(tbl_metrics, list):
                for m in tbl_metrics:
                    m_name = m.get("name", m.get("unique_name", "Unknown"))
                    m_expr = m.get("expr", m.get("expression", "Unknown"))
                    m_key = f"{tbl_name}.{m_name}"
                    m_count += 1
                    normalized["metrics"][m_key] = {
                        "name": m_name,
                        "table": tbl_name,
                        "definition": str(m_expr)
                    }
            elif isinstance(tbl_metrics, dict):
                for m_name, m_data in tbl_metrics.items():
                    m_expr = m_data.get("expr", m_data.get("expression", m_data.get("sql", "Unknown"))) if isinstance(m_data, dict) else str(m_data)
                    m_key = f"{tbl_name}.{m_name}"
                    m_count += 1
                    normalized["metrics"][m_key] = {
                        "name": m_name,
                        "table": tbl_name,
                        "definition": str(m_expr)
                    }

            # Extract relationships inside the dict table
            r_count = 0
            tbl_rels = tbl_data.get("relationships", tbl_data.get("joins", []))
            if isinstance(tbl_rels, list):
                for r in tbl_rels:
                    if not isinstance(r, dict): continue
                    r_name = r.get("name", f"join_{tbl_name}")
                    right_table = r.get("right_table", r.get("destination", r.get("to", "Unknown")))
                    l_col = r.get("left_column", r.get("on", "Unknown"))
                    r_col = r.get("right_column", "Unknown")
                    card = r.get("cardinality", r.get("type", r.get("relationship_type", "Unknown")))
                    r_count += 1
                    r_key = f"{tbl_name}_{r_name}"
                    normalized["relationships"][r_key] = {
                        "name": r_name,
                        "left_table": tbl_name,
                        "right_table": right_table,
                        "left_column": l_col,
                        "right_column": r_col,
                        "cardinality": card
                    }
            
            normalized["tables"][tbl_name] = {
                "name": tbl_name,
                "column_count": col_count,
                "metric_count": m_count,
                "relationship_count": r_count
            }

    # Metrics
    metrics = root.get("metrics", root.get("measures", []))
    if isinstance(metrics, list):
        for m in metrics:
            m_name = m.get("name", m.get("unique_name", "Unknown"))
            m_table = m.get("table", m.get("dataset", "Unknown"))
            m_expr = m.get("expr", m.get("expression", "Unknown"))
            
            # Increment table metric count if possible
            if m_table in normalized["tables"]:
                normalized["tables"][m_table]["metric_count"] += 1
                
            m_key = f"{m_table}.{m_name}"
            normalized["metrics"][m_key] = {
                "name": m_name,
                "table": m_table,
                "definition": str(m_expr)
            }
    elif isinstance(metrics, dict):
        for m_name, m_data in metrics.items():
             m_table = m_data.get("table", "Unknown")
             m_expr = m_data.get("expr", m_data.get("expression", "Unknown"))
             if m_table in normalized["tables"]:
                 normalized["tables"][m_table]["metric_count"] += 1
             m_key = f"{m_table}.{m_name}"
             normalized["metrics"][m_key] = {
                 "name": m_name,
                 "table": m_table,
                 "definition": str(m_expr)
             }

    # Relationships / Joins
    rels = root.get("relationships", root.get("joins", []))
    if isinstance(rels, list):
        for r in rels:
            r_name = r.get("name", f"{r.get('left_table')}-{r.get('right_table')}")
            left_table = r.get("left_table", "Unknown")
            right_table = r.get("right_table", "Unknown")
            l_col = r.get("left_column", r.get("left_key", "Unknown"))
            r_col = r.get("right_column", r.get("right_key", "Unknown"))
            cardinality = r.get("cardinality", r.get("relationship_type", "Unknown"))
            
            if left_table in normalized["tables"]:
                normalized["tables"][left_table]["relationship_count"] += 1
            if right_table in normalized["tables"]:
                normalized["tables"][right_table]["relationship_count"] += 1
                
            normalized["relationships"][r_name] = {
                "name": r_name,
                "left_table": left_table,
                "right_table": right_table,
                "left_column": l_col,
                "right_column": r_col,
                "cardinality": cardinality
            }

    return normalized


@router.post("/parse")
async def parse_single_yaml(file: UploadFile = File(...)):
    if not file.filename.endswith(('.yaml', '.yml')):
        raise HTTPException(status_code=400, detail="Only YAML files are allowed.")
    
    content = await file.read()
    content_str = content.decode("utf-8")
    
    try:
        normalized = parse_yaml_to_normalized(content_str)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    # Build statistics
    total_tables = len(normalized["tables"])
    total_cols = len(normalized["columns"])
    total_metrics = len(normalized["metrics"])
    total_rels = len(normalized["relationships"])
    
    tables_list = list(normalized["tables"].values())
    metrics_list = list(normalized["metrics"].values())
    relationships_list = list(normalized["relationships"].values())
    columns_list = list(normalized["columns"].values())

    return {
        "summary": {
            "total_tables": total_tables,
            "total_columns": total_cols,
            "total_metrics": total_metrics,
            "total_relationships": total_rels
        },
        "tables": tables_list,
        "columns": columns_list,
        "metrics": metrics_list,
        "relationships": relationships_list
    }


class CompareRequest(BaseModel):
    file1_name: str
    file1_content: str
    file2_name: str
    file2_content: str

def compare_entities(dict1: Dict, dict2: Dict) -> List[Dict]:
    """Compares two dicts of entities and tags them with their diff status."""
    keys = set(dict1.keys()).union(set(dict2.keys()))
    res = []
    for k in keys:
        if k in dict1 and k not in dict2:
            base = dict1[k].copy()
            base["_diff_status"] = "only_in_1"
            base["_id"] = k
            res.append(base)
        elif k in dict2 and k not in dict1:
            base = dict2[k].copy()
            base["_diff_status"] = "only_in_2"
            base["_id"] = k
            res.append(base)
        else:
            e1 = dict1[k]
            e2 = dict2[k]
            if json.dumps(e1, sort_keys=True) == json.dumps(e2, sort_keys=True):
                 e1_copy = e1.copy()
                 e1_copy["_diff_status"] = "identical"
                 e1_copy["_id"] = k
                 res.append(e1_copy)
            else:
                 # Modified
                 e2_copy = e2.copy()
                 e2_copy["_diff_status"] = "modified"
                 e2_copy["_id"] = k
                 e2_copy["_old_value"] = str(e1)
                 res.append(e2_copy)
    return res

@router.post("/compare")
async def compare_yamls(req: CompareRequest):
    try:
        norm1 = parse_yaml_to_normalized(req.file1_content)
        norm2 = parse_yaml_to_normalized(req.file2_content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    # 1st level comparison
    tables_diff = compare_entities(norm1["tables"], norm2["tables"])
    columns_diff = compare_entities(norm1["columns"], norm2["columns"])
    metrics_diff = compare_entities(norm1["metrics"], norm2["metrics"])
    rels_diff = compare_entities(norm1["relationships"], norm2["relationships"])
    
    return {
        "file1_name": req.file1_name,
        "file2_name": req.file2_name,
        "tables": tables_diff,
        "columns": columns_diff,
        "metrics": metrics_diff,
        "relationships": rels_diff
    }


class AisuitCompareRequest(BaseModel):
    metric1_definition: str
    metric2_definition: str

@router.post("/compare-semantic")
async def semantic_compare(req: AisuitCompareRequest):
    """
    2nd level comparison using LLM (via aisuite) to check if definitions are identical in meaning.
    """
    prompt = f"\"\"\"\nCompare the following two metric definitions to see if they mean the exact same logical calculation in a semantic layer. Answer ONLY 'YES' or 'NO' followed by a short 1-sentence reason.\nDefinition 1: {req.metric1_definition}\nDefinition 2: {req.metric2_definition}\n\"\"\""
    
    try:
        # Use gemini wrapper
        # The provider relies on typical conventions. For google it is `google:gemini-1.5-flash` or `google:gemini-1.5-pro`
        model_name = "google:gemini-1.5-flash"
        
        messages = [
            {"role": "system", "content": "You are a data engineering expert evaluating semantic definitions."},
            {"role": "user", "content": prompt}
        ]
        
        response = ai_client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=0.0
        )
        
        content = response.choices[0].message.content.strip()
        is_same = content.upper().startswith("YES")
        
        return {
            "is_semantically_identical": is_same,
            "explanation": content
        }
    except Exception as e:
        print(f"Exception in AISuite: {e}")
        return {
            "error": str(e)
        }
