"""AST-based code graph parser.

Walks a Python source tree with the standard ``ast`` module and extracts:

- File nodes and symbol nodes (top-level functions, classes, methods), each
  with a short descriptive text (signature, first docstring line)
- Import edges (file → file)
- Call edges (symbol → symbol), resolved through each file's imports
- Co-edit edges (file ↔ file) from git history, weighted by how often the
  pair changed together

Edge kinds:
  contains — file contains a symbol (file → sym)
  import   — module A imports module B
  call     — symbol A calls symbol B
  co_edit  — two files changed in the same commit (stored once, a < b;
             ``weight`` is the number of such commits)

Call resolution is deliberately conservative. A call becomes an edge only
when its target can be named: a symbol bound in the calling file (defined
there or imported), a method reached through ``self``/``super()``, an
attribute of an imported module, or a method on a value whose class is
visible from a constructor call or annotation. A method call on a receiver of
unknown type is linked only when exactly one class reachable from the calling
file defines that method. Matching bare names across the whole tree is never
done: on real projects it links every ``x.get()`` to every ``get``.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from importlib.util import decode_source
from pathlib import Path, PurePosixPath

SCHEMA_VERSION = 2

_SKIP_DIRS = frozenset({
    "__pycache__", ".git", ".hg", ".svn", ".venv", "venv", ".tox", ".nox",
    "node_modules", "site-packages", ".ckg", ".eggs", ".mypy_cache",
    ".pytest_cache", ".ruff_cache",
})
# Build output of setuptools/poetry: only at the top of the tree, where a real
# package of that name is not expected.
_SKIP_TOP_DIRS = frozenset({"build", "dist"})
_MAX_FILE_BYTES = 1_000_000
_TEXT_MAX = 200

# Methods of builtin containers, strings and files. A call such as ``x.get()``
# on a receiver of unknown type is far more likely to be one of these than a
# project method of the same name.
_BUILTIN_METHODS = frozenset(
    name
    for typ in (dict, list, str, bytes, bytearray, set, frozenset, tuple, int, float)
    for name in dir(typ)
    if not name.startswith("_")
) | {"read", "write", "close", "flush", "seek", "tell", "readline", "readlines", "writelines"}

_DOTTED_RE = re.compile(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*\Z")
_FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


# --------------------------------------------------------------------------- #
# File discovery
# --------------------------------------------------------------------------- #

def _skipped(parts: tuple[str, ...]) -> bool:
    dirs = parts[:-1]
    return bool(_SKIP_DIRS.intersection(dirs)) or bool(dirs and dirs[0] in _SKIP_TOP_DIRS)


def _git_py_files(tree: Path) -> list[str] | None:
    """Tracked and untracked-but-not-ignored ``.py`` paths, or None outside git."""
    try:
        result = subprocess.run(
            ["git", "-C", str(tree), "ls-files", "-z", "--cached", "--others",
             "--exclude-standard", "--", "*.py"],
            capture_output=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return [os.fsdecode(p) for p in result.stdout.split(b"\0") if p]


def _walk_py_files(tree: Path) -> list[str]:
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(tree):
        at_top = Path(dirpath) == tree
        dirnames[:] = [
            d for d in dirnames
            if d not in _SKIP_DIRS
            and not (at_top and d in _SKIP_TOP_DIRS)
            and not os.path.exists(os.path.join(dirpath, d, "pyvenv.cfg"))
        ]
        for name in filenames:
            if name.endswith(".py"):
                out.append(Path(dirpath, name).relative_to(tree).as_posix())
    return out


def _iter_py_files(tree: Path) -> list[Path]:
    """Python source files of the project, sorted.

    Inside a git work tree the file list comes from git, so ``.gitignore`` is
    honoured; elsewhere the tree is walked. Either way caches, virtualenvs and
    build output are skipped (only path components *below* ``tree`` count, so
    a tree that lives under e.g. ``~/venv/`` is still parsed), and symlinks
    pointing outside the tree are not followed.
    """
    rels = _git_py_files(tree) or _walk_py_files(tree)
    root = tree.resolve()
    out: list[Path] = []
    for rel in sorted(set(rels)):
        if _skipped(PurePosixPath(rel).parts):
            continue
        path = tree / rel
        try:
            if path.is_symlink() and not path.resolve().is_relative_to(root):
                continue
            if path.is_file():
                out.append(path)
        except OSError:
            continue
    return out


def _rel(tree: Path, path: Path) -> str:
    return path.relative_to(tree).as_posix()


def read_module(path: Path) -> ast.Module | None:
    """Parse a source file honouring BOMs and PEP 263; None when unusable."""
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return None
        return ast.parse(decode_source(path.read_bytes()), filename=str(path))
    except (OSError, SyntaxError, ValueError, LookupError, RecursionError):
        return None


# --------------------------------------------------------------------------- #
# Per-file facts (collected in one pass; the AST is not kept)
# --------------------------------------------------------------------------- #

@dataclass(slots=True)
class _Sym:
    qual: str
    is_class: bool
    text: str
    owner: str | None = None  # class qualname, for methods
    # ("n", name) | ("a", receiver-chain-or-None, attr) | ("s", attr) for super()
    calls: set[tuple] = field(default_factory=set)
    local_types: dict[str, str] = field(default_factory=dict)
    bases: list[str] = field(default_factory=list)
    attr_types: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class _File:
    rel: str
    text: str
    syms: dict[str, _Sym] = field(default_factory=dict)
    modules: dict[str, str] = field(default_factory=dict)  # local (dotted) name → file
    names: dict[str, tuple[str, str]] = field(default_factory=dict)  # name → (file, export)
    stars: list[str] = field(default_factory=list)
    external: set[str] = field(default_factory=set)
    imports: set[str] = field(default_factory=set)


def _first_doc_line(node: ast.AST) -> str:
    try:
        doc = ast.get_docstring(node, clean=False)  # type: ignore[arg-type]
    except TypeError:
        return ""
    if not doc:
        return ""
    return " ".join(doc.strip().split("\n", 1)[0].split())[:_TEXT_MAX]


def _signature(node: ast.AST) -> str:
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(ast.unparse(b) for b in node.bases)
        return f"class {node.name}({bases})" if bases else f"class {node.name}"
    assert isinstance(node, _FUNC_TYPES)
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    sig = f"{prefix} {node.name}({ast.unparse(node.args)})"
    if node.returns is not None:
        sig += f" -> {ast.unparse(node.returns)}"
    return " ".join(sig.split())[:_TEXT_MAX]


def _symbol_text(node: ast.AST) -> str:
    doc = _first_doc_line(node)
    sig = _signature(node)
    return f"{sig}. {doc}" if doc else sig


def _dotted(expr: ast.AST) -> str | None:
    """``a.b.c`` for a pure name/attribute chain, else None."""
    parts: list[str] = []
    while isinstance(expr, ast.Attribute):
        parts.append(expr.attr)
        expr = expr.value
    if not isinstance(expr, ast.Name):
        return None
    parts.append(expr.id)
    return ".".join(reversed(parts))


def _type_ref(expr: ast.AST | None) -> str | None:
    """Class named by an annotation: ``Foo``, ``m.Foo``, ``"Foo"``, ``Foo | None``,
    ``Optional[Foo]``."""
    if expr is None:
        return None
    if isinstance(expr, ast.Constant):
        value = expr.value
        return value if isinstance(value, str) and _DOTTED_RE.match(value) else None
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.BitOr):
        return _type_ref(expr.left) or _type_ref(expr.right)
    if isinstance(expr, ast.Subscript):
        outer = _dotted(expr.value)
        if outer and outer.rsplit(".", 1)[-1] == "Optional":
            return _type_ref(expr.slice)
        return None
    return _dotted(expr)


def _call_ref(func: ast.AST) -> tuple | None:
    if isinstance(func, ast.Name):
        return ("n", func.id)
    if isinstance(func, ast.Attribute):
        recv = func.value
        if (isinstance(recv, ast.Call) and isinstance(recv.func, ast.Name)
                and recv.func.id == "super"):
            return ("s", func.attr)
        return ("a", _dotted(recv), func.attr)
    return None


def _collect_body(node: ast.AST, sym: _Sym, self_attrs: dict[str, str] | None) -> None:
    """Record the calls and visible value types inside one symbol.

    A class does not own what happens inside its methods: those are separate
    symbols. ``self_attrs`` (methods only) receives ``self.x`` types for the
    owning class.
    """
    if isinstance(node, _FUNC_TYPES):
        args = node.args
        for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
            annotated = _type_ref(arg.annotation)
            if annotated:
                sym.local_types[arg.arg] = annotated
    for deco in getattr(node, "decorator_list", ()):
        if not isinstance(deco, ast.Call):
            call = _call_ref(deco)
            if call:
                sym.calls.add(call)

    stack: list[ast.AST] = [node]
    while stack:
        cur = stack.pop()
        for child in ast.iter_child_nodes(cur):
            if sym.is_class and cur is node and isinstance(child, _FUNC_TYPES):
                continue
            if isinstance(child, ast.Call):
                call = _call_ref(child.func)
                if call:
                    sym.calls.add(call)
            elif isinstance(child, (ast.Assign, ast.AnnAssign)):
                _collect_assignment(child, sym, self_attrs)
            elif isinstance(child, ast.withitem):
                target = child.optional_vars
                if isinstance(target, ast.Name) and isinstance(child.context_expr, ast.Call):
                    opened = _dotted(child.context_expr.func)
                    if opened:
                        sym.local_types[target.id] = opened
            stack.append(child)


def _collect_assignment(
    stmt: ast.Assign | ast.AnnAssign, sym: _Sym, self_attrs: dict[str, str] | None
) -> None:
    if isinstance(stmt, ast.AnnAssign):
        targets: list[ast.expr] = [stmt.target]
        ref = _type_ref(stmt.annotation)
    else:
        targets = stmt.targets
        ref = None
    value = stmt.value
    if ref is None and isinstance(value, ast.Call):
        ref = _dotted(value.func)
    if ref is None and isinstance(value, ast.Name):
        ref = sym.local_types.get(value.id)
    if ref is None:
        return
    for target in targets:
        if isinstance(target, ast.Name):
            if sym.is_class:
                sym.attr_types.setdefault(target.id, ref)
            else:
                sym.local_types[target.id] = ref
        elif (self_attrs is not None and isinstance(target, ast.Attribute)
              and isinstance(target.value, ast.Name) and target.value.id == "self"):
            self_attrs.setdefault(target.attr, ref)


# --------------------------------------------------------------------------- #
# Module resolution
# --------------------------------------------------------------------------- #

class _ModuleIndex:
    """Maps import statements to files of the tree.

    An absolute import ``a.b.c`` is tried against the plausible source roots
    (the tree, ``src/``, parents of top-level packages), then against the tree
    being the package ``pkg_root`` itself, then next to the importing file,
    and finally as a path suffix when exactly one file matches.
    """

    def __init__(self, rels: list[str], pkg_root: str) -> None:
        self.pkg_root = pkg_root
        self.by_parts: dict[tuple[str, ...], str] = {}
        suffixes: dict[tuple[str, ...], list[str]] = {}
        package_dirs: set[tuple[str, ...]] = set()
        for rel in rels:
            parts = PurePosixPath(rel).with_suffix("").parts
            if parts[-1] == "__init__":
                parts = parts[:-1]
                package_dirs.add(parts)
            self.by_parts.setdefault(parts, rel)
            for i in range(1, len(parts)):
                suffixes.setdefault(parts[i:], []).append(rel)
        self.unique_suffix = {
            parts: found[0] for parts, found in suffixes.items()
            if len(found) == 1 and len(parts) >= 2
        }
        roots = {(), ("src",)}
        roots.update(p[:-1] for p in package_dirs if p and p[:-1] not in package_dirs)
        self.roots = sorted(roots, key=lambda r: (len(r), r))

    def absolute(self, parts: tuple[str, ...], importer: str) -> str | None:
        for root in self.roots:
            found = self.by_parts.get(root + parts)
            if found is not None:
                return found
        if parts[0] == self.pkg_root:
            found = self.by_parts.get(parts[1:])
            if found is not None:
                return found
        here = PurePosixPath(importer).parent.parts
        found = self.by_parts.get(here + parts)
        if found is not None:
            return found
        return self.unique_suffix.get(parts)

    def relative_base(self, importer: str, level: int) -> tuple[str, ...] | None:
        """Package directory a ``from .`` import is relative to, or None when
        it climbs out of the package."""
        here = PurePosixPath(importer).parent.parts
        up = level - 1
        if up > len(here):
            return None
        base = here[: len(here) - up]
        if up and base not in self.by_parts:
            return None
        return base


def _collect_imports(mod: ast.Module, info: _File, index: _ModuleIndex) -> None:
    def bind_module(name: str, target: str) -> None:
        info.modules[name] = target
        if target != info.rel:
            info.imports.add(target)

    for node in ast.walk(mod):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = tuple(alias.name.split("."))
                target = index.absolute(parts, info.rel)
                if target is None:
                    info.external.add(alias.asname or parts[0])
                    continue
                if alias.asname:
                    bind_module(alias.asname, target)
                    continue
                bind_module(alias.name, target)
                for i in range(1, len(parts)):
                    parent = index.absolute(parts[:i], info.rel)
                    if parent is not None:
                        info.modules.setdefault(".".join(parts[:i]), parent)
        elif isinstance(node, ast.ImportFrom):
            mod_parts = tuple(node.module.split(".")) if node.module else ()
            if node.level:
                base = index.relative_base(info.rel, node.level)
                if base is None:
                    continue
                full = base + mod_parts
                source = index.by_parts.get(full)

                def resolve(parts: tuple[str, ...]) -> str | None:
                    return index.by_parts.get(parts)
            else:
                if not mod_parts:
                    continue
                full = mod_parts
                source = index.absolute(full, info.rel)

                def resolve(parts: tuple[str, ...]) -> str | None:
                    return index.absolute(parts, info.rel)

            if source is not None and source != info.rel:
                info.imports.add(source)
            for alias in node.names:
                local = alias.asname or alias.name
                if alias.name == "*":
                    if source is not None:
                        info.stars.append(source)
                    continue
                submodule = resolve(full + (alias.name,))
                if submodule is not None:
                    bind_module(local, submodule)
                elif source is not None:
                    info.names[local] = (source, alias.name)
                else:
                    info.external.add(local)


# --------------------------------------------------------------------------- #
# Call resolution
# --------------------------------------------------------------------------- #

class _Resolver:
    def __init__(self, files: dict[str, _File]) -> None:
        self.files = files
        self._exports: dict[tuple[str, str], str | None] = {}
        self._methods: dict[str, dict[str, list[str]]] = {}
        self._reach: dict[str, list[str]] = {}
        for rel, info in files.items():
            by_name: dict[str, list[str]] = {}
            for qual, sym in info.syms.items():
                if sym.owner is not None:
                    by_name.setdefault(qual.rsplit(".", 1)[1], []).append(f"sym:{rel}::{qual}")
            self._methods[rel] = by_name

    def export(self, rel: str, name: str, depth: int = 0) -> str | None:
        """Symbol id that ``name`` refers to at module level of ``rel``."""
        key = (rel, name)
        if key in self._exports:
            return self._exports[key]
        info = self.files.get(rel)
        found: str | None = None
        if info is not None and depth <= 5:
            if name in info.syms:
                found = f"sym:{rel}::{name}"
            elif name in info.names:
                src, exported = info.names[name]
                found = self.export(src, exported, depth + 1)
            else:
                for star in info.stars:
                    found = self.export(star, name, depth + 1)
                    if found:
                        break
        if depth == 0:
            self._exports[key] = found
        return found

    def _sym(self, sid: str) -> tuple[_File, _Sym]:
        rel, qual = sid[4:].split("::", 1)
        info = self.files[rel]
        return info, info.syms[qual]

    def is_class(self, sid: str) -> bool:
        return self._sym(sid)[1].is_class

    def method(self, class_id: str, name: str, depth: int = 0) -> str | None:
        """``name`` on a class or the nearest base class defining it."""
        info, cls = self._sym(class_id)
        qual = f"{cls.qual}.{name}"
        if qual in info.syms:
            return f"sym:{info.rel}::{qual}"
        if depth >= 5:
            return None
        return self.inherited(class_id, name, depth)

    def inherited(self, class_id: str, name: str, depth: int = 0) -> str | None:
        info, cls = self._sym(class_id)
        for base in cls.bases:
            base_id = self.dotted(info, base)
            if base_id and base_id != class_id and self.is_class(base_id):
                found = self.method(base_id, name, depth + 1)
                if found:
                    return found
        return None

    def dotted(self, info: _File, ref: str) -> str | None:
        """Symbol named by ``Name``, ``module.name``, ``Class.method`` or
        ``module.Class.method`` as seen from ``info``."""
        parts = ref.split(".")
        for i in range(len(parts) - 1, 0, -1):
            module = info.modules.get(".".join(parts[:i]))
            if module is None:
                continue
            rest = parts[i:]
            head = self.export(module, rest[0])
            if head is None or len(rest) == 1:
                return head
            if len(rest) == 2 and self.is_class(head):
                return self.method(head, rest[1])
            return None
        head = self.export(info.rel, parts[0])
        if head is None or len(parts) == 1:
            return head
        if len(parts) == 2 and self.is_class(head):
            return self.method(head, parts[1])
        return None

    def _class_of(self, info: _File, ref: str | None) -> str | None:
        if not ref:
            return None
        sid = self.dotted(info, ref)
        return sid if sid and self.is_class(sid) else None

    def _unique_reachable_method(self, info: _File, name: str) -> str | None:
        """The one method called ``name`` among classes defined in or imported
        by this file; None when there is none or the choice is ambiguous."""
        if name in _BUILTIN_METHODS:
            return None
        reach = self._reach.get(info.rel)
        if reach is None:
            seen = {info.rel, *info.imports}
            for local in info.names:
                sid = self.export(info.rel, local)
                if sid:
                    seen.add(sid[4:].split("::", 1)[0])
            reach = self._reach[info.rel] = sorted(seen)
        found: list[str] = []
        for rel in reach:
            found.extend(self._methods.get(rel, {}).get(name, ()))
            if len(found) > 1:
                return None
        return found[0] if found else None

    def call_target(self, info: _File, sym: _Sym, ref: tuple) -> str | None:
        owner_id = f"sym:{info.rel}::{sym.owner}" if sym.owner else None
        if ref[0] == "n":
            return self.export(info.rel, ref[1])
        if ref[0] == "s":
            return self.inherited(owner_id, ref[1]) if owner_id else None

        _, chain, attr = ref
        if chain is None:
            return self._unique_reachable_method(info, attr)
        parts = chain.split(".")
        head = parts[0]
        if head in ("self", "cls") and owner_id:
            if len(parts) == 1:
                return self.method(owner_id, attr)
            if len(parts) == 2:
                attr_types = info.syms[sym.owner].attr_types  # type: ignore[index]
                if parts[1] in attr_types:
                    cls = self._class_of(info, attr_types[parts[1]])
                    return self.method(cls, attr) if cls else None
            return self._unique_reachable_method(info, attr)
        if len(parts) == 1 and head in sym.local_types:
            cls = self._class_of(info, sym.local_types[head])
            return self.method(cls, attr) if cls else None
        target = self.dotted(info, f"{chain}.{attr}")
        if target is not None:
            return target
        known = (
            head in info.external or head in info.modules or head in sym.local_types
            or self.export(info.rel, head) is not None
        )
        return None if known else self._unique_reachable_method(info, attr)


# --------------------------------------------------------------------------- #
# Git co-edit history
# --------------------------------------------------------------------------- #

def _detect_co_edits(
    tree: Path,
    max_commits: int = 500,
    *,
    known_files: set[str] | None = None,
    max_files_per_commit: int = 10,
) -> dict[tuple[str, str], int]:
    """File pairs changed in the same commit → number of such commits.

    Pairs are undirected (stored with ``a < b``) and relative to ``tree``, so
    they line up with the file nodes even when ``tree`` is a sub-directory of
    the repository. A commit touching more than ``max_files_per_commit`` files
    (bulk renames, formatting sweeps) and the root commit (the initial import)
    say nothing about which files belong together and would add O(n^2) pairs,
    so both are skipped. When ``known_files`` is given, only those paths count.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(tree), "-c", "core.quotePath=false", "log",
             f"--max-count={max_commits}", "--no-merges", "--relative",
             "--name-only", "--pretty=format:%x00%P", "--", "*.py"],
            capture_output=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, OSError):
        return {}
    if result.returncode != 0:
        return {}

    co_edits: Counter[tuple[str, str]] = Counter()
    for block in result.stdout.decode("utf-8", "surrogateescape").split("\0")[1:]:
        parents, _, names = block.partition("\n")
        if not parents.strip():
            continue
        paths: set[str] = set()
        for line in names.split("\n"):
            if not line or line.startswith('"'):
                continue
            if known_files is not None:
                if line not in known_files:
                    continue
            elif not (tree / line).exists():
                continue
            paths.add(line)
        if not 2 <= len(paths) <= max_files_per_commit:
            continue
        ordered = sorted(paths)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
                co_edits[(a, b)] += 1
    return dict(co_edits)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def _scan_file(path: Path, rel: str, index: _ModuleIndex) -> _File:
    mod = read_module(path)
    if mod is None:
        return _File(rel=rel, text=f"Python module {rel}")
    info = _File(rel=rel, text=_first_doc_line(mod) or f"Python module {rel}")
    _collect_imports(mod, info, index)

    for node in mod.body:
        if isinstance(node, _FUNC_TYPES):
            sym = _Sym(qual=node.name, is_class=False, text=_symbol_text(node))
            _collect_body(node, sym, None)
            info.syms[sym.qual] = sym
        elif isinstance(node, ast.ClassDef):
            cls = _Sym(qual=node.name, is_class=True, text=_symbol_text(node))
            cls.bases = [b for b in map(_dotted, node.bases) if b]
            _collect_body(node, cls, None)
            info.syms[cls.qual] = cls
            for sub in node.body:
                if isinstance(sub, _FUNC_TYPES):
                    method = _Sym(
                        qual=f"{node.name}.{sub.name}", is_class=False,
                        text=_symbol_text(sub), owner=node.name,
                    )
                    _collect_body(sub, method, cls.attr_types)
                    info.syms[method.qual] = method
    return info


def parse_tree(tree: Path, *, pkg_root: str) -> dict:
    """Parse a Python source tree into a code graph.

    Args:
        tree: Root of the project or package to parse.
        pkg_root: Import name of ``tree`` when it is itself a package
            (``import pkg_root.mod`` then resolves to ``tree/mod.py``).

    Returns:
        dict with keys:
          - nodes: {node_id: {id, kind, text}}
          - edges: list of {src, dst, kind[, weight]}
          - meta: {schema, n_files, n_nodes, n_edges, pkg_root}

    Raises:
        FileNotFoundError: if tree is not a directory.
    """
    tree = Path(tree)
    if not tree.is_dir():
        raise FileNotFoundError(
            f"Source tree not found: {tree}. "
            f"Point parse_tree() at a real Python package directory."
        )

    paths = _iter_py_files(tree)
    rels = [_rel(tree, p) for p in paths]
    index = _ModuleIndex(rels, pkg_root)
    files = {rel: _scan_file(path, rel, index) for path, rel in zip(paths, rels, strict=True)}

    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    for rel, info in files.items():
        file_id = f"file:{rel}"
        nodes[file_id] = {"id": file_id, "kind": "file", "text": info.text}
        for qual, sym in info.syms.items():
            sid = f"sym:{rel}::{qual}"
            nodes[sid] = {"id": sid, "kind": "sym", "text": sym.text}
            edges.append({"src": file_id, "dst": sid, "kind": "contains"})

    for rel, info in files.items():
        for imported in sorted(info.imports):
            edges.append({"src": f"file:{rel}", "dst": f"file:{imported}", "kind": "import"})

    resolver = _Resolver(files)
    call_edges: set[tuple[str, str]] = set()
    for rel, info in files.items():
        for qual, sym in info.syms.items():
            caller = f"sym:{rel}::{qual}"
            for ref in sym.calls:
                target = resolver.call_target(info, sym, ref)
                if target is not None and target != caller:
                    call_edges.add((caller, target))
    for src, dst in sorted(call_edges):
        edges.append({"src": src, "dst": dst, "kind": "call"})

    co_edits = _detect_co_edits(tree, known_files=set(files))
    for (a, b), count in sorted(co_edits.items()):
        edges.append({
            "src": f"file:{a}", "dst": f"file:{b}", "kind": "co_edit", "weight": count,
        })

    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "schema": SCHEMA_VERSION,
            "n_files": len(files),
            "n_nodes": len(nodes),
            "n_edges": len(edges),
            "pkg_root": pkg_root,
        },
    }
