#!/usr/bin/env node
/**
 * CKG postinstall — best-effort setup of the bundled Python environment.
 *
 * Creates `<package>/.venv` with the `ckg` CLI so the pi extension and Claude
 * Code hooks work without any manual Python setup. Uses `uv` when available
 * (fast, lockfile-pinned), falls back to `python3 -m venv` + pip.
 *
 * This is intentionally non-fatal: if neither uv nor python3 exists, the
 * extension degrades gracefully (it will look for `ckg` on PATH and report a
 * helpful error otherwise).
 */
"use strict";

const { spawnSync } = require("node:child_process");
const { existsSync, mkdirSync } = require("node:fs");
const { join } = require("node:path");

const ROOT = join(__dirname, "..");
const VENV = join(ROOT, ".venv");
const VENV_BIN = process.platform === "win32" ? join(VENV, "Scripts") : join(VENV, "bin");

function log(msg) {
  console.log(`[ckg] ${msg}`);
}

function warn(msg) {
  console.error(`[ckg] ${msg}`);
}

function run(cmd, args, opts = {}) {
  const r = spawnSync(cmd, args, { cwd: ROOT, encoding: "utf8", stdio: "inherit", ...opts });
  if (r.error) throw r.error;
  return r.status === 0;
}

function venvCkg() {
  const bin = process.platform === "win32" ? "ckg.exe" : "ckg";
  return join(VENV_BIN, bin);
}

async function main() {
  // Already set up?
  if (existsSync(venvCkg()) && existsSync(join(VENV_BIN, process.platform === "win32" ? "python.exe" : "python"))) {
    log("Python environment already present — skipping.");
    return 0;
  }

  // 1) uv — preferred
  const uv = spawnSync("uv", ["--version"], { encoding: "utf8" });
  if (!uv.error && uv.status === 0) {
    log("Setting up Python environment with uv ...");
    try {
      if (run("uv", ["sync", "--no-dev", "--frozen"])) {
        log(`Done. CLI at ${venvCkg()}`);
        return 0;
      }
    } catch (e) {
      warn(`uv sync failed: ${e.message}`);
    }
  }

  // 2) python3 + venv + pip
  const py = spawnSync("python3", ["--version"], { encoding: "utf8" });
  if (!py.error && py.status === 0) {
    log("Setting up Python environment with python3 + venv ...");
    try {
      if (!run("python3", ["-m", "venv", VENV])) throw new Error("venv creation failed");
      const pip = process.platform === "win32" ? join(VENV_BIN, "pip.exe") : join(VENV_BIN, "pip");
      if (run(pip, ["install", "--quiet", "--upgrade", "pip"])) {
        if (run(pip, ["install", "--quiet", "."])) {
          log(`Done. CLI at ${venvCkg()}`);
          return 0;
        }
      }
    } catch (e) {
      warn(`pip install failed: ${e.message}`);
    }
  }

  warn(
    "Could not set up the bundled Python environment. Install the ckg CLI manually: " +
      "`pip install ckg` or `uv tool install ckg`, or set CKG_CLI=/path/to/ckg."
  );
  return 0; // never fail the npm install
}

main().catch((e) => {
  warn(`postinstall error: ${e.message}`);
  process.exit(0);
});
