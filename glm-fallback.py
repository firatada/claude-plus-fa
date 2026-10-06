# StopFailure hook (matcher: rate_limit). When Claude's usage limit ends a turn,
# ask whether to continue with the GLM profile (~/.local/bin/glm.cmd); on Yes,
# reopen the same session in a new cmd window. Same transcript, so no context is lost.
#
# The question is asked inside the hook, so settings.json gives this hook a long
# timeout; StopFailure output is ignored, so blocking costs nothing.
#
# Part of claude-plus. Edit the copy in the claude-plus folder and rerun install.cmd.
import ctypes, json, os, subprocess, sys

MB_YESNO, MB_ICONQUESTION, MB_SETFOREGROUND, MB_TOPMOST, IDYES = 0x4, 0x20, 0x10000, 0x40000, 6

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

text = ("Claude's usage limit has been reached in %s.\n\n"
        "Continue this session with GLM (z.ai) in a new window?"
        % os.path.basename(cwd.rstrip("\\/")))
answer = ctypes.windll.user32.MessageBoxW(
    None, text, "Claude Code", MB_YESNO | MB_ICONQUESTION | MB_SETFOREGROUND | MB_TOPMOST)
if answer != IDYES:
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
