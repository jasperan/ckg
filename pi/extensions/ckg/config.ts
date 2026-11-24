/**
 * CKG settings for the pi extension.
 *
 * Precedence: CKG_INJECT / CKG_AUTOBUILD env > project `.ckg/pi.json` >
 * global `~/.config/ckg/pi.json` > defaults. Values of the wrong type and
 * unknown keys are ignored.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

export interface CkgSettings {
  /** Transparent structure-map injection on coding prompts. */
  inject: boolean;
  /** Build the graph in the background when missing (that prompt gets no map). */
  autoBuild: boolean;
  /** Skip injection when the prompt does not look like a coding task. */
  keywordGate: boolean;
  /** Max milliseconds to wait for `ckg inject` before skipping. */
  injectTimeoutMs: number;
}

const DEFAULTS: CkgSettings = {
  inject: true,
  autoBuild: true,
  keywordGate: true,
  injectTimeoutMs: 6_000,
};

const TIMEOUT_MIN_MS = 1_000;
const TIMEOUT_MAX_MS = 30_000;

const GLOBAL_SETTINGS_FILE = join(homedir(), ".config", "ckg", "pi.json");

function readJson(file: string): Record<string, unknown> {
  try {
    if (existsSync(file)) {
      const data: unknown = JSON.parse(readFileSync(file, "utf8"));
      if (data && typeof data === "object" && !Array.isArray(data)) return data as Record<string, unknown>;
    }
  } catch {
    /* unreadable or corrupt: same as absent */
  }
  return {};
}

function asBoolean(value: unknown): boolean | undefined {
  if (typeof value === "boolean") return value;
  if (typeof value === "string") {
    const v = value.trim().toLowerCase();
    if (["1", "true", "on", "yes"].includes(v)) return true;
    if (["0", "false", "off", "no"].includes(v)) return false;
  }
  return undefined;
}

function apply(settings: CkgSettings, raw: Record<string, unknown>): void {
  for (const key of ["inject", "autoBuild", "keywordGate"] as const) {
    const value = asBoolean(raw[key]);
    if (value !== undefined) settings[key] = value;
  }
  const timeout = raw.injectTimeoutMs;
  if (typeof timeout === "number" && Number.isFinite(timeout)) {
    settings.injectTimeoutMs = Math.min(TIMEOUT_MAX_MS, Math.max(TIMEOUT_MIN_MS, Math.round(timeout)));
  }
}

export function loadSettings(projectRoot?: string): CkgSettings {
  const settings: CkgSettings = { ...DEFAULTS };
  apply(settings, readJson(GLOBAL_SETTINGS_FILE));
  if (projectRoot) apply(settings, readJson(join(projectRoot, ".ckg", "pi.json")));
  apply(settings, { inject: process.env.CKG_INJECT, autoBuild: process.env.CKG_AUTOBUILD });
  return settings;
}

/**
 * Persist a settings change to the project file (or the global one when no
 * project is given). Returns false when the file could not be written.
 */
export function saveSettings(projectRoot: string | undefined, patch: Partial<CkgSettings>): boolean {
  const file = projectRoot ? join(projectRoot, ".ckg", "pi.json") : GLOBAL_SETTINGS_FILE;
  try {
    const dir = dirname(file);
    const created = !existsSync(dir);
    mkdirSync(dir, { recursive: true });
    // A `.ckg/` created here holds machine-local state: keep it out of git.
    if (projectRoot && created) writeFileSync(join(dir, ".gitignore"), "*\n");
    writeFileSync(file, JSON.stringify({ ...readJson(file), ...patch }, null, 2) + "\n");
    return true;
  } catch {
    return false;
  }
}
