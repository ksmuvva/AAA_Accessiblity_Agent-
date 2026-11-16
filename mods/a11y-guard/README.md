# a11y-guard

A Claude Code mod (a hooks plugin) that enforces three rules from `CLAUDE.md` on every tool call.

1. **No top-level `agents` module.** Writing `agents.py` or `agents/` at the repository root is refused, because it shadows the OpenAI Agents SDK. Shell commands are checked best-effort (`mkdir`, `touch`, `mv`, `cp`, `tee`, redirects).
2. **Catalogue sync reminder.** After an edit to `agent_access/rules/*.py`, the status line shows "catalogue sync due" and Claude is reminded once, until `python -m agent_access.catalogue.sync` runs without error.
3. **Confirm paid or traced runs.** `python -m evals.run_eval` (a real model, costs money) and an audit run with `--trace` (sends page content and screenshots to OpenAI) wait for your answer; anything but "Run it" refuses the command.

Load it for a session: `claude --plugin-dir mods/a11y-guard`.

Check it: `claude plugin validate mods/a11y-guard` and `claude plugin test mods/a11y-guard`.
