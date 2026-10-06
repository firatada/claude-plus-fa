# claude-plus

A small set of add-ons for [Claude Code](https://code.claude.com) on Windows. They fill gaps Claude Code leaves open: you can see how full the context is, nothing learned in a session is lost to auto-compaction, your last prompt stays in view, and a session can continue on another model when your usage limit runs out.

Everything runs through Claude Code's own extension points (hooks and the status line). Claude Code itself is not modified.

Not affiliated with or endorsed by Anthropic.

## What you get

**Status line.** Two lines under the prompt:

```
Opus 5.5 · ctx 58% · 5h 38% 02:40 · week 42% Tue 07:00
› 14:32  the last prompt you sent in this session, cut to the width of the terminal…
```

The first line shows the model, how full the context window is, and your 5-hour and weekly usage with their reset times. Bars are green under 50 %, yellow under 80 %, red above. The 5-hour and weekly figures exist only on a claude.ai subscription. The second line shows your last prompt, so you never scroll up to find what you asked.

**Context guard.** Claude Code compacts the conversation automatically at about 97 % of the context window, and only a summary survives. When the window passes 90 %, claude-plus stops Claude once and has it write everything down first: commit the finished work, update the project's `memory.md` (state, files, commands, decisions and why), and write a `handoff.md` with the exact next step. The compaction summary is also told to keep decisions, abandoned approaches and open files.

**After compaction.** Claude reads `handoff.md` and `memory.md` back before it continues, and tells you in one line which files it read. You also see a line naming those files.

**New session.** In a project with a `handoff.md`, a new session reads it and `memory.md` before the first task, says when the handoff was written, and names what it read. Without a handoff it stays silent. The files are looked up from the session's folder upwards, up to the git root.

**Usage-limit fallback (optional).** When you hit your Claude usage limit, a dialog asks whether to continue on z.ai's GLM model. Yes opens a new window that resumes the same session, with the whole conversation, through the `glm` command; No does nothing. This needs a z.ai API key.

> **Privacy:** choosing Yes sends that session's code and conversation to z.ai, a third party. Think twice on sensitive work. Without a key the dialog never appears and nothing is sent.

## Requirements

- Windows 10 or 11
- [Claude Code](https://code.claude.com) installed and on `PATH`
- Python 3.8 or newer on `PATH`
- For the fallback only: a [z.ai API key](https://z.ai/manage-apikey/apikey-list)

## Install

Download or clone this repository, then run the installer from inside the folder:

```
.\install.cmd
```

It is safe to run again; that is also how you update. It:

- copies the scripts to `~/.claude` and `~/.claude/hooks`, and `glm.cmd` to `~/.local/bin` (added to your user `PATH`);
- registers the hooks in `~/.claude/settings.json`, after saving a backup as `settings.json.before-claude-plus`;
- sets the status line only if you have none (if you keep your own, the context guard stays silent, because it reads the context figure the status line records);
- creates `~/.claudex/profiles/glm/.env` with a placeholder and opens it in Notepad, so you can paste a z.ai key. Skip it if you do not want the fallback.

Restart any open Claude Code session afterwards: hooks load when a session starts.

## Use your own steps

The context guard and the after-compaction message carry built-in steps. If you keep your own procedure, for example in your `CLAUDE.md`, create `~/.claude/claude-plus.local.json`:

```json
{
  "rule": "the global CLAUDE.md, section \"Saving work before compaction\""
}
```

The messages then tell Claude to run the steps in that place instead. The installer never writes this file, so updates keep it.

## Uninstall

1. Remove the claude-plus entries from `~/.claude/settings.json` (the hooks whose command names `glm-fallback.py`, `context-guard.py` or `prompt-recall.py`, and the `statusLine` naming `statusline.py`), or restore `settings.json.before-claude-plus` if nothing else changed since.
2. Delete `~/.claude/statusline.py`, those three scripts in `~/.claude/hooks`, `~/.local/bin/glm.cmd`, and optionally `~/.claude/state/context` and `~/.claude/claude-plus.local.json`.

## Limits

- Windows only (the dialog and the launcher use Windows APIs and `cmd`).
- A short rate-limit error that ends a turn can also bring up the fallback dialog.
- After the fallback opens a new window, the old one stays open; close it yourself.

## Files

| File | Role |
|---|---|
| `statusline.py` | Status line; also records the context figure per session for the context guard |
| `prompt-recall.py` | `UserPromptSubmit` hook: keeps the last prompt for the status line, prints nothing |
| `context-guard.py` | `Stop`, `PreCompact` and `SessionStart` hooks: the 90 % warning, compaction notes, read-back |
| `glm-fallback.py` | `StopFailure` hook on `rate_limit`: the Yes/No dialog |
| `glm.cmd` | Runs Claude Code against z.ai's Anthropic-compatible endpoint, sharing `~/.claude` |
| `install.cmd`, `install.py` | The installer |

## License

[MIT](./LICENSE)
