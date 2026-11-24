"""Tests for ckg.storage against a recording fake pool (no live Oracle)."""

import time

import pytest

import ckg.storage.connection as connection
from ckg.storage.connection import connect_pgq, dsn_host, last_connect_error, oracle_config, redact_dsn
from ckg.storage.oracle_pgq import (
    create_property_graph,
    ensure_schema,
    load_graph,
    match_neighborhood_bulk,
    replace_domain_graph,
    require_identifier,
)

VALID_IDENTIFIERS = ["ckg_code_graph", "MEMORY_GRAPH", "A1$#"]
INVALID_IDENTIFIERS = [
    "", "1abc", "_x", "x y", 'x"; DROP TABLE y --', "x;DROP", "x\n", "GRAPH TABLE",
    "table", "SELECT", "User",
]


class FakeCursor:
    def __init__(self, pool):
        self.pool = pool
        self._rows = []

    def execute(self, sql, params=None, **kw):
        sql = " ".join(sql.split())
        self.pool.statements.append(("execute", sql, params if params is not None else kw))
        if self.pool.fail_on and self.pool.fail_on in sql:
            raise RuntimeError("ORA-00001: boom")
        self._rows = list(self.pool.results.get(next(
            (key for key in self.pool.results if key in sql), None), []))

    def executemany(self, sql, rows):
        sql = " ".join(sql.split())
        self.pool.statements.append(("executemany", sql, list(rows)))
        if self.pool.fail_on and self.pool.fail_on in sql:
            raise RuntimeError("ORA-00001: boom")

    def fetchone(self):
        return self._rows[0] if self._rows else (0,)

    def fetchall(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class FakeConn:
    def __init__(self, pool):
        self.pool = pool

    def cursor(self):
        return FakeCursor(self.pool)

    def commit(self):
        self.pool.statements.append(("commit", "", None))

    def rollback(self):
        self.pool.statements.append(("rollback", "", None))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakePool:
    """Records every statement; ``results`` maps an SQL substring to rows."""

    def __init__(self, results=None, fail_on=None):
        self.statements = []
        self.results = results or {}
        self.fail_on = fail_on

    def acquire(self):
        return FakeConn(self)

    def sql(self, kind="execute"):
        return [s for k, s, _ in self.statements if k == kind]


class Mem:
    def __init__(self, pool):
        self._pool = pool


class TestIdentifiers:
    @pytest.mark.parametrize("name", VALID_IDENTIFIERS)
    def test_accepts_valid(self, name):
        assert require_identifier(name, kind="graph name") == name

    @pytest.mark.parametrize("name", INVALID_IDENTIFIERS + ["a.b", "a.b.c", "a..b"])
    def test_rejects_invalid(self, name):
        with pytest.raises(ValueError):
            require_identifier(name, kind="table prefix")

    @pytest.mark.parametrize("name", VALID_IDENTIFIERS + ["MYSCHEMA.ckg_code_graph"])
    def test_allows_qualified_graph_name(self, name):
        assert require_identifier(name, kind="graph name", allow_qualified=True) == name

    @pytest.mark.parametrize("name", ["a.b.c", "a..b", ".b", "a.", "a.b;DROP", "myschema.table"])
    def test_rejects_bad_qualification(self, name):
        with pytest.raises(ValueError):
            require_identifier(name, kind="graph name", allow_qualified=True)

    def test_rejects_overlong(self):
        with pytest.raises(ValueError):
            require_identifier("a" * 129, kind="graph name")

    def test_prefix_must_leave_room_for_the_table_suffix(self):
        with pytest.raises(ValueError):
            ensure_schema(Mem(FakePool()), table_prefix="a" * 125)

    def test_injected_names_fail_before_any_sql(self):
        pool = FakePool()
        mem = Mem(pool)
        bad = "x; DROP TABLE t"
        with pytest.raises(ValueError):
            create_property_graph(mem, graph_name=bad)
        with pytest.raises(ValueError):
            match_neighborhood_bulk(mem, anchors=["a"], domain="d", graph_name=bad)
        with pytest.raises(ValueError):
            replace_domain_graph(mem, [], [], domain="d", table_prefix=bad)
        with pytest.raises(ValueError):
            load_graph(mem, domain="d", table_prefix=bad)
        with pytest.raises(ValueError):
            ensure_schema(mem, table_prefix=bad)
        assert pool.statements == []


class TestEnsureSchema:
    def test_creates_missing_tables(self):
        pool = FakePool()
        ensure_schema(Mem(pool), table_prefix="CKG")
        created = [s for s in pool.sql() if s.startswith("CREATE TABLE")]
        assert [s.split()[2] for s in created] == ["CKG_NODES", "CKG_EDGES"]
        assert "PRIMARY KEY (id, domain)" in created[0]
        assert "PRIMARY KEY (src, dst, kind, domain)" in created[1]

    def test_existing_tables_are_left_alone(self):
        pool = FakePool(results={"user_tables": [(1,)], "user_tab_columns": [(1,)]})
        ensure_schema(Mem(pool), table_prefix="CKG")
        assert not [s for s in pool.sql() if s.startswith(("CREATE", "ALTER", "DROP"))]

    def test_edge_table_without_weight_gets_the_column(self):
        pool = FakePool(results={"user_tables": [(1,)], "user_tab_columns": [(0,)]})
        ensure_schema(Mem(pool), table_prefix="CKG")
        assert [s for s in pool.sql() if s.startswith("ALTER")] == [
            "ALTER TABLE CKG_EDGES ADD (weight NUMBER DEFAULT 1)"
        ]


class TestReplaceDomainGraph:
    def _graph(self, n):
        nodes = [{"id": f"file:m{i}.py", "text": f"t{i}"} for i in range(n)]
        edges = [{"src": f"file:m{i}.py", "dst": f"file:m{i + 1}.py", "kind": "import"}
                 for i in range(n - 1)]
        return nodes, edges

    def test_batches_rows_instead_of_one_statement_each(self):
        pool = FakePool()
        nodes, edges = self._graph(2500)
        assert replace_domain_graph(Mem(pool), nodes, edges, domain="d") == (2500, 2499)
        batches = [rows for kind, _, rows in pool.statements if kind == "executemany"]
        assert [len(b) for b in batches] == [1000, 1000, 500, 1000, 1000, 499]
        assert len(pool.statements) < 12  # not O(rows)

    def test_replaces_only_its_own_domain_in_one_transaction(self):
        pool = FakePool()
        nodes, edges = self._graph(3)
        replace_domain_graph(Mem(pool), nodes, edges, domain="mine")
        kinds = [k for k, _, _ in pool.statements]
        assert kinds == ["execute", "execute", "executemany", "executemany", "commit"]
        deletes = [(s, p) for k, s, p in pool.statements if k == "execute"]
        assert deletes == [
            ("DELETE FROM MEMORY_GRAPH_EDGES WHERE domain = :d", {"d": "mine"}),
            ("DELETE FROM MEMORY_GRAPH_NODES WHERE domain = :d", {"d": "mine"}),
        ]
        node_rows = pool.statements[2][2]
        assert node_rows[0] == ("file:m0.py", "mine", "t0")

    def test_values_are_bound_never_interpolated(self):
        pool = FakePool()
        evil = "file:x'; DROP TABLE t --.py"
        replace_domain_graph(Mem(pool), [{"id": evil, "text": "'; --"}], [], domain="d")
        assert all("DROP" not in s for _, s, _ in pool.statements)

    def test_edge_weight_is_stored(self):
        pool = FakePool()
        nodes, _ = self._graph(2)
        edges = [{"src": "file:m0.py", "dst": "file:m1.py", "kind": "co_edit", "weight": 4}]
        replace_domain_graph(Mem(pool), nodes, edges, domain="d")
        assert pool.statements[3][2] == [("file:m0.py", "file:m1.py", "co_edit", 4.0, "d")]

    def test_failure_rolls_back_and_does_not_commit(self):
        pool = FakePool(fail_on="INSERT INTO MEMORY_GRAPH_EDGES")
        nodes, edges = self._graph(3)
        with pytest.raises(RuntimeError):
            replace_domain_graph(Mem(pool), nodes, edges, domain="d")
        kinds = [k for k, _, _ in pool.statements]
        assert kinds[-1] == "rollback" and "commit" not in kinds

    def test_oversized_ids_and_their_edges_are_left_out(self):
        pool = FakePool()
        long_id = "sym:" + "é" * 200  # 404 bytes
        nodes = [{"id": "file:a.py", "text": ""}, {"id": long_id, "text": ""}]
        edges = [{"src": "file:a.py", "dst": long_id, "kind": "contains"}]
        assert replace_domain_graph(Mem(pool), nodes, edges, domain="d") == (1, 0)

    @pytest.mark.parametrize("domain", ["", "x" * 33])
    def test_domain_must_fit_the_column(self, domain):
        with pytest.raises(ValueError, match="invalid domain"):
            replace_domain_graph(Mem(FakePool()), [], [], domain=domain)


class TestMatchNeighborhoodBulk:
    def test_one_statement_per_hop_for_all_anchors(self):
        pool = FakePool()
        match_neighborhood_bulk(Mem(pool), anchors=["a", "b", "c"], domain="d", hops=2)
        assert len(pool.sql()) == 2

    def test_sql_is_distinct_undirected_and_fully_bound(self):
        pool = FakePool()
        anchor = "sym:x.py::f'; DROP TABLE t --"
        match_neighborhood_bulk(Mem(pool), anchors=[anchor], domain="dom", hops=2, graph_name="g")
        (_, one_hop, binds), (_, two_hop, _) = pool.statements
        assert one_hop == (
            "SELECT DISTINCT neighbor FROM GRAPH_TABLE (g MATCH (v) -[]- (w) "
            "WHERE v.domain = :domain AND v.id IN (:a0) COLUMNS (w.id AS neighbor) )"
        )
        assert "MATCH (v) -[]- (n0) -[]- (w)" in two_hop
        assert binds == {"domain": "dom", "a0": anchor}

    def test_shortest_distance_wins_and_anchors_are_excluded(self):
        pool = FakePool(results={
            "(v) -[]- (n0) -[]- (w)": [("x",), ("y",), ("a",)],
            "(v) -[]- (w)": [("x",), ("b",)],
        })
        rows = match_neighborhood_bulk(Mem(pool), anchors=["a", "b"], domain="d", hops=2)
        assert rows == [{"neighbor": "x", "hops": 1}, {"neighbor": "y", "hops": 2}]

    def test_many_anchors_are_chunked_below_the_in_list_limit(self):
        pool = FakePool()
        match_neighborhood_bulk(Mem(pool), anchors=[f"n{i}" for i in range(1200)], domain="d", hops=1)
        assert [len(b) - 1 for _, _, b in pool.statements] == [500, 500, 200]

    def test_no_anchors_no_query(self):
        pool = FakePool()
        assert match_neighborhood_bulk(Mem(pool), anchors=[], domain="d") == []
        assert pool.statements == []

    def test_hops_must_be_positive(self):
        with pytest.raises(ValueError):
            match_neighborhood_bulk(Mem(FakePool()), anchors=["a"], domain="d", hops=0)


def test_create_property_graph_replaces_atomically_without_a_drop():
    pool = FakePool()
    create_property_graph(Mem(pool), graph_name="g", table_prefix="CKG")
    (statement,) = pool.sql()
    assert statement.startswith("CREATE OR REPLACE PROPERTY GRAPH g VERTEX TABLES ( CKG_NODES")
    assert "DROP" not in statement


def test_load_graph_derives_kinds_and_keeps_weights():
    pool = FakePool(results={
        "_NODES": [("file:a.py", "doc"), ("sym:a.py::f", None)],
        "_EDGES": [("file:a.py", "sym:a.py::f", "contains", 1), ("file:a.py", "file:b.py", "co_edit", 3)],
    })
    graph = load_graph(Mem(pool), domain="d")
    assert graph["nodes"] == {
        "file:a.py": {"id": "file:a.py", "kind": "file", "text": "doc"},
        "sym:a.py::f": {"id": "sym:a.py::f", "kind": "sym", "text": ""},
    }
    assert graph["edges"] == [
        {"src": "file:a.py", "dst": "sym:a.py::f", "kind": "contains"},
        {"src": "file:a.py", "dst": "file:b.py", "kind": "co_edit", "weight": 3},
    ]


class TestDsnHandling:
    @pytest.mark.parametrize("dsn,expected", [
        ("scott/tiger@dbhost:1521/svc", "***@dbhost:1521/svc"),
        ("admin@dbhost/svc", "***@dbhost/svc"),
        ("user:p@ss@host/svc", "***@host/svc"),
        ("localhost:1521/FREEPDB1", "localhost:1521/FREEPDB1"),
    ])
    def test_redact_strips_all_userinfo(self, dsn, expected):
        assert redact_dsn(dsn) == expected

    @pytest.mark.parametrize("dsn,host", [
        ("localhost:1521/FREEPDB1", "localhost"),
        ("scott/tiger@DBHost:1521/svc", "dbhost"),
        ("tcps://[::1]:1521/x", "::1"),
        ("127.0.0.1/x", "127.0.0.1"),
        ("prod-localhost.attacker.example:1521/X", "prod-localhost.attacker.example"),
        ("myhost:1521/X?localhost=1", "myhost"),
        ("(DESCRIPTION=(ADDRESS=(HOST=localhost)))", ""),
    ])
    def test_host_is_parsed_exactly(self, dsn, host):
        assert dsn_host(dsn) == host

    @pytest.mark.parametrize("dsn,warns", [
        ("localhost:1521/FREEPDB1", False),
        ("127.0.0.1:1521/FREEPDB1", False),
        ("prod-localhost.attacker.example:1521/X", True),
        ("myhost:1521/X?localhost=1", True),
    ])
    def test_default_password_warning(self, monkeypatch, capsys, dsn, warns):
        monkeypatch.setenv("CKG_ORACLE_DSN", dsn)
        monkeypatch.delenv("CKG_ORACLE_PASSWORD", raising=False)
        oracle_config()
        assert ("default CKG_ORACLE_PASSWORD" in capsys.readouterr().err) is warns

    def test_connect_timeout_setting(self, monkeypatch):
        monkeypatch.setenv("CKG_ORACLE_DSN", "localhost/x")
        assert oracle_config()["connect_timeout"] == 3.0
        monkeypatch.setenv("CKG_ORACLE_CONNECT_TIMEOUT", "0.5")
        assert oracle_config()["connect_timeout"] == 0.5
        monkeypatch.setenv("CKG_ORACLE_CONNECT_TIMEOUT", "garbage")
        assert oracle_config()["connect_timeout"] == 3.0


class TestConnect:
    @pytest.fixture(autouse=True)
    def _fresh(self, monkeypatch):
        monkeypatch.setattr(connection, "_failed", {})
        monkeypatch.setattr(connection, "_last_error", None)

    def test_not_configured(self, monkeypatch):
        monkeypatch.delenv("CKG_ORACLE_DSN", raising=False)
        assert connect_pgq() == (None, None)

    def test_unreachable_database_fails_fast_and_is_remembered(self, monkeypatch):
        monkeypatch.setenv("CKG_ORACLE_DSN", "192.0.2.1:1521/FREEPDB1")  # TEST-NET-1: no route
        monkeypatch.setenv("CKG_ORACLE_PASSWORD", "x")
        monkeypatch.setenv("CKG_ORACLE_CONNECT_TIMEOUT", "0.5")
        start = time.monotonic()
        assert connect_pgq() == (None, None)
        first = time.monotonic() - start
        assert first < 4.0
        assert last_connect_error()
        start = time.monotonic()
        assert connect_pgq() == (None, None)
        assert time.monotonic() - start < 0.2
        assert last_connect_error()

    def test_success_requires_a_statement_to_run(self, monkeypatch):
        monkeypatch.setenv("CKG_ORACLE_DSN", "localhost/x")
        pool = FakePool()
        pool.close = lambda force=False: None
        monkeypatch.setattr(connection, "create_pool", lambda cfg: pool)
        mem, cfg = connect_pgq()
        assert mem._pool is pool and cfg["dsn"] == "localhost/x"
        assert pool.sql() == ["SELECT 1 FROM DUAL"]
        assert last_connect_error() is None

    def test_failure_on_first_use_closes_the_pool(self, monkeypatch):
        monkeypatch.setenv("CKG_ORACLE_DSN", "scott/tiger@localhost/x")
        closed = []
        pool = FakePool(fail_on="SELECT 1")
        pool.close = lambda force=False: closed.append(force)
        monkeypatch.setattr(connection, "create_pool", lambda cfg: pool)
        assert connect_pgq() == (None, None)
        assert closed == [True]
        assert last_connect_error() == "ORA-00001: boom"
