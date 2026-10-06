# Installs the GLM fallback into the current Windows user profile. Safe to rerun.
#   glm.cmd          -> ~/.local/bin        (launches Claude Code against z.ai)
#   glm-fallback.py  -> ~/.claude/hooks     (StopFailure hook: Yes/No, then resume on GLM)
#   context-guard.py -> ~/.claude/hooks     (Stop at 90 % context, PreCompact, SessionStart compact + startup)
#   prompt-recall.py -> ~/.claude/hooks     (UserPromptSubmit: keeps the last prompt for the status line)
#   statusline.py    -> ~/.claude           (bottom bar: context, 5-hour and weekly usage, last prompt)
#   settings.json    <- the hook entries and statusLine (backed up first)
#   z.ai key file    -> ~/.claudex/profiles/glm/.env (created with a placeholder if missing)
import ctypes, json, os, shutil, subprocess, sys, time, winreg

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
BIN = os.path.join(HOME, ".local", "bin")
CLAUDE_DIR = os.path.join(HOME, ".claude")
HOOKS = os.path.join(CLAUDE_DIR, "hooks")
SETTINGS = os.path.join(CLAUDE_DIR, "settings.json")
KEY_FILE = os.path.join(HOME, ".claudex", "profiles", "glm", ".env")
PLACEHOLDER = "PASTE_YOUR_KEY_HERE"
HOOK_TIMEOUT = 3600  # the Yes/No dialog waits inside the hook


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
    print("ACTION  paste your z.ai key (https://z.ai/manage-apikey/apikey-list) into %s, then save"
          % KEY_FILE)
    subprocess.Popen(["notepad.exe", KEY_FILE])
    return False


def main():
    if shutil.which("claude") is None:
        print("WARNING Claude Code (claude) is not on PATH; install it first")
    copy_files()
    ensure_on_path()
    register_hook()
    has_key = ensure_key()
    print()
    print("Done. New Claude Code sessions pick up the hook; already open ones need a restart.")
    if has_key:
        print("Try it: glm -p \"Reply with exactly: GLM OK\"")


if __name__ == "__main__":
    main()
