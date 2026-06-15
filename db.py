# -*- coding: utf-8 -*-
r"""SQLite database layer — enhanced with:
  - Palace spatial memory: wings, rooms, drawers, closets, halls, tunnels
  - Temporal knowledge graph: entity-relationship with validity windows
  - Confidence labels: EXTRACTED / INFERRED / AMBIGUOUS
  - Graph-style edges between memories

Inspired by: Graphify (safishamsi/graphify) + MemPalace (MemPalace/mempalace)
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import get_config, Confidence


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _uid() -> str:
    return uuid.uuid4().hex[:16]


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _json_loads(s: str | None) -> Any:
    if not s:
        return []
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return []


class MemoryDB:
    """Enhanced SQLite database with Palace + Temporal Graph support."""

    def __init__(self, db_path: str | None = None) -> None:
        cfg = get_config()
        self.db_path = db_path or cfg.db_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._connections_lock = threading.Lock()
        self._closed = False
        self._init_tables()

    @property
    def conn(self) -> sqlite3.Connection:
        """Return one tuned SQLite connection per worker thread."""
        if self._closed:
            raise RuntimeError("MemoryDB is closed")
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = sqlite3.connect(
                self.db_path,
                timeout=30.0,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=30000")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA temp_store=MEMORY")
            connection.execute("PRAGMA cache_size=-32768")
            connection.execute("PRAGMA mmap_size=268435456")
            connection.execute("PRAGMA wal_autocheckpoint=1000")
            self._local.connection = connection
            with self._connections_lock:
                self._connections.append(connection)
        return connection

    # ═══════════════════════════════════════════════════════════════════════
    # Schema
    # ═══════════════════════════════════════════════════════════════════════

    def _init_tables(self) -> None:
        self.conn.executescript("""
        -- ── Projects ────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            slug TEXT NOT NULL UNIQUE,
            description TEXT DEFAULT '',
            context_type TEXT DEFAULT 'auto',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS project_revisions (
            project_id TEXT PRIMARY KEY,
            revision INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        -- ── Palace: Wings (person / project / topic) ─────────────────────
        CREATE TABLE IF NOT EXISTS wings (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            name TEXT NOT NULL,
            slug TEXT NOT NULL,
            description TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_wings_slug ON wings(project_id, slug);

        -- ── Palace: Rooms (specific topics within a wing) ────────────────
        CREATE TABLE IF NOT EXISTS rooms (
            id TEXT PRIMARY KEY,
            wing_id TEXT NOT NULL,
            name TEXT NOT NULL,
            slug TEXT NOT NULL,
            description TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (wing_id) REFERENCES wings(id) ON DELETE CASCADE
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_rooms_slug ON rooms(wing_id, slug);

        -- ── Palace: Halls (memory type / classification) ─────────────────
        CREATE TABLE IF NOT EXISTS halls (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            description TEXT DEFAULT ''
        );
        -- Seed default halls
        INSERT OR IGNORE INTO halls (id, name, description) VALUES
            ('facts', 'Facts', 'Verifiable facts and truths'),
            ('events', 'Events', 'Things that happened at specific times'),
            ('decisions', 'Decisions', 'Architectural and design decisions'),
            ('preferences', 'Preferences', 'User and team preferences'),
            ('architecture', 'Architecture', 'System design and structure'),
            ('bugs', 'Bugs', 'Bugs discovered and their fixes'),
            ('rules', 'Rules', 'Coding rules and conventions'),
            ('tasks', 'Tasks', 'Task tracking and snapshots'),
            ('general', 'General', 'Uncategorized memories');

        -- ── Memories (with confidence labels) ────────────────────────────
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            wing_id TEXT,
            room_id TEXT,
            hall_id TEXT DEFAULT 'general',
            memory_type TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            -- Confidence: EXTRACTED | INFERRED | AMBIGUOUS
            confidence_label TEXT DEFAULT 'INFERRED',
            confidence_score REAL DEFAULT 0.5,
            tags TEXT DEFAULT '[]',
            source_files TEXT DEFAULT '[]',
            importance REAL DEFAULT 0.0,
            confidence REAL DEFAULT 0.0,
            novelty REAL DEFAULT 0.0,
            reusability REAL DEFAULT 0.0,
            actionability REAL DEFAULT 0.0,
            score REAL DEFAULT 0.0,
            hit_count INTEGER DEFAULT 0,
            last_hit_at TEXT,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            deprecated_at TEXT,
            FOREIGN KEY (project_id) REFERENCES projects(id),
            FOREIGN KEY (wing_id) REFERENCES wings(id),
            FOREIGN KEY (room_id) REFERENCES rooms(id),
            FOREIGN KEY (hall_id) REFERENCES halls(id)
        );
        CREATE INDEX IF NOT EXISTS idx_mem_project ON memories(project_id);
        CREATE INDEX IF NOT EXISTS idx_mem_wing ON memories(wing_id);
        CREATE INDEX IF NOT EXISTS idx_mem_room ON memories(room_id);
        CREATE INDEX IF NOT EXISTS idx_mem_hall ON memories(hall_id);
        CREATE INDEX IF NOT EXISTS idx_mem_type ON memories(memory_type);
        CREATE INDEX IF NOT EXISTS idx_mem_status ON memories(status);
        CREATE INDEX IF NOT EXISTS idx_mem_score ON memories(score);
        CREATE INDEX IF NOT EXISTS idx_mem_confidence_label ON memories(confidence_label);
        CREATE INDEX IF NOT EXISTS idx_mem_project_status_type_score
            ON memories(project_id, status, memory_type, score DESC, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_mem_project_status_updated
            ON memories(project_id, status, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_mem_project_status_score_updated
            ON memories(project_id, status, score DESC, updated_at DESC);

        -- ── Memory edges (graph relationships) ───────────────────────────
        CREATE TABLE IF NOT EXISTS memory_edges (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            relation TEXT NOT NULL DEFAULT 'related_to',
            confidence_label TEXT DEFAULT 'INFERRED',
            confidence_score REAL DEFAULT 0.5,
            metadata TEXT DEFAULT '{}',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (source_id) REFERENCES memories(id) ON DELETE CASCADE,
            FOREIGN KEY (target_id) REFERENCES memories(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_edges_source ON memory_edges(source_id);
        CREATE INDEX IF NOT EXISTS idx_edges_target ON memory_edges(target_id);
        CREATE INDEX IF NOT EXISTS idx_edges_relation ON memory_edges(relation);

        -- ── Temporal Knowledge Graph (entity-relationship with time) ─────
        CREATE TABLE IF NOT EXISTS temporal_triples (
            id TEXT PRIMARY KEY,
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object TEXT NOT NULL,
            valid_from TEXT,
            valid_to TEXT,
            confidence_label TEXT DEFAULT 'INFERRED',
            confidence_score REAL DEFAULT 0.5,
            drawer_ref TEXT,
            metadata TEXT DEFAULT '{}',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            invalidated_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_triple_subject ON temporal_triples(subject);
        CREATE INDEX IF NOT EXISTS idx_triple_object ON temporal_triples(object);
        CREATE INDEX IF NOT EXISTS idx_triple_predicate ON temporal_triples(predicate);
        CREATE INDEX IF NOT EXISTS idx_triple_valid ON temporal_triples(valid_from, valid_to);

        -- ── Memory Candidates ────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS memory_candidates (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            wing_id TEXT,
            room_id TEXT,
            hall_id TEXT DEFAULT 'general',
            source_type TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            extracted_title TEXT,
            extracted_content TEXT,
            candidate_type TEXT,
            reason TEXT,
            confidence_label TEXT DEFAULT 'INFERRED',
            importance INTEGER DEFAULT 0,
            confidence REAL DEFAULT 0.0,
            novelty REAL DEFAULT 0.0,
            reusability REAL DEFAULT 0.0,
            actionability REAL DEFAULT 0.0,
            score REAL DEFAULT 0.0,
            status TEXT DEFAULT 'pending',
            duplicate_of TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TEXT,
            FOREIGN KEY (project_id) REFERENCES projects(id),
            FOREIGN KEY (duplicate_of) REFERENCES memories(id)
        );
        CREATE INDEX IF NOT EXISTS idx_candidates_status ON memory_candidates(status);
        CREATE INDEX IF NOT EXISTS idx_candidates_project_status_score
            ON memory_candidates(project_id, status, score DESC);

        -- ── Reviews, Compactions, Tasks, Decisions (unchanged core) ──────
        CREATE TABLE IF NOT EXISTS memory_reviews (
            id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            reviewer TEXT DEFAULT 'memory_curator',
            decision TEXT NOT NULL,
            merged_to TEXT,
            reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (candidate_id) REFERENCES memory_candidates(id),
            FOREIGN KEY (merged_to) REFERENCES memories(id)
        );

        CREATE TABLE IF NOT EXISTS memory_compactions (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            wing_id TEXT,
            scope TEXT DEFAULT 'all',
            memories_before INTEGER,
            memories_after INTEGER,
            removed_count INTEGER,
            merged_count INTEGER,
            deprecated_count INTEGER,
            summary TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );

        CREATE TABLE IF NOT EXISTS task_snapshots (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            task_name TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            completed TEXT DEFAULT '[]',
            remaining TEXT DEFAULT '[]',
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_project ON task_snapshots(project_id);
        CREATE INDEX IF NOT EXISTS idx_tasks_project_created
            ON task_snapshots(project_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS decisions (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            rationale TEXT DEFAULT '',
            alternatives TEXT DEFAULT '[]',
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );
        CREATE INDEX IF NOT EXISTS idx_decisions_project ON decisions(project_id);
        CREATE INDEX IF NOT EXISTS idx_decisions_project_created
            ON decisions(project_id, created_at DESC);

        CREATE TRIGGER IF NOT EXISTS trg_project_revision_create
        AFTER INSERT ON projects
        BEGIN
            INSERT OR IGNORE INTO project_revisions(project_id, revision)
            VALUES (NEW.id, 0);
        END;

        CREATE TRIGGER IF NOT EXISTS trg_project_revision_update
        AFTER UPDATE OF description, context_type ON projects
        BEGIN
            INSERT INTO project_revisions(project_id, revision, updated_at)
            VALUES (NEW.id, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(project_id) DO UPDATE SET
                revision = revision + 1, updated_at = CURRENT_TIMESTAMP;
        END;

        DROP TRIGGER IF EXISTS trg_memory_revision_insert;
        DROP TRIGGER IF EXISTS trg_memory_revision_update;
        DROP TRIGGER IF EXISTS trg_memory_revision_delete;

        CREATE TRIGGER IF NOT EXISTS trg_candidate_revision_insert
        AFTER INSERT ON memory_candidates
        BEGIN
            INSERT INTO project_revisions(project_id, revision, updated_at)
            VALUES (NEW.project_id, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(project_id) DO UPDATE SET
                revision = revision + 1, updated_at = CURRENT_TIMESTAMP;
        END;

        CREATE TRIGGER IF NOT EXISTS trg_candidate_revision_update
        AFTER UPDATE ON memory_candidates
        BEGIN
            INSERT INTO project_revisions(project_id, revision, updated_at)
            VALUES (NEW.project_id, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(project_id) DO UPDATE SET
                revision = revision + 1, updated_at = CURRENT_TIMESTAMP;
        END;

        CREATE TRIGGER IF NOT EXISTS trg_task_revision_insert
        AFTER INSERT ON task_snapshots
        BEGIN
            INSERT INTO project_revisions(project_id, revision, updated_at)
            VALUES (NEW.project_id, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(project_id) DO UPDATE SET
                revision = revision + 1, updated_at = CURRENT_TIMESTAMP;
        END;

        CREATE TRIGGER IF NOT EXISTS trg_decision_revision_insert
        AFTER INSERT ON decisions
        BEGIN
            INSERT INTO project_revisions(project_id, revision, updated_at)
            VALUES (NEW.project_id, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(project_id) DO UPDATE SET
                revision = revision + 1, updated_at = CURRENT_TIMESTAMP;
        END;
        """)
        project_columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(projects)").fetchall()
        }
        if "context_type" not in project_columns:
            self.conn.execute(
                "ALTER TABLE projects ADD COLUMN context_type TEXT DEFAULT 'auto'"
            )
        self.conn.execute(
            """INSERT OR IGNORE INTO project_revisions(project_id, revision)
               SELECT id, 0 FROM projects"""
        )
        self.conn.commit()

    # ═══════════════════════════════════════════════════════════════════════
    # Wings (Palace)
    # ═══════════════════════════════════════════════════════════════════════

    def create_wing(self, project_id: str, name: str, slug: str | None = None,
                    description: str = "") -> dict:
        slug = slug or name.lower().replace(" ", "_").replace("-", "_").strip("_")
        wid = _uid()
        now = _now()
        self.conn.execute(
            "INSERT INTO wings (id, project_id, name, slug, description, created_at) VALUES (?,?,?,?,?,?)",
            (wid, project_id, name, slug, description, now),
        )
        self.conn.commit()
        return self.get_wing(wid)  # type: ignore

    def get_wing(self, wid: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM wings WHERE id=?", (wid,)).fetchone()
        return dict(row) if row else None

    def get_wing_by_slug(self, project_id: str, slug: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM wings WHERE project_id=? AND slug=?", (project_id, slug)
        ).fetchone()
        return dict(row) if row else None

    def list_wings(self, project_id: str | None = None) -> list[dict]:
        if project_id:
            rows = self.conn.execute(
                "SELECT * FROM wings WHERE project_id=? ORDER BY name", (project_id,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM wings ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def delete_wing(self, wid: str) -> bool:
        cur = self.conn.execute("DELETE FROM wings WHERE id=?", (wid,))
        self.conn.commit()
        return cur.rowcount > 0

    # ═══════════════════════════════════════════════════════════════════════
    # Rooms (Palace)
    # ═══════════════════════════════════════════════════════════════════════

    def create_room(self, wing_id: str, name: str, slug: str | None = None,
                    description: str = "") -> dict:
        slug = slug or name.lower().replace(" ", "_").replace("-", "_").strip("_")
        rid = _uid()
        now = _now()
        self.conn.execute(
            "INSERT INTO rooms (id, wing_id, name, slug, description, created_at) VALUES (?,?,?,?,?,?)",
            (rid, wing_id, name, slug, description, now),
        )
        self.conn.commit()
        return self.get_room(rid)  # type: ignore

    def get_room(self, rid: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM rooms WHERE id=?", (rid,)).fetchone()
        return dict(row) if row else None

    def get_room_by_slug(self, wing_id: str, slug: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM rooms WHERE wing_id=? AND slug=?", (wing_id, slug)
        ).fetchone()
        return dict(row) if row else None

    def list_rooms(self, wing_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM rooms WHERE wing_id=? ORDER BY name", (wing_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ═══════════════════════════════════════════════════════════════════════
    # Halls
    # ═══════════════════════════════════════════════════════════════════════

    def list_halls(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM halls ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def get_hall(self, hid: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM halls WHERE id=?", (hid,)).fetchone()
        return dict(row) if row else None

    # ═══════════════════════════════════════════════════════════════════════
    # Projects
    # ═══════════════════════════════════════════════════════════════════════

    def create_project(self, name: str, slug: str | None = None,
                       description: str = "", context_type: str = "auto") -> dict:
        slug = slug or name.lower().replace(" ", "-").replace("_", "-")
        pid = _uid()
        now = _now()
        self.conn.execute(
            """INSERT INTO projects
               (id, name, slug, description, context_type, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?)""",
            (pid, name, slug, description, context_type, now, now),
        )
        self.conn.commit()
        # Auto-create a default wing for this project
        self.create_wing(pid, name, slug, description)
        return self.get_project(pid)  # type: ignore

    def get_project(self, pid: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        return dict(row) if row else None

    def get_project_by_name(self, name: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM projects WHERE name=?", (name,)).fetchone()
        return dict(row) if row else None

    def list_projects(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM projects ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def get_project_revision(self, project_id: str) -> int:
        row = self.conn.execute(
            "SELECT revision FROM project_revisions WHERE project_id=?",
            (project_id,),
        ).fetchone()
        return int(row["revision"]) if row else 0

    @staticmethod
    def _bump_project_revision(connection: sqlite3.Connection, project_id: str) -> None:
        connection.execute(
            """INSERT INTO project_revisions(project_id, revision, updated_at)
               VALUES (?, 1, CURRENT_TIMESTAMP)
               ON CONFLICT(project_id) DO UPDATE SET
                   revision = revision + 1, updated_at = CURRENT_TIMESTAMP""",
            (project_id,),
        )

    # ═══════════════════════════════════════════════════════════════════════
    # Memories (enhanced with palace + confidence labels)
    # ═══════════════════════════════════════════════════════════════════════

    def write_memory(
        self,
        project_id: str,
        memory_type: str,
        title: str,
        content: str,
        wing_id: str | None = None,
        room_id: str | None = None,
        hall_id: str = "general",
        confidence_label: str = Confidence.INFERRED,
        confidence_score: float = 0.5,
        tags: list[str] | None = None,
        source_files: list[str] | None = None,
        importance: float = 0.0,
        confidence: float = 0.0,
        novelty: float = 0.0,
        reusability: float = 0.0,
        actionability: float = 0.0,
    ) -> dict:
        mid = _uid()
        now = _now()
        score = self._calc_score(importance, confidence, novelty, reusability, actionability)
        self.conn.execute(
            """INSERT INTO memories (id, project_id, wing_id, room_id, hall_id,
               memory_type, title, content, confidence_label, confidence_score,
               tags, source_files,
               importance, confidence, novelty, reusability, actionability,
               score, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                mid, project_id, wing_id, room_id, hall_id,
                memory_type, title, content, confidence_label, confidence_score,
                _json_dumps(tags or []), _json_dumps(source_files or []),
                importance, confidence, novelty, reusability, actionability,
                score, now, now,
            ),
        )
        if memory_type not in {
            "project_core", "latest_conversation_summary", "project_summary",
        }:
            self._bump_project_revision(self.conn, project_id)
        self.conn.commit()
        return self.get_memory(mid)  # type: ignore

    def write_memories_batch(self, items: list[dict]) -> list[dict]:
        """Insert many memories in one transaction."""
        if not items:
            return []
        rows = []
        ids = []
        now = _now()
        for item in items:
            mid = item.get("id") or _uid()
            ids.append(mid)
            importance = float(item.get("importance", 0.0))
            confidence = float(item.get("confidence", 0.0))
            novelty = float(item.get("novelty", 0.0))
            reusability = float(item.get("reusability", 0.0))
            actionability = float(item.get("actionability", 0.0))
            rows.append((
                mid, item["project_id"], item.get("wing_id"), item.get("room_id"),
                item.get("hall_id", "general"), item.get("memory_type", "fact"),
                item.get("title", "Untitled"), item.get("content", ""),
                item.get("confidence_label", Confidence.INFERRED),
                float(item.get("confidence_score", confidence or 0.5)),
                _json_dumps(item.get("tags") or []),
                _json_dumps(item.get("source_files") or []),
                importance, confidence, novelty, reusability, actionability,
                self._calc_score(
                    importance, confidence, novelty, reusability, actionability,
                ),
                now, now,
            ))
        connection = self.conn
        try:
            connection.executemany(
                """INSERT INTO memories (id, project_id, wing_id, room_id, hall_id,
                   memory_type, title, content, confidence_label, confidence_score,
                   tags, source_files, importance, confidence, novelty, reusability,
                   actionability, score, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
            project_ids = {
                item["project_id"] for item in items
                if item.get("memory_type", "fact") not in {
                    "project_core", "latest_conversation_summary", "project_summary",
                }
            }
            for project_id in project_ids:
                self._bump_project_revision(connection, project_id)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        return self.search_memories_by_ids(ids)

    def get_memory(self, mid: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
        return dict(row) if row else None

    def get_project_summary(self, project_id: str) -> dict | None:
        """Return the active, auto-generated summary for a project."""
        return self.get_singleton_memory(project_id, "project_summary")

    def get_singleton_memory(self, project_id: str, memory_type: str) -> dict | None:
        """Return the active singleton memory of a given project-level type."""
        row = self.conn.execute(
            """SELECT * FROM memories
               WHERE project_id=? AND memory_type=? AND status='active'
               ORDER BY updated_at DESC LIMIT 1""",
            (project_id, memory_type),
        ).fetchone()
        return dict(row) if row else None

    def get_top_level_memories(self, project_id: str) -> dict[str, dict]:
        """Read all pinned project memories with one indexed query."""
        rows = self.conn.execute(
            """SELECT * FROM memories
               WHERE project_id=? AND status='active'
                 AND memory_type IN (
                    'project_core', 'latest_conversation_summary', 'project_summary'
                 )""",
            (project_id,),
        ).fetchall()
        return {row["memory_type"]: dict(row) for row in rows}

    def update_memory(self, mid: str, **kwargs) -> dict | None:
        existing = self.get_memory(mid)
        if not existing:
            return None
        now = _now()
        score_fields = {"importance", "confidence", "novelty", "reusability", "actionability"}
        if score_fields & set(kwargs.keys()):
            merged = {**existing, **kwargs}
            kwargs["score"] = self._calc_score(
                merged["importance"], merged["confidence"],
                merged["novelty"], merged["reusability"], merged["actionability"],
            )
        kwargs["updated_at"] = now
        set_clause = ", ".join(f"{k}=?" for k in kwargs)
        values = list(kwargs.values()) + [mid]
        self.conn.execute(f"UPDATE memories SET {set_clause} WHERE id=?", values)
        resulting_type = kwargs.get("memory_type", existing.get("memory_type"))
        if resulting_type not in {
            "project_core", "latest_conversation_summary", "project_summary",
        }:
            self._bump_project_revision(self.conn, existing["project_id"])
        self.conn.commit()
        return self.get_memory(mid)

    def search_memories(
        self,
        project_id: str | None = None,
        wing_id: str | None = None,
        room_id: str | None = None,
        hall_id: str | None = None,
        memory_type: str | None = None,
        keyword: str | None = None,
        tags: list[str] | None = None,
        confidence_label: str | None = None,
        min_score: float | None = None,
        status: str = "active",
        limit: int = 50,
    ) -> list[dict]:
        conditions = ["status=?"]
        params: list[Any] = [status]
        if project_id:
            conditions.append("project_id=?")
            params.append(project_id)
        if wing_id:
            conditions.append("wing_id=?")
            params.append(wing_id)
        if room_id:
            conditions.append("room_id=?")
            params.append(room_id)
        if hall_id:
            conditions.append("hall_id=?")
            params.append(hall_id)
        if memory_type:
            conditions.append("memory_type=?")
            params.append(memory_type)
        if keyword:
            conditions.append("(title LIKE ? OR content LIKE ?)")
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        if confidence_label:
            conditions.append("confidence_label=?")
            params.append(confidence_label)
        if min_score is not None:
            conditions.append("score >= ?")
            params.append(min_score)
        if tags:
            tag_conds = " OR ".join(["tags LIKE ?" for _ in tags])
            conditions.append(f"({tag_conds})")
            params.extend([f"%{t}%" for t in tags])
        where = " AND ".join(conditions)
        if memory_type is None:
            pinned = self.conn.execute(
                f"""SELECT * FROM memories WHERE {where}
                    AND memory_type IN (
                        'project_core', 'latest_conversation_summary', 'project_summary'
                    )
                    ORDER BY CASE memory_type
                        WHEN 'project_core' THEN 0
                        WHEN 'latest_conversation_summary' THEN 1
                        ELSE 2 END
                    LIMIT ?""",
                [*params, limit],
            ).fetchall()
            remaining = max(0, limit - len(pinned))
            ordinary = []
            if remaining:
                ordinary_rows = self.conn.execute(
                    f"""SELECT * FROM memories WHERE {where}
                        ORDER BY score DESC, updated_at DESC LIMIT ?""",
                    [*params, remaining + 3],
                ).fetchall()
                ordinary = [
                    row for row in ordinary_rows
                    if row["memory_type"] not in {
                        "project_core", "latest_conversation_summary", "project_summary",
                    }
                ][:remaining]
            return [dict(row) for row in [*pinned, *ordinary]]
        rows = self.conn.execute(
            f"SELECT * FROM memories WHERE {where} ORDER BY score DESC, updated_at DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
        return [dict(row) for row in rows]

    def search_memories_by_ids(self, ids: list[str]) -> list[dict]:
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = self.conn.execute(
            f"SELECT * FROM memories WHERE id IN ({placeholders})", ids
        ).fetchall()
        return [dict(r) for r in rows]

    def record_memory_hit(self, mid: str) -> None:
        now = _now()
        self.conn.execute(
            "UPDATE memories SET hit_count = hit_count + 1, last_hit_at = ? WHERE id = ?",
            (now, mid),
        )
        self.conn.commit()

    def delete_memory(self, mid: str) -> bool:
        existing = self.get_memory(mid)
        if not existing:
            return False
        self.conn.execute("DELETE FROM memory_edges WHERE source_id=? OR target_id=?", (mid, mid))
        cur = self.conn.execute("DELETE FROM memories WHERE id=?", (mid,))
        if existing["memory_type"] not in {
            "project_core", "latest_conversation_summary", "project_summary",
        }:
            self._bump_project_revision(self.conn, existing["project_id"])
        self.conn.commit()
        return cur.rowcount > 0

    def deprecate_memory(self, mid: str) -> bool:
        existing = self.get_memory(mid)
        if not existing:
            return False
        now = _now()
        cur = self.conn.execute(
            "UPDATE memories SET status='deprecated', deprecated_at=? WHERE id=?", (now, mid)
        )
        if existing["memory_type"] not in {
            "project_core", "latest_conversation_summary", "project_summary",
        }:
            self._bump_project_revision(self.conn, existing["project_id"])
        self.conn.commit()
        return cur.rowcount > 0

    def list_memories(
        self, project_id: str | None = None, wing_id: str | None = None,
        status: str = "active", limit: int = 200,
    ) -> list[dict]:
        conditions = ["status=?"]
        params: list[Any] = [status]
        if project_id:
            conditions.append("project_id=?")
            params.append(project_id)
        if wing_id:
            conditions.append("wing_id=?")
            params.append(wing_id)
        where = " AND ".join(conditions)
        pinned = self.conn.execute(
            f"""SELECT * FROM memories WHERE {where}
                AND memory_type IN (
                    'project_core', 'latest_conversation_summary', 'project_summary'
                )
                ORDER BY CASE memory_type
                    WHEN 'project_core' THEN 0
                    WHEN 'latest_conversation_summary' THEN 1
                    ELSE 2 END
                LIMIT ?""",
            [*params, limit],
        ).fetchall()
        remaining = max(0, limit - len(pinned))
        ordinary = []
        if remaining:
            ordinary_rows = self.conn.execute(
                f"""SELECT * FROM memories WHERE {where}
                    ORDER BY score DESC, updated_at DESC LIMIT ?""",
                [*params, remaining + 3],
            ).fetchall()
            ordinary = [
                row for row in ordinary_rows
                if row["memory_type"] not in {
                    "project_core", "latest_conversation_summary", "project_summary",
                }
            ][:remaining]
        return [dict(row) for row in [*pinned, *ordinary]]

    def list_summary_memories(self, project_id: str, limit: int = 120) -> list[dict]:
        """Return the bounded, highest-value source set for project summaries."""
        priority_types = (
            "goal", "principle", "architecture", "decision", "preference",
            "product_rule", "api_contract", "db_schema",
        )
        collected: list[dict] = []
        seen = set()
        per_type = max(4, min(12, limit // len(priority_types)))
        for memory_type in priority_types:
            rows = self.conn.execute(
                """SELECT * FROM memories
                   WHERE project_id=? AND status='active' AND memory_type=?
                     AND title NOT LIKE '[Graph]%'
                   ORDER BY score DESC, updated_at DESC LIMIT ?""",
                (project_id, memory_type, per_type),
            ).fetchall()
            for row in rows:
                item = dict(row)
                if item["id"] not in seen:
                    seen.add(item["id"])
                    collected.append(item)

        remaining = max(0, limit - len(collected))
        if remaining:
            placeholders = ",".join("?" for _ in priority_types)
            rows = self.conn.execute(
                f"""SELECT * FROM memories
                    WHERE project_id=? AND status='active'
                      AND memory_type NOT IN ({placeholders})
                      AND memory_type NOT IN (
                        'project_core', 'latest_conversation_summary', 'project_summary'
                      )
                      AND title NOT LIKE '[Graph]%'
                    ORDER BY updated_at DESC LIMIT ?""",
                (project_id, *priority_types, remaining),
            ).fetchall()
            collected.extend(dict(row) for row in rows)
        return collected[:limit]

    # ═══════════════════════════════════════════════════════════════════════
    # Memory Edges (graph relationships)
    # ═══════════════════════════════════════════════════════════════════════

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        relation: str = "related_to",
        confidence_label: str = Confidence.INFERRED,
        confidence_score: float = 0.5,
        metadata: dict | None = None,
    ) -> dict:
        eid = _uid()
        now = _now()
        self.conn.execute(
            "INSERT INTO memory_edges (id, source_id, target_id, relation, confidence_label, confidence_score, metadata, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (eid, source_id, target_id, relation, confidence_label, confidence_score,
             _json_dumps(metadata or {}), now),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM memory_edges WHERE id=?", (eid,)).fetchone()
        return dict(row)

    def get_neighbors(self, memory_id: str, direction: str = "both",
                      max_hops: int = 1) -> list[dict]:
        """Get graph neighbors of a memory node."""
        results: list[dict] = []
        if direction in ("out", "both"):
            rows = self.conn.execute(
                "SELECT * FROM memory_edges WHERE source_id=?", (memory_id,)
            ).fetchall()
            for r in rows:
                mem = self.get_memory(r["target_id"])
                if mem:
                    results.append({"edge": dict(r), "node": mem, "direction": "out"})
        if direction in ("in", "both"):
            rows = self.conn.execute(
                "SELECT * FROM memory_edges WHERE target_id=?", (memory_id,)
            ).fetchall()
            for r in rows:
                mem = self.get_memory(r["source_id"])
                if mem:
                    results.append({"edge": dict(r), "node": mem, "direction": "in"})
        return results

    def find_path(self, source_id: str, target_id: str, max_depth: int = 4) -> list[dict] | None:
        """BFS path between two nodes."""
        from collections import deque
        if source_id == target_id:
            return []
        visited = {source_id}
        queue = deque([(source_id, [])])
        while queue:
            current, path = queue.popleft()
            if len(path) >= max_depth:
                continue
            neighbors = self.get_neighbors(current, direction="both")
            for n in neighbors:
                nid = n["node"]["id"]
                if nid == target_id:
                    return path + [n]
                if nid not in visited:
                    visited.add(nid)
                    queue.append((nid, path + [n]))
        return None

    # ═══════════════════════════════════════════════════════════════════════
    # Temporal Knowledge Graph
    # ═══════════════════════════════════════════════════════════════════════

    def add_triple(
        self,
        subject: str,
        predicate: str,
        obj: str,
        valid_from: str | None = None,
        valid_to: str | None = None,
        confidence_label: str = Confidence.INFERRED,
        confidence_score: float = 0.5,
        drawer_ref: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        tid = _uid()
        now = _now()
        self.conn.execute(
            """INSERT INTO temporal_triples (id, subject, predicate, object,
               valid_from, valid_to, confidence_label, confidence_score,
               drawer_ref, metadata, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (tid, subject, predicate, obj, valid_from, valid_to,
             confidence_label, confidence_score, drawer_ref,
             _json_dumps(metadata or {}), now),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM temporal_triples WHERE id=?", (tid,)).fetchone()
        return dict(row)

    def query_entity(self, entity: str, direction: str = "both",
                     as_of: str | None = None) -> list[dict]:
        """Query all triples about an entity, optionally filtered by time."""
        if as_of:
            rows = self.conn.execute(
                """SELECT * FROM temporal_triples
                   WHERE (subject=? OR object=?)
                   AND (valid_from IS NULL OR valid_from <= ?)
                   AND (valid_to IS NULL OR valid_to >= ?)
                   AND invalidated_at IS NULL
                   ORDER BY created_at DESC""",
                (entity, entity, as_of, as_of),
            ).fetchall()
        elif direction == "both":
            rows = self.conn.execute(
                "SELECT * FROM temporal_triples WHERE (subject=? OR object=?) AND invalidated_at IS NULL ORDER BY created_at DESC",
                (entity, entity),
            ).fetchall()
        elif direction == "out":
            rows = self.conn.execute(
                "SELECT * FROM temporal_triples WHERE subject=? AND invalidated_at IS NULL ORDER BY created_at DESC",
                (entity,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM temporal_triples WHERE object=? AND invalidated_at IS NULL ORDER BY created_at DESC",
                (entity,),
            ).fetchall()
        return [dict(r) for r in rows]

    def invalidate_triple(self, triple_id: str, ended: str | None = None) -> bool:
        """Mark a triple as invalidated (no longer true)."""
        now = _now()
        cur = self.conn.execute(
            "UPDATE temporal_triples SET invalidated_at=?, valid_to=? WHERE id=?",
            (now, ended, triple_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_timeline(self, entity: str) -> list[dict]:
        """Get a timeline of changes for an entity."""
        rows = self.conn.execute(
            """SELECT * FROM temporal_triples
               WHERE subject=? OR object=?
               ORDER BY COALESCE(valid_from, created_at) DESC""",
            (entity, entity),
        ).fetchall()
        return [dict(r) for r in rows]

    # ═══════════════════════════════════════════════════════════════════════
    # Memory Candidates (enhanced)
    # ═══════════════════════════════════════════════════════════════════════

    def create_candidate(
        self,
        project_id: str,
        source_type: str,
        raw_text: str,
        extracted_title: str | None = None,
        extracted_content: str | None = None,
        candidate_type: str | None = None,
        wing_id: str | None = None,
        room_id: str | None = None,
        hall_id: str = "general",
        confidence_label: str = Confidence.INFERRED,
        reason: str | None = None,
        importance: float = 0.0,
        confidence: float = 0.0,
        novelty: float = 0.0,
        reusability: float = 0.0,
        actionability: float = 0.0,
    ) -> dict:
        cid = _uid()
        now = _now()
        score = self._calc_score(importance, confidence, novelty, reusability, actionability)
        self.conn.execute(
            """INSERT INTO memory_candidates (id, project_id, wing_id, room_id, hall_id,
               source_type, raw_text, extracted_title, extracted_content,
               candidate_type, reason, confidence_label,
               importance, confidence, novelty, reusability, actionability,
               score, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?)""",
            (
                cid, project_id, wing_id, room_id, hall_id,
                source_type, raw_text, extracted_title, extracted_content,
                candidate_type, reason, confidence_label,
                importance, confidence, novelty, reusability, actionability,
                score, now,
            ),
        )
        self.conn.commit()
        return self.get_candidate(cid)  # type: ignore

    def get_candidate(self, cid: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM memory_candidates WHERE id=?", (cid,)).fetchone()
        return dict(row) if row else None

    def update_candidate(self, cid: str, **kwargs) -> dict | None:
        now = _now()
        score_fields = {"importance", "confidence", "novelty", "reusability", "actionability"}
        if score_fields & set(kwargs.keys()):
            existing = self.get_candidate(cid)
            if existing:
                merged = {**existing, **kwargs}
                kwargs["score"] = self._calc_score(
                    merged["importance"], merged["confidence"],
                    merged["novelty"], merged["reusability"], merged["actionability"],
                )
        kwargs["reviewed_at"] = now
        set_clause = ", ".join(f"{k}=?" for k in kwargs)
        values = list(kwargs.values()) + [cid]
        self.conn.execute(f"UPDATE memory_candidates SET {set_clause} WHERE id=?", values)
        self.conn.commit()
        return self.get_candidate(cid)

    def list_pending_candidates(self, project_id: str | None = None,
                                wing_id: str | None = None) -> list[dict]:
        conditions = ["status='pending'"]
        params: list[Any] = []
        if project_id:
            conditions.append("project_id=?")
            params.append(project_id)
        if wing_id:
            conditions.append("wing_id=?")
            params.append(wing_id)
        where = " AND ".join(conditions)
        rows = self.conn.execute(
            f"SELECT * FROM memory_candidates WHERE {where} ORDER BY score DESC", params
        ).fetchall()
        return [dict(r) for r in rows]

    # ═══════════════════════════════════════════════════════════════════════
    # Reviews, Compactions, Tasks, Decisions
    # ═══════════════════════════════════════════════════════════════════════

    def create_review(self, candidate_id: str, decision: str,
                      merged_to: str | None = None, reason: str | None = None,
                      reviewer: str = "memory_curator") -> dict:
        rid = _uid()
        now = _now()
        self.conn.execute(
            "INSERT INTO memory_reviews (id, candidate_id, reviewer, decision, merged_to, reason, created_at) VALUES (?,?,?,?,?,?,?)",
            (rid, candidate_id, reviewer, decision, merged_to, reason, now),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM memory_reviews WHERE id=?", (rid,)).fetchone()
        return dict(row)

    def create_compaction(self, project_id: str, scope: str = "all",
                          wing_id: str | None = None, memories_before: int = 0,
                          memories_after: int = 0, removed_count: int = 0,
                          merged_count: int = 0, deprecated_count: int = 0,
                          summary: str = "") -> dict:
        cid = _uid()
        now = _now()
        self.conn.execute(
            """INSERT INTO memory_compactions (id, project_id, wing_id, scope,
               memories_before, memories_after, removed_count, merged_count,
               deprecated_count, summary, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (cid, project_id, wing_id, scope, memories_before, memories_after,
             removed_count, merged_count, deprecated_count, summary, now),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM memory_compactions WHERE id=?", (cid,)).fetchone()
        return dict(row)

    def record_task_snapshot(self, project_id: str, task_name: str,
                             status: str = "pending", completed: list[str] | None = None,
                             remaining: list[str] | None = None,
                             notes: str = "") -> dict:
        tid = _uid()
        now = _now()
        self.conn.execute(
            "INSERT INTO task_snapshots (id, project_id, task_name, status, completed, remaining, notes, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (tid, project_id, task_name, status, _json_dumps(completed or []),
             _json_dumps(remaining or []), notes, now),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM task_snapshots WHERE id=?", (tid,)).fetchone()
        return dict(row)

    def get_latest_task_snapshot(self, project_id: str, task_name: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM task_snapshots WHERE project_id=? AND task_name=? ORDER BY created_at DESC LIMIT 1",
            (project_id, task_name),
        ).fetchone()
        return dict(row) if row else None

    def list_task_snapshots(self, project_id: str, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM task_snapshots WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def record_decision(self, project_id: str, title: str, content: str,
                        rationale: str = "",
                        alternatives: list[str] | None = None) -> dict:
        did = _uid()
        now = _now()
        self.conn.execute(
            "INSERT INTO decisions (id, project_id, title, content, rationale, alternatives, created_at) VALUES (?,?,?,?,?,?,?)",
            (did, project_id, title, content, rationale, _json_dumps(alternatives or []), now),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM decisions WHERE id=?", (did,)).fetchone()
        return dict(row)

    def list_decisions(self, project_id: str, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM decisions WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ═══════════════════════════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _calc_score(importance: float, confidence: float, novelty: float,
                    reusability: float, actionability: float) -> float:
        return round(
            importance * 0.30 + confidence * 0.20 +
            novelty * 0.15 + reusability * 0.20 + actionability * 0.15, 4)

    def get_stats(self, project_id: str | None = None) -> dict:
        """Get memory stats for a project."""
        params: list[Any] = []
        where = ""
        if project_id:
            where = "WHERE project_id=?"
            params = [project_id]

        total = self.conn.execute(
            f"SELECT COUNT(*) as c FROM memories {where}", params
        ).fetchone()["c"]
        by_type = {}
        rows = self.conn.execute(
            f"SELECT memory_type, COUNT(*) as c FROM memories {where} GROUP BY memory_type", params
        ).fetchall()
        for r in rows:
            by_type[r["memory_type"]] = r["c"]
        by_confidence = {}
        rows2 = self.conn.execute(
            f"SELECT confidence_label, COUNT(*) as c FROM memories {where} GROUP BY confidence_label", params
        ).fetchall()
        for r in rows2:
            by_confidence[r["confidence_label"]] = r["c"]

        edge_count = self.conn.execute("SELECT COUNT(*) as c FROM memory_edges").fetchone()["c"]
        triple_count = self.conn.execute("SELECT COUNT(*) as c FROM temporal_triples WHERE invalidated_at IS NULL").fetchone()["c"]

        return {
            "total_memories": total,
            "by_type": by_type,
            "by_confidence": by_confidence,
            "total_edges": edge_count,
            "active_triples": triple_count,
        }

    def close(self) -> None:
        self._closed = True
        with self._connections_lock:
            connections = list(self._connections)
            self._connections.clear()
        for connection in connections:
            try:
                connection.close()
            except sqlite3.Error:
                pass
