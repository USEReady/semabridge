"""The deploy-side outline every target platform follows (Phase 3.1).

``TargetRules`` (semabridge.target_rules) answers how a platform *spells and
decides* about one measure. A ``TargetBackend`` is the rest of a platform's
deploy side, one method per Phase 3 step, each taking the engine's
RunContext:

    rules           the platform's TargetRules                       (3.1)
    match_tables()  bind each model table to a real table, or a stub (3.4)
    plan()          lay out the target objects (e.g. metric views)    (3.6)
    write()         produce the artifacts (e.g. metric view YAML)     (3.7)
    deploy()        create them through the guarded client           (3.8)
    verify()        check what went live                              (3.9)

Steps not built yet raise NotImplementedError naming their step, so nothing
can call a half-built backend by accident. Snowflake keeps its existing
emitter and is not wrapped in a backend.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from semabridge.target_rules.base import TargetRules

if TYPE_CHECKING:  # engine types are only needed for annotations
    from semabridge.core.engine.context import RunContext


class TargetBackendNotReady(NotImplementedError):
    """A backend step that has not been built yet."""


class TargetBackend:
    """Deploy-side steps for one target platform."""

    #: Platform name, same as TargetRules.target (e.g. "databricks").
    target: str = ""

    def __init__(self, rules: TargetRules) -> None:
        if self.target and rules.target != self.target:
            raise ValueError(f"{type(self).__name__} needs {self.target!r} rules, got {rules.target!r}")
        self._rules = rules

    @property
    def rules(self) -> TargetRules:
        return self._rules

    def _not_ready(self, step: str) -> TargetBackendNotReady:
        return TargetBackendNotReady(f"{self.target or type(self).__name__}: {step} is not built yet")

    def match_tables(self, context: "RunContext") -> Any:
        raise self._not_ready("table matching (3.4)")

    def plan(self, context: "RunContext") -> Any:
        raise self._not_ready("planning (3.6)")

    def write(self, context: "RunContext") -> Any:
        raise self._not_ready("artifact writing (3.7)")

    def deploy(self, context: "RunContext") -> Any:
        raise self._not_ready("deploy (3.8)")

    def verify(self, context: "RunContext") -> Any:
        raise self._not_ready("verification (3.9)")
