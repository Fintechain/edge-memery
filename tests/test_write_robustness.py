from __future__ import annotations

import sqlite3

from memory_server.db import MemoryDB


class BrokenVectorStore:
    def insert(self, *args, **kwargs) -> None:
        raise RuntimeError("vector unavailable")

    def insert_batch(self, *args, **kwargs) -> None:
        raise RuntimeError("vector unavailable")

    def update(self, *args, **kwargs) -> None:
        raise RuntimeError("vector unavailable")

    def delete(self, *args, **kwargs) -> None:
        raise RuntimeError("vector unavailable")

    def search(self, *args, **kwargs) -> list:
        return []


def test_old_database_schema_is_migrated_before_write(tmp_path):
    db_path = tmp_path / "old-memory.db"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            slug TEXT NOT NULL UNIQUE
        );
        CREATE TABLE memories (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            memory_type TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL
        );
        INSERT INTO projects (id, name, slug)
        VALUES ('project-1', 'legacy-project', 'legacy-project');
        """
    )
    connection.commit()
    connection.close()

    db = MemoryDB(str(db_path))
    project = db.get_project_by_name("legacy-project")
    memory = db.write_memory(
        project_id=project["id"],
        memory_type="fact",
        title="Migrated write",
        content="Old database schemas should accept current writes.",
    )

    assert memory["content"] == "Old database schemas should accept current writes."
    assert db.get_stats(project["id"])["total_memories"] == 1
    assert db.list_wings(project["id"])
    db.close()


def test_server_write_memory_survives_bad_tags_and_vector_failure(tmp_path, monkeypatch):
    import memory_server.server as server

    db = MemoryDB(str(tmp_path / "memory.db"))
    project = db.create_project("write-robustness")
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "vector_store", BrokenVectorStore())
    monkeypatch.setattr(server.curator, "db", db)
    monkeypatch.setattr(server.curator, "vs", BrokenVectorStore())

    result = server.write_memory(
        project_name="write-robustness",
        memory_type="fact",
        title="Direct write",
        content="This should be saved even when vector indexing fails.",
        tags="not-json",
        refresh_summary=False,
    )

    assert result["status"] == "written"
    assert result["memory"]["content"] == "This should be saved even when vector indexing fails."
    assert result["warnings"]
    assert db.get_stats(project["id"])["total_memories"] == 1
    db.close()


def test_server_write_memory_falls_back_from_unknown_hall(tmp_path, monkeypatch):
    import memory_server.server as server

    db = MemoryDB(str(tmp_path / "memory.db"))
    project = db.create_project("unknown-hall")
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server.curator, "db", db)

    result = server.write_memory(
        project_name="unknown-hall",
        memory_type="fact",
        title="Unknown hall",
        content="Unknown halls should not break writes.",
        hall_id="missing-hall",
        refresh_summary=False,
    )

    assert result["status"] == "written"
    assert result["memory"]["hall_id"] == "general"
    assert "Unknown hall_id" in result["warnings"][0]
    assert db.get_stats(project["id"])["total_memories"] == 1
    db.close()


def test_server_batch_write_accepts_list_and_sanitizes_item_fields(tmp_path, monkeypatch):
    import memory_server.server as server

    db = MemoryDB(str(tmp_path / "memory.db"))
    project = db.create_project("batch-robustness")
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "vector_store", BrokenVectorStore())
    monkeypatch.setattr(server.curator, "db", db)
    monkeypatch.setattr(server.curator, "vs", BrokenVectorStore())

    result = server.write_memories_batch(
        "batch-robustness",
        [{
            "memory_type": "fact",
            "title": "Batch direct",
            "content": "Batch writes should accept decoded lists.",
            "hall_id": "missing-hall",
            "tags": "plain-tag",
        }],
    )

    assert result["status"] == "written"
    assert result["count"] == 1
    assert result["memories"][0]["hall_id"] == "general"
    assert result["warnings"]
    assert db.get_stats(project["id"])["total_memories"] == 3
    db.close()


def test_review_candidate_saves_memory_when_vector_index_fails(tmp_path):
    from memory_server.curator import MemoryCurator

    db = MemoryDB(str(tmp_path / "memory.db"))
    project = db.create_project("candidate-vector-fail")
    candidate = db.create_candidate(
        project_id=project["id"],
        source_type="conversation",
        raw_text="The durable write path must not depend on vector indexing.",
        extracted_title="Durable writes",
        extracted_content="The durable write path must not depend on vector indexing.",
        candidate_type="principle",
        importance=0.9,
        confidence=0.9,
        novelty=0.8,
        reusability=0.8,
        actionability=0.8,
    )
    curator = MemoryCurator(db, BrokenVectorStore())

    result = curator.review_candidate(candidate["id"], "accepted")

    assert result["decision"] == "accepted"
    assert "Vector index write failed" in result["warning"]
    assert db.search_memories(project_id=project["id"], memory_type="principle")
    db.close()
