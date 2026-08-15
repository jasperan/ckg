"""Claude Code plugin — transparent background CKG context injection.

Hooks into the Claude Code agent loop to inject dependency-aware context maps
without any user action:

  On session start → detect project root, load/cache the code graph
  Before each prompt → append a compact structure map (anchors + 2-hop reach)

The user never sees the CKG — they just make fewer tool calls and implement
features correctly more often.

Integration points:
  - Claude Code skill (skills/ckg/SKILL.md) loads this module
  - Claude Code hooks: before:prompt → inject_context()
  - Standalone: ckg inject <query> → print the structure map
"""

from __future__ import annotations

import json
from pathlib import Path

from ckg.claude.prompts import SYSTEM_PROMPT_PREAMBLE, STRUCTURE_MAP_TEMPLATE


def detect_project(cwd: Path | None = None) -> Path | None:
    """Detect the project root from the current working directory.

    Walks upward looking for .git, pyproject.toml, or package.json.
    Returns the project root path, or None if not found.
    """
    cwd = Path(cwd) if cwd else Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".git").is_dir():
            return parent
        if (parent / "pyproject.toml").exists():
            return parent
        if (parent / "package.json").exists():
            return parent
    return None


def build_context_map(
    query: str,
    project_root: Path,
    *,
    cache_dir: Path | None = None,
    mem=None,
    domain: str | None = None,
) -> str | None:
    """Build a context map for a query against the project's code graph.

    Args:
        query: The current task/feature description.
        project_root: Root of the project to analyze.
        cache_dir: Where to store/read cached graph data (default: .ckg/).
        mem: Optional AgentMemory with Oracle connection (for PGQ queries).
        domain: PGQ domain scope.

    Returns:
        A markdown structure map string, or None if graph building failed.
    """
    cache_dir = cache_dir or (project_root / ".ckg")
    graph_path = cache_dir / "code_graph.json"

    # Load cached graph or build one
    graph: dict | None = None
    if graph_path.exists():
        try:
            graph = json.loads(graph_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    if graph is None:
        from ckg.graph.parser import parse_tree
        try:
            graph = parse_tree(project_root, pkg_root=project_root.name)
            cache_dir.mkdir(parents=True, exist_ok=True)
            graph_path.write_text(json.dumps(graph, indent=2))
        except (FileNotFoundError, OSError):
            return None

    # Run hybrid retrieval
    from ckg.retrieval.hybrid import hybrid_retrieve
    results = hybrid_retrieve(
        query, graph,
        mem=mem, domain=domain,
        k_anchor=5, hops=2, top_k=10,
    )

    # Render the structure map
    from ckg.graph.builder import CodeGraph
    cg = CodeGraph.from_dict(graph)

    # Extract anchors as file IDs — prefer the lexical seed anchors, which are
    # the actual entry points (they may rank below symbols in the PPR results).
    anchors = [a for a in results.get("anchors", []) if a.startswith("file:")][:5]
    if not anchors:
        anchors = [r["node_id"] for r in results["results"]
                   if r["node_id"].startswith("file:")][:5]
    if not anchors and results["results"]:
        anchors = [results["results"][0]["node_id"]]

    from ckg.graph.builder import render_structure_map
    return render_structure_map(anchors, cg, query=query)


def inject_context(
    query: str,
    project_root: Path | None = None,
    **kwargs,
) -> str:
    """Build and return the full context injection for a Claude Code prompt.

    This is the main entry point for Claude Code skill integration. It returns
    a string suitable for appending to the system prompt.

    Args:
        query: The current user prompt or task description.
        project_root: Project root (auto-detected if omitted).
        **kwargs: Passed to build_context_map.

    Returns:
        A string to append to the agent's system prompt, or empty string if
        graph building failed.
    """
    if project_root is None:
        project_root = detect_project()

    if project_root is None:
        return ""

    structure_map = build_context_map(query, project_root, **kwargs)

    if structure_map is None:
        return ""

    return STRUCTURE_MAP_TEMPLATE.format(
        project_name=project_root.name,
        structure_map=structure_map,
    )
