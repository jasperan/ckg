"""AST-based code graph parser.

Walks a Python source tree with Python's built-in ``ast`` module to extract:

- File nodes and symbol nodes (top-level functions, classes, methods)
- Import edges (file → file)
- Call edges (symbol → symbol) resolved against the symbol table
- Co-edit edges (file ↔ file) from git log evidence

The output is a deterministic {nodes, edges} dict suitable for storage in
Oracle PGQ or in-memory graph operations.

Edge types:
  import   — module A imports module B (directed)
  call     — function A calls function B (directed)
  co_edit  — file A and file B changed in the same commit (undirected)
  contains — file contains a top-level symbol (directed, file → sym)
"""

from __future__ import annotations

import ast
import subprocess
from collections import defaultdict
from pathlib import Path


def _iter_py_files(tree: Path) -> list[Path]:
    """Walk tree for .py files, skipping __pycache__ and .venv."""
    return sorted(
        p for p in tree.rglob("*.py")
        if "__pycache__" not in p.parts and ".venv" not in p.parts
    )


def _module_name(tree: Path, path: Path, pkg_root: str) -> str:
    """Dotted module name as imports would reference it."""
    rel = path.relative_to(tree).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join([pkg_root, *parts]) if parts else pkg_root


def _rel(tree: Path, path: Path) -> str:
    return path.relative_to(tree).as_posix()


def _top_level_symbols(tree_node: ast.AST) -> list[tuple[str, ast.AST]]:
    """Top-level functions, async functions, classes, and one level of methods."""
    out: list[tuple[str, ast.AST]] = []
    for node in tree_node.body:  # type: ignore[attr-defined]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((node.name, node))
        elif isinstance(node, ast.ClassDef):
            out.append((node.name, node))
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append((f"{node.name}.{sub.name}", sub))
    return out


def _resolve_call_target(name: str, file_rel: str, sym_by_name: dict,
                         mod_targets: dict) -> set[str]:
    """Resolve a called name to possible symbol targets.

    If the name is a local name in the current file's symbol table, prefer it.
    Otherwise, look for any symbol with that short name globally.
    """
    candidates: set[str] = set()
    local = f"sym:{file_rel}::{name}"
    if local in sym_by_name:
        candidates.add(local)
    # Also check global symbol table for the same short name
    if name in mod_targets:
        for sid in mod_targets[name]:
            if sid != local:
                candidates.add(sid)
    return candidates


def _detect_co_edits(tree: Path, max_commits: int = 500) -> set[tuple[str, str]]:
    """Parse git log for file pairs changed in the same commit.

    Returns undirected pairs of relative file paths.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(tree), "log", f"--max-count={max_commits}",
             "--name-only", "--pretty=format:", "--", "*.py"],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return set()

    co_edits: set[tuple[str, str]] = set()
    for block in result.stdout.strip().split("\n\n"):
        paths_in_commit = []
        for line in block.strip().split("\n"):
            p = Path(line.strip())
            # Only include files that exist in our parsed tree
            if (tree / p).exists():
                rel = p.as_posix()
                if rel not in paths_in_commit:
                    paths_in_commit.append(rel)
        # All pairs in the same commit → co_edit edge
        for i in range(len(paths_in_commit)):
            for j in range(i + 1, len(paths_in_commit)):
                a, b = paths_in_commit[i], paths_in_commit[j]
                # Sort to make undirected; treat as (a, b) for consistency
                if a != b:
                    co_edits.add((a, b) if a < b else (b, a))
    return co_edits


def parse_tree(tree: Path, *, pkg_root: str) -> dict:
    """Parse a Python source tree into a code graph.

    Args:
        tree: Path to the root of a Python package (contains .py files).
        pkg_root: Top-level package name (e.g., "agent_harness", "ckg").

    Returns:
        dict with keys:
          - nodes: dict of {node_id: {id, kind, text}}
          - edges: list of {src, dst, kind}
          - meta: {n_files, n_nodes, n_edges, pkg_root}

    Raises:
        FileNotFoundError: if tree is not a directory.
    """
    tree = Path(tree)
    if not tree.is_dir():
        raise FileNotFoundError(
            f"Source tree not found: {tree}. "
            f"Point parse_tree() at a real Python package directory."
        )

    files = _iter_py_files(tree)
    mod_to_file = {_module_name(tree, f, pkg_root): _rel(tree, f) for f in files}

    nodes: dict[str, dict] = {}
    import_edges: set[tuple[str, str]] = set()
    call_edges: set[tuple[str, str]] = set()
    # symbol short-name → set of full sym ids (for resolving call targets)
    mod_targets: dict[str, set[str]] = defaultdict(set)
    # per-file symbol list (two-pass: collect, then resolve calls)
    file_syms: dict[str, list[tuple[str, ast.AST]]] = {}

    # ---- pass 1 — nodes + imports + symbol table --------------------------
    for f in files:
        rel = _rel(tree, f)
        nodes[f"file:{rel}"] = {
            "id": f"file:{rel}",
            "kind": "file",
            "text": f"Module {rel} in {pkg_root}.",
        }
        try:
            mod = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        except (SyntaxError, UnicodeDecodeError):
            file_syms[rel] = []
            continue

        cur_mod = _module_name(tree, f, pkg_root)
        syms = _top_level_symbols(mod)
        file_syms[rel] = syms
        for qual, _node in syms:
            sid = f"sym:{rel}::{qual}"
            short = qual.split(".")[-1]
            nodes[sid] = {
                "id": sid,
                "kind": "sym",
                "text": f"{short} in {rel} ({pkg_root}).",
            }
            mod_targets[short].add(sid)

        # imports → resolve internal targets to file: nodes
        for imp in ast.walk(mod):
            targets: list[str] = []
            if isinstance(imp, ast.Import):
                targets = [a.name for a in imp.names]
            elif isinstance(imp, ast.ImportFrom):
                if imp.module is None:
                    continue
                # `from X import Y` → the import edge is to module X
                if imp.module in mod_to_file:
                    target_rel = mod_to_file[imp.module]
                    if rel != target_rel:
                        import_edges.add((rel, target_rel))
                continue  # don't process individual names
            for target in targets:
                if target in mod_to_file:
                    target_rel = mod_to_file[target]
                    if rel != target_rel:
                        import_edges.add((rel, target_rel))

    # ---- pass 2 — call edges (resolve short names against symbol table) ----
    for f_rel, syms in file_syms.items():
        for _qual, node in syms:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    name: str | None = None
                    if isinstance(sub.func, ast.Name):
                        name = sub.func.id
                    elif isinstance(sub.func, ast.Attribute):
                        name = sub.func.attr
                    if name is None:
                        continue
                    caller_sid = f"sym:{f_rel}::_callee_lookup"
                    targets = _resolve_call_target(name, f_rel, {}, mod_targets)
                    if not targets:
                        for t_rel in file_syms:
                            local = f"sym:{t_rel}::{name}"
                            if local in nodes:
                                targets.add(local)
                    for target in targets:
                        call_edges.add((f"sym:{f_rel}::{name}", target))

    # ---- co-edit edges -----------------------------------------------------
    co_edit_edges = _detect_co_edits(tree)

    # ---- assemble output ---------------------------------------------------
    edges: list[dict] = []

    # contains edges (file → sym)
    for sid, info in nodes.items():
        if sid.startswith("sym:"):
            parts = sid[4:].split("::", 1)
            if len(parts) == 2:
                file_id = f"file:{parts[0]}"
                if file_id in nodes:
                    edges.append({"src": file_id, "dst": sid, "kind": "contains"})

    # import edges
    for src, dst in sorted(import_edges):
        edges.append({"src": f"file:{src}", "dst": f"file:{dst}", "kind": "import"})

    # call edges
    for src, dst in sorted(call_edges):
        edges.append({"src": src, "dst": dst, "kind": "call"})

    # co_edit edges
    for a, b in sorted(co_edit_edges):
        edges.append({"src": f"file:{a}", "dst": f"file:{b}", "kind": "co_edit"})

    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "n_files": len(files),
            "n_nodes": len(nodes),
            "n_edges": len(edges),
            "pkg_root": pkg_root,
        },
    }
