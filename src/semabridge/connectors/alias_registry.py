"""Registry for tracking aliases and used names during DDL generation.

Keep this minimal: it centralizes alias maps/sets so future refactors can
pass a single context object instead of multiple loose variables.
"""
from __future__ import annotations

from typing import Dict, Set


class AliasRegistry:
    def __init__(self) -> None:
        # dataset_unique_name -> alias
        self.dataset_aliases: Dict[str, str] = {}
        # used table aliases (to ensure uniqueness)
        self.used_table_aliases: Set[str] = set()
        # used dimension aliases
        self.used_dimension_aliases: Set[str] = set()
        # used metric names
        self.used_metric_names: Set[str] = set()

    def register_dataset_alias(self, dataset_name: str, alias: str) -> None:
        self.dataset_aliases[dataset_name] = alias

    def get_alias(self, dataset_name: str) -> str | None:
        return self.dataset_aliases.get(dataset_name)
