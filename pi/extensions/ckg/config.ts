/**
 * CKG settings: transparent-injection toggle.
 *
 * Precedence: CKG_INJECT env > project .ckg/pi.json > global ~/.config/ckg/pi.json > default on.
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { homedir } from "node:os";

export interface CkgSettings {
  /** Transparent structure-map injection on every coding prompt. */
  inject: boolean;
  /** Build the graph in the background when missing (first prompt is skipped). */
  autoBuild: boolean;
  /** Injection skip when the prompt has no coding keywords. */
  keywordGate: boolean;
  /** Max milliseconds to wait for `ckg inject` before skipping. */
  injectTimeoutMs: number;
}

const DEFAULTS: CkgSettings = {
  inject: true,
  autoBuild: true,
  keywordGate: true,
  injectTimeoutMs: 12_000,
};

function projectSettingsFile(root: string): string {
  return join(root, ".ckg", "pi.json");
}

function globalSettingsFile(): string {
  return join(homedir(), ".config", "ckg", "pi.json");
}

function readJson(file: string): Partial<CkgSettings> | null {
  try {
    if (existsSync(file)) return JSON.parse(readFileSync(file, "utf8"));
  } catch {
    /* corrupt/ignored */
  }
  return null;
}

export function loadSettings(projectRoot?: string): CkgSettings {
  const s: CkgSettings = { ...DEFAULTS };
  const global = readJson(globalSettingsFile());
  if (global) Object.assign(s, global);
  if (projectRoot) {
    const local = readJson(projectSettingsFile(projectRoot));
    if (local) Object.assign(s, local);
  }
  const env = process.env.CKG_INJECT;
  if (env !== undefined) s.inject = env !== "0" && env.toLowerCase() !== "false";
  const envBuild = process.env.CKG_AUTOBUILD;
  if (envBuild !== undefined) s.autoBuild = envBuild !== "0" && envBuild.toLowerCase() !== "false";
  return s;
}

export function saveSettings(projectRoot: string | undefined, patch: Partial<CkgSettings>): CkgSettings {
  const file = projectRoot ? projectSettingsFile(projectRoot) : globalSettingsFile();
  try {
    mkdirSync(dirname(file), { recursive: true });
    const prev = readJson(file) ?? {};
    const next = { ...prev, ...patch };
    writeFileSync(file, JSON.stringify(next, null, 2) + "\n");
    return { ...DEFAULTS, ...next };
  } catch {
    return { ...DEFAULTS };
  }
}
