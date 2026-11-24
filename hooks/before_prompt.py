#!/usr/bin/env python3
"""CKG Claude Code UserPromptSubmit hook — transparent structure-map injection.

Reads the Claude Code hook payload from stdin (JSON with "prompt", "cwd") and,
for coding prompts inside a project, adds a compact structure map to the
prompt context.

Design constraints:
  - Never blocks the agent loop: 8s hard timeout, and the graph is never
    built inside the hook. The first coding prompt in a project starts a
    background build; the map appears once that has finished.
  - Never crashes: any failure emits an empty hook output.
  - Keyword-gated and toggleable (CKG_INJECT=0 / .ckg/pi.json) so non-coding
    prompts and opt-outs pay nothing.
  - When the ckg CLI is missing, emits install guidance once per project so a
    marketplace install never silently no-ops.

Claude Code hook protocol:
  input:  {"prompt": "...", "cwd": "...", ...} on stdin
  output: {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                  "additionalContext": "..."}} on stdout
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
parsed ahead of time. The structure map below shows the *dependency cluster*
for this task. Changing one file in the cluster often requires changing its
neighbors — start exploration there instead of searching the whole filesystem."""

INSTALL_GUIDANCE = """## Code Knowledge Graph (CKG) — not installed

CKG is installed as a plugin but the Python CLI is missing. Install it once
with one of:

    uv tool install git+https://github.com/jasperan/ckg.git
    pip install git+https://github.com/jasperan/ckg.git

(uv tool install puts `ckg` on your PATH. The dependency graph for a project
is then built automatically on the first coding prompt.)"""

# Same gate as the pi extension (pi/extensions/ckg/project.ts).
CODING_KEYWORDS = (
    "implement", "add ", "fix", "change", "refactor", "build", "create",
    "modify", "update", "feature", "bug", "edit", "write", "debug", "deploy",
    "function", "class", "module", "import", "api", "endpoint",
    "test", "migrate", "upgrade", "rewrite", "optimize", "integrate",
)
CODING_PATTERNS = (
    re.compile(
        r"\b(implement|add|fix|change|refactor|build|create|modify|update|debug)\b.*"
        r"\b(feature|bug|function|class|module|file|code|endpoint|api|test|support)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(write|edit)\b", re.IGNORECASE),
)


def injection_enabled(project_root: Path) -> bool:
    """CKG_INJECT env > .ckg/pi.json > default on."""
    env = os.environ.get("CKG_INJECT")
    if env is not None:
        return env.strip().lower() not in ("0", "false", "off", "no")
    try:
        cfg_path = project_root / ".ckg" / "pi.json"
        if cfg_path.exists():
            value = json.loads(cfg_path.read_text()).get("inject", True)
            return value if isinstance(value, bool) else True
    except Exception:
        pass
    return True


def is_coding_prompt(prompt: str) -> bool:
    lower = prompt.lower()
    if sum(1 for kw in CODING_KEYWORDS if kw in lower) >= 2:
        return True
    return any(pattern.search(prompt) for pattern in CODING_PATTERNS)


def detect_project(cwd: str) -> Path | None:
    """Walk upward looking for a project root (mirrors ckg.claude.plugin)."""
    p = Path(cwd)
    home = Path.home()
    for parent in [p, *p.parents]:
        if parent == home or parent == parent.parent:
            return None  # never treat $HOME or / as one project
        # `.git` is a file in worktrees and submodules.
        if any((parent / m).exists() for m in (".git", "pyproject.toml", "package.json")):
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


def install_guidance_once(root: Path) -> str | None:
    """The install hint the first time it is needed in a project, else None."""
    marker = root / ".ckg" / ".hook-warned"
    if marker.exists():
        return None
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        ignore = marker.parent / ".gitignore"
        if not ignore.exists():
            ignore.write_text("*\n")
        marker.write_text("1")
    except OSError:
        pass
    return INSTALL_GUIDANCE


def emit(context: str | None) -> int:
    if not context:
        print("{}")
        return 0
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }))
    return 0


def main() -> int:
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        payload = json.loads(raw) if raw.strip() else {}
        raw_prompt = str(payload.get("prompt", ""))
        cwd = str(payload.get("cwd") or os.getcwd())
        if not raw_prompt.strip():
            return emit(None)

        root = detect_project(cwd)
        if root is None or not injection_enabled(root) or not is_coding_prompt(raw_prompt):
            return emit(None)

        cmd = find_ckg()
        if cmd is None:
            return emit(install_guidance_once(root))

        prompt = " ".join(raw_prompt.split())[:MAX_QUERY_CHARS]
        proc = subprocess.run(
            # "--": the prompt may start with "-". "--refresh background": a
            # missing graph is built by a detached process, not here.
            [*cmd, "inject", "--root", str(root), "--refresh", "background", "--", prompt],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            cwd=cwd,
        )
        structure_map = proc.stdout.strip() if proc.returncode == 0 else ""
        return emit(f"{PREAMBLE}\n\n{structure_map}" if structure_map else None)
    except Exception:
        return emit(None)


if __name__ == "__main__":
    sys.exit(main())
