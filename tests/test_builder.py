"""Tests for ckg.graph.builder — CodeGraph, enrichment, structure map rendering."""

import pytest
from ckg.graph.builder import CodeGraph, enrich_node_text, render_structure_map


class TestCodeGraph:
    def test_from_dict(self):
        data = {
            "nodes": {
                "A": {"id": "A", "text": "node A", "kind": "file"},
                "B": {"id": "B", "text": "node B", "kind": "sym"},
            },
            "edges": [
                {"src": "A", "dst": "B", "kind": "contains"},
            ],
            "meta": {"n_files": 1, "n_nodes": 2, "n_edges": 1, "pkg_root": "test"},
        }
        cg = CodeGraph.from_dict(data)
        assert cg.n_files == 1
        assert cg.n_nodes == 2
        assert cg.n_edges == 1

    def test_node_lookup(self, sample_cg):
        file_ids = sample_cg.file_nodes()
        assert len(file_ids) > 0
        node = sample_cg.node(file_ids[0])
        assert node is not None
        assert node["kind"] == "file"

    def test_file_nodes(self, sample_cg):
        files = sample_cg.file_nodes()
        assert all(fid.startswith("file:") for fid in files)
        assert len(files) >= 3

    def test_neighbors(self, sample_cg):
        files = sample_cg.file_nodes()
        # Find a file that has imports (it will have neighbors)
        found = False
        for fid in files:
            nbrs = sample_cg.neighbors(fid)
            if nbrs:
                found = True
                # Each neighbor is (neighbor_id, edge_kind, direction)
                for nbr_id, kind, direction in nbrs:
                    assert direction in ("in", "out")
                    assert kind in ("import", "call", "co_edit", "contains")
                break
        assert found, "At least one file should have neighbors"

    def test_file_neighbors(self, sample_cg):
        files = sample_cg.file_nodes()
        file_nbrs = sample_cg.file_neighbors(files[0])
        if file_nbrs:
            for nbr_id, kind in file_nbrs:
                assert nbr_id.startswith("file:")
                assert isinstance(kind, str)


class TestEnrichNodeText:
    def test_enrich_file_node(self, sample_pkg_path):
        from ckg.graph.parser import parse_tree
        graph = parse_tree(sample_pkg_path, pkg_root="sample_pkg")
        core_ids = [nid for nid in graph["nodes"] if "core.py" in nid and nid.startswith("file:")]
        assert len(core_ids) == 1
        core_node = graph["nodes"][core_ids[0]]
        enriched = enrich_node_text(sample_pkg_path, core_node, "sample_pkg")
        # After enrichment, text should contain the docstring excerpt
        assert "sample package" in enriched["text"].lower(), (
            f"Expected docstring in enriched text. Got: {enriched['text']}"
        )

    def test_enrich_sym_node(self, sample_pkg_path):
        from ckg.graph.parser import parse_tree
        graph = parse_tree(sample_pkg_path, pkg_root="sample_pkg")
        sym_ids = [nid for nid in graph["nodes"]
                   if nid.startswith("sym:") and "process_order" in nid]
        if sym_ids:
            sym_node = graph["nodes"][sym_ids[0]]
            enriched = enrich_node_text(sample_pkg_path, sym_node, "sample_pkg")
            # Should have the function signature
            assert "def" in enriched["text"] or "process_order" in enriched["text"]


class TestRenderStructureMap:
    def test_render_with_anchors(self, sample_cg):
        files = sample_cg.file_nodes()
        anchors = files[:3] if len(files) >= 3 else files
        result = render_structure_map(anchors, sample_cg, query="test query")

        assert "Structure Map" in result
        assert "test query" in result
        assert "Anchor Files" in result
        assert "Dependency Reach" in result

    def test_render_empty_anchors(self, sample_cg):
        result = render_structure_map([], sample_cg)
        assert "*(No additional files found" in result or "Anchor Files" in result

    def test_render_includes_file_labels(self, sample_cg):
        files = sample_cg.file_nodes()
        anchors = files[:2]
        result = render_structure_map(anchors, sample_cg)
        # Should include at least one file path
        assert "core.py" in result or "helpers.py" in result or "db.py" in result
