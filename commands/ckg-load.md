---
description: CKG — load the code graph into Oracle PGQ
argument-hint: [domain]
---

# CKG Load into Oracle PGQ

Store the parsed code graph into Oracle AI Database PGQ so retrieval runs
`GRAPH_TABLE ... MATCH` in the database. Requires `CKG_ORACLE_DSN` in the
environment (e.g. `localhost:1521/FREEPDB1`).

```bash
ckg load . --pkg-root "$(basename "$(pwd)")" {{domain:--domain default}}
```

Verify afterwards:

```bash
ckg oracle-status
```

If Oracle is not configured, show the user how to set `CKG_ORACLE_DSN`,
`CKG_ORACLE_USER`, and `CKG_ORACLE_PASSWORD`.
