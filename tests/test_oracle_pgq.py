"""Tests for ckg.storage.oracle_pgq identifier validation (no live Oracle)."""

import pytest

from ckg.storage.oracle_pgq import (
    create_property_graph,
    load_graph,
    match_edges,
    match_neighborhood,
    require_identifier,
    upsert_graph_edges,
    upsert_graph_nodes,
)

VALID_IDENTIFIERS = ["ckg_code_graph", "MEMORY_GRAPH", "_x", "A1$#"]
INVALID_IDENTIFIERS = ["", "1abc", "x y", 'x"; DROP TABLE y --', "x;DROP", "x\n"]


@pytest.mark.parametrize("name", VALID_IDENTIFIERS)
def test_require_identifier_accepts_valid(name):
    assert require_identifier(name, kind="graph name") == name


@pytest.mark.parametrize("name", INVALID_IDENTIFIERS + ["a.b", "a.b.c", "a..b"])
def test_require_identifier_rejects_invalid(name):
    with pytest.raises(ValueError):
        require_identifier(name, kind="table prefix")


@pytest.mark.parametrize("name", VALID_IDENTIFIERS + ["MYSCHEMA.ckg_code_graph"])
def test_require_identifier_allows_qualified_graph_name(name):
    assert require_identifier(name, kind="graph name", allow_qualified=True) == name


@pytest.mark.parametrize("name", ["a.b.c", "a..b", ".b", "a.", "a.b;DROP"])
def test_require_identifier_rejects_bad_qualification(name):
    with pytest.raises(ValueError):
        require_identifier(name, kind="graph name", allow_qualified=True)


def test_require_identifier_rejects_overlong():
    with pytest.raises(ValueError):
        require_identifier("a" * 129, kind="graph name")


def test_sql_helpers_reject_injected_names_before_touching_db():
    # No pool is passed: an injected identifier must fail as ValueError,
    # not RuntimeError, so validation happens before any SQL is built.
    with pytest.raises(ValueError):
        create_property_graph(None, graph_name="x; DROP TABLE t")
    with pytest.raises(ValueError):
        match_neighborhood(None, anchor="a", domain="d", graph_name="x; DROP TABLE t")
    with pytest.raises(ValueError):
        match_edges(None, anchor="a", domain="d", graph_name="x; DROP TABLE t")
    with pytest.raises(ValueError):
        upsert_graph_nodes(None, [], domain="d", table_prefix="x; DROP TABLE t")
    with pytest.raises(ValueError):
        upsert_graph_edges(None, [], domain="d", table_prefix="x; DROP TABLE t")
    with pytest.raises(ValueError):
        load_graph(None, domain="d", table_prefix="x; DROP TABLE t")
