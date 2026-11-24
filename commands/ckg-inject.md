---
description: CKG — inject a structure map for the current task
argument-hint: "<task summary>"
---

# CKG Inject

Generate the structure map (anchor files + dependency reach) for the task,
ready to append to context.

```bash
ckg inject --refresh sync -- "$ARGUMENTS"
```

Read the output carefully:

- **Anchor Files** — lexically relevant to the task; start exploration here.
- **Dependency Reach** — files connected via imports, calls, or co-edits;
  changing an anchor often requires changing these too.

Exit code 1 with "no context map available" means the task summary names
nothing in the code: retry with identifiers from the task. The map is a
starting point, not a constraint.
