Parent: [`memory.md`](../memory.md) — the traps of Windows, pywebview, WebView2 and Claude Code that this code works around; current knowledge, one line each.

# claude-plus-fa: gotchas

Moved whole from memory.md § Gotchas on 2026-11-26, when memory.md reached its budget.

## Scope
Owns: the platform and tool traps met while building claude-plus-fa, each with what to do about it. Not what a feature does (its own memory file) nor what cannot be done at all ([`limits.md`](./limits.md)).

## Gotchas
- Claude Code (seen from 2.1.278 to 2.1.332) stores a pasted text inside the prompt as `<pasted_content id="ab12">` + newline + the text + newline + `</pasted_content id="ab12">`, in the transcript and in the `UserPromptSubmit` hook's `prompt` alike; a prompt that starts with a paste then starts with two newlines and `<`, like the lines Claude Code writes itself (`<command-name>`, `<local-command-stdout>`, `<bash-input>`, `<system-reminder>`, `<task-notification>`). Every reader takes such a prompt as the user's and its words as what the wrappers hold (`prompt_words()` in `agent-panel.pyw` and `glm-fallback.py`, `PASTED` in `prompt-recall.py`); any other leading `<` stays a system line.
- Claude Code appends lines with no turn to an open session's transcript (`last-prompt` to `atis-latch`, `bridge-session`, `data-flow-records`, `away_summary`, `cost-state`), even a day after its last prompt: a transcript's file time is not its last activity.
- A pywebview window shown only by `SetWindowPos(SWP_SHOWWINDOW)` stays blank white (no `WM_SHOWWINDOW` reaches WinForms); show it once with `ShowWindow`.
- pywebview 6.2 activates every window it makes (WinForms applies `MinimumSize` and `TopMost` with `SetWindowPos` without `SWP_NOACTIVATE`, `Show()` is `SW_SHOW`, Shown calls `webview.Focus()`, `show()` calls `Activate()`); `focus=False` only adds `WS_EX_NOACTIVATE`. Hence `quiet_forms()`.
- pywebview sets an empty `window.pywebview.api` before adding the methods; a call in that gap throws. Wait for `pywebviewready`.
- DWM draws the frame's shadow and border round the whole window rectangle and ignores the window region.
- WebView2 draws no see-through pixels and does not take a layered parent; translucent effects need a separate layered window.
- An owned window stays above its owner but does not hide with it.
- `SetForegroundWindow` can succeed while another window stays over the target; follow it with HWND_TOPMOST then HWND_NOTOPMOST. From ctypes pass them as `c_void_p(-1)` / `c_void_p(-2)` (a bare `-1` fails with error 1400).
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
- `cmd.exe` started in a `//host/share` folder runs in `C:\Windows` ("UNC paths are not supported"); `CreateProcess` accepts the folder, so nothing fails.
- The first open of a newly written large file waits on the virus scanner (1.4 s for a 1.2 GB one); time reads only after one open.
- Top-dock heights follow a measurement against a maximised Chrome at 100 % (`trials.md`); another Chrome or scale: measure again.
