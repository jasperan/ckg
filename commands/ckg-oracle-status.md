---
description: CKG — check Oracle AI Database connectivity and graph stats
---

# CKG Oracle Status

Check whether Oracle AI Database 26ai Free is reachable and what is stored for
the configured PGQ domain.

```bash
ckg oracle-status
```

Report:

1. **Configured?** — `CKG_ORACLE_DSN` must be set (e.g. `localhost:1521/FREEPDB1`).
2. **Connected?** — database version banner.
3. **Stored graph** — node/edge counts for the current domain, and whether the
   `ckg_code_graph` property graph exists.

If the graph is missing from Oracle, suggest `ckg load .` to store it.
