# -*- coding: utf-8 -*-
"""Memory Curator Server v2 — Enhanced with Graphify + MemPalace.

Spatial memory system inspired by:
  - graphify (safishamsi/graphify): code pipeline, Leiden clustering, confidence labels
  - mempalace (MemPalace/mempalace): palace architecture, temporal KG, hall routing

Modules:
  - config: unified configuration (env > file > defaults)
  - db: SQLite layer with palace + graph + temporal triple tables
  - backends: pluggable vector store (base/lancedb)
  - pipeline: wraps graphify extract + cluster
  - palace: wraps mempalace get_collection + detect_hall + KnowledgeGraph
  - curator: ingest → score → dedup → store → compact → prune
  - server: 29+ MCP tools exposed to AI assistants
"""

__version__ = "1.12"
