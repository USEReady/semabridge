"""Semantic description diffing, classification, and caveat sensitivity analysis.

Computes exact word- and phrase-level differences between baseline and warehouse
comments, classifies changes (TEXT_ADDED, TEXT_REMOVED, TEXT_REWORDED), and determines
risk-adjusted severity.

Severity is determined primarily by the structural shape of the change:
  - Text removed is higher severity than text added.
  - Text added is medium severity for a full sentence or clause, and low only for trivial additions.
  - Text reworded is low severity for typos and minor phrasing, and medium for substantive edits.
The caveat keyword list serves strictly as an auxiliary signal that can elevate severity,
never as the primary deciding factor.

When a change contains both deletions and additions of similar length, the classification
is flagged as uncertain (is_uncertain=True) to indicate a best-effort verdict.
"""

from __future__ import annotations

import difflib
from enum import Enum
import os
import re
from typing import Any, Dict, List, Optional, Tuple


class DiffSeverity(str, Enum):
    """Governance assessment severity levels for description changes."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


_CAVEAT_KEYWORDS = frozenset({
    "must", "requires", "required", "require", "mandatory", "partition",
    "partitioned", "dimension", "dimensions", "caveat", "non-additive",
    "nonadditive", "window", "filter", "filters", "only", "do not", "never",
    "cannot", "disallowed", "restricted", "refuse",
})


def get_typo_similarity_threshold() -> float:
    """Minimum character-level similarity ratio (default 0.88) required to classify an edit as a typo or minor rewording."""
    val = os.getenv("SEMABRIDGE_DIFF_TYPO_SIMILARITY", "").strip()
    if val:
        try:
            return float(val)
        except ValueError:
            pass
    try:
        from semabridge.core.settings import get_settings
        cfg = getattr(get_settings(), "drift", None)
        if cfg and hasattr(cfg, "diff_typo_similarity"):
            return float(cfg.diff_typo_similarity)
    except Exception:
        pass
    return 0.88


def get_typo_max_length_diff() -> int:
    """Maximum absolute character length difference (default 6) between baseline and live text for typo qualification."""
    val = os.getenv("SEMABRIDGE_DIFF_TYPO_MAX_LEN_DIFF", "").strip()
    if val and val.isdigit():
        return int(val)
    try:
        from semabridge.core.settings import get_settings
        cfg = getattr(get_settings(), "drift", None)
        if cfg and hasattr(cfg, "diff_typo_max_len_diff"):
            return int(cfg.diff_typo_max_len_diff)
    except Exception:
        pass
    return 6


def get_removal_insert_ratio_threshold() -> float:
    """Maximum ratio of inserted text to deleted text (default 0.5) to treat a mixed edit as net removal rather than rewording."""
    val = os.getenv("SEMABRIDGE_DIFF_REMOVAL_RATIO", "").strip() or os.getenv("SEMABRIDGE_DRIFT_DIFF_REMOVAL_INSERT_RATIO", "").strip()
    if val:
        try:
            return float(val)
        except ValueError:
            pass
    try:
        from semabridge.core.settings import get_settings
        cfg = getattr(get_settings(), "drift", None)
        if cfg and hasattr(cfg, "diff_removal_insert_ratio"):
            return float(cfg.diff_removal_insert_ratio)
    except Exception:
        pass
    return 0.5


def get_semantic_overlap_threshold() -> float:
    """Minimum semantic word overlap ratio (default 0.25) to treat inserted text as a replacement for deleted text."""
    val = os.getenv("SEMABRIDGE_DIFF_SEMANTIC_OVERLAP", "").strip() or os.getenv("SEMABRIDGE_DRIFT_DIFF_SEMANTIC_OVERLAP", "").strip()
    if val:
        try:
            return float(val)
        except ValueError:
            pass
    try:
        from semabridge.core.settings import get_settings
        cfg = getattr(get_settings(), "drift", None)
        if cfg and hasattr(cfg, "diff_semantic_overlap"):
            return float(cfg.diff_semantic_overlap)
    except Exception:
        pass
    return 0.25


def get_removal_length_ratio_threshold() -> float:
    """Ratio threshold (default 1.3) where deleted length exceeding inserted length by this factor indicates net removal."""
    val = os.getenv("SEMABRIDGE_DIFF_REMOVAL_LEN_RATIO", "").strip() or os.getenv("SEMABRIDGE_DRIFT_DIFF_REMOVAL_LEN_RATIO", "").strip()
    if val:
        try:
            return float(val)
        except ValueError:
            pass
    try:
        from semabridge.core.settings import get_settings
        cfg = getattr(get_settings(), "drift", None)
        if cfg and hasattr(cfg, "diff_removal_len_ratio"):
            return float(cfg.diff_removal_len_ratio)
    except Exception:
        pass
    return 1.3


def _tokenize(text: str) -> List[str]:
    """Split text into words and whitespace/punctuation tokens to preserve formatting."""
    return re.findall(r"\w+|[^\w\s]+|\s+", text, re.UNICODE)


def _is_sentence_or_clause(text: str, words: Optional[List[str]] = None) -> bool:
    """Return True if text constitutes a sentence, clause, or substantive instruction."""
    w = words if words is not None else re.findall(r"\b\w+\b", text)
    has_clause_punct = any(p in text for p in (".", ";", ":", "\n"))
    return has_clause_punct or len(w) >= 5 or len(text.strip()) >= 25


def classify_and_diff_description(
    baseline_text: Optional[str],
    live_text: Optional[str],
) -> Dict[str, Any]:
    """Classify the description change, compute marked untruncated diffs, and assess severity."""
    base_raw = (baseline_text or "").strip()
    live_raw = (live_text or "").strip()

    if not base_raw and not live_raw:
        return {
            "classification": "NO_CHANGE",
            "severity": DiffSeverity.LOW.value,
            "diff_segments": [],
            "baseline_marked": "",
            "live_marked": "",
            "removed_caveats": [],
            "similarity_ratio": 1.0,
            "is_uncertain": False,
            "confidence": 1.0,
            "uncertainty_reason": None,
            "summary": "Descriptions are identical or empty.",
        }

    if not base_raw and live_raw:
        is_clause = _is_sentence_or_clause(live_raw)
        severity = DiffSeverity.MEDIUM if is_clause else DiffSeverity.LOW
        return {
            "classification": "TEXT_ADDED",
            "severity": severity.value,
            "diff_segments": [{"type": "insert", "text": live_raw}],
            "baseline_marked": "(no description)",
            "live_marked": f"[ADDED: {live_raw}]",
            "removed_caveats": [],
            "similarity_ratio": 0.0,
            "is_uncertain": False,
            "confidence": 1.0,
            "uncertainty_reason": None,
            "summary": "Description added to warehouse view.",
        }

    if base_raw and not live_raw:
        words = {w.lower() for w in re.findall(r"\b\w+\b", base_raw)}
        removed_cavs = sorted(words & _CAVEAT_KEYWORDS)
        return {
            "classification": "TEXT_REMOVED",
            "severity": DiffSeverity.HIGH.value,
            "diff_segments": [{"type": "delete", "text": base_raw}],
            "baseline_marked": f"[REMOVED: {base_raw}]",
            "live_marked": "(no description)",
            "removed_caveats": removed_cavs,
            "similarity_ratio": 0.0,
            "is_uncertain": False,
            "confidence": 1.0,
            "uncertainty_reason": None,
            "summary": "Description removed completely from warehouse view.",
        }

    base_tokens = _tokenize(base_raw)
    live_tokens = _tokenize(live_raw)

    matcher = difflib.SequenceMatcher(None, base_tokens, live_tokens)
    opcodes = matcher.get_opcodes()

    diff_segments: List[Dict[str, str]] = []
    base_marked_parts: List[str] = []
    live_marked_parts: List[str] = []

    has_deletes = False
    has_inserts = False
    deleted_words: List[str] = []
    inserted_words: List[str] = []

    for tag, i1, i2, j1, j2 in opcodes:
        base_chunk = "".join(base_tokens[i1:i2])
        live_chunk = "".join(live_tokens[j1:j2])

        if tag == "equal":
            diff_segments.append({"type": "equal", "text": base_chunk})
            base_marked_parts.append(base_chunk)
            live_marked_parts.append(live_chunk)
        elif tag == "delete":
            has_deletes = True
            diff_segments.append({"type": "delete", "text": base_chunk})
            base_marked_parts.append(f"[REMOVED: {base_chunk}]")
            deleted_words.extend(re.findall(r"\b\w+\b", base_chunk.lower()))
        elif tag == "insert":
            has_inserts = True
            diff_segments.append({"type": "insert", "text": live_chunk})
            live_marked_parts.append(f"[ADDED: {live_chunk}]")
            inserted_words.extend(re.findall(r"\b\w+\b", live_chunk.lower()))
        elif tag == "replace":
            has_deletes = True
            has_inserts = True
            diff_segments.append({"type": "delete", "text": base_chunk})
            diff_segments.append({"type": "insert", "text": live_chunk})
            base_marked_parts.append(f"[REMOVED: {base_chunk}]")
            live_marked_parts.append(f"[ADDED: {live_chunk}]")
            deleted_words.extend(re.findall(r"\b\w+\b", base_chunk.lower()))
            inserted_words.extend(re.findall(r"\b\w+\b", live_chunk.lower()))

    # Caveat inspection: did removed words contain critical constraint keywords?
    removed_cavs = sorted(set(deleted_words) & _CAVEAT_KEYWORDS)

    # Calculate string similarity ratio for typo detection
    clean_base = re.sub(r"\s+", " ", base_raw).strip()
    clean_live = re.sub(r"\s+", " ", live_raw).strip()
    char_matcher = difflib.SequenceMatcher(None, clean_base.lower(), clean_live.lower())
    sim_ratio = char_matcher.ratio()

    # Thresholds from configuration
    typo_sim_thresh = get_typo_similarity_threshold()
    typo_max_len_diff = get_typo_max_length_diff()
    removal_ratio = get_removal_insert_ratio_threshold()
    semantic_overlap_thresh = get_semantic_overlap_threshold()
    removal_len_ratio = get_removal_length_ratio_threshold()

    deleted_char_len = sum(len(w) for w in deleted_words)
    inserted_char_len = sum(len(w) for w in inserted_words)

    _STOPWORDS = frozenset({
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "has", "he", "in", "is", "it", "its", "of", "on", "or", "that",
        "the", "to", "was", "were", "will", "with", "so", "while",
    })
    del_content = {w for w in deleted_words if w not in _STOPWORDS and len(w) > 1}
    ins_content = {w for w in inserted_words if w not in _STOPWORDS and len(w) > 1}
    word_overlap = (len(del_content & ins_content) / len(del_content)) if del_content else 0.0
    phrase_sim = difflib.SequenceMatcher(None, " ".join(deleted_words), " ".join(inserted_words)).ratio()
    overlap_score = max(word_overlap, phrase_sim)

    is_uncertain = False
    confidence = 1.0
    uncertainty_reason: Optional[str] = None

    if has_deletes and not has_inserts:
        classification = "TEXT_REMOVED"
        severity = DiffSeverity.HIGH
        summary = "Text removed from description (potential caveat or constraint loss)."
    elif has_inserts and not has_deletes:
        classification = "TEXT_ADDED"
        inserted_text = " ".join(inserted_words)
        is_clause = _is_sentence_or_clause(inserted_text, inserted_words)
        severity = DiffSeverity.MEDIUM if is_clause else DiffSeverity.LOW
        summary = "Additional text appended/inserted into description."
    else:
        # Both deletes and inserts exist
        if inserted_char_len == 0 and deleted_char_len > 0:
            classification = "TEXT_REMOVED"
            severity = DiffSeverity.HIGH
            summary = "Text removed from description."
        elif deleted_char_len == 0 and inserted_char_len > 0:
            classification = "TEXT_ADDED"
            severity = DiffSeverity.MEDIUM
            summary = "Text added to description."
        elif deleted_char_len > 0 and inserted_char_len < deleted_char_len * removal_ratio:
            classification = "TEXT_REMOVED"
            severity = DiffSeverity.HIGH
            summary = "Substantial text removed from description (potential constraint or caveat loss)."
        elif deleted_char_len > inserted_char_len and sim_ratio < typo_sim_thresh:
            # Rule 1: A change that deletes more than it adds is a removal (not a rewording)
            classification = "TEXT_REMOVED"
            severity = DiffSeverity.HIGH if (removed_cavs or deleted_char_len >= 25) else DiffSeverity.MEDIUM
            summary = "Text removed from description exceeds text added (net removal of information)."
        elif inserted_char_len > 0 and deleted_char_len < inserted_char_len * removal_ratio:
            classification = "TEXT_ADDED"
            severity = DiffSeverity.MEDIUM
            summary = "Substantial text added to description."
        elif sim_ratio >= typo_sim_thresh and abs(len(clean_base) - len(clean_live)) <= typo_max_len_diff:
            classification = "TEXT_REWORDED"
            severity = DiffSeverity.LOW
            summary = "Typo correction or minor rewording."
        elif removed_cavs and (overlap_score < semantic_overlap_thresh or not (set(inserted_words) & _CAVEAT_KEYWORDS)):
            # Dropped text carried constraints/caveats and inserted text is unrelated or did not replace them
            classification = "TEXT_REMOVED"
            severity = DiffSeverity.HIGH
            summary = f"Text removed with caveat/constraint lost ({', '.join(removed_cavs)})."
        elif deleted_char_len >= inserted_char_len * removal_len_ratio and overlap_score < semantic_overlap_thresh:
            # Substantial net removal without semantic replacement
            classification = "TEXT_REMOVED"
            severity = DiffSeverity.HIGH
            summary = "Substantial text removed with no semantic replacement (potential constraint loss)."
        elif inserted_char_len >= deleted_char_len * removal_len_ratio and overlap_score < semantic_overlap_thresh:
            # Substantial addition of unrelated text
            classification = "TEXT_ADDED"
            severity = DiffSeverity.MEDIUM
            summary = "Substantial text added with unrelated content."
        else:
            # Both deletions and insertions of similar size and/or semantic overlap exist (clause replacement/rewording)
            classification = "TEXT_REWORDED"
            is_uncertain = True
            confidence = round(max(overlap_score, sim_ratio), 2)
            uncertainty_reason = "Both deletions and insertions detected; classification is best-effort rewording."

            # Shape-first severity: keyword list acts only as an auxiliary signal that can elevate severity
            if removed_cavs and not (set(inserted_words) & _CAVEAT_KEYWORDS):
                severity = DiffSeverity.HIGH
                summary = f"Clause replaced with caveat/constraint removed ({', '.join(removed_cavs)})."
            else:
                is_substantive = deleted_char_len >= 15 or len(deleted_words) >= 3
                severity = DiffSeverity.MEDIUM if is_substantive else DiffSeverity.LOW
                summary = "Clause replaced with different wording of similar length."

    return {
        "classification": classification,
        "severity": severity.value if isinstance(severity, DiffSeverity) else str(severity),
        "diff_segments": diff_segments,
        "baseline_marked": "".join(base_marked_parts),
        "live_marked": "".join(live_marked_parts),
        "removed_caveats": removed_cavs,
        "similarity_ratio": round(sim_ratio, 3),
        "is_uncertain": is_uncertain,
        "confidence": confidence,
        "uncertainty_reason": uncertainty_reason,
        "summary": summary,
    }
