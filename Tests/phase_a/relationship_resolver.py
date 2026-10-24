"""
relationship_resolver.py - Phase A, REPORT-ONLY relationship resolver.

Builds a RelationshipPlan from the same inputs SemaBridge's relationship builder receives,
and says what SHOULD happen to every source relationship. It never changes the model or
the generated DDL; it only produces a plan you can compare with what was actually emitted.

Generic: decisions use only relationship metadata (active flag, cardinality, columns),
declared keys, live column lists and, when available, key uniqueness. No table names.

Decisions (one per source relationship, never skipped):
  emit                  referenced side has a matching declared key -> emit as is
  emit_add_primary_key  referenced side has NO key at all; columns verified unique (or 1:1)
                        -> declare PRIMARY KEY on the referenced side, then emit
  emit_add_unique_key   referenced side has a different PRIMARY KEY; columns verified unique
                        -> declare UNIQUE(<cols>) on the referenced side, then emit
  emit_via_alias        inactive in the source -> emit against a role-playing alias of the
                        referenced table with its own PRIMARY KEY (keeps the default path clean)
  reject                cannot be represented correctly; reason given
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

UniqueCheck = Callable[[str, Sequence[str]], Optional[bool]]   # (dataset, cols) -> True/False/None(unknown)


@dataclass
class KeyPlan:
    logical_name: str
    physical: str
    primary_key: List[str] = field(default_factory=list)
    unique_keys: List[List[str]] = field(default_factory=list)
    is_alias_of: Optional[str] = None
    note: str = ""


@dataclass
class RelationshipDecision:
    source: str
    source_active: bool
    source_cardinality: str
    decision: str
    from_logical: Optional[str]
    to_logical: Optional[str]
    from_columns: List[str]
    to_columns: List[str]
    key_verified: Optional[bool]
    reason: str


@dataclass
class RelationshipPlan:
    keys: Dict[str, KeyPlan]
    decisions: List[RelationshipDecision]

    def as_dict(self):
        return {"keys": {k: asdict(v) for k, v in self.keys.items()},
                "decisions": [asdict(d) for d in self.decisions]}


def _card(rel) -> str:
    raw = str(getattr(rel, "cardinality", "") or "").strip().lower().replace("_", "-")
    raw = raw.split(".")[-1]                       # enum reprs like Cardinality.ONE_TO_ONE
    return {"m:1": "many-to-one", "1:1": "one-to-one", "1:m": "one-to-many", "m:m": "many-to-many"}.get(raw, raw)


def _u(cols) -> List[str]:
    return [str(c).strip('"').upper() for c in (cols or [])]


def _safe(name: str) -> str:
    return re.sub(r"[^A-Z0-9_]", "_", name.upper())


def build_plan(
    relationships: Sequence,
    dataset_aliases: Dict[str, str],
    declared_pk_by_alias: Dict[str, List[str]],
    dataset_col_lookup: Dict[str, set],
    resolve_column: Callable[[str, str], str] = lambda ds, c: c,
    is_unique: Optional[UniqueCheck] = None,
    allow_cardinality_hint: bool = False,
) -> RelationshipPlan:
    """All inputs are what SemaBridge's _build_relationships already receives.

    allow_cardinality_hint=False (default, the agreed rule): a key is only planned when
    is_unique() returns True; unknown uniqueness -> 'reject' with reason
    'rejected_unverified_key'. True is only for report-only tracing without live data."""
    is_unique = is_unique or (lambda ds, cols: None)
    keys: Dict[str, KeyPlan] = {
        alias: KeyPlan(alias, ds, primary_key=_u(declared_pk_by_alias.get(alias)))
        for ds, alias in dataset_aliases.items()
    }
    decisions: List[RelationshipDecision] = []

    for rel in relationships:
        f_ds, t_ds = getattr(rel, "from_dataset", None), getattr(rel, "to_dataset", None)
        f_cols = _u(resolve_column(f_ds, c) for c in (getattr(rel, "from_columns", None) or []))
        t_cols = _u(resolve_column(t_ds, c) for c in (getattr(rel, "to_columns", None) or []))
        active = bool(getattr(rel, "is_active", True))
        card = _card(rel)
        src = f"{f_ds}{list(getattr(rel, 'from_columns', []) or [])} -> {t_ds}{list(getattr(rel, 'to_columns', []) or [])}"
        f_alias, t_alias = dataset_aliases.get(f_ds), dataset_aliases.get(t_ds)

        def decide(decision, reason, to_logical=t_alias, verified=None):
            decisions.append(RelationshipDecision(src, active, card, decision, f_alias, to_logical,
                                                  f_cols, t_cols, verified, reason))

        if not f_alias or not t_alias:
            decide("reject", f"no logical table for {'from' if not f_alias else 'to'} dataset "
                             f"'{f_ds if not f_alias else t_ds}' (alias resolution)")
            continue
        if not f_cols or not t_cols or len(f_cols) != len(t_cols):
            decide("reject", f"key columns missing or count mismatch ({len(f_cols)} vs {len(t_cols)})")
            continue
        missing = [c for c in f_cols if dataset_col_lookup.get(f_ds) and c not in _u(dataset_col_lookup[f_ds])] + \
                  [c for c in t_cols if dataset_col_lookup.get(t_ds) and c not in _u(dataset_col_lookup[t_ds])]
        if missing:
            decide("reject", f"column(s) not found in live table: {missing}")
            continue
        if card == "many-to-many":
            decide("reject", "many-to-many cannot be a REFERENCES relationship; needs a bridge table")
            continue

        verified = is_unique(t_ds, t_cols)
        if verified is False:
            decide("reject", f"referenced columns {t_cols} on '{t_alias}' are not unique in the data",
                   verified=verified)
            continue
        if verified is None:
            already = keys[t_alias].primary_key == t_cols or t_cols in keys[t_alias].unique_keys
            if not already and not (allow_cardinality_hint and card in ("one-to-one", "many-to-one")):
                decide("reject", f"rejected_unverified_key: uniqueness of {t_cols} on '{t_alias}' "
                                 f"could not be verified (no live data); retry with live data",
                       verified=verified)
                continue

        if not active:
            alias_name = _safe(f"{t_alias}_{'_'.join(t_cols)}")
            n = 2
            while alias_name in keys and keys[alias_name].is_alias_of != t_alias:
                alias_name = _safe(f"{t_alias}_{'_'.join(t_cols)}_{n}"); n += 1
            keys.setdefault(alias_name, KeyPlan(alias_name, t_ds, primary_key=t_cols, is_alias_of=t_alias,
                                                note="role-playing alias for an INACTIVE source relationship"))
            decide("emit_via_alias", "inactive in source: alias keeps the default join path unambiguous",
                   to_logical=alias_name, verified=verified)
            continue

        kp = keys[t_alias]
        if kp.primary_key == t_cols or t_cols in kp.unique_keys:
            decide("emit", "referenced side already declares this key", verified=verified)
        elif not kp.primary_key:
            kp.primary_key = t_cols
            kp.note = (kp.note + "; " if kp.note else "") + f"PRIMARY KEY {t_cols} added (referenced by {src})"
            decide("emit_add_primary_key", "referenced side had no key declared (e.g. fact-table rule)",
                   verified=verified)
        else:
            kp.unique_keys.append(t_cols)
            kp.note = (kp.note + "; " if kp.note else "") + f"UNIQUE {t_cols} added (referenced by {src})"
            decide("emit_add_unique_key", f"referenced side's PRIMARY KEY is {kp.primary_key}; "
                                          f"needs UNIQUE {t_cols}", verified=verified)
    return RelationshipPlan(keys, decisions)
