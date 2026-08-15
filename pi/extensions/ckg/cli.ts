/**
 * CKG CLI discovery + execution helpers.
 *
 * The CKG core is a Python package. This module finds a usable `ckg` CLI
 * (env override → bundled venv → PATH), and runs its subcommands. All
 * discovery/exec is best-effort: any failure surfaces as a readable message,
 * never as a thrown error that could disturb the agent loop.
 */
import { execFile, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

export type CliCommand = "build" | "query" | "inject" | "status";

export interface CliSpec {
  /** Executable (absolute path) or bare command name. */
  bin: string;
  /** Extra argv prefix, e.g. ["run", "ckg"] for `uv run ckg`. */
  prefix: string[];
  /** Working directory for the process. */
  cwd: string;
  /** How this CLI was resolved (for diagnostics). */
  source: string;
}

/**
 * Package root of the installed CKG package (where package.json lives).
 * Works for both `pi install` (npm/git/local) and `pi -e` runs.
 */
export function packageRoot(): string {
  const here = (() => {
    try {
      // jiti ESM context
      const url = import.meta.url;
      if (url) return fileURLToPath(url);
    } catch {
      /* fall through */
    }
    try {
      // CJS fallback
      return __filename;
    } catch {
      return process.cwd() + "/pi/extensions/ckg/index.ts";
    }
  })();
  // <pkg>/pi/extensions/ckg/index.ts  →  <pkg>
  const rel = ["pi", "extensions", "ckg", "index.ts"];
  let pkg = here;
  for (let i = 0; i < rel.length; i++) pkg = dirname(pkg);
  return pkg;
}

/** True if a python-ish venv CLI binary exists. */
function hasFile(p: string): boolean {
  try {
    return existsSync(p);
  } catch {
    return false;
  }
}

/** Discover a usable `ckg` CLI. Returns null when none is available. */
export function discoverCli(): CliSpec | null {
  // 1) Explicit override
  const override = process.env.CKG_CLI;
  if (override) {
    return { bin: override, prefix: [], cwd: process.cwd(), source: "env CKG_CLI" };
  }

  // 2) Bundled venv (created by postinstall in the installed package)
  const bundled = join(packageRoot(), ".venv", "bin", "ckg");
  if (hasFile(bundled)) {
    return { bin: bundled, prefix: [], cwd: packageRoot(), source: "bundled venv" };
  }

  // 3) `uv run ckg` from the package dir (dev checkout without .venv yet)
  const uvBins = ["uv", "uv.exe", "uv.cmd"];
  for (const uv of uvBins) {
    try {
      const r = execFileSyncOrNull(uv, ["--version"], packageRoot());
      if (r !== null) {
        return {
          bin: uv,
          prefix: ["run", "--no-project", "ckg"],
          cwd: packageRoot(),
          source: "uv run (bundled)",
        };
      }
    } catch {
      /* keep scanning */
    }
  }

  // 4) On PATH
  for (const candidate of ["ckg", "ckg.cmd", "ckg.exe"]) {
    try {
      const r = execFileSyncOrNull(candidate, ["--help"], process.cwd());
      if (r !== null) {
        return { bin: candidate, prefix: [], cwd: process.cwd(), source: "PATH" };
      }
    } catch {
      /* keep scanning */
    }
  }

  return null;
}

export interface ExecResult {
  code: number | null;
  stdout: string;
  stderr: string;
}

/** Run a CLI with a timeout. Never throws — returns {code, stdout, stderr}. */
export function runCli(
  cli: CliSpec,
  args: string[],
  opts: { timeoutMs?: number; cwd?: string } = {},
): Promise<ExecResult> {
  const { timeoutMs = 60_000 } = opts;
  const all = [...cli.prefix, ...args];
  return new Promise((resolve) => {
    execFile(
      cli.bin,
      all,
      {
        cwd: opts.cwd ?? cli.cwd,
        timeout: timeoutMs,
        maxBuffer: 8 * 1024 * 1024,
        env: { ...process.env, CKG_NONINTERACTIVE: "1" },
      },
      (err, stdout, stderr) => {
        resolve({
          code: err ? (typeof (err as any).code === "number" ? (err as any).code : 1) : 0,
          stdout: String(stdout ?? ""),
          stderr: String(stderr ?? ""),
        });
      },
    );
  });
}

/** Synchronous discovery helper — returns stdout or null. */
function execFileSyncOrNull(bin: string, args: string[], cwd: string): string | null {
  const r = spawnSync(bin, args, { cwd, encoding: "utf8", timeout: 10_000, stdio: ["ignore", "pipe", "pipe"] });
  if (r.error || r.status !== 0) return null;
  return String(r.stdout ?? "");
}

/** Human-readable install guidance when no CLI is found. */
export function installGuidance(): string {
  const repo = "https://github.com/jasperan/ckg";
  return [
    "CKG requires the Python CLI (`ckg`). Install it with one of:",
    "",
    `  pip install ckg          # from PyPI`,
    `  uv tool install ckg      # or with uv`,
    `  git clone ${repo} && cd ckg && uv sync`,
    "",
    "Or set CKG_CLI=/path/to/ckg to point at an existing install.",
  ].join("\n");
}
