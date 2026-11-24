"""CKG CLI — build, load, query, and inject code knowledge graphs.

Usage:
    ckg build <path> [--pkg-root NAME]       # Parse a Python tree into a code graph
    ckg load <path> [--pkg-root NAME]        # Parse + store into Oracle PGQ (needs CKG_ORACLE_DSN)
    ckg query <query> [--graph FILE]         # Hybrid retrieval (PGQ when Oracle configured)
    ckg inject <query> [--root DIR]          # Structure map for a prompt (agent mode)
    ckg oracle-status                        # Oracle AI Database connectivity + stats

Examples:
    ckg build . --pkg-root mypackage
    ckg load . --pkg-root mypackage --domain myapp        # → Oracle PGQ
    ckg query "add JWT authentication" --graph .ckg/code_graph.json
    ckg inject --root ~/projects/myapp -- "fix the rate limiter"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ckg import __version__


def _oracle_mem():
    """Connect to Oracle PGQ. Returns (mem, cfg) or (None, None).

    A database that is configured but unreachable is reported once on stderr;
    retrieval then runs in memory.
    """
    from ckg.storage.connection import connect_pgq, last_connect_error, oracle_config

    cfg = oracle_config()
    if cfg is None:
        return None, None
    mem, cfg = connect_pgq(cfg)
    if mem is None:
        reason = last_connect_error() or "connection failed"
        print(
            f"warning: Oracle configured but unreachable ({reason}); "
            "using in-memory retrieval",
            file=sys.stderr,
        )
    return mem, cfg


def _opt(value: int | None, default: int) -> int:
    """CLI int option with a default only when the flag was omitted (0 is valid)."""
    return default if value is None else int(value)


def _non_negative(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return value


def _positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return value


def cmd_build(args: argparse.Namespace) -> int:
    """Parse a Python source tree into a code graph and cache it."""
    from ckg.graph.cache import build_and_store, cache_path

    quiet = getattr(args, "quiet", False)
    lock = getattr(args, "lock", None)
    try:
        tree = Path(args.path).resolve()
        if not tree.is_dir():
            print(f"error: {tree} is not a directory", file=sys.stderr)
            return 1

        pkg_root = args.pkg_root or tree.name
        output = Path(args.output) if args.output else cache_path(tree)
        if not quiet:
            print(f"Parsing {tree} (pkg_root={pkg_root}) ...")
        try:
            graph = build_and_store(tree, pkg_root=pkg_root, output=output)
        except OSError as exc:
            print(f"error: cannot write graph to {output}: {exc}", file=sys.stderr)
            return 1
        if not quiet:
            print(f"Wrote {len(graph['nodes'])} nodes, {len(graph['edges'])} edges → {output}")
            print(f"  files: {graph['meta']['n_files']}")
        return 0
    finally:
        if lock:
            Path(lock).unlink(missing_ok=True)


def cmd_load(args: argparse.Namespace) -> int:
    """Parse a Python source tree and store it into Oracle PGQ.

    Builds the code graph exactly like ``ckg build``, then makes the domain's
    rows in the Oracle node/edge tables match it (creating the tables when
    missing) and (re)creates the SQL property graph so GRAPH_TABLE ... MATCH
    queries run in the database.
    """
    from ckg.graph.cache import build_and_store, cache_path
    from ckg.storage.connection import (
        close_pgq,
        connect_pgq,
        last_connect_error,
        oracle_config,
        redact_dsn,
    )
    from ckg.storage.oracle_pgq import (
        create_property_graph,
        ensure_schema,
        replace_domain_graph,
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

    tree = Path(args.path).resolve()
    if not tree.is_dir():
        print(f"error: {tree} is not a directory", file=sys.stderr)
        return 1

    # Connect only after validating inputs so errors are meaningful.
    mem, _ = connect_pgq(cfg)
    if mem is None:
        reason = last_connect_error()
        print(
            f"error: cannot connect to Oracle at {redact_dsn(cfg['dsn'])}"
            + (f" ({reason})" if reason else "")
            + " — check CKG_ORACLE_DSN/USER/PASSWORD and that the database is up",
            file=sys.stderr,
        )
        return 1

    pkg_root = args.pkg_root or tree.name
    graph_path = Path(args.graph) if args.graph else cache_path(tree)
    try:
        try:
            graph = build_and_store(tree, pkg_root=pkg_root, output=graph_path)
        except OSError as exc:
            print(f"error: cannot write graph to {graph_path}: {exc}", file=sys.stderr)
            return 1

        domain = cfg["domain"]
        try:
            ensure_schema(mem, table_prefix=cfg["table_prefix"])
            n, e = replace_domain_graph(
                mem, list(graph["nodes"].values()), graph["edges"],
                domain=domain, table_prefix=cfg["table_prefix"],
            )
            create_property_graph(
                mem, graph_name=cfg["graph_name"], table_prefix=cfg["table_prefix"],
            )
        except Exception as exc:  # driver errors carry the ORA code on the first line
            first = (str(exc).splitlines() or [type(exc).__name__])[0]
            print(f"error: storing the graph in Oracle failed: {first}", file=sys.stderr)
            return 1
    finally:
        close_pgq(mem)

    print(f"Stored into Oracle PGQ ({redact_dsn(cfg['dsn'])}, domain={domain}):")
    print(f"  {n} nodes, {e} edges → {cfg['graph_name']}")
    print(f"  graph cache → {graph_path}")
    return 0


def cmd_oracle_status(args: argparse.Namespace) -> int:
    """Report Oracle connectivity, stored graph stats and the retrieval mode."""
    from ckg.storage.connection import oracle_summary

    summary = oracle_summary()
    if not summary["configured"]:
        print(f"Oracle PGQ: not configured — {summary['reason']}")
        print("  retrieval mode: memory")
        print("Set CKG_ORACLE_DSN (e.g. localhost:1521/FREEPDB1) to enable.")
        return 0
    print(f"Oracle PGQ: configured  → {summary['dsn']} (domain={summary['domain']})")
    if not summary["connected"]:
        print(f"  connection: FAILED — {summary.get('error', 'unknown error')}")
        print("  retrieval mode: memory (database unreachable)")
        return 1
    has_graph = bool(summary.get("property_graph"))
    print(f"  version:    {summary.get('version', 'unknown')}")
    print("  connected:  yes")
    print(f"  nodes:      {summary.get('nodes', 'n/a')}")
    print(f"  edges:      {summary.get('edges', 'n/a')}")
    print(f"  property graph: {'present' if has_graph else 'missing'}")
    print(f"  retrieval mode: {'pgq' if has_graph else 'memory (run ckg load)'}")
    if "stats_error" in summary:
        print(f"  (stats: {summary['stats_error']})")
    return 0


def _graph_for_query(graph_path: Path, mem, cfg) -> dict | None:
    """The graph to query: the local file, else the copy stored in Oracle."""
    from ckg.graph.cache import read_graph

    try:
        return read_graph(graph_path)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        print(f"error: cannot read graph {graph_path}: {exc}. Re-run 'ckg build'.",
              file=sys.stderr)
        return None

    if mem is not None:
        from ckg.storage.oracle_pgq import load_graph

        try:
            graph = load_graph(mem, domain=cfg["domain"], table_prefix=cfg["table_prefix"])
        except Exception:
            graph = None
        if graph and graph.get("nodes"):
            return graph
    print(f"error: no graph at {graph_path}. Run 'ckg build' first.", file=sys.stderr)
    return None


def cmd_query(args: argparse.Namespace) -> int:
    """Run hybrid retrieval against a cached graph (PGQ when Oracle configured)."""
    from ckg.retrieval.hybrid import hybrid_retrieve
    from ckg.storage.connection import close_pgq

    mem, cfg = (None, None)
    if not getattr(args, "no_pgq", False):
        mem, cfg = _oracle_mem()
    try:
        graph = _graph_for_query(Path(args.graph or ".ckg/code_graph.json"), mem, cfg)
        if graph is None:
            return 1
        results = hybrid_retrieve(
            args.query, graph,
            mem=mem,
            domain=cfg["domain"] if cfg else None,
            k_anchor=_opt(args.k_anchor, 5),
            hops=_opt(args.hops, 2),
            top_k=_opt(args.top_k, 10),
            graph_name=cfg["graph_name"] if cfg else "ckg_code_graph",
        )
    finally:
        close_pgq(mem)

    if getattr(args, "json", False):
        print(json.dumps(results, indent=2))
        return 0
    print(f"Retrieval ({results['method']}):")
    print(f"  Anchors: {', '.join(results['anchor_labels']) or '(none)'}")
    print()
    for r in results["results"]:
        print(f"  {r['score']:.4f}  {r['node_id'].removeprefix('file:')}")
    return 0


def cmd_inject(args: argparse.Namespace) -> int:
    """Print the structure map for a prompt (PGQ when configured)."""
    from ckg.claude.plugin import inject_context
    from ckg.storage.connection import close_pgq

    project_root = Path(args.root).resolve() if args.root else None

    mem, cfg = (None, None)
    if not getattr(args, "no_pgq", False):
        mem, cfg = _oracle_mem()
    try:
        result = inject_context(
            args.query,
            project_root=project_root,
            mem=mem,
            domain=cfg["domain"] if cfg else None,
            graph_name=cfg["graph_name"] if cfg else "ckg_code_graph",
            refresh=getattr(args, "refresh", "auto"),
        )
    finally:
        close_pgq(mem)

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
    ap.add_argument("--version", action="version", version=f"ckg {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    # build
    sp_build = sub.add_parser("build", help="Parse a Python tree into a code graph")
    sp_build.add_argument("path", help="Path to the project or package directory")
    sp_build.add_argument("--pkg-root", help="Top-level package name (default: dir name)")
    sp_build.add_argument("--output", help="Output path (default: <path>/.ckg/code_graph.json)")
    sp_build.add_argument("--quiet", action="store_true", help="Print errors only")
    sp_build.add_argument("--lock", help=argparse.SUPPRESS)  # removed when the build ends
    sp_build.set_defaults(func=cmd_build)

    # load
    sp_load = sub.add_parser(
        "load",
        help="Parse a Python tree and store it into Oracle PGQ (requires CKG_ORACLE_DSN)",
    )
    sp_load.add_argument("path", help="Path to the project or package directory")
    sp_load.add_argument("--pkg-root", help="Top-level package name (default: dir name)")
    sp_load.add_argument("--graph", help="Graph cache path (default: <path>/.ckg/code_graph.json)")
    sp_load.add_argument("--domain", help="PGQ domain scope (overrides CKG_ORACLE_DOMAIN)")
    sp_load.set_defaults(func=cmd_load)

    # oracle-status
    sp_status = sub.add_parser(
        "oracle-status",
        help="Check Oracle connectivity, stored graph stats and the retrieval mode",
    )
    sp_status.set_defaults(func=cmd_oracle_status)

    # query
    sp_query = sub.add_parser("query", help="Hybrid retrieval against a cached graph")
    sp_query.add_argument("query", help="Feature description or query")
    sp_query.add_argument(
        "--graph",
        help="Path to a graph JSON (default: .ckg/code_graph.json; when absent "
             "and Oracle is configured, the graph stored there is used)",
    )
    sp_query.add_argument("--k-anchor", type=_non_negative, help="Max lexical anchors (default 5)")
    sp_query.add_argument("--hops", type=_positive, help="Graph reach depth (default 2)")
    sp_query.add_argument("--top-k", type=_non_negative, help="Top results to return (default 10)")
    sp_query.add_argument("--json", action="store_true", help="Print the result as JSON")
    sp_query.add_argument(
        "--no-pgq", action="store_true", help="Force in-memory retrieval (skip Oracle)"
    )
    sp_query.set_defaults(func=cmd_query)

    # inject
    sp_inject = sub.add_parser("inject", help="Print the structure map for a prompt (agent mode)")
    sp_inject.add_argument("query", help="Task description")
    sp_inject.add_argument("--root", help="Project root (auto-detected if omitted)")
    sp_inject.add_argument(
        "--refresh", choices=("auto", "sync", "background"), default="auto",
        help="Missing or stale graph cache: 'auto' builds a missing one now and "
             "refreshes a stale one in the background (default); 'sync' always "
             "builds now; 'background' never builds in this process",
    )
    sp_inject.add_argument(
        "--no-pgq", action="store_true", help="Force in-memory retrieval (skip Oracle)"
    )
    sp_inject.set_defaults(func=cmd_inject)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
