#!/usr/bin/env python3
"""
API Usage Tracking and Logging for DAX Translation Pipeline.

Provides utilities to track and report on:
- Number of metrics classified as simple vs complex
- LLM API calls made and quota usage
- Translation success rates
- Performance metrics
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field, asdict
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class TranslationBatchMetrics:
    """Metrics for a batch translation operation."""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    total_metrics: int = 0
    simple_metrics: int = 0
    complex_metrics: int = 0
    api_calls_made: int = 0
    successful_translations: int = 0
    failed_translations: int = 0
    rule_based_successes: int = 0
    rule_based_failures: int = 0
    llm_successes: int = 0
    llm_failures: int = 0
    elapsed_seconds: float = 0.0
    
    @property
    def simple_percentage(self) -> float:
        """Percentage of metrics classified as simple."""
        if self.total_metrics == 0:
            return 0.0
        return (self.simple_metrics / self.total_metrics) * 100
    
    @property
    def api_call_reduction(self) -> float:
        """Percentage reduction in API calls from classification."""
        if self.total_metrics == 0:
            return 0.0
        # If we have N simple metrics and 0 API calls for them, that's N/total * 100% reduction
        return (self.simple_metrics / self.total_metrics) * 100
    
    @property
    def llm_accuracy(self) -> float:
        """LLM translation success rate."""
        total_llm = self.llm_successes + self.llm_failures
        if total_llm == 0:
            return 0.0
        return (self.llm_successes / total_llm) * 100
    
    @property
    def rule_accuracy(self) -> float:
        """Rule-based translation success rate."""
        total_rule = self.rule_based_successes + self.rule_based_failures
        if total_rule == 0:
            return 0.0
        return (self.rule_based_successes / total_rule) * 100


class APIUsageTracker:
    """Track and report on API usage across translation sessions."""
    
    def __init__(self, log_file: str = ".api_usage_log.jsonl"):
        """
        Initialize tracker.
        
        Args:
            log_file: Path to JSONL file for storing usage metrics
        """
        self.log_file = Path(log_file)
        self.current_batch: Optional[TranslationBatchMetrics] = None
        self.history: List[TranslationBatchMetrics] = []
        
        # Load existing history
        self._load_history()
    
    def start_batch(self) -> None:
        """Start tracking a new batch translation."""
        self.current_batch = TranslationBatchMetrics()
        logger.debug(f"Started tracking new batch at {self.current_batch.timestamp}")
    
    def end_batch(self, elapsed_seconds: float) -> Optional[TranslationBatchMetrics]:
        """
        End current batch and save metrics.
        
        Args:
            elapsed_seconds: Time taken for the batch
            
        Returns:
            The completed batch metrics
        """
        if not self.current_batch:
            logger.warning("No active batch to end")
            return None
        
        self.current_batch.elapsed_seconds = elapsed_seconds
        self.history.append(self.current_batch)
        self._save_metrics(self.current_batch)
        
        # Log summary
        self._log_batch_summary(self.current_batch)
        
        metrics = self.current_batch
        self.current_batch = None
        return metrics
    
    def add_simple_metric(self) -> None:
        """Record that a metric was classified as simple."""
        if self.current_batch:
            self.current_batch.simple_metrics += 1
            self.current_batch.total_metrics += 1
    
    def add_complex_metric(self) -> None:
        """Record that a metric was classified as complex."""
        if self.current_batch:
            self.current_batch.complex_metrics += 1
            self.current_batch.total_metrics += 1
    
    def add_rule_based_translation(self, success: bool) -> None:
        """Record outcome of rule-based translation."""
        if self.current_batch:
            if success:
                self.current_batch.rule_based_successes += 1
                self.current_batch.successful_translations += 1
            else:
                self.current_batch.rule_based_failures += 1
                self.current_batch.failed_translations += 1
    
    def add_llm_translation(self, success: bool) -> None:
        """Record outcome of LLM translation."""
        if self.current_batch:
            if success:
                self.current_batch.llm_successes += 1
                self.current_batch.successful_translations += 1
            else:
                self.current_batch.llm_failures += 1
                self.current_batch.failed_translations += 1
    
    def add_api_call(self) -> None:
        """Record that an API call was made."""
        if self.current_batch:
            self.current_batch.api_calls_made += 1
    
    def get_current_batch(self) -> Optional[TranslationBatchMetrics]:
        """Get current batch metrics."""
        return self.current_batch
    
    def get_summary_stats(self) -> Dict:
        """Get summary statistics across all batches."""
        if not self.history:
            return {
                'total_batches': 0,
                'total_metrics_processed': 0,
                'total_api_calls': 0,
                'average_simple_percentage': 0.0,
                'average_success_rate': 0.0,
            }
        
        total_metrics = sum(b.total_metrics for b in self.history)
        total_api_calls = sum(b.api_calls_made for b in self.history)
        total_successes = sum(b.successful_translations for b in self.history)
        avg_simple_pct = sum(b.simple_percentage for b in self.history) / len(self.history) if self.history else 0
        avg_success_rate = (total_successes / total_metrics * 100) if total_metrics > 0 else 0
        
        return {
            'total_batches': len(self.history),
            'total_metrics_processed': total_metrics,
            'total_api_calls': total_api_calls,
            'average_simple_percentage': avg_simple_pct,
            'average_success_rate': avg_success_rate,
            'quota_savings': f"{avg_simple_pct:.0f}% of metrics avoided LLM",
        }
    
    def _save_metrics(self, batch: TranslationBatchMetrics) -> None:
        """Save batch metrics to file."""
        try:
            with open(self.log_file, 'a') as f:
                f.write(json.dumps(asdict(batch)) + '\n')
            logger.debug(f"Saved metrics to {self.log_file}")
        except Exception as e:
            logger.warning(f"Failed to save metrics: {e}")
    
    def _load_history(self) -> None:
        """Load existing metrics from file."""
        if not self.log_file.exists():
            return
        
        try:
            with open(self.log_file, 'r') as f:
                for line in f:
                    try:
                        data = json.loads(line)
                        # Reconstruct TranslationBatchMetrics from dict
                        metrics = TranslationBatchMetrics(**data)
                        self.history.append(metrics)
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON line in {self.log_file}")
            
            logger.debug(f"Loaded {len(self.history)} historical batches from {self.log_file}")
        except Exception as e:
            logger.warning(f"Failed to load history: {e}")
    
    def _log_batch_summary(self, batch: TranslationBatchMetrics) -> None:
        """Log a summary of the completed batch."""
        logger.info(
            f"📊 Translation Batch Summary:\n"
            f"   ├─ Total metrics: {batch.total_metrics}\n"
            f"   ├─ Simple (rule-based): {batch.simple_metrics} ({batch.simple_percentage:.0f}%)\n"
            f"   ├─ Complex (LLM): {batch.complex_metrics} ({100-batch.simple_percentage:.0f}%)\n"
            f"   ├─ API calls made: {batch.api_calls_made}\n"
            f"   ├─ Successful: {batch.successful_translations}/{batch.total_metrics}\n"
            f"   ├─ Rule success rate: {batch.rule_accuracy:.0f}%\n"
            f"   ├─ LLM success rate: {batch.llm_accuracy:.0f}%\n"
            f"   ├─ Elapsed: {batch.elapsed_seconds:.1f}s\n"
            f"   └─ 🎯 API CALL REDUCTION: {batch.api_call_reduction:.0f}% "
            f"({batch.simple_metrics} metrics skipped LLM)"
        )


# Global tracker instance
_tracker = None


def get_tracker() -> APIUsageTracker:
    """Get global API usage tracker instance."""
    global _tracker
    if _tracker is None:
        _tracker = APIUsageTracker()
    return _tracker


def log_complexity_classification(is_simple: bool) -> None:
    """Log that a metric was classified."""
    tracker = get_tracker()
    if is_simple:
        tracker.add_simple_metric()
    else:
        tracker.add_complex_metric()


def log_rule_based_result(success: bool) -> None:
    """Log outcome of rule-based translation."""
    tracker = get_tracker()
    tracker.add_rule_based_translation(success)


def log_llm_result(success: bool) -> None:
    """Log outcome of LLM translation."""
    tracker = get_tracker()
    tracker.add_llm_translation(success)


def log_api_call() -> None:
    """Log that an API call was made."""
    tracker = get_tracker()
    tracker.add_api_call()
