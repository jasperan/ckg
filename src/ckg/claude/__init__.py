"""Agent integration — build the structure map injected into coding prompts.

``ckg inject`` (called by the Claude Code hook and the pi extension) goes
through this package: detect the project root, load or refresh the cached
code graph, run hybrid retrieval for the prompt, and render the map.
"""

from ckg.claude.plugin import (
    build_context_map,
    detect_project,
    inject_context,
)

__all__ = ["detect_project", "build_context_map", "inject_context"]
