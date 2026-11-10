# Installs the GLM fallback into the current Windows user profile. Safe to rerun.
#   glm.cmd          -> ~/.local/bin        (launches Claude Code against z.ai)
#   glm-fallback.py  -> ~/.claude/hooks     (StopFailure hook: Yes/No, then resume on GLM)
#   context-guard.py -> ~/.claude/hooks     (Stop at 90 % context, PreCompact, SessionStart compact + startup)
#   prompt-recall.py -> ~/.claude/hooks     (UserPromptSubmit: keeps the last prompt for the status line)
#   statusline.py    -> ~/.claude           (bottom bar: context, 5-hour and weekly usage, last prompt)
#   agent-panel.pyw  -> ~/.claude           (every session in a list on the right edge; starts at sign-in)
#   agent-panel.html -> ~/.claude           (the panel's look, shown by pywebview in a WebView2 window)
#   agent-alert.html -> ~/.claude           (the alert shown when a session waits for an answer)
#   settings.json    <- the hook entries and statusLine (backed up first)
#   z.ai key file    -> ~/.claudex/profiles/glm/.env (created with a placeholder if missing)
import ctypes, json, os, shutil, stat, subprocess, sys, time, winreg

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
BIN = os.path.join(HOME, ".local", "bin")
CLAUDE_DIR = os.path.join(HOME, ".claude")
HOOKS = os.path.join(CLAUDE_DIR, "hooks")
SETTINGS = os.path.join(CLAUDE_DIR, "settings.json")
KEY_FILE = os.path.join(HOME, ".claudex", "profiles", "glm", ".env")
PLACEHOLDER = "PASTE_YOUR_KEY_HERE"
HOOK_TIMEOUT = 3600  # the Yes/No dialog waits inside the hook
PANEL = os.path.join(CLAUDE_DIR, "agent-panel.pyw")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "ClaudePlusFA agent panel"


def remove_tree(path):
    """A folder deleted whole, read-only parts too: copytree gives the copy its source's read-only
    flag, and shutil.rmtree cannot delete a read-only folder, so a reinstall then failed to copy."""
    def again(func, p, _):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    shutil.rmtree(path, **({"onexc": again} if sys.version_info >= (3, 12) else {"onerror": again}))


def copy_files():
    os.makedirs(BIN, exist_ok=True)
    os.makedirs(HOOKS, exist_ok=True)
    shutil.copy2(os.path.join(HERE, "glm.cmd"), BIN)
    shutil.copy2(os.path.join(HERE, "glm-fallback.py"), HOOKS)
    shutil.copy2(os.path.join(HERE, "context-guard.py"), HOOKS)
    shutil.copy2(os.path.join(HERE, "prompt-recall.py"), HOOKS)
    shutil.copy2(os.path.join(HERE, "statusline.py"), CLAUDE_DIR)
    print("copied  glm.cmd -> %s" % BIN)
    print("copied  glm-fallback.py, context-guard.py, prompt-recall.py -> %s" % HOOKS)
    print("copied  statusline.py -> %s" % CLAUDE_DIR)


def ensure_on_path():
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                        winreg.KEY_READ | winreg.KEY_WRITE) as k:
        try:
            value, kind = winreg.QueryValueEx(k, "Path")
        except FileNotFoundError:
            value, kind = "", winreg.REG_EXPAND_SZ
        parts = [p for p in value.split(";") if p]
        if any(os.path.normcase(os.path.expandvars(p).rstrip("\\")) == os.path.normcase(BIN)
               for p in parts):
            print("ok      %s is on the user PATH" % BIN)
            return
        winreg.SetValueEx(k, "Path", 0, kind, ";".join(parts + [BIN]))
    # tell running programs the environment changed (new terminals pick it up)
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x1A, 0, "Environment", 2, 5000, None)
    print("added   %s to the user PATH (open a new terminal)" % BIN)


def put_hook(settings, event, matcher, script, args="", timeout=None):
    """Add one hook entry for `script args`, replacing any earlier copy of that same entry."""
    def ours(cmd):
        return script in cmd and cmd.rstrip().endswith(('" ' + args) if args else '"')
    entries = settings.setdefault("hooks", {}).setdefault(event, [])
    entries[:] = [e for e in entries
                  if not any(ours(h.get("command", "")) for h in e.get("hooks", []))]
    hook = {"type": "command", "command": ('python "%s" %s' % (os.path.join(HOOKS, script), args)).strip()}
    if timeout:
        hook["timeout"] = timeout
    entry = {"hooks": [hook]}
    if matcher:
        entry = {"matcher": matcher, **entry}
    entries.append(entry)


def register_hook():
    raw, settings = "", {}
    if os.path.exists(SETTINGS):
        raw = open(SETTINGS, encoding="utf-8", newline="").read()
        settings = json.loads(raw)
        shutil.copy2(SETTINGS, SETTINGS + ".before-claude-plus")
    put_hook(settings, "StopFailure", "rate_limit", "glm-fallback.py", timeout=HOOK_TIMEOUT)
    put_hook(settings, "Stop", None, "context-guard.py", "stop", timeout=10)
    put_hook(settings, "PreCompact", None, "context-guard.py", "precompact", timeout=10)
    put_hook(settings, "SessionStart", "compact", "context-guard.py", "resumed", timeout=10)
    put_hook(settings, "SessionStart", "startup|clear", "context-guard.py", "started", timeout=10)
    put_hook(settings, "UserPromptSubmit", None, "prompt-recall.py", timeout=10)

    # The status line is yours to change: set it only when there is none, or it is ours.
    current = (settings.get("statusLine") or {}).get("command", "")
    if not current or "statusline.py" in current:
        settings["statusLine"] = {"type": "command", "padding": 0,
                                  "command": 'python "%s"' % os.path.join(CLAUDE_DIR, "statusline.py")}
        status_note = "ok      statusLine set"
    else:
        status_note = ("SKIPPED statusLine: you already have one (%s); the context warning "
                       "needs ours, so it stays silent" % current)

    nl = "\r\n" if "\r\n" in raw else "\n"
    with open(SETTINGS, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(settings, indent=2, ensure_ascii=False).replace("\n", nl) + nl)
    print("ok      hooks registered in %s: StopFailure, Stop, PreCompact, SessionStart(compact, startup|clear), UserPromptSubmit"
          % SETTINGS)
    print(status_note)


def ensure_key():
    if os.path.exists(KEY_FILE) and PLACEHOLDER not in open(KEY_FILE, encoding="utf-8").read():
        print("ok      z.ai key present in %s" % KEY_FILE)
        return True
    if not os.path.exists(KEY_FILE):
        os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
        with open(KEY_FILE, "w", encoding="utf-8", newline="\n") as f:
            f.write('# z.ai API key for glm.cmd. DO NOT commit.\nCLAUDEX_KEY="%s"\n' % PLACEHOLDER)
    # optional: everything except the GLM switch works without a key, so never push the user into it
    print("note    optional: for the free GLM switch, put a z.ai key (https://z.ai/manage-apikey/apikey-list) in %s"
          % KEY_FILE)
    return False


def install_panel():
    """The Agent Panel: copied, its tray library installed, started now and at every sign-in."""
    os.makedirs(CLAUDE_DIR, exist_ok=True)
    stop_panel()
    remove_old_name()
    shutil.copy2(os.path.join(HERE, "agent-panel.pyw"), PANEL)
    shutil.copy2(os.path.join(HERE, "agent-panel.html"), CLAUDE_DIR)
    shutil.copy2(os.path.join(HERE, "agent-alert.html"), CLAUDE_DIR)
    # replaced whole, so strips of an earlier mascot do not linger
    remove_tree(os.path.join(CLAUDE_DIR, "agent-panel-mascot"))
    shutil.copytree(os.path.join(HERE, "mascot"), os.path.join(CLAUDE_DIR, "agent-panel-mascot"))
    print("copied  agent-panel.pyw, agent-panel.html, agent-alert.html and agent-panel-mascot -> %s" % CLAUDE_DIR)
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--user", "--quiet", "pywebview", "pystray", "pillow"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("WARNING could not install pywebview, pystray and pillow (%s); the Agent Panel needs them"
              % r.stderr.strip()[-200:])
        return
    print("ok      pywebview, pystray and pillow installed")
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    command = '"%s" "%s"' % (pythonw, PANEL)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, command)
    print("ok      Agent Panel starts at sign-in (HKCU\\%s\\%s)" % (RUN_KEY, RUN_NAME))
    # without the markers of a Claude session that may be running this installer: the panel
    # passes its environment to every session it opens, and an inherited
    # CLAUDE_CODE_CHILD_SESSION stops a resumed session from saving its transcript
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("CLAUDE", "ANTHROPIC"))}
    subprocess.Popen([pythonw, PANEL], creationflags=0x00000008, env=env)  # DETACHED_PROCESS
    print("started the Agent Panel on the right edge of the screen (Ctrl+Alt+C shows or hides it)")


def stop_panel():
    """Ask a running panel to quit, so it gives its screen space back; force it after 3 s.
    Also stops the earlier "session panel", the same program under its old name."""
    k = ctypes.windll.kernel32
    asked = False
    for name in ("Local\\claude-plus-fa-agent-panel-quit", "Local\\claude-plus-fa-session-panel-quit"):
        h = k.OpenEventW(0x0002, False, name)  # EVENT_MODIFY_STATE
        if h:
            k.SetEvent(h)
            k.CloseHandle(h)
            asked = True
    if asked:
        time.sleep(3)
    subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | "
                    "Where-Object { $_.CommandLine -like '*agent-panel.pyw*' -or $_.CommandLine -like '*session-panel.pyw*' } | "
                    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
                   capture_output=True)


def remove_old_name():
    """The panel was called "session panel" before 2026-11-07: drop that copy and its sign-in entry."""
    old = os.path.join(CLAUDE_DIR, "session-panel.pyw")
    if os.path.exists(old):
        os.remove(old)
    shutil.rmtree(os.path.join(CLAUDE_DIR, "state", "session-panel"), ignore_errors=True)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, "ClaudePlusFA session panel")
    except FileNotFoundError:
        pass


def main():
    if shutil.which("claude") is None:
        print("WARNING Claude Code (claude) is not on PATH; install it first")
    copy_files()
    ensure_on_path()
    register_hook()
    install_panel()
    has_key = ensure_key()
    print()
    print("Done. New Claude Code sessions pick up the hook; already open ones need a restart.")
    if has_key:
        print("Try it: glm -p \"Reply with exactly: GLM OK\"")


if __name__ == "__main__":
    main()
