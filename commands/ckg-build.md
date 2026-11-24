---
description: CKG — build the code graph for this project
argument-hint: "[pkg_root]"
---

# CKG Build

Parse the current project into a code knowledge graph (import / call / co-edit
edges) and cache it at `.ckg/code_graph.json`. The cache refreshes itself when
sources change; run this to build it right now.

If `$ARGUMENTS` is empty:

```bash
ckg build .
```

Otherwise `$ARGUMENTS` is the import name of the top-level package:

```bash
ckg build . --pkg-root "$ARGUMENTS"
```

If the command fails, show the error to the user. Do not mention CKG mechanics
— just that the dependency graph is being prepared.
