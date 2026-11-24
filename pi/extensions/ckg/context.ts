/**
 * CKG context-map building for pi.
 *
 * Transparent injection strategy (same as the Claude Code hook):
 *   - Only act inside a detected project, when injection is enabled and the
 *     prompt looks like a coding task.
 *   - With a graph cache present, run `ckg inject` with a short timeout and
 *     return the structure map. The CLI itself notices a stale cache: it
 *     serves the map from it and rebuilds in the background.
 *   - Without a cache (and autoBuild on), start a background build and skip
 *     this turn — the next coding prompt gets the map.
 *   - Without a CLI, set the bundled Python environment up in the background
 *     once (installs that skipped npm scripts never ran postinstall).
 *
 * The Python side uses Oracle PGQ automatically when CKG_ORACLE_DSN is set.
 */
import { existsSync } from "node:fs";
import { join } from "node:path";
import {
  discoverCli,
  installGuidance,
  needsPythonSetup,
  packageRoot,
  runCli,
  runFile,
  type CliSpec,
} from "./cli";
import { loadSettings } from "./config";
import { detectProject, graphCacheStamp, isCodingPrompt } from "./project";

const MAX_QUERY_CHARS = 400;
const MEMO_MAX = 32;
const RETRY_TIMEOUT_MS = 4_000;
const BUILD_TIMEOUT_MS = 10 * 60_000;
const SETUP_TIMEOUT_MS = 10 * 60_000;

/** Per-session state. */
export interface CkgSessionState {
  /** Discovered CLI; `undefined` = not looked up yet, `null` = none found. */
  cli: CliSpec | null | undefined;
  /** Projects whose background build was already started this session. */
  buildsStarted: Set<string>;
  /** The Python environment setup was already attempted this session. */
  setupStarted: boolean;
  /** Abort handles of background processes, cancelled on session shutdown. */
  background: Set<AbortController>;
  /** Recent structure maps, keyed by project, graph cache identity and query. */
  memo: Map<string, string>;
}

export function newSessionState(): CkgSessionState {
  return { cli: undefined, buildsStarted: new Set(), setupStarted: false, background: new Set(), memo: new Map() };
}

/** Stop every background process of the session. */
export function abortBackground(state: CkgSessionState): void {
  for (const controller of state.background) controller.abort();
  state.background.clear();
}

/** The CLI for this session, discovered once (discovery spawns a process). */
export function sessionCli(state: CkgSessionState): CliSpec | null {
  if (state.cli === undefined) state.cli = discoverCli();
  return state.cli;
}

/** Activity hooks so the caller can surface background work in the HUD. */
export interface BuildHooks {
  onInjectStart?: () => void;
  /** `ckg inject` finished; anchors = anchor-file count, 0 when there is no map. */
  onInjectDone?: (anchors: number) => void;
  onBuildStarted?: () => void;
  onBuildDone?: (ok: boolean) => void;
  onSetupStarted?: () => void;
  onSetupDone?: (ok: boolean) => void;
}

/** Count anchor-file entries in a `ckg inject` structure map. */
export function countAnchors(map: string): number {
  const idx = map.indexOf("### Anchor Files");
  if (idx < 0) return 0;
  let n = 0;
  for (const line of map.slice(idx).split("\n")) {
    if (/^- `/.test(line)) n++;
    else if (n > 0 && /^#{3,4} /.test(line)) break; // next section
  }
  return n;
}

const PREAMBLE = `## Code Knowledge Graph (CKG) — Structure-Aware Context

A dependency graph of this codebase (imports, function calls, git co-edits) was
parsed ahead of time. The structure map below shows the *dependency cluster*
for this task: anchor files found by lexical match plus their reach via
imports/calls/co-edits. Changing one file in the cluster often requires
changing its neighbors — start exploration there instead of grepping the
whole filesystem.`;

function inBackground(
  state: CkgSessionState,
  start: (signal: AbortSignal) => Promise<boolean>,
  done?: (ok: boolean) => void,
): void {
  const controller = new AbortController();
  state.background.add(controller);
  void start(controller.signal).then((ok) => {
    state.background.delete(controller);
    if (!controller.signal.aborted) done?.(ok);
  });
}

/** Set up the bundled Python environment once per session, in the background. */
function startPythonSetup(state: CkgSessionState, hooks?: BuildHooks): void {
  if (state.setupStarted || process.env.CKG_SKIP_SETUP === "1" || !needsPythonSetup()) return;
  const script = join(packageRoot(), "scripts", "postinstall.js");
  if (!existsSync(script)) return;
  state.setupStarted = true;
  hooks?.onSetupStarted?.();
  inBackground(
    state,
    async (signal) => {
      const result = await runFile(process.execPath, [script], {
        cwd: packageRoot(),
        timeoutMs: SETUP_TIMEOUT_MS,
        signal,
      });
      state.cli = undefined; // look again: the venv may exist now
      return result.outcome === "ok" && sessionCli(state) !== null;
    },
    hooks?.onSetupDone,
  );
}

/**
 * Build a structure map for the current prompt, or null when unavailable.
 * Never throws — returns null on any failure so the agent loop is untouched.
 */
export async function buildMap(
  state: CkgSessionState,
  prompt: string,
  cwd: string,
  hooks?: BuildHooks,
): Promise<string | null> {
  try {
    const project = detectProject(cwd);
    if (!project) return null;

    const settings = loadSettings(project.root);
    if (!settings.inject) return null;
    if (settings.keywordGate && !isCodingPrompt(prompt)) return null;

    const cli = sessionCli(state);
    if (!cli) {
      startPythonSetup(state, hooks);
      return null;
    }

    const stamp = graphCacheStamp(project.root);
    if (stamp === null) {
      if (settings.autoBuild && !state.buildsStarted.has(project.root)) {
        state.buildsStarted.add(project.root);
        hooks?.onBuildStarted?.();
        inBackground(
          state,
          async (signal) =>
            (await runCli(cli, ["build", project.root, "--quiet"], {
              timeoutMs: BUILD_TIMEOUT_MS,
              cwd: project.root,
              signal,
            })).outcome === "ok",
          hooks?.onBuildDone,
        );
      }
      return null;
    }

    // One line, bounded: the prompt is echoed into the map and may be pasted text.
    const query = prompt.replace(/\s+/g, " ").trim().slice(0, MAX_QUERY_CHARS);
    const key = `${project.root}\0${stamp}\0${query}`;
    const cached = state.memo.get(key);
    if (cached !== undefined) {
      hooks?.onInjectDone?.(countAnchors(cached));
      return cached;
    }

    hooks?.onInjectStart?.();
    // "--refresh background": never build inside this call. "--": the query may start with "-".
    const base = ["inject", "--root", project.root, "--refresh", "background"];
    let result = await runCli(cli, [...base, "--", query], {
      timeoutMs: settings.injectTimeoutMs,
      cwd: project.root,
    });
    if (result.outcome === "timeout") {
      // The usual cause is a slow database: retry once on the local graph only.
      result = await runCli(cli, [...base, "--no-pgq", "--", query], {
        timeoutMs: RETRY_TIMEOUT_MS,
        cwd: project.root,
      });
    }
    const body = result.outcome === "ok" ? result.stdout.trim() : "";
    if (!body) {
      hooks?.onInjectDone?.(0);
      return null;
    }

    const map = `${PREAMBLE}\n\n${body}`;
    if (state.memo.size >= MEMO_MAX) {
      const oldest = state.memo.keys().next().value;
      if (oldest !== undefined) state.memo.delete(oldest);
    }
    state.memo.set(key, map);
    hooks?.onInjectDone?.(countAnchors(map));
    return map;
  } catch {
    hooks?.onInjectDone?.(0);
    return null;
  }
}

/** Human-readable status block for the /ckg command and ckg_status tool. */
export async function statusBlock(state: CkgSessionState, cwd: string, signal?: AbortSignal): Promise<string> {
  const cli = sessionCli(state);
  const project = detectProject(cwd);
  const settings = loadSettings(project?.root);
  const lines: string[] = ["CKG — Code Knowledge Graph", ""];
  lines.push(`CLI:            ${cli ? `${cli.bin} (${cli.source})` : "NOT FOUND"}`);
  if (!cli) {
    lines.push(...installGuidance().split("\n").map((l) => `  ${l}`));
    return lines.join("\n");
  }
  lines.push(`Injection:      ${settings.inject ? "on" : "off"} (/ckg on|off · CKG_INJECT · .ckg/pi.json)`);
  lines.push(`Auto-build:     ${settings.autoBuild ? "on" : "off"}`);
  lines.push(`Project:        ${project ? `${project.root} (${project.marker})` : "none detected"}`);
  lines.push(`Graph cache:    ${project ? (graphCacheStamp(project.root) ? "present" : "missing") : "n/a"}`);

  const oracle = await runCli(cli, ["oracle-status"], { timeoutMs: 15_000, cwd, signal });
  const report = oracle.stdout
    .split("\n")
    .filter((l) => /oracle pgq:|version:|nodes:|edges:|connect|retrieval mode:/i.test(l));
  if (report.length > 0) {
    lines.push("Oracle PGQ:");
    for (const l of report) lines.push(`  ${l.trim()}`);
  } else {
    lines.push(`Oracle PGQ:     status unavailable (${oracle.outcome})`);
  }

  lines.push("");
  lines.push("Tools: ckg_status · ckg_build · ckg_load · ckg_query · ckg_inject · ckg_oracle_status");
  lines.push("Command: /ckg [on|off]  ·  Transparent injection on coding prompts");
  return lines.join("\n");
}
