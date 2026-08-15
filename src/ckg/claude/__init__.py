"""Claude Code integration — transparent background CKG context injection.

The CKG Claude Code plugin hooks into the agent loop silently:
  - On session start: detect the project root, check for a cached code graph
  - Before each prompt: inject a compact structure map (anchors + graph reach)
    so the agent sees dependency-aware context without any user action

The plugin runs as a Claude Code skill that loads this package in the
background. No user configuration needed beyond installing the repo.
"""

from ckg.claude.plugin import (
    detect_project,
    build_context_map,
    inject_context,
)

__all__ = ["detect_project", "build_context_map", "inject_context"]
