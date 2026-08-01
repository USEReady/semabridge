"""DaxTranslationService — the single public entry point.

Calls the unchanged Tier 1-4 deterministic translators first; only falls
through to Tier 5 (LLM) if every deterministic tier declines. Nothing in
this file is wired into any existing pipeline yet (Step 1 is additive).
"""
from __future__ import annotations

from typing import List, Optional

from semabridge.dax_translation.types import TranslationRequest, TranslationResult
from semabridge.dax_translation.tiers_1_4 import translate_tiers_1_4
from semabridge.dax_translation.tier5.config import Tier5Config
from semabridge.dax_translation.tier5.service import Tier5Service


class DaxTranslationService:
    def __init__(self, tier5_config: Optional[Tier5Config] = None) -> None:
        self._tier5 = Tier5Service(tier5_config)

    def translate_metric(self, request: TranslationRequest) -> TranslationResult:
        deterministic_result = translate_tiers_1_4(request)
        if deterministic_result is not None:
            return deterministic_result

        llm_result = self._tier5.translate(request)
        if llm_result is not None:
            return llm_result

        return TranslationResult(sql=None, tier=4, original_dax=request.dax)

    def translate_batch(self, requests: List[TranslationRequest]) -> List[TranslationResult]:
        """Tier 1-4 still runs per-item, first, for every request — the
        tier-ordering invariant (Tests/dax_translation/test_tier_ordering_invariant.py)
        holds exactly as it does for translate_metric(). Only the subset
        every deterministic tier declines on is handed to Tier5Service in
        one batched call (chunked internally at Tier5Config.max_batch_size)
        instead of one Tier 5 call per metric.
        """
        if not requests:
            return []

        results: List[Optional[TranslationResult]] = [None] * len(requests)
        tier5_indices: List[int] = []
        tier5_requests: List[TranslationRequest] = []

        for i, request in enumerate(requests):
            deterministic_result = translate_tiers_1_4(request)
            if deterministic_result is not None:
                results[i] = deterministic_result
            else:
                tier5_indices.append(i)
                tier5_requests.append(request)

        if tier5_requests:
            tier5_results = self._tier5.translate_batch(tier5_requests)
            for idx, tier5_result in zip(tier5_indices, tier5_results):
                results[idx] = tier5_result

        return [
            result if result is not None else TranslationResult(sql=None, tier=4, original_dax=requests[i].dax)
            for i, result in enumerate(results)
        ]
