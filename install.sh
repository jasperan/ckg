#!/usr/bin/env bash
# CKG — Code Knowledge Graph: one-command install
#
# Installs CKG and registers it as a skill with your coding agents.
# Supports Claude Code, Codex (OpenCode), and any agent that reads skill dirs.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/jasperan/ckg/main/install.sh | bash
#   bash install.sh
#
# Options:
#   PROJECT_DIR          — where to clone CKG (default: $HOME/ckg)
#   CKG_SKIP_SKILLS      — set to 1 to skip skill registration
#   CKG_AGENTS           — comma-separated: claude,codex,opencode (default: all)

set -euo pipefail

REPO_URL="https://github.com/jasperan/ckg.git"
PROJECT_DIR="${PROJECT_DIR:-$HOME/ckg}"
CKG_SKIP_SKILLS="${CKG_SKIP_SKILLS:-0}"
CKG_AGENTS="${CKG_AGENTS:-claude,codex,opencode}"

echo ""
echo "  CKG — Code Knowledge Graph Installer"
echo "  ====================================="
echo ""

# Clone or update
if [ -d "$PROJECT_DIR/.git" ]; then
    echo "  Updating CKG at $PROJECT_DIR ..."
    git -C "$PROJECT_DIR" pull --quiet --ff-only 2>/dev/null || true
else
    echo "  Cloning CKG into $PROJECT_DIR ..."
    git clone --quiet "$REPO_URL" "$PROJECT_DIR"
fi
cd "$PROJECT_DIR"

# Python environment
if command -v uv &> /dev/null; then
    echo "  Installing with uv ..."
    uv sync --quiet
else
    echo "  Installing with pip ..."
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -e . --quiet
fi

# Verify CLI
if uv run ckg --help &>/dev/null 2>&1; then
    echo "  CKG CLI: OK"
else
    echo "  CKG CLI: not on PATH (add $PROJECT_DIR/.venv/bin to PATH)"
fi

# Register skills
if [ "$CKG_SKIP_SKILLS" = "1" ]; then
    echo "  Skipping skill registration."
else
    SKILL_SRC="$PROJECT_DIR/skills/ckg"
    IFS=',' read -ra AGENTS <<< "$CKG_AGENTS"

    for agent in "${AGENTS[@]}"; do
        case "$agent" in
            claude)
                DST="$HOME/.claude/skills/ckg"
                mkdir -p "$(dirname "$DST")"
                rm -rf "$DST" 2>/dev/null || true
                cp -r "$SKILL_SRC" "$DST"
                echo "  Registered: Claude Code -> ~/.claude/skills/ckg/"
                ;;
            codex|opencode)
                DST="$HOME/.config/opencode/skills/ckg"
                mkdir -p "$(dirname "$DST")"
                rm -rf "$DST" 2>/dev/null || true
                cp -r "$SKILL_SRC" "$DST"
                echo "  Registered: OpenCode/Codex -> ~/.config/opencode/skills/ckg/"
                ;;
            *)
                echo "  Unknown agent: $agent (use claude,codex,opencode)"
                ;;
        esac
    done
fi

echo ""
echo "  CKG installed. Restart your coding agent to pick up the skill."
echo "  Quick test: cd any-python-project && ckg build . && ckg query 'your task'"
echo ""

