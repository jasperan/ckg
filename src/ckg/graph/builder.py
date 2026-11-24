"""Graph builder — build the graph, hold it in memory, render structure maps.

``build_graph`` is the single entry point that turns a source tree into the
graph dict stored in ``.ckg/code_graph.json``: parsed nodes and edges plus the
lexical index used for anchor selection. ``CodeGraph`` wraps such a dict for
neighbourhood queries, and ``render_structure_map`` produces the compact
markdown map that agents receive.
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path


def validate_graph(data: object) -> dict:
    """Return ``data`` if it has the shape of a ckg graph, else raise ValueError.

    Graph files are read back from disk (and may have been written by another
    version, truncated, or committed by someone else), so every consumer
    checks the shape once instead of failing with a KeyError mid-render.
    """
    if not isinstance(data, dict):
        raise ValueError("invalid ckg graph: not a JSON object")
    nodes = data.get("nodes")
    edges = data.get("edges", [])
    if not isinstance(nodes, dict):
        raise ValueError("invalid ckg graph: 'nodes' must be an object")
    if not isinstance(edges, list):
        raise ValueError("invalid ckg graph: 'edges' must be a list")
    for nid, node in nodes.items():
        if not isinstance(node, dict) or not isinstance(node.get("text", ""), str):
            raise ValueError(f"invalid ckg graph: malformed node {nid!r}")
    for edge in edges:
        if not (
            isinstance(edge, dict)
            and isinstance(edge.get("src"), str)
            and isinstance(edge.get("dst"), str)
            and isinstance(edge.get("kind"), str)
            and isinstance(edge.get("weight", 1), (int, float))
        ):
            raise ValueError(f"invalid ckg graph: malformed edge {edge!r}")
    if not isinstance(data.get("meta", {}), dict):
        raise ValueError("invalid ckg graph: 'meta' must be an object")
    return data


@dataclass
class CodeGraph:
    """In-memory representation of a parsed code graph.

    Lightweight wrapper around the parsed nodes and edges with convenience
    methods for neighborhood queries and structure map rendering.
    """

    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    # Derived indices (built lazily on first access)
    _node_index: dict[str, dict] | None = field(default=None, repr=False)
    _adj: dict[str, list[tuple[str, str, str]]] | None = field(default=None, repr=False)
    _file_adj: dict[str, list[tuple[str, str]]] | None = field(default=None, repr=False)

    @property
    def n_files(self) -> int:
        return self.meta.get("n_files", 0)

    @property
    def n_nodes(self) -> int:
        return len(self.nodes)

    @property
    def n_edges(self) -> int:
        return len(self.edges)

    def _ensure_index(self) -> None:
        if self._node_index is not None:
            return
        self._node_index = {n["id"]: n for n in self.nodes}
        self._adj = defaultdict(list)
        self._file_adj = defaultdict(list)
        for e in self.edges:
            src, dst, kind = e["src"], e["dst"], e["kind"]
            self._adj[src].append((dst, kind, "out"))
            self._adj[dst].append((src, kind, "in"))
            if src.startswith("file:") and dst.startswith("file:"):
                self._file_adj[src].append((dst, kind))

    def node(self, node_id: str) -> dict | None:
        self._ensure_index()
        return self._node_index.get(node_id)  # type: ignore[union-attr]

    def neighbors(self, node_id: str) -> list[tuple[str, str, str]]:
        """Return (neighbor_id, edge_kind, direction) for a node."""
        self._ensure_index()
        return self._adj.get(node_id, [])  # type: ignore[union-attr]

    def file_neighbors(self, file_id: str) -> list[tuple[str, str]]:
        """Return (neighbor_file_id, edge_kind) at the file level."""
        self._ensure_index()
        return self._file_adj.get(file_id, [])  # type: ignore[union-attr]

    def file_nodes(self) -> list[str]:
        """All file-level node IDs."""
        self._ensure_index()
        return [nid for nid in self._node_index if nid.startswith("file:")]  # type: ignore[union-attr]

    @classmethod
    def from_dict(cls, data: dict) -> CodeGraph:
        """Wrap a graph dict. Raises ValueError when it is not a ckg graph."""
        validate_graph(data)
        nodes = [{**node, "id": nid} for nid, node in data["nodes"].items()]
        return cls(nodes=nodes, edges=data.get("edges", []), meta=data.get("meta", {}))


def build_graph(tree: Path, *, pkg_root: str) -> dict:
    """Parse ``tree`` into a graph and attach the lexical index.

    The single entry point used by ``ckg build``/``ckg load`` and the on-demand
    cache build, so every graph cache has the same shape.
    """
    from ckg.graph.parser import parse_tree
    from ckg.retrieval.lexical import build_index

    graph = parse_tree(Path(tree), pkg_root=pkg_root)
    graph["lex"] = build_index(graph["nodes"])
    return graph


# --------------------------------------------------------------------------- #
# Structure map
# --------------------------------------------------------------------------- #

# How a neighbouring file relates to the file it was reached from, keyed by
# (edge kind, edge points from the neighbour).
_RELATION: dict[tuple[str, bool], str] = {
    ("import", True): "imports",
    ("import", False): "imported by",
    ("call", True): "calls",
    ("call", False): "called by",
    ("co_edit", True): "co-edited with",
    ("co_edit", False): "co-edited with",
}
_RELATION_ORDER = ("imports", "imported by", "calls", "called by", "co-edited with")


def _file_reach(graph: CodeGraph, file_id: str) -> list[tuple[str, str]]:
    """(neighbor_file_id, relation) for a file, lifting symbol call edges.

    Call edges connect symbols, so a call between a symbol of file A and a
    symbol of file B counts as an A–B connection. The relation is phrased
    from the neighbour's side ("imports", "called by", ...). Edge kinds this
    version does not know are ignored rather than echoed.
    """
    out: list[tuple[str, str]] = []
    for nbr, kind, direction in graph.neighbors(file_id):
        if nbr.startswith("file:"):
            relation = _RELATION.get((kind, direction == "in"))
            if relation and nbr != file_id:
                out.append((nbr, relation))
        elif kind == "contains" and direction == "out":
            for sym_nbr, sym_kind, sym_dir in graph.neighbors(nbr):
                if sym_kind != "call" or not sym_nbr.startswith("sym:"):
                    continue
                other = "file:" + sym_nbr.removeprefix("sym:").split("::", 1)[0]
                if other != file_id:
                    out.append((other, _RELATION[("call", sym_dir == "in")]))
    return out


_MAP_LABEL_MAX = 200
_MAP_PATH_MAX = 200
_MAP_QUERY_MAX = 200
_MAP_STRIP = {ord(c): None for c in "`*#[]<>|"}


def _render_text(value: object, limit: int) -> str:
    """Untrusted text (repo docstrings and paths, cache files, the prompt) as
    one bounded line without markdown structure or invisible characters, so it
    cannot open headings, rules or fences inside the injected map."""
    text = "".join(
        " " if unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") else ch
        for ch in str(value)
    )
    text = " ".join(text.split()).translate(_MAP_STRIP)
    while "---" in text:
        text = text.replace("---", "-")
    return text[:limit]


def _file_line(graph: CodeGraph, file_id: str) -> str:
    rel = _render_text(file_id.removeprefix("file:"), _MAP_PATH_MAX)
    node = graph.node(file_id)
    label = _render_text(node.get("text", ""), _MAP_LABEL_MAX) if node else ""
    if not label or label == f"Python module {rel}":
        return f"- `{rel}`"
    return f"- `{rel}` — {label}"


def render_structure_map(
    anchors: list[str],
    graph: CodeGraph,
    *,
    hops: int = 2,
    max_extra: int = 15,
    query: str = "",
    scores: dict[str, float] | None = None,
) -> str:
    """Render a compact markdown structure map for agent injection.

    Args:
        anchors: Top file anchor IDs (e.g., ["file:x.py", "file:y.py"]).
        graph: The parsed code graph.
        hops: How many hops of graph reach to include.
        max_extra: Maximum extra files to include beyond anchors.
        query: The retrieval query (for the preamble).
        scores: Relevance per file id. When given, the reach list shows the
            highest-scoring files first; otherwise the nearest ones.

    Returns:
        A markdown string suitable for appending to a system prompt.
    """
    graph._ensure_index()
    anchor_set = set(anchors)
    seen: set[str] = set(anchors)

    # A file next to an anchor is described by how it relates to the anchors;
    # one reached through another file only by which files lead to it.
    queue = deque((a, 0) for a in anchors)
    depth_of: dict[str, int] = {}
    direct: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    via: dict[str, set[str]] = defaultdict(set)
    while queue:
        current, depth = queue.popleft()
        if depth >= hops:
            continue
        for nbr, relation in _file_reach(graph, current):
            if nbr in anchor_set:
                continue
            if nbr not in seen:
                seen.add(nbr)
                depth_of[nbr] = depth + 1
                queue.append((nbr, depth + 1))
            if current in anchor_set:
                direct[nbr][relation].add(current)
            else:
                via[nbr].add(current)

    def names(file_ids: set[str]) -> str:
        return ", ".join(
            _render_text(f.removeprefix("file:"), _MAP_PATH_MAX) for f in sorted(file_ids)[:3]
        )

    shown_query = _render_text(query, _MAP_QUERY_MAX)
    parts: list[str] = [
        "## Code Structure Map",
        "",
        f"**Query**: {shown_query}" if shown_query else "**Query**: (feature task)",
        "",
        "### Anchor Files (lexical match)",
        "",
    ]
    parts.extend(_file_line(graph, a) for a in anchors)

    parts.append("")
    parts.append(f"### Dependency Reach ({int(hops)}-hop imports, calls, co-edits)")
    parts.append("")

    if not depth_of:
        parts.append("*(No additional files found in the graph neighborhood.)*")
    else:
        rank = scores or {}
        ordered = sorted(
            depth_of, key=lambda fid: (-rank.get(fid, 0.0), depth_of[fid], fid)
        )[:max_extra]
        for file_id in ordered:
            if file_id in direct:
                why = "; ".join(
                    f"{relation} {names(direct[file_id][relation])}"
                    for relation in _RELATION_ORDER
                    if relation in direct[file_id]
                )
            else:
                why = f"via {names(via[file_id])}"
            parts.append(f"{_file_line(graph, file_id)}\n  *({why})*")

    if len(depth_of) > max_extra:
        parts.append("")
        hidden = len(depth_of) - max_extra
        parts.append(f"*({hidden} more files not shown.)*")

    parts.append("")
    parts.append("---")
    parts.append(
        "The files above form a *dependency cluster* — "
        "changing one likely requires changing its neighbors. "
        "Use this map to discover all files that may need edits."
    )

    return "\n".join(parts)
