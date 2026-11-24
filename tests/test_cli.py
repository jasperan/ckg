"""Tests for the CLI commands and the context-injection entry point."""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

import ckg
from ckg.claude.plugin import build_context_map, detect_project, inject_context
from ckg.cli.main import cmd_build, cmd_inject, cmd_load, cmd_oracle_status, cmd_query
from ckg.graph.cache import cache_path, is_stale, read_graph
from tests.conftest import commit_all, git, write_files

CKG = [sys.executable, "-m", "ckg.cli.main"]


def _build_ns(path, output=None, pkg_root=None) -> argparse.Namespace:
    return argparse.Namespace(
        path=str(path), pkg_root=pkg_root, output=str(output) if output else None,
        quiet=True, lock=None,
    )


def _query_ns(graph, query="validate email", **over) -> argparse.Namespace:
    values = dict(query=query, graph=str(graph), k_anchor=None, hops=None, top_k=None,
                  no_pgq=True, json=False)
    values.update(over)
    return argparse.Namespace(**values)


@pytest.fixture
def project(tmp_path) -> Path:
    """A small project of its own, so tests may edit it and build caches in it."""
    root = tmp_path / "shop"
    write_files(root, {
        "pyproject.toml": "",
        "billing.py": '"""Billing."""\nfrom tax import vat\n\ndef charge_card(amount):\n    return vat(amount)\n',
        "tax.py": '"""Tax rules."""\ndef vat(amount):\n    return amount * 1.2\n',
        "other.py": "X = 1\n",
    })
    return root


@pytest.fixture
def graph_file(sample_pkg_path, tmp_path) -> Path:
    output = tmp_path / "graph.json"
    assert cmd_build(_build_ns(sample_pkg_path, output, "sample_pkg")) == 0
    return output


def _wait_for(predicate, timeout=20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class TestBuild:
    def test_writes_a_loadable_graph(self, graph_file):
        graph = read_graph(graph_file)
        assert graph["meta"]["n_files"] == 5 and graph["meta"]["pkg_root"] == "sample_pkg"
        assert "lex" in graph and graph["meta"]["fingerprint"]

    def test_pkg_root_defaults_to_directory_name(self, sample_pkg_path, tmp_path):
        output = tmp_path / "g.json"
        assert cmd_build(_build_ns(sample_pkg_path, output)) == 0
        assert read_graph(output)["meta"]["pkg_root"] == "sample_pkg"

    def test_nonexistent_dir_is_an_error(self, tmp_path, capsys):
        assert cmd_build(_build_ns(tmp_path / "missing", tmp_path / "g.json")) == 1
        assert "is not a directory" in capsys.readouterr().err

    def test_unwritable_output_is_an_error_not_a_traceback(self, project, tmp_path, capsys):
        blocker = tmp_path / "file"
        blocker.write_text("")
        assert cmd_build(_build_ns(project, blocker / "sub" / "g.json")) == 1
        assert "cannot write graph" in capsys.readouterr().err

    def test_default_cache_is_ignored_by_git_and_leaves_no_temp_files(self, project):
        git(project, "init", "-q")
        commit_all(project, "initial")
        assert cmd_build(_build_ns(project)) == 0
        assert sorted(p.name for p in (project / ".ckg").iterdir()) == [".gitignore", "code_graph.json"]
        assert git(project, "status", "--porcelain") == ""

    def test_survives_syntax_error_file(self, tmp_path):
        write_files(tmp_path, {"ok.py": "def f(): pass\n", "bad.py": "def (:\n"})
        out = tmp_path / "out.json"
        assert cmd_build(_build_ns(tmp_path, out, "p")) == 0
        assert "sym:ok.py::f" in read_graph(out)["nodes"]

    def test_lock_file_is_removed_even_when_the_build_fails(self, tmp_path):
        lock = tmp_path / "build.lock"
        lock.write_text("")
        ns = _build_ns(tmp_path / "missing", tmp_path / "g.json")
        ns.lock = str(lock)
        assert cmd_build(ns) == 1
        assert not lock.exists()


class TestQuery:
    def test_ranks_the_named_symbol_first(self, graph_file, capsys):
        assert cmd_query(_query_ns(graph_file)) == 0
        out = capsys.readouterr().out
        assert "Anchors: validate_email" in out
        first = next(line for line in out.splitlines() if line.startswith("  0."))
        assert "utils/helpers.py" in first  # the file defining it, or the symbol itself

    def test_json_output(self, graph_file, capsys):
        assert cmd_query(_query_ns(graph_file, json=True)) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["method"] == "memory"
        assert data["anchors"][0] == "sym:utils/helpers.py::validate_email"

    def test_no_match_prints_no_results(self, graph_file, capsys):
        assert cmd_query(_query_ns(graph_file, query="zzqx plugh")) == 0
        out = capsys.readouterr().out
        assert "no lexical match" in out and "Anchors: (none)" in out
        assert not [line for line in out.splitlines() if line.startswith("  0.")]

    def test_zero_top_k_is_honoured(self, graph_file, capsys):
        assert cmd_query(_query_ns(graph_file, top_k=0)) == 0
        assert not [line for line in capsys.readouterr().out.splitlines() if line.startswith("  0.")]

    def test_missing_graph(self, tmp_path, capsys):
        assert cmd_query(_query_ns(tmp_path / "nope.json")) == 1
        assert "Run 'ckg build' first" in capsys.readouterr().err

    @pytest.mark.parametrize("content", [
        "{not json",
        "[]",
        '{"nodes": {"file:a.py": "str"}, "edges": []}',
        '{"nodes": {"file:a.py": {"text": "a"}}, "edges": [{"src": "file:a.py"}]}',
    ])
    def test_unusable_graph_is_an_error_not_a_traceback(self, tmp_path, capsys, content):
        bad = tmp_path / "graph.json"
        bad.write_text(content)
        assert cmd_query(_query_ns(bad)) == 1
        assert "cannot read graph" in capsys.readouterr().err

    def test_negative_numbers_are_rejected_by_the_parser(self, graph_file):
        proc = subprocess.run(
            [*CKG, "query", "x", "--graph", str(graph_file), "--top-k", "-3"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 2 and "must be >= 0" in proc.stderr


class TestCommandLine:
    def test_version(self):
        proc = subprocess.run([*CKG, "--version"], capture_output=True, text=True)
        assert proc.returncode == 0 and proc.stdout.strip() == f"ckg {ckg.__version__}"

    def test_version_matches_the_installed_distribution(self):
        from importlib.metadata import version

        assert version("ckg") == ckg.__version__


class TestDetectProject:
    def test_git_file_of_a_worktree_marks_the_root(self, tmp_path):
        root = tmp_path / "wt"
        write_files(root, {".git": "gitdir: /elsewhere\n", "pkg/mod.py": ""})
        assert detect_project(root / "pkg") == root

    def test_home_directory_is_never_a_project(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        write_files(home, {".git/HEAD": "", "notes/todo.py": "", "work/app/pyproject.toml": ""})
        monkeypatch.setattr(Path, "home", lambda: home)
        assert detect_project(home / "notes") is None
        assert detect_project(home / "work" / "app") == home / "work" / "app"


class TestInjectContext:
    def test_map_names_anchor_and_neighbour(self, project):
        out = inject_context("charge the card", project_root=project)
        assert "## CKG Structure Map for `shop`" in out
        anchors, reach = out.split("### Dependency Reach")
        assert "- `billing.py` — Billing." in anchors
        assert "- `tax.py` — Tax rules.\n  *(imported by billing.py; called by billing.py)*" in reach

    def test_nonexistent_project(self):
        assert inject_context("anything", project_root=Path("/nonexistent")) == ""

    def test_query_matching_nothing_injects_nothing(self, project):
        assert inject_context("zzqx plugh", project_root=project) == ""

    def test_symbol_only_match_is_anchored_on_its_file(self, project):
        out = build_context_map("vat", project)
        assert "`tax.py`" in out.split("### Dependency Reach")[0]

    @pytest.mark.parametrize("content", ["[]", "{broken", '{"nodes": {"a": 1}}'])
    def test_unusable_cache_is_rebuilt(self, project, content):
        write_files(project, {".ckg/code_graph.json": content})
        assert "`billing.py`" in build_context_map("charge card", project)
        assert "file:billing.py" in read_graph(cache_path(project))["nodes"]

    def test_cache_from_an_older_schema_is_rebuilt(self, project):
        old = {"nodes": {"file:billing.py": {"id": "file:billing.py", "kind": "file",
                                             "text": "Module billing.py in shop."}},
               "edges": [], "meta": {"pkg_root": "shop"}}
        write_files(project, {".ckg/code_graph.json": json.dumps(old)})
        assert "`tax.py`" in build_context_map("charge card", project)

    def test_custom_cache_dir(self, project, tmp_path):
        cache = tmp_path / "elsewhere"
        assert build_context_map("charge card", project, cache_dir=cache)
        assert (cache / "code_graph.json").exists() and not (project / ".ckg").exists()

    def test_invalid_refresh_mode(self, project):
        with pytest.raises(ValueError):
            build_context_map("x", project, refresh="sometimes")


class TestCacheFreshness:
    def _add_module(self, project):
        write_files(project, {"refunds.py": "def issue_refund():\n    '''Give money back.'''\n"})

    def test_edit_makes_the_cache_stale(self, project):
        build_context_map("charge card", project)
        graph = read_graph(cache_path(project))
        assert not is_stale(graph, project)
        self._add_module(project)
        assert is_stale(graph, project)

    def test_commit_makes_the_cache_stale(self, project):
        git(project, "init", "-q")
        commit_all(project, "initial")
        build_context_map("charge card", project)
        graph = read_graph(cache_path(project))
        assert not is_stale(graph, project)
        git(project, "commit", "-q", "--allow-empty", "-m", "history moved")
        assert is_stale(graph, project)

    def test_sync_refresh_sees_new_code_immediately(self, project):
        build_context_map("charge card", project)
        self._add_module(project)
        assert "`refunds.py`" in build_context_map("issue refund", project, refresh="sync")

    def test_auto_refresh_serves_the_old_graph_and_rebuilds_behind_it(self, project):
        build_context_map("charge card", project)
        self._add_module(project)
        assert build_context_map("issue refund", project) is None  # old graph: no such code
        assert "`billing.py`" in build_context_map("charge card", project)
        assert _wait_for(lambda: "file:refunds.py" in read_graph(cache_path(project))["nodes"])
        assert _wait_for(lambda: not (project / ".ckg" / "build.lock").exists())
        assert "`refunds.py`" in build_context_map("issue refund", project)

    def test_background_refresh_never_builds_in_process(self, project):
        assert build_context_map("charge card", project, refresh="background") is None
        assert _wait_for(lambda: cache_path(project).exists())
        assert "`billing.py`" in build_context_map("charge card", project, refresh="background")

    def test_only_one_background_build_at_a_time(self, project):
        from ckg.graph.cache import spawn_background_build

        write_files(project, {".ckg/build.lock": ""})
        assert spawn_background_build(project, pkg_root="shop") is False


class TestInjectCommand:
    def test_prints_the_map(self, project, capsys):
        ns = argparse.Namespace(query="charge card", root=str(project), no_pgq=True, refresh="auto")
        assert cmd_inject(ns) == 0
        assert "`billing.py`" in capsys.readouterr().out

    def test_no_map_is_exit_1_with_stdout_empty(self, project, capsys):
        ns = argparse.Namespace(query="zzqx", root=str(project), no_pgq=True, refresh="auto")
        assert cmd_inject(ns) == 1
        captured = capsys.readouterr()
        assert captured.out == "" and "no context map" in captured.err

    def test_dash_led_query_after_double_dash(self, project):
        proc = subprocess.run(
            [*CKG, "inject", "--root", str(project), "--no-pgq", "--", "--charge-card"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0 and "`billing.py`" in proc.stdout


class TestOracleUnavailable:
    """Oracle configured but not reachable; no database needed."""

    @pytest.fixture(autouse=True)
    def _unreachable(self, monkeypatch):
        import ckg.storage.connection as connection

        monkeypatch.setenv("CKG_ORACLE_DSN", "db.invalid:1521/FREEPDB1")
        monkeypatch.setenv("CKG_ORACLE_PASSWORD", "x")
        monkeypatch.setattr(connection, "_failed", {})
        monkeypatch.setattr(
            connection, "create_pool",
            lambda cfg: (_ for _ in ()).throw(RuntimeError("DPY-6005: cannot connect\nmore")),
        )

    def test_load_reports_the_reason(self, sample_pkg_path, tmp_path, capsys):
        ns = argparse.Namespace(path=str(sample_pkg_path), pkg_root=None,
                                graph=str(tmp_path / "g.json"), domain=None)
        assert cmd_load(ns) == 1
        err = capsys.readouterr().err
        assert "cannot connect to Oracle at db.invalid:1521/FREEPDB1 (DPY-6005: cannot connect)" in err

    def test_query_falls_back_to_memory_and_says_so(self, graph_file, capsys):
        assert cmd_query(_query_ns(graph_file, no_pgq=False)) == 0
        captured = capsys.readouterr()
        assert "Retrieval (memory)" in captured.out
        assert "Oracle configured but unreachable (DPY-6005: cannot connect)" in captured.err

    def test_inject_still_prints_the_map(self, project, capsys):
        ns = argparse.Namespace(query="charge card", root=str(project), no_pgq=False, refresh="auto")
        assert cmd_inject(ns) == 0
        assert "`billing.py`" in capsys.readouterr().out

    def test_status_reports_memory_mode(self, capsys):
        assert cmd_oracle_status(argparse.Namespace()) == 1
        out = capsys.readouterr().out
        assert "connection: FAILED" in out and "retrieval mode: memory" in out


def test_status_without_oracle_reports_memory_mode(monkeypatch, capsys):
    monkeypatch.delenv("CKG_ORACLE_DSN", raising=False)
    assert cmd_oracle_status(argparse.Namespace()) == 0
    assert "retrieval mode: memory" in capsys.readouterr().out
