from typing import Any, Dict, List, Optional
from semabridge.sml.models import SMLModel, SMLDataset, SMLMetric, SMLRelationship, SMLDimension

def apply_sync_mode(src_model: SMLModel, tgt_model: Optional[SMLModel], sync_mode: str) -> SMLModel:
    """
    Apply sync_mode logic to merge source and target models.
    
    ## sync_mode = 'copy'
    - Target is fully replaced by source content.
    - Entities in target that are NOT in source are removed.
    
    ## sync_mode = 'upsert'
    - Union of source + target.
    - For entities in both source and target -> source definition wins (overwrites target).
    - For entities only in target -> preserved (not deleted).
    - For entities only in source -> added to target.
    """
    if sync_mode == "copy" or tgt_model is None:
        return src_model  # target fully replaced or no target exists
    
    if sync_mode != "upsert":
        # Default to copy if unknown mode
        return src_model

    # --- UPSERT Logic ---
    
    # Start with a copy of the target model
    merged_model = tgt_model.model_copy(deep=True)
    
    # 1. Merge Datasets (Tables + Columns)
    src_datasets_by_name = {ds.unique_name: ds for ds in src_model.datasets}
    tgt_datasets_by_name = {ds.unique_name: ds for ds in merged_model.datasets}
    
    for name, src_ds in src_datasets_by_name.items():
        if name in tgt_datasets_by_name:
            # Granular merge of columns within the dataset
            for i, tgt_ds in enumerate(merged_model.datasets):
                if tgt_ds.unique_name == name:
                    # Map existing columns
                    tgt_cols_by_name = {c.unique_name: c for c in tgt_ds.columns}
                    new_cols = list(src_ds.columns) # Start with all source columns
                    src_col_names = {c.unique_name for c in src_ds.columns}
                    
                    # Add target-only columns
                    for c_name, tgt_c in tgt_cols_by_name.items():
                        if c_name not in src_col_names:
                            new_cols.append(tgt_c)
                    
                    # Create merged dataset (source properties win, but columns are unioned)
                    merged_ds = src_ds.model_copy(deep=True)
                    merged_ds.columns = new_cols
                    merged_model.datasets[i] = merged_ds
                    break
        else:
            merged_model.datasets.append(src_ds)
            
    # 2. Merge Metrics
    src_metrics_by_name = {m.unique_name: m for m in src_model.metrics}
    tgt_metrics_by_name = {m.unique_name: m for m in merged_model.metrics}
    
    for name, src_m in src_metrics_by_name.items():
        if name in tgt_metrics_by_name:
            for i, m in enumerate(merged_model.metrics):
                if m.unique_name == name:
                    merged_model.metrics[i] = src_m # Source wins on same-named metrics
                    break
        else:
            merged_model.metrics.append(src_m)
            
    # 3. Merge Dimensions (Groups of Attributes)
    src_dims_by_name = {d.unique_name: d for d in src_model.dimensions}
    tgt_dims_by_name = {d.unique_name: d for d in merged_model.dimensions}
    
    for name, src_d in src_dims_by_name.items():
        if name in tgt_dims_by_name:
            for i, tgt_d in enumerate(merged_model.dimensions):
                if tgt_d.unique_name == name:
                    # Granular merge of attributes within the dimension
                    tgt_attrs_by_name = {a.unique_name: a for a in tgt_d.attributes}
                    new_attrs = list(src_d.attributes)
                    src_attr_names = {a.unique_name for a in src_d.attributes}
                    
                    for a_name, tgt_a in tgt_attrs_by_name.items():
                        if a_name not in src_attr_names:
                            new_attrs.append(tgt_a)
                    
                    merged_d = src_d.model_copy(deep=True)
                    merged_d.attributes = new_attrs
                    merged_model.dimensions[i] = merged_d
                    break
        else:
            merged_model.dimensions.append(src_d)
            
    # 4. Merge Relationships
    src_rels_by_name = {r.unique_name: r for r in src_model.relationships}
    tgt_rels_by_name = {r.unique_name: r for r in merged_model.relationships}
    
    for name, src_r in src_rels_by_name.items():
        if name in tgt_rels_by_name:
            for i, r in enumerate(merged_model.relationships):
                if r.unique_name == name:
                    merged_model.relationships[i] = src_r
                    break
        else:
            merged_model.relationships.append(src_r)
            
    # Update modified timestamp
    from datetime import datetime
    merged_model.modified_at = datetime.utcnow().isoformat()
    
    return merged_model
