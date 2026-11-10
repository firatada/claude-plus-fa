# StopFailure hook (matcher: rate_limit). When Claude's usage limit ends a turn,
# ask in a themed window whether to continue with the GLM profile (~/.local/bin/glm.cmd); on Yes,
# reopen the same session in a new cmd window. Same transcript, so no context is lost.
# On Yes it also records the move in ~/.claude/state/glm-return/<session>.json (the session, its folder,
# the GLM window's process, when Claude's limit resets), so the Agent Panel can offer the way back to
# Claude once the limit has reset; the panel asks that question through this script's --ask-return.
#
# The question is asked inside the hook, so settings.json gives this hook a long
# timeout; StopFailure output is ignored, so blocking costs nothing.
#
# Part of claude-plus. Edit the copy in the claude-plus-fa folder and rerun install.cmd.
import base64, ctypes, html, json, os, subprocess, sys, threading, time

MB_YESNO, MB_ICONQUESTION, MB_SETFOREGROUND, MB_TOPMOST, IDYES = 0x4, 0x20, 0x10000, 0x40000, 6
TITLE = "Claude Code: usage limit reached"
RETURN_TITLE = "Claude Code: usage limit reset"
HOME = os.path.expanduser("~")
RETURN_DIR = os.path.join(HOME, ".claude", "state", "glm-return")  # read by the Agent Panel
LIMITS_FILE = os.path.join(HOME, ".claude", "state", "context", "rate_limits.json")  # written by statusline.py
FIVE_HOURS = 5 * 3600
# the two questions the window asks: the way out to GLM (this hook) and the way back to Claude (the panel)
OUT = {"window": TITLE, "title": "Usage Limit Reached: %(folder)s",
       "body": "Claude's usage limit ended this session's turn. Continue it with GLM (z.ai) in a new window, "
               "from where it stopped?",
       "note": '<p class="note">The conversation so far is sent to z.ai.</p>',
       "no": "Cancel", "yes": "Continue With GLM"}
BACK = {"window": RETURN_TITLE, "title": "Usage Limit Reset: %(folder)s",
        "body": "Claude's limit has reset. Continue <strong>%(session)s</strong> with Claude, from where it stopped?",
        "note": '<p class="hint">Continue With Claude opens it in a new Claude window, then closes the GLM one. '
                '%(later)s</p>',
        "no": "Not Now", "yes": "Continue With Claude"}

# The question is the app's own themed modal (mindmap design.md "No Native Popups"), drawn in a
# WebView2 window by pywebview, which the installer puts in for the Agent Panel. Same
# Spark tokens as agent-panel.html. Shape per design-dashboard.md "Modals": a title naming the
# thing, an X, the body with an amber callout for the side effect, Cancel and a confirm that
# names its action, side by side and equally wide. Above the title a brand line says the window
# comes from claude-plus-fa, with the Agent Panel's purple octopus in its asking pose (the maintainer,
# 2026-11-10: so the user knows where the question comes from).
PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><style>
:root { --bg: #f4f6fb; --panel: #ffffff; --surface2: #eef2fb; --line: #e2e8f5; --text: #0a1a40; --muted: #4b5672;
  --blue: #1b4df5; --on-accent: #ffffff; --amber-tint: #fff7e6; --amber-line: #f5d9a8; --amber-text: #7a4b00;
  --glow: 0 4px 14px rgba(27, 77, 245, .35); --r-ctl: 10px; --r-small: 6px; }
*, *::before, *::after { box-sizing: border-box; margin: 0; }
html, body { height: 100%; }
body { background: var(--panel); color: var(--text); font: 400 13px/1.45 "Segoe UI", system-ui, sans-serif;
  display: flex; flex-direction: column; overflow: hidden; user-select: none; cursor: default;
  border: 1px solid var(--line); }
button { font: inherit; cursor: pointer; }
:focus-visible { outline: 2px solid var(--blue); outline-offset: 2px; }
/* the octopus's drawing fills its 96 px frames almost edge to edge (ink x 3..94), so the box is the drawing's
   size; -1 px puts its ink on the title's left edge */
.brand { display: flex; align-items: center; gap: 8px; padding: 12px 20px 0; color: var(--muted);
  font-size: 12px; font-weight: 600; -webkit-app-region: drag; }
#me { flex: none; width: 28px; height: 28px; margin-left: -1px; background: no-repeat 0 0 / 400% 100%; }
header { display: flex; align-items: center; gap: 12px; padding: 8px 20px 14px; border-bottom: 1px solid var(--line);
  -webkit-app-region: drag; }
h1 { flex: 1; min-width: 0; font-size: 15px; font-weight: 600; overflow-wrap: anywhere; }
#x { -webkit-app-region: no-drag; flex: none; width: 26px; height: 26px; padding: 0; border: 0;
  border-radius: var(--r-small); background: none; color: var(--muted); display: grid; place-items: center; }
#x:hover { background: var(--surface2); color: var(--text); }
main { flex: 1; padding: 16px 20px; display: flex; flex-direction: column; gap: 12px; }
.hint { color: var(--muted); }
.note { padding: 8px 12px; background: var(--amber-tint); border: 1px solid var(--amber-line);
  border-radius: var(--r-ctl); color: var(--amber-text); }
footer { display: flex; gap: 12px; padding: 14px 20px; border-top: 1px solid var(--line); }
.btn { flex: 1; min-height: 34px; padding: 6px 14px; border: 1px solid var(--line); border-radius: var(--r-ctl);
  background: var(--panel); color: var(--text); font-weight: 600; transition: transform .16s ease-out; }
.btn:hover { background: var(--surface2); }
.btn.go { background: var(--blue); border-color: var(--blue); color: var(--on-accent); box-shadow: var(--glow); }
.btn.go:hover { filter: brightness(1.08); background: var(--blue); }
.btn:active, #x:active { transform: scale(.97); }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } .btn:active, #x:active { transform: none; } }
</style></head><body role="dialog" aria-labelledby="t" aria-describedby="d">
<div class="brand">%(mascot)s<span>claude-plus-fa</span></div>
<header><h1 id="t">%(title)s</h1>
  <button id="x" aria-label="Close"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="2.4" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button></header>
<main><p id="d">%(body)s</p>
  %(note)s</main>
<footer><button class="btn" id="no">%(no)s</button><button class="btn go" id="yes">%(yes)s</button></footer>
<script>
// the asking octopus, its 4 frames played as in the Agent Panel (550 ms each); frame 0 only under reduced motion
const me = document.getElementById("me"), still = matchMedia("(prefers-reduced-motion: reduce)");
if (me) { let f = 0; setInterval(() => { f = still.matches ? 0 : (f + 1) % 4; me.style.backgroundPositionX = f * 100 / 3 + "%"; }, 550); }
const answer = yes => window.pywebview && window.pywebview.api.answer(yes);
document.getElementById("yes").onclick = () => answer(true);
document.getElementById("no").onclick = () => answer(false);
document.getElementById("x").onclick = () => answer(false);
addEventListener("keydown", e => { if (e.key === "Escape") answer(false); });
// the window is sized to its content, then shown, so nothing is cut at any display scale
addEventListener("pywebviewready", async () => {
  const need = [...document.body.children].filter(e => e.tagName !== "SCRIPT")
                                         .reduce((h, e) => h + e.getBoundingClientRect().height, 0) + 2;
  await window.pywebview.api.fit(Math.ceil(need - innerHeight));
  document.getElementById("yes").focus();
});
</script></body></html>"""


def mascot_html():
    """The octopus strip the Agent Panel installs (or the repo's, run from the claude-plus-fa folder) as a data
    URL, since the page is handed over as a string; without it the brand line is the name alone."""
    here = os.path.dirname(os.path.abspath(__file__))
    for folder in (os.path.join(os.path.dirname(here), "agent-panel-mascot"), os.path.join(here, "mascot")):
        try:
            with open(os.path.join(folder, "asking-purple.webp"), "rb") as f:
                data = base64.b64encode(f.read()).decode()
        except OSError:
            continue
        return '<span id="me" aria-hidden="true" style="background-image: url(data:image/webp;base64,%s)"></span>' % data
    return ""


def ask_themed(folder, forward=True, words=OUT, session="", later="", activate=True):
    """The themed modal; True for the confirm button, False for the other one, the X, Escape or a closed window.
    forward=False leaves the foreground alone (the preview: no key is pressed). activate=False shows it over
    every window without taking the keyboard: the way back comes at a moment the user did not pick, and a key
    they were typing elsewhere must never answer it."""
    import webview
    result = {"yes": False}

    class Api:
        def answer(self, yes):
            result["yes"] = bool(yes)
            window.destroy()

        def fit(self, more):
            window.resize(window.width, window.height + int(more))
            window.show()
            if forward and activate:
                threading.Thread(target=come_forward, daemon=True).start()

    def come_forward():
        # a hook runs in the background, and Windows refuses the foreground to it
        # unless a key was pressed: a lone Alt press lets the window take it
        user32 = ctypes.windll.user32
        for _ in range(50):
            hwnd = user32.FindWindowW(None, words["window"])
            if hwnd:
                break
            time.sleep(0.1)
        else:
            return
        user32.SetForegroundWindow(hwnd)
        if user32.GetForegroundWindow() != hwnd:
            user32.keybd_event(0x12, 0, 0, 0)
            user32.keybd_event(0x12, 0, 2, 0)
            user32.SetForegroundWindow(hwnd)

    fill = {"folder": html.escape(folder), "session": html.escape(session), "later": html.escape(later)}
    page = PAGE.replace("%(mascot)s", mascot_html())
    for key in ("title", "body", "note", "no", "yes"):
        page = page.replace("%%(%s)s" % key, words[key] % fill)
    window = webview.create_window(words["window"], html=page, js_api=Api(),
                                   width=440, height=250, frameless=True, easy_drag=False, on_top=True,
                                   hidden=True, focus=activate, background_color="#FFFFFF")
    webview.start(gui="edgechromium", private_mode=True)
    return result["yes"]


def ask_native(folder):
    """Only when pywebview cannot load (not installed): the Windows message box, so the offer is never lost."""
    text = ("Claude's usage limit has been reached in %s.\n\n"
            "Continue this session with GLM (z.ai) in a new window?" % folder)
    return ctypes.windll.user32.MessageBoxW(
        None, text, "Claude Code", MB_YESNO | MB_ICONQUESTION | MB_SETFOREGROUND | MB_TOPMOST) == IDYES


def has_key():
    """A real z.ai key in the file glm.cmd reads; without one, Yes would only send the
    conversation to z.ai to be refused, so there is nothing to offer."""
    path = os.path.join(HOME, ".claudex", "profiles", "glm", ".env")
    try:
        for line in open(path, encoding="utf-8"):
            name, _, value = line.strip().partition("=")
            if name == "CLAUDEX_KEY":
                value = value.strip().strip('"')
                return bool(value) and value != "PASTE_YOUR_KEY_HERE"
    except OSError:
        pass
    return False


# ---------- the record the Agent Panel reads to bring the session back to Claude ----------
def alive(pid):
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
    k32.CloseHandle(h)
    return bool(ok) and code.value == 259  # STILL_ACTIVE


def reset_time(now, limits=None):
    """When Claude can be used again, and where that figure came from. The 5-hour window's resets_at as the
    status line last saved it; a weekly limit that is also used up and resets later wins. Missing, unreadable
    or already past (stale): now + 5 h, the latest a 5-hour window can reset, so the question never comes
    before the limit is gone."""
    if limits is None:
        try:
            with open(LIMITS_FILE) as f:
                limits = json.load(f)
        except (OSError, ValueError):
            limits = {}
    best = None
    for name in ("five_hour", "seven_day"):
        v = limits.get(name) if isinstance(limits, dict) else None
        try:
            t, used = float(v["resets_at"]), float(v.get("used_percentage") or 0)
        except (TypeError, KeyError, ValueError, AttributeError):
            continue
        if t > now and (name == "five_hour" or used >= 100) and (best is None or t > best[0]):
            best = (t, name)
    return best or (now + FIVE_HOURS, "estimate")


def claude_pid(sid):
    """The live Claude process holding this session (the one whose turn the limit ended), from
    ~/.claude/sessions/<pid>.json; None if not found."""
    folder = os.path.join(HOME, ".claude", "sessions")
    try:
        names = os.listdir(folder)
    except OSError:
        return None
    for n in names:
        if n.endswith(".json"):
            try:
                o = json.load(open(os.path.join(folder, n), encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if o.get("sessionId") == sid and o.get("pid") and alive(o["pid"]):
                return int(o["pid"])
    return None


def record_switch(sid, cwd, glm_cmd, orig_pid, skip, limits=None, now=None):
    """Write the record of a session moved to GLM. glm_cmd: the process of the GLM window (cmd /k); orig_pid:
    the Claude process whose turn the limit ended, looked up before GLM started (after, GLM's own process
    holds the same session and could be taken for it)."""
    now = time.time() if now is None else now
    resets_at, source = reset_time(now, limits)
    rec = {"session": sid, "cwd": cwd, "folder": os.path.basename(cwd.rstrip("\\/")) or cwd,
           "glm_cmd": glm_cmd, "orig_pid": orig_pid, "skip": bool(skip), "at": now,
           "resets_at": resets_at, "reset_from": source}
    os.makedirs(RETURN_DIR, exist_ok=True)
    path = os.path.join(RETURN_DIR, sid + ".json")
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(rec, f)
    os.replace(path + ".tmp", path)
    return rec


def ask_return(answer_file, folder, session, later):
    """The way back, asked for the Agent Panel. The answer ("continue" or "later") goes to answer_file, which
    the panel reads, so it is not lost if the panel restarts while the window is open."""
    yes = ask_themed(folder, words=BACK, session=session, later=later, activate=False)
    with open(answer_file + ".tmp", "w", encoding="utf-8") as f:
        f.write("continue" if yes else "later")
    os.replace(answer_file + ".tmp", answer_file)


def main():
    # python glm-fallback.py --preview [folder]: the dialog alone, its answer printed; nothing is read, sent or
    # started, and no key is pressed. For checking its look. --preview-return [folder]: the way back's dialog.
    if sys.argv[1:2] == ["--preview"]:
        print(ask_themed(sys.argv[2] if len(sys.argv) > 2 else "example", forward=False))
        return
    if sys.argv[1:2] == ["--preview-return"]:
        print(ask_themed(sys.argv[2] if len(sys.argv) > 2 else "example", forward=False, words=BACK,
                         session="Example session", later="Not Now stays on GLM and asks once more in 30 minutes."))
        return
    # --ask-return <answer file> <folder> <session title> <what Not Now does>: run by the Agent Panel
    if sys.argv[1:2] == ["--ask-return"] and len(sys.argv) >= 6:
        ask_return(*sys.argv[2:6])
        return

    data = json.load(sys.stdin)

    # Already running through a gateway (the GLM session itself): never chain.
    if os.environ.get("ANTHROPIC_BASE_URL"):
        return
    sid = data.get("session_id")
    if not sid or not has_key():
        return
    cwd = data.get("cwd") or os.getcwd()

    folder = os.path.basename(cwd.rstrip("\\/")) or cwd
    try:
        import webview  # noqa: F401
        yes = ask_themed(folder)
    except ImportError:
        yes = ask_native(folder)
    if not yes:
        return

    skip = data.get("permission_mode", "bypassPermissions") == "bypassPermissions"
    orig = claude_pid(sid)
    cmd = "glm --resume " + sid
    if skip:
        cmd += " --dangerously-skip-permissions"
    cmd += ' "Claude usage limit reached. Continue exactly where the previous turn stopped."'

    # A string, not a list: list quoting escapes the inner quotes as \" which cmd
    # does not understand. Break away from the hook's job so the window outlives it.
    flags = subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        glm = subprocess.Popen("cmd /k " + cmd, cwd=cwd,
                               creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB)
    except OSError:  # the job forbids breakaway
        glm = subprocess.Popen("cmd /k " + cmd, cwd=cwd, creationflags=flags)
    try:
        record_switch(sid, cwd, glm.pid, orig, skip)
    except OSError:
        pass  # the way back is a convenience; the move to GLM has happened either way


if __name__ == "__main__":
    main()
