"""CKG — Code Knowledge Graph.

Structure-aware retrieval for coding agents. Parse codebases into typed
dependency graphs (import / call / co-edit edges), persist them in Oracle PGQ
property graphs, and inject dependency-aware context into agents so they find
the right files faster.

Core modules:
  ckg.graph     — parse Python source trees into typed code graphs
  ckg.storage   — Oracle PGQ persistence (load, query, traverse)
  ckg.retrieval — hybrid retrieval: lexical anchors → graph reach → PPR scoring
  ckg.claude    — Claude Code plugin: build context in the background
  ckg.cli       — standalone CLI for build, query, and inject workflows
"""

__version__ = "0.1.0"
