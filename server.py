# -*- coding: utf-8 -*-
r"""MCP Memory Server v2 — Enhanced with Graphify + MemPalace.

Inspired by:
  - graphify: `safishamsi/graphify` — code knowledge graph pipeline
  - mempalace: `MemPalace/mempalace` — spatial memory system

Tools (29+):
  ── Palace Management ──
    create_project, list_projects
    create_wing, list_wings, create_room, list_rooms
    list_halls

  ── Memory Write/Read ──
    write_memory, search_memory, recall_for_task
    get_context_bundle, delete_memory, deprecate_memory
    list_memories, get_memory

  ── Memory Curator Pipeline ──
    ingest_conversation, extract_memory_candidates
    review_memory_candidate, list_pending_candidates
    compact_project_memory, prune_low_value_memories

  ── Graph Pipeline (graphify) ──
    analyze_project_code, query_graph_path, get_graph_neighbors
    get_graph_stats

  ── Knowledge Graph (mempalace) ──
    add_knowledge_triple, query_entity, query_entity_timeline
    invalidate_triple, get_timeline

  ── Task & Decisions ──
    record_decision, list_decisions
    record_task_snapshot, list_task_snapshots
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .config import get_config, Confidence
from .db import MemoryDB, _uid, _now
from .curator import MemoryCurator, PROTECTED_TYPES, TOP_LEVEL_MEMORY_TYPES
from .stdio import run_fastmcp_stdio

logging.getLogger("numexpr").setLevel(logging.WARNING)
logging.getLogger("numexpr.utils").setLevel(logging.WARNING)

# Lazy imports for heavy deps
_backends = None


def _get_backends():
    global _backends
    if _backends is None:
        from .backends.lancedb_backend import LanceDBStore
        _backends = LanceDBStore
    return _backends


# ── Initialize ──────────────────────────────────────────────────────────

mcp = FastMCP("Memory Curator Server v2 (Graphify+MemPalace)")

cfg = get_config()
db = MemoryDB()
vector_store = _get_backends()()
curator = MemoryCurator(db, vector_store)


def _ensure_project(project_name: str) -> dict | None:
    project = db.get_project_by_name(project_name)
    if not project:
        return {"error": f"Project '{project_name}' not found."}
    return project


def _parse_string_list(value, field_name: str) -> tuple[list[str], str | None]:
    """Accept JSON arrays, comma-separated strings, or a single plain value."""
    if value is None or value == "":
        return [], None
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()], None
    if not isinstance(value, str):
        return [str(value)], f"{field_name} was coerced to a string list."
    raw = value.strip()
    if not raw:
        return [], None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        if "," in raw:
            return [part.strip() for part in raw.split(",") if part.strip()], None
        return [raw], f"{field_name} was not valid JSON; treated as one value."
    if isinstance(parsed, list):
        return [str(item) for item in parsed if str(item).strip()], None
    if isinstance(parsed, str):
        return [parsed], f"{field_name} JSON value was a string; treated as one value."
    return [str(parsed)], f"{field_name} JSON value was not an array; coerced."


def _clean_text(value, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _normalize_memory_type(memory_type) -> str:
    return _clean_text(memory_type, "fact")


def _normalize_content(content) -> str:
    return _clean_text(content)


def _warning(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def _safe_refresh_summary(project_id: str) -> tuple[dict | None, str | None]:
    try:
        return curator.refresh_project_summary(project_id=project_id), None
    except Exception as exc:
        return None, _warning(exc)


# ═══════════════════════════════════════════════════════════════════════════
# Palace Management
# ═══════════════════════════════════════════════════════════════════════════

@mcp.tool()
def create_project(name: str, slug: str | None = None,
                   description: str = "", context_type: str = "auto") -> dict:
    """Create an isolated context for software or any other ongoing matter."""
    allowed = {"auto", "software", "research", "business", "learning", "general"}
    if context_type not in allowed:
        return {"error": f"context_type must be one of {sorted(allowed)}"}
    existing = db.get_project_by_name(name)
    if existing:
        return {"warning": f"Project '{name}' already exists.", "project": existing}
    project = db.create_project(name, slug, description, context_type)
    summary = curator.refresh_project_summary(project_id=project["id"])
    return {"status": "created", "project": project,
            "project_summary": summary.get("summary")}


@mcp.tool()
def create_context(name: str, description: str = "",
                   context_type: str = "auto") -> dict:
    """Create a general context: software, research, business, learning, or other."""
    allowed = {"auto", "software", "research", "business", "learning", "general"}
    if context_type not in allowed:
        return {"error": f"context_type must be one of {sorted(allowed)}"}
    existing = db.get_project_by_name(name)
    if existing:
        return {"warning": f"Context '{name}' already exists.", "context": existing}
    context = db.create_project(name, description=description, context_type=context_type)
    summary = curator.refresh_project_summary(project_id=context["id"])
    return {"status": "created", "context": context,
            "top_level_memory": summary}


@mcp.tool()
def list_projects() -> dict:
    """List all projects."""
    projects = db.list_projects()
    return {"count": len(projects), "projects": projects}


@mcp.tool()
def create_wing(project_name: str, name: str, slug: str | None = None,
                description: str = "") -> dict:
    """Create a wing (person/project/topic) in the memory palace."""
    project = _ensure_project(project_name)
    if "error" in project:
        return project
    wing = db.create_wing(project["id"], name, slug, description)
    return {"status": "created", "wing": wing}


@mcp.tool()
def list_wings(project_name: str | None = None) -> dict:
    """List wings in the palace."""
    pid = None
    if project_name:
        project = db.get_project_by_name(project_name)
        if project:
            pid = project["id"]
    wings = db.list_wings(pid)
    return {"count": len(wings), "wings": wings}


@mcp.tool()
def create_room(wing_name: str, project_name: str,
                name: str, slug: str | None = None,
                description: str = "") -> dict:
    """Create a room (topic) within a wing."""
    project = _ensure_project(project_name)
    if "error" in project:
        return project
    wing = db.get_wing_by_slug(project["id"], wing_name.lower().replace(" ", "_"))
    if not wing:
        return {"error": f"Wing '{wing_name}' not found."}
    room = db.create_room(wing["id"], name, slug, description)
    return {"status": "created", "room": room}


@mcp.tool()
def list_rooms(wing_name: str, project_name: str) -> dict:
    """List rooms in a wing."""
    project = _ensure_project(project_name)
    if "error" in project:
        return project
    wing = db.get_wing_by_slug(project["id"], wing_name.lower().replace(" ", "_"))
    if not wing:
        return {"error": f"Wing '{wing_name}' not found."}
    rooms = db.list_rooms(wing["id"])
    return {"count": len(rooms), "rooms": rooms}


@mcp.tool()
def list_halls() -> dict:
    """List all hall types (memory classifications)."""
    halls = db.list_halls()
    return {"count": len(halls), "halls": halls}


# ═══════════════════════════════════════════════════════════════════════════
# Memory Write/Read
# ═══════════════════════════════════════════════════════════════════════════

@mcp.tool()
def write_memory(
    project_name: str, memory_type: str, title: str, content: str,
    wing_name: str | None = None, room_name: str | None = None,
    hall_id: str = "general",
    tags: str | None = None, source_files: str | None = None,
    importance: float = 0.5, confidence: float = 0.5,
    novelty: float = 0.5, reusability: float = 0.5,
    actionability: float = 0.5,
    refresh_summary: bool = True,
) -> dict:
    """Write a memory directly."""
    memory_type = _normalize_memory_type(memory_type)
    if memory_type in TOP_LEVEL_MEMORY_TYPES:
        return {
            "error": (
                f"'{memory_type}' is managed as a pinned singleton. Use "
                "refresh_project_summary or update_latest_conversation_summary."
            )
        }
    project = _ensure_project(project_name)
    if "error" in project:
        return project
    project_id = project["id"]

    wing_id = None
    room_id = None
    if wing_name:
        wing = db.get_wing_by_slug(project_id, wing_name.lower().replace(" ", "_"))
        if wing:
            wing_id = wing["id"]
            if room_name:
                room = db.get_room_by_slug(wing_id, room_name.lower().replace(" ", "_"))
                if room:
                    room_id = room["id"]

    content = _normalize_content(content)
    if not content:
        return {"error": "content cannot be empty."}

    warnings = []
    tags_list, tags_warning = _parse_string_list(tags, "tags")
    files_list, files_warning = _parse_string_list(source_files, "source_files")
    warnings.extend(w for w in (tags_warning, files_warning) if w)
    if not db.get_hall(hall_id):
        warnings.append(f"Unknown hall_id '{hall_id}'; using 'general'.")
        hall_id = "general"

    try:
        memory = db.write_memory(
            project_id=project_id, memory_type=memory_type,
            title=title, content=content,
            wing_id=wing_id, room_id=room_id, hall_id=hall_id,
            confidence_label=Confidence.INFERRED, confidence_score=confidence,
            tags=tags_list, source_files=files_list,
            importance=importance, confidence=confidence,
            novelty=novelty, reusability=reusability, actionability=actionability,
        )
    except ValueError as exc:
        return {"error": str(exc)}
    try:
        vector_store.insert(memory["id"], project_id, content)
    except Exception as exc:
        warnings.append(f"Vector index write failed; SQLite memory was saved. {_warning(exc)}")
    if not refresh_summary:
        result = {"status": "written", "memory": memory, "summary_refresh": "deferred"}
        if warnings:
            result["warnings"] = warnings
        return result
    summary, summary_warning = _safe_refresh_summary(project_id)
    if summary_warning:
        warnings.append(f"Project summary refresh failed; memory was saved. {summary_warning}")
    result = {"status": "written", "memory": memory,
              "project_summary": summary.get("summary") if summary else None}
    if warnings:
        result["warnings"] = warnings
    return result


@mcp.tool()
def write_memories_batch(project_name: str, memories: str) -> dict:
    """Write a JSON array of memories in one transaction and refresh once."""
    project = _ensure_project(project_name)
    if "error" in project:
        return project
    warnings = []
    if isinstance(memories, list):
        items = memories
    else:
        try:
            items = json.loads(memories)
        except (json.JSONDecodeError, TypeError) as exc:
            return {"error": f"Invalid memories JSON: {exc}"}
    if not isinstance(items, list) or not items:
        return {"error": "memories must be a non-empty JSON array."}
    if len(items) > 5000:
        return {"error": "A single batch cannot exceed 5000 memories."}

    rows = []
    vector_items = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            return {"error": f"Memory at index {index} must be an object."}
        memory_type = _normalize_memory_type(item.get("memory_type", "fact"))
        if memory_type in TOP_LEVEL_MEMORY_TYPES:
            return {"error": f"Memory at index {index} uses protected type '{memory_type}'."}
        content = _normalize_content(item.get("content"))
        if not content:
            return {"error": f"Memory at index {index} has empty content."}
        hall_id = str(item.get("hall_id", "general") or "general")
        if not db.get_hall(hall_id):
            warnings.append(
                f"Memory at index {index} uses unknown hall_id '{hall_id}'; using 'general'."
            )
            hall_id = "general"
        tags_list, tags_warning = _parse_string_list(
            item.get("tags"), f"memories[{index}].tags"
        )
        files_list, files_warning = _parse_string_list(
            item.get("source_files"), f"memories[{index}].source_files"
        )
        warnings.extend(w for w in (tags_warning, files_warning) if w)
        row = {
            **item,
            "project_id": project["id"],
            "memory_type": memory_type,
            "title": _clean_text(item.get("title")) or curator._extract_title(content),
            "content": content,
            "hall_id": hall_id,
            "tags": tags_list,
            "source_files": files_list,
        }
        rows.append(row)

    try:
        written = db.write_memories_batch(rows)
    except ValueError as exc:
        return {"error": str(exc)}
    for memory in written:
        vector_items.append({
            "id": memory["id"],
            "project_id": project["id"],
            "text": memory["content"],
        })
    try:
        vector_store.insert_batch(vector_items)
    except Exception as exc:
        warnings.append(f"Vector index batch write failed; SQLite memories were saved. {_warning(exc)}")
    summary, summary_warning = _safe_refresh_summary(project["id"])
    if summary_warning:
        warnings.append(f"Project summary refresh failed; memories were saved. {summary_warning}")
    result = {
        "status": "written",
        "count": len(written),
        "memories": written,
        "project_summary": summary.get("summary") if summary else None,
    }
    if warnings:
        result["warnings"] = warnings
    return result


@mcp.tool()
def search_memory(
    project_name: str | None = None, wing_name: str | None = None,
    room_name: str | None = None, hall_id: str | None = None,
    memory_type: str | None = None, keyword: str | None = None,
    semantic_query: str | None = None, tags: str | None = None,
    confidence_label: str | None = None, min_score: float | None = None,
    limit: int = 20,
) -> dict:
    """Search memories by various filters."""
    project_id = None
    wing_id = None
    room_id = None

    if project_name:
        project = db.get_project_by_name(project_name)
        if project:
            project_id = project["id"]
            if wing_name:
                wing = db.get_wing_by_slug(project_id, wing_name.lower().replace(" ", "_"))
                if wing:
                    wing_id = wing["id"]
                    if room_name:
                        room = db.get_room_by_slug(wing_id, room_name.lower().replace(" ", "_"))
                        if room:
                            room_id = room["id"]

    if semantic_query:
        vr = vector_store.search(semantic_query, project_id=project_id, limit=limit)
        ids = [r.id for r in vr]
        results = db.search_memories_by_ids(ids) if ids else []
        for mem in results:
            for v in vr:
                if v.id == mem["id"]:
                    mem["semantic_similarity"] = v.similarity
                    break
        for mem in results:
            db.record_memory_hit(mem["id"])
    else:
        tags_list, _ = _parse_string_list(tags, "tags")
        results = db.search_memories(
            project_id=project_id, wing_id=wing_id, room_id=room_id,
            hall_id=hall_id, memory_type=memory_type, keyword=keyword,
            tags=tags_list or None, confidence_label=confidence_label,
            min_score=min_score, limit=limit,
        )
        for mem in results:
            db.record_memory_hit(mem["id"])

    return {"count": len(results), "results": results}


@mcp.tool()
def recall_for_task(project_name: str, task: str, limit: int = 10) -> dict:
    """Recall relevant memories for a task."""
    project = _ensure_project(project_name)
    if "error" in project:
        return project
    project_id = project["id"]

    sr = vector_store.search(task, project_id=project_id, limit=limit)
    kr = db.search_memories(project_id=project_id, keyword=task, limit=limit)

    seen = set()
    combined = []
    for r in sr:
        mem = db.get_memory(r.id)
        if mem and r.id not in seen:
            seen.add(r.id)
            mem["semantic_similarity"] = r.similarity
            combined.append(mem)
    for r in kr:
        if r["id"] not in seen:
            seen.add(r["id"])
            combined.append(r)
    for m in combined:
        db.record_memory_hit(m["id"])

    return {
        "task": task, "project": project_name,
        "relevant_memories": combined[:limit],
    }


@mcp.tool()
def wake_up(project_name: str) -> dict:
    """Activate memory context for this session. Call once at session start.

    Returns the durable project summary plus compact L0+L1 context. No API
    keys needed. Use search_memory / recall_for_task for deeper L2/L3 queries.

    L0: project identity, wing structure
    L1: top decisions, key rules, recent decisions
    """
    project = _ensure_project(project_name)
    if "error" in project:
        return project
    project_id = project["id"]

    # L0: Palace layout
    wings = db.list_wings(project_id)
    wing_names = [w["name"] for w in wings[:5]]

    refreshed = curator.get_top_level_context(project_id=project_id)
    summary = refreshed.get("summary")
    project_core = refreshed.get("project_core")
    latest_conversation = refreshed.get("latest_conversation_summary")

    # L1: Key memories, with pinned project context sorted first.
    top_memories = db.search_memories(project_id=project_id, min_score=0.4, limit=10)
    rules = db.search_memories(project_id=project_id, memory_type="coding_rule", limit=3)
    decisions = db.list_decisions(project_id, limit=3)
    pending = db.list_pending_candidates(project_id)

    # Compact context for AI
    context = {
        "project": project_name,
        "top_level_memory": {
            "project_core": project_core.get("content") if project_core else None,
            "latest_conversation_summary": (
                latest_conversation.get("content") if latest_conversation else None
            ),
        },
        "project_summary": summary.get("content") if summary else None,
        "wings": wing_names,
        "key_rules": [{"title": r["title"], "content": r["content"][:120]} for r in rules],
        "recent_decisions": [{"title": d["title"], "rationale": d.get("rationale", "")[:80]} for d in decisions],
        "top_memories": [m["title"] for m in top_memories[:5]],
        "pending_review": len(pending),
        "hint": "Use search_memory() for deep recall, recall_for_task() for task context, analyze_project_code() for code insights.",
    }

    stats = db.get_stats(project_id)
    return {"context": context, "stats": stats}


@mcp.tool()
def get_context_bundle(project_name: str) -> dict:
    """Get full context bundle: active memories, tasks, decisions."""
    project = _ensure_project(project_name)
    if "error" in project:
        return project
    project_id = project["id"]

    refreshed = curator.get_top_level_context(project_id=project_id)
    summary = refreshed.get("summary")
    project_core = refreshed.get("project_core")
    latest_conversation = refreshed.get("latest_conversation_summary")
    memories = db.list_memories(project_id=project_id, limit=50)
    tasks = db.list_task_snapshots(project_id, limit=10)
    decisions = db.list_decisions(project_id, limit=10)
    pending = db.list_pending_candidates(project_id)

    by_type: dict[str, list] = {}
    for m in memories:
        t = m.get("memory_type", "other")
        by_type.setdefault(t, []).append(m["title"])

    return {
        "project": project_name,
        "top_level_memory": {
            "project_core": project_core.get("content") if project_core else None,
            "latest_conversation_summary": (
                latest_conversation.get("content") if latest_conversation else None
            ),
        },
        "total_memories": len(memories),
        "memories_by_type": {k: len(v) for k, v in by_type.items()},
        "project_summary": summary["content"] if summary else None,
        "active_tasks": [{"name": t["task_name"], "status": t["status"]} for t in tasks],
        "recent_decisions": [{"title": d["title"], "created": d["created_at"]} for d in decisions],
        "pending_candidates": len(pending),
    }


@mcp.tool()
def get_top_level_memory(project_name: str) -> dict:
    """Return pinned project core and latest-conversation memory only."""
    project = _ensure_project(project_name)
    if "error" in project:
        return project
    refreshed = curator.get_top_level_context(project_id=project["id"])
    return {
        "project": project_name,
        "project_core": refreshed.get("project_core"),
        "latest_conversation_summary": refreshed.get("latest_conversation_summary"),
        "project_summary": refreshed.get("summary"),
    }


@mcp.tool()
def get_memory(memory_id: str) -> dict:
    """Get a specific memory by ID with its graph neighbors."""
    mem = db.get_memory(memory_id)
    if not mem:
        return {"error": f"Memory '{memory_id}' not found."}
    neighbors = db.get_neighbors(memory_id)
    return {"memory": mem, "neighbors": neighbors}


@mcp.tool()
def delete_memory(memory_id: str) -> dict:
    """Delete a memory."""
    mem = db.get_memory(memory_id)
    if not mem:
        return {"error": f"Memory '{memory_id}' not found."}
    if mem["memory_type"] in PROTECTED_TYPES and mem.get("importance", 0) >= 0.8:
        return {"error": f"Cannot delete protected memory.", "memory": mem}
    db.delete_memory(memory_id)
    vector_store.delete(memory_id)
    summary = curator.refresh_project_summary(project_id=mem["project_id"])
    return {"status": "deleted", "memory_id": memory_id,
            "project_summary": summary.get("summary")}


@mcp.tool()
def deprecate_memory(memory_id: str) -> dict:
    """Mark a memory as deprecated."""
    mem = db.get_memory(memory_id)
    if not mem:
        return {"status": "not_found", "memory_id": memory_id}
    if mem["memory_type"] in PROTECTED_TYPES:
        return {"error": "Cannot deprecate protected memory.", "memory": mem}
    ok = db.deprecate_memory(memory_id)
    summary = curator.refresh_project_summary(project_id=mem["project_id"])
    return {"status": "deprecated" if ok else "not_found", "memory_id": memory_id,
            "project_summary": summary.get("summary")}


@mcp.tool()
def list_memories(project_name: str | None = None,
                  wing_name: str | None = None,
                  status: str = "active", limit: int = 200) -> dict:
    """List memories."""
    project_id = None
    wing_id = None
    if project_name:
        project = db.get_project_by_name(project_name)
        if project:
            project_id = project["id"]
            if wing_name:
                wing = db.get_wing_by_slug(project_id, wing_name.lower().replace(" ", "_"))
                if wing:
                    wing_id = wing["id"]
    memories = db.list_memories(project_id=project_id, wing_id=wing_id,
                                status=status, limit=limit)
    return {"count": len(memories), "memories": memories}


# ═══════════════════════════════════════════════════════════════════════════
# Memory Curator Pipeline
# ═══════════════════════════════════════════════════════════════════════════

@mcp.tool()
def ingest_conversation(project_name: str, conversation_text: str,
                        source_type: str = "conversation") -> dict:
    """Ingest raw conversation and extract candidate memories."""
    return curator.ingest_conversation(project_name, conversation_text, source_type)


@mcp.tool()
def ingest_update(context_name: str, update_text: str,
                  source_type: str = "update") -> dict:
    """Ingest any ongoing matter update and refresh its pinned context."""
    return curator.ingest_conversation(context_name, update_text, source_type)


@mcp.tool()
def extract_memory_candidates(project_name: str,
                              conversation_text: str) -> dict:
    """Extract structured candidate memories from conversation."""
    project = _ensure_project(project_name)
    if "error" in project:
        return project
    import re
    segments = re.split(r'(?<=[。！？\.\!\?\n])\s*', conversation_text)
    segments = [s.strip() for s in segments if len(s.strip()) > 10]
    candidates_output = []
    for seg in segments[:20]:
        candidates_output.append({
            "project_name": project_name,
            "raw_text": seg,
            "should_store": None,
            "memory_type": None,
        })
    return {"candidates": candidates_output}


@mcp.tool()
def review_memory_candidate(candidate_id: str, decision: str,
                            merged_to: str | None = None,
                            reason: str | None = None) -> dict:
    """Review a candidate: accept / reject / merge / needs_review."""
    return curator.review_candidate(candidate_id, decision, merged_to, reason)


@mcp.tool()
def list_pending_candidates(project_name: str | None = None,
                            wing_name: str | None = None) -> dict:
    """List pending memory candidates."""
    pid = None
    wid = None
    if project_name:
        project = db.get_project_by_name(project_name)
        if project:
            pid = project["id"]
            if wing_name:
                wing = db.get_wing_by_slug(pid, wing_name.lower().replace(" ", "_"))
                if wing:
                    wid = wing["id"]
    candidates = db.list_pending_candidates(pid, wid)
    return {"count": len(candidates), "candidates": candidates}


@mcp.tool()
def compact_project_memory(project_name: str) -> dict:
    """Compact project memories by merging/deprecating low-value ones."""
    return curator.compact_project_memory(project_name)


@mcp.tool()
def refresh_project_summary(
    project_name: str,
    latest_conversation_text: str | None = None,
    source_type: str = "conversation",
) -> dict:
    """Refresh pinned project context, optionally replacing the latest conversation."""
    return curator.refresh_project_summary(
        project_name=project_name,
        latest_conversation_text=latest_conversation_text,
        source_type=source_type,
    )


@mcp.tool()
def update_latest_conversation_summary(
    project_name: str,
    conversation_text: str,
    source_type: str = "conversation",
) -> dict:
    """Replace the pinned latest-conversation summary and refresh project context."""
    latest = curator.update_latest_conversation_summary(
        project_name=project_name,
        conversation_text=conversation_text,
        source_type=source_type,
    )
    if "error" in latest:
        return latest
    refreshed = curator.refresh_project_summary(project_name=project_name)
    return {
        "status": "updated",
        "latest_conversation_summary": latest.get("summary"),
        "project_core": refreshed.get("project_core"),
        "project_summary": refreshed.get("summary"),
    }


@mcp.tool()
def prune_low_value_memories(project_name: str) -> dict:
    """Prune low-value memories from a project."""
    return curator.prune_low_value_memories(project_name)


# ═══════════════════════════════════════════════════════════════════════════
# Graph Pipeline (graphify)
# ═══════════════════════════════════════════════════════════════════════════

@mcp.tool()
def analyze_project_code(project_name: str, paths: str) -> dict:
    """Run graphify pipeline: extract code structure, cluster, find god nodes.

    Args:
        project_name: Target project.
        paths: JSON array of file/directory paths to analyze.
    """
    path_list = json.loads(paths) if paths else []
    if not path_list:
        return {"error": "No paths provided."}
    return curator.analyze_project_code(project_name, path_list)


@mcp.tool()
def query_graph_path(memory_id_a: str, memory_id_b: str,
                     max_depth: int = 4) -> dict:
    """Find the path between two memory nodes in the graph."""
    path = db.find_path(memory_id_a, memory_id_b, max_depth)
    if path is None:
        return {"found": False, "path": []}
    return {"found": True, "length": len(path), "path": path}


@mcp.tool()
def get_graph_neighbors(memory_id: str, direction: str = "both") -> dict:
    """Get graph neighbors of a memory node."""
    neighbors = db.get_neighbors(memory_id, direction)
    return {"memory_id": memory_id, "neighbor_count": len(neighbors),
            "neighbors": neighbors}


@mcp.tool()
def get_graph_stats(project_name: str | None = None) -> dict:
    """Get graph and memory statistics."""
    pid = None
    if project_name:
        project = db.get_project_by_name(project_name)
        if project:
            pid = project["id"]
    stats = db.get_stats(pid)
    return {"stats": stats}


# ═══════════════════════════════════════════════════════════════════════════
# Knowledge Graph (mempalace)
# ═══════════════════════════════════════════════════════════════════════════

@mcp.tool()
def add_knowledge_triple(
    subject: str, predicate: str, obj: str,
    valid_from: str | None = None, valid_to: str | None = None,
) -> dict:
    """Add a temporal knowledge triple (entity-relationship with time)."""
    return curator.add_triple(subject, predicate, obj, valid_from, valid_to)


@mcp.tool()
def query_entity(entity: str, as_of: str | None = None) -> dict:
    """Query all knowledge graph triples about an entity."""
    return curator.query_entity(entity, as_of=as_of)


@mcp.tool()
def query_entity_timeline(entity: str) -> dict:
    """Get the timeline of changes for an entity."""
    results = db.get_timeline(entity)
    return {"entity": entity, "count": len(results), "timeline": results}


@mcp.tool()
def invalidate_triple(subject: str, predicate: str, obj: str,
                      ended: str | None = None) -> dict:
    """Invalidate a temporal triple (mark as no longer true)."""
    try:
        ok = curator.palace.invalidate_knowledge(subject, predicate, obj, ended)
    except RuntimeError as exc:
        return {"error": str(exc)}
    return {"invalidated": ok, "subject": subject, "predicate": predicate, "object": obj}


# ═══════════════════════════════════════════════════════════════════════════
# Task & Decisions
# ═══════════════════════════════════════════════════════════════════════════

@mcp.tool()
def record_decision(project_name: str, title: str, content: str,
                    rationale: str = "", alternatives: str | None = None) -> dict:
    """Record an architectural design decision."""
    project = _ensure_project(project_name)
    if "error" in project:
        return project
    alt_list, _ = _parse_string_list(alternatives, "alternatives")
    decision = db.record_decision(project["id"], title, content, rationale, alt_list)
    summary = curator.refresh_project_summary(project_id=project["id"])
    return {"status": "recorded", "decision": decision,
            "project_summary": summary.get("summary")}


@mcp.tool()
def list_decisions(project_name: str, limit: int = 50) -> dict:
    """List decisions for a project."""
    project = _ensure_project(project_name)
    if "error" in project:
        return project
    decisions = db.list_decisions(project["id"], limit)
    return {"count": len(decisions), "decisions": decisions}


@mcp.tool()
def record_task_snapshot(project_name: str, task_name: str,
                         status: str = "pending", completed: str | None = None,
                         remaining: str | None = None, notes: str = "") -> dict:
    """Record a task snapshot."""
    project = _ensure_project(project_name)
    if "error" in project:
        return project
    completed_list, _ = _parse_string_list(completed, "completed")
    remaining_list, _ = _parse_string_list(remaining, "remaining")
    snapshot = db.record_task_snapshot(
        project["id"], task_name, status, completed_list, remaining_list, notes)
    summary = curator.refresh_project_summary(project_id=project["id"])
    return {"status": "recorded", "snapshot": snapshot,
            "project_summary": summary.get("summary")}


@mcp.tool()
def list_task_snapshots(project_name: str, limit: int = 50) -> dict:
    """List task snapshots for a project."""
    project = _ensure_project(project_name)
    if "error" in project:
        return project
    tasks = db.list_task_snapshots(project["id"], limit)
    return {"count": len(tasks), "tasks": tasks}


# ── Main entry point ───────────────────────────────────────────────────

def main():
    run_fastmcp_stdio(mcp)
