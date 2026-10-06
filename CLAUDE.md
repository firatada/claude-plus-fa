# claude-plus

Add-ons for Claude Code on Windows, so a session and its context are never lost. Public repository; one code base for everyone. The maintainer's own behaviour differs only through `~/.claude/claude-plus.local.json`, which lives outside the repo.

1. Limit fallback: when the usage limit ends a turn, a Yes/No dialog continues the same session on z.ai GLM (only when a key is set).
2. `glm` command: Claude Code on GLM, sharing `~/.claude`.
3. Status line: model, context, 5-hour and weekly usage bars with reset times.
4. Context guard: past 90 % Claude documents everything and writes `handoff.md`.
5. Compaction: extra summary instructions before, `handoff.md` + `memory.md` read back after.
6. New sessions start from `handoff.md` when the project has one.
7. Prompt recall: the status line's second line shows your last prompt, so you never scroll up to find it.

Install or reinstall: double-click `install.cmd` (or `python install.py`). Remove: `uninstall.cmd`. Both are safe to rerun.

- [`README.md`](./README.md) — the public description: what it does, install, own steps, uninstall, limits.
- [`memory.md`](./memory.md) — what each file does, where it installs, how it was tested, known limits. Read it before any change AND before answering any question about how this works or should work; the code is only the implementation of those rules.
- [`LICENSE`](./LICENSE) — MIT.
- Design lessons (maintainer's knowledge base, outside this repo): `claude-code.md` § "Falling back to another provider when the usage limit hits".

## Writing for users
- **Running a `.cmd` file means double-clicking it.** Wherever a doc or a reply tells the user to run `install.cmd` or `uninstall.cmd`, say to double-click it in the claude-plus folder. The terminal form (`.\install.cmd`) may follow as the alternative; it never leads.
