# claude-plus-fa

Add-ons for Claude Code on Windows, so a session and its context are never lost. Public repository; one code base for everyone. The maintainer's own behaviour differs only through `~/.claude/claude-plus.local.json`, which lives outside the repo.

1. Limit fallback: when the usage limit ends a turn, a themed window offers to continue the session on z.ai GLM or on Codex, with set-up steps for whichever is not ready; once the limit resets and GLM or Codex is idle, the same window offers to continue it with Claude again, carrying what Codex did into the same transcript.
2. `glm` command: Claude Code on GLM, sharing `~/.claude`.
3. Status line: model, context, 5-hour and weekly usage bars with reset times.
4. Context guard: past 90 % Claude documents everything and writes `handoff.md`.
5. Compaction: extra summary instructions before, `handoff.md` + `memory.md` read back after.
6. New sessions start from `handoff.md` when the project has one.
7. Prompt recall: the status line's second line shows your last prompt, so you never scroll up to find it.
8. Field Agents (the panel; called Agent Panel before): every session on the PC in a list docked to the right edge; a click resumes it in its own folder; resting the pointer on a card opens it with its last prompt and what runs in its background; right-click hides, closes or deletes one.
9. Reply to passages: select part of Claude's answer, type `: your answer`, press Enter; the selection goes with the prompt as the passage being replied to. Several selections are numbered in the screenshot strip, and `:1 yes :3 no` answers each by number in one prompt.

Install or reinstall: double-click `install.cmd` (or `python install.py`). Remove: `uninstall.cmd`. Both are safe to rerun.

- [`README.md`](./README.md) — the public description: what it does, install, own steps, uninstall, limits.
- [`memory.md`](./memory.md) — what is true now: what each file does, where it installs, settings and state files, gotchas, known limits; the limit fallback, the screenshot features and the background work with the opened card in [`memory/`](./memory/). Read it before any change AND before answering any question about how this works or should work; the code is only the implementation of those rules.
- [`trials.md`](./trials.md) — how it got there: the dated history and every test run, moved word for word from the old memory.md.
- [`LICENSE`](./LICENSE) — MIT.
- Maintainer's knowledge base, outside this repo: `claude-plus-fa.md` (what the package is and the decisions behind it), `claude-code.md` § "Falling back to another provider when the usage limit hits", `desktop-apps.md` (tray techniques).

## Writing for users
- **Running a `.cmd` file means double-clicking it.** Wherever a doc or a reply tells the user to run `install.cmd` or `uninstall.cmd`, say to double-click it in the claude-plus-fa folder. The terminal form (`.\install.cmd`) may follow as the alternative; it never leads.
