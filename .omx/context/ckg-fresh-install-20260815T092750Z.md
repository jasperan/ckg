# Autopilot context snapshot: ckg fresh-install validation

- task: Test ckg installation fresh (clean environment) and fix any issues found
- desired outcome: every documented install path works from scratch:
  (a) GitHub clone + uv sync + CLI works
  (b) install.sh one-command installer registers pi/claude/codex without errors
  (c) pi install (local path) loads extension, tools work, Oracle PGQ path works
  (d) npm package: npm pack → install in clean dir → postinstall venv → extension discovers CLI
  (e) Claude Code plugin validates + hook produces context
- known facts: repo at https://github.com/jasperan/ckg (main); pi 0.84.1; claude 2.1.220;
  Oracle AI DB Free at localhost:1601/FREEPDB1 (dmuser/continual_learning); uv 0.11.15
- constraints: never touch the dev checkout /home/ubuntu/personal/ckg working tree except fixes
  in /tmp test copies; use fresh $HOME-scoped env for pi/claude where possible
- unknowns: whether pi install local-path works with jiti; whether postinstall runs under npm
  install from tarball; whether claude plugin marketplace add accepts local path
- touchpoints: install.sh, package.json, pi/extensions/ckg/*, scripts/postinstall.js,
  .claude-plugin/*, hooks/before_prompt.py
