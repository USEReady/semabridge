"""
Configuration resolver for multi-model pattern expansion.

Resolves wildcard patterns and explicit model lists into concrete
model sets, and identifies multi-target broadcasting scenarios.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from semabridge.core.concurrency.models import BroadcastScenario
from semabridge.core.project import ProjectConfig
from semabridge.utils.model_dedup import deduplicate_model_names

logger = logging.getLogger(__name__)


class ConfigurationResolver:
    """Resolve model patterns and multi-target configurations.

    Uses the existing ``SourceConfig.matches_model()`` for pattern matching
    and ``ProjectConfig.get_included_models()`` for inclusion/exclusion.

    Args:
        config: The project configuration.
    """

    def __init__(self, config: ProjectConfig) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve_models(self, available_models: List[str]) -> List[str]:
        """Resolve wildcard patterns to a concrete, deduplicated model list.

        Args:
            available_models: All models discovered from the source.

        Returns:
            Sorted list of unique model names matching patterns
            after exclusion rules are applied.
        """
        # Delegate to ProjectConfig which applies include + exclude
        resolved = self._config.get_included_models(available_models)

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for model in resolved:
            if model not in seen:
                seen.add(model)
                unique.append(model)

        # Module 3: Canonical-name dedup — catches "Regional_Sales_Sample"
        # vs "Regional Sales Sample" before extraction phase.
        unique, dupes = deduplicate_model_names(unique)
        if dupes:
            for g in dupes:
                logger.warning(
                    "Canonical-name duplicate detected: %s", g.summary()
                )

        logger.info(
            "Resolved %d models from %d available (source pattern=%r, "
            "explicit_list=%s, exclusions=%d)",
            len(unique),
            len(available_models),
            self._config.source.model,
            bool(self._config.source.models),
            len(self._config.options.exclude_model),
        )
        return unique

    def identify_broadcast_scenarios(self) -> List[BroadcastScenario]:
        """Identify single-source → multi-target broadcasting scenarios.

        A broadcast scenario exists when there are multiple deploy-enabled
        targets configured.

        Returns:
            List of broadcast scenarios (one per model in multi-target configs).
        """
        deploy_targets = [t for t in self._config.targets if t.deploy]
        if len(deploy_targets) <= 1:
            return []

        target_names = [
            getattr(t, "name", None) or f"{t.type.value}_{i}"
            for i, t in enumerate(deploy_targets)
        ]

        # Source model pattern — actual models resolved later
        model_pattern = self._config.source.model
        return [
            BroadcastScenario(
                source_model=model_pattern,
                target_names=target_names,
            )
        ]

    def is_multi_target(self) -> bool:
        """Return ``True`` if multiple deploy-enabled targets are configured."""
        return len([t for t in self._config.targets if t.deploy]) > 1

    def get_deploy_targets(self) -> list:
        """Return the list of targets with deploy=True."""
        return [t for t in self._config.targets if t.deploy]
