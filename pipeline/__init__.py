# -*- coding: utf-8 -*-
"""Pipeline — wraps graphify's extract + cluster for memory pipeline.

Uses the real graphify library for:
  - extract(paths) → {nodes, edges} dict  (tree-sitter AST + LLM)
  - cluster(G) → community detection  (Leiden algorithm)
Then feeds results into our SQLite db + vector store.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import networkx as nx

from ..config import get_config, Confidence
from ..db import MemoryDB


def _load_graphify():
    try:
        from graphify.extract import extract as graph_extract
        from graphify.cluster import cluster as graph_cluster
    except ImportError as exc:
        raise RuntimeError(
            "Code analysis requires the optional Graphify integration. "
            "Install it with: pip install 'memery-mcp[graph]'"
        ) from exc
    return graph_extract, graph_cluster


def run_pipeline(
    db: MemoryDB,
    project_name: str,
    paths: list[str],
    *,
    use_cache: bool = True,
    parallel: bool = True,
) -> dict:
    """Run the full graphify-inspired pipeline on a set of paths.

    detect → extract → build_graph → cluster → analyze → report

    Returns a summary dict with nodes, edges, communities, god_nodes.
    """
    cfg = get_config()
    graph_extract, graph_cluster = _load_graphify()
    project = db.get_project_by_name(project_name)
    if not project:
        return {"error": f"Project '{project_name}' not found."}
    project_id = project["id"]

    path_objs = [Path(p).resolve() for p in paths]
    valid_paths = [p for p in path_objs if p.exists()]
    if not valid_paths:
        return {"error": "No valid paths found."}

    # Phase 1: Extract structural knowledge from code files
    extraction = graph_extract(valid_paths, parallel=parallel)

    nodes = extraction.get("nodes", [])
    edges = extraction.get("edges", [])

    # Phase 2: Build NetworkX graph for clustering
    G = nx.Graph()
    for node in nodes:
        G.add_node(node["id"], **{k: v for k, v in node.items() if k != "id"})
    for edge in edges:
        src, tgt = edge.get("source"), edge.get("target")
        if src and tgt:
            attrs = {k: v for k, v in edge.items() if k not in ("source", "target")}
            G.add_edge(src, tgt, **attrs)

    # Phase 3: Community detection (Leiden)
    communities = {}
    if G.number_of_nodes() > 0:
        try:
            communities = graph_cluster(G)
        except Exception:
            communities = {}

    # Phase 4: Store results as memories
    memory_rows = []
    for node in nodes:
        node_id = node.get("id", "")
        label = node.get("label", node_id)
        source_file = node.get("source_file", "")
        mem_type = "architecture" if source_file else "concept"

        memory_rows.append({
            "project_id": project_id,
            "memory_type": mem_type,
            "title": f"[Graph] {label}",
            "content": f"Node: {label}\nSource: {source_file}\nType: {node.get('type', 'unknown')}",
            "confidence_label": Confidence.EXTRACTED,
            "confidence_score": 0.95,
            "source_files": [source_file] if source_file else [],
            "importance": 0.7,
            "confidence": 0.95,
            "novelty": 0.5,
            "reusability": 0.6,
            "actionability": 0.3,
        })
    stored_memories = db.write_memories_batch(memory_rows)
    stored_count = len(stored_memories)

    # Store edges as graph relationships
    edge_count = 0
    for edge in edges:
        src, tgt = edge.get("source"), edge.get("target")
        if src and tgt:
            relation = edge.get("relation", "related_to")
            conf_label = edge.get("confidence", Confidence.INFERRED)
            conf_score = edge.get("confidence_score", 0.5)
            try:
                db.add_edge(
                    source_id=src, target_id=tgt,
                    relation=relation,
                    confidence_label=conf_label,
                    confidence_score=conf_score,
                )
                edge_count += 1
            except Exception:
                pass

    # Phase 5: God nodes (most connected)
    degree_sorted = sorted(G.degree, key=lambda x: x[1], reverse=True) if G.number_of_nodes() > 0 else []
    god_nodes = []
    for node_id, deg in degree_sorted[:cfg.god_node_top_n]:
        attrs = G.nodes[node_id] if node_id in G.nodes else {}
        god_nodes.append({
            "id": node_id,
            "label": attrs.get("label", node_id),
            "degree": deg,
        })

    # Phase 6: Community summary
    community_summary = {}
    for cid, c_nodes in communities.items():
        labels = [G.nodes[n].get("label", n) for n in c_nodes if n in G.nodes]
        community_summary[cid] = {
            "size": len(c_nodes),
            "top_nodes": labels[:5],
        }

    return {
        "project": project_name,
        "paths_scanned": len(valid_paths),
        "nodes_found": len(nodes),
        "edges_found": len(edges),
        "communities": len(communities),
        "god_nodes": god_nodes,
        "community_summary": community_summary,
        "memories_stored": stored_count,
        "graph_edges_stored": edge_count,
    }


def extract_from_text(text: str) -> dict:
    """Extract structured knowledge from plain text using graphify.

    Writes text to a temp file, runs extract, returns nodes+edges.
    """
    import tempfile
    graph_extract, _ = _load_graphify()
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write(text)
        tmp_path = f.name

    try:
        extraction = graph_extract([Path(tmp_path)], parallel=False)
    finally:
        try:
            Path(tmp_path).unlink()
        except OSError:
            pass

    return {
        "nodes": extraction.get("nodes", []),
        "edges": extraction.get("edges", []),
    }
