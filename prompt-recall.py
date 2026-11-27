# UserPromptSubmit hook: keeps the last prompt of each session, so statusline.py can
# show it on a second line and nobody has to scroll up to find what they asked.
# Prints nothing: whatever a UserPromptSubmit hook prints is added to Claude's context.
#
# Part of claude-plus. Edit the copy in the claude-plus-fa folder and rerun install.cmd.
import json, os, re, sys, time

STATE = os.path.join(os.path.expanduser("~"), ".claude", "state", "context")
# Claude Code also sends its own messages through this hook as if they were prompts
# (a finished background task arrives as <task-notification>…); they must not replace yours.
# Field Agents' keep-alive is no prompt of yours either (its hooks are off; this is the second guard).
MACHINE = ("<task-notification>", "<system-reminder>", "Keep-alive from claude-plus")
# A pasted text reaches this hook wrapped as <pasted_content id="ab12">\n...\n</pasted_content id="ab12"> (Claude Code
# 2.1.278 on); the status line shows what it holds, not the tags.
PASTED = re.compile(r'<pasted_content(?: id="([^"]*)")?>\n?(.*?)\n?</pasted_content(?: id="\1")?>', re.S)

try:
    data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    sid = data.get("session_id") or ""
    prompt = PASTED.sub(lambda m: m.group(2), data.get("prompt") or "").strip()
    if sid and prompt and not prompt.startswith(MACHINE):
        os.makedirs(STATE, exist_ok=True)
        with open(os.path.join(STATE, sid + ".prompt.json"), "w", encoding="utf-8") as f:
            json.dump({"prompt": prompt, "at": time.time()}, f, ensure_ascii=False)
except (OSError, ValueError):
    pass  # never block a prompt over a convenience
sys.exit(0)
