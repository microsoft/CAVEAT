# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tiny decorator-based registry used for environments and scaffolds.

    from caveat.core.registry import Registry
    SCAFFOLDS = Registry("scaffold")

    @SCAFFOLDS.register("browseruse")
    class BrowserUseScaffold(Scaffold): ...

    SCAFFOLDS.create("browseruse", **kwargs)   # instantiate by name
    SCAFFOLDS.names()                          # ["browseruse", ...]

Registration happens at import time, so a plug-in only needs to be imported
(see ``caveat.scaffolds`` / ``caveat.envs`` which import their members).
"""

from __future__ import annotations

from typing import Callable, Generic, TypeVar

T = TypeVar("T")


class Registry(Generic[T]):
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._items: dict[str, Callable[..., T]] = {}

    def register(self, name: str) -> Callable[[Callable[..., T]], Callable[..., T]]:
        def deco(obj: Callable[..., T]) -> Callable[..., T]:
            if name in self._items:
                raise ValueError(f"{self.kind} {name!r} already registered")
            self._items[name] = obj
            return obj
        return deco

    def get(self, name: str) -> Callable[..., T]:
        if name not in self._items:
            raise KeyError(
                f"unknown {self.kind} {name!r}. Registered: {', '.join(sorted(self._items)) or '(none)'}")
        return self._items[name]

    def create(self, name: str, *args, **kwargs) -> T:
        return self.get(name)(*args, **kwargs)

    def names(self) -> list[str]:
        return sorted(self._items)

    def __contains__(self, name: str) -> bool:
        return name in self._items
