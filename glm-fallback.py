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
#
# The question is asked inside the hook, so settings.json gives this hook a long
# timeout; StopFailure output is ignored, so blocking costs nothing.
#
# Part of claude-plus. Edit the copy in the claude-plus-fa folder and rerun install.cmd.
import base64, ctypes, glob, hashlib, html, json, os, re, shutil, subprocess, sys, threading, time

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


def transfer(transcript, cwd):
    """Import a Claude transcript into a resumable Codex thread with the Codex plugin's own transfer
    (codex-companion.mjs transfer --source <jsonl>; it runs without Claude's model). Returns the thread id;
    raises RuntimeError with the reason."""
    script = companion()
    if not script:
        raise RuntimeError("The Codex plugin is not installed.")
    source = os.path.realpath(transcript)
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


def open_codex(thread, cwd, skip):
    cmd = "codex resume " + thread
    if skip:
        cmd += " --dangerously-bypass-approvals-and-sandbox"
    return new_console(cmd + ' "%s"' % CONTINUE, cwd=cwd, env=clean_env())


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


def record_switch(sid, cwd, console, orig_pid, skip, limits=None, now=None, via="glm", transcript=None, **extra):
    """Write the record of a session moved to GLM or Codex. console: the process of the new window (cmd /k);
    orig_pid: the Claude process whose turn the limit ended, looked up before the new window started (after,
    GLM's own process holds the same session and could be taken for it). extra: Codex's thread, its file and
    the file's size after the import (codex_thread, codex_file, codex_from)."""
    now = time.time() if now is None else now
    resets_at, source = reset_time(now, limits)
    rec = {"session": sid, "cwd": cwd, "folder": os.path.basename(cwd.rstrip("\\/")) or cwd, "via": via,
           "console": console, "orig_pid": orig_pid, "skip": bool(skip), "at": now, "transcript": transcript,
           "resets_at": resets_at, "reset_from": source}
    rec.update(extra)
    os.makedirs(RETURN_DIR, exist_ok=True)
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


def codex_record(rec):
    """Everything that happened in Codex after the import, in order, read from its thread file: each user
    message, each Codex reply, each command it ran with its exit code, and the files it changed."""
    lines, calls, files = [], {}, []
    for o in read_from(rec.get("codex_file"), rec.get("codex_from")):
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
        written = changed_since(rec["cwd"], rec.get("at") or 0)
        if written:
            lines.append("Files in %s written since the move to Codex (by Codex or anyone else): %s"
                         % (rec.get("folder") or rec["cwd"], ", ".join(written)))
    return "\n\n".join(lines) if lines else "Codex did nothing in this session."


def codex_back_prompt(rec, now=None):
    """The first prompt of the resumed Claude session: the record of what happened in Codex, so it lands in the
    same Claude transcript. A record longer than PROMPT_MAX is written whole to a file in the session's folder
    and the prompt carries its path and its last TAIL characters. Returns (prompt, file or None)."""
    head = "While Claude's limit was out, this session continued in Codex. This is what happened there:"
    end = "Continue from where Codex stopped."
    body = codex_record(rec)
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
def ask_out(folder, forward=True, transcript=None, cwd=None, preview=None):
    """The way out. Returns ("glm", None), ("codex", thread id) or (None, None). The Codex transfer runs while
    the window shows its working line, so a failure is shown there and the user can pick again.
    preview: {"glm": bool or None, "codex": dict or None, "open": "glm"/"codex"/None}; nothing is transferred,
    started or opened, and a forced value replaces the real check."""
    result = {"via": None, "thread": None}
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
                    result["thread"] = transfer(transcript, cwd)
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

    page = page_head(OUT_PAGE, "Usage Limit Reached: %s" % html.escape(folder))
    if preview.get("open"):
        page = page.replace("<script>", '<script>window.OPEN = "%s";' % preview["open"], 1)
    if preview.get("press"):
        page = page.replace("<script>", '<script>window.PRESS = "%s";' % preview["press"], 1)
    show(TITLE, page, Api(), forward, width=540)
    return result["via"], result["thread"]


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
    opts = {"glm": None, "codex": None, "open": None, "press": None, "fail": False}
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
        elif k == "--fail":
            opts["fail"] = True
            args.insert(0, v) if v else None
    return opts


def main():
    # python glm-fallback.py --preview [folder] [--glm on|off] [--codex ready|cli|plugin|login|none] [--open glm|codex]
    # [--press glm|codex] [--fail]: the dialog alone, its answer printed; nothing is sent, transferred or started,
    # and no key is pressed. For checking its look; --press presses a button on its own, --fail makes Codex's
    # transfer fail after 2 s (the working line, then the failure). --preview-return [folder] [GLM|Codex]: the way back's dialog.
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

    data = json.load(sys.stdin)

    # Already running through a gateway (the GLM session itself): never chain.
    if os.environ.get("ANTHROPIC_BASE_URL"):
        return
    sid = data.get("session_id")
    if not sid:
        return
    cwd = data.get("cwd") or os.getcwd()
    transcript = data.get("transcript_path")
    folder = os.path.basename(cwd.rstrip("\\/")) or cwd
    try:
        import webview  # noqa: F401
        via, thread = ask_out(folder, transcript=transcript, cwd=cwd)
    except ImportError:
        via, thread = ("glm" if has_key() and ask_native(folder) else None), None
    if not via:
        return

    skip = data.get("permission_mode", "bypassPermissions") == "bypassPermissions"
    orig = claude_pid(sid)
    extra = {}
    if via == "codex":
        path = thread_file(thread)
        extra = {"codex_thread": thread, "codex_file": path,
                 "codex_from": os.path.getsize(path) if path else 0}
        console = open_codex(thread, cwd, skip)
    else:
        cmd = "glm --resume " + sid
        if skip:
            cmd += " --dangerously-skip-permissions"
        console = new_console(cmd + ' "%s"' % CONTINUE, cwd=cwd)
    try:
        record_switch(sid, cwd, console.pid, orig, skip, via=via, transcript=transcript, **extra)
    except OSError:
        pass  # the way back is a convenience; the move has happened either way


if __name__ == "__main__":
    main()
