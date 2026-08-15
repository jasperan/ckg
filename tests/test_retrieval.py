"""Tests for ckg.retrieval — PPR, seed selection, hybrid pipeline."""

import pytest
from ckg.retrieval.pagerank import personalized_pagerank, seed_from_query
from ckg.retrieval.hybrid import hybrid_retrieve, _memory_neighborhood, _label


# --- Minimal test graph -------------------------------------------------------
MINI_GRAPH = {
    "nodes": {
        "A": {"id": "A", "text": "core processing module handles order validation"},
        "B": {"id": "B", "text": "email validation utility for user input"},
        "C": {"id": "C", "text": "database connection pool and query execution"},
        "D": {"id": "D", "text": "logging configuration and setup"},
    },
    "edges": [
        {"src": "A", "dst": "B", "kind": "import"},
        {"src": "A", "dst": "C", "kind": "import"},
        {"src": "B", "dst": "C", "kind": "call"},
        {"src": "C", "dst": "D", "kind": "import"},
    ],
}


class TestPersonalizedPageRank:
    def test_ppr_basic(self):
        scores = personalized_pagerank(MINI_GRAPH, {"A": 1.0})
        assert len(scores) == 4, "Should score all 4 nodes"
        # A should have the highest score (it's the seed)
        assert scores["A"] >= scores["B"], "Seed node should rank highly"
        assert all(0 <= v <= 1 for v in scores.values()), "Scores should be in [0, 1]"

    def test_ppr_empty_graph(self):
        scores = personalized_pagerank({"nodes": {}, "edges": []}, {"A": 1.0})
        assert scores == {}

    def test_ppr_isolated_node(self):
        graph = {
            "nodes": {"X": {"id": "X"}, "Y": {"id": "Y"}},
            "edges": [],
        }
        scores = personalized_pagerank(graph, {"X": 1.0})
        assert len(scores) == 2
        assert "X" in scores
        assert "Y" in scores

    def test_ppr_multiple_seeds(self):
        scores = personalized_pagerank(MINI_GRAPH, {"A": 1.0, "D": 1.0})
        # Both seed nodes should get reasonable scores
        assert scores["A"] > 0
        assert scores["D"] > 0

    def test_ppr_alpha_effect(self):
        # Higher alpha = less teleporting = more weight diffuses through edges
        scores_high = personalized_pagerank(MINI_GRAPH, {"A": 1.0}, alpha=0.95)
        scores_low = personalized_pagerank(MINI_GRAPH, {"A": 1.0}, alpha=0.5)
        # Both should produce valid scores for the seed node
        assert scores_high["A"] > 0
        assert scores_low["A"] > 0
        # Different alphas should produce different score distributions
        assert scores_high != scores_low


class TestSeedFromQuery:
    def test_seed_token_overlap(self):
        nodes = MINI_GRAPH["nodes"]
        seeds = seed_from_query("email validation utility", nodes, k=3)
        # "email" and "validation" tokens should match node B
        assert "B" in seeds, f"B should match 'email validation'. Seeds: {seeds}"
        # "processing" token should match node A
        seeds2 = seed_from_query("core processing handles", nodes, k=3)
        assert "A" in seeds2, f"A should match 'core processing'. Seeds: {seeds2}"

    def test_seed_k_limit(self):
        nodes = MINI_GRAPH["nodes"]
        seeds = seed_from_query("database processing", nodes, k=1)
        assert len(seeds) <= 1

    def test_seed_no_match_returns_empty(self):
        nodes = MINI_GRAPH["nodes"]
        seeds = seed_from_query("xyzzy_nonexistent_token", nodes)
        assert seeds == {}

    def test_seed_snake_case_splitting(self):
        nodes = {"file:rate_limiter.py": {"id": "file:rate_limiter.py",
                                           "text": "rate limiter module"}}
        seeds = seed_from_query("rate limiting", nodes, k=3)
        assert len(seeds) >= 1


class TestHybridRetrieve:
    def test_hybrid_memory_mode(self):
        result = hybrid_retrieve(
            "validate email",
            MINI_GRAPH,
            k_anchor=3, hops=2, top_k=5,
        )
        assert result["method"] == "memory"
        assert len(result["results"]) > 0
        assert len(result["anchors"]) > 0
        # "validate email" should match B
        assert any("B" in r["node_id"] for r in result["results"])

    def test_hybrid_empty_graph(self):
        result = hybrid_retrieve(
            "anything",
            {"nodes": {}, "edges": []},
        )
        assert "memory" in result["method"]
        assert result["results"] == []

    def test_hybrid_returns_scores_descending(self):
        result = hybrid_retrieve("database query", MINI_GRAPH, top_k=3)
        scores = [r["score"] for r in result["results"]]
        assert scores == sorted(scores, reverse=True), "Results should be sorted by score descending"

    def test_hybrid_anchor_labels(self):
        result = hybrid_retrieve("core processing", MINI_GRAPH)
        assert len(result["anchor_labels"]) > 0
        for label in result["anchor_labels"]:
            assert isinstance(label, str)
            assert len(label) > 0


class TestMemoryNeighborhood:
    def test_neighborhood_size(self):
        nb = _memory_neighborhood(MINI_GRAPH, ["A"], hops=1)
        # A is connected to B and C
        assert len(nb["nodes"]) >= 3, f"Should include A, B, C. Got {list(nb['nodes'].keys())}"

    def test_neighborhood_hops_2(self):
        nb = _memory_neighborhood(MINI_GRAPH, ["A"], hops=2)
        # A → B → C, A → C, C → D — all should be included
        assert len(nb["nodes"]) == 4, f"2-hop from A should include all nodes: {list(nb['nodes'].keys())}"

    def test_edges_filtered_to_neighborhood(self):
        nb = _memory_neighborhood(MINI_GRAPH, ["A"], hops=1)
        for e in nb["edges"]:
            assert e["src"] in nb["nodes"]
            assert e["dst"] in nb["nodes"]


class TestLabel:
    def test_file_label(self):
        assert _label("file:core/agent.py", {}) == "core/agent.py"

    def test_symbol_label(self):
        assert _label("sym:core/agent.py::process_order", {}) == "process_order"

    def test_symbol_with_class(self):
        assert _label("sym:models.py::OrderValidator.validate", {}) == "validate"
