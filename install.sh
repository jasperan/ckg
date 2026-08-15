#!/usr/bin/env bash
# CKG — Code Knowledge Graph: one-command install for every coding agent.
#
# Installs the Python CLI, then registers CKG as a plugin/skill with pi,
# Claude Code, and Codex/OpenCode — whichever agents you use.
#
# Usage (1 command, no clone needed):
#   curl -fsSL https://raw.githubusercontent.com/jasperan/ckg/main/install.sh | bash
#
# Options:
#   PROJECT_DIR          — where to clone CKG (default: $HOME/ckg)
#   CKG_SKIP_SKILLS      — set to 1 to skip agent registration
#   CKG_AGENTS           — comma-separated: pi,claude,codex (default: all found)

set -euo pipefail

REPO_URL="https://github.com/jasperan/ckg.git"
PROJECT_DIR="${PROJECT_DIR:-$HOME/ckg}"
CKG_SKIP_SKILLS="${CKG_SKIP_SKILLS:-0}"
CKG_AGENTS="${CKG_AGENTS:-pi,claude,codex}"

echo ""
echo "  CKG — Code Knowledge Graph Installer"
echo "  ====================================="
echo ""

# --------------------------------------------------------------------------- #
# 1) Clone or update
# --------------------------------------------------------------------------- #
if [ -d "$PROJECT_DIR/.git" ]; then
    echo "  [1/4] Updating CKG at $PROJECT_DIR ..."
    git -C "$PROJECT_DIR" pull --quiet --ff-only 2>/dev/null || true
else
    echo "  [1/4] Cloning CKG into $PROJECT_DIR ..."
    git clone --quiet "$REPO_URL" "$PROJECT_DIR"
fi
cd "$PROJECT_DIR"

# --------------------------------------------------------------------------- #
# 2) Python environment + CLI
# --------------------------------------------------------------------------- #
echo "  [2/4] Installing the Python CLI (uv preferred) ..."
if command -v uv &> /dev/null; then
    uv sync --quiet || true
else
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -e . --quiet || true
fi

CLI="$PROJECT_DIR/.venv/bin/ckg"
if [ -x "$CLI" ]; then
    echo "        CKG CLI: $CLI"
else
    echo "        CKG CLI: not found (add .venv/bin to PATH if needed)"
fi

# --------------------------------------------------------------------------- #
# 3) Register with agents
# --------------------------------------------------------------------------- #
echo "  [3/4] Registering CKG with your agents ..."

if [ "$CKG_SKIP_SKILLS" = "1" ]; then
    echo "        Skipping agent registration."
else
    IFS=',' read -ra AGENTS <<< "$CKG_AGENTS"

    for agent in "${AGENTS[@]}"; do
        case "$agent" in
            pi)
                if command -v pi &> /dev/null; then
                    echo "        pi → pi install $PROJECT_DIR"
                    pi install "$PROJECT_DIR" 2>/dev/null || \
                        { mkdir -p "$HOME/.pi/agent/extensions" && \
                          rm -rf "$HOME/.pi/agent/extensions/ckg" && \
                          cp -r "$PROJECT_DIR/pi/extensions/ckg" "$HOME/.pi/agent/extensions/ckg" && \
                          echo "        pi → copied extension to ~/.pi/agent/extensions/ckg/"; }
                else
                    echo "        pi → 'pi' CLI not found (skip; install pi first: npm i -g pi-coding-agent)"
                fi
                ;;
            claude)
                if command -v claude &> /dev/null; then
                    echo "        claude → marketplace add + plugin install"
                    (claude plugin marketplace add "$PROJECT_DIR" 2>/dev/null || true)
                    (claude plugin install ckg 2>/dev/null || true) || \
                        { DST="$HOME/.claude/skills/ckg"
                          mkdir -p "$(dirname "$DST")"
                          rm -rf "$DST" 2>/dev/null || true
                          cp -r "$PROJECT_DIR/skills/ckg" "$DST"
                          echo "        claude → copied skill to ~/.claude/skills/ckg/"; }
                else
                    echo "        claude → 'claude' CLI not found (skip)"
                fi
                ;;
            codex|opencode)
                DST="$HOME/.config/opencode/skills/ckg"
                mkdir -p "$(dirname "$DST")"
                rm -rf "$DST" 2>/dev/null || true
                cp -r "$PROJECT_DIR/skills/ckg" "$DST"
                echo "        codex → ~/.config/opencode/skills/ckg/"
                ;;
            *)
                echo "        Unknown agent: $agent (use pi,claude,codex)"
                ;;
        esac
    done
fi

# --------------------------------------------------------------------------- #
# 4) Optional: Oracle AI Database 26ai Free container
# --------------------------------------------------------------------------- #
echo "  [4/4] Oracle AI Database 26ai Free (optional, enables PGQ retrieval) ..."
if command -v docker &> /dev/null; then
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qiE 'oracle|free'; then
        echo "        Oracle container detected — set CKG_ORACLE_DSN/USER/PASSWORD in your shell."
    else
        echo "        None detected. Run one command to start it:"
        echo "        docker run -d --name ckg-oracle -p 1521:1521 -e ORACLE_PWD=continual_learning container-registry.oracle.com/database/free:latest"
    fi
else
    echo "        docker not found (skip)"
fi

echo ""
echo "  CKG installed. Restart your coding agent to pick up the plugin."
echo ""
echo "  Quick test: cd any-python-project && ckg build . && ckg query 'your task'"
echo "  Oracle PGQ: export CKG_ORACLE_DSN=localhost:1521/FREEPDB1 && ckg load ."
echo ""
