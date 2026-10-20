# UserPromptSubmit hook: keeps the last prompt of each session, so statusline.py can
# show it on a second line and nobody has to scroll up to find what they asked.
# Prints nothing: whatever a UserPromptSubmit hook prints is added to Claude's context.
#
# Part of claude-plus. Edit the copy in the claude-plus folder and rerun install.cmd.
import json, os, sys, time

STATE = os.path.join(os.path.expanduser("~"), ".claude", "state", "context")
# Claude Code also sends its own messages through this hook as if they were prompts
# (a finished background task arrives as <task-notification>…); they must not replace yours.
MACHINE = ("<task-notification>", "<system-reminder>")

try:
    data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    sid, prompt = data.get("session_id") or "", data.get("prompt") or ""
    if sid and prompt.strip() and not prompt.lstrip().startswith(MACHINE):
        os.makedirs(STATE, exist_ok=True)
        with open(os.path.join(STATE, sid + ".prompt.json"), "w", encoding="utf-8") as f:
            json.dump({"prompt": prompt, "at": time.time()}, f, ensure_ascii=False)
except (OSError, ValueError):
    pass  # never block a prompt over a convenience
sys.exit(0)
