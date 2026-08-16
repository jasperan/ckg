/**
 * CKG HUD — footer status segment + transient activity feedback.
 *
 * Renders via `ctx.ui.setStatus("ckg", ...)` (persistent footer entry) and
 * `ctx.ui.notify(...)` (toast). Both are fire-and-forget and no-ops in
 * print/JSON modes, so headless runs are unaffected. Every path here is
 * try/catch-guarded — the HUD is cosmetic and must never break the agent loop.
 *
 * Base states:
 *   ● CKG on      — injection enabled (CKG_INJECT / config) and CLI found
 *   ○ CKG off     — injection disabled (CKG_INJECT=0 or .ckg/pi.json inject:false)
 *   ◐ CKG no CLI  — enabled but the Python CLI is missing (tools degrade)
 *
 * Activity overlay (per-call, shown until the next update):
 *   ⚙ CKG building graph…            — background build kicked off
 *   CKG analyzing… / querying…       — transient busy text
 *   ✓ CKG map injected (n anchors)   — task feedback after success
 */
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { existsSync } from "node:fs";
import { discoverCli } from "./cli";
import { loadSettings } from "./config";

export type CkgHudState = "on" | "off" | "no-cli";

/** Transient overlay for the footer segment. Callers pass one per update. */
export type CkgHudActivity =
  | { kind: "working"; detail: string } // busy text, e.g. "querying…"
  | { kind: "done"; detail: string } // success feedback, e.g. "map injected (5 anchors)"
  | { kind: "building" } // background graph build running
  | { kind: "idle" }; // force back to the base state

export function computeHudState(projectRoot?: string): CkgHudState {
  try {
    const settings = loadSettings(projectRoot);
    if (!settings.inject) return "off";
    const cli = discoverCli();
    if (!cli) return "no-cli";
    // An env override pointing at a nonexistent file doesn't count as installed.
    if (cli.source === "env CKG_CLI") {
      const envPath = process.env.CKG_CLI || "";
      if (envPath.includes("/") && !existsSync(envPath)) return "no-cli";
    }
    return "on";
  } catch {
    return "no-cli"; // cannot even evaluate settings -> degrade, never throw
  }
}

function colored(ui: unknown, color: string, text: string): string {
  try {
    const theme = (ui as { theme?: { fg?: (c: string, t: string) => string } }).theme;
    if (typeof theme?.fg === "function") return theme.fg(color, text);
  } catch {
    /* fall through to plain text */
  }
  return text;
}

function render(ui: unknown, state: CkgHudState, activity?: CkgHudActivity): string {
  switch (activity?.kind) {
    case "building":
      return colored(ui, "accent", "⚙ CKG building graph…");
    case "working":
      return colored(ui, "accent", `CKG ${activity.detail}`);
    case "done":
      return colored(ui, "accent", `✓ CKG ${activity.detail}`);
    default:
      break;
  }
  const base: Record<CkgHudState, { color: string; text: string }> = {
    on: { color: "accent", text: "● CKG on" },
    off: { color: "dim", text: "○ CKG off" },
    "no-cli": { color: "warn", text: "◐ CKG no CLI" },
  };
  const spec = base[state];
  return colored(ui, spec.color, spec.text);
}

let _last: string | undefined;

/** Refresh the footer status. Safe to call often — re-renders only on change. */
export function updateHud(
  ctx: ExtensionContext | undefined,
  projectRoot?: string,
  activity?: CkgHudActivity,
): void {
  try {
    const ui = ctx?.ui as { setStatus?: (k: string, t: string | undefined) => void } | undefined;
    if (!ui?.setStatus) return;
    const text = render(ui, computeHudState(projectRoot), activity);
    if (text === _last) return;
    _last = text;
    ui.setStatus("ckg", text);
  } catch {
    /* HUD is cosmetic — never break the loop */
  }
}

/** Clear the footer status (e.g. on session shutdown). */
export function clearHud(ctx: ExtensionContext | undefined): void {
  try {
    const ui = ctx?.ui as { setStatus?: (k: string, t: string | undefined) => void } | undefined;
    if (!ui?.setStatus) return;
    _last = undefined;
    ui.setStatus("ckg", undefined);
  } catch {
    /* cosmetic */
  }
}

/** Fire a one-time toast notification (guarded, cosmetic). */
export function notifyHud(
  ctx: ExtensionContext | undefined,
  message: string,
  type: "info" | "warning" = "info",
): void {
  try {
    const ui = ctx?.ui as { notify?: (m: string, t?: string) => void } | undefined;
    if (typeof ui?.notify === "function") ui.notify(message, type);
  } catch {
    /* cosmetic */
  }
}
