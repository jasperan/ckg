"""Tests for ckg.graph.parser — AST-based code graph parsing."""

import json

import pytest

from ckg.graph.parser import _detect_co_edits, _iter_py_files, parse_tree
from tests.conftest import commit_all, git, write_files


def _edges(graph: dict, kind: str) -> set[tuple[str, str]]:
    return {(e["src"], e["dst"]) for e in graph["edges"] if e["kind"] == kind}


def _calls(tmp_path, files: dict[str, str]) -> set[tuple[str, str]]:
    write_files(tmp_path, files)
    return _edges(parse_tree(tmp_path, pkg_root="proj"), "call")


class TestParseTree:
    """End-to-end parsing of the sample project fixture."""

    def test_files_and_symbols_become_nodes(self, sample_graph):
        nodes = sample_graph["nodes"]
        assert {"file:core.py", "file:utils/db.py", "file:utils/helpers.py"} <= set(nodes)
        assert {
            "sym:core.py::process_order",
            "sym:core.py::OrderValidator",
            "sym:core.py::OrderValidator.validate",
            "sym:utils/db.py::DatabaseConnection.open",
        } <= set(nodes)

    def test_import_edges(self, sample_graph):
        assert _edges(sample_graph, "import") == {
            ("file:core.py", "file:utils/db.py"),
            ("file:core.py", "file:utils/helpers.py"),
            ("file:utils/db.py", "file:utils/helpers.py"),
        }

    def test_every_symbol_is_contained_by_its_file(self, sample_graph):
        contains = _edges(sample_graph, "contains")
        syms = [nid for nid in sample_graph["nodes"] if nid.startswith("sym:")]
        assert syms
        for sid in syms:
            assert ("file:" + sid[4:].split("::")[0], sid) in contains

    def test_meta_counts(self, sample_graph):
        meta = sample_graph["meta"]
        assert meta["pkg_root"] == "sample_pkg"
        assert meta["n_files"] == 5
        assert meta["n_nodes"] == len(sample_graph["nodes"])
        assert meta["n_edges"] == len(sample_graph["edges"])

    def test_every_edge_endpoint_is_a_node(self, sample_graph):
        nodes = sample_graph["nodes"]
        for e in sample_graph["edges"]:
            assert e["src"] in nodes and e["dst"] in nodes, e

    def test_parse_nonexistent_tree_raises(self):
        with pytest.raises(FileNotFoundError):
            parse_tree("/nonexistent/path", pkg_root="test")

    def test_output_is_identical_across_runs(self, sample_pkg_path):
        first = parse_tree(sample_pkg_path, pkg_root="sample_pkg")
        second = parse_tree(sample_pkg_path, pkg_root="sample_pkg")
        assert json.dumps(first) == json.dumps(second)


class TestNodeText:
    def test_symbol_text_is_signature_and_first_doc_line(self, sample_graph):
        text = sample_graph["nodes"]["sym:core.py::process_order"]["text"]
        assert text == (
            "def process_order(customer_email: str, items: list[dict]) -> dict. "
            "Process an order from a customer."
        )

    def test_file_text_is_the_module_docstring(self, sample_graph):
        assert sample_graph["nodes"]["file:core.py"]["text"] == "Sample package for CKG testing."

    def test_signature_keeps_every_kind_of_parameter(self, tmp_path):
        write_files(tmp_path, {"m.py": "async def f(a, /, b, *args, c, d=1, **kw): pass\n"})
        text = parse_tree(tmp_path, pkg_root="p")["nodes"]["sym:m.py::f"]["text"]
        assert text == "async def f(a, /, b, *args, c, d=1, **kw)"

    def test_string_literals_never_reach_node_text(self, tmp_path):
        write_files(tmp_path, {
            "config.py": (
                'API_KEY = "sk-live-4f9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c"\n'
                'DB_URL = "postgres://admin:hunter2@db.internal:5432/prod"\n'
                "def load():\n    return API_KEY\n"
            ),
        })
        dumped = json.dumps(parse_tree(tmp_path, pkg_root="p"))
        assert "sk-live" not in dumped and "hunter2" not in dumped


class TestCallResolution:
    def test_calls_resolve_through_imports(self, sample_graph):
        calls = _edges(sample_graph, "call")
        assert ("sym:core.py::process_order", "sym:utils/helpers.py::validate_email") in calls
        assert ("sym:core.py::_save_order", "sym:utils/db.py::query_db") in calls
        assert not any(src == "sym:core.py::validate_email" for src, _ in calls)

    def test_class_does_not_own_its_methods_calls(self, tmp_path):
        calls = _calls(tmp_path, {
            "m.py": "def helper():\n    pass\n\nclass K:\n    def run(self):\n        helper()\n",
        })
        assert calls == {("sym:m.py::K.run", "sym:m.py::helper")}

    def test_aliased_import_is_followed(self, tmp_path):
        calls = _calls(tmp_path, {
            "util.py": "def helper(): pass\n",
            "main.py": "from util import helper as h\ndef go():\n    h()\n",
        })
        assert calls == {("sym:main.py::go", "sym:util.py::helper")}

    def test_module_attribute_call(self, tmp_path):
        calls = _calls(tmp_path, {
            "pkg/__init__.py": "",
            "pkg/tools.py": "def build(): pass\n",
            "a.py": "import pkg.tools\ndef go():\n    pkg.tools.build()\n",
            "b.py": "from pkg import tools as t\ndef go():\n    t.build()\n",
        })
        assert calls == {
            ("sym:a.py::go", "sym:pkg/tools.py::build"),
            ("sym:b.py::go", "sym:pkg/tools.py::build"),
        }

    def test_same_named_methods_elsewhere_are_not_linked(self, tmp_path):
        """A method call on a value of unknown type must not fan out to every
        method of that name in the tree."""
        calls = _calls(tmp_path, {
            "shapes.py": "class Circle:\n    def draw(self): pass\n",
            "render.py": "class Renderer:\n    def draw(self): pass\n",
            "main.py": "def paint(obj, cache):\n    obj.draw()\n    cache.get('k')\n",
        })
        assert calls == set()

    def test_ambiguous_method_among_imports_is_not_guessed(self, tmp_path):
        calls = _calls(tmp_path, {
            "models.py": (
                "class User:\n    def save(self): pass\n"
                "class Order:\n    def save(self): pass\n"
            ),
            "main.py": "from models import User, Order\ndef go(u):\n    u.save()\n",
        })
        assert calls == set()

    def test_unique_method_among_imports_is_linked(self, tmp_path):
        calls = _calls(tmp_path, {
            "models.py": "class User:\n    def deactivate(self): pass\n",
            "main.py": "from models import User\ndef go(u):\n    u.deactivate()\n",
        })
        assert calls == {("sym:main.py::go", "sym:models.py::User.deactivate")}

    def test_receiver_type_from_constructor_and_annotation(self, tmp_path):
        calls = _calls(tmp_path, {
            "models.py": (
                "class User:\n    def save(self): pass\n"
                "class Order:\n    def save(self): pass\n"
            ),
            "main.py": (
                "from models import User, Order\n"
                "def make():\n    u = User()\n    u.save()\n"
                "def ship(order: Order | None):\n    order.save()\n"
            ),
        })
        assert calls == {
            ("sym:main.py::make", "sym:models.py::User"),
            ("sym:main.py::make", "sym:models.py::User.save"),
            ("sym:main.py::ship", "sym:models.py::Order.save"),
        }

    def test_self_calls_follow_inheritance_across_files(self, tmp_path):
        calls = _calls(tmp_path, {
            "base.py": "class Base:\n    def setup(self): pass\n    def run(self): pass\n",
            "child.py": (
                "from base import Base\n"
                "class Child(Base):\n"
                "    def run(self):\n        self.setup()\n        super().run()\n"
            ),
        })
        assert calls == {
            ("sym:child.py::Child.run", "sym:base.py::Base.setup"),
            ("sym:child.py::Child.run", "sym:base.py::Base.run"),
        }

    def test_attribute_typed_in_init(self, tmp_path):
        calls = _calls(tmp_path, {
            "db.py": "class Conn:\n    def commit(self): pass\n",
            "repo.py": (
                "from db import Conn\n"
                "class Repo:\n"
                "    def __init__(self):\n        self.conn = Conn()\n"
                "    def save(self):\n        self.conn.commit()\n"
            ),
        })
        assert ("sym:repo.py::Repo.save", "sym:db.py::Conn.commit") in calls

    def test_third_party_receiver_is_never_linked(self, tmp_path):
        calls = _calls(tmp_path, {
            "client.py": "class Client:\n    def request(self): pass\n",
            "main.py": (
                "import requests\nfrom client import Client\n"
                "def go():\n    requests.request('GET', 'u')\n"
            ),
        })
        assert calls == set()

    def test_edge_count_stays_linear_in_callers(self, tmp_path):
        files = {}
        for i in range(40):
            files[f"provider_{i}.py"] = f"class P{i}:\n    def run(self): pass\n    def stop(self): pass\n"
            files[f"consumer_{i}.py"] = "def use(thing):\n    thing.run()\n    thing.stop()\n"
        assert _calls(tmp_path, files) == set()


class TestImportResolution:
    def _imports(self, tmp_path, files):
        write_files(tmp_path, files)
        return _edges(parse_tree(tmp_path, pkg_root="pkg"), "import")

    def test_relative_imports_resolve(self, tmp_path):
        imports = self._imports(tmp_path, {
            "__init__.py": "",
            "a.py": "from .b import f\nfrom . import c\n",
            "b.py": "def f(): pass\n",
            "c.py": "",
            "sub/__init__.py": "from ..b import f\n",
        })
        assert ("file:a.py", "file:b.py") in imports
        assert ("file:a.py", "file:c.py") in imports
        assert ("file:sub/__init__.py", "file:b.py") in imports

    def test_tree_as_named_package(self, tmp_path):
        imports = self._imports(tmp_path, {
            "__init__.py": "",
            "a.py": "from pkg.b import f\nimport pkg.c\n",
            "b.py": "def f(): pass\n",
            "c.py": "",
        })
        assert imports == {("file:a.py", "file:b.py"), ("file:a.py", "file:c.py")}

    def test_absolute_imports_resolve_in_src_layout(self, tmp_path):
        imports = self._imports(tmp_path, {
            "src/proj/__init__.py": "",
            "src/proj/a.py": "from proj.b import f\nfrom proj import c\n",
            "src/proj/b.py": "def f(): pass\n",
            "src/proj/c.py": "",
        })
        assert ("file:src/proj/a.py", "file:src/proj/b.py") in imports
        assert ("file:src/proj/a.py", "file:src/proj/c.py") in imports

    def test_src_marked_as_package_still_resolves(self, tmp_path):
        imports = self._imports(tmp_path, {
            "src/__init__.py": "",
            "src/proj/__init__.py": "",
            "src/proj/mod.py": "def f(): pass\n",
            "app.py": "from proj.mod import f\n",
        })
        assert ("file:app.py", "file:src/proj/mod.py") in imports

    def test_namespace_package_without_init(self, tmp_path):
        imports = self._imports(tmp_path, {
            "ns/__init__.py": "",
            "ns/sub/mod.py": "def f(): pass\n",
            "app.py": "from ns.sub.mod import f\n",
            "other.py": "import mod\n",  # a third-party-looking name: no edge
        })
        assert imports == {("file:app.py", "file:ns/sub/mod.py")}

    def test_relative_import_beyond_top_level_makes_no_edge(self, tmp_path):
        imports = self._imports(tmp_path, {
            "top.py": "x = 1\n",
            "a/__init__.py": "",
            "a/b/__init__.py": "",
            "a/b/m.py": "from ....top import x\n",
        })
        assert imports == set()

    def test_third_party_import_does_not_match_local_file(self, tmp_path):
        imports = self._imports(tmp_path, {
            "utils.py": "",
            "path.py": "",
            "app.py": "import os.path\nfrom django.utils import timezone\n",
        })
        assert imports == set()


class TestCoEditDetection:
    def test_pairs_and_counts(self, sample_project_path):
        co_edits = _detect_co_edits(sample_project_path)
        assert co_edits == {
            ("sample_pkg/core.py", "sample_pkg/utils/db.py"): 1,
            ("sample_pkg/utils/db.py", "sample_pkg/utils/helpers.py"): 1,
        }

    def test_paths_are_relative_to_a_sub_tree(self, sample_pkg_path):
        assert set(_detect_co_edits(sample_pkg_path)) == {
            ("core.py", "utils/db.py"),
            ("utils/db.py", "utils/helpers.py"),
        }

    def test_edges_carry_the_number_of_shared_commits(self, tmp_path):
        write_files(tmp_path, {"a.py": "", "b.py": "", "c.py": ""})
        git(tmp_path, "init", "-q")
        commit_all(tmp_path, "initial")
        for i in range(3):
            write_files(tmp_path, {"a.py": f"x = {i}\n", "b.py": f"y = {i}\n"})
            commit_all(tmp_path, f"change {i}")
        graph = parse_tree(tmp_path, pkg_root="p")
        co = [e for e in graph["edges"] if e["kind"] == "co_edit"]
        assert co == [{"src": "file:a.py", "dst": "file:b.py", "kind": "co_edit", "weight": 3}]

    def test_initial_and_bulk_commits_are_ignored(self, tmp_path):
        names = [f"m{i:02d}.py" for i in range(30)]
        write_files(tmp_path, {n: "" for n in names})
        git(tmp_path, "init", "-q")
        commit_all(tmp_path, "initial import")
        write_files(tmp_path, {n: "x = 1\n" for n in names})
        commit_all(tmp_path, "reformat everything")
        assert _detect_co_edits(tmp_path) == {}

    def test_non_ascii_file_names_are_kept(self, tmp_path):
        write_files(tmp_path, {"café.py": "", "menu.py": ""})
        git(tmp_path, "init", "-q")
        commit_all(tmp_path, "initial")
        write_files(tmp_path, {"café.py": "x = 1\n", "menu.py": "y = 1\n"})
        commit_all(tmp_path, "together")
        assert _detect_co_edits(tmp_path) == {("café.py", "menu.py"): 1}

    def test_no_git_repository_means_no_co_edits(self, tmp_path):
        write_files(tmp_path, {"a.py": "", "b.py": ""})
        assert _detect_co_edits(tmp_path) == {}


class TestFileDiscovery:
    def _names(self, tree):
        return [f.relative_to(tree).as_posix() for f in _iter_py_files(tree)]

    def test_skips_vendored_dirs_below_tree_only(self, tmp_path):
        tree = tmp_path / "venv" / "proj"  # tree itself lives under a 'venv' dir
        write_files(tree, {
            "a.py": "", "node_modules/x/y.py": "", ".venv/lib/z.py": "",
            ".git/hooks/h.py": "", ".nox/s/n.py": "", "pkg/__pycache__/c.py": "",
        })
        assert self._names(tree) == ["a.py"]

    def test_skips_build_output_at_the_top_only(self, tmp_path):
        write_files(tmp_path, {
            "pkg/mod.py": "", "build/lib/pkg/mod.py": "", "dist/pkg/mod.py": "",
            "pkg/build/steps.py": "",
        })
        assert self._names(tmp_path) == ["pkg/build/steps.py", "pkg/mod.py"]

    def test_skips_a_virtualenv_under_any_name(self, tmp_path):
        write_files(tmp_path, {"a.py": "", "myenv/pyvenv.cfg": "", "myenv/lib/site.py": ""})
        assert self._names(tmp_path) == ["a.py"]

    def test_honours_gitignore_inside_a_repository(self, tmp_path):
        write_files(tmp_path, {
            ".gitignore": "generated/\n", "a.py": "", "generated/big.py": "",
            "untracked.py": "",
        })
        git(tmp_path, "init", "-q")
        git(tmp_path, "add", "a.py", ".gitignore")
        assert self._names(tmp_path) == ["a.py", "untracked.py"]

    def test_symlink_leaving_the_tree_is_not_followed(self, tmp_path):
        outside = tmp_path / "outside"
        tree = tmp_path / "tree"
        write_files(outside, {"secrets.py": '"""PROD CREDENTIALS."""\n'})
        write_files(tree, {"a.py": "", "real.py": ""})
        (tree / "leak.py").symlink_to(outside / "secrets.py")
        (tree / "alias.py").symlink_to(tree / "real.py")
        assert self._names(tree) == ["a.py", "alias.py", "real.py"]

    def test_broken_files_do_not_abort_parse(self, tmp_path):
        write_files(tmp_path, {"ok.py": "def f(): pass\n", "bad.py": "def (:\n"})
        (tmp_path / "dangling.py").symlink_to(tmp_path / "missing.py")
        graph = parse_tree(tmp_path, pkg_root="p")
        assert "sym:ok.py::f" in graph["nodes"]
        assert "file:bad.py" in graph["nodes"]
        assert "file:dangling.py" not in graph["nodes"]


class TestSourceEncoding:
    def test_bom_and_declared_encoding_are_parsed(self, tmp_path):
        (tmp_path / "bom.py").write_bytes(b"\xef\xbb\xbfdef bom_func():\n    return 1\n")
        (tmp_path / "latin.py").write_bytes(
            b"# -*- coding: latin-1 -*-\ndef latin_func():\n    return 'caf\xe9'\n"
        )
        nodes = parse_tree(tmp_path, pkg_root="p")["nodes"]
        assert "sym:bom.py::bom_func" in nodes
        assert "sym:latin.py::latin_func" in nodes
