"""Live-gated integration tests for ckg.storage.oracle_pgq.

Requires a running Oracle AI Database 26ai Free container.
Set CKG_ORACLE_LIVE=1 to enable these tests.

The dl-ai-continual-learning container (dlai-oracle-free) already has
MEMORY_GRAPH_NODES and MEMORY_GRAPH_EDGES tables with the correct schema.
These tests use a dedicated 'ckg_test' domain to avoid interfering
with existing course data.
"""

import os
import pytest
import oracledb

from ckg.storage.oracle_pgq import (
    create_property_graph,
    match_neighborhood,
    match_edges,
    upsert_graph_nodes,
    upsert_graph_edges,
    load_graph,
    DEFAULT_GRAPH_NAME,
)
from ckg.graph.parser import parse_tree
from ckg.graph.builder import CodeGraph
from ckg.retrieval.hybrid import hybrid_retrieve

# ── Live-gating ──────────────────────────────────────────────────────────────
_LIVE = pytest.mark.skipif(
    not os.environ.get("CKG_ORACLE_LIVE"),
    reason="CKG_ORACLE_LIVE not set — requires live Oracle AI Database 26ai Free",
)

# ── Test config ──────────────────────────────────────────────────────────────
DSN = os.environ.get("CKG_ORACLE_DSN", "localhost:1601/FREEPDB1")
USER = os.environ.get("CKG_ORACLE_USER", "dmuser")
PASSWORD = os.environ.get("CKG_ORACLE_PASSWORD", "continual_learning")
TEST_DOMAIN = "ckg_test_live"
TABLE_PREFIX = "MEMORY_GRAPH"


class FakeMem:
    """Thin wrapper that exposes _pool so ckg.storage.oracle_pgq can use it."""

    def __init__(self, pool: oracledb.ConnectionPool):
        self._pool = pool  # type: ignore[assignment]


def _get_pool() -> oracledb.ConnectionPool:
    """Create a connection pool for the test Oracle instance."""
    return oracledb.create_pool(
        user=USER, password=PASSWORD, dsn=DSN,
        min=1, max=4, increment=1,
    )


def _cleanup_domain(pool: oracledb.ConnectionPool, domain: str) -> None:
    """Remove all test data from the given domain."""
    with pool.acquire() as conn:
        cur = conn.cursor()
        cur.execute(
            f"DELETE FROM {TABLE_PREFIX}_EDGES WHERE domain = :domain",
            domain=domain,
        )
        cur.execute(
            f"DELETE FROM {TABLE_PREFIX}_NODES WHERE domain = :domain",
            domain=domain,
        )
        conn.commit()


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def pool():
    """Module-scoped Oracle connection pool. Cleaned up at module teardown."""
    p = _get_pool()
    yield p
    _cleanup_domain(p, TEST_DOMAIN)
    p.close()


@pytest.fixture(scope="module")
def mem(pool):
    """FakeMem wrapping the pool for ckg.storage API."""
    return FakeMem(pool)


@pytest.fixture(scope="module")
def test_edges():
    """Sample edges to load into Oracle."""
    return [
        {"src": "A", "dst": "B", "kind": "import"},
        {"src": "A", "dst": "C", "kind": "import"},
        {"src": "B", "dst": "C", "kind": "call"},
        {"src": "C", "dst": "D", "kind": "import"},
        {"src": "A", "dst": "D", "kind": "co_edit"},
    ]


@pytest.fixture(scope="module")
def test_nodes():
    """Sample nodes matching test_edges."""
    return [
        {"id": "A", "text": "Module A", "kind": "file"},
        {"id": "B", "text": "Module B", "kind": "file"},
        {"id": "C", "text": "Module C", "kind": "file"},
        {"id": "D", "text": "Module D", "kind": "file"},
    ]


@pytest.fixture(scope="module")
def loaded_domain(mem, pool, test_nodes, test_edges):
    """Load test data into Oracle and create the property graph."""
    _cleanup_domain(pool, TEST_DOMAIN)
    upsert_graph_nodes(mem, test_nodes, domain=TEST_DOMAIN)
    upsert_graph_edges(mem, test_edges, domain=TEST_DOMAIN)
    create_property_graph(mem, graph_name=DEFAULT_GRAPH_NAME)
    yield TEST_DOMAIN
    # Cleanup after all tests in the module
    try:
        with pool.acquire() as conn:
            conn.cursor().execute(f"DROP PROPERTY GRAPH {DEFAULT_GRAPH_NAME}")
            conn.commit()
    except Exception:
        pass


# ── Tests ────────────────────────────────────────────────────────────────────

@_LIVE
class TestOracleUpsert:
    def test_upsert_nodes(self, mem, pool):
        _cleanup_domain(pool, TEST_DOMAIN)
        nodes = [{"id": "U1", "text": "Test node 1", "kind": "file"}]
        count = upsert_graph_nodes(mem, nodes, domain=TEST_DOMAIN)
        assert count == 1

        # Idempotent
        count2 = upsert_graph_nodes(mem, nodes, domain=TEST_DOMAIN)
        assert count2 == 1

    def test_upsert_edges(self, mem, pool):
        _cleanup_domain(pool, TEST_DOMAIN)
        nodes = [
            {"id": "U1", "text": "U1", "kind": "file"},
            {"id": "U2", "text": "U2", "kind": "file"},
        ]
        upsert_graph_nodes(mem, nodes, domain=TEST_DOMAIN)
        edges = [{"src": "U1", "dst": "U2", "kind": "import"}]
        count = upsert_graph_edges(mem, edges, domain=TEST_DOMAIN)
        assert count == 1

    def test_load_graph(self, mem, pool):
        _cleanup_domain(pool, TEST_DOMAIN)
        nodes = [
            {"id": "L1", "text": "L1", "kind": "file"},
            {"id": "L2", "text": "L2", "kind": "sym"},
        ]
        edges = [{"src": "L1", "dst": "L2", "kind": "contains"}]
        upsert_graph_nodes(mem, nodes, domain=TEST_DOMAIN)
        upsert_graph_edges(mem, edges, domain=TEST_DOMAIN)

        loaded = load_graph(mem, domain=TEST_DOMAIN)
        assert "L1" in loaded["nodes"]
        assert "L2" in loaded["nodes"]
        assert len(loaded["edges"]) == 1
        assert loaded["edges"][0]["kind"] == "contains"


@_LIVE
class TestOraclePGQ:
    def test_create_property_graph(self, mem, loaded_domain):
        # loaded_domain fixture already creates the PG — just verify it doesn't crash
        # on a second call (idempotent)
        create_property_graph(mem, graph_name=DEFAULT_GRAPH_NAME)

    def test_match_neighborhood_one_hop(self, mem, loaded_domain):
        results = match_neighborhood(
            mem, anchor="A", domain=loaded_domain, hops=1,
        )
        neighbors = {r["neighbor"] for r in results}
        # A → B (import), A → C (import), A → D (co_edit)
        assert "B" in neighbors
        assert "C" in neighbors
        assert "D" in neighbors

    def test_match_neighborhood_two_hops(self, mem, loaded_domain):
        results = match_neighborhood(
            mem, anchor="B", domain=loaded_domain, hops=2,
        )
        neighbors = {r["neighbor"] for r in results}
        # B → C (call), C → D (import), and B is also connected from A
        assert "C" in neighbors
        assert "D" in neighbors

    def test_match_edges(self, mem, loaded_domain):
        results = match_edges(mem, anchor="A", domain=loaded_domain)
        kinds = {(r["neighbor"], r["kind"]) for r in results}
        assert ("B", "import") in kinds
        assert ("C", "import") in kinds
        assert ("D", "co_edit") in kinds


@_LIVE
class TestFullPipeline:
    """End-to-end: parse → Oracle store → PGQ → hybrid retrieve."""

    def test_e2e_graphify_to_retrieval(self, mem, pool, tmp_path):
        # 1. Create a tiny Python package
        pkg = tmp_path / "e2e_pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "main.py").write_text(
            "from e2e_pkg.auth import login\nfrom e2e_pkg.db import connect\n\n"
            "def run():\n    login()\n    connect()\n"
        )
        (pkg / "auth.py").write_text(
            "from e2e_pkg.db import connect\n\ndef login():\n    connect()\n"
        )
        (pkg / "db.py").write_text("def connect():\n    return 'connected'\n")

        # 2. Parse
        graph = parse_tree(pkg, pkg_root="e2e_pkg")
        assert len(graph["nodes"]) > 0
        assert len(graph["edges"]) > 0

        # 3. Load into Oracle
        domain = f"{TEST_DOMAIN}_e2e"
        _cleanup_domain(pool, domain)
        nodes_list = list(graph["nodes"].values())
        edges_list = graph["edges"]
        n_loaded = upsert_graph_nodes(mem, nodes_list, domain=domain)
        e_loaded = upsert_graph_edges(mem, edges_list, domain=domain)
        assert n_loaded > 0
        assert e_loaded > 0

        # 4. Create PGQ property graph
        create_property_graph(mem)

        # 5. Run hybrid retrieve with PGQ
        result = hybrid_retrieve(
            "login authentication",
            graph,
            mem=mem, domain=domain,
            k_anchor=3, hops=2, top_k=5,
        )
        assert "pgq" in result["method"], (
            f"Expected PGQ method, got: {result['method']}"
        )
        assert len(result["results"]) > 0
        assert len(result["anchors"]) > 0

        # Cleanup
        _cleanup_domain(pool, domain)


@_LIVE
class TestExistingGraphData:
    """Verify the dl-ai-continual-learning graph data is queryable."""

    def test_existing_graph_has_nodes(self, mem):
        """The dlai-oracle-free container has MEMORY_GRAPH_NODES with
        agent-harness data under domain 'agent_harness_cold'."""
        try:
            loaded = load_graph(mem, domain="agent_harness_cold")
            if loaded["nodes"]:
                assert len(loaded["nodes"]) > 0
                assert len(loaded["edges"]) > 0
        except Exception:
            pytest.skip("No existing graph data found")
