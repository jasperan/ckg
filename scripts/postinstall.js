#!/usr/bin/env node
/**
 * CKG postinstall — best-effort setup of the bundled Python environment.
 *
 * Creates `<package>/.venv` with the `ckg` CLI so the pi extension and Claude
 * Code hook work without any manual Python setup. Uses `uv` when available
 * (fast, lockfile-pinned), falls back to `python3 -m venv` + pip.
 *
 * Never fails the npm install: when no environment can be set up, the
 * extension looks for `ckg` on PATH and otherwise reports how to install it.
 * Set CKG_SKIP_POSTINSTALL=1 to skip this step. The pi extension runs this
 * script itself on first use when the environment is missing, so installs
 * done with `--ignore-scripts` still work.
 */
"use strict";

const { spawnSync } = require("node:child_process");
const { existsSync } = require("node:fs");
const { join } = require("node:path");

const ROOT = join(__dirname, "..");
const VENV = join(ROOT, ".venv");
const WINDOWS = process.platform === "win32";
const VENV_BIN = join(VENV, WINDOWS ? "Scripts" : "bin");
const VENV_CKG = join(VENV_BIN, WINDOWS ? "ckg.exe" : "ckg");
const MIN_PYTHON = [3, 12];

function log(msg) {
  console.log(`[ckg] ${msg}`);
}

function warn(msg) {
  console.error(`[ckg] ${msg}`);
}

function run(cmd, args) {
  const r = spawnSync(cmd, args, { cwd: ROOT, encoding: "utf8", stdio: "inherit" });
  return !r.error && r.status === 0;
}

/** [major, minor] of `python3`, or null when it is absent. */
function pythonVersion() {
  const r = spawnSync("python3", ["--version"], { encoding: "utf8" });
  const match = !r.error && r.status === 0 && /(\d+)\.(\d+)/.exec(`${r.stdout}${r.stderr}`);
  return match ? [Number(match[1]), Number(match[2])] : null;
}

function main() {
  if (process.env.CKG_SKIP_POSTINSTALL === "1") {
    log("CKG_SKIP_POSTINSTALL=1 — skipping Python environment setup.");
    return;
  }
  if (existsSync(VENV_CKG)) {
    log("Python environment already present — skipping.");
    return;
  }

  // 1) uv — preferred: installs the locked versions and fetches Python itself.
  const uv = spawnSync("uv", ["--version"], { encoding: "utf8" });
  if (!uv.error && uv.status === 0) {
    log("Setting up Python environment with uv ...");
    if (run("uv", ["sync", "--no-dev", "--frozen"]) && existsSync(VENV_CKG)) {
      log(`Done. CLI at ${VENV_CKG}`);
      return;
    }
    warn("uv sync failed.");
  }

  // 2) python3 + venv + pip
  const version = pythonVersion();
  if (version === null) {
    warn("Neither uv nor python3 was found.");
  } else if (version[0] < MIN_PYTHON[0] || (version[0] === MIN_PYTHON[0] && version[1] < MIN_PYTHON[1])) {
    warn(`Python ${MIN_PYTHON.join(".")}+ required (found ${version.join(".")}).`);
  } else {
    log("Setting up Python environment with python3 + venv ...");
    const pip = join(VENV_BIN, WINDOWS ? "pip.exe" : "pip");
    if (run("python3", ["-m", "venv", VENV]) && run(pip, ["install", "--quiet", "."]) && existsSync(VENV_CKG)) {
      log(`Done. CLI at ${VENV_CKG}`);
      return;
    }
    warn("pip install failed.");
  }

  warn(
    "Could not set up the bundled Python environment. Install the ckg CLI manually: " +
      "`uv tool install git+https://github.com/jasperan/ckg.git`, or set CKG_CLI=/path/to/ckg.",
  );
}

try {
  main();
} catch (e) {
  warn(`postinstall error: ${e.message}`);
}
