"""Graph builder — enrich node text, construct MemGraph, render structure maps.

Works on the output of ``parse_tree()``. Adds enrichment (symbol names,
docstrings, string literals from source), constructs the in-memory graph
representation, and renders the compact markdown structure maps that agents
receive in their system prompt.
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
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
        if isinstance(nodes, dict):  # nodes was a dict keyed by id
            nodes = list(nodes)
        return cls(
            nodes=nodes,
            edges=data.get("edges", []),
            meta=data.get("meta", {}),
        )


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
        if not fp.exists():
            return node
        try:
            source = fp.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return node
        mod = ast.parse(source, filename=str(fp))
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
        if not fp.exists():
            return node
        try:
            source = fp.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return node
        mod = ast.parse(source, filename=str(fp))
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
    """Find a named symbol (func or class) in an AST module."""
    parts = qualname.split(".")
    for node in ast.walk(tree_node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == parts[-1]:
                return node
    return None


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
    from collections import deque
    queue = deque((a, 0) for a in anchors)
    reach: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    while queue:
        current, depth = queue.popleft()
        if depth >= hops:
            continue
        for nbr, kind, direction in graph.neighbors(current):
            if not nbr.startswith("file:"):
                continue
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
            via = sorted({s[1] for s in sources})  # via which nodes
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
