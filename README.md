<p align="center">
  <h1 align="center">CKG — Code Knowledge Graph</h1>
</p>

<p align="center">
  <strong>Structure-aware retrieval for coding agents.</strong> Parse your codebase into a dependency graph, store it in Oracle AI Database 26ai Free PGQ, and inject dependency-aware context into <strong>pi</strong>, Claude Code, and Codex — so your agent finds the right files, faster.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.12+" />
  <img src="https://img.shields.io/badge/npm-ckg-CB3837?style=for-the-badge&logo=npm&logoColor=white" alt="npm: ckg" />
  <img src="https://img.shields.io/badge/pi-Plugin-00D9FF?style=for-the-badge" alt="pi Plugin" />
  <img src="https://img.shields.io/badge/Oracle_AI_Database-26ai_Free-F80000?style=for-the-badge&logo=oracle&logoColor=white" alt="Oracle AI Database 26ai Free" />
  <img src="https://img.shields.io/badge/Claude_Code-Plugin-6C47FF?style=for-the-badge&logo=anthropic&logoColor=white" alt="Claude Code Plugin" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg?style=for-the-badge" alt="License: MIT" /></a>
</p>

---

CKG is a **transparent background plugin** for coding agents. It builds a typed dependency graph of your codebase — import edges, function calls, git co-edits — and injects a compact **structure map** into the agent's system prompt on every task. The agent sees the dependency cluster it needs to touch *before* it starts searching the filesystem.

## The Thesis

> **Structure-aware retrieval helps coding agents find the right files faster.**

A codebase is not a flat list of files. It's a graph: files import each other, functions call each other, and git history reveals which files change together. CKG surfaces these relationships so the agent navigates the *structure*, not the *filesystem*.

## Verified

The hybrid retrieval algorithm was empirically verified across **7 real httpie PRs** via the [graphify-verification](https://github.com/jasperan/dl-ai-continual-learning/tree/main/ckg_tests) experiment:

| Metric | Lexical Only (bare repo) | CKG Hybrid | Improvement |
|--------|--------------------------|------------|:-----------:|
| **Gold file recall** | 0.20 | 0.72 | **+0.52** |
| **Tool calls to first correct edit** | baseline | −36% | fewer |
| **Total tool calls** | baseline | −22% | fewer |
| **Cost** | baseline | −12% | cheaper |

The experiment drives Claude Code headless — 5 runs with a bare checkout (control) vs 5 runs with CKG's structure map (treatment) — implementing real features in real repos. The agent makes fewer, more targeted edits because it starts from the right files.

## Architecture at a Glance

```
                      ┌──────────────────────┐
                      │   Claude Code / Agent │
                      │                       │
                      │  "implement feature"  │
                      └──────────┬───────────┘
                                 │
                    system prompt injection
                                 │
              ┌──────────────────┴──────────────────┐
              │           CKG Plugin                  │
              │                                       │
              │  ┌─────────┐  ┌──────────┐  ┌──────┐ │
              │  │ Parser  │  │ Retrieval│  │Inject│ │
              │  │ (AST)   │→ │ (Hybrid) │→ │(Map) │ │
              │  └─────────┘  └──────────┘  └──────┘ │
              │       │              │                │
              └───────┼──────────────┼────────────────┘
                      │              │
              ┌───────┴──────┐  ┌───┴──────────────┐
              │  Source Tree │  │  Oracle AI DB     │
              │  .py files   │  │  PGQ Property     │
              │              │  │  Graph            │
              └──────────────┘  └──────────────────┘
```

## Why Oracle PGQ?

CKG uses Oracle's **SQL Property Graph** (PGQ) to store and query code graphs:

- **In-DB traversal** — `GRAPH_TABLE ... MATCH` finds dependency neighborhoods in the database, not in Python
- **Quantified paths** — `MATCH (v)-[]->{1,3}(w)` for multi-hop reach without recursive queries
- **Domain-scoped** — Multiple projects share the same tables, isolated by `domain` key
- **ACID** — No data loss on crash; idempotent MERGE for repeated builds

The split: **Oracle PGQ finds the structural edges. Python's Personalized PageRank scores them.**

## Quick Start

<!-- one-command-install -->
> **One command, every agent**: clones CKG, installs the Python CLI, and
> registers it with **pi**, **Claude Code**, and **Codex** automatically:
>
> ```bash
> curl -fsSL https://raw.githubusercontent.com/jasperan/ckg/main/install.sh | bash
> ```
>
> <details><summary>Advanced options</summary>
>
> Override install location:
> ```bash
> PROJECT_DIR=/opt/ckg curl -fsSL https://raw.githubusercontent.com/jasperan/ckg/main/install.sh | bash
> ```
>
> Register with specific agents only (`pi,claude,codex`):
> ```bash
> CKG_AGENTS=pi curl -fsSL https://raw.githubusercontent.com/jasperan/ckg/main/install.sh | bash
> ```
> </details>

### Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/) (recommended) or pip
- [Oracle AI Database 26ai Free](https://www.oracle.com/database/free/) (optional — enables in-DB PGQ retrieval)

### Install for pi (recommended)

```bash
# One command — npm package (auto-installs the Python core):
pi install npm:ckg

# Or straight from GitHub (works right now, no npm needed):
pi install git:github.com/jasperan/ckg

# Or try it for one session without installing:
pi -e git:github.com/jasperan/ckg
```

CKG is a **pi package** ([pi.dev/packages](https://pi.dev/packages) catalog) and
works as a **transparent plugin**: on every coding prompt it builds (or loads)
 your codebase's dependency graph and injects a compact **structure map** into
the system prompt — no user action, ever. It also registers six tools
(`ckg_status`, `ckg_build`, `ckg_load`, `ckg_query`, `ckg_inject`,
`ckg_oracle_status`) and a `/ckg` command. `pi install npm:ckg` runs a
postinstall that sets up the Python CLI in a bundled venv, so there is nothing
extra to configure.

### Install for Claude Code

```bash
claude plugin marketplace add jasperan/ckg
claude plugin install ckg
```

This installs a marketplace plugin with a `UserPromptSubmit` hook that injects
the structure map on every prompt, plus six `/ckg-*` slash commands. (Or
`claude plugins install ~/ckg/skills/ckg` for the skill-only variant.)

### Install for Codex / OpenCode

```bash
mkdir -p ~/.config/opencode/skills && cp -r skills/ckg ~/.config/opencode/skills/
```

### Install the CLI only

```bash
# From this repo:
git clone https://github.com/jasperan/ckg.git && cd ckg && uv sync

# Or straight from git, anywhere:
pip install git+https://github.com/jasperan/ckg.git
```

### 1. Build a Graph

```bash
# From the root of any Python project:
ckg build . --pkg-root mypackage

# Output: .ckg/code_graph.json
#   files: 208
#   nodes: 1467
#   edges: 5201
```

### 2. Query the Graph

```bash
ckg query "add JWT authentication middleware"
# Retrieval (memory):
#   Anchors: auth.py, middleware.py, config.py
#
#   0.8521  auth.py
#   0.7234  middleware.py
#   0.6891  security.py
#   0.5432  config.py
#   ...
```

### 3. (Optional) Store in Oracle PGQ

```bash
# Start Oracle AI Database 26ai Free (one command):
docker run -d --name ckg-oracle -p 1521:1521 \
  -e ORACLE_PWD=continual_learning \
  container-registry.oracle.com/database/free:latest

export CKG_ORACLE_DSN=localhost:1521/FREEPDB1 \
       CKG_ORACLE_USER=dmuser \
       CKG_ORACLE_PASSWORD=continual_learning

# Parse + store the graph into PGQ:
ckg load . --pkg-root mypackage --domain myapp
ckg oracle-status   # verify connectivity + stored graph
```

With `CKG_ORACLE_DSN` set, **every** `ckg query` / `ckg inject` (and the pi
plugin's transparent injection) runs the neighborhood match via
`GRAPH_TABLE ... MATCH` inside Oracle and only Personalized PageRank in
Python. `ckg oracle-status` shows the retrieval mode at a glance.

### 4. Inject into an agent

```bash
ckg inject "fix the rate limiter bug"
# ---
# ## CKG Structure Map for `myapp`
#
# ### Anchor Files (lexical match)
# - `core/rate_limiter.py` — Module ...
#
# ### Dependency Reach (2-hop imports, calls, co-edits)
# - `core/throttle.py` — ...
#   *(via rate_limiter.py, imports)*
# - `config/settings.py` — ...
#   *(via rate_limiter.py, imports)*
#   ...
```

The output is a markdown blob ready to append to your agent's system prompt.

## pi Plugin (detailed)

CKG for pi is a **transparent background extension**: you install it once and
never think about it again.

**What it does on every coding prompt:**

1. Detects the project root (`.git` / `pyproject.toml` / `package.json`).
2. Loads the cached code graph (`.ckg/code_graph.json`), building it once in
the background if missing.
3. Runs hybrid retrieval against your prompt — via **Oracle PGQ** when
`CKG_ORACLE_DSN` is set, in-memory otherwise.
4. Appends a compact structure map (anchor files + dependency reach) to the
system prompt, behind the `before_agent_start` event.

Keyword-gated (only coding prompts), time-boxed (never blocks the loop), and
cached per session — the agent sees the map, the user sees nothing.

**Tools the agent can call:**

| Tool | Purpose |
|------|---------|
| `ckg_status` | CLI / project / cache / Oracle state |
| `ckg_build` | Parse the tree once (`.ckg/code_graph.json`) |
| `ckg_load` | Store the graph into Oracle PGQ |
| `ckg_query` | Ranked hybrid retrieval for a task |
| `ckg_inject` | Explicit structure map for a query |
| `ckg_oracle_status` | Oracle connectivity + stored graph stats |

`/ckg` shows the same status in the TUI. Disable transparent injection with
`CKG_INJECT=0` or `.ckg/pi.json` (`{"inject": false}`).

## Claude Code Plugin (detailed)

CKG works as a **transparent Claude Code plugin**. Install the marketplace and
Claude Code automatically injects structure maps on every prompt — no user
action needed.

- **Hook**: `UserPromptSubmit` runs `hooks/before_prompt.py` (8s timeout,
  best-effort) which appends the structure map via
  `hookSpecificOutput.additionalContext`.
- **Commands**: `/ckg-status`, `/ckg-build`, `/ckg-load`, `/ckg-query`,
  `/ckg-inject`, `/ckg-oracle-status`.
- **Skill**: `skills/ckg/` activates on coding keywords and instructs the agent
  to run `ckg build/inject` silently.

The user never sees CKG — they just make fewer tool calls.

## Edge Types

CKG parses four edge types from your source tree and git history:

| Edge | Direction | Source | Meaning |
|------|:---------:|--------|---------|
| **import** | directed | AST `import` / `from X import` | File A depends on file B |
| **call** | directed | AST `Call` nodes | Function A calls function B |
| **co_edit** | undirected | `git log --name-only` | Files A and B changed together |
| **contains** | directed | AST top-level symbols | File contains a function/class |

## Schema (Oracle PGQ)

When using Oracle PGQ as the graph backend, CKG creates these tables:

| Table | Purpose | Key Feature |
|--------|---------|-------------|
| `MEMORY_GRAPH_NODES` | Code graph vertices | Composite key: `(id, domain)` |
| `MEMORY_GRAPH_EDGES` | Typed dependency edges | Composite key: `(src, dst, kind, domain)` |

The property graph (`ckg_code_graph`) layers SQL/PGQ over both tables so `GRAPH_TABLE ... MATCH` queries traverse dependencies entirely in the database.

## Retrieval Algorithm

The hybrid retrieval pipeline runs in three steps:

1. **Lexical anchors** — Token overlap between the query and enriched node labels (docstrings, signatures, string literals). These are the entry points an agent *would* find anyway.

2. **Graph reach** — 2-hop neighborhood from each anchor via Oracle PGQ `MATCH` (or in-memory BFS). These are the files a keyword search *misses* — the imports, callers, and co-edited siblings.

3. **Personalized PageRank** — Structural ranking over the matched subgraph. Seeds at the lexical anchors, teleports to the graph neighborhood, surfaces the most structurally central files.

```python
from ckg.retrieval import hybrid_retrieve

results = hybrid_retrieve(
    "add rate limiting to the API gateway",
    graph,
    k_anchor=5,   # max lexical seeds
    hops=2,       # graph reach depth
    top_k=10,     # results to return
)
# results["results"] → [{node_id, score, text}, ...]
# results["anchors"] → ["file:api/gateway.py", ...]
# results["method"] → "memory" or "pgq"
```

## Configuration

```yaml
# .ckg/config.yaml or configs/default.yaml

# Top-level package name
pkg_root: "myapp"

# Retrieval tuning
k_anchor: 5      # Max lexical seeds
hops: 2          # Graph reach depth
top_k: 10        # Results to return

# Oracle PGQ (optional)
domain: "default"
graph_name: "ckg_code_graph"
table_prefix: "MEMORY_GRAPH"
```

## Layout

```
ckg/
  src/ckg/
    graph/
      parser.py           # AST-based import/call/co_edit parsing
      builder.py          # Enrichment, CodeGraph dataclass, structure map rendering
    storage/
      oracle_pgq.py       # Oracle PGQ: CREATE PROPERTY GRAPH, MATCH, upsert
      connection.py      # CKG_ORACLE_* env config, pool, oracle-summary
    retrieval/
      hybrid.py           # Lexical anchor → graph reach → PPR pipeline
      pagerank.py         # Personalized PageRank (pure NumPy)
    claude/
      plugin.py           # Agent integration: detect, build, inject
      prompts.py          # System prompt templates
    cli/
      main.py             # CLI: build, load, query, inject, oracle-status
  pi/
    extensions/ckg/       # pi plugin: transparent injection, ckg_* tools, /ckg
  skills/
    ckg/                  # Claude Code / Codex skill definition
    ckg-pi/               # pi-native skill (uses the ckg_* tools)
  .claude-plugin/
    plugin.json           # Claude Code plugin manifest (UserPromptSubmit hook)
    marketplace.json      # Claude Code marketplace
  hooks/
    before_prompt.py      # Claude Code UserPromptSubmit hook
  commands/               # Claude Code /ckg-* slash commands
  scripts/
    postinstall.js        # npm postinstall: bundles the Python venv
  configs/
    default.yaml          # Default configuration
  tests/                  # Test suite (incl. live Oracle tests, CKG_ORACLE_LIVE=1)
  package.json            # npm / pi-package manifest
  pyproject.toml
  install.sh              # One-command installer (pi + Claude Code + Codex)
```

## Sister Projects

- [dl-ai-continual-learning](https://github.com/jasperan/dl-ai-continual-learning) — the 4-module course where CKG was developed and empirically verified. Module 3 teaches structure-aware retrieval from first principles.
- [ironoraclaw](https://github.com/jasperan/ironoraclaw) — Oracle AI Database-powered Rust agent using the same PGQ graph patterns for memory persistence.
- [picooraclaw](https://github.com/jasperan/picooraclaw) — Go-based agent, same Oracle PGQ pattern.
- [oraclaw](https://github.com/jasperan/oraclaw) — TypeScript + Python sidecar agent with Oracle memory.

## Credits

- [Oracle AI Database](https://www.oracle.com/database/) — the PGQ property graph engine that makes in-DB graph traversal possible
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) — the coding agent CKG integrates with transparently
- The [graphify-verification experiment](https://github.com/jasperan/dl-ai-continual-learning/tree/main/ckg_tests) — 7 httpie PRs, 70 headless Claude runs, 1 thesis proven

## License

MIT

---

<div align="center">

[![GitHub](https://img.shields.io/badge/GitHub-jasperan-181717?style=for-the-badge&logo=github&logoColor=white)](https://github.com/jasperan)&nbsp;
[![LinkedIn](https://img.shields.io/badge/LinkedIn-jasperan-0077B5?style=for-the-badge&logo=linkedin&logoColor=white)](https://www.linkedin.com/in/jasperan/)&nbsp;
[![Oracle](https://img.shields.io/badge/Oracle_AI_Database-26ai_Free-F80000?style=for-the-badge&logo=oracle&logoColor=white)](https://www.oracle.com/database/free/)

</div>
