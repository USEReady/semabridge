"""Secrets scoped to the current run (the user's LLM provider API keys).

Each run executes in its own thread / asyncio context; ``run_secrets`` puts
the run's keys in a ContextVar and every LLM key lookup goes through
``get_secret``, which prefers the run's value over the process environment.
Different users' runs therefore never see each other's keys and do NOT wait
for each other (the old process-wide ``os.environ`` gate serialized them).

Thread pools that copy the context (run_blocking, the per-model thread
executor) see the scope. A process pool gets the keys explicitly through
``install_in_process`` (its children belong to one run).
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType
from typing import Dict, Iterator, Mapping, Optional

_EMPTY: Mapping[str, str] = MappingProxyType({})
_RUN_SECRETS: ContextVar[Mapping[str, str]] = ContextVar("sb_run_secrets", default=_EMPTY)


def get_secret(name: Optional[str], default: Optional[str] = None) -> Optional[str]:
    """The current run's value for ``name``, else the environment's, else ``default``."""
    if not name:
        return default
    value = _RUN_SECRETS.get().get(name)
    if value:
        return value
    return os.environ.get(name, default)


def current_secrets() -> Dict[str, str]:
    return dict(_RUN_SECRETS.get())


@contextmanager
def run_secrets(values: Mapping[str, str]) -> Iterator[Mapping[str, str]]:
    """Scope ``values`` (overlaid on any outer scope) to this context."""
    merged = {**_RUN_SECRETS.get(), **{k: v for k, v in (values or {}).items() if k and v}}
    token = _RUN_SECRETS.set(MappingProxyType(merged))
    try:
        yield _RUN_SECRETS.get()
    finally:
        _RUN_SECRETS.reset(token)


def install_in_process(values: Mapping[str, str]) -> None:
    """ProcessPoolExecutor initializer: a child process serves one run, so its
    own environment can hold that run's keys (also for libraries that read
    os.environ themselves)."""
    for key, value in (values or {}).items():
        if key and value:
            os.environ[key] = value
    _RUN_SECRETS.set(MappingProxyType(dict(values or {})))
