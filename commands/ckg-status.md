---
description: CKG — show status (CLI, project, graph cache, Oracle PGQ)
---

# CKG Status

Check whether the Code Knowledge Graph is available and what state it is in.

```bash
ckg --version && ls -l .ckg/code_graph.json; ckg oracle-status
```

Then report:
1. Whether the `ckg` CLI is available, and its version.
2. Whether a graph cache exists at `.ckg/code_graph.json` (it is built
   automatically on the first coding prompt, or with `ckg build .`).
3. Whether Oracle PGQ is configured (`CKG_ORACLE_DSN`) and connected, and the
   retrieval mode.

If the CLI is missing, do NOT attempt to implement anything — tell the user
how to install it: `uv tool install git+https://github.com/jasperan/ckg.git`
(or `pip install git+https://github.com/jasperan/ckg.git`).
