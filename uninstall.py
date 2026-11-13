# Removes claude-plus-fa from the current Windows user profile. Safe to rerun.
#   settings.json    -> the claude-plus-fa hook entries, and the statusLine if it is ours
#                       (backed up first as settings.json.before-claude-plus-uninstall)
#   ~/.claude        -> statusline.py, the three hook scripts, the state folder
#   ~/.local/bin     -> glm.cmd
# Kept on purpose: your z.ai key file and ~/.claude/claude-plus.local.json (yours,
# not ours), the install backup, and ~/.local/bin on PATH (other tools live there).
import ctypes, json, os, shutil, stat, subprocess, sys, time, winreg

HOME = os.path.expanduser("~")
CLAUDE_DIR = os.path.join(HOME, ".claude")
HOOKS = os.path.join(CLAUDE_DIR, "hooks")
SETTINGS = os.path.join(CLAUDE_DIR, "settings.json")
SCRIPTS = ("glm-fallback.py", "context-guard.py", "prompt-recall.py")
FILES = [os.path.join(HOOKS, s) for s in SCRIPTS] + [
    os.path.join(CLAUDE_DIR, "statusline.py"),
    os.path.join(CLAUDE_DIR, "agent-panel.pyw"),
    os.path.join(CLAUDE_DIR, "agent-panel.html"),
    os.path.join(CLAUDE_DIR, "agent-alert.html"),
    os.path.join(CLAUDE_DIR, "agent-panel-shots.py"),
    os.path.join(CLAUDE_DIR, "session-panel.pyw"),
    os.path.join(HOME, ".local", "bin", "glm.cmd"),
]
STATE = os.path.join(CLAUDE_DIR, "state", "context")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "ClaudePlusFA agent panel"
KEPT = [os.path.join(HOME, ".claudex", "profiles", "glm", ".env"),
        os.path.join(CLAUDE_DIR, "claude-plus.local.json")]


def ours(command):
    return any(s in command for s in SCRIPTS)


def clean_settings():
    if not os.path.exists(SETTINGS):
        print("ok      no %s" % SETTINGS)
        return
    raw = open(SETTINGS, encoding="utf-8", newline="").read()
    settings = json.loads(raw)
    removed = 0
    hooks = settings.get("hooks") or {}
    for event in list(hooks):
        kept = []
        for entry in hooks[event]:
            inner = [h for h in entry.get("hooks", []) if not ours(h.get("command", ""))]
            removed += len(entry.get("hooks", [])) - len(inner)
            if inner:
                kept.append({**entry, "hooks": inner})
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if "hooks" in settings and not hooks:
        del settings["hooks"]
    status = (settings.get("statusLine") or {}).get("command", "")
    status_removed = "statusline.py" in status
    if status_removed:
        del settings["statusLine"]
    if not removed and not status_removed:
        print("ok      nothing of claude-plus-fa in %s" % SETTINGS)
        return
    shutil.copy2(SETTINGS, SETTINGS + ".before-claude-plus-uninstall")
    nl = "\r\n" if "\r\n" in raw else "\n"
    with open(SETTINGS, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(settings, indent=2, ensure_ascii=False).replace("\n", nl) + nl)
    print("removed %d hook entries%s from %s" % (removed, " and the status line" if status_removed else "", SETTINGS))
    if status and not status_removed:
        print("kept    your own status line (%s)" % status)


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


def remove_files():
    for path in FILES:
        if os.path.exists(path):
            os.remove(path)
            print("deleted %s" % path)
    if os.path.isdir(STATE):
        shutil.rmtree(STATE, ignore_errors=True)
        print("deleted %s" % STATE)
    mascot = os.path.join(CLAUDE_DIR, "agent-panel-mascot")
    if os.path.isdir(mascot):
        remove_tree(mascot)
        print("deleted %s" % mascot)


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


def remove_shortcut():
    """The installer's "Agent Panel" icon on the user's real Desktop, only if it starts our panel."""
    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf)  # CSIDL_DESKTOPDIRECTORY
    link = os.path.join(buf.value, "Agent Panel.lnk")
    try:
        with open(link, "rb") as f:
            if "agent-panel.pyw".encode("utf-16-le") not in f.read():
                return
    except OSError:
        return
    os.remove(link)
    print("deleted %s" % link)


def remove_panel():
    """Stop the Agent Panel, remove its sign-in entry and desktop icon; remove_files() deletes the script."""
    stop_panel()
    remove_old_name()
    remove_shortcut()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, RUN_NAME)
        print("removed the Agent Panel from sign-in")
    except FileNotFoundError:
        pass
    for name in ("agent-panel", "session-panel", "glm-return"):
        panel_state = os.path.join(CLAUDE_DIR, "state", name)
        if os.path.isdir(panel_state):
            shutil.rmtree(panel_state, ignore_errors=True)
            print("deleted %s" % panel_state)


def main():
    clean_settings()
    remove_panel()
    remove_files()
    for path in KEPT:
        if os.path.exists(path):
            print("kept    %s (delete it yourself if you no longer need it)" % path)
    print()
    print("Done. Restart open Claude Code sessions: hooks stay loaded until a session restarts.")


if __name__ == "__main__":
    main()
