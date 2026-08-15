/**
 * CKG context-map building for pi.
 *
 * Transparent injection strategy (mirrors the Claude Code skill):
 *   - Only act when inside a detected project and the prompt looks like a
 *     coding task (keyword gate) and injection is enabled.
 *   - If a graph cache already exists (.ckg/code_graph.json), run
 *     `ckg inject "<query>" --root <project>` with a short timeout and append
 *     the structure map to the system prompt.
 *   - If no cache exists and autoBuild is on, kick off a background build
 *     (fire-and-forget) and skip injection for this turn — the *next* coding
 *     prompt gets the map.
 *
 * The Python side chooses PGQ automatically when CKG_ORACLE_DSN is set, so the
 * exact same code path runs against Oracle AI Database 26ai Free.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { discoverCli, runCli, installGuidance, type CliSpec } from "./cli";
import { detectProject, hasGraphCache, isCodingPrompt } from "./project";
import { loadSettings, type CkgSettings } from "./config";

/** Per-session in-memory state so we build each project at most once. */
export interface CkgSessionState {
  /** Projects whose background build was already started this session. */
  buildsStarted: Set<string>;
  cli: CliSpec | null;
}

export function newSessionState(): CkgSessionState {
  return { buildsStarted: new Set(), cli: null };
}

const PREAMBLE = `## Code Knowledge Graph (CKG) — Structure-Aware Context

A dependency graph of this codebase (imports, function calls, git co-edits) was
parsed and stored in Oracle PGQ / local cache. The structure map below shows the
*dependency cluster* for this task: anchor files found by lexical match plus the
2-hop reach via imports/calls/co-edits. Changing one file in the cluster often
requires changing its neighbors — start exploration there instead of grepping
the whole filesystem.`;

/**
 * Build a structure map for the current prompt, or null when unavailable.
 * Never throws — returns null on any failure so the agent loop is untouched.
 */
export async function buildMap(
  pi: ExtensionAPI,
  state: CkgSessionState,
  prompt: string,
  cwd: string,
): Promise<string | null> {
  try {
    const settings = loadSettings();
    if (!settings.inject) return null;
    if (settings.keywordGate && !isCodingPrompt(prompt)) return null;

    const project = detectProject(cwd);
    if (!project) return null;

    const cli = state.cli ?? discoverCli();
    if (!cli) return null;
    state.cli = cli;

    // No graph cache yet → background build, skip this turn.
    if (!hasGraphCache(project.root)) {
      if (settings.autoBuild && !state.buildsStarted.has(project.root)) {
        state.buildsStarted.add(project.root);
        void runCli(cli, ["build", project.root, "--pkg-root", projectName(project.root)], {
          timeoutMs: 120_000,
          cwd: project.root,
        });
      }
      return null;
    }

    const result = await runCli(
      cli,
      ["inject", prompt, "--root", project.root],
      { timeoutMs: settings.injectTimeoutMs, cwd: project.root },
    );
    if (result.code !== 0) return null;
    const map = result.stdout.trim();
    if (!map) return null;
    return `${PREAMBLE}\n\n${map}`;
  } catch {
    return null;
  }
}

function projectName(root: string): string {
  const parts = root.split("/").filter(Boolean);
  return parts[parts.length - 1] ?? root;
}

/** Human-readable status block for the /ckg command and ckg_status tool. */
export async function statusBlock(state: CkgSessionState, cwd: string): Promise<string> {
  const cli = state.cli ?? discoverCli();
  const settings = loadSettings(detectProject(cwd)?.root);
  const project = detectProject(cwd);
  const lines: string[] = [];
  lines.push("CKG — Code Knowledge Graph");
  lines.push("");
  lines.push(`CLI:            ${cli ? `${cli.bin} (${cli.source})` : "NOT FOUND"}`);
  if (!cli) {
    lines.push(...installGuidance().split("\n").map((l) => `  ${l}`));
    return lines.join("\n");
  }
  lines.push(`Injection:      ${settings.inject ? "on" : "off"} (CKG_INJECT / .ckg/pi.json)`);
  lines.push(`Auto-build:     ${settings.autoBuild ? "on" : "off"}`);
  lines.push(`Project:        ${project ? `${project.root} (${project.marker})` : "none detected"}`);
  lines.push(`Graph cache:    ${project ? (hasGraphCache(project.root) ? "present" : "missing") : "n/a"}`);

  const oracle = await runCli(cli, ["oracle-status"], { timeoutMs: 15_000, cwd: cwd });
  if (oracle.code === 0) {
    const head = oracle.stdout
      .split("\n")
      .filter((l) => /oracle pgq:|version:|nodes:|edges:|connected:/i.test(l));
    lines.push("Oracle PGQ:");
    for (const l of head) lines.push(`  ${l.trim()}`);
  } else {
    const msg = (oracle.stdout || oracle.stderr).split("\n")[0]?.trim() || "in-memory mode";
    lines.push(`Oracle PGQ:      ${msg}`);
  }

  lines.push("");
  lines.push("Tools: ckg_status · ckg_build · ckg_load · ckg_query · ckg_inject · ckg_oracle_status");
  lines.push("Command: /ckg  ·  Transparent injection on coding prompts (disable: CKG_INJECT=0)");
  return lines.join("\n");
}
