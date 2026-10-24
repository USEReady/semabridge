"""
reference_normalizer.py - REFERENCE behaviour for relationship normalisation (not used by SemaBridge).

Documents the agreed rules so the regression tests have something to check against before the
real fix exists, and so the real `_normalize_relationships` fix can be tested with the same tests.

Rules
  1. Duplicates are decided by FULL identity: (from table, from columns, to table, to columns).
     Two relationships between the same tables on different columns are BOTH kept.
  2. Inactive relationships are never pruned and never count as a path for other relationships.
     They are passed on marked `needs_alias`.
  3. Reachability for cycle / transitive checks uses ACTIVE relationships only.
  4. An active relationship that is genuinely redundant (another active path exists) is NOT
     dropped silently: it is kept and marked `multi_path` so a later step can alias it, add
     USING, or reject it with a reason.
  5. Every input relationship gets exactly one decision.
"""
from collections import defaultdict, deque
from typing import Dict, List, Sequence, Tuple


def identity(rel) -> Tuple:
    def cols(c):
        return tuple(str(x).strip().casefold() for x in (c or []))
    return (str(rel.from_dataset).casefold(), cols(rel.from_columns),
            str(rel.to_dataset).casefold(), cols(rel.to_columns))


def _has_active_path(graph: Dict[str, set], src: str, dst: str) -> bool:
    seen, q = {src}, deque([src])
    while q:
        n = q.popleft()
        if n == dst:
            return True
        for m in graph.get(n, ()):
            if m not in seen:
                seen.add(m); q.append(m)
    return False


def normalize(relationships: Sequence) -> Tuple[List, List[Dict]]:
    """-> (kept relationships, decisions[{identity, decision, reason}])"""
    kept, decisions, seen = [], [], set()
    actives = []
    for rel in relationships:
        ident = identity(rel)
        if ident in seen:
            decisions.append({"identity": ident, "decision": "dropped_duplicate",
                              "reason": "exact duplicate of an earlier relationship (full identity)"})
            continue
        seen.add(ident)
        if not getattr(rel, "is_active", True):
            kept.append(rel)
            decisions.append({"identity": ident, "decision": "kept_needs_alias",
                              "reason": "inactive in source: kept, not a path, needs a role-playing alias"})
            continue
        actives.append(rel)

    for rel in actives:
        ident = identity(rel)
        graph = defaultdict(set)                        # active edges except this one
        for other in actives:
            if other is not rel:
                graph[str(other.from_dataset).casefold()].add(str(other.to_dataset).casefold())
        kept.append(rel)
        if _has_active_path(graph, ident[0], ident[2]):
            decisions.append({"identity": ident, "decision": "kept_multi_path",
                              "reason": "another ACTIVE path exists between these tables; "
                                        "resolve by alias/USING later, never drop silently"})
        else:
            decisions.append({"identity": ident, "decision": "kept", "reason": ""})
    return kept, decisions
