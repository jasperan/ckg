---
name: ckg
description: >
  Code Knowledge Graph (CKG) — dependency-aware context for coding tasks in
  Python projects. Use at the start of a task that edits, creates or debugs
  source files: it names the files a change is likely to touch (anchors plus
  their import / call / co-edit neighbours).
---

# CKG — Code Knowledge Graph

CKG runs **silently in the background**. Never mention it to the user unless
asked. It works like a co-pilot's map — the pilot sees the terrain, not
the map generator.

## When to use

On a coding task in a project with Python sources, when the `ckg` CLI is
available (`ckg --version` succeeds). If a `CKG Structure Map` is already in
your context for this task, use that one and skip the steps below.

## Workflow

### 1. Get the structure map

```bash
PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
ckg inject --root "$PROJECT_ROOT" -- "<1-sentence task summary>" 2>/dev/null
```

The first call in a project builds the dependency graph (a few seconds; cached
in `.ckg/`, refreshed automatically when sources change). Exit code 1 with no
output means the summary names nothing in the code — retry with identifiers
from the task (function, class or file names).

The map shows:
- **Anchor files** — lexically relevant to the task
- **Dependency reach** — connected via imports, calls, co-edits, most relevant
  first, each with how it relates to the anchors

### 2. Use the map

- Start exploration from anchor + reach files
- Check co-edited files for parallel changes
- The map is a starting point, not a constraint

## Communication Rule

Never mention CKG to the user. It's background infrastructure.
