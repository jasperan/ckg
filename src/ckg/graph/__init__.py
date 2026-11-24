"""Code graph construction — parse Python source trees into typed dependency graphs.

Edge kinds:
  contains — file contains a symbol (file → sym)
  import   — file imports another file
  call     — function/method calls another function/method
  co_edit  — two files changed together in git history (undirected, weighted)
"""

from ckg.graph.builder import CodeGraph, build_graph
from ckg.graph.parser import parse_tree

__all__ = ["CodeGraph", "build_graph", "parse_tree"]
