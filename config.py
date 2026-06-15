# -*- coding: utf-8 -*-
"""Unified configuration system — inspired by MemPalace config.py.

Priority: env vars > config file (~/.memery/config.json) > defaults
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

# ── Name validation ────────────────────────────────────────────────────
MAX_NAME_LENGTH = 128
_SAFE_NAME_RE = re.compile(r"^(?:[^\W_]|[^\W_][\w .'-]{0,126}[^\W_])$")
_LONE_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def strip_lone_surrogates(text: str) -> str:
    """Replace lone UTF-16 surrogates so string is legal UTF-8."""
    return _LONE_SURROGATE_RE.sub("\ufffd", text)


def sanitize_name(value: str, field_name: str = "name") -> str:
    """Validate and sanitize a wing/room/entity name."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string, got {type(value).__name__}")
    if len(value) > MAX_NAME_LENGTH:
        raise ValueError(f"{field_name} too long: {len(value)} > {MAX_NAME_LENGTH}")
    clean = strip_lone_surrogates(value).strip()
    if not clean:
        raise ValueError(f"{field_name} cannot be empty")
    return clean


def normalize_slug(name: str) -> str:
    """Lower-case + collapse separators to _"""
    return name.lower().replace(" ", "_").replace("-", "_").strip("_")


# ── Scoring weights — aligned with Graphify confidence levels ──────────

@dataclass
class ScoreWeights:
    importance: float = 0.30
    confidence: float = 0.20
    novelty: float = 0.15
    reusability: float = 0.20
    actionability: float = 0.15


# ── Thresholds ─────────────────────────────────────────────────────────

@dataclass
class Thresholds:
    write: float = 0.75       # score >= threshold → write to long-term memory
    candidate: float = 0.50   # score >= threshold → keep as candidate
    dedup: float = 0.85       # similarity > threshold → merge instead of create


# ── Confidence labels (from Graphify) ──────────────────────────────────

class Confidence(str):
    EXTRACTED = "EXTRACTED"    # Explicitly stated in source
    INFERRED = "INFERRED"      # Reasonable deduction
    AMBIGUOUS = "AMBIGUOUS"    # Uncertain, flagged for review


# ── Memory types ───────────────────────────────────────────────────────

HIGH_VALUE_TYPES = frozenset({
    "architecture", "decision", "api_contract", "db_schema",
    "frontend_page", "backend_endpoint", "bug", "task_snapshot",
    "deployment", "coding_rule", "dependency", "integration",
    "project_summary", "project_core", "latest_conversation_summary", "product_rule",
    "goal", "principle", "preference", "fact", "event", "plan", "note",
})

PROTECTED_TYPES = frozenset({
    "decision", "api_contract", "db_schema", "project_summary",
    "project_core", "latest_conversation_summary",
})


# ── Main config ────────────────────────────────────────────────────────

CONFIG_DIR = Path.home() / ".memery"
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class MemeryConfig:
    """Global memery configuration."""

    # Paths
    data_dir: str = ""
    db_path: str = ""

    # Scoring
    score_weights: ScoreWeights = field(default_factory=ScoreWeights)
    thresholds: Thresholds = field(default_factory=Thresholds)

    # Embedding
    embedding_dim: int = 256
    vector_backend: str = "lancedb"  # lancedb | chroma

    # Palace
    default_wing: str = "default"
    drawer_chunk_size: int = 800  # chars per drawer
    palace_vector_enabled: bool = False

    # Graph
    community_max_fraction: float = 0.25  # max community size before split
    god_node_top_n: int = 10

    # Cache
    cache_enabled: bool = True
    cache_dir: str = ""

    # MCP
    mcp_tool_prefix: str = "memery_"

    # Hall keywords — route content to memory type
    hall_keywords: dict = field(default_factory=lambda: {
        "facts": ["是", "is", "事实", "fact"],
        "events": ["发生了", "happened", "event", "事件"],
        "decisions": ["决定", "decision", "选择", "chose", "采用", "选定"],
        "preferences": ["偏好", "prefer", "喜欢", "like", "常用"],
        "architecture": ["架构", "architecture", "模块", "module", "设计", "design"],
        "bugs": ["bug", "错误", "error", "fix", "修复", "defect"],
        "rules": ["禁止", "不允许", "严禁", "不能", "不得", "必须", "must", "rule"],
        "tasks": ["任务", "task", "todo", "待办", "完成", "done"],
    })

    def __post_init__(self):
        # Runtime data belongs in the user profile, not the installed package.
        if not self.data_dir:
            self.data_dir = str(CONFIG_DIR / "data")
        if not self.db_path:
            self.db_path = str(CONFIG_DIR / "memory.db")
        if not self.cache_dir:
            self.cache_dir = str(CONFIG_DIR / "cache")

    @classmethod
    def from_env(cls) -> MemeryConfig:
        """Load config from env vars, then file, then defaults."""
        cfg = cls()

        # Env overrides
        if os.environ.get("MEMERY_DATA_DIR"):
            cfg.data_dir = os.environ["MEMERY_DATA_DIR"]
        if os.environ.get("MEMERY_DB_PATH"):
            cfg.db_path = os.environ["MEMERY_DB_PATH"]
        if os.environ.get("MEMERY_VECTOR_BACKEND"):
            cfg.vector_backend = os.environ["MEMERY_VECTOR_BACKEND"]
        if os.environ.get("MEMERY_EMBEDDING_DIM"):
            cfg.embedding_dim = int(os.environ["MEMERY_EMBEDDING_DIM"])
        if os.environ.get("MEMERY_PALACE_VECTOR_ENABLED"):
            cfg.palace_vector_enabled = os.environ[
                "MEMERY_PALACE_VECTOR_ENABLED"
            ].lower() in {"1", "true", "yes", "on"}

        # File overrides
        if CONFIG_FILE.exists():
            try:
                file_cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                for key, val in file_cfg.items():
                    if hasattr(cfg, key):
                        setattr(cfg, key, val)
            except (json.JSONDecodeError, OSError):
                pass

        return cfg

    def save(self) -> None:
        """Persist config to file."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "data_dir": self.data_dir,
            "db_path": self.db_path,
            "vector_backend": self.vector_backend,
            "embedding_dim": self.embedding_dim,
            "drawer_chunk_size": self.drawer_chunk_size,
            "palace_vector_enabled": self.palace_vector_enabled,
            "cache_enabled": self.cache_enabled,
            "cache_dir": self.cache_dir,
        }
        CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# Singleton
_config: MemeryConfig | None = None


def get_config() -> MemeryConfig:
    global _config
    if _config is None:
        _config = MemeryConfig.from_env()
    return _config
