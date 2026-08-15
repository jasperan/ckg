I have completed a full static review of all files under the three commits. Note: as a review subagent I could not run `git diff ccbe6c4^..HEAD --stat` or any shell commands, so I read every listed file in full and cross-checked against tests, README, and related modules (`oracle_pgq.py`, `pagerank.py`, `claude/plugin.py`, `prompts.py`).

---

## Review

### Correct (with evidence)
- **No shell interpolation anywhere.** The pi extension shells out exclusively via `execFile`/`spawnSync` with argv arrays (`cli.ts:116-140`); the Claude hook uses `subprocess.run(cmd, ...)` with a list (`before_prompt.py:88`); user-supplied paths/queries are passed as args, never concatenated into a shell string. No command-injection vector found.
- **SQL values are bind-parameterized** (`oracle_pgq.py`: `:id`, `:domain`, `:anchor`; `connection.py:114-123`). Only identifiers (`table_prefix`, `graph_name`) are interpolated, and those come from env config, not untrusted input.
- **Agent-loop hooks never throw.** `buildMap` (context.ts:48-85) and `before_prompt.py:main` are wrapped so any failure emits null/`{}` and the loop continues; `runCli` never rejects.
- **CLI fallback design is sound**: `connect_pgq` returning `(None, None)` and `hybrid.py:95`'s new fallback message (`memory (pgq unavailable: <ORA hint>; run 'ckg load' ...)`) cleanly degrade to in-memory mode when Oracle is absent.
- **npm packaging**: `files` whitelist + `.npmignore` are consistent (`.venv/`, `tests/`, `docs/` excluded); `postinstall.js` is deliberately non-fatal (always `exit 0`); `uv.lock` and `pyproject.toml` ship so `uv sync --frozen` can reproduce the env.
- New CLI code is exercised by `tests/test_cli.py` (build/query/inject) and live-gated `tests/test_oracle_live.py` covers `cmd_load`/`cmd_oracle_status`/PGQ auto-detection.

### Blocker
- None found. No CRITICAL or HIGH issues in the reviewed code.

### Fixed
- None (review-only pass; no edits made).

### Findings (severity-rated)

**MEDIUM-1 — CLI discovery can shadow a working PATH install** — `pi/extensions/ckg/cli.ts:82-90`
`discoverCli()` picks the `uv run --no-project ckg` spec whenever `uv` exists, but only probes `uv --version`, never that `ckg` actually runs. If `ckg` is installed on PATH (`pip install ckg` / `uv tool install ckg`) but not in the uv-managed environment, this spec is chosen over the PATH candidate and every tool call fails with a confusing error. Fix: probe `uv run --no-project ckg --help` before accepting the spec, or reorder so the PATH check comes first, or use `uv tool run ckg`.

**MEDIUM-2 — Injection guard runs after the expensive work, and stale maps win** — `pi/extensions/ckg/index.ts:42-46`
`buildMap()` (which spawns `ckg inject` with a 12s timeout) runs before `if (event.systemPrompt.includes("CKG Structure Map")) return;`. Every coding prompt therefore pays a subprocess round-trip even when a map is already present, and when the marker is present the freshly computed map for the *current* prompt is discarded — pinning the first prompt's map. Fix: check the marker first and skip `buildMap` (or splice the new map in place of the old one).

**MEDIUM-3 — Claude Code hook runs on every prompt with no gate/toggle** — `hooks/before_prompt.py` + `.claude-plugin/plugin.json` (matcher `"*"`)
Unlike the pi extension (which has `keywordGate` and `CKG_INJECT`/`CKG_AUTOBUILD` toggles in `config.ts`), the Claude hook unconditionally spawns `ckg inject` (8s timeout) for *every* user prompt once a graph cache exists — including non-coding prompts, with no way to disable short of uninstalling the plugin. Fix: mirror `config.ts` gating (env `CKG_INJECT` + `.ckg/pi.json`) and/or add a keyword pre-filter; at minimum document the toggle.

**MEDIUM-4 — Default Oracle credentials hardcoded and silently applied** — `src/ckg/storage/connection.py:37`
`password: os.environ.get("CKG_ORACLE_PASSWORD", "continual_learning")` — whenever `CKG_ORACLE_DSN` is set, the well-known default password is used with no warning; `install.sh` also prints it to the terminal. Acceptable for the documented local dev container, but a real risk if a user points the DSN at a shared instance. Fix: require an explicit password (or emit a prominent warning) when the DSN target isn't localhost, and drop the password from install.sh's echo.

**MEDIUM-5 — DSN echoed to stdout; can leak embedded credentials** — `connection.py:92`, `src/ckg/cli/main.py:84,132`
`oracle-status` prints `summary['dsn']` and `cmd_load` prints the DSN in its error message. The documented DSN form (`host:port/service`) is safe, but oracledb also accepts `user:pass@host:port/service`, in which case credentials are printed to the console/agent logs. Fix: redact userinfo (`re.sub(r'^[^@]*@', '', dsn)`) before display.

**MEDIUM-6 — Pool leak on the `oracle_summary` failure path** — `connection.py:104-105`
When the version query fails after pool creation, `return summary` exits without `pool.close()` (the `finally` at line 131 only runs on the success path). Harmless in the exit-immediately CLI, but a real leak in any long-lived embedding. Fix: `pool.close()` before returning, or restructure with `try/finally`.

**LOW-7 — Bad env values crash `oracle-status`** — `connection.py:41-42`
`int(os.environ.get("CKG_ORACLE_POOL_MIN", "1"))` raises `ValueError` from `oracle_config()`, which is called *outside* the try in both `oracle_summary` and `connect_pgq` → traceback instead of a graceful message. Fix: safe-int helper (default on failure).

**LOW-8 — `cmd_load` connects before validating the path, and swallows the real error** — `main.py:82-84`
The pool is opened, then `tree.is_dir()` is checked and the function returns 1 with only `cfg['dsn']` printed — no reason for the connection failure. Fix: validate the path first; include the exception detail in the error output; close the pool on the error path.

**LOW-9 — Explicit `0` for `--k-anchor/--hops/--top-k` silently becomes the default** — `main.py:164` (`int(args.k_anchor or 5)`).

**LOW-10 — Dead code** — `hybrid.py:70` imports `match_edges` but never uses it; `context.ts:27,78` writes `graphChecked` but never reads it; `cli.ts:29` includes `"status"` in `CliCommand` but the CLI has no `status` subcommand (`oracle-status` is the real one).

**LOW-11 — Windows support is broken in several places** — `project.ts:46` derives the parent via `dir.slice(0, dir.lastIndexOf("/"))` (returns `-1`/wrong result on `\` paths → project detection fails); `cli.ts:82` hardcodes `.venv/bin/ckg` (Windows needs `.venv/Scripts/ckg.exe`); `context.ts` `projectName` splits on `"/"`. Windows is clearly intended (`uv.exe`/`ckg.exe` probes in cli.ts:78, postinstall.js handles `Scripts`). Fix with `node:path` and platform checks.

**LOW-12 — Documentation inaccuracies**
- `main.py:130` prints `Set CKG_ORACLE_DSN=CKG_ORACLE_USER/PASSWORD to enable.` — nonsense instruction (DSN ≠ user/password).
- `cli.ts:158` install guidance says `pip install ckg # from PyPI`; README only documents `pip install git+...`. Verify PyPI publication or fix the text.
- marketplace.json/README metric claims (−22% tool calls, −36% sooner) are not verifiable from the repo — fine if backed by the referenced study, otherwise soften.
- `statusBlock` (context.ts:108) reads the oracle-status error from stderr, but the CLI prints failures to stdout → shows the generic "in-memory mode" instead of the real reason.

**LOW-13 — Packaging / portability notes**
- `package.json:59` declares peer `typebox`, but the canonical package is `@sinclair/typebox` — verify `typebox` actually resolves in the pi runtime, else the extension fails to load (unverifiable offline; flagged).
- `packageRoot()` (cli.ts:38-51) breaks in `install.sh`'s fallback copy mode (`~/.pi/agent/extensions/ckg` copy): the bundled `<pkg>/.venv` is never found, so it silently degrades to PATH.
- `install.sh` uses `curl | bash` without commit pinning (standard supply-chain caveat).
- `tests/test_cli.py:139-146` uses a bottom-of-file `try: import argparse` hack — works but should just be a normal import.

### Note
- The fallback-message change in `hybrid.py:95` is correct and well-tested (`tests/test_retrieval.py` asserts method strings; live tests assert `"pgq" in method`).
- `before_agent_start` returning `{ systemPrompt }` and the `hookSpecificOutput.additionalContext` protocol usage are both consistent with the documented Claude Code hook contract — assumed correct, can't be verified offline.

## Final recommendation: **REQUEST CHANGES**

No CRITICAL/HIGH; the security posture (no shell injection, bind-parameterized SQL, non-fatal hooks) is solid. However, MEDIUM-1 (discovery shadowing a working install), MEDIUM-2 (injection loop ordering/stale maps), and MEDIUM-3 (ungated per-prompt subprocess) are genuine functional defects in the two headline features under review (PGQ auto-detection, transparent injection). The remaining MEDIUMs (credentials, DSN redaction, pool leak) are cheap to fix. All fixes are small and localized.

Suggested order: MEDIUM-1 → MEDIUM-2 → MEDIUM-3 → MEDIUM-5 (one-line redaction) → MEDIUM-6 (close pool) → MEDIUM-4 (warn on default creds) → LOWs as convenient.