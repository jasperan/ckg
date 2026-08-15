"""Hybrid retrieval — lexical anchors → graph reach.

The CKG core retrieval algorithm. Combines:

1. Lexical seed selection — token overlap between query and node labels
2. Graph neighborhood match — PGQ or in-memory traversal from seeds
3. Personalized PageRank ranking — structural importance scoring

This is the algorithm verified across 7 real httpie PRs:
  - Lexical-only gold recall: 0.20
  - Hybrid gold recall: 0.72 (+0.52)

Usage:
    from ckg.retrieval import hybrid_retrieve
    results = hybrid_retrieve(query, graph, mem=agent_memory, domain="my_project")
"""

from __future__ import annotations

from ckg.retrieval.pagerank import personalized_pagerank, seed_from_query


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

    Pipeline:
      1. Lexical anchors: token-overlap seeds from node labels
      2. Graph neighborhood: PGQ match (preferred) or in-memory BFS
      3. Personalized PageRank: structural ranking over the neighborhood

    Args:
        query: Feature description or retrieval query.
        graph: Parsed code graph dict with "nodes" and "edges".
        mem: Optional AgentMemory with live Oracle connection (for PGQ).
        domain: Domain for PGQ scoping (required if mem is provided).
        k_anchor: Maximum number of lexical anchor seeds (default 5).
        hops: Graph reach depth (default 2).
        top_k: Number of top results to return.
        graph_name: Oracle PGQ property graph name.

    Returns:
        dict with:
          - results: list of {node_id, score, text}
          - anchors: list of anchor node IDs
          - anchor_labels: anchor file names
          - method: "pgq" or "memory"
    """
    nodes = graph.get("nodes", {})

    # Step 1 — lexical anchors
    seeds = seed_from_query(query, nodes, k=k_anchor)
    anchors = list(seeds.keys())

    # Step 2 — graph neighborhood
    neighborhood: dict[str, dict] = {}

    if mem is not None and domain is not None:
        # PGQ path — match neighborhood in the database
        try:
            from ckg.storage.oracle_pgq import match_edges, match_neighborhood
            neighbors: set[str] = set(anchors)
            for anchor in anchors:
                matched = match_neighborhood(
                    mem, anchor=anchor, domain=domain,
                    hops=hops, graph_name=graph_name,
                )
                for row in matched:
                    neighbors.add(row["neighbor"])
            # Build subgraph from matched nodes
            subgraph_nodes = {
                nid: nodes.get(nid, {"id": nid, "text": nid})
                for nid in neighbors if nid in nodes
            }
            # Filter edges to those in the neighborhood
            subgraph_edges = [
                e for e in graph.get("edges", [])
                if e["src"] in neighbors and e["dst"] in neighbors
            ]
            neighborhood = {"nodes": subgraph_nodes, "edges": subgraph_edges}
            method = "pgq"
        except Exception as exc:  # PGQ unavailable — fall back to in-memory
            import re as _re
            m = _re.search(r"ORA-\d+: ([^\n]+)", str(exc))
            hint = (m.group(1).strip() if m else str(exc).splitlines()[0])[:60]
            method = f"memory (pgq unavailable: {hint}; run 'ckg load' to store the graph)"
            neighborhood = _memory_neighborhood(graph, anchors, hops=hops)
    else:
        method = "memory"
        neighborhood = _memory_neighborhood(graph, anchors, hops=hops)

    if not neighborhood.get("nodes"):
        # No neighborhood found — return just anchors with PPR over full graph
        scored = personalized_pagerank(graph, seeds)
        results = [
            {"node_id": nid, "score": round(score, 4),
             "text": nodes.get(nid, {}).get("text", nid)}
            for nid, score in sorted(scored.items(), key=lambda x: -x[1])[:top_k]
        ]
        return {
            "results": results,
            "anchors": anchors,
            "anchor_labels": [_label(nid, nodes) for nid in anchors],
            "method": f"{method} (empty neighborhood)",
        }

    # Step 3 — Personalized PageRank over the neighborhood subgraph
    scored = personalized_pagerank(neighborhood, seeds)
    results = [
        {"node_id": nid, "score": round(score, 4),
         "text": nodes.get(nid, {}).get("text", nid)}
        for nid, score in sorted(scored.items(), key=lambda x: -x[1])[:top_k]
    ]

    return {
        "results": results,
        "anchors": anchors,
        "anchor_labels": [_label(nid, nodes) for nid in anchors],
        "method": method,
    }


def _memory_neighborhood(graph: dict, seeds: list[str], *, hops: int = 2) -> dict:
    """Build a neighborhood subgraph via in-memory BFS from seeds."""
    from collections import deque

    nodes = graph.get("nodes", {})
    edges = graph.get("edges", [])

    # Build adjacency
    adj: dict[str, set[str]] = {nid: set() for nid in nodes}
    for e in edges:
        src, dst = e["src"], e["dst"]
        if src in adj:
            adj[src].add(dst)
        if dst in adj:
            adj[dst].add(src)

    # BFS
    visited: set[str] = set(seeds)
    queue = deque((s, 0) for s in seeds if s in adj)
    while queue:
        current, depth = queue.popleft()
        if depth >= hops:
            continue
        for nbr in adj.get(current, set()):
            if nbr not in visited:
                visited.add(nbr)
                queue.append((nbr, depth + 1))

    sub_nodes = {nid: nodes.get(nid, {"id": nid, "text": nid}) for nid in visited}
    sub_edges = [
        e for e in edges
        if e["src"] in visited and e["dst"] in visited
    ]
    return {"nodes": sub_nodes, "edges": sub_edges}


def _label(node_id: str, nodes: dict) -> str:
    """Extract a human-readable label from a node ID."""
    if node_id.startswith("file:"):
        return node_id.removeprefix("file:")
    parts = node_id.split("::", 1)
    if len(parts) == 2:
        return parts[1].rsplit(".", 1)[-1] if "." in parts[1] else parts[1]
    return node_id
