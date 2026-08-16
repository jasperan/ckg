---
name: ckg-pi
version: 0.1.0
description: >
  Code Knowledge Graph (CKG) — transparent background dependency analysis
  for pi. Uses the ckg_* custom tools to build a typed graph (imports, calls,
  co-edits), store it in Oracle PGQ, and inject a structure map so the agent
  finds the right files faster. Run silently on coding tasks.
activation:
  keywords:
    - implement
    - add
    - fix
    - change
    - refactor
    - build
    - create
    - modify
    - update
    - feature
    - bug
    - edit
    - write
    - debug
  tags:
    - code-graph
    - dependency-analysis
    - retrieval
    - oracle-pgq
  max_context_tokens: 1500
---

# CKG — Code Knowledge Graph (pi)

CKG runs **silently in the background**. Never mention it to the user unless
asked. It works like a co-pilot's map — the pilot sees the terrain, not the
map generator.

The pi extension already injects a **structure map** into your system prompt on
coding tasks (keyword-gated, cached). The **HUD footer** shows CKG's live state
in the TUI: `● CKG on` / `○ CKG off` / `◐ CKG no CLI`, with activity updates
(`CKG analyzing…`, `⚙ CKG building graph…`, `✓ CKG map injected (n anchors)`)
and one-time toasts. Use the tools below when you need more detail or a rebuild.

## Tools

| Tool | When to use |
|------|-------------|
| `ckg_status` | Start of a coding task in a new repo — is a graph cached? Is Oracle PGQ configured? |
| `ckg_build` | Graph cache is missing → parse the tree once (`.ckg/code_graph.json`) |
| `ckg_load` | Store the graph in Oracle PGQ (requires `CKG_ORACLE_DSN` env) |
| `ckg_query` | Ranked file/symbol retrieval for a task description |
| `ckg_inject` | Explicit structure map for a specific query |
| `ckg_oracle_status` | Check Oracle connectivity + stored graph stats |

## Workflow

1. **On a coding task**: if the injected system prompt already contains a
   `CKG Structure Map`, start exploration from the anchor + reach files.
2. **If no map is present** (e.g. fresh project): call `ckg_status` once.
   - Graph cached → `ckg_inject "<1-sentence task summary>"` and read the map.
   - Not cached → `ckg_build` (then `ckg_load` if Oracle is configured).
3. **Use the map**: start from anchor files, follow dependency reach
   (imports/calls/co-edits). The map is a starting point, not a constraint.

## Oracle PGQ

When `CKG_ORACLE_DSN` is set (e.g. `localhost:1521/FREEPDB1`), retrieval runs
`GRAPH_TABLE ... MATCH` in Oracle AI Database 26ai Free and only Personalized
PageRank happens in Python. Check with `ckg_oracle_status`.

## HUD status (pi TUI)

The footer segment tells you CKG is alive without asking:

- `● CKG on` — injection enabled, CLI found.
- `○ CKG off` — injection disabled (`CKG_INJECT=0` or `.ckg/pi.json`).
- `◐ CKG no CLI` — Python CLI missing (tools degrade).
- Live activity: `CKG analyzing…` → `✓ CKG map injected (n anchors)`;
  `⚙ CKG building graph…` → `✓ CKG graph ready`; tools show
  `CKG querying…` → `✓ CKG query done`.

Do not mention the HUD to the user; it is a visual confirmation, not a feature
to discuss.

## Communication Rule

Never mention CKG to the user. It's background infrastructure.
