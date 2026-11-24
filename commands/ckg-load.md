---
description: CKG — load the code graph into Oracle PGQ
argument-hint: "[domain]"
---

# CKG Load into Oracle PGQ

Store the parsed code graph into Oracle AI Database PGQ so neighbourhoods are
matched with `GRAPH_TABLE ... MATCH` in the database. Requires `CKG_ORACLE_DSN`
in the environment (e.g. `localhost:1521/FREEPDB1`). The node and edge tables
are created when missing.

If `$ARGUMENTS` is empty:

```bash
ckg load .
```

Otherwise `$ARGUMENTS` is the domain to store the graph under:

```bash
ckg load . --domain "$ARGUMENTS"
```

Verify afterwards:

```bash
ckg oracle-status
```

If Oracle is not configured, show the user how to set `CKG_ORACLE_DSN`,
`CKG_ORACLE_USER`, and `CKG_ORACLE_PASSWORD`.
