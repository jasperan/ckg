"""Code graph construction — parse Python source trees into typed dependency graphs.

Edge types:
  import  — file imports another file (directed)
  call    — function/method calls another function/method (directed)
  co_edit — two files changed together in the same git commit (undirected)
  contains — file contains a symbol (directed, file → sym)
"""

from ckg.graph.parser import parse_tree
from ckg.graph.builder import CodeGraph

__all__ = ["parse_tree", "CodeGraph"]
