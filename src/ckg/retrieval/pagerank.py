"""Personalized PageRank for code graphs.

Pure NumPy implementation of Personalized PageRank, used as the structural
ranking layer after PGQ retrieves the dependency neighborhood.

The split: Oracle PGQ finds which nodes are connected → Python's PPR ranks them
by structural importance given lexical anchor seeds.
"""

from __future__ import annotations

import numpy as np


def personalized_pagerank(
    graph: dict,
    seed_weights: dict,
    *,
    alpha: float = 0.85,
    iters: int = 50,
) -> dict:
    """Compute Personalized PageRank over a graph.

    Args:
        graph: A dict with "nodes" (dict of id→node) and "edges" (list of {src, dst, kind}).
               Weights are derived from edges; all edges contribute equally.
        seed_weights: {node_id: weight} — which nodes to personalize toward.
        alpha: Teleport probability (0.85 = standard PPR).
        iters: Number of power iterations.

    Returns:
        {node_id: ppr_score} dict, sorted descending.

    Edge weights: each node distributes its mass equally to all outgoing
    neighbors. A lazy self-loop (weight=1) is added so seed mass stays put
    on connected nodes. Isolated nodes have their mass redistributed via
    the teleport vector.
    """
    nodes = list(graph.get("nodes", {}).keys())
    if not nodes:
        return {}

    idx = {n: i for i, n in enumerate(nodes)}
    n = len(nodes)
    M = np.zeros((n, n), dtype=np.float64)
    dangling = np.zeros(n, dtype=np.float64)

    # Build transition matrix from edges
    out_degree: dict[str, float] = {node: 1.0 for node in nodes}  # +1 for self-loop
    for edge in graph.get("edges", []):
        src, dst = edge["src"], edge["dst"]
        if src in idx and dst in idx:
            out_degree[src] = out_degree.get(src, 1.0) + 1.0

    for edge in graph.get("edges", []):
        src, dst = edge["src"], edge["dst"]
        if src in idx and dst in idx:
            M[idx[dst], idx[src]] += 1.0 / out_degree[src]

    # Self-loops
    for node_id in nodes:
        i = idx[node_id]
        M[i, i] += 1.0 / out_degree[node_id]

    # Dangling nodes
    for i, node_id in enumerate(nodes):
        if out_degree[node_id] == 1.0:  # only the self-loop
            dangling[i] = 1.0

    # Seed vector
    p = np.zeros(n, dtype=np.float64)
    s = sum(seed_weights.values()) or 1.0
    for node, w in seed_weights.items():
        if node in idx:
            p[idx[node]] = w / s
    if p.sum() == 0:
        p[:] = 1.0 / n

    # Power iteration
    r = p.copy()
    for _ in range(iters):
        dmass = float(r @ dangling)
        r = alpha * (M @ r) + alpha * dmass * p + (1 - alpha) * p

    total = r.sum() or 1.0
    r = r / total

    return {nodes[i]: float(r[i]) for i in range(n)}


def seed_from_query(
    query: str,
    nodes: dict[str, dict],
    *,
    k: int = 5,
) -> dict:
    """Pick seed nodes by lexical overlap between query tokens and node text.

    Each node contributes equally (weight=1.0) if any of its text tokens
    match query tokens. This is the *lexical anchor* step — keyword entry
    that mirrors what a coding agent would find by itself.

    Args:
        query: Natural-language query or feature description.
        nodes: Dict of {node_id: {text: str, ...}}.
        k: Maximum number of seed nodes.

    Returns:
        {node_id: 1.0} for each matched node, up to k.
    """
    import re
    _token_re = re.compile(r"[a-z0-9_]+")

    def _tokens(text: str) -> set[str]:
        out: set[str] = set()
        for raw in _token_re.findall(text.lower()):
            out.add(raw)
            # Also split snake_case
            parts = raw.split("_")
            if len(parts) > 1:
                out.update(p for p in parts if len(p) > 1)
        return out

    query_tokens = _tokens(query)
    scored: list[tuple[str, int]] = []
    for nid, node in nodes.items():
        text = node.get("text", nid)
        node_tokens = _tokens(text)
        overlap = len(query_tokens & node_tokens)
        if overlap > 0:
            scored.append((nid, overlap))
    scored.sort(key=lambda x: -x[1])
    return {nid: 1.0 for nid, _ in scored[:k]}
