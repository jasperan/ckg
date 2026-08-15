"""CKG CLI — build, query, and inject code knowledge graphs.

Usage:
    ckg build <path> [--pkg-root NAME]     # Parse a Python tree into a code graph
    ckg query <query> [--graph FILE]       # Run hybrid retrieval against a cached graph
    ckg inject <query> [--root DIR]        # Build + inject context (Claude Code mode)
    ckg serve [--port PORT]                # Start a lightweight API server (future)

Examples:
    ckg build . --pkg-root mypackage
    ckg query "add JWT authentication" --graph .ckg/code_graph.json
    ckg inject "fix the rate limiter" --root ~/projects/myapp
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def cmd_build(args: argparse.Namespace) -> int:
    """Parse a Python source tree into a code graph and cache it."""
    tree = Path(args.path).resolve()
    if not tree.is_dir():
        print(f"error: {tree} is not a directory", file=sys.stderr)
        return 1

    pkg_root = args.pkg_root or tree.name
    output = Path(args.output or (tree / ".ckg" / "code_graph.json"))
    output.parent.mkdir(parents=True, exist_ok=True)

    print(f"Parsing {tree} (pkg_root={pkg_root}) ...")
    from ckg.graph.parser import parse_tree
    from ckg.graph.builder import enrich_node_text

    graph = parse_tree(tree, pkg_root=pkg_root)

    # Enrich node text from source
    nodes = list(graph["nodes"].values())
    for node in nodes:
        enrich_node_text(tree, node, pkg_root)
    graph["nodes"] = {n["id"]: n for n in nodes}

    output.write_text(json.dumps(graph, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {len(nodes)} nodes, {len(graph['edges'])} edges → {output}")
    print(f"  files: {graph['meta']['n_files']}")
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    """Run hybrid retrieval against a cached graph."""
    graph_path = Path(args.graph or ".ckg/code_graph.json")
    if not graph_path.exists():
        print(f"error: no graph at {graph_path}. Run 'ckg build' first.", file=sys.stderr)
        return 1

    graph = json.loads(graph_path.read_text())
    from ckg.retrieval.hybrid import hybrid_retrieve

    results = hybrid_retrieve(
        args.query, graph,
        k_anchor=int(args.k_anchor or 5),
        hops=int(args.hops or 2),
        top_k=int(args.top_k or 10),
    )

    print(f"Retrieval ({results['method']}):")
    print(f"  Anchors: {', '.join(results['anchor_labels'])}")
    print()
    for r in results["results"]:
        label = r["node_id"]
        if label.startswith("file:"):
            label = label.removeprefix("file:")
        print(f"  {r['score']:.4f}  {label}")
    return 0


def cmd_inject(args: argparse.Namespace) -> int:
    """Build + inject: generate the full Claude Code context map."""
    from ckg.claude.plugin import inject_context

    project_root = Path(args.root).resolve() if args.root else None
    result = inject_context(args.query, project_root=project_root)

    if not result:
        print("(no context map available for this project)", file=sys.stderr)
        return 1

    print(result)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="CKG — Code Knowledge Graph CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = ap.add_subparsers(dest="command", required=True)

    # build
    sp_build = sub.add_parser("build", help="Parse a Python tree into a code graph")
    sp_build.add_argument("path", help="Path to the Python package directory")
    sp_build.add_argument("--pkg-root", help="Top-level package name (default: dir name)")
    sp_build.add_argument("--output", help="Output path (default: .ckg/code_graph.json)")
    sp_build.set_defaults(func=cmd_build)

    # query
    sp_query = sub.add_parser("query", help="Hybrid retrieval against a cached graph")
    sp_query.add_argument("query", help="Feature description or query")
    sp_query.add_argument("--graph", help="Path to cached code graph JSON")
    sp_query.add_argument("--k-anchor", type=int, help="Max lexical anchors (default 5)")
    sp_query.add_argument("--hops", type=int, help="Graph reach depth (default 2)")
    sp_query.add_argument("--top-k", type=int, help="Top results to return (default 10)")
    sp_query.set_defaults(func=cmd_query)

    # inject
    sp_inject = sub.add_parser("inject", help="Build + inject context map (Claude Code mode)")
    sp_inject.add_argument("query", help="Task description")
    sp_inject.add_argument("--root", help="Project root (auto-detected if omitted)")
    sp_inject.set_defaults(func=cmd_inject)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
