"""
Module 3: Deterministic Semantic Deduplication Pipeline.

Provides:
- Canonical name normalization
- Structural fingerprinting of semantic models
- Model deduplication based on structural similarity
- Duplicate group tracking and reporting

This module enables detection and merging of semantically equivalent models
that may have different names, labels, or minor structural variations.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set
from collections import defaultdict


# ───────────────────────────────────────────────────────────────────────────
# Canonical Name Normalization
# ───────────────────────────────────────────────────────────────────────────

def canonicalize_name(name: str) -> str:
    """
    Normalize a name to canonical form for duplicate detection.

    Steps:
    1. Trim whitespace
    2. Convert to lowercase
    3. Replace spaces with underscores
    4. Replace hyphens with underscores
    5. Remove special characters (keep only alphanumeric and underscore)
    6. Collapse multiple underscores
    7. Strip leading/trailing underscores

    Args:
        name: Raw name string

    Returns:
        Canonical form: lowercase with underscores, no special chars
    """
    if not name:
        return ""

    # Trim whitespace
    name = name.strip()

    # Convert to lowercase
    name = name.lower()

    # Replace spaces with underscores
    name = name.replace(" ", "_")

    # Replace hyphens with underscores
    name = name.replace("-", "_")

    # Remove special characters (keep only alphanumeric and underscore)
    name = re.sub(r"[^a-z0-9_]", "", name)

    # Collapse multiple underscores
    name = re.sub(r"_+", "_", name)

    # Strip leading/trailing underscores
    name = name.strip("_")

    return name


# ───────────────────────────────────────────────────────────────────────────
# Structural Fingerprinting
# ───────────────────────────────────────────────────────────────────────────

def fingerprint_model(model: Any, include_labels: bool = False) -> str:
    """
    Generate a structural fingerprint (SHA-256 hash) of a semantic model.

    The fingerprint is based on:
    1. Dataset names (canonical form)
    2. Dataset columns (names and counts)
    3. Metric names and their aggregation types
    4. Relationship definitions
    5. Optional: Label information (if include_labels=True)

    Two models with identical structure (but possibly different names/labels)
    will have the same fingerprint if include_labels=False.

    Args:
        model: A semantic model object with datasets, metrics, relationships
        include_labels: If True, include label/description info in fingerprint

    Returns:
        SHA-256 hash (hex digest) of the model structure
    """
    # Collect structural information
    components = []

    # 1. Dataset structure
    if hasattr(model, 'datasets'):
        for ds in model.datasets:
            ds_name = canonicalize_name(getattr(ds, 'unique_name', ''))
            components.append(f"dataset:{ds_name}")

            # Column names
            if hasattr(ds, 'columns'):
                col_names = sorted([
                    canonicalize_name(getattr(col, 'unique_name', ''))
                    for col in ds.columns
                ])
                for col_name in col_names:
                    components.append(f"column:{col_name}")

    # 2. Metric structure
    if hasattr(model, 'metrics'):
        for metric in model.metrics:
            m_name = canonicalize_name(getattr(metric, 'unique_name', ''))
            agg_type = getattr(metric, 'aggregation', 'unknown').lower()
            ds_ref = canonicalize_name(getattr(metric, 'dataset', ''))
            components.append(f"metric:{m_name}:agg={agg_type}:ds={ds_ref}")

    # 3. Relationship structure
    if hasattr(model, 'relationships'):
        for rel in model.relationships:
            r_name = canonicalize_name(getattr(rel, 'unique_name', ''))
            from_ds = canonicalize_name(getattr(rel, 'from_dataset', ''))
            to_ds = canonicalize_name(getattr(rel, 'to_dataset', ''))
            components.append(f"relationship:{r_name}:{from_ds}->{to_ds}")

    # 4. Optional: labels
    if include_labels and hasattr(model, 'label'):
        label = canonicalize_name(getattr(model, 'label', ''))
        components.append(f"label:{label}")

    # Generate hash
    content = "|".join(sorted(components))
    return hashlib.sha256(content.encode()).hexdigest()


# ───────────────────────────────────────────────────────────────────────────
# Duplicate Detection
# ───────────────────────────────────────────────────────────────────────────

@dataclass
class DuplicateGroup:
    """
    Represents a group of semantically equivalent (duplicate) models.

    Attributes:
        canonical_name: The canonical name for this group
        fingerprint: Structural fingerprint (SHA-256 hash)
        models: List of model objects in this group
        metadata: Additional metadata about the group
    """

    canonical_name: str
    fingerprint: str
    models: List[Any] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_model(self, model: Any) -> None:
        """Add a model to this duplicate group."""
        if model not in self.models:
            self.models.append(model)

    def get_names(self) -> List[str]:
        """Get all unique names from models in this group."""
        return sorted([
            getattr(m, 'unique_name', f"model_{i}")
            for i, m in enumerate(self.models)
        ])

    def get_labels(self) -> List[str]:
        """Get all labels from models in this group."""
        return sorted(set([
            getattr(m, 'label', f"label_{i}")
            for i, m in enumerate(self.models)
        ]))


def deduplicate_models(
    models: List[Any],
    include_labels: bool = False,
    similarity_threshold: float = 1.0,
) -> List[DuplicateGroup]:
    """
    Identify and group duplicate (equivalent) models.

    Groups models by their structural fingerprint. Models with identical
    fingerprints are considered duplicates.

    Args:
        models: List of semantic model objects
        include_labels: If True, include labels in fingerprint (stricter)
        similarity_threshold: Minimum similarity to group models (0.0-1.0, default 1.0 = exact)

    Returns:
        List of DuplicateGroup objects, one per unique fingerprint
    """
    # Build fingerprint map
    fingerprint_map: Dict[str, DuplicateGroup] = {}

    for model in models:
        fp = fingerprint_model(model, include_labels=include_labels)
        canonical_name = canonicalize_name(getattr(model, 'unique_name', 'unknown'))

        if fp not in fingerprint_map:
            fingerprint_map[fp] = DuplicateGroup(
                canonical_name=canonical_name,
                fingerprint=fp,
            )

        fingerprint_map[fp].add_model(model)

    return list(fingerprint_map.values())


def deduplicate_model_names(model_names: List[str]) -> Dict[str, str]:
    """
    Identify and normalize duplicate name patterns.

    Maps variations of the same name (via canonicalization) to their canonical form.
    Useful for normalizing model names before duplicate detection.

    Args:
        model_names: List of model name strings

    Returns:
        Dict mapping each original name to its canonical form
    """
    canonical_map = {}
    for name in model_names:
        canonical = canonicalize_name(name)
        canonical_map[name] = canonical

    return canonical_map


def find_duplicate_names(model_names: List[str]) -> Dict[str, List[str]]:
    """
    Find groups of names that canonicalize to the same value.

    Args:
        model_names: List of model name strings

    Returns:
        Dict mapping canonical names to lists of original names
    """
    groups = defaultdict(list)
    for name in model_names:
        canonical = canonicalize_name(name)
        groups[canonical].append(name)

    # Only return groups with duplicates
    return {
        canonical: names
        for canonical, names in groups.items()
        if len(names) > 1
    }
