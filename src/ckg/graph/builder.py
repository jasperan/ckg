"""Graph builder — enrich node text, construct MemGraph, render structure maps.

Works on the output of ``parse_tree()``. Adds enrichment (symbol names,
docstrings, string literals from source), constructs the in-memory graph
representation, and renders the compact markdown structure maps that agents
receive in their system prompt.
"""

from __future__ import annotations

import ast
from functools import lru_cache
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path


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
            # File-level adjacency (undirected for co_edit)
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
    def from_dict(cls, data: dict) -> "CodeGraph":
        nodes = list(data.get("nodes", {}).values())
        return cls(
            nodes=nodes,
            edges=data.get("edges", []),
            meta=data.get("meta", {}),
        )


@lru_cache(maxsize=64)
def _parse_source(path: str, mtime_ns: int) -> ast.Module | None:
    """Parse a file once for all of its nodes; None when unreadable/invalid."""
    try:
        return ast.parse(Path(path).read_text(encoding="utf-8"), filename=path)
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
        return None


def _load_module(fp: Path) -> ast.Module | None:
    try:
        return _parse_source(str(fp), fp.stat().st_mtime_ns)
    except OSError:
        return None


def build_graph(tree: Path, *, pkg_root: str) -> dict:
    """Parse ``tree`` and enrich every node's text from source.

    The single entry point used by ``ckg build``/``ckg load`` and the plugin's
    on-demand cache build, so all graph caches carry the same node text.
    """
    from ckg.graph.parser import parse_tree

    graph = parse_tree(tree, pkg_root=pkg_root)
    for node in graph["nodes"].values():
        enrich_node_text(Path(tree), node, pkg_root)
    return graph


def enrich_node_text(tree: Path, node: dict, pkg_root: str) -> dict:
    """Enrich a node's text with source-code evidence.

    For file nodes: first docstring, key imports, module-level constants.
    For symbol nodes: signature, docstring first line.
    """
    nid = node["id"]
    kind = node.get("kind", "")

    if kind == "file":
        file_rel = nid.removeprefix("file:")
        fp = tree / file_rel
        mod = _load_module(fp)
        if mod is None:
            return node
        extras: list[str] = []
        # Docstring
        if (isinstance(mod, ast.Module) and mod.body
                and isinstance(mod.body[0], ast.Expr)
                and isinstance(mod.body[0].value, ast.Constant)
                and isinstance(mod.body[0].value.value, str)):
            doc = mod.body[0].value.value.strip().split("\n")[0]
            extras.append(f"doc: {doc}")
        # Key string literals (flags, config keys)
        strings: set[str] = set()
        for node_obj in ast.walk(mod):
            if isinstance(node_obj, ast.Constant) and isinstance(node_obj.value, str):
                s = node_obj.value.strip()
                if 2 < len(s) < 80 and not s.startswith("#"):
                    strings.add(s)
        if strings:
            top = sorted(strings, key=len, reverse=True)[:10]
            extras.append(f"strings: {', '.join(top)}")
        node["text"] = f"{node['text']} {'; '.join(extras)}".strip()

    elif kind == "sym":
        # symbol: "sym:relpath::qualname"
        parts = nid.removeprefix("sym:").split("::", 1)
        if len(parts) != 2:
            return node
        file_rel, qualname = parts
        fp = tree / file_rel
        mod = _load_module(fp)
        if mod is None:
            return node
        # Walk to find the function/class
        sym_node = _find_symbol(mod, qualname)
        if sym_node is None:
            return node
        extras: list[str] = []
        if isinstance(sym_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Signature
            args = [a.arg for a in sym_node.args.args]
            extras.append(f"def {sym_node.name}({', '.join(args)})")
            # Docstring
            if (sym_node.body and isinstance(sym_node.body[0], ast.Expr)
                    and isinstance(sym_node.body[0].value, ast.Constant)
                    and isinstance(sym_node.body[0].value.value, str)):
                doc = sym_node.body[0].value.value.strip().split("\n")[0]
                extras.append(f"doc: {doc}")
        elif isinstance(sym_node, ast.ClassDef):
            extras.append(f"class {sym_node.name}")
        node["text"] = " ".join([node["text"]] + extras)

    return node


def _find_symbol(tree_node: ast.AST, qualname: str) -> ast.AST | None:
    """Find a top-level function/class or ``Class.method`` in an AST module."""
    scope = getattr(tree_node, "body", [])
    found: ast.AST | None = None
    for part in qualname.split("."):
        found = next(
            (n for n in scope
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
             and n.name == part),
            None,
        )
        if found is None:
            return None
        scope = getattr(found, "body", [])
    return found


def _file_reach(graph: CodeGraph, file_id: str) -> list[tuple[str, str]]:
    """(neighbor_file_id, edge_kind) for a file, lifting symbol call edges.

    Call edges connect symbols, so a call from a symbol in file A to a symbol
    in file B (in either direction) counts as an A–B connection.
    """
    out: list[tuple[str, str]] = []
    for nbr, kind, _direction in graph.neighbors(file_id):
        if nbr.startswith("file:"):
            out.append((nbr, kind))
        elif kind == "contains":
            for sym_nbr, sym_kind, _d in graph.neighbors(nbr):
                if sym_kind != "call" or not sym_nbr.startswith("sym:"):
                    continue
                other = "file:" + sym_nbr.removeprefix("sym:").split("::", 1)[0]
                if other != file_id:
                    out.append((other, "call"))
    return out


_KIND_LABEL: dict[str, str] = {
    "import": "imports",
    "call": "calls",
    "co_edit": "co-edited with",
    "contains": "contains",
}


def render_structure_map(
    anchors: list[str],
    graph: CodeGraph,
    *,
    hops: int = 2,
    max_extra: int = 15,
    query: str = "",
) -> str:
    """Render a compact markdown structure map for agent injection.

    Args:
        anchors: Top file anchor IDs (e.g., ["file:x.py", "file:y.py"]).
        graph: The parsed code graph.
        hops: How many hops of graph reach to include.
        max_extra: Maximum extra files to include beyond anchors.
        query: The retrieval query (for the preamble).

    Returns:
        A markdown string suitable for appending to a system prompt.
    """
    graph._ensure_index()
    anchor_set = set(anchors)
    seen: set[str] = set(anchors)

    # BFS from anchors
    queue = deque((a, 0) for a in anchors)
    reach: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    while queue:
        current, depth = queue.popleft()
        if depth >= hops:
            continue
        for nbr, kind in _file_reach(graph, current):
            if nbr in anchor_set:
                continue
            if nbr not in seen:
                seen.add(nbr)
                queue.append((nbr, depth + 1))
            reach[nbr].append((current, kind, depth + 1))

    # Build markdown
    parts: list[str] = [
        "## Code Structure Map",
        "",
        f"**Query**: {query}" if query else "**Query**: (feature task)",
        "",
        "### Anchor Files (lexical match)",
        "",
    ]
    for a in anchors:
        node = graph.node(a)
        rel = a.removeprefix("file:")
        label = node["text"] if node else rel
        parts.append(f"- `{rel}` — {label}")

    parts.append("")
    parts.append("### Dependency Reach (2-hop imports, calls, co-edits)")
    parts.append("")

    if not reach:
        parts.append("*(No additional files found in the graph neighborhood.)*")
    else:
        # Sort by depth then alphabetically
        ordered = sorted(
            reach.items(),
            key=lambda kv: (min(d for _, _, d in kv[1]), kv[0]),
        )[:max_extra]
        for file_id, sources in ordered:
            rel = file_id.removeprefix("file:")
            node = graph.node(file_id)
            label = node["text"] if node else rel
            kinds = sorted({_KIND_LABEL.get(k, k) for _, k, _ in sources})
            via = sorted({src for src, _, _ in sources})  # via which files
            via_display = ", ".join(v.removeprefix("file:") for v in via[:3])
            parts.append(
                f"- `{rel}` — {label}\n"
                f"  *(via {via_display}, {', '.join(kinds)})*"
            )

    if len(reach) > max_extra:
        parts.append("")
        hidden = len(reach) - max_extra
        parts.append(f"*({hidden} more files not shown.)*")

    parts.append("")
    parts.append("---")
    parts.append(
        "The files above form a *dependency cluster* — "
        "changing one likely requires changing its neighbors. "
        "Use this map to discover all files that may need edits."
    )

    return "\n".join(parts)
