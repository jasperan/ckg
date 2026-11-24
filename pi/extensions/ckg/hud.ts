/**
 * CKG HUD — footer status segment + transient activity feedback.
 *
 * Renders via `ctx.ui.setStatus("ckg", ...)` (persistent footer entry) and
 * `ctx.ui.notify(...)` (toast). Every path is guarded: the HUD is cosmetic
 * and must never break the agent loop.
 *
 * Base states:
 *   ● CKG on      — injection enabled and CLI found
 *   ○ CKG off     — injection disabled (CKG_INJECT=0 or .ckg/pi.json inject:false)
 *   ◐ CKG no CLI  — enabled but the Python CLI is missing (tools degrade)
 *
 * Activity overlay (shown until the next update):
 *   ⚙ CKG building graph… / installing…  — background work running
 *   CKG analyzing… / querying…           — transient busy text
 *   ✓ CKG map injected (n anchors)       — feedback after success
 */
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { loadSettings } from "./config";

export type CkgHudState = "on" | "off" | "no-cli";

/** Transient overlay for the footer segment. Callers pass one per update. */
export type CkgHudActivity =
  | { kind: "working"; detail: string } // busy text, e.g. "querying…"
  | { kind: "done"; detail: string } // success feedback, e.g. "map injected (5 anchors)"
  | { kind: "background"; detail: string }; // long-running background work

export function computeHudState(projectRoot: string | undefined, hasCli: boolean): CkgHudState {
  if (!loadSettings(projectRoot).inject) return "off";
  return hasCli ? "on" : "no-cli";
}

const BASE: Record<CkgHudState, { color: "accent" | "dim" | "warning"; text: string }> = {
  on: { color: "accent", text: "● CKG on" },
  off: { color: "dim", text: "○ CKG off" },
  "no-cli": { color: "warning", text: "◐ CKG no CLI" },
};

function render(ctx: ExtensionContext, state: CkgHudState, activity?: CkgHudActivity): string {
  const theme = ctx.ui.theme;
  switch (activity?.kind) {
    case "background":
      return theme.fg("accent", `⚙ CKG ${activity.detail}`);
    case "working":
      return theme.fg("accent", `CKG ${activity.detail}`);
    case "done":
      return theme.fg("accent", `✓ CKG ${activity.detail}`);
    default:
      return theme.fg(BASE[state].color, BASE[state].text);
  }
}

let last: string | undefined;

/**
 * Refresh the footer status. Without an activity it shows the base state, so
 * calling it after work ends clears any busy text. Re-renders only on change.
 */
export function updateHud(
  ctx: ExtensionContext,
  projectRoot: string | undefined,
  hasCli: boolean,
  activity?: CkgHudActivity,
): void {
  try {
    if (!ctx.hasUI) return;
    const text = render(ctx, computeHudState(projectRoot, hasCli), activity);
    if (text === last) return;
    last = text;
    ctx.ui.setStatus("ckg", text);
  } catch {
    /* cosmetic */
  }
}

/** Clear the footer status (e.g. on session shutdown). */
export function clearHud(ctx: ExtensionContext): void {
  try {
    last = undefined;
    if (ctx.hasUI) ctx.ui.setStatus("ckg", undefined);
  } catch {
    /* cosmetic */
  }
}

/** Show a toast notification (guarded, cosmetic). */
export function notifyHud(ctx: ExtensionContext, message: string, type: "info" | "warning" = "info"): void {
  try {
    if (ctx.hasUI) ctx.ui.notify(message, type);
  } catch {
    /* cosmetic */
  }
}
