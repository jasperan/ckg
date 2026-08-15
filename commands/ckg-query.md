---
description: CKG — hybrid retrieval over the code graph
argument-hint: "<query>"
---

# CKG Query

Run hybrid retrieval (lexical anchors → graph reach → Personalized PageRank)
over the cached code graph. Uses Oracle PGQ when configured, in-memory
otherwise.

```bash
ckg query "{{query}}" --graph .ckg/code_graph.json
```

Use the ranked results as the starting point for exploration — the anchors are
what keyword search finds anyway; the ranked list below them surfaces the
structurally-important dependencies.
