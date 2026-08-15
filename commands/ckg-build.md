---
description: CKG — build the code graph for this project
argument-hint: [pkg_root]
---

# CKG Build

Parse the current project into a code knowledge graph (import / call / co-edit
edges) and cache it at `.ckg/code_graph.json`. Run once per project.

```bash
ckg build . --pkg-root "{{pkg_root:$(basename "$(pwd)")}}"
```

If the command fails, show the error to the user. Do not mention CKG mechanics
— just that the dependency graph is being prepared.
