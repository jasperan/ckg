---
description: CKG — hybrid retrieval over the code graph
argument-hint: "<query>"
---

# CKG Query

Run hybrid retrieval (lexical anchors → graph reach → Personalized PageRank)
over the cached code graph. Uses Oracle PGQ when configured, in-memory
otherwise.

```bash
ckg query --graph .ckg/code_graph.json -- "$ARGUMENTS"
```

The anchors are what the query names directly; the ranked list below them adds
the structurally close files and symbols (callers, callees, importers,
co-edited files). No anchors means the query names nothing in the code: retry
with identifiers from the task.
