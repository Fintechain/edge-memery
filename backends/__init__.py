# -*- coding: utf-8 -*-
"""Backends - Pluggable vector store abstraction layer.

Inspired by MemPalace backends/base.py:
  - BaseVectorStore — abstract contract
  - QueryResult — typed result dataclass
  - Registry for backend discovery
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar

from ..config import get_config


# ── Errors ─────────────────────────────────────────────────────────────

class BackendError(Exception):
    """Base class for backend errors."""


class BackendNotInitializedError(BackendError):
    """Backend has not been initialized."""


# ── Result types ────────────────────────────────────────────────────────

@dataclass
class QueryResult:
    id: str
    text: str = ""
    similarity: float = 0.0
    metadata: dict = field(default_factory=dict)


# ── Base contract ──────────────────────────────────────────────────────

class BaseVectorStore(ABC):
    """Every vector backend must implement this interface."""

    backend_name: ClassVar[str] = "base"

    @abstractmethod
    def insert(self, memory_id: str, project_id: str, text: str,
               metadata: dict | None = None) -> None:
        """Insert a single memory embedding."""

    @abstractmethod
    def insert_batch(self, items: list[dict]) -> None:
        """Batch insert. Each item: {id, project_id, text, metadata?}."""

    @abstractmethod
    def search(self, query: str, project_id: str | None = None,
               limit: int = 10) -> list[QueryResult]:
        """Semantic search."""

    @abstractmethod
    def delete(self, memory_id: str) -> None:
        """Remove a memory embedding."""

    @abstractmethod
    def delete_by_project(self, project_id: str) -> None:
        """Remove all embeddings for a project."""

    @abstractmethod
    def update(self, memory_id: str, project_id: str, text: str,
               metadata: dict | None = None) -> None:
        """Update a memory embedding."""

    @abstractmethod
    def count(self) -> int:
        """Return total embedding count."""


# ── Backend registry ───────────────────────────────────────────────────

_registry: dict[str, type[BaseVectorStore]] = {}


def register_backend(name: str, cls: type[BaseVectorStore]) -> None:
    """Register a backend implementation."""
    _registry[name] = cls


def get_backend(name: str | None = None) -> BaseVectorStore:
    """Get a vector store backend by name (default: from config)."""
    cfg = get_config()
    backend_name = name or cfg.vector_backend
    if backend_name not in _registry:
        raise BackendError(
            f"Backend '{backend_name}' not found. Available: {list(_registry)}"
        )
    return _registry[backend_name]()


def list_backends() -> list[str]:
    return list(_registry.keys())


# Auto-discover backends
def _discover_backends() -> None:
    """Import all backend modules so they self-register."""
    try:
        from . import lancedb_backend  # noqa: F401
    except ImportError:
        pass


_discover_backends()
