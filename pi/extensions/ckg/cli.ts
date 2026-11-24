/**
 * CKG CLI discovery + execution helpers.
 *
 * The CKG core is a Python package. This module finds a usable `ckg` CLI
 * (env override → bundled venv → PATH) and runs its subcommands. Nothing here
 * throws: every failure comes back as a result the caller can describe.
 */
import { execFile, spawnSync, type ExecFileException } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, isAbsolute, join, sep } from "node:path";
import { fileURLToPath } from "node:url";

export interface CliSpec {
  /** Executable: an absolute path, or a bare command name from CKG_CLI. */
  bin: string;
  /** How this CLI was resolved (for diagnostics). */
  source: string;
}

const IS_WINDOWS = process.platform === "win32";

/**
 * Package root of the installed CKG package (where package.json lives).
 * Works for both `pi install` (npm/git/local) and `pi -e` runs.
 */
export function packageRoot(): string {
  // <pkg>/pi/extensions/ckg/cli.ts  →  <pkg>
  let dir = dirname(fileURLToPath(import.meta.url));
  for (let i = 0; i < 3; i++) dir = dirname(dir);
  return dir;
}

function bundledCli(): string {
  const root = packageRoot();
  return IS_WINDOWS ? join(root, ".venv", "Scripts", "ckg.exe") : join(root, ".venv", "bin", "ckg");
}

/** Absolute path of `name` on PATH, or null. */
function onPath(name: string): string | null {
  const r = spawnSync(IS_WINDOWS ? "where" : "which", [name], {
    encoding: "utf8",
    timeout: 5_000,
    stdio: ["ignore", "pipe", "ignore"],
  });
  if (r.error || r.status !== 0) return null;
  const first = String(r.stdout ?? "").split(/\r?\n/)[0]?.trim();
  return first || null;
}

/** True when the Python environment has never been set up for this install. */
export function needsPythonSetup(): boolean {
  const root = packageRoot();
  return existsSync(join(root, "pyproject.toml")) && !existsSync(join(root, ".venv"));
}

/** Discover a usable `ckg` CLI. Returns null when none is available. */
export function discoverCli(): CliSpec | null {
  const override = process.env.CKG_CLI;
  if (override) {
    // An override that names a file must exist; a bare name is looked up on PATH.
    const isPath = isAbsolute(override) || override.includes(sep) || override.includes("/");
    if (isPath) return existsSync(override) ? { bin: override, source: "env CKG_CLI" } : null;
    const resolved = onPath(override);
    return resolved ? { bin: resolved, source: "env CKG_CLI" } : null;
  }

  const bundled = bundledCli();
  if (existsSync(bundled)) return { bin: bundled, source: "bundled venv" };

  const found = onPath("ckg");
  return found ? { bin: found, source: "PATH" } : null;
}

export type ExecOutcome = "ok" | "exit" | "timeout" | "missing" | "overflow" | "aborted" | "error";

export interface ExecResult {
  outcome: ExecOutcome;
  /** Process exit code; null when the process did not exit by itself. */
  code: number | null;
  stdout: string;
  stderr: string;
}

export interface ExecOptions {
  timeoutMs?: number;
  cwd?: string;
  signal?: AbortSignal;
}

function classify(err: ExecFileException): ExecOutcome {
  if (err.name === "AbortError" || err.code === "ABORT_ERR") return "aborted";
  if (err.code === "ENOENT" || err.code === "EACCES") return "missing";
  if (err.code === "ERR_CHILD_PROCESS_STDIO_MAXBUFFER") return "overflow";
  if (err.killed) return "timeout";
  if (typeof err.code === "number") return "exit";
  return "error";
}

/** Run an executable with a timeout. Never throws or rejects. */
export function runFile(bin: string, args: string[], opts: ExecOptions = {}): Promise<ExecResult> {
  const { promise, resolve } = Promise.withResolvers<ExecResult>();
  try {
    execFile(
      bin,
      args,
      {
        cwd: opts.cwd,
        timeout: opts.timeoutMs ?? 60_000,
        maxBuffer: 8 * 1024 * 1024,
        signal: opts.signal,
      },
      (err, stdout, stderr) => {
        resolve({
          outcome: err ? classify(err) : "ok",
          code: !err ? 0 : typeof err.code === "number" ? err.code : null,
          stdout: String(stdout ?? ""),
          stderr: String(stderr ?? ""),
        });
      },
    );
  } catch (err) {
    // Invalid options (e.g. a bad cwd type) throw synchronously.
    resolve({ outcome: "error", code: null, stdout: "", stderr: String(err) });
  }
  return promise;
}

/** Run a `ckg` subcommand. Never throws or rejects. */
export function runCli(cli: CliSpec, args: string[], opts: ExecOptions = {}): Promise<ExecResult> {
  return runFile(cli.bin, args, opts);
}

/** One-paragraph explanation of a failed `ckg` run, for tool output. */
export function describeFailure(cli: CliSpec, args: string[], result: ExecResult, timeoutMs: number): string {
  const what = `ckg ${args[0] ?? ""}`.trim();
  switch (result.outcome) {
    case "missing":
      return `${what}: the CLI at ${cli.bin} (${cli.source}) could not be run.\n\n${installGuidance()}`;
    case "timeout":
      return `${what} timed out after ${Math.round(timeoutMs / 1000)}s.`;
    case "aborted":
      return `${what} was cancelled.`;
    case "overflow":
      return `${what} produced more output than can be shown (over 8 MB).`;
    default: {
      const detail = (result.stderr || result.stdout || "no output").trim().split("\n").slice(0, 8).join("\n");
      const code = result.code === null ? "" : ` (exit ${result.code})`;
      return `${what} failed${code}:\n${detail}`;
    }
  }
}

/** Human-readable install guidance when no CLI is found. */
export function installGuidance(): string {
  const repo = "https://github.com/jasperan/ckg";
  return [
    "CKG requires the Python CLI (`ckg`). Install it with one of:",
    "",
    `  uv tool install git+${repo}.git`,
    `  pip install git+${repo}.git`,
    "",
    "Or set CKG_CLI=/path/to/ckg to point at an existing install.",
  ].join("\n");
}
