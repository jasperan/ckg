#!/usr/bin/env python3
"""CKG Claude Code BeforePrompt hook — transparent structure-map injection.

Reads the Claude Code hook payload from stdin (JSON with "prompt", "cwd"),
and — when inside a project with a cached code graph — appends a compact
structure map to the prompt context via hookSpecificOutput.additionalContext.

Design constraints:
  - Never blocks the agent loop: 8s hard timeout, everything best-effort.
  - Never crashes: any failure emits an empty hook output.
  - Uses the same CKG_ORACLE_* env vars as the CLI, so retrieval runs through
    Oracle PGQ automatically when configured.

Claude Code hook protocol:
  input:  {"prompt": "...", "cwd": "...", ...} on stdin
  output: {"hookSpecificOutput": {"additionalContext": "..."}} to stdout
"""

import json
import os
import subprocess
import sys
from pathlib import Path

MAX_QUERY_CHARS = 400
TIMEOUT_SECONDS = 8
PREAMBLE = """## Code Knowledge Graph (CKG) — Structure-Aware Context

A dependency graph of this codebase (imports, function calls, git co-edits) was
parsed and stored in Oracle PGQ / local cache. The structure map below shows the
*dependency cluster* for this task. Changing one file in the cluster often
requires changing its neighbors — start exploration there instead of searching
the whole filesystem."""


def detect_project(cwd: str) -> Path | None:
    """Walk upward looking for a project root (mirrors ckg.claude.plugin)."""
    p = Path(cwd)
    for parent in [p, *p.parents]:
        if (parent / ".git").is_dir():
            return parent
        if (parent / "pyproject.toml").exists():
            return parent
        if (parent / "package.json").exists():
            return parent
    return None


def find_ckg() -> list[str] | None:
    """Locate the ckg CLI: env override → bundled venv → PATH."""
    env = os.environ.get("CKG_CLI")
    if env:
        return [env]
    # bundled venv from the npm/pi install (repo layout: <root>/.venv/bin/ckg)
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent / ".venv" / "bin" / "ckg",
    ]
    for cand in candidates:
        if cand.exists():
            return [str(cand)]
    return ["ckg"]  # let PATH resolve; hook reports failure if absent


def main() -> int:
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        payload = json.loads(raw) if raw.strip() else {}
        prompt = str(payload.get("prompt", ""))[:MAX_QUERY_CHARS]
        cwd = str(payload.get("cwd") or os.getcwd())
        if not prompt.strip():
            print("{}")
            return 0

        root = detect_project(cwd)
        if root is None:
            print("{}")
            return 0

        graph_cache = root / ".ckg" / "code_graph.json"
        if not graph_cache.exists():
            print("{}")  # background build happens via the skill / CLI
            return 0

        cmd = [*find_ckg(), "inject", prompt, "--root", str(root)]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            cwd=cwd,
            env={**os.environ, "CKG_NONINTERACTIVE": "1"},
        )
        if proc.returncode != 0:
            print("{}")
            return 0

        structure_map = proc.stdout.strip()
        if not structure_map:
            print("{}")
            return 0

        context = f"{PREAMBLE}\n\n{structure_map}"
        out = {"hookSpecificOutput": {"additionalContext": context}}
        print(json.dumps(out))
        return 0
    except Exception:
        print("{}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
