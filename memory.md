# claude-plus-fa: project memory

What is true now, nothing else. How it got here (test runs, measurements, fixes, the maintainer's requests word for word) is in [`trials.md`](./trials.md), each block headed with its line in the old file. Why each choice was made: the maintainer's mindmap page `claude-plus-fa.md`, Decisions. `~` is the user's home; paths without one are relative to the repo.

## What it is
- Claude Code add-ons for Windows: limit fallback to GLM or Codex and back, `glm` command, status line with prompt recall, context guard, compaction read-back, start from `handoff.md`, Field Agents (the panel; session list, alert, tray, screenshots, reply to passages, the reply window, paste key, links to the front).
- The panel's user-visible name is **Field Agents** (Agent Panel until 2026-11-22); internal names keep `agent-panel` (files, state and mascot folders, mutex and events, Run value).
- Public repo `firatada/claude-plus-fa` (MIT), one code base for everyone; the maintainer's own behaviour differs only through `~/.claude/claude-plus.local.json`, which the installer never writes.
- Windows only (WebView2, cmd). The move to Codex has run on a real usage-limit hit (2026-11-21); the move to GLM and the way back only in tests and harnesses (see `trials.md`).

## Repo and publishing
- `main` is the public branch, tracking `origin` = https://github.com/firatada/claude-plus-fa (old URLs of `firatada-ctrl/claude-plus` redirect). Work on `main`.
- `history` is a local-only branch with the full development history before the release (personal paths and session links in old commits): never pushed.
- Every push needs the maintainer's approval. Before one, scan what goes out for keys, and run `git log main -p --format='%an %ae %cn %ce%n%B' | grep -icEf .git/info/private-names.txt`, which must print 0.
- `.git/info/private-names.txt` holds the private names (client projects, private paths and addresses, one extended regex per line); it lives in `.git/info/` because a tracked file would publish them. A hit is removed from history with `git filter-repo --refs main --replace-text`, never only by a new commit.
- `.gitignore`: `.env`, `__pycache__/`, `handoff.md`. `archive/` is excluded locally in `.git/info/exclude`.
- The claude-plus-fa card on firatada.com is live.
- `docs/img/*.png` are the README's pictures, taken at 2x from the real pages fed made-up sessions (never real session names); the alert and the way-out dialog from their pages in Playwright with a stub `pywebview` (`trials.md`, 2026-11-23).

## Install, uninstall, run
- Install or reinstall: double-click `install.cmd` (or `python install.py`); safe to rerun. Remove: double-click `uninstall.cmd` (or `python uninstall.py`). Never run uninstall on the maintainer's own profile as a test.
- `install.py`: copies the files, writes the Desktop icon, adds `~/.local/bin` to the user PATH if missing, registers the hooks in `~/.claude/settings.json` (backup `settings.json.before-claude-plus`), turns on Windows Terminal's `copyOnSelect` once per settings file, sets `statusLine` only if none exists or it is already ours, creates the GLM key file with a placeholder if missing and prints a one-line optional note; it never opens Notepad.
- The installers stop the panel through the named event `Local\claude-plus-fa-agent-panel-quit` before forcing it, also a copy under its old name (`session-panel.pyw`, event `Local\claude-plus-fa-session-panel-quit`), and delete the old file, sign-in entry and state folder. They also stop a leftover paste process.
- `uninstall.py`: removes every hook whose command names one of `SCRIPTS` (`glm-fallback.py`, `context-guard.py`, `prompt-recall.py`, `quote-reply.py`), keeps other hooks in the same event, drops events and `hooks` left empty, removes `statusLine` only when it names `statusline.py`, deletes the installed files and the state folders `context`, `agent-panel` and `glm-return` under `~/.claude/state`, runs `copy_on_select.restore()`, removes the Desktop icon (`remove_shortcut()`). Backup `settings.json.before-claude-plus-uninstall`, only when something changes.
- Uninstall keeps on purpose: the GLM key file, `claude-plus.local.json`, the install backup, `~/.local/bin` on PATH, `~/.claude/state/copy-on-select.json`.
- Hooks load at session start: open sessions need a restart after install. `statusLine` does not: open sessions pick it up live.
- The running panel uses the installed copy in `~/.claude`, not the repo; a code change reaches it only through `install.py` (which restarts it).
- `install.py` installs `pywebview`, `pystray` and `pillow` with `pip install --user` into the Python that runs it. `glm.cmd` does not need claudex.

## Files and where they install
| Source | Installed to | Job |
|---|---|---|
| `glm.cmd` | `~/.local/bin` | Claude Code on z.ai (`ANTHROPIC_BASE_URL` `https://api.z.ai/api/anthropic`, model `glm-4.7-flash`, small model `glm-4.5-flash`, key from the key file); shares `~/.claude`, so `glm --resume` sees every session |
| `glm-fallback.py` | `~/.claude/hooks` | `StopFailure` hook (matcher `rate_limit`, timeout 3600): the way-out dialog; also `--carry` hook, `--ask-return` dialog, previews, and helpers the panel loads |
| `statusline.py` | `~/.claude` | `statusLine` command; writes context state and `rate_limits.json` |
| `prompt-recall.py` | `~/.claude/hooks` | `UserPromptSubmit`: writes the last prompt for the status line |
| `quote-reply.py` | `~/.claude/hooks` | `UserPromptSubmit`, timeout 10: reply to passages |
| `context-guard.py` | `~/.claude/hooks` | `stop`, `precompact`, `resumed`, `started` modes |
| `agent-panel.pyw` | `~/.claude` | the panel (Field Agents); started by `pythonw.exe` from `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, value `ClaudePlusFA agent panel` |
| `agent-panel.html` | `~/.claude` | the panel page: look and input handling |
| `agent-alert.html` | `~/.claude` | the alert window's page |
| `agent-panel-shots.py` | `~/.claude` | the screenshot keeper and the resident OCR worker, loaded by the panel |
| `agent-shots.html` | `~/.claude` | the screenshot strip and its preview (one page, two windows) |
| `agent-column.html` | `~/.claude` | an opened card's column of running work, right dock |
| `agent-reply.py` | `~/.claude` | the reply window's rules (replies from a transcript, drafts, the line sent, dictation's server calls), loaded by the panel |
| `agent-reply.html` | `~/.claude` | the reply window (session tabs, replies, answers) left of the strip |
| `agent-panel-paste.py` | `~/.claude` | the paste key and Send to Session's typing, a process of its own |
| `mascot/` | `~/.claude/agent-panel-mascot` | hero strips, `agent-panel.ico`, `logo.png`, `knock.wav`; replaced whole on install |
| `install.py`, `uninstall.py`, `copy_on_select.py` | repo only | installers; `copy_on_select.py` is imported by both from their own folder |
| `tools/` | repo only | `hero-export.py` (+ `mascot-source/hero/`; cuts every mascot asset), `knock-sound.py`, the unit tests |

## Settings and state files
- `~/.claude/claude-plus.local.json` (outside the repo; read-modify-write by `save_local()`, other keys kept, a file that is not valid JSON left alone): `rule` (where the context guard's steps live), `screenshots` (keeper, default on, read every step), `shot_strip` (strip switch, read at start), `shot_strip_at` (`[right, top]` screen pixels, read at start), `shot_pins` (list of capture times in pin order), `paste_keys` (default on, read every second), `reply_open` (reply window open or folded, default open), `dictation_url`, `dictation_server`, `dictation_app`, `dictation_language`, `dictation_terms` (dictation in the answer boxes: [`memory/reply.md`](./memory/reply.md); `dictation_terms` is written by the panel the first time it is missing), `headless_hours` (default `HEADLESS_HOURS` 24, read every push).
- GLM key file: `~/.claudex/profiles/glm/.env`, line `CLAUDEX_KEY="..."`; never in this repo. The path is claudex's, so `claudex validate glm` works on it.
- `~/.claude/state/context/`: `<session_id>.json` (`{"used_percentage": n}`), `<session_id>.warned`, `<session_id>.prompt.json` (`{"prompt", "at"}`), `rate_limits.json` (the `rate_limits` object plus `at`, written via `.tmp` and swap), `quote-list.json` (the panel's passage lists), `quote-used/<session_id>.json` (`{gen, upto, at}`), `quote-used.json` (fallback `{seq, at}`).
- `~/.claude/state/agent-panel/`: `cache.json` (parsed sessions by mtime and size), `hidden.json`, `options.json` (`dock`, Alerts, Alert Sound), `update.json` (last failed update handled), `screenshots.json` (keeper index, last `KEEP` 300, pinned never dropped), `paste-windows.json`, `paste.log` (cut at 64 KB), `reply-drafts.json` (the reply window's answers per session), `reply-pins.json` (its pinned replies per session, 8 at most, with their text), `links.log` (cut to 32 KB past 64 KB), `reply-send.log` (each Send to Session's steps, no text; cut the same way).
- `~/.claude/state/glm-return/`: `<sid>.json` (way-back record), `<sid>.answer` (dialog answer), `<sid>.<codex thread>.carried` (how far Codex's turns were carried; swept after `CLAIM_KEEP` 7 days once no record names the session) with its short-lived `.lock`, `<sid>.watched` (the hook's first-turn watch is over), `<sid>.quiet` (no way-out window for that session until its `until`; written by the hook once its window is answered, and by the panel's early return from Codex).
- `~/.claude/state/copy-on-select.json`: per Windows Terminal settings file `{path, before, changed, at}`.
- Paste temp pictures: `%TEMP%\claude-plus-fa-paste\paste-YYYYMMDD-HHMMSS-mmm.png`, deleted after 24 h.

## Status line and prompt recall
- Line 1: model · `ctx` bar · `5h` bar + reset time · `week` bar + reset day/time. Bars green < 50 %, yellow < 80 %, red above; halves round up, as in the panel.
- Input fields used: `model.display_name`, `context_window.used_percentage` (null before the first reply), `rate_limits.five_hour|seven_day.used_percentage` and `.resets_at` (epoch seconds). `rate_limits` comes only with a claude.ai subscription, so GLM sessions show `ctx` alone.
- Each refresh writes `~/.claude/state/context/<session_id>.json`; the figure lags by one message.
- `prompt-recall.py` writes the prompt file and prints nothing (a UserPromptSubmit hook's stdout goes into Claude's context); it skips prompts starting with `<task-notification>` or `<system-reminder>` (Claude Code delivers finished background tasks through this hook).
- Line 2: `› HH:MM  <prompt>` in full, whitespace collapsed, wrapped to the console width read from `CONOUT$` (120 if that fails), at most `PROMPT_LINES` 12 lines, the last ending in `…` when cut. The continuation indent starts with a colour code, since Claude Code trims every status line.

## Context guard
- `stop` (Stop hook): at ≥ 90 % context, once per climb, exit 2 with the documentation steps; re-arms below 50 %. Reads the status line's state file, so without our status line it stays silent. Silent when `stop_hook_active` or state is missing.
- `precompact` (PreCompact): stdout becomes extra compaction instructions.
- `resumed` (SessionStart matcher `compact`): tells Claude to read `handoff.md` and `memory.md` when present, then run the resume steps.
- `started` (SessionStart matcher `startup|clear`): if there is a `handoff.md`, tells Claude its date and to read it and `memory.md` first; silent otherwise. `--resume` needs nothing.
- `resumed` and `started` answer in JSON: `systemMessage` tells the user which files will be read (or that no handoff.md was found after compaction), `additionalContext` tells Claude to read them and name them in one line. The installer keeps them as separate SessionStart entries.
- `handoff.md` is looked up from the hook's `cwd` upwards, stopping at the git root.
- Steps: built-in `WARN_STEPS` and `RESUME_STEPS`; `{"rule": "<where your steps live>"}` in `claude-plus.local.json` replaces them with "run the ... steps in <rule>" (missing, unreadable or non-object: built-in). The maintainer's copy points at the global CLAUDE.md, section "Never let unwritten knowledge accumulate".

## Limit fallback
- The way-out dialog, the move to GLM or Codex (transfer, cut, first-turn watch, previews) and the way back to Claude (record, steps, cards, carry): [`memory/limit-fallback.md`](./memory/limit-fallback.md).

## Field Agents: sessions
- Sessions are the `<uuid>.jsonl` files directly under `~/.claude/projects/<folder>/` (subagent and set-aside transcripts excluded), plus the same inside each WSL distro already running (`wsl -l --running`; the panel never starts one).
- Title: the last `custom-title`, else the last `ai-title`, else the first real prompt.
- The `cwd` used is the one whose slug (every non-alphanumeric character as `-`) equals the folder name, since a moved session lists several.
- Open = `~/.claude/sessions/<pid>.json` names the session and the pid is alive (`running()`); fields used: `sessionId`, `status` (busy/idle/shell/waiting), `name`, `waitingFor`, `statusUpdatedAt`. Any non-empty status counts as open.
- Asking = `waitingFor` present (written while `status` is `busy` or `waiting`), or `waiting` without it.
- Live pass every `LIVE_EVERY` 2 s (session files, transcript times of open sessions, re-sort; an unknown session triggers a full scan); full scan every 15 s, about 0.05 s with the cache.
- Hidden sessions, sessions whose folder is gone and headless runs (`entrypoint` `sdk-cli`) stay out of the main list and the count; the link *Hidden, Deleted and Headless (n)* adds that section after the list and scrolls to it. A headless run not running whose transcript is older than `HEADLESS_HOURS` shows nowhere (`shown()` in `state()`; `self.sessions` keeps it for every other feature; nothing on disk touched). Cards there are not greyed; their flags say `hidden`, `folder deleted` or `headless`; a headless card's menu has no Hide.
- Every child process gets a clean environment (`clean_env()`), so sessions it opens never inherit `CLAUDE_CODE_CHILD_SESSION`.

## Field Agents: window and instance
- An ordinary window over the right edge of the work area, 280 logical px wide, or a 46 px strip over the top (Top dock); never topmost, reserves no space. It registers and removes an AppBar once at start only to clear a strip left by an older copy.
- Python pushes state with `app.update(<json>)` only when it changed; the page calls `window.pywebview.api.<ready, click, open, set_hidden, close, delete, hide, ...>`, each returning the line shown at the bottom.
- One instance: mutex `Local\claude-plus-fa-agent-panel`. A second start (desktop icon, Run key) calls `AllowSetForegroundWindow(ASFW_ANY)`, sets the auto-reset event `Local\claude-plus-fa-agent-panel-show` and exits; the main loop polls it and the quit event every 0.2 s and calls `show()`.
- `Ctrl+Alt+C` or the tray: raises the panel when covered, hides it when in front, shows it when hidden.
- Tray left click (`tray_click()`, event `tray:<time>:<window in front>` from the `Icon` subclass's `__call__`): the toggle, then, while the panel shows, the session terminal last in front comes back (`bring_front()`, restored if minimised, no key for tabs) with the panel `lift()`ed under it; a click on its card then raises it (never minimises: see the second click below). The poll loop waits on the queue, so an event is taken at once. `tray_stale()` drops a click the user moved on from: `self.used` (a card's `click()`/`open()`, any panel window to the front) is later, or a panel window is in front now and none was at the click. The menu's *Show or Hide* and `Ctrl+Alt+C` stay the plain toggle.
- Last terminal: `self.terminals`, session windows newest in front first (`note_terminal()`, 8), noted by `follow_front()` as each comes forward and by `follow_terminal()` each poll turn (one found later); `last_terminal()` takes the newest that still exists and shows an open session, else none.
- `quit()` destroys every pywebview window (a hidden one left keeps the process alive).
- Start-up takes no foreground: `quiet_forms()`, called before `webview.start()`, replaces pywebview's `BrowserView.BrowserForm` with a subclass that never activates (how and why: its docstring); every `create_window` passes `min_size=(0, 0)`; `main()` shows the panel with `SW_SHOWNOACTIVATE`. A pywebview without these parts is left as is (the windows then activate again).
- `wake()` shows the strip and preview once off screen with `ShowWindow(SW_SHOWNOACTIVATE)` and hides them; from then on Win32 alone shows and hides them (`SWP_SHOWWINDOW | SWP_NOACTIVATE`). The alert is shown the same way.

## Field Agents: look
- Spark theme (`mindmap/design_themes/spark_theme`) as CSS in `agent-panel.html`; fonts Segoe UI; no dark theme. Gold only for rewards.
- States: working = turning green arc (10 px in the dot's 8 px slot, 1 s a turn), still green tint and 1.5 px ring, and the turn timer in the age's place (`m:ss`, `h:mm:ss`, 600 weight); open and waiting = still hollow ring in `--violet` #5c3bcd; closed = grey dot; asking = red dot and a red pulse (1.2 s), the only thing that pulses. An open session with background work running (`delegated()`, the conductor's test) takes the green tint and ring too, its dot staying its own; asking wins (card class from `groupOf()`).
- Title, age, meta and ctx bar look the same on every card; nothing on an open card looks disabled.
- Reduced motion: arc still, timer counting, asking card a still red tint and ring, mascots on frame 0, no glides.
- Timer: `ticking()` each second patches the age text, the dot's `aria-label` ("Working, 0:42"), the card's `aria-label` (not while the card has the keyboard) and the opened card's times; the ticking text carries no `data-k`.
- Turn start (`turn` in the state, epoch ms, `turn_start()`): `statusUpdatedAt` from the session file; a question inside the turn keeps the start; a new stamp on a session seen working means a new turn; without a stamp, the first read that saw it working.
- Card rows: title (age on the title row); then the `ctx` bar + percent (left out when unknown) and on the right the flags (`hidden` / `headless` / `folder deleted` / `on GLM` / `on Codex`) and age joined by ` · `; title and flags end in `…` when they do not fit (full in the card's `aria-label`). Cards are 74 px high (a 56 px hero).
- `where_of()`: a WSL-distro session gets `\\wsl.localhost\<distro>` in front, `//` paths become backslashes, a trailing separator is dropped (a drive root keeps its own); the one form for the card's label, the alert and the strip.
- Cards are kept and patched by id, never redrawn wholesale (a redrawn card drops hover and its opened part).
- Tooltips (`.tip`): navy at 88 %, 11 px, 400 ms after real pointer movement, gone after 3 s, on leave, click or scroll; never on keyboard focus; every button has one (no `title`). Nothing inside a card has one.
- Bottom strip: `5h` and `week` bars from `rate_limits.json`; a figure past its `resets_at` says *no figure yet*.
- Messages: about one card under that card (an error stays until the next click or key, a success leaves after 5 s); about the whole panel above the bottom link; a failed scan stays there in amber until a scan succeeds.
- Search: ×, Esc, matches title and full path, marks hits, count shows `matches/total`. A skeleton shows until the first scan (`scanned`).
- Keyboard: cards are buttons (Enter or Space opens, ignoring key repeat; Shift+F10 or the menu key opens the menu, arrows walk it).

## Field Agents: order
- Groups by state: asking, working (an open session with background work too), open, closed; newest transcript first within each. Done in the page (`byState()`, a stable sort on `RANK` over `groupOf()`), so both docks follow; the `.pyw` sends newest first; the set-aside section is not regrouped.
- A card that changes place glides 200 ms (FLIP); a new card fades in from 4 px above.
- The order holds while the pointer is over the list, a menu or confirm is open, or the keyboard is on a card while `document.hasFocus()`; it applies the moment that ends (`letGo()`). The window's `blur` clears the hover hold.

## Field Agents: menu and clicks
- Right-click menu (HTML inside the panel, opened at the pointer and kept inside it): Back to Claude (when a record exists), Hide / Unhide, Close (open sessions only), Delete (red, pale red hover). Closes on Escape, a click outside (which does nothing else), scroll and focus loss.
- Close and Delete confirm in two steps: the menu turns into a small card with the question, one sentence, Cancel (focused) and *Yes, Close* / *Yes, Delete*, placed under the card (above it near the bottom).
- Close: `WM_CLOSE` to the session's window when no other open session shares it, otherwise ends the process; the transcript stays.
- Delete: an open session is closed first (waits up to 10 s, `close_delete`); then the transcript, its `<id>/` folder and `file-history/<id>/` go to the Recycle Bin (`SHFileOperationW`, `FOF_ALLOWUNDO`). Project files and auto memory are never touched. On a session stored inside WSL it is greyed with the reason (no Recycle Bin on a network path).
- A closed session opens only on a double click (a single click says "Double-click to open it." after 450 ms). `open()` takes a lock, re-reads `running()`, and starts nothing for a session it launched less than `LAUNCH_WAIT` 8 s ago (until `~/.claude/sessions` shows it).
- Open command: `claude --resume <id> --dangerously-skip-permissions` (with `--model` when needed) in a new console in the session's folder: `cmd /k`, PowerShell for a UNC folder, `wsl.exe --cd` for a session inside a WSL distro.
- A single click on an open session raises its window (`focus_session()` → `bring_front()`: restored only if minimised, a lone Alt when refused, then TOPMOST and NOTOPMOST); then `lift()` puts the panel right under it. `click()` never launches.
- Tab search: Ctrl+Tab (up to 20 times, only while that window stays in front) only when the raised window's title shows another listed session's name (`shown_name()`, `other_names()`); otherwise no key is sent.
- A second click minimises (`SW_MINIMIZE`) only the window the card's own first click raised (`put_away()`: `self.raised` = (session, window), dropped by `follow_front()` when any other non-panel window comes forward) and only while `shown_in_front()`: not minimised, `self.front` (the last non-panel foreground), `top_window()`; in a shared window (`shared()`) its title shows this session's name. A window brought forward otherwise (tray, taskbar, Alt+Tab, Windows after a minimise) is raised by the first click.
- The page ignores the second click of a double click (`e.detail > 1`).

## Field Agents: session windows and z-order
- Order: session window > panel (with its shadow, screenshot strip and reply window) > every other window. A session window minimised leaves the panel on screen: only the terminal goes to the taskbar.
- `follow_front()`: `SetWinEventHook(EVENT_SYSTEM_FOREGROUND)` on its own thread; when the new foreground is in `self.wins` (pid → window, from `find_windows()` on every live pass), `lift(hwnd)` = `SetWindowPos(panel, after that window, SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE)`; skipped while that window is `WS_EX_TOPMOST`, while hidden or dragged.
- Own windows are filtered in the callback (`own()`), not with `WINEVENT_SKIPOWNPROCESS`.
- `stay_over()` looks again after `LIFT_AGAIN` (0.1, 0.2, 0.4, 0.8 s apart) while that window stays in front and re-lifts when the panel is not `beneath()` it.
- While a session window is in front a second hook follows `EVENT_OBJECT_LOCATIONCHANGE` for its process only (maximise, restore, drag), removed when anything else comes to the front.
- A session window already in front when `find_windows()` first finds it gets the panel under it at once.
- Minimise (`on_min()` on `EVENT_SYSTEM_MINIMIZESTART`): for a session window the panel is `beneath()`, `stay_on_screen()` waits up to `MIN_FRONT_WAIT` for the next foreground (Windows raises it over the panel) and `surface()`s the panel over it (TOPMOST then NOTOPMOST, not activated; HWND_TOP is refused to a process not in front); not when the panel or a session window comes forward.
- The shadow and the strip are owned windows of the panel, so Windows keeps them directly above it; they are never moved in z-order on their own.

## Field Agents: top dock
- `options.json` `dock` = `right` (default) or `top`, set by dragging the header or the tray's *Dock on the Right* / *Dock at the Top*; injected as `window.DOCK`.
- On top the window spans the work area's width, `STRIP` 46 + `DROP_ROOM` 440 logical px tall, clipped by `SetWindowRgn` to the strip plus whatever is open under it (`syncRegion` in the page reports every `.pop`, `.tip`, `.xbox`, `.say`, non-empty `#note` and open `#drop`; `region()` unions them as rounded rects). Clicks outside the region reach the windows below. The region is set before the window grows.
- `syncRegion` waits for `pywebviewready` (`bridged`).
- On top DWM's frame is off (`frame()`: `DWMWA_NCRENDERING_POLICY` disabled); on the right it stays on (pywebview `shadow=True`).
- Strip, left to right: grip, hero, count, 210 px search, tabs of 170 x 34 px (`body.top #list .card`: hero 28 px, title cut, 20 px ctx bar and percent; no age, flags or timer), the link *Hidden, Deleted and Headless (n) ▾* opening a 300 px dropdown (`#drop`), Status, the `5h` / `Week` bars (hidden under 1400 px wide, where the search shrinks to 120 px), the hide button last.
- Every item is `--tab` 34 px high and centred (6 px above and below); the header's hero 32 px, the count pill 18.
- The row never scrolls: `#list` wraps and clips (`overflow: clip`), and `fit()` gives every tab past the first row `.off` (hidden, out of the Tab order), the oldest dropping off; the alert still announces a waiting session whose tab is hidden.
- Drop shadow: `Shade`, a layered click-through window of its own (per-pixel alpha, styles in the class, own thread, owned by the panel), `SHADE` 8 px, black at `SHADE_PEAK` 0.30 easing out as (1 - t)^2, leaving clear the rects the page has open. Shown on top while visible; hidden on the right, while dragged as a column and when hidden.
- On top a right-click menu opens at the strip's bottom edge under the tab, a message under a tab floats under the strip, and the alert opens `STRIP` px lower.
- Drag (`drag()`, after 4 px with the button down on the header or grip): the window takes its drop shape live; the strip while the pointer is within `TOP_ZONE` 120 px of the top edge, else the column; on release the column glides to the right edge (160 ms). The button is read before the pointer.

## Field Agents: alert
- `agent-alert.html` in a second pywebview window, made hidden (`on_top`, `focus=False`, frameless, `WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE`), shown top centre of the monitor holding the foreground window with `ShowWindow(SW_SHOWNOACTIVATE)`, rounded by a region, sized 93 + 36 a row, 4 rows then "and n more".
- `check_alerts()` after every live pass and scan: a session that waits joins once per wait; while its own window is in front (`in_front()`: a terminal counts only when its title shows the session's name) it is held back and announced once that window leaves; a session no longer waiting leaves; the alert hides when none is left. Nothing is marked announced before the page says `ready()`; the window is looked up in `alert_up()`.
- Each new row plays `mascot/knock.wav` (three deep knocks, 1.3 s, made by `tools/knock-sound.py`) through `winsound` asynchronously.
- A click on a row brings that window forward and drops the row; the cross clears the alert. Tray menu: Alerts and Alert Sound, kept in `options.json`.
- Rows: the name keeps up to 60 % of the row, the folder the rest, losing its start (CSS in `agent-alert.html`); the row's tooltip is the full folder, after the full name when cut.

## Field Agents: tray, status, update repair, desktop icon
- Tray icon: the 5h figure (`read_limits()`) as a rounded tile in the level colour (`LEVELS`: under 50 #16a34a, under 80 #f5a623, else #e11d48) with digits in #0a1a40 or white, whichever contrasts more; 100 shows `!` on red; no figure or one past its reset: the hero's head (`logo_image()`: the `.ico`'s bust at that size, else the nearest larger shrunk; without the `.ico` a drawn blue tile). Tooltip `Field Agents: 5h 69 %, resets 01:40` or `Field Agents: 5h no figure yet`.
- Drawn at `SM_CXSMICON` (`tray_size()`; font and margins in the code). The `Icon` subclass in `tray()` overrides pystray's private `_assert_icon_handle` to load a one-size ICO at exactly that size (pystray 0.19.5; recheck after an upgrade; `__call__` is overridden too, for the left click). `tray_update()` redraws only when (size, percent, reset) changes.
- Status button `#status` right of the set-aside link (`#foot`, 24 px; on top 34 px in the strip): opens `STATUS_PAGE` https://status.claude.com/ with `os.startfile`. Its dot from `claude_status()` (`STATUS_API` summary, every `STATUS_EVERY` 180 s; `state.claude` shape in the code). none = green; minor or maintenance = gold with an amber halo; major or critical = red with a red halo; anything else = hollow grey ring, never green. Tooltip: description, first unresolved incident (+ "and n more"), "Opens status.claude.com".
- Update repair (`watch_update()` on every scan): when `~/.claude/.last-update-result.json` has `outcome` `failed` with a `timestamp` not in `update.json`, `repair_update()` on a thread: (1) the newest file in `~/.local/share/claude/versions/` that reports its own version; (2) `<it> update` (hidden, 600 s); (3) if `claude.exe` still does not start, move it aside as `claude.exe.old.<ms>` and run `<it> install <version>`; (4) if `claude.exe` starts at or above `version_to`, write a success record in Claude Code's format (`path` `native`, `outcome`/`status` `success`). Otherwise the message stays in the bottom note (the `warn` slot) until a newer attempt is recorded. No session is closed or reopened.
- Version files have no `.exe`: started with `subprocess` `executable=` and `stdin=DEVNULL` (pythonw's own stdin is an invalid handle).
- Desktop icon: `install.py` `desktop_shortcut()` writes `Field Agents.lnk` on the real Desktop (`SHGetFolderPathW(CSIDL_DESKTOPDIRECTORY)`, may be OneDrive) through PowerShell's `WScript.Shell` (paths as environment variables): target the `pythonw.exe` beside the installer's Python, argument the installed `.pyw`, working folder `~/.claude`, icon `~/.claude/agent-panel-mascot/agent-panel.ico`; rewritten in place, then `refresh_icon()`: `SHUpdateImage` + `SHCNE_UPDATEITEM` renew only the 16 and 32 px icons, `SHCNE_ASSOCCHANGED` the 48 px desktop one too (no Explorer restart); then deletes `OLD_SHORTCUTS` (`Agent Panel.lnk`). `remove_shortcut()` deletes either name, each only when it holds `agent-panel.pyw` in UTF-16.

## Field Agents: links come to the front
- `follow_links()`: a hidden top-level window (class `ClaudePlusFAAgentPanelLinks`, `WS_POPUP`, `WS_EX_TOOLWINDOW`, never shown) on `RegisterShellHookWindow`; on `HSHELL_FLASH` (0x8006) `on_flash()` asks `link_raise()` and, on yes, calls `bring_front()`.
- `link_raise()` says yes only when: the foreground is a session window; the flashing window is visible, not cloaked, a root window, of another process than the terminal's and not a session window; the last primary-button Ctrl+click landed on that session window at most `LINK_WAIT` 3 s before the flash (0.5 s after allowed); that click was not answered before.
- `watch_clicks()`: while a session window is in front, `GetAsyncKeyState` of the primary button (`SM_SWAPBUTTON`) and Ctrl every `CLICK_EVERY` 20 ms; otherwise every 0.25 s. No keyboard or mouse hook.
- `links.log`: one line per flash of a visible window while a session window is in front (`brought forward: <exe> <s> after the Ctrl+click`, `could not bring forward: ...`, `left flashing: <exe> (...)` once per window per 30 s). WSLg's invisible `msrdc.exe` RemoteApp window is never logged or raised.

## Background work and the opened card
- The turning badge (child runs and subagents), the opened card (a rested-on card grows with the running work grouped under the typed prompt that set each piece off; *and n more* opens a column of them all), their readers and their limits: [`memory/background-work.md`](./memory/background-work.md).

## Mascot
- The big-headed chibi FA hero: every card 56 px on the left, header 36 px (32 on top), top tab 28 px (the whole figure). One figure per session, first match wins (`figureOf()`): asking (`waiting`) waves with a red "!"; background work running (`delegated()`) conducts with a baton; working runs; open stands; closed sleeps, greyed by CSS (`.av.gone`: `grayscale(1)`, opacity 0.5). Tabs alike; the header shows the most pressing session's figure (ask > background work > working > open), asleep when none is open. Dot, tint and ring still carry the state; a top tab hides its dot while a hero shows.
- Strips (WebP, 284 KB in all), each in 56 px frames (`hero-*.webp`) and 112 px (`hero-*@2x.webp`): `hero-run` 12, `hero-conduct` 9, `hero-ask` 4, `hero-idle` 6, `hero-sleep` 4 frames, each frame's pose listed in `tools/hero-export.py`'s header. Cut by `python -I tools/hero-export.py` from the 512 px frames in `tools/mascot-source/hero/` (its header says how), one Lanczos pass each; one box (run cycle + sleep) for all, checked by the export; cap 35 px wide at 1x, the sleep's 33 (area-matched). How the frames were made: `trials.md`, 2026-11-22.
- Play: `agent-panel.html` holds each strip's `[frame, ms]` steps and the glance (`advance()`, every `GLANCE_EVERY` 6 to 10 s at random); each card starts at a random moment; reduced motion: frame 0 only, no glance. `stripOf()` takes the 56 px strip when the element's width times `devicePixelRatio` is at most 56, else the 112 px one, checked on every patch. Rules: `mindmap/mascot.md`.
- `agent-panel.pyw` inlines the strips named in `HEROES` (both sizes) as `window.MASCOT` data URLs (`mascot_js(only)`); without them the page keeps its old blue logo and no mascot. Both installers delete the folder with `remove_tree` before copying.
- `agent-panel.ico`, the desktop shortcut's icon (the panel's windows are tool windows: no taskbar button, no window icon) and, at 16 to 48 px, the tray's picture without a 5h figure: the hero's idle glance (`idle-4`, facing the viewer, reads best small), a bust (head and cap, cut above the gloves) at 16 to 48 px, the whole figure at 64, 128, 256; made at the end of `hero-export.py`.
- The alert plays `hero-ask` (56 px; the @2x strip when `devicePixelRatio` > 1) at 180 ms a frame; the way-out and way-back dialogs' brand line `hero-ask` at 28 px (both ask a question, so the asking pose fits). README `logo.png`: the idle glance (`idle-4`), whole figure, 280 px (shown 140 wide), cut last by `hero-export.py`.

## Screenshots and the paste key
- The screenshot keeper, the strip and its preview, Copy as Text's OCR, Copy as Image, and the paste key: [`memory/screenshots.md`](./memory/screenshots.md).

## Reply to passages and the reply window
- The ":N" reply (markers, quoted text, the numbered lists, attribution) and the reply window beside the strip (tabs, the tab following the terminal in front, replies from the transcript, markdown with tables, answer boxes, dictation with Space held, drafts, pinned replies, Copy, Send to Session): [`memory/reply.md`](./memory/reply.md).

## Tests
- Unit tests (all pass, 2026-11-25), each `python -I tools/<file>`: `test-quote-reply.py` (62), `test-reply.py` (101, part in headless Edge and Node), `test-agent-panel-state.py` (27), `test-peek.py` (24, made-up files), `test-paste.py` (19; `--clipboard` adds Copy as Image's real clipboard round trip), `test-link-front.py` (15), `test-tray-front.py` (35, fake `user32` and `pystray`), `test-minimise.py` (15; `--windows` adds 2 on throwaway windows), `test-codex-move.py` (12), `test-way-out.py` (16), `test-codex-way-back.py` (16), `test-ocr.py` without `-I` (rendered pictures; `--unit` for the 7 `ocr_rows()` checks).
- Reply window: `%TEMP%` harness page with `agent-shots.html` and `agent-reply.html` in iframes and a stub `pywebview`, Playwright at 1x and 2x; Send to Session end to end only into a `conhost.exe` window the test makes (`trials.md`, 2026-11-23).
- Page checks: the repo's `agent-panel.html` with the mascot strips put in at `/*MASCOT*/`, served from 127.0.0.1 to Playwright with a stub `pywebview`, fed made-up sessions through `app.update()`.
- Window checks: the repo's `agent-panel.pyw` run by a throwaway driver with its mutex and event names renamed (the live panel keeps running), on a temp profile (`USERPROFILE`/`HOME` on `%TEMP%`), the keeper built without `start()`; windows captured by their own handle (`PrintWindow`, `PW_RENDERFULLCONTENT`).
- Functions are taken out of the `.pyw` by `ast` (importing it trips the one-instance mutex and raises the running panel).
- Way-out dialog states: `--preview` flags above; screenshots deleted after looking.

## Gotchas
- A pywebview window shown only by `SetWindowPos(SWP_SHOWWINDOW)` stays blank white (no `WM_SHOWWINDOW` reaches WinForms); show it once with `ShowWindow`.
- pywebview 6.2 activates every window it makes (WinForms applies `MinimumSize` and `TopMost` with `SetWindowPos` without `SWP_NOACTIVATE`, `Show()` is `SW_SHOW`, Shown calls `webview.Focus()`, `show()` calls `Activate()`); `focus=False` only adds `WS_EX_NOACTIVATE`. Hence `quiet_forms()`.
- pywebview sets an empty `window.pywebview.api` before adding the methods; a call in that gap throws. Wait for `pywebviewready`.
- DWM draws the frame's shadow and border round the whole window rectangle and ignores the window region.
- WebView2 draws no see-through pixels and does not take a layered parent; translucent effects need a separate layered window.
- An owned window stays above its owner but does not hide with it.
- `SetForegroundWindow` can succeed while another window stays over the target; follow it with HWND_TOPMOST then HWND_NOTOPMOST. From ctypes pass them as `c_void_p(-1)` / `c_void_p(-2)` (a bare `-1` fails with error 1400).
- Inserting the panel after a topmost window makes the panel topmost too.
- The foreground event can arrive before Windows Terminal raises its window; maximise, restore or drag of the front window gives only `EVENT_OBJECT_LOCATIONCHANGE`.
- `WM_CLOSE` to a console window under Windows Terminal (the default terminal here, `HKCU\Console\%%Startup` DelegationTerminal) closes every tab; a process ending with exit code 0 makes it close only its tab.
- `UnregisterHotKey` from another thread fails (error 1419); a dead process's hotkeys are freed.
- WSLg's `msrdc.exe` reads every text clipboard at once, quiet formats or not.
- A `WS_EX_NOACTIVATE` window with no taskbar button gets no `HSHELL_FLASH` (a test window needs `WS_EX_APPWINDOW`); a message-only window gets no shell hook.
- pythonw started detached has an invalid stdin (OSError 9 from `subprocess`): pass `stdin=DEVNULL`.
- pystray loads its ICO with `LR_DEFAULTSIZE` (32 px at 100 %) and the tray shrinks it.
- Chromium sends a wheel to the window under the pointer; an inactive strip gets none with Windows' "Scroll inactive windows" off.
- The panel is not topmost, so a drag on a covered panel lands on the window above it; raise it first (Ctrl+Alt+C).
- A hook's detached child was killed when the hook ended; that is why the dialog waits inside the hook.
- Testing `/compact` from Git Bash needs `MSYS_NO_PATHCONV=1`.
- A pywebview window the user types into must not get `focus=False` or `WS_EX_NOACTIVATE`: a click then never gives it the keyboard.
- A pywebview page given as `html=` runs at `about:blank`, not a secure context (no `navigator.mediaDevices`); load it from `file:///` and answer WebView2's `PermissionRequested` yourself, or it asks with its own dialog.
- The strip and the preview are one page (`agent-shots.html`): a new element id there must not repeat one of the other mode's (`#head` did).
- `SendMessageTimeoutW` declared with `LPARAM` takes `ctypes.addressof(struct)`, not `byref`.
- In tests, find a stand-in window by the handle of the window the test created, never by title. Check `WindowFromPoint`'s root before a real click by position. Bringing a test window forward while the user types sends their keys into it: keep such tests to one short batch.
- The Recycle Bin retries a file held open for about 1 s before refusing (0x20).
- Top-dock heights were measured against a maximised Chrome at 100 % (toolbar y = 40, address box y = 46); a Chrome update or another scale can move them: measure again rather than guess.

## Limits
- A short 429 that ends a turn also triggers the dialog; once it is answered (Cancel included), that session's dialog stays shut until the 5-hour reset (5 h when no figure is known), so a real limit before then shows none. Every project can fall back, so sensitive code goes to z.ai or OpenAI if you click Continue.
- The old window closes once GLM holds the session or Codex has answered, unless it still runs work (subagents, a `claude -p` it started, background commands): then it closes when that ends; work the panel cannot see (a background command whose process is gone, a Monitor) does not hold it. Text typed into it and not sent is lost with it. An old window kept open for its work does not see GLM's turns (another process wrote them): a prompt typed there goes on without them.
- The way back needs the panel running (except a hand-typed resume or prompt after Codex, which only needs the `--carry` hooks); it is answered by mouse only. So do closing the old window and the return when the Codex window closes.
- A hand-typed `claude --resume <id>` after GLM fails with a model error (Claude Code resumes on the last request's model, ahead of `settings.json`, before any hook); `/model default` inside a run does not fix later resumes; `ANTHROPIC_MODEL` would, but pins every session and is not used. Workaround: `claude --resume <id> --model default` (README).
- A hand-started `glm --resume` holding a session with a record counts as Claude holding it: a GLM record is dropped; Codex's turns so far are carried into GLM, later ones at a prompt typed there once the Codex window is closed.
- Codex's work reaches the Claude transcript only as the record, not as Claude turns, and only when the session comes back (or at a resume by hand; a prompt typed while the Codex window is open carries nothing). The list of files written since the move may include other people's changes.
- Codex gets only the latest part of a long session (after the last compaction, or less over 100,000 tokens); it can read the rest from the transcript path. A single prompt over the budget is still carried whole. The estimate follows Codex CLI 0.164.0; a later import that keeps more is caught by the ceiling.
- Each move to Codex of a compacted or over-budget session, and each further move, leaves a copy in `codex-transfer/` (nothing deletes them); a cut that Codex counts over the ceiling leaves its first import as an unused thread.
- The first-turn watch sees only up to Codex's first answer; a later failure is not reported. Codex's traps are worked around for Codex CLI 0.164.0 to 0.171.0 and plugin 1.0.6 (`memory/limit-fallback.md`, Codex's own traps); a later version may ask something new. The move marks the folder's git root trusted in Codex's own config when Codex had no answer for it.
- Reopened in Claude before the reset (the Codex window closed), the session's way-out window stays shut until the reset; that Claude Code then continues by itself is not tested on a freshly resumed window (`trials.md`, 2026-11-22).
- Without Windows Terminal the Codex window's title comes from Codex itself (`tui.terminal_title`), seen only in a Windows Terminal tab that did not pin its title, not in a plain console window.
- Status dot: up to 3 minutes behind status.claude.com, and says only what Anthropic posts there.
- Update repair acts only on a failure Claude Code recorded, and needs the panel running.
- Screenshots need the panel running: one taken while it is off is not saved, a paste made while it is off is not followed. An image copied from Snip & Sketch's editor counts; on Windows 11 anything the broker delivers while the Snipping Tool runs may count. Not seen: a real PrintScreen or Win+PrintScreen press.
- Pinned screenshots all stay in the strip: past about 100 cards on a 1920 x 1040 work area the bottom rows are cut off (no scrolling). The preview holds every strip picture at full size in memory. A pinned file on a missing drive loses its pin.
- Copy as Text takes about 0.1 to 0.3 s on ordinary text and 0.6 to 1.2 s on terminal text; rows of code of nearly the same length at one indent can be joined when the picture also holds wrapped rows; Consolas quotes can come out as `ll` or `I`, `=` as `:`; the first Copy after the worker died takes about 1 s more; text in pictures over 10000 px is not read.
- A large new screenshot takes a moment to reach the preview (about 0.7 s for 6 MB); a click then waits. Copy as Image of a 3840 x 2160 picture: about 0.35 s, 76 MB on the clipboard. The strip never spans two monitors. While the preview shows, Esc goes to it alone. Delete on a file held open answers *Failed* after about 1 s.
- Reply to a passage: numbered lists and the reply window need the panel running and shown with strip and keeper on (otherwise bare `:` quotes the clipboard and `:N` matches nothing). Only a selection tied to exactly one session is listed: none from outside a session's terminal or from a tab whose title does not show its name; none from a Linux `claude` inside WSL. A chance ` :2 ` outside code quotes entry 2 if listed. Hiding the panel or switching the strip off empties every list. A selection cannot be dropped by hand; a prompt empties the list. A `:N` with at least 16 characters round it that also stand in a listed passage or one of Claude's last 40 text blocks counts as quoted text, so typing a long stretch of a reply's own example verbatim is not a marker. Without `copyOnSelect` (or Ctrl+C) a `:` quotes whatever was copied before. Console (conhost) windows were not tested.
- Reply window: needs the panel running with the strip shown; only replies that ended a turn (text Claude writes between tool calls is not shown); not for a session Codex holds; a session's window changes only when its turn ends. Send to Session types only into the session's own terminal window whose title shows Claude Code's glyph (with several session tabs, its own tab must come to the front), needs the paste key (`paste_keys` on), inserts at the cursor (text already in the prompt stays and is not read), never presses Enter; a line over 5 KiB may meet Windows Terminal's large-paste question (not tried). Answers to replies a newer prompt replaced are dropped. The tab follows a terminal only after the panel has found its window (a scan, every 15 s, after the session opens); pins not tried on the live panel; the follow and Send to Session end to end only on conhost windows the test made, not in Windows Terminal (`trials.md`).
- Dictation (Space held in an answer box): needs a local speech-to-text server with the `/api/dictate` contract (the maintainer's Voice to Text) and a microphone Windows lets desktop apps use. Never tried with a real microphone or a real hold on the live panel (`trials.md`). Words come in steps of one server pass (about 2.5 to 4 s here, `small` on the CPU), and the CPU stays busy while Space is held. Claude Code's timing is copied by counting key repeats, so a keyboard repeat delay longer than `HOLD_MS` 700 ms starts on the timer instead.
- Paste key: needs the panel running; never in a Codex window (use Codex's own Alt+V there); only in a session's own terminal window whose title shows Claude Code's glyph (a Linux `claude` in WSL only in a window that also holds a Windows session). The model sees each picture's path. A screenshot pasted within the keeper's 3 s `HOLD` goes as a temp file. Restoring loses OLE-only and handle formats; a clipboard manager that ignores the quiet formats sees the picture put back as a new copy. Ctrl+V in Windows Terminal's own search box or command palette with a Claude tab in front pastes the path there. A key another program registered is left to it (logged).
- Links come to the front only after a primary-button Ctrl+click on the session's own window while the panel runs, and only for a window Windows flashes within 3 s; a link opened another way, or a program refused without a flash, stays behind; a Ctrl+click on plain text followed within 3 s by another program's own flash would bring that program forward. Not yet seen live: a refused window brought forward.
- Tray click's terminal, and its card clicked after it: fake Win32 and throwaway Windows Terminal windows (`trials.md`); the real tray icon not clicked. A maximised terminal brought back covers the panel.
- Minimise keeping the panel: throwaway windows and a throwaway Windows Terminal window (`trials.md`, 2026-11-24), not a real session's terminal.
- Not tested: a real mouse double click on the desktop icon; a terminal with several session tabs for `shared()`; a light taskbar on the real tray.

## Open questions
- The busy card's last action ("Edit memory.md", "Bash: npm test") from the transcript tail: feasible within the 2 s pass (measured, see `trials.md`), not built; the maintainer has not decided whether to show it.
- An option, not done: the `--carry` SessionStart hook could print a hint when a resumed session's last turn was not `claude-*`.
