"""CKG CLI — build, load, query, and inject code knowledge graphs.

Usage:
    ckg build <path> [--pkg-root NAME]       # Parse a Python tree into a code graph
    ckg load <path> [--pkg-root NAME]        # Parse + store into Oracle PGQ (needs CKG_ORACLE_DSN)
    ckg query <query> [--graph FILE]         # Hybrid retrieval (PGQ when Oracle configured)
    ckg inject <query> [--root DIR]          # Build + inject context (agent mode)
    ckg oracle-status                       # Oracle AI DB Free connectivity + stats

Examples:
    ckg build . --pkg-root mypackage
    ckg load . --pkg-root mypackage --domain myapp        # → Oracle PGQ
    ckg query "add JWT authentication" --graph .ckg/code_graph.json
    ckg inject "fix the rate limiter" --root ~/projects/myapp
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _oracle_mem(cfg: dict | None = None):
    """Connect to Oracle PGQ. Returns (mem, cfg) or (None, None)."""
    from ckg.storage.connection import connect_pgq

    return connect_pgq(cfg)

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


def cmd_load(args: argparse.Namespace) -> int:
    """Parse a Python source tree and store it into Oracle PGQ.

    Builds the code graph exactly like ``ckg build``, then upserts nodes and
    edges into the Oracle MEMORY_GRAPH tables and (re)creates the SQL property
    graph so GRAPH_TABLE ... MATCH queries run in the database.
    """
    from ckg.storage.connection import oracle_config, connect_pgq
    from ckg.storage.oracle_pgq import (
        upsert_graph_nodes, upsert_graph_edges, create_property_graph,
    )

    cfg = oracle_config()
    if cfg is None:
        print(
            "error: Oracle not configured. Set CKG_ORACLE_DSN (see ckg oracle-status).",
            file=sys.stderr,
        )
        return 1
    if args.domain:
        cfg["domain"] = args.domain

    mem, cfg = connect_pgq(cfg)
    if mem is None:
        print(f"error: cannot connect to Oracle at {cfg['dsn']}", file=sys.stderr)
        return 1

    tree = Path(args.path).resolve()
    if not tree.is_dir():
        print(f"error: {tree} is not a directory", file=sys.stderr)
        return 1

    pkg_root = args.pkg_root or tree.name
    graph_path = Path(args.graph or (tree / ".ckg" / "code_graph.json"))

    # Parse (or reuse a cached graph)
    from ckg.graph.parser import parse_tree
    from ckg.graph.builder import enrich_node_text

    graph = parse_tree(tree, pkg_root=pkg_root)
    nodes = list(graph["nodes"].values())
    for node in nodes:
        enrich_node_text(tree, node, pkg_root)
    graph["nodes"] = {n["id"]: n for n in nodes}

    graph_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_text(json.dumps(graph, indent=2, sort_keys=True) + "\n")

    domain = cfg["domain"]
    n = upsert_graph_nodes(mem, nodes, domain=domain, table_prefix=cfg["table_prefix"])
    e = upsert_graph_edges(mem, graph["edges"], domain=domain, table_prefix=cfg["table_prefix"])
    create_property_graph(
        mem,
        graph_name=cfg["graph_name"],
        table_prefix=cfg["table_prefix"],
    )

    print(f"Stored into Oracle PGQ ({cfg['dsn']}, domain={domain}):")
    print(f"  {n} nodes, {e} edges → {cfg['graph_name']}")
    print(f"  graph cache → {graph_path}")
    return 0


def cmd_oracle_status(args: argparse.Namespace) -> int:
    """Report Oracle AI DB Free connectivity and stored graph stats."""
    from ckg.storage.connection import oracle_summary

    summary = oracle_summary()
    if not summary["configured"]:
        print(f"Oracle PGQ: not configured — {summary['reason']}")
        print("Set CKG_ORACLE_DSN=CKG_ORACLE_USER/PASSWORD to enable.")
        return 0
    print(f"Oracle PGQ: configured  → {summary['dsn']} (domain={summary['domain']})")
    if not summary["connected"]:
        print(f"  connection: FAILED — {summary.get('error', 'unknown error')}")
        return 1
    print(f"  version:    {summary.get('version', 'unknown')}")
    print(f"  connected:  yes")
    print(f"  nodes:      {summary.get('nodes', 'n/a')}")
    print(f"  edges:      {summary.get('edges', 'n/a')}")
    print(f"  property graph: {'present' if summary.get('property_graph') else 'missing'}")
    if "stats_error" in summary:
        print(f"  (stats: {summary['stats_error']})")
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    """Run hybrid retrieval against a cached graph (PGQ when Oracle configured)."""
    graph_path = Path(args.graph or ".ckg/code_graph.json")
    if not graph_path.exists():
        print(f"error: no graph at {graph_path}. Run 'ckg build' first.", file=sys.stderr)
        return 1

    graph = json.loads(graph_path.read_text())
    from ckg.retrieval.hybrid import hybrid_retrieve

    mem, cfg = (None, None)
    if not getattr(args, "no_pgq", False):
        mem, cfg = _oracle_mem()

    results = hybrid_retrieve(
        args.query, graph,
        mem=mem,
        domain=cfg["domain"] if cfg else None,
        k_anchor=int(args.k_anchor or 5),
        hops=int(args.hops or 2),
        top_k=int(args.top_k or 10),
        graph_name=cfg["graph_name"] if cfg else "ckg_code_graph",
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
    """Build + inject: generate the full agent context map (PGQ when configured)."""
    from ckg.claude.plugin import inject_context

    project_root = Path(args.root).resolve() if args.root else None

    mem, cfg = (None, None)
    if not getattr(args, "no_pgq", False):
        mem, cfg = _oracle_mem()

    result = inject_context(
        args.query,
        project_root=project_root,
        mem=mem,
        domain=cfg["domain"] if cfg else None,
    )

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

    # load
    sp_load = sub.add_parser(
        "load",
        help="Parse a Python tree and store it into Oracle PGQ (requires CKG_ORACLE_DSN)",
    )
    sp_load.add_argument("path", help="Path to the Python package directory")
    sp_load.add_argument("--pkg-root", help="Top-level package name (default: dir name)")
    sp_load.add_argument("--graph", help="Graph cache path (default: .ckg/code_graph.json)")
    sp_load.add_argument("--domain", help="PGQ domain scope (overrides CKG_ORACLE_DOMAIN)")
    sp_load.set_defaults(func=cmd_load)

    # oracle-status
    sp_status = sub.add_parser(
        "oracle-status",
        help="Check Oracle AI DB Free connectivity and stored graph stats",
    )
    sp_status.set_defaults(func=cmd_oracle_status)

    # query
    sp_query = sub.add_parser("query", help="Hybrid retrieval against a cached graph")
    sp_query.add_argument("query", help="Feature description or query")
    sp_query.add_argument("--graph", help="Path to cached code graph JSON")
    sp_query.add_argument("--k-anchor", type=int, help="Max lexical anchors (default 5)")
    sp_query.add_argument("--hops", type=int, help="Graph reach depth (default 2)")
    sp_query.add_argument("--top-k", type=int, help="Top results to return (default 10)")
    sp_query.add_argument(
        "--no-pgq", action="store_true", help="Force in-memory retrieval (skip Oracle)"
    )
    sp_query.set_defaults(func=cmd_query)

    # inject
    sp_inject = sub.add_parser("inject", help="Build + inject context map (agent mode)")
    sp_inject.add_argument("query", help="Task description")
    sp_inject.add_argument("--root", help="Project root (auto-detected if omitted)")
    sp_inject.add_argument(
        "--no-pgq", action="store_true", help="Force in-memory retrieval (skip Oracle)"
    )
    sp_inject.set_defaults(func=cmd_inject)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
