"""Context injection for coding agents — build the structure map for a prompt.

Used by ``ckg inject``, which the Claude Code hook and the pi extension call
before each coding prompt:

  detect the project root → load the cached graph (building or refreshing it
  as needed) → hybrid retrieval for the prompt → render anchors + reach

The user never sees the CKG — they just make fewer tool calls and implement
features correctly more often.
"""

from __future__ import annotations

from pathlib import Path

from ckg.claude.prompts import STRUCTURE_MAP_TEMPLATE
from ckg.graph.builder import CodeGraph, render_structure_map
from ckg.graph.cache import (
    CACHE_DIR,
    CACHE_FILE,
    build_and_store,
    is_stale,
    read_graph,
    spawn_background_build,
)
from ckg.graph.parser import SCHEMA_VERSION
from ckg.retrieval.hybrid import hybrid_retrieve

REFRESH_MODES = ("auto", "sync", "background")
_MAX_ANCHOR_FILES = 5


def detect_project(cwd: Path | None = None) -> Path | None:
    """Detect the project root from the current working directory.

    Walks upward looking for .git (a directory, or a file in worktrees and
    submodules), pyproject.toml, or package.json. The home directory and the
    filesystem root never count: a dotfiles repository or a stray
    package.json there would turn everything below into one "project".
    """
    cwd = Path(cwd) if cwd else Path.cwd()
    home = Path.home()
    for parent in [cwd, *cwd.parents]:
        if parent == home or parent == parent.parent:
            return None
        if any((parent / marker).exists() for marker in (".git", "pyproject.toml", "package.json")):
            return parent
    return None


def _load_graph(project_root: Path, graph_path: Path, refresh: str) -> dict | None:
    """The project's graph, built or refreshed according to ``refresh``.

    auto        missing → build now; stale → use it, rebuild in the background
    sync        missing or stale → build now
    background  never build in this process: a missing graph yields None and
                a background build, a stale one is used and rebuilt behind it
    """
    graph: dict | None
    try:
        graph = read_graph(graph_path)
    except (OSError, ValueError):  # absent, unreadable or not a ckg graph
        graph = None
    if graph is not None and graph.get("meta", {}).get("schema") != SCHEMA_VERSION:
        graph = None  # written by an older version: unusable for ranking

    pkg_root = (graph or {}).get("meta", {}).get("pkg_root") or project_root.name
    if graph is None:
        if refresh == "background":
            spawn_background_build(project_root, pkg_root=pkg_root, output=graph_path)
            return None
        try:
            return build_and_store(project_root, pkg_root=pkg_root, output=graph_path)
        except OSError:
            return None
    if is_stale(graph, project_root):
        if refresh == "sync":
            try:
                return build_and_store(project_root, pkg_root=pkg_root, output=graph_path)
            except OSError:
                return graph
        spawn_background_build(project_root, pkg_root=pkg_root, output=graph_path)
    return graph


def build_context_map(
    query: str,
    project_root: Path,
    *,
    cache_dir: Path | None = None,
    mem=None,
    domain: str | None = None,
    graph_name: str = "ckg_code_graph",
    refresh: str = "auto",
) -> str | None:
    """Build a context map for a query against the project's code graph.

    Args:
        query: The current task/feature description.
        project_root: Root of the project to analyze.
        cache_dir: Where the graph cache lives (default: ``<root>/.ckg``).
        mem: Optional live Oracle handle (for PGQ neighbourhood matching).
        domain: PGQ domain scope.
        graph_name: Oracle property graph name.
        refresh: How to treat a missing or stale cache; see ``REFRESH_MODES``.

    Returns:
        A markdown structure map, or None when no graph is available yet or
        the query names nothing in it — an arbitrary map would be noise.
    """
    if refresh not in REFRESH_MODES:
        raise ValueError(f"refresh must be one of {REFRESH_MODES}, got {refresh!r}")
    project_root = Path(project_root)
    graph_path = (cache_dir or (project_root / CACHE_DIR)) / CACHE_FILE
    graph = _load_graph(project_root, graph_path, refresh)
    if graph is None:
        return None

    results = hybrid_retrieve(
        query, graph,
        mem=mem, domain=domain, graph_name=graph_name,
        k_anchor=5, hops=2, top_k=10,
    )

    # Lexical anchors are the entry points; symbol anchors are lifted to the
    # file that contains them.
    anchors: list[str] = []
    for a in results["anchors"]:
        if a.startswith("sym:"):
            a = "file:" + a.removeprefix("sym:").split("::", 1)[0]
        if a not in anchors:
            anchors.append(a)
    anchors = anchors[:_MAX_ANCHOR_FILES]
    if not anchors:
        return None

    return render_structure_map(
        anchors, CodeGraph.from_dict(graph), query=query, scores=results["file_scores"],
    )


def inject_context(
    query: str,
    project_root: Path | None = None,
    **kwargs,
) -> str:
    """Build and return the full context injection for an agent prompt.

    Args:
        query: The current user prompt or task description.
        project_root: Project root (auto-detected if omitted).
        **kwargs: Passed to build_context_map.

    Returns:
        A string to append to the agent's context, or an empty string when
        there is no map to show.
    """
    if project_root is None:
        project_root = detect_project()
    if project_root is None:
        return ""

    structure_map = build_context_map(query, project_root, **kwargs)
    if structure_map is None:
        return ""

    return STRUCTURE_MAP_TEMPLATE.format(
        project_name=" ".join(project_root.name.replace("`", "").split())[:80],
        structure_map=structure_map,
    )
