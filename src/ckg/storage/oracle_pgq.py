"""Oracle SQL/PGQ property graph for code graph storage and traversal.

The graph lives in two relational tables, ``<PREFIX>_NODES`` keyed on
(id, domain) and ``<PREFIX>_EDGES`` keyed on (src, dst, kind, domain), with a
SQL property graph defined over them so dependency neighbourhoods are matched
in the database with ``GRAPH_TABLE ... MATCH``. A *domain* scopes one stored
graph, so several projects share the same tables.

This is the traversal half of retrieval. Ranking (Personalized PageRank)
runs in Python over the matched neighbourhood.
"""

from __future__ import annotations

import re

DEFAULT_GRAPH_NAME = "ckg_code_graph"
DEFAULT_TABLE_PREFIX = "MEMORY_GRAPH"

_IDENTIFIER_PART = r"[A-Za-z][A-Za-z0-9_$#]*"
_IDENTIFIER_RE = re.compile(_IDENTIFIER_PART + r"\Z")
_QUALIFIED_IDENTIFIER_RE = re.compile(_IDENTIFIER_PART + r"(?:\." + _IDENTIFIER_PART + r")?\Z")
_IDENTIFIER_MAX = 128
# Reserved words that are plausible as a graph name or prefix and would turn
# the DDL below into a syntax error instead of a clear message.
_RESERVED = frozenset({
    "ALL", "AND", "AS", "BY", "CREATE", "DELETE", "DROP", "FOR", "FROM", "GRANT",
    "GROUP", "IN", "INDEX", "INSERT", "INTO", "IS", "NOT", "NULL", "OF", "ON",
    "OR", "ORDER", "SELECT", "SET", "TABLE", "TO", "UNION", "UPDATE", "USER",
    "VALUES", "VIEW", "WHERE", "WITH",
})

# Column sizes of the node/edge tables (bytes).
_ID_MAX_BYTES = 256
_DOMAIN_MAX_BYTES = 32
_KIND_MAX_BYTES = 32

_BATCH_ROWS = 1000
# Oracle caps an IN list at 1000 expressions.
_IN_LIST_MAX = 500


def require_identifier(value: str, *, kind: str, allow_qualified: bool = False) -> str:
    """Validate an Oracle identifier before it is interpolated into SQL.

    Only graph names and table prefixes are ever interpolated (row values are
    always bound parameters). Validating them keeps config/env values from
    injecting DDL/DML through identifiers. Pass ``allow_qualified=True`` for
    graph names, which may be schema-qualified (``SCHEMA.GRAPH``). Raises
    ValueError otherwise.
    """
    pattern = _QUALIFIED_IDENTIFIER_RE if allow_qualified else _IDENTIFIER_RE
    if (
        not isinstance(value, str)
        or not pattern.fullmatch(value)
        or len(value) > _IDENTIFIER_MAX
        or any(part.upper() in _RESERVED for part in value.split("."))
    ):
        raise ValueError(f"invalid Oracle {kind}: {value!r}")
    return value


def _tables(table_prefix: str) -> tuple[str, str]:
    prefix = require_identifier(table_prefix, kind="table prefix")
    nodes, edges = f"{prefix}_NODES", f"{prefix}_EDGES"
    if len(nodes) > _IDENTIFIER_MAX:
        raise ValueError(f"invalid Oracle table prefix: {table_prefix!r}")
    return nodes, edges


def _require_domain(domain: str) -> str:
    if not isinstance(domain, str) or not domain or len(domain.encode()) > _DOMAIN_MAX_BYTES:
        raise ValueError(
            f"invalid domain {domain!r}: must be 1..{_DOMAIN_MAX_BYTES} bytes"
        )
    return domain


def _require_pool(mem):
    pool = getattr(mem, "_pool", None)
    if pool is None:
        raise RuntimeError("Oracle PGQ requires a live Oracle connection pool")
    return pool


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #

def ensure_schema(mem, *, table_prefix: str = DEFAULT_TABLE_PREFIX) -> None:
    """Create the node and edge tables when they do not exist yet, and add
    the edge ``weight`` column to an edge table created without one."""
    nodes_table, edges_table = _tables(table_prefix)
    pool = _require_pool(mem)
    ddl = {
        nodes_table: f"""
            CREATE TABLE {nodes_table} (
              id         VARCHAR2({_ID_MAX_BYTES} BYTE) NOT NULL,
              domain     VARCHAR2({_DOMAIN_MAX_BYTES} BYTE) NOT NULL,
              text       CLOB,
              updated_ts TIMESTAMP DEFAULT SYSTIMESTAMP,
              PRIMARY KEY (id, domain)
            )""",
        edges_table: f"""
            CREATE TABLE {edges_table} (
              src        VARCHAR2({_ID_MAX_BYTES} BYTE) NOT NULL,
              dst        VARCHAR2({_ID_MAX_BYTES} BYTE) NOT NULL,
              kind       VARCHAR2({_KIND_MAX_BYTES} BYTE) NOT NULL,
              weight     NUMBER DEFAULT 1,
              domain     VARCHAR2({_DOMAIN_MAX_BYTES} BYTE) NOT NULL,
              updated_ts TIMESTAMP DEFAULT SYSTIMESTAMP,
              PRIMARY KEY (src, dst, kind, domain)
            )""",
    }
    with pool.acquire() as conn:
        cur = conn.cursor()
        for table, statement in ddl.items():
            cur.execute(
                "SELECT COUNT(*) FROM user_tables WHERE table_name = :t", t=table.upper()
            )
            if cur.fetchone()[0] == 0:
                cur.execute(statement)
        cur.execute(
            "SELECT COUNT(*) FROM user_tab_columns "
            "WHERE table_name = :t AND column_name = 'WEIGHT'",
            t=edges_table.upper(),
        )
        if cur.fetchone()[0] == 0:
            cur.execute(f"ALTER TABLE {edges_table} ADD (weight NUMBER DEFAULT 1)")


def create_property_graph(
    mem, *, graph_name: str = DEFAULT_GRAPH_NAME, table_prefix: str = DEFAULT_TABLE_PREFIX
) -> None:
    """Create the SQL property graph over the node/edge tables, replacing an
    existing definition of the same name.

    (id, domain) is the vertex key and (src, dst, kind, domain) the edge key,
    with domain exposed as a property on both so queries can scope to one
    stored graph.
    """
    graph_name = require_identifier(graph_name, kind="graph name", allow_qualified=True)
    nodes_table, edges_table = _tables(table_prefix)
    pool = _require_pool(mem)
    ddl = f"""
        CREATE OR REPLACE PROPERTY GRAPH {graph_name}
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
        conn.cursor().execute(ddl)


# --------------------------------------------------------------------------- #
# Store / load
# --------------------------------------------------------------------------- #

def _fits(value: str) -> bool:
    return len(value.encode()) <= _ID_MAX_BYTES


def replace_domain_graph(
    mem,
    nodes: list[dict],
    edges: list[dict],
    *,
    domain: str,
    table_prefix: str = DEFAULT_TABLE_PREFIX,
) -> tuple[int, int]:
    """Make the domain's stored graph exactly ``nodes`` and ``edges``.

    Runs as one transaction: rows of the domain that are not in the new graph
    (deleted files, renamed symbols) disappear with the same commit that
    stores the new ones, and a failure leaves the previous graph in place.
    Other domains are not touched. Nodes whose id does not fit the id column,
    and edges touching them, are left out.

    Returns the number of (nodes, edges) stored.
    """
    nodes_table, edges_table = _tables(table_prefix)
    domain = _require_domain(domain)
    pool = _require_pool(mem)

    node_rows = {
        n["id"]: (n["id"], domain, n.get("text", ""))
        for n in nodes
        if _fits(n["id"])
    }
    edge_rows = {
        (e["src"], e["dst"], e["kind"]): (
            e["src"], e["dst"], e["kind"], float(e.get("weight", 1)), domain,
        )
        for e in edges
        if e["src"] in node_rows and e["dst"] in node_rows
        and len(e["kind"].encode()) <= _KIND_MAX_BYTES
    }

    with pool.acquire() as conn:
        cur = conn.cursor()
        try:
            cur.execute(f"DELETE FROM {edges_table} WHERE domain = :d", d=domain)
            cur.execute(f"DELETE FROM {nodes_table} WHERE domain = :d", d=domain)
            _insert_batches(
                cur, f"INSERT INTO {nodes_table} (id, domain, text) VALUES (:1, :2, :3)",
                list(node_rows.values()),
            )
            _insert_batches(
                cur,
                f"INSERT INTO {edges_table} (src, dst, kind, weight, domain) "
                "VALUES (:1, :2, :3, :4, :5)",
                list(edge_rows.values()),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return len(node_rows), len(edge_rows)


def _insert_batches(cur, sql: str, rows: list[tuple]) -> None:
    for start in range(0, len(rows), _BATCH_ROWS):
        cur.executemany(sql, rows[start:start + _BATCH_ROWS])


def load_graph(mem, *, domain: str, table_prefix: str = DEFAULT_TABLE_PREFIX) -> dict:
    """Read a stored graph back as a graph dict (``ckg query`` uses it when
    there is no local graph file)."""
    nodes_table, edges_table = _tables(table_prefix)
    pool = _require_pool(mem)
    with pool.acquire() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT id, text FROM {nodes_table} WHERE domain = :d ORDER BY id", d=domain
        )
        nodes = {}
        for nid, text in cur:
            if text is not None and not isinstance(text, str):
                text = text.read()  # CLOB locator
            kind = "file" if nid.startswith("file:") else "sym" if nid.startswith("sym:") else "node"
            nodes[nid] = {"id": nid, "kind": kind, "text": text or ""}
        cur.execute(
            f"SELECT src, dst, kind, weight FROM {edges_table} WHERE domain = :d "
            "ORDER BY kind, src, dst",
            d=domain,
        )
        edges = []
        for src, dst, kind, weight in cur:
            edge = {"src": src, "dst": dst, "kind": kind}
            if weight is not None and weight != 1:
                edge["weight"] = int(weight) if float(weight).is_integer() else float(weight)
            edges.append(edge)
    return {"nodes": nodes, "edges": edges}


# --------------------------------------------------------------------------- #
# Traversal
# --------------------------------------------------------------------------- #

def match_neighborhood_bulk(
    mem,
    *,
    anchors: list[str],
    domain: str,
    hops: int = 2,
    graph_name: str = DEFAULT_GRAPH_NAME,
) -> list[dict]:
    """Nodes within ``hops`` edges of any anchor, in either edge direction.

    Returns ``[{"neighbor": id, "hops": n}]`` with the shortest distance per
    node, anchors excluded, ordered by (hops, neighbor). Dependencies matter
    both ways — what an anchor uses and what uses it — so edges are matched
    undirected. One statement runs per hop for all anchors together.
    """
    if hops < 1:
        raise ValueError("hops must be >= 1")
    graph_name = require_identifier(graph_name, kind="graph name", allow_qualified=True)
    pool = _require_pool(mem)
    anchors = sorted(set(anchors))
    if not anchors:
        return []

    best: dict[str, int] = {}
    anchor_set = set(anchors)
    with pool.acquire() as conn:
        cur = conn.cursor()
        for h in range(1, int(hops) + 1):
            chain = "".join(f" -[]- (n{i})" for i in range(h - 1)) + " -[]- (w)"
            for start in range(0, len(anchors), _IN_LIST_MAX):
                chunk = anchors[start:start + _IN_LIST_MAX]
                binds = {f"a{i}": anchor for i, anchor in enumerate(chunk)}
                in_list = ", ".join(f":{name}" for name in binds)
                sql = f"""
                    SELECT DISTINCT neighbor FROM GRAPH_TABLE ({graph_name}
                      MATCH (v){chain}
                      WHERE v.domain = :domain AND v.id IN ({in_list})
                      COLUMNS (w.id AS neighbor)
                    )
                """
                cur.execute(sql, {"domain": domain, **binds})
                for (neighbor,) in cur.fetchall():
                    if neighbor not in anchor_set:
                        best.setdefault(neighbor, h)
    return [
        {"neighbor": neighbor, "hops": h}
        for neighbor, h in sorted(best.items(), key=lambda kv: (kv[1], kv[0]))
    ]
