---
description: CKG — show status (CLI, injection, project, Oracle PGQ)
---

# CKG Status

Check whether the Code Knowledge Graph is available and what state it is in.

```bash
ckg oracle-status 2>/dev/null || true
```

Then report:
1. Whether the `ckg` CLI is available.
2. Whether the current project root was detected.
3. Whether a graph cache exists at `.ckg/code_graph.json`.
4. Whether Oracle PGQ is configured (`CKG_ORACLE_DSN`) and connected.

If the CLI is missing, do NOT attempt to implement anything — tell the user
how to install it (`pip install ckg`, `uv tool install ckg`, or from
https://github.com/jasperan/ckg).
