"""Hybrid retrieval — lexical anchors → graph reach → structural ranking.

1. Lexical seed selection — which nodes the query names (``lexical``)
2. Graph neighbourhood — nodes within a few hops of the seeds, matched by
   Oracle PGQ when a database is configured, in memory otherwise
3. Personalized PageRank over that neighbourhood (``pagerank``)

Usage:
    from ckg.retrieval import hybrid_retrieve
    results = hybrid_retrieve(query, graph)
"""

from __future__ import annotations

import re
from collections import deque

from ckg.retrieval.lexical import seed_from_query
from ckg.retrieval.pagerank import personalized_pagerank


def hybrid_retrieve(
    query: str,
    graph: dict,
    *,
    mem=None,
    domain: str | None = None,
    k_anchor: int = 5,
    hops: int = 2,
    top_k: int = 10,
    graph_name: str = "ckg_code_graph",
) -> dict:
    """Run the full hybrid retrieval pipeline.

    Args:
        query: Feature description or retrieval query.
        graph: Graph dict with "nodes" and "edges" (and optionally the "lex"
            index written by ``build_graph``).
        mem: Optional live Oracle handle (``connect_pgq``) for PGQ matching.
        domain: Domain for PGQ scoping (required if mem is provided).
        k_anchor: Maximum number of lexical anchor seeds.
        hops: Graph reach depth.
        top_k: Number of top results to return.
        graph_name: Oracle PGQ property graph name.

    Returns:
        dict with:
          - results: list of {node_id, score, text}, best first
          - anchors: anchor node IDs, strongest match first
          - anchor_labels: short display names of the anchors
          - file_scores: {file_id: score} for every file in the neighbourhood
            (a file's own score plus that of its symbols)
          - method: "pgq", "memory", or "memory (...)" with the reason PGQ was
            not used

    A query that names nothing in the graph returns no results: without an
    anchor there is nothing to rank against.
    """
    nodes = graph.get("nodes", {})
    seeds = seed_from_query(query, nodes, k=k_anchor, index=graph.get("lex"))
    anchors = list(seeds)
    method = "memory"
    if not seeds:
        return {
            "results": [], "anchors": [], "anchor_labels": [], "file_scores": {},
            "method": f"{method} (no lexical match)",
        }

    neighborhood: dict | None = None
    if mem is not None and domain is not None:
        try:
            from ckg.storage.oracle_pgq import match_neighborhood_bulk

            matched = match_neighborhood_bulk(
                mem, anchors=anchors, domain=domain, hops=hops, graph_name=graph_name,
            )
            keep = set(anchors) | {row["neighbor"] for row in matched}
            neighborhood = _subgraph(graph, keep)
            method = "pgq"
        except Exception as exc:  # PGQ unavailable — fall back to in-memory
            method = f"memory (pgq unavailable: {_reason(exc)})"
    if neighborhood is None:
        neighborhood = _memory_neighborhood(graph, anchors, hops=hops)

    scored = personalized_pagerank(neighborhood, seeds)
    ranked = sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))
    file_scores: dict[str, float] = {}
    for nid, score in ranked:
        file_id = _file_of(nid)
        file_scores[file_id] = file_scores.get(file_id, 0.0) + score

    return {
        "results": [
            {"node_id": nid, "score": round(score, 4), "text": nodes.get(nid, {}).get("text", nid)}
            for nid, score in ranked[: max(top_k, 0)]
        ],
        "anchors": anchors,
        "anchor_labels": [_label(nid, nodes) for nid in anchors],
        "file_scores": file_scores,
        "method": method,
    }


def _reason(exc: Exception) -> str:
    """One short line saying why PGQ was not used, with a hint when the cause
    is a missing graph rather than a missing database."""
    text = str(exc)
    ora = re.search(r"ORA-(\d+): ([^\n]+)", text)
    if ora:
        hint = ora.group(2).strip()[:60]
        if ora.group(1) in ("00942", "40434", "00904"):  # table / graph / column missing
            return f"{hint}; run 'ckg load' to store the graph"
        return hint
    return ((text.splitlines() or [type(exc).__name__])[0] or type(exc).__name__)[:60]


def _file_of(node_id: str) -> str:
    if node_id.startswith("sym:"):
        return "file:" + node_id[4:].split("::", 1)[0]
    return node_id


def _subgraph(graph: dict, keep: set[str]) -> dict:
    nodes = graph.get("nodes", {})
    return {
        "nodes": {nid: nodes[nid] for nid in sorted(keep) if nid in nodes},
        "edges": [
            e for e in graph.get("edges", [])
            if e["src"] in keep and e["dst"] in keep
        ],
    }


def _memory_neighborhood(graph: dict, seeds: list[str], *, hops: int = 2) -> dict:
    """Subgraph within ``hops`` edges of the seeds, ignoring edge direction."""
    nodes = graph.get("nodes", {})
    adj: dict[str, list[str]] = {}
    for e in graph.get("edges", []):
        src, dst = e["src"], e["dst"]
        if src in nodes and dst in nodes:
            adj.setdefault(src, []).append(dst)
            adj.setdefault(dst, []).append(src)

    visited: set[str] = {s for s in seeds if s in nodes}
    queue = deque((s, 0) for s in seeds if s in nodes)
    while queue:
        current, depth = queue.popleft()
        if depth >= hops:
            continue
        for nbr in adj.get(current, ()):
            if nbr not in visited:
                visited.add(nbr)
                queue.append((nbr, depth + 1))
    return _subgraph(graph, visited)


def _label(node_id: str, nodes: dict) -> str:
    """Extract a human-readable label from a node ID."""
    if node_id.startswith("file:"):
        return node_id.removeprefix("file:")
    parts = node_id.split("::", 1)
    if len(parts) == 2:
        return parts[1].rsplit(".", 1)[-1] if "." in parts[1] else parts[1]
    return node_id
