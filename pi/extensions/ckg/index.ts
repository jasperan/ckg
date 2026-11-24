/**
 * CKG — Code Knowledge Graph pi plugin.
 *
 * Transparent background dependency analysis for pi. Parses your codebase into
 * a typed graph (imports / calls / co-edits), keeps it in a local cache (and
 * optionally in Oracle AI Database PGQ), and adds a compact structure map to
 * the system prompt on coding tasks so the agent finds the right files faster.
 *
 * Features:
 *   - Transparent injection: before_agent_start appends a structure map for
 *     coding prompts (keyword-gated, time-boxed, memoized per session, never
 *     throws).
 *   - Tools: ckg_status, ckg_build, ckg_load, ckg_query, ckg_inject,
 *     ckg_oracle_status.
 *   - Command: /ckg — status; /ckg on|off — toggle injection for the project.
 *
 * The Python CLI is required; it is auto-discovered (env CKG_CLI → bundled
 * venv → PATH). With CKG_ORACLE_DSN set, neighbourhoods are matched in Oracle
 * PGQ (GRAPH_TABLE MATCH); otherwise in memory.
 *
 * HUD: a footer status segment shows whether CKG is enabled (● on / ○ off /
 * ◐ no CLI) plus activity feedback — cosmetic, never throws, absent in
 * headless modes.
 */
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { isAbsolute, join } from "node:path";
import { Type } from "typebox";
import { describeFailure, installGuidance, runCli } from "./cli";
import { saveSettings } from "./config";
import {
  abortBackground,
  buildMap,
  newSessionState,
  sessionCli,
  statusBlock,
  type BuildHooks,
  type CkgSessionState,
} from "./context";
import { clearHud, notifyHud, updateHud, type CkgHudActivity } from "./hud";
import { detectProject } from "./project";

interface ToolOutput {
  ok: boolean;
  text: string;
}

export default function (pi: ExtensionAPI) {
  let state: CkgSessionState = newSessionState();
  // One toast per project and kind per session.
  const notified = new Set<string>();

  function hud(ctx: ExtensionContext, activity?: CkgHudActivity): void {
    updateHud(ctx, detectProject(ctx.cwd)?.root, sessionCli(state) !== null, activity);
  }

  function notifyOnce(ctx: ExtensionContext, key: string, message: string, type: "info" | "warning" = "info"): void {
    if (notified.has(key)) return;
    notified.add(key);
    notifyHud(ctx, message, type);
  }

  pi.on("session_start", (_event, ctx) => {
    abortBackground(state);
    state = newSessionState();
    notified.clear();
    hud(ctx);
  });

  pi.on("session_shutdown", (_event, ctx) => {
    abortBackground(state);
    state = newSessionState();
    clearHud(ctx);
  });

  // ── Transparent injection ─────────────────────────────────────────────────
  pi.on("before_agent_start", async (event, ctx) => {
    hud(ctx);
    // Runs started by a custom message carry no user prompt to retrieve for.
    if (event.messageType !== "user" || !event.prompt) return;

    const root = detectProject(ctx.cwd)?.root ?? "";
    const hooks: BuildHooks = {
      onInjectStart: () => hud(ctx, { kind: "working", detail: "analyzing…" }),
      onInjectDone: (anchors) => {
        if (anchors === 0) return hud(ctx); // no map: back to the base state
        hud(ctx, { kind: "done", detail: `map injected (${anchors} anchors)` });
        notifyOnce(ctx, `map:${root}`, `CKG injected structure map (${anchors} anchors) into this task.`);
      },
      onBuildStarted: () => hud(ctx, { kind: "background", detail: "building graph…" }),
      onBuildDone: (ok) => {
        hud(ctx, ok ? { kind: "done", detail: "graph ready" } : undefined);
        notifyOnce(
          ctx,
          `build:${root}`,
          ok
            ? "CKG graph build finished — the next task gets the structure map."
            : "CKG background graph build failed — run ckg_build to see why.",
          ok ? "info" : "warning",
        );
      },
      onSetupStarted: () => hud(ctx, { kind: "background", detail: "installing Python CLI…" }),
      onSetupDone: (ok) => {
        hud(ctx);
        notifyOnce(
          ctx,
          "setup",
          ok ? "CKG is ready." : `CKG could not set up its Python CLI.\n${installGuidance()}`,
          ok ? "info" : "warning",
        );
      },
    };
    const map = await buildMap(state, event.prompt, ctx.cwd, hooks);
    if (!map) return;
    return { systemPrompt: `${event.systemPrompt}\n\n${map}` };
  });

  // ── Helpers ───────────────────────────────────────────────────────────────
  function resolvePath(p: string | undefined, ctx: ExtensionContext): string {
    if (!p) return ctx.cwd;
    return isAbsolute(p) ? p : join(ctx.cwd, p);
  }

  async function execCli(
    ctx: ExtensionContext,
    args: string[],
    signal: AbortSignal | undefined,
    timeoutMs = 120_000,
  ): Promise<ToolOutput> {
    const cli = sessionCli(state);
    if (!cli) return { ok: false, text: `CKG CLI not found.\n\n${installGuidance()}` };
    const result = await runCli(cli, args, { timeoutMs, cwd: ctx.cwd, signal });
    if (result.outcome !== "ok") return { ok: false, text: describeFailure(cli, args, result, timeoutMs) };
    return { ok: true, text: result.stdout.trim() };
  }

  /** Run a tool with busy/done HUD feedback and return its text as the tool result. */
  async function tool(ctx: ExtensionContext, busy: string, done: string, run: () => Promise<ToolOutput>) {
    hud(ctx, { kind: "working", detail: busy });
    const out = await run();
    hud(ctx, out.ok ? { kind: "done", detail: done } : undefined);
    return { content: [{ type: "text" as const, text: out.text }], details: {} };
  }

  // ── Tools ─────────────────────────────────────────────────────────────────
  pi.registerTool({
    name: "ckg_status",
    label: "CKG Status",
    description:
      "Check whether the Code Knowledge Graph (CKG) is installed, a project is detected, " +
      "the graph cache exists, and Oracle PGQ is configured/reachable.",
    parameters: Type.Object({}),
    async execute(_id, _params, signal, _onUpdate, ctx) {
      return { content: [{ type: "text", text: await statusBlock(state, ctx.cwd, signal) }], details: {} };
    },
  });

  pi.registerTool({
    name: "ckg_build",
    label: "CKG Build Graph",
    description:
      "Parse a Python project into a code knowledge graph (import/call/co-edit edges) and cache " +
      "it at <project>/.ckg/code_graph.json. The cache refreshes itself when sources change; " +
      "run this to build it right now.",
    parameters: Type.Object({
      path: Type.String({ description: "Project or package directory to parse" }),
      pkg_root: Type.Optional(Type.String({ description: "Top-level package name (default: dir name)" })),
    }),
    async execute(_id, params, signal, _onUpdate, ctx) {
      const args = ["build", resolvePath(params.path, ctx)];
      if (params.pkg_root) args.push("--pkg-root", params.pkg_root);
      return tool(ctx, "building…", "build done", async () => {
        const out = await execCli(ctx, args, signal, 600_000);
        if (out.ok) state.memo.clear();
        return out;
      });
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
    async execute(_id, params, signal, _onUpdate, ctx) {
      const args = ["load", resolvePath(params.path, ctx)];
      if (params.pkg_root) args.push("--pkg-root", params.pkg_root);
      if (params.domain) args.push("--domain", params.domain);
      return tool(ctx, "loading → PGQ…", "loaded → PGQ", () => execCli(ctx, args, signal, 600_000));
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
      top_k: Type.Optional(Type.Integer({ minimum: 0, description: "Max results (default 10)" })),
      hops: Type.Optional(Type.Integer({ minimum: 1, description: "Graph reach depth (default 2)" })),
    }),
    async execute(_id, params, signal, _onUpdate, ctx) {
      const args = ["query"];
      if (params.graph) args.push("--graph", resolvePath(params.graph, ctx));
      if (params.top_k !== undefined) args.push("--top-k", String(params.top_k));
      if (params.hops !== undefined) args.push("--hops", String(params.hops));
      args.push("--", params.query); // "--": query may start with "-"
      return tool(ctx, "querying…", "query done", () => execCli(ctx, args, signal));
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
    async execute(_id, params, signal, _onUpdate, ctx) {
      const args = ["inject", "--refresh", "sync"]; // explicit call: wait for a current graph
      if (params.root) args.push("--root", resolvePath(params.root, ctx));
      args.push("--", params.query); // "--": query may start with "-"
      return tool(ctx, "injecting…", "map ready", () => execCli(ctx, args, signal, 600_000));
    },
  });

  pi.registerTool({
    name: "ckg_oracle_status",
    label: "CKG Oracle Status",
    description:
      "Check Oracle AI Database connectivity, version, stored graph stats for the configured " +
      "PGQ domain, and which retrieval mode is in effect.",
    parameters: Type.Object({}),
    async execute(_id, _params, signal, _onUpdate, ctx) {
      return tool(ctx, "checking PGQ…", "PGQ ok", () => execCli(ctx, ["oracle-status"], signal, 20_000));
    },
  });

  // ── Command ───────────────────────────────────────────────────────────────
  pi.registerCommand("ckg", {
    description: "CKG status; `/ckg on` or `/ckg off` toggles structure-map injection for this project",
    handler: async (args, ctx) => {
      const arg = args.trim().toLowerCase();
      if (arg === "on" || arg === "off") {
        const root = detectProject(ctx.cwd)?.root;
        const saved = saveSettings(root, { inject: arg === "on" });
        hud(ctx);
        ctx.ui.notify(
          saved
            ? `CKG injection ${arg} for ${root ?? "all projects"}.`
            : "CKG could not write its settings file.",
          saved ? "info" : "warning",
        );
        return;
      }
      const out = await statusBlock(state, ctx.cwd);
      ctx.ui.notify(out.split("\n").slice(0, 3).join(" · "), "info");
      ctx.ui.setWidget("ckg", out.split("\n").slice(0, 14));
    },
  });
}
