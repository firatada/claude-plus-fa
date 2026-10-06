# Removes claude-plus from the current Windows user profile. Safe to rerun.
#   settings.json    -> the claude-plus hook entries, and the statusLine if it is ours
#                       (backed up first as settings.json.before-claude-plus-uninstall)
#   ~/.claude        -> statusline.py, the three hook scripts, the state folder
#   ~/.local/bin     -> glm.cmd
# Kept on purpose: your z.ai key file and ~/.claude/claude-plus.local.json (yours,
# not ours), the install backup, and ~/.local/bin on PATH (other tools live there).
import json, os, shutil

HOME = os.path.expanduser("~")
CLAUDE_DIR = os.path.join(HOME, ".claude")
HOOKS = os.path.join(CLAUDE_DIR, "hooks")
SETTINGS = os.path.join(CLAUDE_DIR, "settings.json")
SCRIPTS = ("glm-fallback.py", "context-guard.py", "prompt-recall.py")
FILES = [os.path.join(HOOKS, s) for s in SCRIPTS] + [
    os.path.join(CLAUDE_DIR, "statusline.py"),
    os.path.join(HOME, ".local", "bin", "glm.cmd"),
]
STATE = os.path.join(CLAUDE_DIR, "state", "context")
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
        print("ok      nothing of claude-plus in %s" % SETTINGS)
        return
    shutil.copy2(SETTINGS, SETTINGS + ".before-claude-plus-uninstall")
    nl = "\r\n" if "\r\n" in raw else "\n"
    with open(SETTINGS, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(settings, indent=2, ensure_ascii=False).replace("\n", nl) + nl)
    print("removed %d hook entries%s from %s" % (removed, " and the status line" if status_removed else "", SETTINGS))
    if status and not status_removed:
        print("kept    your own status line (%s)" % status)


def remove_files():
    for path in FILES:
        if os.path.exists(path):
            os.remove(path)
            print("deleted %s" % path)
    if os.path.isdir(STATE):
        shutil.rmtree(STATE, ignore_errors=True)
        print("deleted %s" % STATE)


def main():
    clean_settings()
    remove_files()
    for path in KEPT:
        if os.path.exists(path):
            print("kept    %s (delete it yourself if you no longer need it)" % path)
    print()
    print("Done. Restart open Claude Code sessions: hooks stay loaded until a session restarts.")


if __name__ == "__main__":
    main()
