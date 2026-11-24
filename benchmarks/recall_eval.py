#!/usr/bin/env python3
"""Retrieval recall benchmark from a repository's own git history.

Each recent commit that touched a handful of Python files is a test case: the
commit subject is the query and the files it changed are the gold set. The
graph is built from a throwaway clone checked out at the parent of the oldest
test commit, so neither the source tree nor the co-edit history has seen the
changes being predicted.

Reported per repository:

  anchors   recall of the lexical anchor files alone
  top-10    recall of the 10 best-ranked files from hybrid retrieval
  map       recall of the files named in the injected structure map
  map size  mean number of files named in the map
  MRR       mean reciprocal rank of the first gold file in the ranked list

Usage:
    python benchmarks/recall_eval.py <repo> [<repo> ...] [--commits 40]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_PREFIX_RE = re.compile(r"^\w+(\([^)]*\))?!?:\s*")
_MAP_FILE_RE = re.compile(r"^- `([^`]+)`", re.MULTILINE)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "core.quotePath=false", *args],
        capture_output=True, text=True, check=True,
    ).stdout


def collect_cases(repo: Path, *, n: int, min_files: int, max_files: int) -> tuple[str, list[dict]]:
    """Latest ``n`` commits touching min..max Python files, and the base rev."""
    log = _git(
        repo, "log", "--no-merges", "--name-only", "--diff-filter=M",
        "--pretty=format:%x01%H%x02%s", "--", "*.py",
    )
    cases: list[dict] = []
    for block in log.split("\x01")[1:]:
        head, _, names = block.partition("\n")
        sha, _, subject = head.partition("\x02")
        files = sorted({ln.strip() for ln in names.splitlines() if ln.strip()})
        query = _PREFIX_RE.sub("", subject).strip()
        if min_files <= len(files) <= max_files and len(query.split()) >= 3:
            cases.append({"sha": sha, "query": query, "gold": files})
        if len(cases) == n:
            break
    if not cases:
        return "", []
    return f"{cases[-1]['sha']}^", cases


def _files_in_order(node_ids: list[str]) -> list[str]:
    out: list[str] = []
    for nid in node_ids:
        rel = nid.removeprefix("file:").removeprefix("sym:").split("::", 1)[0]
        if rel not in out:
            out.append(rel)
    return out


def evaluate(repo: Path, *, n: int, min_files: int, max_files: int) -> dict | None:
    from ckg.claude.plugin import build_context_map
    from ckg.retrieval.hybrid import hybrid_retrieve

    base, cases = collect_cases(repo, n=n, min_files=min_files, max_files=max_files)
    if not cases:
        return None

    work = Path(tempfile.mkdtemp(prefix="ckg-eval-"))
    clone = work / repo.name
    try:
        subprocess.run(
            ["git", "clone", "-q", "--shared", "--no-checkout", str(repo), str(clone)],
            check=True,
        )
        _git(clone, "checkout", "-q", "--detach", base)
        present = set(_git(clone, "ls-files", "*.py").splitlines())
        cases = [
            {**c, "gold": [g for g in c["gold"] if g in present]} for c in cases
        ]
        cases = [c for c in cases if len(c["gold"]) >= min_files]
        if not cases:
            return None

        t0 = time.perf_counter()
        build_context_map(cases[0]["query"], clone)
        build_s = time.perf_counter() - t0
        cache = clone / ".ckg" / "code_graph.json"
        graph = json.loads(cache.read_text())

        sums = {"anchors": 0.0, "top10": 0.0, "map": 0.0, "map_size": 0.0, "mrr": 0.0}
        query_s = 0.0
        for case in cases:
            gold = set(case["gold"])
            t0 = time.perf_counter()
            res = hybrid_retrieve(case["query"], graph, top_k=200)
            query_s += time.perf_counter() - t0
            ranked = _files_in_order([r["node_id"] for r in res["results"]])
            anchors = set(_files_in_order(res["anchors"]))
            top10 = ranked[:10]
            rr = next((1.0 / (i + 1) for i, f in enumerate(ranked) if f in gold), 0.0)
            rendered = build_context_map(case["query"], clone) or ""
            in_map = set(_MAP_FILE_RE.findall(rendered))
            sums["anchors"] += len(gold & anchors) / len(gold)
            sums["top10"] += len(gold & set(top10)) / len(gold)
            sums["map"] += len(gold & in_map) / len(gold)
            sums["map_size"] += len(in_map)
            sums["mrr"] += rr

        k = len(cases)
        edges: dict[str, int] = {}
        for e in graph["edges"]:
            edges[e["kind"]] = edges.get(e["kind"], 0) + 1
        return {
            "repo": repo.name,
            "cases": k,
            "files": len(present),
            "nodes": len(graph["nodes"]),
            "edges": edges,
            "cache_mb": round(cache.stat().st_size / 1e6, 2),
            "build_s": round(build_s, 2),
            "query_ms": round(1000 * query_s / k, 1),
            **{name: round(total / k, 3) for name, total in sums.items()},
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("repos", nargs="+", type=Path)
    ap.add_argument("--commits", type=int, default=40, help="test commits per repo")
    ap.add_argument("--min-files", type=int, default=2)
    ap.add_argument("--max-files", type=int, default=8)
    ap.add_argument("--json", type=Path, help="write raw results to this file")
    args = ap.parse_args()

    rows = []
    for repo in args.repos:
        row = evaluate(
            repo.resolve(), n=args.commits,
            min_files=args.min_files, max_files=args.max_files,
        )
        if row is None:
            print(f"{repo}: no usable commits", file=sys.stderr)
            continue
        rows.append(row)
        print(
            f"{row['repo']:<28} cases={row['cases']:<3} files={row['files']:<4} "
            f"anchors={row['anchors']:.3f} top10={row['top10']:.3f} "
            f"map={row['map']:.3f} map_size={row['map_size']:.1f} mrr={row['mrr']:.3f} "
            f"build={row['build_s']}s query={row['query_ms']}ms cache={row['cache_mb']}MB",
            flush=True,
        )
    if args.json:
        args.json.write_text(json.dumps(rows, indent=2) + "\n")
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
