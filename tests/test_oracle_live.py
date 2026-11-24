"""Integration tests against a live Oracle AI Database (26ai Free).

Skipped unless ``CKG_ORACLE_LIVE=1``. Connection settings come from the usual
``CKG_ORACLE_*`` variables (default DSN ``localhost:1521/FREEPDB1``).

The database may be shared, so the tests keep to themselves: they create
their own ``CKG_TEST_*`` tables and ``ckg_test_graph`` property graph, write
only to domains starting with ``ckg_test_``, and drop all of it afterwards.
"""

import argparse
import os

import oracledb
import pytest

from ckg.retrieval.hybrid import hybrid_retrieve
from ckg.storage.connection import close_pgq, connect_pgq, oracle_summary
from ckg.storage.oracle_pgq import (
    create_property_graph,
    ensure_schema,
    load_graph,
    match_neighborhood_bulk,
    replace_domain_graph,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("CKG_ORACLE_LIVE"),
    reason="CKG_ORACLE_LIVE not set — requires a live Oracle AI Database",
)

PREFIX = "CKG_TEST"
GRAPH = "ckg_test_graph"
DOMAIN = "ckg_test_live"
OTHER_DOMAIN = "ckg_test_other"

NODES = [
    {"id": "file:a.py", "kind": "file", "text": "order processing"},
    {"id": "file:b.py", "kind": "file", "text": "email validation"},
    {"id": "file:c.py", "kind": "file", "text": "database pool"},
    {"id": "file:d.py", "kind": "file", "text": "logging setup"},
    {"id": "sym:a.py::run", "kind": "sym", "text": "def run(). Unicode — é. " + "x" * 5000},
]
EDGES = [
    {"src": "file:a.py", "dst": "file:b.py", "kind": "import"},
    {"src": "file:c.py", "dst": "file:b.py", "kind": "import"},
    {"src": "file:a.py", "dst": "sym:a.py::run", "kind": "contains"},
    {"src": "file:c.py", "dst": "file:d.py", "kind": "co_edit", "weight": 3},
]


def _drop_everything(mem) -> None:
    with mem._pool.acquire() as conn:
        cur = conn.cursor()
        for statement in (
            f"DROP PROPERTY GRAPH {GRAPH}",
            f"DROP TABLE {PREFIX}_EDGES PURGE",
            f"DROP TABLE {PREFIX}_NODES PURGE",
        ):
            try:
                cur.execute(statement)
            except Exception:
                pass


@pytest.fixture(scope="module")
def mem():
    os.environ.setdefault("CKG_ORACLE_DSN", "localhost:1521/FREEPDB1")
    mem, _cfg = connect_pgq()
    assert mem is not None, "CKG_ORACLE_LIVE is set but the database is unreachable"
    _drop_everything(mem)
    yield mem
    _drop_everything(mem)
    close_pgq(mem)


@pytest.fixture(scope="module")
def stored(mem):
    """Schema created from nothing, the sample graph stored, the graph defined."""
    ensure_schema(mem, table_prefix=PREFIX)
    counts = replace_domain_graph(mem, NODES, EDGES, domain=DOMAIN, table_prefix=PREFIX)
    create_property_graph(mem, graph_name=GRAPH, table_prefix=PREFIX)
    return counts


def _tables(mem) -> set[str]:
    with mem._pool.acquire() as conn:
        cur = conn.cursor()
        cur.execute("select table_name from user_tables where table_name like :p", p=f"{PREFIX}%")
        return {row[0] for row in cur}


class TestSchema:
    def test_tables_are_created_from_scratch(self, mem, stored):
        assert _tables(mem) == {f"{PREFIX}_NODES", f"{PREFIX}_EDGES"}

    def test_ensure_schema_and_graph_creation_are_idempotent(self, mem, stored):
        ensure_schema(mem, table_prefix=PREFIX)
        create_property_graph(mem, graph_name=GRAPH, table_prefix=PREFIX)
        assert load_graph(mem, domain=DOMAIN, table_prefix=PREFIX)["nodes"].keys() == {
            n["id"] for n in NODES
        }


class TestStoreAndLoad:
    def test_round_trip(self, mem, stored):
        assert stored == (5, 4)
        graph = load_graph(mem, domain=DOMAIN, table_prefix=PREFIX)
        assert graph["nodes"]["sym:a.py::run"] == NODES[4]  # long unicode text intact
        assert graph["nodes"]["file:a.py"]["kind"] == "file"
        assert {"src": "file:c.py", "dst": "file:d.py", "kind": "co_edit", "weight": 3} in graph["edges"]
        assert len(graph["edges"]) == 4

    def test_reload_drops_what_is_gone_and_leaves_other_domains(self, mem, stored):
        replace_domain_graph(mem, NODES[:2], EDGES[:1], domain=OTHER_DOMAIN, table_prefix=PREFIX)
        replace_domain_graph(mem, NODES[:1], [], domain=OTHER_DOMAIN, table_prefix=PREFIX)
        other = load_graph(mem, domain=OTHER_DOMAIN, table_prefix=PREFIX)
        assert list(other["nodes"]) == ["file:a.py"] and other["edges"] == []
        assert len(load_graph(mem, domain=DOMAIN, table_prefix=PREFIX)["nodes"]) == 5

    def test_failed_reload_keeps_the_previous_graph(self, mem, stored):
        # An empty kind is NULL in Oracle: the edge insert fails after the
        # domain's rows were already deleted and the nodes re-inserted.
        bad_edges = [{"src": "file:a.py", "dst": "file:b.py", "kind": ""}]
        with pytest.raises(oracledb.DatabaseError):
            replace_domain_graph(mem, NODES[:2], bad_edges, domain=DOMAIN, table_prefix=PREFIX)
        graph = load_graph(mem, domain=DOMAIN, table_prefix=PREFIX)
        assert (len(graph["nodes"]), len(graph["edges"])) == (5, 4)


class TestMatch:
    def test_one_hop_follows_edges_in_both_directions(self, mem, stored):
        rows = match_neighborhood_bulk(mem, anchors=["file:b.py"], domain=DOMAIN, hops=1,
                                       graph_name=GRAPH)
        # b.py has only incoming edges
        assert rows == [{"neighbor": "file:a.py", "hops": 1}, {"neighbor": "file:c.py", "hops": 1}]

    def test_two_hops_report_shortest_distance_once(self, mem, stored):
        rows = match_neighborhood_bulk(mem, anchors=["file:b.py"], domain=DOMAIN, hops=2,
                                       graph_name=GRAPH)
        assert rows == [
            {"neighbor": "file:a.py", "hops": 1},
            {"neighbor": "file:c.py", "hops": 1},
            {"neighbor": "file:d.py", "hops": 2},
            {"neighbor": "sym:a.py::run", "hops": 2},
        ]

    def test_several_anchors_in_one_call(self, mem, stored):
        rows = match_neighborhood_bulk(mem, anchors=["file:a.py", "file:d.py"], domain=DOMAIN,
                                       hops=1, graph_name=GRAPH)
        assert [r["neighbor"] for r in rows] == ["file:b.py", "file:c.py", "sym:a.py::run"]

    def test_scoped_to_the_domain(self, mem, stored):
        assert match_neighborhood_bulk(mem, anchors=["file:b.py"], domain="ckg_test_absent",
                                       graph_name=GRAPH) == []

    def test_anchor_text_is_bound(self, mem, stored):
        assert match_neighborhood_bulk(mem, anchors=["x' OR '1'='1"], domain=DOMAIN,
                                       graph_name=GRAPH) == []


def test_hybrid_retrieval_uses_pgq(mem, stored):
    graph = {"nodes": {n["id"]: n for n in NODES}, "edges": EDGES}
    result = hybrid_retrieve("email validation", graph, mem=mem, domain=DOMAIN, graph_name=GRAPH)
    assert result["method"] == "pgq"
    assert result["anchors"] == ["file:b.py"]
    assert {r["node_id"] for r in result["results"]} == {n["id"] for n in NODES}


class TestCli:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch, mem, stored):
        monkeypatch.setenv("CKG_ORACLE_TABLE_PREFIX", PREFIX)
        monkeypatch.setenv("CKG_ORACLE_GRAPH", GRAPH)
        monkeypatch.setenv("CKG_ORACLE_DOMAIN", "ckg_test_cli")

    def test_load_then_query_from_the_database_alone(self, sample_pkg_path, tmp_path, capsys):
        from ckg.cli.main import cmd_load, cmd_query

        ns = argparse.Namespace(path=str(sample_pkg_path), pkg_root="sample_pkg",
                                graph=str(tmp_path / "g.json"), domain=None)
        assert cmd_load(ns) == 0
        assert "21 nodes, 31 edges" in capsys.readouterr().out

        # No local graph file: the stored copy is used, and matched with PGQ.
        query = argparse.Namespace(query="validate email", graph=str(tmp_path / "absent.json"),
                                   k_anchor=None, hops=None, top_k=None, no_pgq=False, json=False)
        assert cmd_query(query) == 0
        out = capsys.readouterr().out
        assert "Retrieval (pgq)" in out and "validate_email" in out

    def test_summary_reports_the_stored_graph(self, monkeypatch):
        monkeypatch.setenv("CKG_ORACLE_DOMAIN", DOMAIN)
        summary = oracle_summary()
        assert summary["connected"] is True
        assert (summary["nodes"], summary["edges"]) == (5, 4)
        assert summary["property_graph"] is True
        assert "Oracle" in summary["version"]
