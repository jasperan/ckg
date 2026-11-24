"""Hybrid retrieval — lexical anchors → graph reach → PPR scoring.

1. Lexical anchors — the nodes a query names (rarity-weighted token match)
2. Graph reach — the dependency neighbourhood of those anchors (imports,
   calls, co-edits), which keyword search alone misses
3. Personalized PageRank — structural ranking over that neighbourhood
"""

from ckg.retrieval.hybrid import hybrid_retrieve
from ckg.retrieval.lexical import seed_from_query
from ckg.retrieval.pagerank import personalized_pagerank

__all__ = ["hybrid_retrieve", "personalized_pagerank", "seed_from_query"]
