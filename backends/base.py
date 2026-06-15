# -*- coding: utf-8 -*-
"""Pluggable vector-store backend system.

Contract: every backend implements BaseVectorStore.
Inspired by: MemPalace backends/base.py + MemPalace backends/registry.py
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


# ── Errors ──────────────────────────────────────────────────────────────

class BackendError(Exception):
    """Base class for backend errors."""


class BackendNotAvailableError(BackendError):
    """Requested backend is not installed."""


class BackendClosedError(BackendError):
    """Backend method called after close()."""


# ── Result types ────────────────────────────────────────────────────────

@dataclass
class VectorSearchResult:
    id: str
    text: str = ""
    similarity: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass
class HealthStatus:
    ok: bool
    backend: str = ""
    item_count: int = 0
    error: str = ""


# ── Base contract ───────────────────────────────────────────────────────

class BaseVectorStore(ABC):
    """Contract every vector backend must satisfy."""

    backend_name: str = "base"

    @abstractmethod
    def insert(self, memory_id: str, text: str, metadata: dict | None = None) -> None: ...

    @abstractmethod
    def insert_batch(self, items: list[dict]) -> None: ...

    @abstractmethod
    def search(self, query: str, filter_meta: dict | None = None,
               limit: int = 10) -> list[VectorSearchResult]: ...

    @abstractmethod
    def search_by_ids(self, ids: list[str]) -> list[VectorSearchResult]: ...

    @abstractmethod
    def delete(self, memory_id: str) -> None: ...

    @abstractmethod
    def delete_by_filter(self, filter_meta: dict) -> int: ...

    @abstractmethod
    def update(self, memory_id: str, text: str,
               metadata: dict | None = None) -> None: ...

    @abstractmethod
    def count(self) -> int: ...

    @abstractmethod
    def health(self) -> HealthStatus: ...

    @abstractmethod
    def close(self) -> None: ...


# ── Registry ────────────────────────────────────────────────────────────

_registry: dict[str, type[BaseVectorStore]] = {}


def register_backend(name: str):
    """Decorator to register a backend class."""
    def dec(cls: type[BaseVectorStore]):
        _registry[name] = cls
        cls.backend_name = name
        return cls
    return dec


def get_backend(name: str | None = None, **kwargs) -> BaseVectorStore:
    """Factory: create a backend instance by name.

    If name is None, uses config default (lancedb).
    Falls back to lancedb if requested backend is unavailable.
    """
    if name is None:
        from .config import get_config
        name = get_config().vector_backend

    if name not in _registry:
        raise BackendNotAvailableError(
            f"Backend '{name}' not registered. Available: {list(_registry)}")

    try:
        return _registry[name](**kwargs)
    except ImportError as e:
        raise BackendNotAvailableError(
            f"Backend '{name}' requires missing dependency: {e}")
