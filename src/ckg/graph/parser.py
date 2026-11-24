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


_SKIP_DIRS = frozenset({
    "__pycache__", ".git", ".venv", "venv", ".tox", "node_modules",
    "site-packages", ".ckg",
})


def _iter_py_files(tree: Path) -> list[Path]:
    """Walk tree for .py files, skipping caches, VCS data and virtualenvs.

    Only path components *below* ``tree`` are checked, so a tree that happens
    to live under e.g. ``~/venv/`` is still parsed.
    """
    return sorted(
        p for p in tree.rglob("*.py")
        if not _SKIP_DIRS.intersection(p.relative_to(tree).parts[:-1])
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


def _package_module_name(tree: Path, path: Path) -> str | None:
    """Dotted module name as Python would import it from a source root.

    Walks up from the file while the containing directory is a package (has
    ``__init__.py``), stopping at ``tree``. This resolves ``src/pkg/mod.py``
    to ``pkg.mod`` even when ``tree`` is the project root rather than the
    package directory itself.
    """
    rel = path.relative_to(tree).with_suffix("")
    parts = list(rel.parts)
    is_init = parts[-1] == "__init__"
    pkg_dirs = parts[:-1]
    keep = 0
    for i in range(len(pkg_dirs) - 1, -1, -1):
        if not (tree.joinpath(*pkg_dirs[: i + 1]) / "__init__.py").is_file():
            break
        keep += 1
    mod_parts = pkg_dirs[len(pkg_dirs) - keep:]
    if not is_init:
        mod_parts = [*mod_parts, parts[-1]]
    return ".".join(mod_parts) if mod_parts else None


def _resolve_relative(base_names: list[str], is_pkg: bool, module: str | None,
                      level: int) -> list[str]:
    """Absolute module candidates for ``from <.*level><module> import ...``."""
    out: list[str] = []
    for name in base_names:
        parts = name.split(".")
        if not is_pkg:
            parts = parts[:-1]
        drop = level - 1
        if drop > len(parts):
            continue
        parts = parts[: len(parts) - drop]
        if module:
            parts = [*parts, *module.split(".")]
        if parts:
            out.append(".".join(parts))
    return out


def _calls_in(node: ast.AST) -> list[str]:
    """Names called within a symbol's own body.

    A class does not own the calls made inside its methods: those belong to
    the method symbols, which are separate nodes.
    """
    names: list[str] = []
    stack: list[ast.AST] = [node]
    while stack:
        cur = stack.pop()
        for child in ast.iter_child_nodes(cur):
            if isinstance(node, ast.ClassDef) and cur is node and isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                continue
            if isinstance(child, ast.Call):
                if isinstance(child.func, ast.Name):
                    names.append(child.func.id)
                elif isinstance(child.func, ast.Attribute):
                    names.append(child.func.attr)
            stack.append(child)
    return names


def _resolve_call_target(name: str, file_rel: str, mod_targets: dict) -> set[str]:
    """Resolve a called name to possible symbol targets.

    A symbol with that short name in the calling file wins; otherwise every
    symbol with that short name across the tree is a candidate.
    """
    candidates = mod_targets.get(name, ())
    prefix = f"sym:{file_rel}::"
    local = {sid for sid in candidates if sid.startswith(prefix)}
    return local or set(candidates)


def _detect_co_edits(
    tree: Path,
    max_commits: int = 500,
    *,
    known_files: set[str] | None = None,
    max_files_per_commit: int = 100,
) -> set[tuple[str, str]]:
    """Parse git log for file pairs changed in the same commit.

    Returns undirected pairs of paths relative to ``tree`` (so they line up
    with the file nodes even when ``tree`` is a sub-directory of the repo).
    Commits touching more than ``max_files_per_commit`` files (bulk renames,
    initial imports) carry no co-change signal and would add O(n^2) edges, so
    they are skipped. When ``known_files`` is given, only those paths are
    considered.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(tree), "log", f"--max-count={max_commits}",
             "--relative", "--name-only", "--pretty=format:", "--", "*.py"],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return set()
    if result.returncode != 0:
        return set()

    co_edits: set[tuple[str, str]] = set()
    for block in result.stdout.strip().split("\n\n"):
        paths_in_commit: list[str] = []
        for line in block.strip().split("\n"):
            rel = Path(line.strip()).as_posix() if line.strip() else ""
            if not rel or rel in paths_in_commit:
                continue
            # Only include files that exist in our parsed tree
            if known_files is not None:
                if rel not in known_files:
                    continue
            elif not (tree / rel).exists():
                continue
            paths_in_commit.append(rel)
        if len(paths_in_commit) > max_files_per_commit:
            continue
        # All pairs in the same commit → co_edit edge
        for i in range(len(paths_in_commit)):
            for j in range(i + 1, len(paths_in_commit)):
                a, b = paths_in_commit[i], paths_in_commit[j]
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
    # Module name → file. The pkg_root-prefixed name (tree *is* the package)
    # takes precedence; package-aware names (``src/pkg/mod.py`` → ``pkg.mod``)
    # are added so absolute imports resolve when ``tree`` is a project root.
    mod_to_file = {_module_name(tree, f, pkg_root): _rel(tree, f) for f in files}
    file_mod_names: dict[str, list[str]] = {
        _rel(tree, f): [_module_name(tree, f, pkg_root)] for f in files
    }
    for f in files:
        alias = _package_module_name(tree, f)
        if alias and alias not in mod_to_file:
            mod_to_file[alias] = _rel(tree, f)
        if alias and alias not in file_mod_names[_rel(tree, f)]:
            file_mod_names[_rel(tree, f)].append(alias)

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
        except (SyntaxError, ValueError, UnicodeDecodeError, OSError):
            file_syms[rel] = []
            continue

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
        is_pkg = f.name == "__init__.py"
        for imp in ast.walk(mod):
            modules: list[str] = []
            if isinstance(imp, ast.Import):
                modules = [a.name for a in imp.names]
            elif isinstance(imp, ast.ImportFrom):
                if imp.level:
                    bases = _resolve_relative(
                        file_mod_names[rel], is_pkg, imp.module, imp.level
                    )
                else:
                    bases = [imp.module] if imp.module else []
                # `from X import Y` → the import edge is to module X, plus to
                # module X.Y when Y is itself a module of the tree.
                for base in bases:
                    modules.append(base)
                    modules.extend(f"{base}.{a.name}" for a in imp.names)
            for target in modules:
                target_rel = mod_to_file.get(target)
                if target_rel is not None and rel != target_rel:
                    import_edges.add((rel, target_rel))

    # ---- pass 2 — call edges (resolve short names against symbol table) ----
    for f_rel, syms in file_syms.items():
        for qual, node in syms:
            caller = f"sym:{f_rel}::{qual}"
            for name in _calls_in(node):
                for target in _resolve_call_target(name, f_rel, mod_targets):
                    if target != caller:
                        call_edges.add((caller, target))

    # ---- co-edit edges -----------------------------------------------------
    co_edit_edges = _detect_co_edits(tree, known_files=set(file_syms))

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
