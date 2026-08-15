# RALPLAN artifact: ckg fresh-install test spec

Approach: test every documented install path in a clean /tmp sandbox against the
*published* repo (github.com/jasperan/ckg), fix failures in the dev checkout, re-test.

## Test matrix
- A. Fresh clone → uv sync → `ckg build/query/oracle-status` works (CLI path)
- B. install.sh (CKG_SKIP_SKILLS=0) in sandboxed HOME → pi + claude + codex registration
- C. pi: `pi install git:github.com/jasperan/ckg` (sandboxed HOME) → extension loads,
     ckg_status works, Oracle PGQ retrieval works with CKG_ORACLE_DSN
- D. npm: `npm pack` → `npm install <tarball>` in clean dir → postinstall builds venv →
     extension discovers bundled CLI
- E. claude plugin validate + hook simulation against fresh clone

## Exit criteria
All tests pass; any fixes committed & pushed; README stays truthful.
