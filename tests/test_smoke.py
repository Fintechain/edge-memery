"""Standalone smoke test that never touches the user's real memory data."""

from pathlib import Path
from tempfile import TemporaryDirectory

from memory_server.backends.lancedb_backend import LanceDBStore
from memory_server.db import MemoryDB
from memory_server.curator import MemoryCurator


def main():
    with TemporaryDirectory() as temporary_dir:
        root = Path(temporary_dir)
        db = MemoryDB(str(root / "memory.db"))
        vectors = LanceDBStore(str(root / "vectors"))
        curator = MemoryCurator(db, vectors)

        project = db.create_project(
            "smoke-test",
            description="Verify local memory ingestion and recall.",
            context_type="software",
        )
        assert project["id"]

        result = curator.ingest_conversation(
            "smoke-test",
            "The API is implemented in Python. Passwords must never be hard-coded.",
        )
        assert result["candidates_created"] >= 1

        context = curator.get_top_level_context(project_id=project["id"])
        assert context["project_core"]
        assert context["latest_conversation_summary"]
        assert context["summary"]

        memories = db.list_memories(project_id=project["id"], limit=20)
        assert memories

        db.close()
    print("\n=== ALL TESTS PASSED ===")


def test_lancedb_store_reopens_existing_table(tmp_path):
    vector_dir = tmp_path / "vectors"
    first = LanceDBStore(str(vector_dir))
    first.insert("memory-1", "project-1", "The server should restart cleanly.")

    second = LanceDBStore(str(vector_dir))

    assert second.count() == 1
    assert second.search("restart", project_id="project-1", limit=1)[0].id == "memory-1"


if __name__ == "__main__":
    main()
