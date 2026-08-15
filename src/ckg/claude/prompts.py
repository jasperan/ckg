"""Prompt templates for CKG Claude Code integration.

The preamble explains to the coding agent what a CKG structure map is and
how to use it. The structure map template wraps the rendered markdown in
a consistent format the agent can parse.
"""

SYSTEM_PROMPT_PREAMBLE: str = """## Code Knowledge Graph (CKG) — Structure-Aware Context

A dependency graph of this codebase has been automatically built from the
source tree. Every time you receive a task, a **structure map** is appended
below showing:

1. **Anchor files** — files most relevant to the task (found by lexical match)
2. **Dependency reach** — files connected to the anchors via imports, function
   calls, and git co-edit history

The files in the map form a *dependency cluster* — changing one often requires
changing its neighbors. Use this map to discover all files that may need edits,
rather than searching the filesystem from scratch.

**How to read the map:**
- `imports` — file A imports file B (A depends on B)
- `calls` — function A calls function B (A depends on B)
- `co-edited with` — both files changed together in git history
- `contains` — file contains the following symbols
"""

STRUCTURE_MAP_TEMPLATE: str = """\
---
## CKG Structure Map for `{project_name}`
{structure_map}
"""
