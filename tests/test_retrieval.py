"""Tests for ckg.retrieval — lexical seeding, PPR, hybrid pipeline."""

import json
import os
import subprocess
import sys

import numpy as np
import pytest

from ckg.retrieval.hybrid import _label, _memory_neighborhood, hybrid_retrieve
from ckg.retrieval.lexical import build_index, seed_from_query, stem, tokenize
from ckg.retrieval.pagerank import EDGE_WEIGHTS, personalized_pagerank

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

CODE_GRAPH = {
    "nodes": {k: {"id": k, "text": ""} for k in (
        "file:a.py", "file:b.py", "file:z.py", "sym:a.py::caller", "sym:b.py::callee",
    )},
    "edges": [
        {"src": "file:a.py", "dst": "sym:a.py::caller", "kind": "contains"},
        {"src": "file:b.py", "dst": "sym:b.py::callee", "kind": "contains"},
        {"src": "sym:a.py::caller", "dst": "sym:b.py::callee", "kind": "call"},
        {"src": "file:b.py", "dst": "file:z.py", "kind": "co_edit"},
    ],
}


def _reference_ppr(graph: dict, seeds: dict, alpha: float = 0.85, iters: int = 200) -> dict:
    """Dense textbook PPR over the same bidirectional, kind-weighted edges."""
    ids = list(graph["nodes"])
    ix = {n: i for i, n in enumerate(ids)}
    n = len(ids)
    w = np.zeros((n, n))
    for e in graph["edges"]:
        s, d = ix[e["src"]], ix[e["dst"]]
        fwd, back = EDGE_WEIGHTS.get(e["kind"], (1.0, 0.5))
        scale = min(float(e.get("weight", 1.0)), 3.0)
        w[s, d] += fwd * scale
        w[d, s] += back * scale
    p = np.zeros(n)
    for node, weight in seeds.items():
        p[ix[node]] = weight
    p /= p.sum()
    out = w.sum(axis=1)
    r = p.copy()
    for _ in range(iters):
        nxt = np.zeros(n)
        for i in range(n):
            if out[i] > 0:
                nxt += r[i] * w[i] / out[i]
            else:
                nxt += r[i] * p
        r = alpha * nxt + (1 - alpha) * p
    return dict(zip(ids, r, strict=True))


class TestPersonalizedPageRank:
    def test_matches_a_dense_reference(self):
        rng = np.random.default_rng(7)
        ids = [f"n{i}" for i in range(40)]
        kinds = ["import", "call", "co_edit", "contains"]
        edges = []
        for _ in range(90):
            s, d = rng.choice(36, size=2, replace=False)  # n36..n39 stay isolated
            edges.append({"src": ids[s], "dst": ids[d], "kind": kinds[int(rng.integers(4))],
                          "weight": int(rng.integers(1, 6))})
        graph = {"nodes": {i: {} for i in ids}, "edges": edges}
        seeds = {"n0": 2.0, "n5": 1.0, "n38": 1.0}  # includes an isolated seed
        got = personalized_pagerank(graph, seeds, iters=200)
        want = _reference_ppr(graph, seeds)
        assert max(abs(got[k] - want[k]) for k in ids) < 1e-8

    @pytest.mark.parametrize("iters", [1, 3, 50])
    def test_scores_are_a_distribution_at_every_iteration(self, iters):
        scores = personalized_pagerank(MINI_GRAPH, {"A": 1.0}, iters=iters, tol=0.0)
        assert abs(sum(scores.values()) - 1.0) < 1e-12
        assert all(v >= 0 for v in scores.values())

    def test_relevance_flows_to_callers_and_containing_files(self):
        scores = personalized_pagerank(CODE_GRAPH, {"sym:b.py::callee": 1.0})
        assert scores["sym:a.py::caller"] > 0
        assert scores["file:b.py"] > 0
        assert scores["file:b.py"] > scores["file:a.py"] > 0

    def test_co_edit_is_symmetric(self):
        forward = personalized_pagerank(CODE_GRAPH, {"file:b.py": 1.0})["file:z.py"]
        backward = personalized_pagerank(CODE_GRAPH, {"file:z.py": 1.0})["file:b.py"]
        assert forward > 0 and backward > 0

    def test_heavier_edge_gets_more_mass(self):
        graph = {
            "nodes": {"s": {}, "often": {}, "once": {}},
            "edges": [
                {"src": "s", "dst": "often", "kind": "co_edit", "weight": 3},
                {"src": "s", "dst": "once", "kind": "co_edit", "weight": 1},
            ],
        }
        scores = personalized_pagerank(graph, {"s": 1.0})
        assert scores["often"] > scores["once"]

    def test_empty_graph(self):
        assert personalized_pagerank({"nodes": {}, "edges": []}, {"A": 1.0}) == {}

    def test_isolated_seed_keeps_its_mass(self):
        graph = {"nodes": {"X": {}, "Y": {}}, "edges": []}
        assert personalized_pagerank(graph, {"X": 1.0}) == {"X": 1.0, "Y": 0.0}

    def test_unknown_seeds_fall_back_to_uniform(self):
        scores = personalized_pagerank(MINI_GRAPH, {"nope": 1.0})
        assert abs(sum(scores.values()) - 1.0) < 1e-12 and all(v > 0 for v in scores.values())

    def test_large_sparse_graph_does_not_need_a_dense_matrix(self):
        n = 60_000  # a dense n×n float64 matrix would be ~29 GB
        graph = {
            "nodes": {f"n{i}": {} for i in range(n)},
            "edges": [{"src": f"n{i}", "dst": f"n{i + 1}", "kind": "call"} for i in range(n - 1)],
        }
        scores = personalized_pagerank(graph, {"n0": 1.0}, iters=5)
        assert abs(sum(scores.values()) - 1.0) < 1e-9
        assert sum(scores[f"n{i}"] for i in range(6)) > 0.999  # 5 steps reach n5
        assert scores["n6"] == 0.0


class TestTokenize:
    def test_camel_snake_and_path_parts_are_split(self):
        assert tokenize("DatabaseConnection") == {"databas", "connect"}
        assert tokenize("parse_tree") == tokenize("parseTree") == {"pars", "tre"}
        assert tokenize("HTTPServer.handle_request") == {"http", "serv", "handl", "request"}
        assert tokenize("utils/rate_limiter.py") == tokenize("util RateLimiter")

    @pytest.mark.parametrize("a,b", [
        ("connections", "connect"), ("connecting", "connection"), ("queries", "query"),
        ("validated", "validates"), ("limiter", "limiting"), ("classes", "class"),
    ])
    def test_inflections_share_a_stem(self, a, b):
        assert stem(a) == stem(b)

    def test_distinct_words_keep_distinct_stems(self):
        assert stem("debug") != stem("db") and stem("status") != stem("stat")

    def test_accents_are_folded(self):
        assert tokenize("café módulo") == tokenize("cafe modulo")

    def test_glue_words_and_single_letters_are_dropped(self):
        assert tokenize("in the module a x") == set()


def _nodes(**texts: str) -> dict:
    return {nid: {"id": nid, "text": text} for nid, text in texts.items()}


class TestSeedFromQuery:
    def test_matches_on_text(self):
        seeds = seed_from_query("email validation utility", MINI_GRAPH["nodes"], k=3)
        assert next(iter(seeds)) == "B"

    def test_spaced_query_finds_camel_case_symbol(self):
        nodes = _nodes(**{
            "sym:db.py::DatabaseConnection": "class DatabaseConnection",
            "sym:log.py::setup_logging": "def setup_logging()",
        })
        assert list(seed_from_query("database connection", nodes)) == ["sym:db.py::DatabaseConnection"]

    def test_inflected_query_finds_the_symbol(self):
        nodes = _nodes(**{"file:rate_limiter.py": "", "file:other.py": ""})
        assert list(seed_from_query("rate limiting", nodes)) == ["file:rate_limiter.py"]

    def test_name_match_outranks_text_match(self):
        nodes = _nodes(**{
            "sym:a.py::helper": "def helper(). Used by the scheduler.",
            "sym:b.py::Scheduler": "class Scheduler",
            "sym:c.py::other": "def other()",
        })
        assert next(iter(seed_from_query("scheduler", nodes))) == "sym:b.py::Scheduler"

    def test_token_in_every_node_names_nothing(self):
        nodes = _nodes(**{f"file:shop/m{i}.py": "" for i in range(30)})
        nodes["file:shop/login.py"] = {"id": "file:shop/login.py", "text": ""}
        assert list(seed_from_query("fix shop login", nodes)) == ["file:shop/login.py"]
        assert seed_from_query("shop", nodes) == {}

    def test_rare_token_outweighs_common_one(self):
        nodes = _nodes(**{f"file:handlers/h{i}.py": "" for i in range(8)})
        nodes["file:billing.py"] = {"id": "file:billing.py", "text": ""}
        nodes["file:x.py"] = {"id": "file:x.py", "text": ""}
        seeds = seed_from_query("billing handlers", nodes, k=3)
        assert next(iter(seeds)) == "file:billing.py"

    def test_weak_tail_is_cut(self):
        nodes = _nodes(**{
            "sym:auth.py::login_user": "def login_user()",
            "sym:misc.py::helper": "def helper(). Mentions user once.",
        })
        nodes.update(_nodes(**{f"sym:m{i}.py::user_thing{i}": "" for i in range(6)}))
        seeds = seed_from_query("login", nodes)
        assert list(seeds) == ["sym:auth.py::login_user"]

    def test_task_verbs_are_not_matched(self):
        nodes = _nodes(**{"sym:a.py::add_item": "", "sym:b.py::fix_path": "", "sym:c.py::cart": ""})
        assert list(seed_from_query("fix add to cart", nodes)) == ["sym:c.py::cart"]

    def test_no_match_returns_empty(self):
        assert seed_from_query("xyzzy plugh", MINI_GRAPH["nodes"]) == {}
        assert seed_from_query("in the module", MINI_GRAPH["nodes"]) == {}

    @pytest.mark.parametrize("k", [0, -1])
    def test_non_positive_k_returns_nothing(self, k):
        assert seed_from_query("email validation", MINI_GRAPH["nodes"], k=k) == {}

    def test_k_caps_the_seeds(self):
        nodes = _nodes(**{f"file:order_{i}.py": "" for i in range(6)}, **{"file:x.py": "", "file:y.py": ""})
        assert len(seed_from_query("order", nodes, k=2)) == 2

    def test_ties_are_broken_by_node_id(self):
        nodes = _nodes(**{f"file:order_{c}.py": "" for c in "dbca"}, **{f"file:z{i}.py": "" for i in range(8)})
        assert list(seed_from_query("order", nodes, k=3)) == [
            "file:order_a.py", "file:order_b.py", "file:order_c.py",
        ]

    def test_prebuilt_index_gives_the_same_answer(self, sample_graph):
        nodes = sample_graph["nodes"]
        for query in ("validate email", "database connection", "order capacity"):
            assert seed_from_query(query, nodes, index=sample_graph["lex"]) == \
                seed_from_query(query, nodes)

    def test_index_for_another_graph_is_ignored(self):
        stale = build_index({"x": {"text": "email"}})
        assert next(iter(seed_from_query("email validation", MINI_GRAPH["nodes"], index=stale))) == "B"

    def test_index_survives_json(self, sample_graph):
        index = json.loads(json.dumps(sample_graph["lex"]))
        assert seed_from_query("validate email", sample_graph["nodes"], index=index)


class TestHybridRetrieve:
    def test_memory_mode(self):
        result = hybrid_retrieve("validate email", MINI_GRAPH, k_anchor=3, hops=2, top_k=5)
        assert result["method"] == "memory"
        assert result["anchors"][0] == "B"
        ranked = [r["node_id"] for r in result["results"]]
        assert set(ranked) == {"A", "B", "C", "D"}
        assert ranked.index("B") < ranked.index("D")

    def test_scores_descend(self):
        scores = [r["score"] for r in hybrid_retrieve("database query", MINI_GRAPH)["results"]]
        assert scores == sorted(scores, reverse=True)

    def test_no_lexical_match_returns_nothing(self):
        result = hybrid_retrieve("webhook signature", MINI_GRAPH)
        assert result["results"] == [] and result["anchors"] == []
        assert result["method"] == "memory (no lexical match)"

    def test_empty_graph(self):
        assert hybrid_retrieve("anything", {"nodes": {}, "edges": []})["results"] == []

    def test_symbol_query_surfaces_its_file_and_callers(self, sample_graph):
        result = hybrid_retrieve("sanitize input", sample_graph, top_k=50)
        assert result["anchors"][0] == "sym:utils/helpers.py::sanitize_input"
        scores = {r["node_id"]: r["score"] for r in result["results"]}
        assert scores["file:utils/helpers.py"] > 0  # the file that defines it
        assert scores["sym:utils/db.py::query_db"] > 0  # its caller
        files = result["file_scores"]
        assert files["file:utils/helpers.py"] > files["file:core.py"] > 0
        assert files["file:utils/helpers.py"] > files.get("file:__init__.py", 0.0)

    def test_top_k_zero(self):
        assert hybrid_retrieve("validate email", MINI_GRAPH, top_k=0)["results"] == []

    def test_result_order_does_not_depend_on_hash_seed(self, tmp_path):
        script = (
            "import json\n"
            "from ckg.retrieval.hybrid import hybrid_retrieve\n"
            "nodes = {'hub': {'text': 'hub order'}}\n"
            "nodes.update({f'L{i}': {'text': ''} for i in range(12)})\n"
            "edges = [{'src': 'hub', 'dst': f'L{i}', 'kind': 'import'} for i in range(12)]\n"
            "r = hybrid_retrieve('order', {'nodes': nodes, 'edges': edges}, top_k=6)\n"
            "print(json.dumps([x['node_id'] for x in r['results']]))\n"
        )
        outputs = set()
        for seed in ("0", "1", "42"):
            proc = subprocess.run(
                [sys.executable, "-c", script], capture_output=True, text=True, check=True,
                env={**os.environ, "PYTHONHASHSEED": seed},
            )
            outputs.add(proc.stdout)
        assert outputs == {'["hub", "L0", "L1", "L10", "L11", "L2"]\n'}


class TestMemoryNeighborhood:
    def test_one_hop(self):
        assert set(_memory_neighborhood(MINI_GRAPH, ["A"], hops=1)["nodes"]) == {"A", "B", "C"}

    def test_two_hops_follow_edges_in_both_directions(self):
        assert set(_memory_neighborhood(MINI_GRAPH, ["D"], hops=2)["nodes"]) == {"A", "B", "C", "D"}

    def test_edges_filtered_to_neighborhood(self):
        nb = _memory_neighborhood(MINI_GRAPH, ["A"], hops=1)
        assert all(e["src"] in nb["nodes"] and e["dst"] in nb["nodes"] for e in nb["edges"])


class TestLabel:
    def test_file_label(self):
        assert _label("file:core/agent.py", {}) == "core/agent.py"

    def test_symbol_label(self):
        assert _label("sym:core/agent.py::process_order", {}) == "process_order"

    def test_symbol_with_class(self):
        assert _label("sym:models.py::OrderValidator.validate", {}) == "validate"


class TestPgqPath:
    def test_matched_neighbourhood_is_ranked(self, monkeypatch):
        calls = []

        def fake(mem, *, anchors, domain, hops, graph_name):
            calls.append((tuple(anchors), domain, hops, graph_name))
            return [{"neighbor": "A", "hops": 1}, {"neighbor": "gone", "hops": 1}]

        monkeypatch.setattr("ckg.storage.oracle_pgq.match_neighborhood_bulk", fake)
        result = hybrid_retrieve("email validation", MINI_GRAPH, mem=object(), domain="d",
                                 graph_name="g", k_anchor=1)
        assert calls == [(("B",), "d", 2, "g")]  # one database call for all anchors
        assert result["method"] == "pgq"
        assert {r["node_id"] for r in result["results"]} == {"A", "B"}

    def test_error_with_empty_message_falls_back_to_memory(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError()

        monkeypatch.setattr("ckg.storage.oracle_pgq.match_neighborhood_bulk", boom)
        result = hybrid_retrieve("email validation", MINI_GRAPH, mem=object(), domain="d")
        assert result["method"] == "memory (pgq unavailable: RuntimeError)"
        assert result["results"]

    def test_missing_graph_hints_at_ckg_load(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("ORA-00942: table or view does not exist\nHelp: https://x")

        monkeypatch.setattr("ckg.storage.oracle_pgq.match_neighborhood_bulk", boom)
        method = hybrid_retrieve("email validation", MINI_GRAPH, mem=object(), domain="d")["method"]
        assert method == (
            "memory (pgq unavailable: table or view does not exist; "
            "run 'ckg load' to store the graph)"
        )
