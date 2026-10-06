"""Tests for the Claude Code UserPromptSubmit hook (hooks/before_prompt.py)."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "before_prompt.py"
CKG = [sys.executable, "-m", "ckg.cli.main"]


def _run_hook(payload: dict, env_extra: dict) -> dict:
    env = {**os.environ, **env_extra}
    proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=30, env=env)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_hook_passes_dash_prefixed_prompt_to_real_cli(tmp_path):
    """A prompt starting with '-' must reach `ckg inject` as the query, not as a flag."""
    pkg = tmp_path / "proj"
    pkg.mkdir()
    (pkg / "pyproject.toml").write_text("")
    (pkg / "rate_limiter.py").write_text("def limit_rate():\n    '''throttle'''\n")
    # Prompt is a single dash-led token, so argparse would treat it as an option.
    wrapper = tmp_path / "ckg"
    wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} -m ckg.cli.main \"$@\"\n")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    subprocess.run([*CKG, "build", str(pkg), "--pkg-root", "proj"], check=True,
                   capture_output=True)

    out = _run_hook(
        {"prompt": "--fix-rate_limiter-bug-implement", "cwd": str(pkg)},
        {"CKG_CLI": str(wrapper), "CKG_INJECT": "1", "CKG_ORACLE_DSN": ""},
    )
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "rate_limiter.py" in ctx
