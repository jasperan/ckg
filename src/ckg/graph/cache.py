"""On-disk graph cache: ``<project>/.ckg/code_graph.json``.

The cache records a fingerprint of the sources it was built from, so a reader
can tell when it has gone stale. Rebuilding can take seconds on a large
project, which is too long to block an agent prompt, so a stale cache is
still served while a detached process rebuilds it for the next prompt.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ckg.graph.builder import build_graph, validate_graph
from ckg.graph.parser import SCHEMA_VERSION, _iter_py_files

CACHE_DIR = ".ckg"
CACHE_FILE = "code_graph.json"
_LOCK_FILE = "build.lock"
# A build that has not finished after this long is assumed dead.
_LOCK_MAX_AGE_S = 15 * 60


def cache_path(root: Path) -> Path:
    return Path(root) / CACHE_DIR / CACHE_FILE


def source_fingerprint(tree: Path) -> str:
    """Digest of the source files (path, size, mtime) and the git HEAD.

    HEAD is part of it because co-edit edges come from history: a commit
    changes them without touching any file's mtime.
    """
    digest = hashlib.sha1(usedforsecurity=False)
    for path in _iter_py_files(tree):
        try:
            st = path.stat()
        except OSError:
            continue
        digest.update(f"{path.relative_to(tree).as_posix()}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    try:
        head = subprocess.run(
            ["git", "-C", str(tree), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if head.returncode == 0:
            digest.update(head.stdout.strip().encode())
    except (subprocess.TimeoutExpired, OSError):
        pass
    return digest.hexdigest()


def write_graph(path: Path, graph: dict) -> None:
    """Write the graph atomically, so a reader never sees a partial file.

    A ``.ckg`` directory created here gets a ``.gitignore`` covering itself:
    the cache belongs to the machine, not to the repository.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ignore = path.parent / ".gitignore"
    if path.parent.name == CACHE_DIR and not ignore.exists():
        ignore.write_text("*\n")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(graph, fh, separators=(",", ":"), ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_graph(path: Path) -> dict:
    """Load and validate a graph file.

    Raises FileNotFoundError when absent and ValueError when it is not a
    usable ckg graph (corrupt JSON, wrong shape, or an older schema).
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid ckg graph: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ValueError(f"invalid ckg graph: {exc}") from exc
    return validate_graph(data)


def build_and_store(tree: Path, *, pkg_root: str, output: Path | None = None) -> dict:
    """Build the graph for ``tree`` and write it to ``output`` (default: the cache)."""
    tree = Path(tree)
    graph = build_graph(tree, pkg_root=pkg_root)
    graph["meta"]["fingerprint"] = source_fingerprint(tree)
    write_graph(output or cache_path(tree), graph)
    return graph


def is_stale(graph: dict, tree: Path) -> bool:
    meta = graph.get("meta", {})
    return (
        meta.get("schema") != SCHEMA_VERSION
        or meta.get("fingerprint") != source_fingerprint(Path(tree))
    )


def spawn_background_build(tree: Path, *, pkg_root: str, output: Path | None = None) -> bool:
    """Start a detached ``ckg build`` for ``tree`` unless one is already running.

    Returns True when a build was started. The lock file is removed by the
    build itself; one left behind by a killed build expires after 15 minutes.
    """
    tree = Path(tree)
    output = Path(output) if output else cache_path(tree)
    lock = output.parent / _LOCK_FILE
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        if lock.exists() and time.time() - lock.stat().st_mtime > _LOCK_MAX_AGE_S:
            lock.unlink()
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except OSError:  # includes FileExistsError: a build is in flight
        return False
    try:
        subprocess.Popen(
            [sys.executable, "-m", "ckg.cli.main", "build", str(tree),
             "--pkg-root", pkg_root, "--output", str(output),
             "--quiet", "--lock", str(lock)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
        )
    except OSError:
        lock.unlink(missing_ok=True)
        return False
    return True
