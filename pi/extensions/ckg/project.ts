/**
 * CKG activation + project detection helpers.
 *
 * Mirrors the Claude Code skill's activation heuristics: only inject a
 * structure map when the user's prompt looks like a coding task and we are
 * inside a project (`.git`, `pyproject.toml`, or `package.json`).
 */
import { existsSync } from "node:fs";
import { join } from "node:path";

const CODING_KEYWORDS = [
  "implement", "add ", "fix", "change", "refactor", "build", "create",
  "modify", "update", "feature", "bug", "edit", "write", "debug", "deploy",
  "function", "class", "module", "import", "api", "endpoint", "refactor",
  "test", "migrate", "upgrade", "rewrite", "optimize", "integrate",
];

const CODING_PATTERNS = [
  /\b(implement|add|fix|change|refactor|build|create|modify|update|debug)\b.*\b(feature|bug|function|class|module|file|code|endpoint|api|test|support)\b/i,
  /\b(write|write|edit)\b/i,
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

/** Walk upward from cwd looking for a project root marker. */
export function detectProject(cwd: string): DetectedProject | null {
  let dir = cwd;
  for (let i = 0; i < 12; i++) {
    try {
      if (existsSync(join(dir, ".git"))) return { root: dir, marker: ".git" };
      if (existsSync(join(dir, "pyproject.toml"))) return { root: dir, marker: "pyproject.toml" };
      if (existsSync(join(dir, "package.json"))) return { root: dir, marker: "package.json" };
    } catch {
      return null;
    }
    const parent = dir === "/" ? null : dir.slice(0, dir.lastIndexOf("/")) || "/";
    if (parent === null || parent === dir) break;
    dir = parent;
  }
  return null;
}

/** Graph cache location for a project. */
export function graphCachePath(root: string): string {
  return join(root, ".ckg", "code_graph.json");
}

/** True when the CKG graph cache exists for a project. */
export function hasGraphCache(root: string): boolean {
  return existsSync(graphCachePath(root));
}
