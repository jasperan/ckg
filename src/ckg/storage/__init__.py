"""Oracle PGQ persistence — store code graphs as property graphs.

Layers an Oracle SQL property graph on top of relational tables so the
dependency neighborhood of any symbol can be matched entirely in the database
via GRAPH_TABLE ... MATCH, rather than reconstructed in Python.

This is the traversal/match half of structure-aware retrieval. Ranking
(Personalized PageRank) still runs in Python over the matched neighborhood.
"""

from ckg.storage.oracle_pgq import (
    DEFAULT_GRAPH_NAME,
    create_property_graph,
    match_neighborhood,
    match_edges,
    load_graph,
    upsert_graph_nodes,
    upsert_graph_edges,
)

__all__ = [
    "DEFAULT_GRAPH_NAME",
    "create_property_graph",
    "match_neighborhood",
    "match_edges",
    "load_graph",
    "upsert_graph_nodes",
    "upsert_graph_edges",
]
