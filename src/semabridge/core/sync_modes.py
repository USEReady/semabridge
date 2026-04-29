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
    
    # 1. Merge Datasets
    src_datasets_by_name = {ds.unique_name: ds for ds in src_model.datasets}
    tgt_datasets_by_name = {ds.unique_name: ds for ds in merged_model.datasets}
    
    for name, src_ds in src_datasets_by_name.items():
        if name in tgt_datasets_by_name:
            # Overwrite existing dataset
            # Find the index in merged_model.datasets
            for i, ds in enumerate(merged_model.datasets):
                if ds.unique_name == name:
                    merged_model.datasets[i] = src_ds
                    break
        else:
            # Add new dataset
            merged_model.datasets.append(src_ds)
            
    # 2. Merge Metrics
    src_metrics_by_name = {m.unique_name: m for m in src_model.metrics}
    tgt_metrics_by_name = {m.unique_name: m for m in merged_model.metrics}
    
    for name, src_m in src_metrics_by_name.items():
        if name in tgt_metrics_by_name:
            for i, m in enumerate(merged_model.metrics):
                if m.unique_name == name:
                    merged_model.metrics[i] = src_m
                    break
        else:
            merged_model.metrics.append(src_m)
            
    # 3. Merge Dimensions
    src_dims_by_name = {d.unique_name: d for d in src_model.dimensions}
    tgt_dims_by_name = {d.unique_name: d for d in merged_model.dimensions}
    
    for name, src_d in src_dims_by_name.items():
        if name in tgt_dims_by_name:
            for i, d in enumerate(merged_model.dimensions):
                if d.unique_name == name:
                    merged_model.dimensions[i] = src_d
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
