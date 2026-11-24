---
description: CKG — check Oracle AI Database connectivity and graph stats
---

# CKG Oracle Status

Check whether Oracle AI Database is reachable, what is stored for the
configured PGQ domain, and which retrieval mode is in effect.

```bash
ckg oracle-status
```

Report:

1. **Configured?** — `CKG_ORACLE_DSN` must be set (e.g. `localhost:1521/FREEPDB1`).
2. **Connected?** — database version banner, or the connection error.
3. **Stored graph** — node/edge counts for the current domain, and whether the
   property graph exists.
4. **Retrieval mode** — `pgq` or `memory`.

If the graph is missing from Oracle, suggest `ckg load .` to store it.
