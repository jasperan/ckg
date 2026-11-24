/**
 * CKG activation + project detection helpers.
 *
 * Mirrors the Claude Code hook's activation heuristics: only inject a
 * structure map when the user's prompt looks like a coding task and we are
 * inside a project (`.git`, `pyproject.toml`, or `package.json`).
 */
import { existsSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

const CODING_KEYWORDS = [
  "implement", "add ", "fix", "change", "refactor", "build", "create",
  "modify", "update", "feature", "bug", "edit", "write", "debug", "deploy",
  "function", "class", "module", "import", "api", "endpoint",
  "test", "migrate", "upgrade", "rewrite", "optimize", "integrate",
];

const CODING_PATTERNS = [
  /\b(implement|add|fix|change|refactor|build|create|modify|update|debug)\b.*\b(feature|bug|function|class|module|file|code|endpoint|api|test|support)\b/i,
  /\b(write|edit)\b/i,
];

export function isCodingPrompt(prompt: string): boolean {
  const lower = prompt.toLowerCase();
  const keywordHits = CODING_KEYWORDS.filter((k) => lower.includes(k)).length;
  if (keywordHits >= 2) return true;
  return CODING_PATTERNS.some((re) => re.test(prompt));
}

export interface DetectedProject {
  root: string;
  marker: string;
}

const MARKERS = [".git", "pyproject.toml", "package.json"];

/**
 * Walk upward from cwd looking for a project root marker. The home directory
 * and the filesystem root never count as a project.
 */
export function detectProject(cwd: string): DetectedProject | null {
  const home = homedir();
  let dir = cwd;
  for (;;) {
    const parent = dirname(dir);
    if (dir === home || parent === dir) return null;
    for (const marker of MARKERS) {
      try {
        if (existsSync(join(dir, marker))) return { root: dir, marker };
      } catch {
        return null;
      }
    }
    dir = parent;
  }
}

/** Graph cache location for a project. */
export function graphCachePath(root: string): string {
  return join(root, ".ckg", "code_graph.json");
}

/**
 * Identity of the current graph cache (mtime + size), or null when there is
 * none. A rebuild changes it, which is what invalidates memoized maps.
 */
export function graphCacheStamp(root: string): string | null {
  try {
    const st = statSync(graphCachePath(root));
    return `${st.mtimeMs}:${st.size}`;
  } catch {
    return null;
  }
}
