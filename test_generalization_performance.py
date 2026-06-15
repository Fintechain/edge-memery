"""Generalization and concurrency regression tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import builtins

import pytest

from memory_server.curator import MemoryCurator
from memory_server.db import MemoryDB
from memory_server import palace
from memory_server.pipeline import _load_graphify


class NullVectorStore:
    def insert(self, *args, **kwargs) -> None:
        pass

    def insert_batch(self, *args, **kwargs) -> None:
        pass

    def update(self, *args, **kwargs) -> None:
        pass

    def delete(self, *args, **kwargs) -> None:
        pass

    def search(self, *args, **kwargs) -> list:
        return []


def test_general_context_profiles_and_classification(tmp_path):
    db = MemoryDB(str(tmp_path / "memory.db"))
    curator = MemoryCurator(db, NullVectorStore())
    project = db.create_project(
        "research-context",
        description="研究低功耗设备上的长期记忆检索。",
        context_type="research",
    )
    db.write_memory(
        project_id=project["id"], memory_type="goal",
        title="研究目标", content="验证边端检索在低资源设备上的可行性。",
        importance=0.9, confidence=0.9, reusability=0.8,
    )

    refreshed = curator.refresh_project_summary(project_id=project["id"])

    assert "研究核心问题、假设与方法" in refreshed["project_core"]["content"]
    assert curator._heuristic_score("我希望三个月内掌握这门课程")["candidate_type"] == "goal"
    assert curator._heuristic_score("我偏好每天早晨复习")["candidate_type"] == "preference"
    assert curator._heuristic_score("所有会议结论必须有负责人")["candidate_type"] == "principle"
    db.close()


def test_batch_write_and_concurrent_mixed_access(tmp_path):
    db = MemoryDB(str(tmp_path / "memory.db"))
    project = db.create_project("concurrency-context", context_type="general")
    rows = [{
        "project_id": project["id"],
        "memory_type": "fact",
        "title": f"seed-{index}",
        "content": f"seed content {index}",
    } for index in range(200)]
    assert len(db.write_memories_batch(rows)) == 200

    errors = []

    def operation(index: int):
        if index % 5 == 0:
            return db.write_memory(
                project_id=project["id"], memory_type="event",
                title=f"event-{index}", content=f"event content {index}",
            )
        return db.search_memories(project_id=project["id"], limit=10)

    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(operation, index) for index in range(500)]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as exc:
                errors.append(exc)

    assert errors == []
    assert db.get_stats(project["id"])["total_memories"] == 300
    db.close()


def test_top_level_reads_do_not_rebuild_context(tmp_path):
    db = MemoryDB(str(tmp_path / "memory.db"))
    curator = MemoryCurator(db, NullVectorStore())
    project = db.create_project("read-context", context_type="general")
    first = curator.refresh_project_summary(project_id=project["id"])
    before = first["summary"]["updated_at"]

    for _ in range(20):
        context = curator.get_top_level_context(project_id=project["id"])
        assert context["summary"]["updated_at"] == before

    revision = db.get_project_revision(project["id"])
    db.record_memory_hit(first["summary"]["id"])
    assert db.get_project_revision(project["id"]) == revision

    db.write_memory(
        project_id=project["id"], memory_type="goal",
        title="new goal", content="新增一个需要进入摘要的目标。",
    )
    updated = curator.refresh_project_summary(project_id=project["id"])
    assert "new goal" in updated["project_core"]["content"]

    db.close()


def test_optional_integrations_fail_gracefully(monkeypatch):
    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name.startswith(("graphify", "mempalace")):
            raise ImportError(f"blocked optional dependency: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    assert palace.route_to_hall("所有操作必须遵守规则") == "rules"
    with pytest.raises(RuntimeError, match="memery-mcp\\[graph\\]"):
        _load_graphify()
    with pytest.raises(RuntimeError, match="memery-mcp\\[palace\\]"):
        palace.get_kg()
