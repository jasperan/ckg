/**
 * CKG HUD — compact footer status segment (enabled / disabled / degraded).
 *
 * Renders via `ctx.ui.setStatus("ckg", ...)` — a persistent footer/status-bar
 * entry (see pi docs: Pattern 4 "Persistent Status Indicator"). The call is
 * fire-and-forget: a no-op in print/JSON modes and ignorable by the client, so
 * headless runs are unaffected. Every path here is try/catch-guarded — the HUD
 * is cosmetic and must never break the agent loop.
 *
 * States:
 *   ● CKG on      — injection enabled (CKG_INJECT / config) and CLI found
 *   ○ CKG off     — injection disabled (CKG_INJECT=0 or .ckg/pi.json inject:false)
 *   ◐ CKG no CLI  — enabled but the Python CLI is missing (tools degrade)
 */
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { existsSync } from "node:fs";
import { discoverCli } from "./cli";
import { loadSettings } from "./config";

export type CkgHudState = "on" | "off" | "no-cli";

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

const _STATE_TEXT: Record<CkgHudState, { color: string; text: string }> = {
  on: { color: "accent", text: "● CKG on" },
  off: { color: "dim", text: "○ CKG off" },
  "no-cli": { color: "warn", text: "◐ CKG no CLI" },
};

let _last: string | undefined;

/** Refresh the footer status if the rendered text changed. Safe to call often. */
export function updateHud(ctx: ExtensionContext | undefined, projectRoot?: string): void {
  try {
    const ui = ctx?.ui as { setStatus?: (k: string, t: string | undefined) => void } | undefined;
    if (!ui?.setStatus) return;
    const state = computeHudState(projectRoot);
    const spec = _STATE_TEXT[state];
    const text = colored(ui, spec.color, spec.text);
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
