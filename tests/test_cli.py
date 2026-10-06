"""Integration tests for CKG CLI commands.

Tests the CLI through its Python API (not subprocess) for determinism.
"""

import argparse
import json
from pathlib import Path

from ckg.cli.main import cmd_build, cmd_query


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
        build_ns = argparse.Namespace(path=str(sample_pkg_path), pkg_root="sample_pkg",
                                      output=str(output))
        cmd_build(build_ns)

        # Query
        query_ns = argparse.Namespace(
            query="validate email processing",
            graph=str(output),
            k_anchor=3,
            hops=2,
            top_k=5,
        )
        rc = cmd_query(query_ns)
        assert rc == 0

    def test_query_missing_graph(self, tmp_path):
        ns = argparse.Namespace(
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
        
        build_ns = argparse.Namespace(path=str(sample_pkg_path), pkg_root="sample_pkg",
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



class TestCliLoadErrors:
    def test_load_reports_unreachable_oracle_instead_of_crashing(
        self, sample_pkg_path, tmp_path, monkeypatch, capsys
    ):
        from ckg.cli.main import cmd_load

        monkeypatch.setenv("CKG_ORACLE_DSN", "db.invalid:1521/FREEPDB1")
        monkeypatch.setenv("CKG_ORACLE_PASSWORD", "x")
        monkeypatch.setattr(
            "ckg.storage.connection.create_pool",
            lambda cfg: (_ for _ in ()).throw(RuntimeError("unreachable")),
        )
        ns = argparse.Namespace(path=str(sample_pkg_path), pkg_root=None,
                                graph=str(tmp_path / "g.json"), domain=None)
        assert cmd_load(ns) == 1
        assert "cannot connect to Oracle at db.invalid" in capsys.readouterr().err


class TestCliQueryInputs:
    def test_corrupt_graph_is_an_error_not_a_traceback(self, tmp_path, capsys):
        bad = tmp_path / "graph.json"
        bad.write_text("{not json")
        ns = argparse.Namespace(query="x", graph=str(bad), k_anchor=None,
                                hops=None, top_k=None, no_pgq=True)
        assert cmd_query(ns) == 1
        assert "cannot read graph" in capsys.readouterr().err

    def test_zero_top_k_is_honoured(self, sample_pkg_path, tmp_path, capsys):
        output = tmp_path / "graph.json"
        cmd_build(argparse.Namespace(path=str(sample_pkg_path), pkg_root="sample_pkg",
                                     output=str(output)))
        capsys.readouterr()
        ns = argparse.Namespace(query="validate email", graph=str(output), k_anchor=3,
                                hops=2, top_k=0, no_pgq=True)
        assert cmd_query(ns) == 0
        lines = [l for l in capsys.readouterr().out.splitlines() if l.startswith("  0.") or l.startswith("  1.")]
        assert lines == []


class TestCliBuildRobustness:
    def test_build_survives_syntax_error_file(self, tmp_path):
        (tmp_path / "ok.py").write_text("def f(): pass\n")
        (tmp_path / "bad.py").write_text("def (:\n")
        out = tmp_path / "out.json"
        rc = cmd_build(argparse.Namespace(path=str(tmp_path), pkg_root="p", output=str(out)))
        assert rc == 0
        assert "sym:ok.py::f" in json.loads(out.read_text())["nodes"]


class TestInjectContext:
    def test_query_matching_nothing_injects_nothing(self, sample_pkg_path, tmp_path):
        from ckg.claude.plugin import inject_context

        assert inject_context("zzqx plugh", project_root=sample_pkg_path,
                              cache_dir=tmp_path / ".ckg") == ""

    def test_corrupt_cache_is_rebuilt_with_enriched_text(self, sample_pkg_path, tmp_path):
        from ckg.claude.plugin import build_context_map

        cache = tmp_path / ".ckg"
        cache.mkdir()
        (cache / "code_graph.json").write_text("[]")  # valid JSON, not a graph
        out = build_context_map("process order validation", sample_pkg_path, cache_dir=cache)
        assert out and "core.py" in out
        rebuilt = json.loads((cache / "code_graph.json").read_text())
        # docstring evidence only exists in enriched node text
        assert "sample package" in rebuilt["nodes"]["file:core.py"]["text"].lower()
