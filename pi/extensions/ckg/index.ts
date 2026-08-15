/**
 * CKG — Code Knowledge Graph pi plugin.
 *
 * Transparent background dependency analysis for pi. Parses your codebase into
 * a typed graph (imports / calls / co-edits), stores it in Oracle AI Database
 * 26ai Free PGQ (or a local cache), and injects a compact structure map into
 * the system prompt on coding tasks so the agent finds the right files faster.
 *
 * Features:
 *   - Transparent injection: before_agent_start appends a structure map for
 *     coding prompts (keyword-gated, cached, short timeout, never throws).
 *   - Tools: ckg_status, ckg_build, ckg_load, ckg_query, ckg_inject,
 *     ckg_oracle_status.
 *   - Command: /ckg — status + usage.
 *
 * The Python CLI is required; it is auto-discovered (env CKG_CLI → bundled
 * venv → uv run → PATH). With CKG_ORACLE_DSN set, all retrieval runs through
 * Oracle PGQ (GRAPH_TABLE MATCH); otherwise it falls back to in-memory.
 */
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { join } from "node:path";
import { discoverCli, runCli, installGuidance, type CliSpec } from "./cli";
import { detectProject, hasGraphCache, graphCachePath } from "./project";
import { loadSettings } from "./config";
import { buildMap, newSessionState, statusBlock, type CkgSessionState } from "./context";

export default function (pi: ExtensionAPI) {
  let state: CkgSessionState = newSessionState();

  pi.on("session_start", () => {
    state = newSessionState();
  });

  pi.on("session_shutdown", () => {
    state = newSessionState();
  });

  // ── Transparent injection ─────────────────────────────────────────────────
  pi.on("before_agent_start", async (event, ctx) => {
    if (!event.prompt) return;
    // Skip before doing any work when a map is already present (avoids paying
    // the inject subprocess cost on every prompt and pinning a stale map).
    if (event.systemPrompt.includes("CKG Structure Map")) return;
    const map = await buildMap(pi, state, event.prompt, ctx.cwd);
    if (!map) return;
    const suffix = `\n\n${map}`;
    return { systemPrompt: event.systemPrompt + suffix };
  });

  // ── Helpers ───────────────────────────────────────────────────────────────
  function resolvePath(p: string | undefined, ctx: ExtensionContext): string {
    if (!p) return ctx.cwd;
    return p.startsWith("/") ? p : join(ctx.cwd, p);
  }

  async function execCli(ctx: ExtensionContext, args: string[], timeoutMs = 120_000): Promise<string> {
    const cli: CliSpec | null = state.cli ?? discoverCli();
    if (!cli) return `CKG CLI not found.\n\n${installGuidance()}`;
    state.cli = cli;
    const result = await runCli(cli, args, { timeoutMs, cwd: ctx.cwd });
    if (result.code !== 0) {
      const err = (result.stderr || result.stdout || "unknown error").trim().split("\n").slice(0, 8).join("\n");
      return `ckg ${args.join(" ")} failed (exit ${result.code}):\n${err}`;
    }
    return result.stdout.trim();
  }

  // ── Tools ─────────────────────────────────────────────────────────────────
  pi.registerTool({
    name: "ckg_status",
    label: "CKG Status",
    description:
      "Check whether the Code Knowledge Graph (CKG) is installed, a project is detected, " +
      "the graph cache exists, and Oracle PGQ is configured/reachable.",
    parameters: Type.Object({}),
    async execute(_id, _params, _signal, _onUpdate, ctx) {
      return { content: [{ type: "text", text: await statusBlock(state, ctx.cwd) }], details: {} };
    },
  });

  pi.registerTool({
    name: "ckg_build",
    label: "CKG Build Graph",
    description:
      "Parse a Python project into a code knowledge graph (import/call/co-edit edges) and cache " +
      "it at <project>/.ckg/code_graph.json. Run this once per project before ckg_query/ckg_inject " +
      "for the in-memory path.",
    parameters: Type.Object({
      path: Type.String({ description: "Project or package directory to parse" }),
      pkg_root: Type.Optional(Type.String({ description: "Top-level package name (default: dir name)" })),
    }),
    async execute(_id, params, _signal, _onUpdate, ctx) {
      const args = ["build", resolvePath(params.path, ctx)];
      if (params.pkg_root) args.push("--pkg-root", params.pkg_root);
      return { content: [{ type: "text", text: await execCli(ctx, args) }], details: {} };
    },
  });

  pi.registerTool({
    name: "ckg_load",
    label: "CKG Load into Oracle PGQ",
    description:
      "Parse a Python project and store the code knowledge graph into Oracle AI Database PGQ " +
      "(requires CKG_ORACLE_DSN set). Retrieval then runs GRAPH_TABLE MATCH in the database.",
    parameters: Type.Object({
      path: Type.String({ description: "Project or package directory to parse and load" }),
      pkg_root: Type.Optional(Type.String({ description: "Top-level package name (default: dir name)" })),
      domain: Type.Optional(Type.String({ description: "PGQ domain scope (default: CKG_ORACLE_DOMAIN or 'default')" })),
    }),
    async execute(_id, params, _signal, _onUpdate, ctx) {
      const args = ["load", resolvePath(params.path, ctx)];
      if (params.pkg_root) args.push("--pkg-root", params.pkg_root);
      if (params.domain) args.push("--domain", params.domain);
      return { content: [{ type: "text", text: await execCli(ctx, args, 180_000) }], details: {} };
    },
  });

  pi.registerTool({
    name: "ckg_query",
    label: "CKG Query",
    description:
      "Hybrid retrieval (lexical anchors → graph reach → Personalized PageRank) over the cached " +
      "code graph. Uses Oracle PGQ when CKG_ORACLE_DSN is set, in-memory otherwise. Returns ranked " +
      "files/symbols for a task description.",
    parameters: Type.Object({
      query: Type.String({ description: "Feature description or retrieval query" }),
      graph: Type.Optional(Type.String({ description: "Path to code_graph.json (default: <cwd>/.ckg/code_graph.json)" })),
      top_k: Type.Optional(Type.Number({ description: "Max results (default 10)" })),
      hops: Type.Optional(Type.Number({ description: "Graph reach depth (default 2)" })),
    }),
    async execute(_id, params, _signal, _onUpdate, ctx) {
      const args = ["query", params.query];
      if (params.graph) args.push("--graph", resolvePath(params.graph, ctx));
      if (params.top_k) args.push("--top-k", String(params.top_k));
      if (params.hops) args.push("--hops", String(params.hops));
      return { content: [{ type: "text", text: await execCli(ctx, args) }], details: {} };
    },
  });

  pi.registerTool({
    name: "ckg_inject",
    label: "CKG Inject Context",
    description:
      "Generate a markdown structure map (anchor files + dependency reach) for a task description, " +
      "ready to append to the agent's context. Same output the transparent injection uses.",
    parameters: Type.Object({
      query: Type.String({ description: "Task description" }),
      root: Type.Optional(Type.String({ description: "Project root (default: auto-detected)" })),
    }),
    async execute(_id, params, _signal, _onUpdate, ctx) {
      const args = ["inject", params.query];
      if (params.root) args.push("--root", resolvePath(params.root, ctx));
      return { content: [{ type: "text", text: await execCli(ctx, args) }], details: {} };
    },
  });

  pi.registerTool({
    name: "ckg_oracle_status",
    label: "CKG Oracle Status",
    description:
      "Check Oracle AI Database 26ai Free connectivity, version, and stored graph stats for the " +
      "configured PGQ domain. In-memory mode when CKG_ORACLE_DSN is not set.",
    parameters: Type.Object({}),
    async execute(_id, _params, _signal, _onUpdate, ctx) {
      return { content: [{ type: "text", text: await execCli(ctx, ["oracle-status"], 20_000) }], details: {} };
    },
  });

  // ── Command ───────────────────────────────────────────────────────────────
  pi.registerCommand("ckg", {
    description: "CKG — Code Knowledge Graph status and usage",
    handler: async (_args, ctx) => {
      const out = await statusBlock(state, ctx.cwd);
      ctx.ui.notify(out.split("\n").slice(0, 3).join(" · "), "info");
      ctx.ui.setWidget("ckg", out.split("\n").slice(0, 12));
    },
  });

  // ── Session-scoped cleanup for background builds ──────────────────────────
  // (background `ckg build` processes are short-lived; nothing else to clean)
}

/** Export state for tests/tools that import the module directly. */
export { newSessionState, graphCachePath };
