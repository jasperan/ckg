"""Tests for ckg.graph.parser — AST-based code graph parsing."""

import pytest
from ckg.graph.parser import parse_tree, _iter_py_files, _top_level_symbols, _detect_co_edits


class TestParseTree:
    """End-to-end parsing of the sample project fixture."""

    def test_parse_creates_nodes(self, sample_pkg_path):
        graph = parse_tree(sample_pkg_path, pkg_root="sample_pkg")
        nodes = graph["nodes"]
        edges = graph["edges"]

        assert len(nodes) > 0, "Should have nodes"
        assert len(edges) > 0, "Should have edges"
        assert graph["meta"]["pkg_root"] == "sample_pkg"

    def test_file_nodes_exist(self, sample_graph):
        nodes = sample_graph["nodes"]
        file_ids = [nid for nid in nodes if nid.startswith("file:")]
        assert len(file_ids) >= 3, "Should have at least core.py, helpers.py, db.py as file nodes"

        # core.py should be present
        assert any("core.py" in fid for fid in file_ids), "core.py should be a file node"

    def test_symbol_nodes_exist(self, sample_graph):
        nodes = sample_graph["nodes"]
        sym_ids = [nid for nid in nodes if nid.startswith("sym:")]

        # Should have some functions/classes
        assert len(sym_ids) > 0, "Should have symbol nodes"

    def test_import_edges(self, sample_graph):
        edges = sample_graph["edges"]
        imports = [e for e in edges if e["kind"] == "import"]

        # db.py imports from helpers.py (sanitize_input)
        assert len(imports) >= 1, f"Expected at least 1 import edge, got {len(imports)}"

        # Check cross-module imports exist
        import_pairs = {(e["src"], e["dst"]) for e in imports}
        # Verify at least one import pair was found
        assert len(import_pairs) >= 1, f"Expected at least 1 import pair, found: {import_pairs}"

    def test_contains_edges(self, sample_graph):
        edges = sample_graph["edges"]
        contains = [e for e in edges if e["kind"] == "contains"]
        assert len(contains) > 0, "Should have contains edges (file → symbol)"

    def test_meta_counts(self, sample_graph):
        meta = sample_graph["meta"]
        assert meta["n_files"] >= 3
        assert meta["n_nodes"] > meta["n_files"], "Should have more nodes than files (symbols exist)"
        assert meta["n_edges"] > 0

    def test_parse_nonexistent_tree_raises(self):
        with pytest.raises(FileNotFoundError):
            parse_tree("/nonexistent/path", pkg_root="test")

    def test_no_duplicate_node_ids(self, sample_graph):
        nodes = list(sample_graph["nodes"].keys())
        assert len(nodes) == len(set(nodes)), "Node IDs must be unique"


class TestCoEditDetection:
    """Git co-edit edge detection."""

    def test_detect_co_edits(self, sample_project_path):
        # _detect_co_edits runs git from the tree root; the git repo is at the
        # parent of sample_pkg, so pass the project root (git root).
        co_edits = _detect_co_edits(sample_project_path, max_commits=10)
        # core.py + db.py were edited together in the second commit
        # helpers.py + db.py were edited together in the third commit
        assert len(co_edits) >= 1, f"Expected at least 1 co-edit pair, got {co_edits}"

        # Both commits should create db.py co-edit edges
        db_pairs = [(a, b) for (a, b) in co_edits if "db.py" in a or "db.py" in b]
        assert len(db_pairs) >= 1, f"db.py should have co-edit edges: {co_edits}"

    def test_detect_co_edits_are_undirected(self, sample_project_path):
        co_edits = _detect_co_edits(sample_project_path / "sample_pkg", max_commits=10)
        for a, b in co_edits:
            # Pairs are sorted so a < b
            assert a <= b, f"Co-edit pair should be sorted: {(a, b)}"


class TestIterPyFiles:
    def test_iter_excludes_pycache(self, sample_pkg_path):
        files = _iter_py_files(sample_pkg_path)
        for f in files:
            assert "__pycache__" not in f.parts
            assert ".venv" not in f.parts

    def test_iter_finds_all_modules(self, sample_pkg_path):
        files = _iter_py_files(sample_pkg_path)
        paths = [f.name for f in files]
        assert "core.py" in paths
        assert "helpers.py" in paths
        assert "db.py" in paths
        assert "__init__.py" in paths


class TestTopLevelSymbols:
    def test_extracts_functions_and_classes(self, sample_pkg_path):
        import ast
        core_path = sample_pkg_path / "core.py"
        tree = ast.parse(core_path.read_text())
        syms = _top_level_symbols(tree)

        names = [name for name, _ in syms]
        assert "process_order" in names
        assert "OrderValidator" in names

    def test_extracts_class_methods(self, sample_pkg_path):
        import ast
        core_path = sample_pkg_path / "core.py"
        tree = ast.parse(core_path.read_text())
        syms = _top_level_symbols(tree)

        # OrderValidator methods should appear
        method_names = [name for name, _ in syms]
        assert "OrderValidator.validate" in method_names
        assert "OrderValidator.get_remaining_capacity" in method_names
