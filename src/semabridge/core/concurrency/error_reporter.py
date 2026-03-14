"""
Error reporter for generating structured, readable batch reports.

Aggregates per-model errors with classification, retry context,
and produces both log output and optional JSON/Markdown reports.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from semabridge.core.concurrency.models import BatchResult, ModelResult

logger = logging.getLogger(__name__)


class ErrorReporter:
    """Generate structured error reports for parallel batch results.

    Provides:
    - Summary logging to the standard logger.
    - JSON report persistence.
    - Markdown report for human review.

    Args:
        output_dir: Directory for persisted reports. Defaults to
            ``output/reports/``.
    """

    def __init__(self, output_dir: Optional[str] = None) -> None:
        self._output_dir = Path(output_dir or "output/reports")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log_summary(self, batch: BatchResult) -> None:
        """Log a human-readable summary to the standard logger.

        Args:
            batch: Completed batch result.
        """
        total = batch.total_models
        ok = batch.success_count
        fail = batch.failure_count

        logger.info("=" * 60)
        logger.info("BATCH %s SUMMARY", batch.batch_id)
        logger.info("=" * 60)
        logger.info("  Total: %d | Succeeded: %d | Failed: %d", total, ok, fail)

        if batch.all_succeeded:
            logger.info("  Result: ALL MODELS SUCCEEDED")
        else:
            logger.warning("  Result: %d MODEL(S) FAILED", fail)
            for model_name, error in batch.failed_models.items():
                logger.warning("    • %s — %s", model_name, error)

        logger.info("=" * 60)

    def generate_json_report(
        self,
        batch: BatchResult,
        model_results: Optional[List[ModelResult]] = None,
    ) -> Path:
        """Write a JSON report to the output directory.

        Args:
            batch: Completed batch result.
            model_results: Optional list of detailed model results.

        Returns:
            Path to the generated JSON file.
        """
        self._output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"batch_{batch.batch_id}_{_timestamp()}.json"
        path = self._output_dir / filename

        report: Dict[str, Any] = {
            "batch_id": batch.batch_id,
            "timestamp": _timestamp(),
            "total_models": batch.total_models,
            "successful_count": batch.success_count,
            "failed_count": batch.failure_count,
            "all_succeeded": batch.all_succeeded,
            "successful_models": batch.successful_models,
            "failed_models": batch.failed_models,
        }

        if model_results:
            report["details"] = [
                _model_result_to_dict(r) for r in model_results
            ]

        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        logger.info("JSON report written: %s", path)
        return path

    def generate_markdown_report(
        self,
        batch: BatchResult,
        model_results: Optional[List[ModelResult]] = None,
    ) -> Path:
        """Write a Markdown report for human review.

        Args:
            batch: Completed batch result.
            model_results: Optional list of detailed model results.

        Returns:
            Path to the generated Markdown file.
        """
        self._output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"batch_{batch.batch_id}_{_timestamp()}.md"
        path = self._output_dir / filename

        lines = [
            f"# Batch Report — `{batch.batch_id}`",
            "",
            f"**Generated:** {_timestamp()}",
            "",
            "## Summary",
            "",
            f"| Metric | Count |",
            f"|--------|-------|",
            f"| Total Models | {batch.total_models} |",
            f"| Succeeded | {batch.success_count} |",
            f"| Failed | {batch.failure_count} |",
            "",
        ]

        if batch.successful_models:
            lines.append("## Successful Models")
            lines.append("")
            for m in batch.successful_models:
                lines.append(f"- {m}")
            lines.append("")

        if batch.failed_models:
            lines.append("## Failed Models")
            lines.append("")
            lines.append("| Model | Error |")
            lines.append("|-------|-------|")
            for model_name, error in batch.failed_models.items():
                # Escape pipes for Markdown table
                safe_error = str(error).replace("|", "\\|")[:120]
                lines.append(f"| {model_name} | {safe_error} |")
            lines.append("")

        if model_results:
            lines.append("## Detailed Results")
            lines.append("")
            for r in model_results:
                status = "✓" if r.success else "✗"
                lines.append(f"### {status} {r.model_name}")
                lines.append("")
                lines.append(f"- **Duration:** {r.duration_seconds:.1f}s")
                lines.append(f"- **Retries:** {r.retry_count}")
                if r.error:
                    lines.append(f"- **Error:** {r.error}")
                if r.error_type:
                    lines.append(f"- **Error Type:** {r.error_type}")
                if r.snapshot_id:
                    lines.append(f"- **Snapshot:** {r.snapshot_id}")
                lines.append("")

        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Markdown report written: %s", path)
        return path


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _model_result_to_dict(r: ModelResult) -> Dict[str, Any]:
    return {
        "model_name": r.model_name,
        "success": r.success,
        "duration_seconds": r.duration_seconds,
        "error": r.error,
        "error_type": r.error_type,
        "retry_count": r.retry_count,
        "snapshot_id": r.snapshot_id,
        "step_failed": r.step_failed,
    }
