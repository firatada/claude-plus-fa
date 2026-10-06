# Claude Code status line: model, context used, 5-hour session and weekly usage, and
# below it the last prompt of this session in full, up to PROMPT_LINES lines (written by prompt-recall.py).
# Claude Code pipes a JSON object on stdin and shows every non-empty line printed.
# rate_limits only exists for a claude.ai subscription, so a GLM session shows
# the context bar alone.
#
# Part of claude-plus. Edit the copy in the claude-plus folder and rerun install.cmd.
#
# It also records the context figure per session for context-guard.py.
import json, os, shutil, sys, textwrap, time

sys.stdout.reconfigure(encoding="utf-8", newline="\n")

GREEN, YELLOW, RED, DIM, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"
WIDTH = 10  # cells per bar


def bar(label, pct, note=""):
    if pct is None:
        return "%s %s--%s" % (label, DIM, RESET)
    pct = max(0, min(100, round(pct)))
    color = GREEN if pct < 50 else YELLOW if pct < 80 else RED
    filled = round(pct * WIDTH / 100)
    text = "%s %s%s%s%s %d%%" % (label, color, "█" * filled, DIM + "░" * (WIDTH - filled), RESET, pct)
    return text + (" %s%s%s" % (DIM, note, RESET) if note else "")


def resets(ts, fmt):
    try:
        return time.strftime(fmt, time.localtime(float(ts)))
    except (TypeError, ValueError):
        return ""


def record(sid, pct):
    if not sid:
        return
    state = os.path.join(os.path.expanduser("~"), ".claude", "state", "context")
    try:
        os.makedirs(state, exist_ok=True)
        with open(os.path.join(state, sid + ".json"), "w") as f:
            json.dump({"used_percentage": pct}, f)
    except OSError:
        pass  # the bar matters more than the record


def columns():
    """Terminal width: Claude Code does not pass it and stdout is a pipe, so ask the console."""
    try:
        fd = os.open("CONOUT$", os.O_RDWR)
        try:
            return os.get_terminal_size(fd).columns
        finally:
            os.close(fd)
    except OSError:
        return shutil.get_terminal_size((120, 20)).columns


PROMPT_LINES = 12  # a pasted wall of text must not push the prompt off the screen


def last_prompt(sid):
    """'> 14:32  what you last asked', wrapped to the terminal width and shown in full up to
    PROMPT_LINES lines, as a list of lines, or []."""
    try:
        with open(os.path.join(os.path.expanduser("~"), ".claude", "state", "context",
                               sid + ".prompt.json"), encoding="utf-8") as f:
            saved = json.load(f)
        text = " ".join(saved["prompt"].split())
        stamp = time.strftime("%H:%M", time.localtime(float(saved["at"])))
    except (OSError, ValueError, KeyError, TypeError):
        return []
    lead = "› %s  " % stamp
    lines = textwrap.wrap(text, max(20, columns() - len(lead) - 4), break_long_words=True) or [""]
    if len(lines) > PROMPT_LINES:
        lines = lines[:PROMPT_LINES]
        lines[-1] = lines[-1][:-1].rstrip() + "…"
    # Claude Code trims every line, so the continuation indent hides behind a colour code
    indent = DIM + " " * len(lead) + RESET
    return [DIM + lead + RESET + lines[0]] + [indent + l for l in lines[1:]]


data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
ctx = (data.get("context_window") or {}).get("used_percentage")
record(data.get("session_id"), ctx)
parts = [(data.get("model") or {}).get("display_name") or "Claude"]
parts.append(bar("ctx", ctx))

limits = data.get("rate_limits") or {}
if "five_hour" in limits:
    h = limits["five_hour"]
    parts.append(bar("5h", h.get("used_percentage"), resets(h.get("resets_at"), "%H:%M")))
if "seven_day" in limits:
    w = limits["seven_day"]
    parts.append(bar("week", w.get("used_percentage"), resets(w.get("resets_at"), "%a %H:%M")))

print("  ·  ".join(parts))
for line in last_prompt(data.get("session_id") or ""):
    print(line)
