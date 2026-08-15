"""Integration tests for CKG CLI commands.

Tests the CLI through its Python API (not subprocess) for determinism.
"""

import json
import pytest
import tempfile
from pathlib import Path

from ckg.cli.main import cmd_build, cmd_query, cmd_inject


class TestCliBuild:
    def test_build_creates_graph(self, sample_pkg_path, tmp_path):
        output = tmp_path / "code_graph.json"
        ns = argparse.Namespace(
            path=str(sample_pkg_path),
            pkg_root="sample_pkg",
            output=str(output),
        )
        rc = cmd_build(ns)
        assert rc == 0
        assert output.exists()
        graph = json.loads(output.read_text())
        assert "nodes" in graph
        assert "edges" in graph
        assert "meta" in graph
        assert graph["meta"]["n_files"] >= 3

    def test_build_nonexistent_dir(self, tmp_path):
        ns = argparse.Namespace(
            path="/tmp/nonexistent_ckg_test_dir",
            pkg_root="test",
            output=str(tmp_path / "graph.json"),
        )
        rc = cmd_build(ns)
        assert rc == 1  # Should fail

    def test_build_auto_pkg_root(self, sample_pkg_path, tmp_path):
        output = tmp_path / "graph.json"
        ns = argparse.Namespace(
            path=str(sample_pkg_path),
            pkg_root=None,
            output=str(output),
        )
        rc = cmd_build(ns)
        assert rc == 0
        graph = json.loads(output.read_text())
        assert graph["meta"]["pkg_root"] == "sample_pkg"


class TestCliQuery:
    def test_query_against_cache(self, sample_pkg_path, tmp_path):
        # Build first
        output = tmp_path / "graph.json"
        import argparse as _argparse
        build_ns = _argparse.Namespace(path=str(sample_pkg_path), pkg_root="sample_pkg",
                                        output=str(output))
        cmd_build(build_ns)

        # Query
        query_ns = _argparse.Namespace(
            query="validate email processing",
            graph=str(output),
            k_anchor=3,
            hops=2,
            top_k=5,
        )
        rc = cmd_query(query_ns)
        assert rc == 0

    def test_query_missing_graph(self, tmp_path):
        import argparse as _argparse
        ns = _argparse.Namespace(
            query="anything",
            graph=str(tmp_path / "nonexistent.json"),
            k_anchor=3,
            hops=2,
            top_k=5,
        )
        rc = cmd_query(ns)
        assert rc == 1  # Should fail


class TestCliInject:
    def test_inject_with_root(self, sample_pkg_path, tmp_path):
        # Build first so the cache exists
        cache_dir = tmp_path / ".ckg"
        output = cache_dir / "code_graph.json"
        import argparse as _argparse
        build_ns = _argparse.Namespace(path=str(sample_pkg_path), pkg_root="sample_pkg",
                                        output=str(output))
        cmd_build(build_ns)

        # Inject — but we need to mock the root detection since inject uses the
        # parsed graph from the fixture (which expects sample_pkg as root).
        # For now, test that the function runs without crashing when project_root
        # is given explicitly.
        from ckg.claude.plugin import inject_context
        result = inject_context("test order validation", project_root=sample_pkg_path,
                                cache_dir=cache_dir)
        assert "Structure Map" in result
        assert "sample_pkg" in result or "sample_project" in result

    def test_inject_nonexistent_project(self):
        from ckg.claude.plugin import inject_context
        result = inject_context("anything", project_root=Path("/nonexistent"))
        assert result == ""


import argparse as _argparse_module
# This is needed for argparse.Namespace to be importable
# (it's used in the test files via the import at top level)
try:
    import argparse
except ImportError:
    pass
