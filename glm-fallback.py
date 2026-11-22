# StopFailure hook (matcher: rate_limit). When Claude's usage limit ends a turn, ask in a themed window
# whether to continue the session with GLM (~/.local/bin/glm.cmd, z.ai) or with Codex (OpenAI), each in a
# new window from where it stopped:
#   GLM   reopens the same session (`glm --resume <id>`): GLM reads and keeps writing the same transcript.
#   Codex imports the transcript into a resumable Codex thread with the Codex plugin's own transfer, then
#         opens `codex resume <thread>`; Codex writes its turns to its own thread file.
# An option that is not ready (no GLM key; Codex CLI, plugin or sign-in missing) shows its set-up steps
# instead, and the window notices when it becomes ready.
# On either way out it records the move in ~/.claude/state/glm-return/<session>.json, so the Agent Panel can
# offer the way back to Claude once the limit has reset; the panel asks through this script's --ask-return
# and, coming back from Codex, uses codex_back_prompt() to carry what happened in Codex into the transcript.
# Codex opens in a window of its own titled with the session's name (its thread named the same); once Codex
# runs there the panel closes the old Claude window, and when the Codex window closes it brings the session
# back to Claude by itself. Codex's turns are carried in incrementally: each carry takes only the turns after
# the last one carried, so an early carry never swallows later turns.
# The record lives until a Claude process holds the session again. Run as `--carry` (SessionStart resume and
# UserPromptSubmit hooks) it carries Codex's turns into a session the user resumed or typed into by hand.
#
# The question is asked inside the hook, so settings.json gives this hook a long
# timeout; StopFailure output is ignored, so blocking costs nothing.
#
# Part of claude-plus. Edit the copy in the claude-plus-fa folder and rerun install.cmd.
import base64, contextlib, ctypes, ctypes.wintypes as wt, datetime, glob, hashlib, html, json, os, queue, re, \
    shutil, struct, subprocess, sys, threading, time

MB_YESNO, MB_ICONQUESTION, MB_SETFOREGROUND, MB_TOPMOST, IDYES = 0x4, 0x20, 0x10000, 0x40000, 6
TITLE = "Claude Code: usage limit reached"
RETURN_TITLE = "Claude Code: usage limit reset"
HOME = os.path.expanduser("~")
RETURN_DIR = os.path.join(HOME, ".claude", "state", "glm-return")  # read by the Agent Panel
LIMITS_FILE = os.path.join(HOME, ".claude", "state", "context", "rate_limits.json")  # written by statusline.py
KEY_FILE = os.path.join(HOME, ".claudex", "profiles", "glm", ".env")
PLACEHOLDER = "PASTE_YOUR_KEY_HERE"
PLUGIN_ID = "codex@openai-codex"
PLUGIN_CACHE = os.path.join(HOME, ".claude", "plugins", "cache", "openai-codex", "codex")
INSTALLED_PLUGINS = os.path.join(HOME, ".claude", "plugins", "installed_plugins.json")
FIVE_HOURS = 5 * 3600
CONTINUE = "Claude usage limit reached. Continue exactly where the previous turn stopped."
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
LINKS = {"zai-signup": "https://z.ai/model-api", "zai-keys": "https://z.ai/manage-apikey/apikey-list",
         "node": "https://nodejs.org/"}
# set-up steps the window can start, each in a console of its own (the user watches and answers there)
STEPS = {"codex-cli": "npm install -g @openai/codex",
         "codex-plugin": "claude plugin marketplace add openai/codex-plugin-cc && claude plugin install " + PLUGIN_ID,
         "codex-login": "codex login"}
BRAND = {"glm": "GLM", "codex": "Codex"}

# the way back to Claude (the panel asks it); %(via)s is GLM or Codex
BACK = {"window": RETURN_TITLE, "title": "Usage Limit Reset: %(folder)s",
        "body": "Claude's limit has reset. Continue <strong>%(session)s</strong> with Claude, from where it stopped?",
        "note": '<p class="hint">Continue With Claude opens it in a new Claude window, then closes the %(via)s one. '
                '%(later)s</p>',
        "no": "Not Now", "yes": "Continue With Claude"}

# The question is the app's own themed modal (mindmap design.md "No Native Popups"), drawn in a
# WebView2 window by pywebview, which the installer puts in for the Agent Panel. Same
# Spark tokens as agent-panel.html. Shape per design-dashboard.md "Modals": a title naming the
# thing, an X, the body with an amber callout for the side effect, Cancel and confirms that
# name their action, side by side and equally wide. Above the title a brand line says the window
# comes from claude-plus-fa, with the Agent Panel's purple octopus in its asking pose (the maintainer,
# 2026-11-10: so the user knows where the question comes from).
STYLE = """<style>
:root { --bg: #f4f6fb; --panel: #ffffff; --surface2: #eef2fb; --line: #e2e8f5; --text: #0a1a40; --muted: #4b5672;
  --blue: #1b4df5; --on-accent: #ffffff; --amber-tint: #fff7e6; --amber-line: #f5d9a8; --amber-text: #7a4b00;
  --red-tint: #fdecec; --red-line: #f3c4c4; --red-text: #a01818; --green: #10845a;
  --glow: 0 4px 14px rgba(27, 77, 245, .35); --r-ctl: 10px; --r-small: 6px; }
*, *::before, *::after { box-sizing: border-box; margin: 0; }
html, body { height: 100%; }
body { background: var(--panel); color: var(--text); font: 400 13px/1.45 "Segoe UI", system-ui, sans-serif;
  display: flex; flex-direction: column; overflow: hidden; user-select: none; cursor: default;
  border: 1px solid var(--line); }
button { font: inherit; cursor: pointer; }
button:disabled { cursor: default; opacity: .55; }
[hidden] { display: none !important; }
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
main { flex: none; padding: 16px 20px; display: flex; flex-direction: column; gap: 12px; }
.hint { color: var(--muted); }
.note { padding: 8px 12px; background: var(--amber-tint); border: 1px solid var(--amber-line);
  border-radius: var(--r-ctl); color: var(--amber-text); }
.fail { padding: 8px 12px; background: var(--red-tint); border: 1px solid var(--red-line);
  border-radius: var(--r-ctl); color: var(--red-text); overflow-wrap: anywhere; user-select: text; }
.steps { padding: 12px 14px; background: var(--surface2); border-radius: var(--r-ctl); display: flex;
  flex-direction: column; gap: 8px; }
.steps h2 { font-size: 13px; font-weight: 600; }
.steps ol { padding-left: 20px; display: flex; flex-direction: column; gap: 6px; }
.steps li.done { color: var(--muted); }
.steps li.done::after { content: "  \\2713 Done"; color: var(--green); font-weight: 600; }
.steps a { color: var(--blue); text-decoration: underline; cursor: pointer; }
.step-btn { margin-left: 6px; padding: 1px 10px; min-height: 24px; border: 1px solid var(--line); border-radius: var(--r-small);
  background: var(--panel); color: var(--text); font-weight: 600; }
.step-btn:hover { border-color: var(--blue); }
code { font: 12px Consolas, monospace; }
footer { display: flex; gap: 12px; padding: 14px 20px; border-top: 1px solid var(--line); }
.btn { flex: 1 1 0; min-width: 0; min-height: 34px; padding: 6px 10px; border: 1px solid var(--line); border-radius: var(--r-ctl);
  background: var(--panel); color: var(--text); font-weight: 600; white-space: nowrap; transition: transform .16s ease-out; }
.btn:hover:not(:disabled) { background: var(--surface2); }
.btn[aria-expanded="true"] { border-color: var(--blue); color: var(--blue); }
.btn.go { background: var(--blue); border-color: var(--blue); color: var(--on-accent); box-shadow: var(--glow); }
.btn.go:hover:not(:disabled) { filter: brightness(1.08); background: var(--blue); }
.btn:active:not(:disabled), #x:active { transform: scale(.97); }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } .btn:active, #x:active { transform: none; } }
</style>"""

HEAD = """<!doctype html><html lang="en"><head><meta charset="utf-8">%(style)s</head>
<body role="dialog" aria-labelledby="t" aria-describedby="d">
<div class="brand">%(mascot)s<span>claude-plus-fa</span></div>
<header><h1 id="t">%(title)s</h1>
  <button id="x" aria-label="Close"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="2.4" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button></header>"""

SIZE_JS = """
// the asking octopus, its 4 frames played as in the Agent Panel (550 ms each); frame 0 only under reduced motion
const me = document.getElementById("me"), still = matchMedia("(prefers-reduced-motion: reduce)");
if (me) { let f = 0; setInterval(() => { f = still.matches ? 0 : (f + 1) % 4; me.style.backgroundPositionX = f * 100 / 3 + "%"; }, 550); }
// the window is sized to its content (again after every change of it), so nothing is cut at any display scale
// one resize at a time: each measures after the last one has landed, so two quick changes never add up
let sizing = Promise.resolve();
function fit() {
  return sizing = sizing.then(async () => {
    const need = [...document.body.children].filter(e => e.tagName !== "SCRIPT")
                                           .reduce((h, e) => h + e.getBoundingClientRect().height, 0) + 2;
    const more = Math.ceil(need - innerHeight);
    if (more || !fit.shown) await window.pywebview.api.fit(more);
    fit.shown = true;
  });
}
"""

# the way back: one question, two buttons
BACK_PAGE = HEAD + """
<main><p id="d">%(body)s</p>
  %(note)s</main>
<footer><button class="btn" id="no">%(no)s</button><button class="btn go" id="yes">%(yes)s</button></footer>
<script>""" + SIZE_JS + """
const answer = yes => window.pywebview && window.pywebview.api.answer(yes);
document.getElementById("yes").onclick = () => answer(true);
document.getElementById("no").onclick = () => answer(false);
document.getElementById("x").onclick = () => answer(false);
addEventListener("keydown", e => { if (e.key === "Escape") answer(false); });
addEventListener("pywebviewready", async () => { await fit(); document.getElementById("yes").focus(); });
</script></body></html>"""

# the way out: GLM or Codex, each either ready (Continue With ...) or with its set-up steps (Set Up ...)
OUT_PAGE = HEAD + """
<main><p id="d">Claude's usage limit ended this session's turn. Continue it in a new window, from where it
  stopped, with GLM (z.ai) or Codex (OpenAI)?</p>
  <section class="steps" id="steps-glm" hidden>
    <h2>Set up GLM</h2>
    <ol><li>Sign up at <a data-link="zai-signup">z.ai</a>.</li>
      <li>Create a key under <a data-link="zai-keys">API Keys</a>.</li>
      <li>Paste it into the key file in place of <code>PASTE_YOUR_KEY_HERE</code> and save it.
        <button class="step-btn" data-do="key-file">Open Key File</button></li></ol>
    <p class="hint">Continue With GLM turns on as soon as the file holds a key.</p>
  </section>
  <section class="steps" id="steps-codex" hidden>
    <h2>Set up Codex</h2>
    <ol><li id="s-cli">Install the Codex CLI. <span id="s-cli-node" hidden>It needs <a data-link="node">Node.js</a> first.</span>
        <button class="step-btn" data-do="codex-cli">Install</button></li>
      <li id="s-plugin">Add the Codex plugin to Claude Code. <button class="step-btn" data-do="codex-plugin">Add Plugin</button></li>
      <li id="s-login">Sign in to Codex with your ChatGPT account; your browser opens.
        <button class="step-btn" data-do="codex-login">Sign In</button></li></ol>
    <p class="hint">Each button runs its step in a window of its own. Continue With Codex turns on once all three are done.</p>
  </section>
  <p class="note">GLM sends the conversation so far to z.ai; Codex sends it to OpenAI.</p>
  <p class="hint" id="work" role="status" hidden>Moving the conversation to Codex&hellip;</p>
  <p class="fail" id="fail" role="alert" hidden></p></main>
<footer><button class="btn" id="no">Cancel</button><button class="btn" id="glm"></button><button class="btn" id="codex"></button></footer>
<script>""" + SIZE_JS + """
const $ = id => document.getElementById(id), api = () => window.pywebview.api;
let st = null, open = window.OPEN || null, busy = false, first = true;
function render() {
  for (const k of ["glm", "codex"]) {
    const ready = k === "glm" ? st.glm : st.codex.ready, b = $(k), name = k === "glm" ? "GLM" : "Codex";
    if (ready && open === k) open = null;
    b.textContent = (ready ? "Continue With " : "Set Up ") + name;
    b.className = "btn" + (ready ? " go" : "");
    if (ready) b.removeAttribute("aria-expanded"); else b.setAttribute("aria-expanded", open === k);
    b.disabled = busy;
    $("steps-" + k).hidden = ready || open !== k;
  }
  $("s-cli").className = st.codex.cli ? "done" : "";
  $("s-plugin").className = st.codex.plugin ? "done" : "";
  $("s-login").className = st.codex.login ? "done" : "";
  $("s-cli-node").hidden = st.codex.cli || st.codex.npm;
  for (const b of document.querySelectorAll(".step-btn")) b.hidden = b.closest("li") && b.closest("li").className === "done";
  // a step needs the ones before it
  document.querySelector('[data-do="codex-plugin"]').disabled = !st.codex.cli;
  document.querySelector('[data-do="codex-login"]').disabled = !st.codex.cli;
  document.querySelector('[data-do="codex-cli"]').disabled = !st.codex.npm;
  $("no").disabled = busy;
  fit();
}
async function poll() {
  st = await api().state();
  render();
  if (first) {
    first = false;
    const f = [$("glm"), $("codex")].find(b => b.classList.contains("go")) || $("glm");
    f.focus();
    if (window.FAIL) { $("fail").textContent = window.FAIL; $("fail").hidden = false; render(); }
    if (window.PRESS) choose(window.PRESS);  // the preview only: press a button on its own
  }
}
async function choose(k) {
  if (busy) return;
  const ready = k === "glm" ? st.glm : st.codex.ready;
  $("fail").hidden = true;
  if (!ready) { open = open === k ? null : k; render(); return; }
  busy = true; $("work").hidden = k !== "codex"; render();
  const r = await api().go(k);
  if (r && r.ok) return;  // the window closes
  busy = false; $("work").hidden = true;
  $("fail").textContent = (r && r.error) || "It could not be started.";
  $("fail").hidden = false; render();
}
const no = () => { if (!busy) api().answer(null); };
$("glm").onclick = () => choose("glm");
$("codex").onclick = () => choose("codex");
$("no").onclick = no; $("x").onclick = no;
addEventListener("keydown", e => { if (e.key === "Escape") no(); });
document.addEventListener("click", e => {
  const a = e.target.closest("a[data-link]");
  if (a) { e.preventDefault(); api().open_link(a.dataset.link); }
  const s = e.target.closest("[data-do]");
  if (s && !s.disabled) api().step(s.dataset.do);
});
addEventListener("pywebviewready", async () => { await poll(); setInterval(() => { if (!busy) poll(); }, 2000); });
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


def page_head(page, title):
    return page.replace("%(style)s", STYLE).replace("%(mascot)s", mascot_html()).replace("%(title)s", title)


def come_forward(title):
    # a hook runs in the background, and Windows refuses the foreground to it
    # unless a key was pressed: a lone Alt press lets the window take it
    user32 = ctypes.windll.user32
    for _ in range(50):
        hwnd = user32.FindWindowW(None, title)
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


class Base:
    """The js_api of a themed window: the page sizes the window with fit() and closes it with close().
    The window lives in WIN, not on the object: pywebview walks the js_api object's attributes, and a window
    stored on it sent that walk into endless recursion."""
    title, forward, activate = "", True, True

    def fit(self, more):
        w = WIN[id(self)]
        w.resize(w.width, w.height + int(more))
        if not getattr(w, "_shown", False):
            w._shown = True
            user32 = ctypes.windll.user32
            before = user32.GetForegroundWindow()
            w.show()
            # pywebview's show() calls Activate(), which takes the foreground whenever Windows lets this process
            # (focus=False does not stop it: seen 2026-11-17); a window that must not take the keyboard hands
            # the foreground straight back, staying on top without it
            if not self.activate and before and user32.GetForegroundWindow() != before:
                user32.SetForegroundWindow(before)
            if self.forward and self.activate:
                threading.Thread(target=come_forward, args=(self.title,), daemon=True).start()

    def close(self):
        WIN[id(self)].destroy()


WIN = {}


def show(title, page, api, forward, activate=True, width=440):
    """Run one themed window until it is closed."""
    import webview
    api.title, api.forward, api.activate = title, forward, activate
    WIN[id(api)] = webview.create_window(title, html=page, js_api=api, width=width, height=250, frameless=True,
                                         easy_drag=False, on_top=True, hidden=True, focus=activate,
                                         background_color="#FFFFFF")
    webview.start(gui="edgechromium", private_mode=True)


def ask_themed(folder, forward=True, words=BACK, session="", later="", activate=True, via="GLM"):
    """The way back's modal; True for its confirm button, False for the other one, the X, Escape or a closed
    window. forward=False leaves the foreground alone (the preview: no key is pressed). activate=False shows it
    over every window without taking the keyboard: the way back comes at a moment the user did not pick, and a
    key they were typing elsewhere must never answer it."""
    result = {"yes": False}

    class Api(Base):
        def answer(self, yes):
            result["yes"] = bool(yes)
            self.close()

    fill = {"folder": html.escape(folder), "session": html.escape(session), "later": html.escape(later),
            "via": html.escape(via)}
    page = page_head(BACK_PAGE, words["title"] % fill)
    for key in ("body", "note", "no", "yes"):
        page = page.replace("%%(%s)s" % key, words[key] % fill)
    show(words["window"], page, Api(), forward, activate)
    return result["yes"]


# ---------- what is ready ----------
def has_key():
    """A real z.ai key in the file glm.cmd reads; without one, Continue With GLM would only send the
    conversation to z.ai to be refused, so the window offers the set-up steps instead. Never shown or logged."""
    try:
        for line in open(KEY_FILE, encoding="utf-8"):
            name, _, value = line.strip().partition("=")
            if name == "CLAUDEX_KEY":
                value = value.strip().strip('"')
                return bool(value) and value != PLACEHOLDER
    except OSError:
        pass
    return False


def version_key(name):
    return tuple(int(x) if x.isdigit() else -1 for x in re.split(r"[.\-+]", name))


def companion():
    """The Codex plugin's codex-companion.mjs, from its newest cached version, if the plugin is installed."""
    try:
        with open(INSTALLED_PLUGINS, encoding="utf-8") as f:
            if PLUGIN_ID not in (json.load(f).get("plugins") or {}):
                return None
        versions = sorted(os.listdir(PLUGIN_CACHE), key=version_key, reverse=True)
    except (OSError, ValueError, AttributeError):
        return None
    for v in versions:
        script = os.path.join(PLUGIN_CACHE, v, "scripts", "codex-companion.mjs")
        if os.path.isfile(script):
            return script
    return None


def clean_env():
    """The environment without the markers of the Claude session the hook runs in: a program started from here
    is not part of that session (CLAUDE_CODE_CHILD_SESSION stops a resumed session saving its transcript, and
    the Codex plugin's CODEX_COMPANION_* would point its tools at this session)."""
    return {k: v for k, v in os.environ.items()
            if not k.upper().startswith(("CLAUDE", "ANTHROPIC", "CODEX_COMPANION"))}


def codex_state():
    """Which of Codex's three pieces are there: the CLI (and node, which the plugin's scripts need), the
    plugin, the sign-in (the CLI's own `codex login status`, exit 0 when signed in). npm: whether the CLI
    could be installed from here."""
    cli = bool(shutil.which("codex") and shutil.which("node"))
    login = False
    if cli:
        try:
            r = subprocess.run("codex login status", capture_output=True, timeout=15, env=clean_env(),
                               creationflags=NO_WINDOW, stdin=subprocess.DEVNULL, shell=True)
            login = r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            pass
    plugin = companion() is not None
    return {"cli": cli, "plugin": plugin, "login": login, "npm": bool(shutil.which("npm")),
            "ready": cli and plugin and login}


def new_console(cmd, cwd=None, env=None):
    """cmd in a new console window that stays open (cmd /k), broken away from the hook's job so it outlives it.
    A string, not a list: list quoting escapes inner quotes as \\" which cmd does not understand."""
    flags = subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        return subprocess.Popen("cmd /k " + cmd, cwd=cwd, env=env,
                                creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB)
    except OSError:  # the job forbids breakaway
        return subprocess.Popen("cmd /k " + cmd, cwd=cwd, env=env, creationflags=flags)


def open_key_file():
    """Notepad on the key file, created with the placeholder line if it is missing."""
    if not os.path.exists(KEY_FILE):
        os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
        with open(KEY_FILE, "w", encoding="utf-8") as f:
            f.write('CLAUDEX_KEY="%s"\n' % PLACEHOLDER)
    subprocess.Popen(["notepad.exe", KEY_FILE])


# ---------- Codex: the way out ----------
def codex_home():
    return os.environ.get("CODEX_HOME") or os.path.join(HOME, ".codex")


def same_path(a, b):
    """Codex records an imported file with the \\\\?\\ prefix (seen 2026-11-17, Codex 0.164.0), the plugin
    compares it with Node's realpath, which has none, and so reports a finished import as failed."""
    strip = lambda p: os.path.normcase(os.path.normpath(p[4:] if p.startswith("\\\\?\\") else p))
    return strip(a) == strip(b)


def ledger():
    """Codex's record of every session it imported: [{source_path, content_sha256, imported_thread_id, ...}]."""
    try:
        with open(os.path.join(codex_home(), "external_agent_session_imports.json"), encoding="utf-8") as f:
            records = json.load(f).get("records") or []
    except (OSError, ValueError, AttributeError):
        return []
    return [r for r in records if isinstance(r, dict) and isinstance(r.get("imported_thread_id"), str)]


def imported_thread(source, sha):
    """The Codex thread made from this transcript at this content, from Codex's own import ledger."""
    hits = [r for r in ledger() if r.get("content_sha256") == sha and same_path(str(r.get("source_path", "")), source)]
    return hits[-1]["imported_thread_id"] if hits else None


def thread_file(thread):
    """Where Codex keeps a thread: ~/.codex/sessions/YYYY/MM/DD/rollout-<time>-<thread>.jsonl."""
    hits = glob.glob(os.path.join(codex_home(), "sessions", "**", "rollout-*%s.jsonl" % thread), recursive=True)
    return max(hits, key=os.path.getmtime) if hits else None


# What Codex is given (2026-11-21). Codex's import takes every turn of the file in order, every compaction's
# summary included, and ignores the compact boundaries: a long session (414 MB, 23 compactions) became a
# 1,591,013-token thread for a 500,000-token model, whose own compaction then failed on every prompt
# ("Codex ran out of room in the model's context window"). So Codex gets a copy holding only what Claude
# itself still had: the last compact boundary, its summary, the messages the compaction preserved and
# everything after; and if that is still over CODEX_BUDGET, the summary and the most recent turns that fit.
# The Claude transcript itself is never cut.
# CODEX_BUDGET: the smallest window a Codex model offers is 272,000 tokens (~/.codex/models_cache.json,
# 2026-11-21), 95 % of it usable; Codex's instructions and tools take ~15,000. 100,000 of carried history
# leaves ~140,000 to work before Codex compacts, and its compaction still fits in the window. Claude
# itself restarts after a compaction with ~35,000 and works up to ~200,000.
CODEX_BUDGET = 100000  # tokens as Codex counts them: characters / 4
CODEX_CEILING = 2 * CODEX_BUDGET  # an import Codex counts above this is cut again, smaller
RESULT_MAX = 4000  # characters of a tool result Codex's import keeps (Codex CLI 0.164.0)


def codex_cost(o):
    """Characters Codex's import makes of one transcript line: user and assistant text and tool calls whole,
    tool results cut to RESULT_MAX; thinking, images, attachments, meta and sidechain lines dropped (all seen
    2026-11-21). Over the real 414 MB transcript this gave 1,788,531 tokens where Codex counted 1,591,013."""
    msg = o.get("message")
    if o.get("isSidechain") or o.get("isMeta") or o.get("type") not in ("user", "assistant") or not isinstance(msg, dict):
        return 0
    content = msg.get("content")
    if isinstance(content, str):
        return len(content) + 20
    n = 0
    for b in content if isinstance(content, list) else []:
        kind = b.get("type") if isinstance(b, dict) else None
        if kind == "text":
            n += len(b.get("text") or "") + 20
        elif kind == "tool_use":
            n += len(json.dumps(b.get("input"), ensure_ascii=False)) + len(b.get("name") or "") + 40
        elif kind == "tool_result":
            n += min(len(text_of(b.get("content"))), RESULT_MAX) + 60
    return n


def is_prompt(o):
    """A prompt the user typed: where a turn starts (not a tool result, a meta line or a compaction summary)."""
    msg = o.get("message")
    if o.get("type") != "user" or o.get("isMeta") or o.get("isSidechain") or o.get("isCompactSummary") \
            or not isinstance(msg, dict):
        return False
    content = msg.get("content")
    return isinstance(content, str) or any(isinstance(b, dict) and b.get("type") == "text" for b in content or [])


def codex_cut(source, budget=CODEX_BUDGET):
    """The lines of the transcript Codex is given, as indexes into rows [(offset, length)], or None when it can
    take the whole file (no compaction and within the budget)."""
    rows, cost, kind, ids, last, meta = [], [], [], {}, None, None
    with open(source, "rb") as f:
        offset = 0
        for raw in f:
            try:
                o = json.loads(raw)
            except ValueError:
                o = None
            o = o if isinstance(o, dict) else {}
            k = ("boundary" if o.get("subtype") == "compact_boundary" and not o.get("isSidechain") else
                 "summary" if o.get("isCompactSummary") else "prompt" if is_prompt(o) else "")
            if k == "boundary":
                last, meta = len(rows), o.get("compactMetadata")
            if isinstance(o.get("uuid"), str):
                ids[o["uuid"]] = (len(rows), o.get("parentUuid"))
            rows.append((offset, len(raw)))
            cost.append(codex_cost(o))
            kind.append(k)
            offset += len(raw)
    room = budget * 4
    head, preserved, start = [], [], 0
    if last is not None:
        head = [last]
        start = last + 1
        if start < len(rows) and kind[start] == "summary":
            head.append(start)
            start += 1
        # the messages the compaction kept as they were (Claude Code puts them after the summary): walk the
        # chain back from its tail to its head; nothing is guessed when the chain is broken
        seg = (meta or {}).get("preservedSegment") or {}
        at, chain = seg.get("tailUuid"), []
        while at in ids and ids[at][0] < last and len(chain) < len(rows):
            chain.append(ids[at][0])
            if at == seg.get("headUuid"):
                preserved = chain[::-1]
                break
            at = ids[at][1]
        room -= sum(cost[i] for i in head)
    body = list(range(start, len(rows)))
    if sum(cost[i] for i in preserved + body) <= room:
        return None if last is None else (rows, head + preserved + body)
    # too big even so: the most recent turns that fit, each from its prompt on
    tail = [0] * (len(rows) + 1)
    for i in range(len(rows) - 1, start - 1, -1):
        tail[i] = tail[i + 1] + cost[i]
    fits = [i for i in body if kind[i] == "prompt" and tail[i] <= room]
    if fits:
        return rows, head + list(range(fits[0], len(rows)))
    # the last turn alone is over: its prompt and the latest of what follows that fits
    prompts = [i for i in body if kind[i] == "prompt"]
    p = prompts[-1] if prompts else start
    room -= cost[p]
    t = next((i for i in range(p + 1, len(rows)) if tail[i] <= room), len(rows))
    return rows, head + [p] + list(range(t, len(rows)))


def write_cut(source, rows, keep, folder):
    """A copy of the chosen lines in folder, in that order; the transcript itself is only read."""
    os.makedirs(folder, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    copy, n = os.path.join(folder, stamp + ".jsonl"), 1
    while os.path.exists(copy):
        n += 1
        copy = os.path.join(folder, "%s-%d.jsonl" % (stamp, n))
    with open(source, "rb") as f, open(copy, "wb") as out:
        for i in keep:
            f.seek(rows[i][0])
            line = f.read(rows[i][1])
            out.write(line if line.endswith(b"\n") else line + b"\n")
    return copy


def imported_tokens(thread):
    """The size Codex gave the imported history (the import's last token_count), or None."""
    tokens, path = None, thread_file(thread)
    for o in read_from(path, 0) if path else []:
        p = o.get("payload") or {}
        if o.get("type") == "event_msg" and p.get("type") == "token_count":
            tokens = ((p.get("info") or {}).get("total_token_usage") or {}).get("total_tokens", tokens)
    return tokens


def transfer(transcript, cwd):
    """Import a Claude transcript into a resumable Codex thread with the Codex plugin's own transfer
    (codex-companion.mjs transfer --source <jsonl>; it runs without Claude's model), cut to what fits (see
    CODEX_BUDGET). Returns (thread id, whether the history was cut); raises RuntimeError with the reason."""
    source = os.path.realpath(transcript)
    budget = CODEX_BUDGET
    for _ in range(2):
        cut = codex_cut(source, budget)
        copy = None
        if cut:
            copy = write_cut(source, cut[0], cut[1], os.path.join(os.path.splitext(source)[0], "codex-transfer"))
        thread = import_once(copy or source, cwd)
        tokens = imported_tokens(thread)
        if not tokens or tokens <= CODEX_CEILING:
            return thread, bool(cut)
        # Codex counted far more than the estimate (a later import that keeps more): once more, smaller
        budget = int(budget * CODEX_BUDGET / tokens)
    raise RuntimeError("The conversation is too long for Codex (%d tokens after cutting it)." % tokens)


def import_once(source, cwd):
    """One import through the plugin; the thread id."""
    script = companion()
    if not script:
        raise RuntimeError("The Codex plugin is not installed.")
    if any(same_path(str(r.get("source_path", "")), source) for r in ledger()):
        # Codex imports a file once: a second import of the same path is skipped, even after the transcript
        # grew (seen 2026-11-17). A session that comes to Codex again is imported from a copy, kept in the
        # session's own folder (<projects>/<folder>/<id>/), where neither Claude Code nor the panel takes it
        # for a session; the plugin only imports files under ~/.claude/projects.
        folder = os.path.join(os.path.splitext(source)[0], "codex-transfer")
        os.makedirs(folder, exist_ok=True)
        copy = os.path.join(folder, time.strftime("%Y-%m-%d_%H%M%S") + ".jsonl")
        shutil.copyfile(source, copy)
        source = copy
    with open(source, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    try:
        r = subprocess.run(["node", script, "transfer", "--source", source, "--json"], cwd=cwd, env=clean_env(),
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
                           creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError("Codex's transfer did not run (%s)." % e)
    thread = None
    try:
        thread = json.loads(r.stdout).get("threadId")
    except (ValueError, AttributeError):
        pass
    thread = thread or imported_thread(source, sha)  # the plugin's own lookup misses on Windows (same_path)
    if not thread:
        lines = [l for l in (r.stderr or "").splitlines() if l.strip() and not l.startswith(("(node:", "(Use `node"))]
        raise RuntimeError(lines[-1] if lines else "Codex did not import the conversation.")
    return thread


# ---------- the session's name: the Codex window's title and its thread's name ----------
NAME_MAX = 80  # characters of the name a window title carries
GLYPH = "✳ "  # "✳ ", Claude Code's idle status glyph, before the name in GLM's pinned title


def session_name(transcript):
    """The name the Agent Panel's card shows for the session (agent-panel.pyw parse(), kept the same): the last
    custom-title, else the last ai-title, else the first real prompt. "" when none can be read."""
    try:
        size = os.path.getsize(transcript)
        with open(transcript, "rb") as f:
            head = f.read(262144)
            f.seek(max(0, size - 1048576))
            tail = f.read()
    except (OSError, TypeError):
        return ""
    custom = ai = None
    for line in reversed(tail.split(b"\n")):
        try:
            if custom is None and b'"custom-title"' in line:
                custom = json.loads(line).get("customTitle")
            elif ai is None and b'"ai-title"' in line:
                ai = json.loads(line).get("aiTitle")
        except ValueError:
            pass
        if custom:
            break
    name = custom or ai
    if not name:
        for line in head.split(b"\n"):
            if b'"type":"user"' not in line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("type") == "user" and not o.get("isMeta") and not o.get("isSidechain"):
                t = " ".join(text_of((o.get("message") or {}).get("content")).split())
                if t and not t.startswith("<") and not t.startswith("This session is being continued"):
                    name = t[:160]
                    break
    return " ".join(str(name or "").split())


def window_name(name):
    """A name cmd can carry in a title and a quoted argument: the characters it would act on removed (the
    panel's clean() removes the same ones)."""
    return re.sub(r'[&|<>^"%]', "", name or "").strip()[:NAME_MAX]


def codex_rpc(calls, cwd, wait=30):
    """Codex's app server (`codex app-server`, stdio): initialize, then each (method, params) in turn. Their results
    in order, None for one that failed or got no answer (the ones after it are not sent)."""
    out = [None] * len(calls)
    if not calls:
        return out
    try:
        p = subprocess.Popen("codex app-server", shell=True, cwd=cwd, env=clean_env(), stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW,
                             text=True, encoding="utf-8", errors="replace")
    except OSError:
        return out
    lines = queue.Queue()
    threading.Thread(target=lambda: [lines.put(l) for l in p.stdout] and None, daemon=True).start()
    end = time.time() + wait

    def answer(i):
        while time.time() < end:
            try:
                o = json.loads(lines.get(timeout=max(0.1, end - time.time())))
            except queue.Empty:
                return None
            except ValueError:
                continue
            if o.get("id") == i:
                return o
        return None
    msgs = [{"id": 0, "method": "initialize", "params": {"clientInfo": {
                "name": "claude-plus-fa", "title": "claude-plus-fa", "version": "1"},
                "capabilities": {"experimentalApi": False}}},
            {"method": "initialized", "params": {}}]
    msgs += [{"id": i + 1, "method": m, "params": params} for i, (m, params) in enumerate(calls)]
    try:
        for msg in msgs:
            p.stdin.write(json.dumps(msg) + "\n")
            p.stdin.flush()
            if "id" in msg:
                got = answer(msg["id"])
                if not got or "error" in got:
                    break
                if msg["id"]:
                    out[msg["id"] - 1] = got.get("result") or {}
        p.stdin.close()
        p.wait(10)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    if p.poll() is None:  # cmd -> codex.cmd -> node -> codex.exe: the whole tree
        subprocess.run("taskkill /T /F /PID %d" % p.pid, capture_output=True, creationflags=NO_WINDOW)
    return out


def name_call(thread, name):
    """Name the Codex thread the way Codex itself does it (its /rename): thread/name/set writes
    ~/.codex/session_index.jsonl and the thread's title in its state database; Codex's footer and terminal title
    then show it, and Codex does not rename it on the first turn."""
    return ("thread/name/set", {"threadId": thread, "name": name})


def trust_root(cwd):
    """The folder Codex asks to trust for cwd, in the form its config keys it: the git repository's root (the
    nearest folder above or at cwd holding .git), else cwd; lower case with backslashes (os.path.normcase), as Codex
    writes `[projects.'c:\\users\\...']` when the user answers "Trust and continue" (Codex CLI 0.171.0)."""
    here = os.path.abspath(cwd)
    while True:
        if os.path.exists(os.path.join(here, ".git")):
            return os.path.normcase(here)
        up = os.path.dirname(here)
        if up == here:
            return os.path.normcase(os.path.abspath(cwd))
        here = up


def trust_known(root):
    """Whether Codex's config already says how far it trusts root (trusted or untrusted: the user's own answer is
    never replaced). True also when the config cannot be read, so a broken file is left to Codex."""
    try:
        import tomllib
        with open(os.path.join(codex_home(), "config.toml"), "rb") as f:
            projects = tomllib.load(f).get("projects") or {}
    except FileNotFoundError:
        return False
    except Exception:
        return True
    return any(os.path.normcase(k) == root and isinstance(v, dict) and "trust_level" in v
               for k, v in projects.items())


def trust_call(cwd):
    """Codex stops a new window on "Trust this folder?" for a git repository it has no answer for, with the
    continue prompt waiting behind it. The folder is the one the user was working in with Claude, so it is trusted
    the way Codex's own "Trust and continue" does it (config/value/write of projects.<root>.trust_level); None when
    Codex already has an answer for it."""
    root = trust_root(cwd)
    if trust_known(root):
        return None
    return ("config/value/write", {"keyPath": "projects.%s.trust_level" % json.dumps(root, ensure_ascii=False),
                                   "value": "trusted", "mergeStrategy": "replace"})


def prepare_codex(thread, name, cwd):
    """Before Codex's window opens: its thread named like the session and its folder trusted, in one app-server
    run. Returns (named, trusted): whether each was done; trusted is None when nothing needed writing."""
    trust = trust_call(cwd)
    calls = ([name_call(thread, name)] if name else []) + ([trust] if trust else [])
    got = codex_rpc(calls, cwd)
    named = bool(name) and got[0] is not None
    trusted = None if not trust else got[-1] is not None
    return named, trusted


# ---------- the processes of this PC (the old Claude window's work, the Codex window's console) ----------
class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                ("szExeFile", ctypes.c_wchar * 260)]


FT_EPOCH = 116444736000000000  # 1970-01-01 as a FILETIME


def procs():
    """{pid: (parent pid, exe name)} for every running process, from one Toolhelp snapshot."""
    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
    snap = k32.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
    out = {}
    if not snap or snap == wt.HANDLE(-1).value:
        return out
    e = PROCESSENTRY32W(dwSize=ctypes.sizeof(PROCESSENTRY32W))
    more = k32.Process32FirstW(wt.HANDLE(snap), ctypes.byref(e))
    while more:
        out[e.th32ProcessID] = (e.th32ParentProcessID, e.szExeFile)
        more = k32.Process32NextW(wt.HANDLE(snap), ctypes.byref(e))
    k32.CloseHandle(wt.HANDLE(snap))
    return out


def cmdline(pid):
    """A process's command line (NtQueryInformationProcess, ProcessCommandLineInformation); "" if unreadable."""
    k32, nt = ctypes.windll.kernel32, ctypes.windll.ntdll
    k32.OpenProcess.restype = wt.HANDLE
    h = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        need = wt.ULONG(0)
        nt.NtQueryInformationProcess(wt.HANDLE(h), 60, None, 0, ctypes.byref(need))
        if not need.value:
            return ""
        buf = ctypes.create_string_buffer(need.value)
        if nt.NtQueryInformationProcess(wt.HANDLE(h), 60, buf, need, ctypes.byref(need)) != 0:
            return ""
        length = struct.unpack_from("H", buf.raw, 0)[0]
        ptr = struct.unpack_from("P", buf.raw, ctypes.sizeof(ctypes.c_void_p))[0]  # UNICODE_STRING.Buffer
        return ctypes.wstring_at(ptr, length // 2) if ptr else ""
    finally:
        k32.CloseHandle(wt.HANDLE(h))


def under(pid, root, table, depth=16):
    """pid is root or started under it."""
    for _ in range(depth):
        if not pid:
            return False
        if pid == root:
            return True
        pid = table.get(pid, (0, ""))[0]
    return False


def unix_born(pid):
    b = born(pid)
    return (b - FT_EPOCH) / 1e7 if b else None


class Console:
    """The console process of a window started through Windows Terminal (wt.exe hands it over and exits): a pid
    with poll(), as Popen has. pid None: it was not found; poll() then cannot tell and says it still runs."""
    def __init__(self, pid):
        self.pid = pid
        self.started = born(pid) if pid else None

    def poll(self):
        if self.pid is None:
            return None
        return None if alive(self.pid) and born(self.pid) == self.started else 0


def quoted_dir(cwd):
    """A folder as it goes between double quotes on a command line: no trailing separator, since "C:\\" would end
    the quoted argument with \\" (a drive root becomes C:\\.)."""
    here = cwd.rstrip("\\/") or cwd
    return here + ("\\." if here.endswith(":") else "")


def open_window(cmd, cwd, title, mark, wait=15):
    """cmd in a new window of its own titled `title`, kept open (cmd /k). With Windows Terminal: a new wt window
    whose title stays put (--suppressApplicationTitle: Codex sets the terminal's title, which would replace ours);
    its cmd is found by `mark` in its command line, since wt.exe itself exits at once. Without it: a new console
    whose title cmd sets first."""
    terminal = shutil.which("wt")
    if terminal:
        here = quoted_dir(cwd)
        start = ctypes.c_ulonglong()
        ctypes.windll.kernel32.GetSystemTimeAsFileTime(ctypes.byref(start))
        try:
            subprocess.Popen('"%s" -w new new-tab --title "%s" --suppressApplicationTitle -d "%s" cmd /k %s'
                             % (terminal, title, here, cmd.replace(";", "\\;")), cwd=cwd, env=clean_env(),
                             stdin=subprocess.DEVNULL)
        except OSError:
            terminal = None
        end = time.time() + wait
        while terminal and time.time() < end:
            for p, (pp, exe) in procs().items():
                if exe.lower() == "cmd.exe" and (born(p) or 0) >= start.value and mark in cmdline(p):
                    return Console(p)
            time.sleep(0.25)
        if terminal:
            return Console(None)  # the window is there but its console was not found: never open a second
    return new_console("title %s& %s" % (title or "Codex", cmd), cwd=cwd, env=clean_env())


def open_codex(thread, cwd, skip, whole=None, name=None, work=None):
    """Codex's window on the thread, with the continue prompt, titled with the session's name; whole: the
    transcript, when Codex was given only its latest part, so Codex can look further back in it; work: what
    the old Claude window still runs (it then stays open, and the prompt says so)."""
    # Nothing may stop the window on a question with the continue prompt waiting behind it (Codex CLI 0.171.0):
    # -C names the folder, so Codex does not ask "Working directory · resume" when the folder the import recorded
    # differs from it (a session that had moved into a subfolder); check_for_update_on_startup=false keeps away the
    # "Update available" screen, whose default answer installs and closes the window. The trust question is
    # answered before (prepare_codex()). Codex titles its terminal "<thread name> | <folder>" by default: the name
    # alone, as the Claude window has it (only seen without Windows Terminal, which keeps the title it was given).
    cmd = ('codex resume %s -C "%s" -c tui.terminal_title=[\'thread-name\'] -c check_for_update_on_startup=false'
           % (thread, quoted_dir(cwd)))
    if skip:
        cmd += " --dangerously-bypass-approvals-and-sandbox"
    return open_window(cmd + ' "%s"' % codex_prompt(whole, cwd, work), cwd, window_name(name) or "Codex", thread)


def open_glm(sid, cwd, skip, name=None, work=None):
    """GLM's window (`glm --resume <sid>`, Claude Code on z.ai, writing the same transcript) with the continue prompt,
    titled with the session's name; work: what the old Claude window still runs (it then stays open, and the
    prompt says so). GLM's own CLAUDE.md loading is Claude Code's: nothing to add for it."""
    cmd = "glm --resume " + sid
    if skip:
        cmd += " --dangerously-skip-permissions"
    # "<glyph> <name>", the way Claude Code titles its own tab, pinned: the paste key takes Ctrl+V and Alt+V only in a
    # session terminal whose title has that shape, and the panel finds the session's name after the glyph
    return open_window(cmd + ' "%s"' % kept_open(CONTINUE, work), cwd, GLYPH + (window_name(name) or "GLM"), sid)


def kept_open(prompt, work):
    """The continue prompt, plus why the old Claude window stays open when it still runs work. One line, no double
    quote or percent sign (it goes through cmd)."""
    if work:
        prompt += (" The old Claude Code window of this session stays open because it still runs %s; it closes "
                   "when that has ended." % ", ".join(work))
    return re.sub(r'["%\r\n]', "", prompt)


def codex_prompt(whole=None, cwd=None, work=None):
    """Codex's first prompt. Codex does not read CLAUDE.md, so it is told to read the global and the project's
    and follow their rules, above all where things are written down, as Claude would have. One line, no double
    quote or percent sign (it goes through cmd)."""
    prompt = CONTINUE
    if whole:
        prompt += (" Only the latest part of this conversation was brought over; the whole Claude transcript is "
                   + whole + " if you need something earlier.")
    docs = [p for p in (os.path.join(HOME, ".claude", "CLAUDE.md"), cwd and os.path.join(cwd, "CLAUDE.md"),
                        cwd and os.path.join(cwd, ".claude", "CLAUDE.md")) if p and os.path.isfile(p)]
    if docs:
        prompt += (" First read %s and follow them as Claude Code would, above all their documentation rules (which "
                   "file holds the current state, what was tried and the decisions), so what is decided here is "
                   "written down the same way." % " and ".join(docs))
    return kept_open(prompt, work)


WATCH = 180  # seconds the hook watches Codex's first turn in its new window


def first_turn_error(path, offset, console, wait=WATCH):
    """Codex's error when its first turn in the new window ended with one (2026-11-21, a history too big for
    its window: "Error running remote compact task: Codex ran out of room in the model's context window..."),
    None when the turn ended well, the model has answered (the history fit: Codex runs, and the old Claude
    window may close), it is still running after wait seconds, or the window is gone."""
    end = time.time() + wait
    while True:
        gone = console.poll() is not None or time.time() > end  # then one last read
        for o in read_from(path, offset):
            p = o.get("payload") or {}
            if o.get("type") == "response_item" and (p.get("role") == "assistant" or p.get("type") in (
                    "reasoning", "function_call", "custom_tool_call", "local_shell_call")):
                return None
            if o.get("type") != "event_msg":
                continue
            if p.get("type") in ("agent_message", "agent_reasoning"):
                return None
            if p.get("type") == "error":
                return str(p.get("message") or "Codex reported an error.")
            if p.get("type") in ("task_complete", "turn_aborted"):
                e = p.get("error")
                if not e:
                    return None
                return str((e.get("message") if isinstance(e, dict) else e) or "Codex reported an error.")
        if gone:
            return None
        time.sleep(1)


def stopped_text(error):
    return ("Codex stopped with an error: \u201c%s\u201d Close its window, then continue with GLM, or Cancel "
            "and wait for Claude's limit to reset." % error.strip())


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


def born(pid):
    """A process's creation time (FILETIME as an int), None if it cannot be read: with alive() it tells the
    console the record names from a later process that got the same number."""
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    t = [ctypes.c_ulonglong() for _ in range(4)]
    got = k32.GetProcessTimes(h, *[ctypes.byref(x) for x in t])
    k32.CloseHandle(h)
    return t[0].value if got else None


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


def claude_name(pid):
    """The name Claude Code gave the session in its process's ~/.claude/sessions/<pid>.json: what its window title
    shows after the status glyph and what the Agent Panel matches window titles against (the card's name for a
    session the user named). "" when there is none."""
    try:
        with open(os.path.join(HOME, ".claude", "sessions", "%d.json" % int(pid)), encoding="utf-8") as f:
            return " ".join(str(json.load(f).get("name") or "").split())
    except (OSError, ValueError, TypeError, AttributeError):
        return ""


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


def record_switch(sid, cwd, console, orig_pid, skip, limits=None, now=None, via="glm", transcript=None, **extra):
    """Write the record of a session moved to GLM or Codex. console: the process of the new window (cmd /k);
    orig_pid: the Claude process whose turn the limit ended, looked up before the new window started (after,
    GLM's own process holds the same session and could be taken for it). extra: Codex's thread, its file and
    the file's size after the import (codex_thread, codex_file, codex_from)."""
    now = time.time() if now is None else now
    resets_at, source = reset_time(now, limits)
    rec = {"session": sid, "cwd": cwd, "folder": os.path.basename(cwd.rstrip("\\/")) or cwd, "via": via,
           "console": console, "console_born": born(console) if console else None, "orig_pid": orig_pid,
           "skip": bool(skip), "at": now, "transcript": transcript, "resets_at": resets_at, "reset_from": source}
    rec.update(extra)
    os.makedirs(RETURN_DIR, exist_ok=True)
    try:
        os.remove(watched_path(sid))  # an earlier move's: this one's is written when the hook ends
    except OSError:
        pass
    path = os.path.join(RETURN_DIR, sid + ".json")
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(rec, f)
    os.replace(path + ".tmp", path)
    return rec


def claude_model(transcript):
    """The model to resume with on the way back. Claude Code resumes a session on the model its last turn used,
    so after GLM `claude --resume` asks Anthropic for glm-4.7-flash and fails (404, seen 2026-11-17): the last
    Claude model of the transcript, else "default" (the account's default)."""
    found = None
    try:
        with open(transcript, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"assistant"' in line:
                    m = re.search(r'"model":"(claude-[^"]+)"', line)
                    if m:
                        found = m.group(1)
    except (OSError, TypeError):
        pass
    return found or "default"


def resume_model(transcript):
    """The --model a resume of this session needs, or None when it needs none: the last assistant turn was a
    claude-* model (or there is none). A session whose last turn was GLM's (or any other non-Claude model) gets
    claude_model(), so `claude --resume` never asks Anthropic for GLM's model. "<synthetic>" turns (Claude
    Code's own error lines) are no model and are skipped."""
    last = None
    try:
        with open(transcript, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"assistant"' in line:
                    m = re.search(r'"model":"([^"<][^"]*)"', line)
                    if m:
                        last = m.group(1)
    except (OSError, TypeError):
        return None
    if last is None or last.startswith("claude-"):
        return None
    return claude_model(transcript)


# ---------- carrying Codex's turns in, incrementally ----------
# Three things may carry Codex's turns into the Claude transcript: the Agent Panel (the first prompt of the
# resume it starts) and the --carry hook (SessionStart on a resume, UserPromptSubmit on a prompt typed into a
# Claude window that holds the session). Each carry takes the thread file's lines after the last one carried, up
# to its last complete line, and moves the mark there under a lock, so two never take the same turns and an early
# carry never swallows the turns that come after it (2026-11-21: a prompt typed into the old window carried
# Codex's first turn alone, and the eight turns after it never reached Claude). The mark lives in the carry file
# <session>.<thread>.carried ({"to": byte offset, "from", "by", "at"}), apart from the record, which the panel
# rewrites; a later move to Codex (a new thread) has a file of its own. A file without "to" (written before
# 2026-11-21, when a record was carried exactly once) counts as everything carried. The Agent Panel deletes carry
# files older than CLAIM_KEEP whose session has no record.
CLAIM_KEEP = 7 * 24 * 3600
SUBSTANCE = ("user_message", "agent_message", "turn_aborted", "patch_apply_end")  # an event worth carrying


def claim_path(rec):
    tag = rec.get("codex_thread") or str(int(rec.get("at") or 0))
    return os.path.join(RETURN_DIR, "%s.%s.carried" % (rec["session"], re.sub(r"[^\w-]", "", tag)))


@contextlib.contextmanager
def carry_lock(rec, wait=3.0, stale=10.0):
    """One carrier at a time (the panel and hooks run in different processes). Yields False when it could not
    be had within wait seconds; a lock older than stale seconds was left by a carrier that died."""
    path = claim_path(rec) + ".lock"
    end, got = time.time() + wait, False
    try:
        os.makedirs(RETURN_DIR, exist_ok=True)
    except OSError:
        pass
    while not got:
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            got = True
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(path) > stale:
                    os.remove(path)
                    continue
            except OSError:
                continue
            if time.time() > end:
                break
            time.sleep(0.02)
        except OSError:
            break
    try:
        yield got
    finally:
        if got:
            try:
                os.remove(path)
            except OSError:
                pass


def carry_state(rec):
    """The carry file's content: {} when nothing was carried yet; None when it cannot be read."""
    try:
        with open(claim_path(rec), encoding="utf-8") as f:
            o = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    if not isinstance(o, dict):
        return None
    if "to" not in o:  # carried exactly once, the old way: all of it
        try:
            o["to"] = os.path.getsize(rec.get("codex_file") or "")
        except OSError:
            return None
    return o


def write_state(rec, state):
    path = claim_path(rec)
    os.makedirs(RETURN_DIR, exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(path + ".tmp", path)


def read_span(path, start, stop=None):
    """The JSON lines of a Codex thread file from byte start, up to stop or else its last complete line, and
    the offset just after the last line read."""
    try:
        with open(path, "rb") as f:
            f.seek(start or 0)
            data = f.read() if stop is None else f.read(max(0, stop - (start or 0)))
    except (OSError, TypeError):
        return [], start or 0
    out, pos = [], 0
    while True:
        nl = data.find(b"\n", pos)
        if nl < 0:
            break
        try:
            out.append(json.loads(data[pos:nl]))
        except ValueError:
            pass
        pos = nl + 1
    return out, (start or 0) + pos


def substance(objs):
    return any(o.get("type") == "event_msg" and (o.get("payload") or {}).get("type") in SUBSTANCE or
               o.get("type") == "response_item" and (o.get("payload") or {}).get("type") in (
                   "function_call", "custom_tool_call", "local_shell_call") for o in objs)


def carried_to(rec):
    state = carry_state(rec)
    if state is None:
        return None
    return state.get("to", rec.get("codex_from") or 0)


def pending(rec):
    """Codex has done something since the last carry (or since the move, when nothing was carried yet)."""
    start = carried_to(rec)
    return start is not None and substance(read_span(rec.get("codex_file"), start)[0])


def take_carry(rec, who):
    """Take every Codex line not carried yet: {"start", "end", "since"} (since: when the turns before it were
    carried, else the move), or None when nothing new happened or the mark could not be had."""
    with carry_lock(rec) as got:
        if not got:
            return None
        state = carry_state(rec)
        if state is None:
            return None
        start = state.get("to", rec.get("codex_from") or 0)
        objs, end = read_span(rec.get("codex_file"), start)
        if not substance(objs):
            return None
        span = {"start": start, "end": end, "since": state.get("at") or rec.get("at") or 0}
        write_state(rec, {"to": end, "from": start, "by": who, "at": time.time(), "prev_at": state.get("at")})
    return span


def give_back(rec, span):
    """Undo a carry whose Claude window never took the session: the mark goes back, unless a later carry has
    moved it on since."""
    with carry_lock(rec) as got:
        state = carry_state(rec) if got else None
        if state and state.get("to") == span["end"]:
            write_state(rec, {"to": span["start"], "by": "given back", "at": state.get("prev_at")})


def codex_open(rec):
    """The Codex window of the record still runs (a console not found counts as running: it cannot be told)."""
    if not rec.get("console"):
        return True
    return alive(rec["console"]) and (rec.get("console_born") is None or born(rec["console"]) == rec["console_born"])


# ---------- markers beside the record ----------
def watched_path(sid):
    return os.path.join(RETURN_DIR, sid + ".watched")


def mark_watched(sid, thread):
    """The hook is ending (after its watch of Codex's first turn; at once after GLM): the old Claude window, in which
    it runs, can close without cutting it off. thread: the record's Codex thread, None for GLM."""
    try:
        with open(watched_path(sid), "w", encoding="utf-8") as f:
            json.dump({"thread": thread, "at": time.time()}, f)
    except OSError:
        pass


def watched(rec):
    try:
        with open(watched_path(rec["session"]), encoding="utf-8") as f:
            return json.load(f).get("thread") == rec.get("codex_thread")
    except (OSError, ValueError, AttributeError):
        return False


def quiet_path(sid):
    return os.path.join(RETURN_DIR, sid + ".quiet")


def set_quiet(sid, until):
    """No way-out window for this session until `until`. Written by the hook once the user has answered it (any
    answer: the same limit asks once), and by the panel when it reopens the session in Claude before the limit
    reset (the Codex window closed): its first prompt meets the limit, and Claude Code waits and goes on by itself."""
    os.makedirs(RETURN_DIR, exist_ok=True)
    with open(quiet_path(sid), "w", encoding="utf-8") as f:
        json.dump({"until": until}, f)


def seen_by_user(pid, transcript):
    """Whether the session is one a person works in: not a `claude -p` or SDK run (entrypoint sdk-cli, sdk-ts, ...),
    the runs the Agent Panel files under Headless. From the Claude process's ~/.claude/sessions/<pid>.json, else
    the transcript's last line that names an entrypoint; unknown counts as seen."""
    entry = None
    if pid:
        try:
            with open(os.path.join(HOME, ".claude", "sessions", "%d.json" % int(pid)), encoding="utf-8") as f:
                entry = json.load(f).get("entrypoint")
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    if entry is None and transcript:
        try:
            size = os.path.getsize(transcript)
            with open(transcript, "rb") as f:
                f.seek(max(0, size - 262144))
                tail = f.read()
        except (OSError, TypeError):
            tail = b""
        for line in reversed(tail.split(b"\n")):
            m = re.search(rb'"entrypoint":\s*"([^"]*)"', line)
            if m:
                entry = m.group(1).decode("utf-8", "replace")
                break
    return not str(entry or "").startswith("sdk")


def hold_dialog(sid):
    """The one way-out window of this session: a named mutex held for the hook's lifetime, so a second StopFailure
    of the same session (its main thread again) finds it taken and shows nothing. The handle, None when another
    hook holds it. Windows frees it when the hook ends, however it ends."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = wt.HANDLE
    h = k32.CreateMutexW(None, True, "Local\\claude-plus-fa-way-out-" + re.sub(r"[^\w-]", "", sid))
    if not h or ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        if h:
            k32.CloseHandle(wt.HANDLE(h))
        return None
    return h


def quiet(sid, now=None):
    try:
        with open(quiet_path(sid), encoding="utf-8") as f:
            until = float(json.load(f).get("until") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return False
    if (now or time.time()) < until:
        return True
    try:
        os.remove(quiet_path(sid))
    except OSError:
        pass
    return False


# ---------- what the old Claude window still runs ----------
BG_TAIL = 4 * 1048576  # bytes of transcript read for background commands started and ended
LATE = 20              # seconds after the Claude process started: a child born later is a tool's, not an MCP server


QUIET = 30 * 60        # seconds an agent without "finished" may go without its transcript growing before it has ended


def agent_ended(transcript, meta):
    """An agent whose meta has no "finished" has ended all the same: a Workflow's agents (subagents/workflows/wf_<run>/)
    once the run's record <session>/workflows/wf_<run>.json exists (Claude Code writes it once, when the run ends; its
    agents never get "finished"), and any agent whose transcript has not grown for QUIET. Same rule as subagents()
    in agent-panel.pyw (which also keeps an agent running while one under it grows)."""
    d = os.path.dirname(meta)
    run = os.path.basename(d)
    if run.startswith("wf_") and os.path.basename(os.path.dirname(d)) == "workflows" and \
            os.path.isfile(os.path.join(os.path.splitext(transcript)[0], "workflows", run + ".json")):
        return True
    try:
        return time.time() - os.path.getmtime(meta[:-10] + ".jsonl") >= QUIET
    except OSError:
        return False


def busy_work(sid, pid, transcript):
    """What the Claude process pid (the old window of session sid) still runs, as short phrases: subagents
    without "finished", Claude Code runs started under it that hold a session of their own (a claude -p), and
    background commands (started in the transcript since the process started, with no notification or TaskStop
    since, and a process under it to show for it). [] when nothing runs: the window may close."""
    out, started = [], unix_born(pid) or 0
    table = procs()
    if transcript:
        for root, dirs, files in os.walk(os.path.join(os.path.splitext(transcript)[0], "subagents")):
            for n in files:
                p = os.path.join(root, n)
                try:
                    if not n.endswith(".meta.json") or os.path.getmtime(p) < started - 1:
                        continue
                    with open(p, encoding="utf-8") as f:
                        m = json.load(f)
                except (OSError, ValueError):
                    continue
                if isinstance(m, dict) and not m.get("finished") and not m.get("stoppedByUser") \
                        and not agent_ended(transcript, p):
                    out.append("a subagent (%s)" % (m.get("description") or m.get("agentType") or "agent"))
    holders = {}
    for n in os.listdir(os.path.join(HOME, ".claude", "sessions")) if os.path.isdir(
            os.path.join(HOME, ".claude", "sessions")) else []:
        try:
            with open(os.path.join(HOME, ".claude", "sessions", n), encoding="utf-8") as f:
                o = json.load(f)
            holders[int(o["pid"])] = o
        except (OSError, ValueError, KeyError, TypeError):
            continue
    for p, (pp, exe) in table.items():
        o = holders.get(p)
        if p != pid and o and o.get("sessionId") != sid and under(p, pid, table) and \
                (unix_born(p) or 0) >= started:
            out.append("a Claude Code run in %s" % os.path.basename(str(o.get("cwd") or "").rstrip("\\/")))
    ids = background_ids(transcript, started)
    if ids:
        me = os.getpid()
        late = [p for p, (pp, exe) in table.items() if pp == pid and not under(me, p, table)
                and (unix_born(p) or 0) > started + LATE]
        if late:
            out.append("%d background command%s" % (len(ids), "" if len(ids) == 1 else "s"))
    return out


def background_ids(transcript, started):
    """Background commands of this process still waiting for their end: "running in background with ID: X"
    after started, without a later <task-id>X</task-id> notification or a TaskStop of X."""
    try:
        size = os.path.getsize(transcript)
        with open(transcript, "rb") as f:
            f.seek(max(0, size - BG_TAIL))
            tail = f.read().decode("utf-8", "replace")
    except (OSError, TypeError):
        return []
    ids = []
    for line in tail.split("\n"):
        m = re.search(r'"timestamp":"([^"]+)"', line)
        try:
            t = datetime.datetime.fromisoformat(m.group(1).replace("Z", "+00:00")).timestamp() if m else 0
        except ValueError:
            t = 0
        for x in re.findall(r"running in background with ID: (\w+)", line):
            if t >= started and x not in ids:
                ids.append(x)
        for x in re.findall(r"<task-id>(\w+)</task-id>", line) + (
                re.findall(r'"task_id":"(\w+)"', line) if '"name":"TaskStop"' in line else []):
            if x in ids:
                ids.remove(x)
    return ids


def carry_hook():
    """--carry, run as a SessionStart hook (matcher resume) and a UserPromptSubmit hook. When the session holds a
    Codex record with turns not carried in yet, their text (codex_back_prompt(), the long-record file rule
    included) goes into this session as additional context, which Claude Code writes into the same transcript.
    A prompt typed while the Codex window still runs carries nothing (Codex is still at work; its turns come in
    once it closes); a resume by hand carries what exists so far. The record itself stays for the panel. Silent
    and quick otherwise: most sessions have no record."""
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    sid, event = data.get("session_id"), data.get("hook_event_name") or "SessionStart"
    path = os.path.join(RETURN_DIR, "%s.json" % sid) if sid else None
    if not path or not os.path.exists(path):
        return
    if event == "SessionStart" and data.get("source") not in (None, "resume"):
        return
    try:
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return
    if rec.get("via") != "codex" or rec.get("session") != sid or not rec.get("codex_file"):
        return
    if event != "SessionStart" and codex_open(rec):
        return
    span = take_carry(rec, "hook:" + event)
    if not span:
        return
    try:
        prompt, _ = codex_back_prompt(rec, span=span)
    except Exception as e:  # the turns are taken: give them back and say so rather than lose them silently
        give_back(rec, span)
        print(json.dumps({"systemMessage": "claude-plus-fa could not read what happened in Codex (%s)." % e}))
        return
    print(json.dumps({"systemMessage": "claude-plus-fa: what happened in Codex was added to this session.",
                      "hookSpecificOutput": {"hookEventName": event, "additionalContext": prompt}}))


# ---------- Codex: the way back, what happened there ----------
PROMPT_MAX = 24000   # characters of record that go into the first prompt (a command line holds 32767)
TAIL = 12000         # of a longer record, the last part that goes into the prompt; the whole is in a file
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".next", "dist", "build", ".claude"}


def read_from(path, offset):
    """The JSON lines of a Codex thread file after offset (its size right after the import)."""
    out = []
    try:
        with open(path, "rb") as f:
            f.seek(offset or 0)
            for raw in f:
                try:
                    out.append(json.loads(raw))
                except ValueError:
                    pass  # a line still being written
    except OSError:
        pass
    return out


def codex_idle(rec):
    """Codex's last turn in the thread has ended (task_complete or turn_aborted after the last task_started), so
    nothing is cut off. A thread with no turn after the import counts as idle too."""
    state = True
    for o in read_from(rec.get("codex_file"), rec.get("codex_from")):
        t = (o.get("payload") or {}).get("type") if o.get("type") == "event_msg" else None
        if t == "task_started":
            state = False
        elif t in ("task_complete", "turn_aborted"):
            state = True
    return state


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("text"))
    return ""


def exit_codes(text):
    """Exit codes in a tool's output: Codex's code mode returns JSON with "exit_code", the plain shell tool
    "Exit code: N"."""
    return [int(x) for x in re.findall(r'(?:"exit_code"\s*:\s*|Exit code:\s*)(-?\d+)', text or "")]


def command_of(name, args):
    """The command line of a tool call, whichever form this Codex version writes."""
    if name == "exec" and isinstance(args, str):  # code mode: JavaScript calling tools.exec_command({cmd: ...})
        cmds = re.findall(r'exec_command\(\{\s*"?cmd"?\s*:\s*"((?:[^"\\]|\\.)*)"', args)
        if cmds:
            return "; ".join(json.loads('"%s"' % c) for c in cmds)
        return args.strip()
    try:
        a = json.loads(args) if isinstance(args, str) else (args or {})
    except ValueError:
        return str(args).strip()
    c = a.get("cmd") or a.get("command") or ""
    return " ".join(c) if isinstance(c, list) else str(c)


def patched_files(patch):
    return re.findall(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$|^\*\*\* Move to: (.+)$", patch or "", re.M)


def changed_since(cwd, since, skip_file=None, limit=100, visit=20000):
    """Files in the session's folder written after `since`: a command Codex ran may write files no patch event
    names. A change the user made meanwhile shows too, so the record says what this list is."""
    out, seen = [], 0
    for root, dirs, files in os.walk(cwd):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for n in files:
            seen += 1
            p = os.path.join(root, n)
            try:
                if os.path.getmtime(p) > since and p != skip_file:
                    out.append(os.path.relpath(p, cwd))
            except OSError:
                pass
            if len(out) >= limit or seen >= visit:
                return sorted(out)
    return sorted(out)


def codex_record(rec, span=None):
    """What happened in Codex, in order, read from its thread file: each user message, each Codex reply, each
    command it ran with its exit code, and the files it changed. span: the part of the file one carry holds
    (take_carry()); without it, everything after the import."""
    span = span or {"start": rec.get("codex_from") or 0, "end": None, "since": rec.get("at") or 0}
    lines, calls, files = [], {}, []
    for o in read_span(rec.get("codex_file"), span["start"], span["end"])[0]:
        p, kind = o.get("payload") or {}, o.get("type")
        t = p.get("type")
        if kind == "event_msg" and t == "user_message" and p.get("message"):
            lines.append("User: " + p["message"].strip())
        elif kind == "event_msg" and t == "agent_message" and p.get("message"):
            lines.append("Codex: " + p["message"].strip())
        elif kind == "event_msg" and t == "turn_aborted":
            lines.append("(The turn was stopped.)")
        elif kind == "response_item" and t in ("function_call", "custom_tool_call", "local_shell_call"):
            name = p.get("name") or t
            args = p.get("arguments") if "arguments" in p else p.get("input")
            if t == "local_shell_call":
                args = (p.get("action") or {})
            if name == "apply_patch":
                names = [a or b for a, b in patched_files(args if isinstance(args, str) else json.dumps(args))]
                files += names
                calls[p.get("call_id")] = len(lines)
                lines.append("Codex edited: " + ", ".join(names) if names else "Codex applied a patch.")
            else:
                calls[p.get("call_id")] = len(lines)
                lines.append("Command: " + command_of(name, args))
        elif kind == "response_item" and t in ("function_call_output", "custom_tool_call_output"):
            i = calls.get(p.get("call_id"))
            out = p.get("output")
            codes = exit_codes(text_of(out) if not isinstance(out, dict) else json.dumps(out))
            if i is not None and lines[i].startswith("Command: "):
                lines[i] += " (exit code %s)" % ", ".join(map(str, codes)) if codes else " (no exit code reported)"
        elif kind == "event_msg" and t == "patch_apply_end":
            files += list((p.get("changes") or {}).keys())
    seen, changed = set(), []
    for f in files:
        if f not in seen:
            seen.add(f)
            changed.append(f)
    if changed:
        lines.append("Files Codex changed: " + ", ".join(changed))
    if rec.get("cwd") and os.path.isdir(rec["cwd"]):
        written = changed_since(rec["cwd"], span["since"])
        if written:
            lines.append("Files in %s written since %s (by Codex or anyone else): %s"
                         % (rec.get("folder") or rec["cwd"], "the move to Codex" if span["since"] == rec.get("at")
                            else "the last record", ", ".join(written)))
    return "\n\n".join(lines) if lines else "Codex did nothing in this session."


def codex_back_prompt(rec, now=None, span=None):
    """The first prompt of the resumed Claude session: the record of what happened in Codex (span: one carry's
    part, take_carry()), so it lands in the same Claude transcript. A record longer than PROMPT_MAX is written
    whole to a file in the session's folder and the prompt carries its path and its last TAIL characters.
    Returns (prompt, file or None)."""
    later = bool(span and span["start"] > (rec.get("codex_from") or 0))
    head = ("This session went on in Codex after the last record of it. This is what happened there since:" if later
            else "While Claude's limit was out, this session continued in Codex. This is what happened there:")
    end = "Continue from where Codex stopped."
    body = codex_record(rec, span)
    if len(body) <= PROMPT_MAX:
        return "%s\n\n%s\n\n%s" % (head, body, end), None
    stamp = time.strftime("%Y-%m-%d_%H%M", time.localtime(now or time.time()))
    path = os.path.join(rec["cwd"], "codex-record_%s.md" % stamp)
    with open(path, "w", encoding="utf-8") as f:
        f.write("# What happened in Codex\n\n%s\n\n%s\n" % (head, body))
    tail = body[-TAIL:]
    tail = tail[tail.find("\n") + 1:] if "\n" in tail else tail
    return ("%s\n\nThe whole record is too long for one message; it is in %s. Read it first. Its last part:\n\n"
            "...\n%s\n\n%s" % (head, path, tail, end)), path


# ---------- the questions ----------
def ask_out(name, forward=True, transcript=None, cwd=None, preview=None, error=None):
    """The way out. Returns ("glm", None), ("codex", thread id) or (None, None). The Codex transfer runs while
    the window shows its working line, so a failure is shown there and the user can pick again.
    preview: {"glm": bool or None, "codex": dict or None, "open": "glm"/"codex"/None}; nothing is transferred,
    started or opened, and a forced value replaces the real check. error: shown in red as the window opens
    (Codex's first turn failed in the window this one opened). Returns (via, thread, whether it was cut)."""
    result = {"via": None, "thread": None, "cut": False}
    preview_on = preview is not None
    preview = preview or {}

    class Api(Base):
        def state(self):
            glm = has_key() if preview.get("glm") is None else preview["glm"]
            codex = codex_state() if preview.get("codex") is None else preview["codex"]
            return {"glm": glm, "codex": codex}

        def go(self, via):
            if via == "codex" and (not preview_on or preview.get("fail")):
                try:
                    if preview_on:  # the preview's failed transfer: the working line, then a made-up reason
                        time.sleep(2)
                        raise RuntimeError("Codex did not import the conversation.")
                    result["thread"], result["cut"] = transfer(transcript, cwd)
                except Exception as e:
                    return {"ok": False, "error": "Could not move it to Codex: %s" % e}
            result["via"] = via
            self.close()
            return {"ok": True}

        def answer(self, _):
            self.close()

        def open_link(self, name):
            if name in LINKS and not preview_on:
                os.startfile(LINKS[name])

        def step(self, name):
            if preview_on:
                return
            if name == "key-file":
                open_key_file()
            elif name in STEPS:
                new_console(STEPS[name], cwd=HOME, env=clean_env())

    page = page_head(OUT_PAGE, "Usage Limit Reached: %s" % html.escape(name))  # the session's name
    if preview.get("open"):
        page = page.replace("<script>", '<script>window.OPEN = "%s";' % preview["open"], 1)
    if preview.get("press"):
        page = page.replace("<script>", '<script>window.PRESS = "%s";' % preview["press"], 1)
    error = error or preview.get("error")
    if error:
        page = page.replace("<script>", "<script>window.FAIL = %s;" % json.dumps(stopped_text(error)), 1)
    show(TITLE, page, Api(), forward, width=540)
    return result["via"], result["thread"], result["cut"]


def ask_native(folder):
    """Only when pywebview cannot load (not installed): the Windows message box, so the offer is never lost.
    It can offer GLM alone (with a key): Codex's transfer and set-up need the themed window."""
    text = ("Claude's usage limit has been reached in %s.\n\n"
            "Continue this session with GLM (z.ai) in a new window?" % folder)
    return ctypes.windll.user32.MessageBoxW(
        None, text, "Claude Code", MB_YESNO | MB_ICONQUESTION | MB_SETFOREGROUND | MB_TOPMOST) == IDYES


def ask_return(answer_file, folder, session, later, via="GLM"):
    """The way back, asked for the Agent Panel. The answer ("continue" or "later") goes to answer_file, which
    the panel reads, so it is not lost if the panel restarts while the window is open."""
    yes = ask_themed(folder, words=BACK, session=session, later=later, activate=False, via=via)
    with open(answer_file + ".tmp", "w", encoding="utf-8") as f:
        f.write("continue" if yes else "later")
    os.replace(answer_file + ".tmp", answer_file)


def preview_args(args):
    """--glm on|off, --codex ready|cli|plugin|login (the piece that is missing; 'none' = all three), --open glm|codex"""
    opts = {"glm": None, "codex": None, "open": None, "press": None, "fail": False, "error": None}
    while args:
        k = args.pop(0)
        v = args.pop(0) if args else ""
        if k == "--glm":
            opts["glm"] = v == "on"
        elif k == "--codex":
            have = {"cli": v not in ("cli", "none"), "plugin": v not in ("plugin", "none"),
                    "login": v not in ("login", "none")}
            if v == "cli":
                have["login"] = False  # signing in needs the CLI
            opts["codex"] = dict(have, npm=True, ready=all(have.values()))
        elif k == "--open":
            opts["open"] = v
        elif k == "--press":
            opts["press"] = v
        elif k == "--stopped":  # the window as it opens again after Codex's first turn failed
            opts["error"] = ("Error running remote compact task: Codex ran out of room in the model's context "
                             "window. Start a new thread or clear earlier history before retrying.")
            args.insert(0, v) if v else None
        elif k == "--fail":
            opts["fail"] = True
            args.insert(0, v) if v else None
    return opts


def main():
    # python glm-fallback.py --preview [folder] [--glm on|off] [--codex ready|cli|plugin|login|none] [--open glm|codex]
    # [--press glm|codex] [--fail] [--stopped]: the dialog alone, its answer printed; nothing is sent, transferred or started,
    # and no key is pressed. For checking its look; --press presses a button on its own, --fail makes Codex's
    # transfer fail after 2 s (the working line, then the failure), --stopped opens it as it reopens after Codex's
    # first turn failed. --preview-return [folder] [GLM|Codex]: the way back's dialog.
    args = sys.argv[1:]
    if args[:1] == ["--preview"]:
        folder = args[1] if len(args) > 1 and not args[1].startswith("--") else "example"
        print(ask_out(folder, forward=False, preview=preview_args([a for a in args[1:] if a != folder])))
        return
    if args[:1] == ["--preview-return"]:
        via = args[2] if len(args) > 2 else "GLM"
        print(ask_themed(args[1] if len(args) > 1 else "example", forward=False, words=BACK, via=via,
                         session="Example session",
                         later="Not Now stays on %s and asks once more in 30 minutes." % via))
        return
    # --ask-return <answer file> <folder> <session title> <what Not Now does> [GLM|Codex]: run by the Agent Panel
    if args[:1] == ["--ask-return"] and len(args) >= 5:
        ask_return(*args[1:6])
        return
    # --carry: the SessionStart (resume) and UserPromptSubmit hook that carries a Codex record in (carry_hook())
    if args[:1] == ["--carry"]:
        carry_hook()
        return

    data = json.load(sys.stdin)

    # Already running through a gateway (the GLM session itself): never chain.
    if os.environ.get("ANTHROPIC_BASE_URL"):
        return
    sid = data.get("session_id")
    if not sid:
        return
    # StopFailure also fires inside subagents (Claude Code 2.1.330: the same session_id and cwd, plus agent_id):
    # subagents stopped by the same limit opened windows of their own (2026-11-22). Only the main thread asks.
    if data.get("agent_id"):
        return
    # quiet: the user already answered this limit's window, or the panel reopened the session in Claude before the
    # reset (its Codex window closed) and Claude Code goes on by itself at the reset
    if quiet(sid):
        return
    cwd = data.get("cwd") or os.getcwd()
    transcript = data.get("transcript_path")
    orig = claude_pid(sid)
    if not seen_by_user(orig, transcript):
        return  # a `claude -p` or SDK run: nobody is there to answer
    hold = hold_dialog(sid)
    if not hold or quiet(sid):
        return  # this session's window is open in another hook, or was answered while this one started
    folder = os.path.basename(cwd.rstrip("\\/")) or cwd
    # the window names the session as its Claude window and card do: a subfolder's name alone did not tell the
    # user which session had stopped
    name = claude_name(orig) or session_name(transcript) or folder
    skip = data.get("permission_mode", "bypassPermissions") == "bypassPermissions"
    error = None
    try:
        while True:
            try:
                import webview  # noqa: F401
                via, thread, cut = ask_out(window_name(name) or folder, transcript=transcript, cwd=cwd, error=error)
            except ImportError:
                via, thread, cut = ("glm" if has_key() and ask_native(folder) else None), None, False
            if not via:
                return
            extra = {}
            if via == "codex":
                path = thread_file(thread)
                # the Codex window and its thread carry the session's name; the folder is trusted for Codex
                named, trusted = prepare_codex(thread, name, cwd)
                work = busy_work(sid, orig, transcript) if orig else []
                extra = {"codex_thread": thread, "codex_file": path, "name": name, "named": named, "work": work,
                         "trusted": trusted, "codex_from": os.path.getsize(path) if path else 0}
                console = open_codex(thread, cwd, skip, whole=transcript if cut else None, name=name, work=work)
            else:
                # GLM's window carries the session's name too; GLM is Claude Code, so it loads CLAUDE.md itself
                work = busy_work(sid, orig, transcript) if orig else []
                extra = {"name": name, "work": work}
                console = open_glm(sid, cwd, skip, name=name, work=work)
            try:
                record_switch(sid, cwd, console.pid, orig, skip, via=via, transcript=transcript, **extra)
            except OSError:
                pass  # the way back is a convenience; the move has happened either way
            # Codex's first turn: if it ends in an error (a history its model cannot hold), the user is not left
            # in a dead window: the way-out window opens again and says what happened
            error = first_turn_error(extra["codex_file"], extra["codex_from"], console) \
                if via == "codex" and extra["codex_file"] else None
            if not error:
                # the hook is ending: the panel may now close this (the old) Claude window
                mark_watched(sid, thread if via == "codex" else None)
                return
    finally:
        # answered (Cancel included): the same limit does not ask again for this session; it may once it reset
        try:
            set_quiet(sid, reset_time(time.time())[0])
        except OSError:
            pass


if __name__ == "__main__":
    main()
