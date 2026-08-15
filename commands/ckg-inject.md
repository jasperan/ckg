---
description: CKG — inject a structure map for the current task
argument-hint: "<task summary>"
---

# CKG Inject

Generate the full structure map (anchor files + dependency reach) for the
current task, ready to append to context.

```bash
ckg inject "{{task_summary}}"
```

Read the output carefully:

- **Anchor Files** — lexically relevant to the task; start exploration here.
- **Dependency Reach** — files connected via imports, calls, or co-edits;
  changing an anchor often requires changing these too.

The map is a starting point, not a constraint.
