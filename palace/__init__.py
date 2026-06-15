# -*- coding: utf-8 -*-
"""Palace — wraps MemPalace's storage and routing for spatial memory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import get_config


def get_palace_path() -> str:
    cfg = get_config()
    palace_dir = Path(cfg.data_dir) / "palace"
    palace_dir.mkdir(parents=True, exist_ok=True)
    return str(palace_dir)


def ensure_wing(db, project_name: str) -> dict:
    project = db.get_project_by_name(project_name)
    if not project:
        return {}
    wings = db.list_wings(project["id"])
    if not wings:
        return {}
    for w in wings:
        if w["slug"] == project["slug"]:
            return w
    return wings[0]


def ensure_room(db, wing_id: str, room_name: str) -> dict:
    slug = room_name.lower().replace(" ", "_").replace("-", "_").strip("_")
    room = db.get_room_by_slug(wing_id, slug)
    return room or db.create_room(wing_id, room_name, slug)


def _missing_dependency_error() -> RuntimeError:
    return RuntimeError(
        "This operation requires the optional MemPalace integration. "
        "Install it with: pip install 'memery-mcp[palace]'"
    )


def get_kg():
    try:
        from mempalace.knowledge_graph import KnowledgeGraph
    except ImportError as exc:
        raise _missing_dependency_error() from exc
    palace_path = get_palace_path()
    return KnowledgeGraph(str(Path(palace_path) / "kg.sqlite3"))


def route_to_hall(content: str) -> str:
    try:
        from mempalace.miner import detect_hall
        return detect_hall(content)
    except ImportError:
        lowered = content.lower()
        for hall, keywords in get_config().hall_keywords.items():
            if any(keyword in lowered for keyword in keywords):
                return hall
        return "general"


def store_in_palace(memory_id: str, content: str, metadata: dict | None = None) -> None:
    try:
        from mempalace.palace import get_collection
        palace_path = get_palace_path()
        coll = get_collection(palace_path, collection_name="memories", create=True)
        coll.add(ids=[memory_id], documents=[content], metadatas=[metadata or {}])
    except Exception:
        pass


def search_palace(query: str, limit: int = 10, wing_id: str | None = None) -> list[dict]:
    try:
        from mempalace.palace import get_collection
        palace_path = get_palace_path()
        coll = get_collection(palace_path, collection_name="memories", create=False)
        where = {"wing_id": wing_id} if wing_id else None
        results = coll.query(query_texts=[query], n_results=limit, where=where)
        mems = []
        for i, doc in enumerate(results.get("documents", [[]])[0]):
            meta = (results.get("metadatas") or [[]])[0][i] if results.get("metadatas") else {}
            dist = (results.get("distances") or [[]])[0][i] if results.get("distances") else 0.0
            rid = (results.get("ids") or [[]])[0][i] if results.get("ids") else f"r_{i}"
            mems.append({"id": rid, "content": doc, "similarity": round(1.0 / (1.0 + float(dist)), 4), "metadata": meta})
        return mems
    except Exception:
        return []


def add_knowledge_triple(
    subject: str, predicate: str, obj: str,
    *, valid_from: str | None = None, valid_to: str | None = None,
    confidence: float = 1.0, drawer_ref: str | None = None,
) -> dict:
    kg = get_kg()
    kg.add_triple(subject, predicate, obj, valid_from=valid_from, valid_to=valid_to,
                  confidence=confidence, source_drawer_id=drawer_ref)
    return {"subject": subject, "predicate": predicate, "object": obj,
            "valid_from": valid_from, "valid_to": valid_to}


def query_knowledge(entity: str, as_of: str | None = None) -> dict:
    kg = get_kg()
    results = kg.query_entity(entity, as_of=as_of)
    return {"entity": entity, "as_of": as_of, "count": len(results), "triples": results}


def invalidate_knowledge(subject: str, predicate: str, obj: str,
                         ended: str | None = None) -> bool:
    kg = get_kg()
    kg.invalidate(subject, predicate, obj, ended=ended)
    return True
