"""Personalized PageRank for code graphs.

Sparse NumPy implementation used as the structural ranking layer: lexical
anchors say where the query lands, PPR says what is structurally close to
those anchors.

Relevance has to travel against an edge as well as along it — the callers of
a function matter when it changes, and a matched symbol should lift the file
that contains it — so every edge is walked in both directions, with a weight
per edge kind and direction.
"""

from __future__ import annotations

import numpy as np

# (weight along the edge, weight against it)
EDGE_WEIGHTS: dict[str, tuple[float, float]] = {
    "contains": (0.5, 1.0),  # file → symbol; a symbol speaks strongly for its file
    "import": (1.0, 0.5),    # importer → imported
    "call": (1.0, 0.7),      # caller → callee
    "co_edit": (1.0, 1.0),   # undirected
}
_DEFAULT_WEIGHT = (1.0, 0.5)
# Files that changed together five times are not five times as related as a
# pair that did once.
_MAX_EDGE_MULTIPLIER = 3.0


def personalized_pagerank(
    graph: dict,
    seed_weights: dict,
    *,
    alpha: float = 0.85,
    iters: int = 50,
    tol: float = 1e-9,
) -> dict:
    """Compute Personalized PageRank over a graph.

    Args:
        graph: A dict with "nodes" (dict of id→node) and "edges" (list of
            {src, dst, kind[, weight]}).
        seed_weights: {node_id: weight} — the personalization vector.
        alpha: Damping factor: the walk follows an edge with probability
            ``alpha`` and teleports back to the seeds with ``1 - alpha``.
        iters: Maximum number of power iterations.
        tol: Stop early once an iteration moves less than this much mass.

    Returns:
        {node_id: score}; scores are a probability distribution (sum to 1).
        Seeds absent from the graph are ignored; with no usable seed the
        walk is unpersonalized.

    A node with no edges at all hands its mass back to the seeds.
    """
    nodes = list(graph.get("nodes", {}))
    if not nodes:
        return {}
    idx = {node: i for i, node in enumerate(nodes)}
    n = len(nodes)

    src_ix: list[int] = []
    dst_ix: list[int] = []
    weights: list[float] = []
    for edge in graph.get("edges", []):
        s_i, d_i = idx.get(edge["src"]), idx.get(edge["dst"])
        if s_i is None or d_i is None or s_i == d_i:
            continue
        forward, backward = EDGE_WEIGHTS.get(edge.get("kind", ""), _DEFAULT_WEIGHT)
        scale = min(float(edge.get("weight", 1.0)), _MAX_EDGE_MULTIPLIER)
        src_ix += (s_i, d_i)
        dst_ix += (d_i, s_i)
        weights += (forward * scale, backward * scale)
    src_arr = np.asarray(src_ix, dtype=np.intp)
    dst_arr = np.asarray(dst_ix, dtype=np.intp)
    w_arr = np.asarray(weights, dtype=np.float64)

    out_weight = np.bincount(src_arr, weights=w_arr, minlength=n)
    dangling = out_weight == 0.0
    transition = w_arr / out_weight[src_arr] if len(w_arr) else w_arr

    p = np.zeros(n, dtype=np.float64)
    for node, weight in seed_weights.items():
        i = idx.get(node)
        if i is not None and weight > 0:
            p[i] = weight
    total = p.sum()
    p = p / total if total > 0 else np.full(n, 1.0 / n)

    r = p.copy()
    for _ in range(iters):
        spread = np.bincount(dst_arr, weights=r[src_arr] * transition, minlength=n)
        nxt = alpha * (spread + r[dangling].sum() * p) + (1.0 - alpha) * p
        moved = np.abs(nxt - r).sum()
        r = nxt
        if moved < tol:
            break

    return {nodes[i]: float(r[i]) for i in range(n)}
