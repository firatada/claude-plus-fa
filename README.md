# claude-plus

Add-ons for [Claude Code](https://code.claude.com) on Windows.

- Built on Claude Code's own hooks and status line. Claude Code itself is not modified.
- Not affiliated with or endorsed by Anthropic.

## What you get

![The claude-plus status line: model, context, 5-hour and weekly usage, and the last prompt](docs/status-line-v2.png)

**Status line**
- Model, context used, 5-hour and weekly usage, with reset times.
- Green under 50 %, yellow under 80 %, red above.

**Last prompt**
- You will be able to see your last prompt here, on the second line.
- No more scrolling up to find what you asked.

**Context Tracker**
- The status line lets you watch the context; claude-plus also tracks it for you, all the time.

**Auto Save Before /compact, at 90 %**
- **Automatically**, at 90 % context, before Claude Code's own auto-compaction (about 97 %), Claude:
  - commits the finished work;
  - updates `memory.md` (state, decisions and why);
  - writes `handoff.md` (the exact next step).
- Nothing learned in the session is lost to compaction.

**After compaction**
- **Automatically**, Claude reads `handoff.md` and `memory.md` back.
- The same happens when a new session opens in a project with a `handoff.md`.
- It tells you which files it read.

**Auto Switch to Free Models When Claude Tokens End**
- When your Claude usage limit runs out, a dialog offers to continue on a **free** model: GLM by [z.ai](https://z.ai).
- Yes resumes the same session, with the whole conversation, in a new window.
- **The models used (GLM-4.7-Flash, GLM-4.5-Flash) are free on z.ai.**
- Needs a free z.ai API key. Without one, no dialog.
- **Yes sends the session's code and conversation to z.ai**, a third party.

**Get your free z.ai API key**
1. Sign up at [z.ai](https://z.ai).
2. Open [API Keys](https://z.ai/manage-apikey/apikey-list) and create a new key.
3. Copy the key.
4. Run `install.cmd`. Notepad opens `~/.claudex/profiles/glm/.env`; replace `PASTE_YOUR_KEY_HERE` with the key and save.

## Requirements

- Windows 10 or 11
- [Claude Code](https://code.claude.com) and Python 3.8+ on `PATH`
- For the free-model switch: a free [z.ai API key](https://z.ai/manage-apikey/apikey-list)

## Install

```
git clone https://github.com/firatada-ctrl/claude-plus
cd claude-plus
.\install.cmd
```

- No git? Download the ZIP (Code, Download ZIP), unpack, double-click `install.cmd`.
- Run it again to update.
- Restart open Claude Code sessions afterwards.

The installer:
- copies the scripts into `~/.claude` and `glm.cmd` into `~/.local/bin` (added to `PATH`);
- registers the hooks in `~/.claude/settings.json` (backup: `settings.json.before-claude-plus`);
- sets the status line only if you have none;
- creates `~/.claudex/profiles/glm/.env` for an optional z.ai key.

## Use your own steps

- Keep your own procedure, for example in your `CLAUDE.md`? Point to it in `~/.claude/claude-plus.local.json`:

```json
{ "rule": "the global CLAUDE.md, section \"Saving work before compaction\"" }
```

- The installer never touches this file.

## Uninstall

- Remove the claude-plus hooks and `statusLine` from `~/.claude/settings.json`, or restore the backup.
- Delete `~/.claude/statusline.py`, the three claude-plus scripts in `~/.claude/hooks` and `~/.local/bin/glm.cmd`.

## Limits

- Windows only.
- A short rate-limit error can also open the free-model switch dialog.
- After the switch, close the old window yourself.

## Files

| File | Role |
|---|---|
| `statusline.py` | Status line |
| `prompt-recall.py` | Keeps the last prompt |
| `context-guard.py` | 90 % warning, compaction notes, read-back |
| `glm-fallback.py` | Usage-limit dialog |
| `glm.cmd` | Claude Code on z.ai GLM |
| `install.cmd`, `install.py` | Installer |

## License

[MIT](./LICENSE)
