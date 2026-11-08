# StopFailure hook (matcher: rate_limit). When Claude's usage limit ends a turn,
# ask in a themed window whether to continue with the GLM profile (~/.local/bin/glm.cmd); on Yes,
# reopen the same session in a new cmd window. Same transcript, so no context is lost.
#
# The question is asked inside the hook, so settings.json gives this hook a long
# timeout; StopFailure output is ignored, so blocking costs nothing.
#
# Part of claude-plus. Edit the copy in the claude-plus-fa folder and rerun install.cmd.
import ctypes, html, json, os, subprocess, sys, threading, time

MB_YESNO, MB_ICONQUESTION, MB_SETFOREGROUND, MB_TOPMOST, IDYES = 0x4, 0x20, 0x10000, 0x40000, 6
TITLE = "Claude Code: usage limit reached"

# The question is the app's own themed modal (mindmap design.md "No Native Popups"), drawn in a
# WebView2 window by pywebview, which the installer puts in for the Agent Panel. Same Afiniti
# Spark tokens as agent-panel.html. Shape per design-dashboard.md "Modals": a title naming the
# thing, an X, the body with an amber callout for the side effect, Cancel and a confirm that
# names its action, side by side and equally wide.
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
header { display: flex; align-items: center; gap: 12px; padding: 14px 20px; border-bottom: 1px solid var(--line);
  -webkit-app-region: drag; }
h1 { flex: 1; min-width: 0; font-size: 15px; font-weight: 600; overflow-wrap: anywhere; }
#x { -webkit-app-region: no-drag; flex: none; width: 26px; height: 26px; padding: 0; border: 0;
  border-radius: var(--r-small); background: none; color: var(--muted); display: grid; place-items: center; }
#x:hover { background: var(--surface2); color: var(--text); }
main { flex: 1; padding: 16px 20px; display: flex; flex-direction: column; gap: 12px; }
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
<header><h1 id="t">Usage Limit Reached: %(folder)s</h1>
  <button id="x" aria-label="Close"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="2.4" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button></header>
<main><p id="d">Claude's usage limit ended this session's turn. Continue it with GLM (z.ai) in a new window, from where it stopped?</p>
  <p class="note">The conversation so far is sent to z.ai.</p></main>
<footer><button class="btn" id="no">Cancel</button><button class="btn go" id="yes">Continue With GLM</button></footer>
<script>
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


def ask_themed(folder):
    """The themed modal; True for Continue, False for Cancel, the X, Escape or a closed window."""
    import webview
    result = {"yes": False}

    class Api:
        def answer(self, yes):
            result["yes"] = bool(yes)
            window.destroy()

        def fit(self, more):
            window.resize(window.width, window.height + int(more))
            window.show()
            threading.Thread(target=come_forward, daemon=True).start()

    def come_forward():
        # a hook runs in the background, and Windows refuses the foreground to it
        # unless a key was pressed: a lone Alt press lets the window take it
        user32 = ctypes.windll.user32
        for _ in range(50):
            hwnd = user32.FindWindowW(None, TITLE)
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

    window = webview.create_window(TITLE, html=PAGE.replace("%(folder)s", html.escape(folder)), js_api=Api(),
                                   width=440, height=250, frameless=True, easy_drag=False, on_top=True,
                                   hidden=True, background_color="#FFFFFF")
    webview.start(gui="edgechromium", private_mode=True)
    return result["yes"]


def ask_native(folder):
    """Only when pywebview cannot load (not installed): the Windows message box, so the offer is never lost."""
    text = ("Claude's usage limit has been reached in %s.\n\n"
            "Continue this session with GLM (z.ai) in a new window?" % folder)
    return ctypes.windll.user32.MessageBoxW(
        None, text, "Claude Code", MB_YESNO | MB_ICONQUESTION | MB_SETFOREGROUND | MB_TOPMOST) == IDYES

data = json.load(sys.stdin)

# Already running through a gateway (the GLM session itself): never chain.
if os.environ.get("ANTHROPIC_BASE_URL"):
    sys.exit(0)
sid = data.get("session_id")
if not sid:
    sys.exit(0)


def has_key():
    """A real z.ai key in the file glm.cmd reads; without one, Yes would only send the
    conversation to z.ai to be refused, so there is nothing to offer."""
    path = os.path.join(os.path.expanduser("~"), ".claudex", "profiles", "glm", ".env")
    try:
        for line in open(path, encoding="utf-8"):
            name, _, value = line.strip().partition("=")
            if name == "CLAUDEX_KEY":
                value = value.strip().strip('"')
                return bool(value) and value != "PASTE_YOUR_KEY_HERE"
    except OSError:
        pass
    return False


if not has_key():
    sys.exit(0)
cwd = data.get("cwd") or os.getcwd()

folder = os.path.basename(cwd.rstrip("\\/")) or cwd
try:
    import webview  # noqa: F401
    yes = ask_themed(folder)
except ImportError:
    yes = ask_native(folder)
if not yes:
    sys.exit(0)

cmd = "glm --resume " + sid
if data.get("permission_mode", "bypassPermissions") == "bypassPermissions":
    cmd += " --dangerously-skip-permissions"
cmd += ' "Claude usage limit reached. Continue exactly where the previous turn stopped."'

# A string, not a list: list quoting escapes the inner quotes as \" which cmd
# does not understand. Break away from the hook's job so the window outlives it.
flags = subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP
try:
    subprocess.Popen("cmd /k " + cmd, cwd=cwd,
                     creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB)
except OSError:  # the job forbids breakaway
    subprocess.Popen("cmd /k " + cmd, cwd=cwd, creationflags=flags)
