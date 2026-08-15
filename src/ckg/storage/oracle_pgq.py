"""Oracle SQL/PGQ property graph for code graph storage and traversal.

Creates a property graph over MEMORY_GRAPH_NODES / MEMORY_GRAPH_EDGES (relational
tables keyed on (id, domain) and (src, dst, kind, domain)) so dependency
neighborhoods are matched entirely in the database with GRAPH_TABLE ... MATCH.

This is the *traversal/match* half of structure-aware retrieval. Ranking
(Personalized PageRank) still runs in Python over the matched neighborhood —
PGQ finds the structural edges; Python scores them.

Supports two operation modes:
  Live Oracle PGQ  — GRAPH_TABLE MATCH with quantified paths
  Offline fallback — in-memory traversal from a loaded graph dict
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_GRAPH_NAME = "ckg_code_graph"


# --------------------------------------------------------------------------- #
# Oracle PGQ (live database)
# --------------------------------------------------------------------------- #

def create_property_graph(
    mem, *, graph_name: str = DEFAULT_GRAPH_NAME, table_prefix: str = "MEMORY_GRAPH"
) -> None:
    """(Re)create the Oracle SQL property graph.

    Idempotent: drops an existing graph of the same name first. Exposes (id,
    domain) as vertex key and (src, dst, kind, domain) as edge key, with domain
    as a property on both so queries can scope to a single graphify snapshot.

    Args:
        mem: An AgentMemory instance with a live Oracle connection pool.
        graph_name: Property graph name in the database.
        table_prefix: Prefix for the node/edge tables.
    """
    pool = _require_pool(mem)
    nodes_table = f"{table_prefix}_NODES"
    edges_table = f"{table_prefix}_EDGES"
    ddl = f"""
        CREATE PROPERTY GRAPH {graph_name}
          VERTEX TABLES (
            {nodes_table}
              KEY (id, domain)
              PROPERTIES (id, domain)
          )
          EDGE TABLES (
            {edges_table}
              KEY (src, dst, kind, domain)
              SOURCE      KEY (src, domain) REFERENCES {nodes_table} (id, domain)
              DESTINATION KEY (dst, domain) REFERENCES {nodes_table} (id, domain)
              PROPERTIES (kind, domain)
          )
    """
    with pool.acquire() as conn:
        cur = conn.cursor()
        try:
            cur.execute(f"DROP PROPERTY GRAPH {graph_name}")
        except Exception:
            pass  # First run: nothing to drop
        cur.execute(ddl)
        conn.commit()


def match_neighborhood(
    mem, *, anchor: str, domain: str,
    hops: int = 2, graph_name: str = DEFAULT_GRAPH_NAME,
) -> list[dict]:
    """Match the 1..hops dependency neighborhood of anchor in domain.

    Returns a list of {"neighbor": str, "hops": int} rows — distinct symbols
    reachable within hops edges, found purely by the Oracle graph engine,
    ordered by shortest reach.
    """
    if hops < 1:
        raise ValueError("hops must be >= 1")
    pool = _require_pool(mem)
    best: dict[str, int] = {}
    with pool.acquire() as conn:
        cur = conn.cursor()
        for h in range(1, int(hops) + 1):
            chain = "".join(f"-[]->(n{i})" for i in range(h - 1)) + "-[]->(w)"
            sql = f"""
                SELECT neighbor FROM GRAPH_TABLE ({graph_name}
                  MATCH (v){chain}
                  WHERE v.id = :anchor AND v.domain = :domain
                  COLUMNS (w.id AS neighbor)
                )
            """
            cur.execute(sql, {"anchor": anchor, "domain": domain})
            for (nb,) in cur.fetchall():
                if nb != anchor and (nb not in best or h < best[nb]):
                    best[nb] = h
    return [{"neighbor": nb, "hops": h}
            for nb, h in sorted(best.items(), key=lambda kv: (kv[1], kv[0]))]


def match_edges(
    mem, *, anchor: str, domain: str,
    graph_name: str = DEFAULT_GRAPH_NAME,
) -> list[dict]:
    """Match the 1-hop edges from ``anchor`` in ``domain``.

    Returns a list of {"neighbor": str, "kind": str} rows — the direct
    dependency edges (import, call, co_edit) so the caller can label each
    connection by its edge type.
    """
    pool = _require_pool(mem)
    sql = f"""
        SELECT neighbor, kind FROM GRAPH_TABLE ({graph_name}
          MATCH (v) -[e]-> (w)
          WHERE v.id = :anchor AND v.domain = :domain
          COLUMNS (w.id AS neighbor, e.kind AS kind)
        )
    """
    out: list[dict] = []
    with pool.acquire() as conn:
        cur = conn.cursor()
        cur.execute(sql, {"anchor": anchor, "domain": domain})
        for nb, kind in cur.fetchall():
            out.append({"neighbor": nb, "kind": kind})
    return out


# --------------------------------------------------------------------------- #
# Upsert helpers — load parsed graph into Oracle tables
# --------------------------------------------------------------------------- #

def upsert_graph_nodes(
    mem, nodes: list[dict], *, domain: str, table_prefix: str = "MEMORY_GRAPH",
) -> int:
    """Insert or update graph nodes in Oracle.

    Uses MERGE so re-runs are idempotent.
    """
    pool = _require_pool(mem)
    with pool.acquire() as conn:
        cur = conn.cursor()
        count = 0
        for node in nodes:
            nid = node["id"]
            text = node.get("text", "")
            cur.execute(
                f"MERGE INTO {table_prefix}_NODES t "
                "USING (SELECT :id AS id, :domain AS domain FROM DUAL) s "
                "ON (t.id = s.id AND t.domain = s.domain) "
                "WHEN MATCHED THEN UPDATE SET t.text = :text "
                "WHEN NOT MATCHED THEN INSERT (id, domain, text) "
                "VALUES (:id, :domain, :text)",
                id=nid, domain=domain, text=text,
            )
            count += 1
        conn.commit()
    return count


def upsert_graph_edges(
    mem, edges: list[dict], *, domain: str, table_prefix: str = "MEMORY_GRAPH",
) -> int:
    """Insert or update graph edges in Oracle."""
    pool = _require_pool(mem)
    with pool.acquire() as conn:
        cur = conn.cursor()
        count = 0
        for edge in edges:
            src, dst, kind = edge["src"], edge["dst"], edge["kind"]
            cur.execute(
                f"MERGE INTO {table_prefix}_EDGES t "
                "USING (SELECT :src AS src, :dst AS dst, :kind AS kind, "
                ":domain AS domain FROM DUAL) s "
                "ON (t.src = s.src AND t.dst = s.dst AND t.kind = s.kind "
                "AND t.domain = s.domain) "
                "WHEN NOT MATCHED THEN INSERT (src, dst, kind, domain) "
                "VALUES (:src, :dst, :kind, :domain)",
                src=src, dst=dst, kind=kind, domain=domain,
            )
            count += 1
        conn.commit()
    return count


def load_graph(
    mem, *, domain: str, table_prefix: str = "MEMORY_GRAPH",
) -> dict:
    """Load a previously stored graph from Oracle tables (offline fallback)."""
    pool = _require_pool(mem)
    with pool.acquire() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT id, text FROM {table_prefix}_NODES WHERE domain = :domain",
            domain=domain,
        )
        nodes = {row[0]: {"id": row[0], "text": row[1]} for row in cur}
        cur.execute(
            f"SELECT src, dst, kind FROM {table_prefix}_EDGES WHERE domain = :domain",
            domain=domain,
        )
        edges = [{"src": row[0], "dst": row[1], "kind": row[2]} for row in cur]
    return {"nodes": nodes, "edges": edges}


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #

def _require_pool(mem):
    pool = getattr(mem, "_pool", None)
    if pool is None:
        raise RuntimeError(
            "Oracle PGQ requires a live Oracle connection pool "
            "(AgentMemory built with memory_backend='real')."
        )
    return pool
