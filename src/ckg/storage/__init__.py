"""Oracle PGQ persistence — store code graphs as property graphs.

Layers an Oracle SQL property graph on top of relational tables so the
dependency neighbourhood of any symbol can be matched in the database via
GRAPH_TABLE ... MATCH. Ranking (Personalized PageRank) runs in Python over
the matched neighbourhood.
"""

from ckg.storage.oracle_pgq import (
    DEFAULT_GRAPH_NAME,
    DEFAULT_TABLE_PREFIX,
    create_property_graph,
    ensure_schema,
    load_graph,
    match_neighborhood_bulk,
    replace_domain_graph,
)

__all__ = [
    "DEFAULT_GRAPH_NAME",
    "DEFAULT_TABLE_PREFIX",
    "create_property_graph",
    "ensure_schema",
    "load_graph",
    "match_neighborhood_bulk",
    "replace_domain_graph",
]
