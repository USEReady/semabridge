from __future__ import annotations

from typing import Callable, Optional


class FunctionRegistry:
    def __init__(self) -> None:
        self._registry: dict[str, Callable[..., object]] = {}

    def register_function(self, name: str, transformer: Callable[..., object]) -> None:
        self._registry[name.upper()] = transformer

    def get_transformer(self, name: str) -> Optional[Callable[..., object]]:
        return self._registry.get(str(name).upper())


default_registry = FunctionRegistry()


def register_function(name: str, transformer: Callable[..., object]) -> None:
    default_registry.register_function(name, transformer)


def get_transformer(name: str):
    return default_registry.get_transformer(name)
