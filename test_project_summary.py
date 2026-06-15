"""Tests for the self-maintaining project summary."""

from __future__ import annotations

from memory_server.curator import MemoryCurator
from memory_server.db import MemoryDB


class FakeVectorStore:
    def __init__(self) -> None:
        self.items: dict[str, tuple[str, str]] = {}

    def insert(self, memory_id: str, project_id: str, text: str, metadata=None) -> None:
        self.items[memory_id] = (project_id, text)

    def update(self, memory_id: str, project_id: str, text: str, metadata=None) -> None:
        self.items[memory_id] = (project_id, text)

    def delete(self, memory_id: str) -> None:
        self.items.pop(memory_id, None)

    def search(self, query: str, project_id: str | None = None, limit: int = 10):
        return []


class FakePalace:
    @staticmethod
    def ensure_wing(db, project_name: str):
        project = db.get_project_by_name(project_name)
        return db.list_wings(project["id"])[0]

    @staticmethod
    def route_to_hall(content: str) -> str:
        return "architecture"


def test_refresh_project_summary_updates_single_memory(tmp_path):
    db = MemoryDB(str(tmp_path / "memory.db"))
    vectors = FakeVectorStore()
    curator = MemoryCurator(db, vectors)
    project = db.create_project("summary-test", description="An edge-first memory server.")

    db.write_memory(
        project_id=project["id"], memory_type="architecture",
        title="Core architecture", content="SQLite stores facts and a vector index supports recall.",
        importance=0.9, confidence=0.9, novelty=0.7, reusability=0.9, actionability=0.7,
    )
    db.record_task_snapshot(
        project["id"], "automatic summary", "in_progress",
        completed=["Implemented durable project summary"],
        remaining=["Expose it during wake-up"],
    )
    db.record_decision(
        project["id"], "One summary per project",
        "Update the same generated memory instead of appending summaries.",
    )

    first = curator.refresh_project_summary(project_id=project["id"])["summary"]
    assert "## 代码与系统核心思想" in first["content"]
    assert "Core architecture" in first["content"]
    assert "Implemented durable project summary" in first["content"]
    assert "Expose it during wake-up" in first["content"]

    db.write_memory(
        project_id=project["id"], memory_type="bug",
        title="Wake-up fixed", content="已修复启动时缺少项目摘要的问题。",
        importance=0.8, confidence=0.9, novelty=0.8, reusability=0.7, actionability=0.9,
    )
    second = curator.refresh_project_summary(project_id=project["id"])["summary"]

    assert second["id"] == first["id"]
    assert "Wake-up fixed" in second["content"]
    summaries = db.search_memories(
        project_id=project["id"], memory_type="project_summary", limit=10,
    )
    assert len(summaries) == 1
    assert second["id"] not in vectors.items
    db.close()


def test_accepting_candidate_refreshes_summary(tmp_path):
    db = MemoryDB(str(tmp_path / "memory.db"))
    vectors = FakeVectorStore()
    curator = MemoryCurator(db, vectors)
    project = db.create_project("candidate-test")
    candidate = db.create_candidate(
        project_id=project["id"], source_type="conversation",
        raw_text="The API layer is implemented with explicit request validation.",
        extracted_title="Validated API layer",
        extracted_content="The API layer is implemented with explicit request validation.",
        candidate_type="api_contract", importance=0.9, confidence=0.9,
        novelty=0.8, reusability=0.8, actionability=0.8,
    )

    result = curator.review_candidate(candidate["id"], "accepted")

    assert result["decision"] == "accepted"
    summary = db.get_project_summary(project["id"])
    assert summary is not None
    assert "Validated API layer" in summary["content"]
    db.close()


def test_ingest_auto_accepts_explicit_completed_architecture(tmp_path):
    db = MemoryDB(str(tmp_path / "memory.db"))
    curator = MemoryCurator(db, FakeVectorStore())
    curator._palace = FakePalace()
    project = db.create_project("ingest-test")

    result = curator.ingest_conversation(
        "ingest-test",
        "项目必须采用模块化架构，所有代码必须通过统一记忆接口存储，当前已经完成自动项目摘要实现和启动上下文接入。",
    )

    assert result["auto_accepted"] == 1
    assert result["pending_review"] == 0
    summary = db.get_project_summary(project["id"])
    assert summary is not None
    assert "模块化架构" in summary["content"]
    assert "最新对话总结" in summary["content"]
    assert "当前实现与已完成内容" in summary["content"]
    db.close()


def test_latest_conversation_is_replaced_and_pinned(tmp_path):
    db = MemoryDB(str(tmp_path / "memory.db"))
    curator = MemoryCurator(db, FakeVectorStore())
    curator._palace = FakePalace()
    project = db.create_project("latest-test", description="核心是边端持久记忆。")

    curator.ingest_conversation(
        "latest-test",
        "第一次对话希望补充旧的搜索接口，并记录这个临时需求。",
    )
    first = db.get_singleton_memory(project["id"], "latest_conversation_summary")
    assert first is not None
    assert "第一次对话" in first["content"]

    curator.ingest_conversation(
        "latest-test",
        "第二次对话要求把项目核心思想和最新对话总结固定在记忆顶层。",
    )
    second = db.get_singleton_memory(project["id"], "latest_conversation_summary")
    assert second is not None
    assert second["id"] == first["id"]
    assert "第二次对话" in second["content"]
    assert "第一次对话" not in second["content"]
    assert "## 要求与后续\n- 第二次对话要求" in second["content"]

    memories = db.list_memories(project_id=project["id"], limit=20)
    assert [memory["memory_type"] for memory in memories[:3]] == [
        "project_core", "latest_conversation_summary", "project_summary",
    ]
    assert "边端持久记忆" in memories[0]["content"]

    curator.prune_low_value_memories("latest-test")
    curator.compact_project_memory("latest-test")
    assert db.get_singleton_memory(project["id"], "project_core") is not None
    assert db.get_singleton_memory(
        project["id"], "latest_conversation_summary",
    ) is not None
    db.close()
