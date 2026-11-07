# Handoff — claude-plus-fa

Written 2026-11-07 on a context warning. Current state, files and tests are in [`memory.md`](./memory.md); this file holds only the timeline and what was abandoned.

## What the project is
Add-ons for Claude Code on Windows, public as `firatada-ctrl/claude-plus` (MIT): status line, 90 % auto save, compaction read-back, free GLM switch, and since 2026-11-07 the Agent Panel, a list of every session on the PC docked to the right edge of the screen.

The project has no mindmap page; its decisions sit under "## Decisions" in memory.md. Proposing a page is the next session's first package.

## Current focus
Agent Panel revisions, driven by Fırat one at a time. Done and committed locally (latest ca668e0): Spark look, raise an open session's window, restore instead of maximise, right-click Hide / Close / Delete, the new name. Nothing pushed.

## Decision timeline
1. Panel built in Tk with an AppBar dock, tray icon, Ctrl+Alt+C, autostart from the Run key.
2. Found during tests: a panel started from a Claude session passed CLAUDE_CODE_CHILD_SESSION to the sessions it opened, so they stopped saving transcripts. Fixed by stripping CLAUDE*/ANTHROPIC* variables not set in Windows itself.
3. Found during tests: `mindmap/tools/branch-index.py` crashed on Turkish characters through a pipe, so the SessionStart branch list may have been missing for a while. Fixed with UTF-8 stdout.
4. Spark redesign (option A, Tk canvases); a working session is blue, since Spark keeps gold for rewards.
5. Raise an open session: first maximised, then changed to restore at Fırat's correction.
6. Renamed to Agent Panel; right-click menu added; Delete moves to the Recycle Bin after two confirmations.

## Abandoned approaches
- Web view (pywebview / WebView2) for the panel: too heavy for an always-open window.
- Maximising the window on click: Fırat wants restore.
- Testing Delete on real sessions: the global rule forbids deleting transcripts, so a throwaway USERPROFILE with fake sessions was used instead.

## Rules to propose
- Test any feature that deletes user data against a throwaway profile or copy, never the real data, and say so in the report.

## Next step
Wait for Fırat's next revision. Then, with his word: rename the GitHub repo, push, and update the claude-plus card on firatada.com (the site's UI Terminology page, commit c3afdd3, is also not deployed yet).
