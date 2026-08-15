#!/usr/bin/env python3
"""CKG Claude Code UserPromptSubmit hook — transparent structure-map injection.

Reads the Claude Code hook payload from stdin (JSON with "prompt", "cwd"),
and — when inside a project with a cached code graph — appends a compact
structure map to the prompt context via hookSpecificOutput.additionalContext.

Design constraints:
  - Never blocks the agent loop: 8s hard timeout, everything best-effort.
  - Never crashes: any failure emits an empty hook output.
  - Keyword-gated and toggleable (CKG_INJECT=0 / .ckg/pi.json) so non-coding
    prompts and opt-outs pay nothing.
  - When the ckg CLI is missing, emits install guidance once per project so a
    marketplace install never silently no-ops.

Claude Code hook protocol:
  input:  {"prompt": "...", "cwd": "...", ...} on stdin
  output: {"hookSpecificOutput": {"additionalContext": "..."}} to stdout
"""

import json
import os
import re
import shutil
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

INSTALL_GUIDANCE = """## Code Knowledge Graph (CKG) — not installed

CKG is installed as a plugin but the Python CLI is missing. Install it once
with one of:

    uv tool install git+https://github.com/jasperan/ckg.git
    pip install git+https://github.com/jasperan/ckg.git

(uv tool install puts `ckg` on your PATH — then run `ckg build .` in this
project to build the dependency graph.)"""

# Lightweight mirror of the pi extension's keyword gate.
CODING_KEYWORDS = (
    "implement", "add ", "fix", "change", "refactor", "build", "create",
    "modify", "update", "feature", "bug", "edit", "write", "debug", "deploy",
    "function", "class", "module", "import", "api", "endpoint",
    "test", "migrate", "upgrade", "rewrite", "optimize", "integrate",
)


def injection_enabled(project_root: Path) -> bool:
    """CKG_INJECT env > .ckg/pi.json > default on."""
    env = os.environ.get("CKG_INJECT")
    if env is not None:
        return env not in ("0", "false", "False")
    try:
        cfg_path = project_root / ".ckg" / "pi.json"
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text())
            return bool(cfg.get("inject", True))
    except Exception:
        pass
    return True


def is_coding_prompt(prompt: str) -> bool:
    lower = prompt.lower()
    hits = sum(1 for kw in CODING_KEYWORDS if kw in lower)
    return hits >= 2


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
    """Locate the ckg CLI: env override → bundled venv → common locations → PATH."""
    env = os.environ.get("CKG_CLI")
    if env:
        return [env]
    here = Path(__file__).resolve()
    home = Path.home()
    candidates = [
        here.parent.parent / ".venv" / "bin" / "ckg",       # repo checkout
        here.parent.parent.parent / ".venv" / "bin" / "ckg",  # plugin cache
        home / "ckg" / ".venv" / "bin" / "ckg",             # curl installer default
        home / ".local" / "bin" / "ckg",                     # uv tool / pip --user
    ]
    for cand in candidates:
        if cand.exists():
            return [str(cand)]
    if shutil.which("ckg"):
        return ["ckg"]
    return None


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
        if not injection_enabled(root):
            print("{}")
            return 0
        if not is_coding_prompt(prompt):
            print("{}")
            return 0

        graph_cache = root / ".ckg" / "code_graph.json"
        if not graph_cache.exists():
            print("{}")  # background build happens via the skill / CLI
            return 0

        cmd = find_ckg()
        if cmd is None:
            # Emit one-time install guidance so the plugin never silently no-ops.
            marker = root / ".ckg" / ".hook-warned"
            if marker.exists():
                print("{}")
                return 0
            try:
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("1")
            except Exception:
                pass
            out = {"hookSpecificOutput": {"additionalContext": INSTALL_GUIDANCE}}
            print(json.dumps(out))
            return 0

        proc = subprocess.run(
            [*cmd, "inject", prompt, "--root", str(root)],
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
