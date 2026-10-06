# Context safety net: nothing learned in a session is lost to auto-compaction.
# One script, four hook modes, picked by the first argument:
#
#   stop        Stop hook. Once per climb past WARN_AT %, exit 2 so Claude keeps
#               going and runs the context-warning steps. Re-arms once the window
#               has been compacted (usage back under REARM_BELOW %).
#   precompact  PreCompact hook. Stdout is appended to the compaction instructions.
#   resumed     SessionStart hook, matcher "compact".
#   started     SessionStart hook, matcher "startup|clear". If the project has a
#               handoff.md, tells Claude to read it and memory.md before working.
#               (--resume needs nothing: the conversation itself comes back.)
#   Both answer in JSON: systemMessage is shown to the user (which files will be read),
#   additionalContext goes to Claude (read them, then name what was read).
#
# The usage figure comes from statusline.py, which writes it per session; Claude
# itself never sees the gauge.
#
# Part of claude-plus. Edit the copy in the claude-plus folder and rerun install.cmd.
import json, os, sys, time

WARN_AT = 90      # auto-compaction measured at about 96.7 % of a 1M window
REARM_BELOW = 50  # after compaction usage drops to a few percent
STATE = os.path.join(os.path.expanduser("~"), ".claude", "state", "context")

CONFIG = os.path.join(os.path.expanduser("~"), ".claude", "claude-plus.local.json")

# The built-in steps. Someone who keeps their own procedure (for example in their
# CLAUDE.md) points at it instead with {"rule": "<where it lives>"} in CONFIG; the
# installer never writes that file, so an update cannot overwrite it.
WARN_STEPS = (
    "write everything down, because auto-compaction is close and only a summary survives it: "
    "commit the work that is verified, never half-done work; update the project's memory.md "
    "(current state, new files, commands, open questions, and each decision with its reason); "
    "write handoff.md in the project root (goal, current focus, decision timeline, abandoned "
    "approaches, work in flight, the exact next step; link memory.md, do not copy it); commit "
    "those documents; tell the user a new session can start from handoff.md")
RESUME_STEPS = (
    "check git log for commits made after memory.md was last updated and write down what they "
    "left out, then write down every decision, dead end and trap the summary holds that no "
    "document does")


def own_rule():
    """The user's own procedure, from CONFIG, or None for the built-in steps."""
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f).get("rule") or None
    except (OSError, ValueError, AttributeError):
        return None


def stop(data):
    if data.get("stop_hook_active"):  # already continuing because of a Stop hook
        return 0
    sid = data.get("session_id") or ""
    try:
        pct = json.load(open(os.path.join(STATE, sid + ".json")))["used_percentage"]
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    flag = os.path.join(STATE, sid + ".warned")
    if pct is None:
        return 0
    if pct < REARM_BELOW and os.path.exists(flag):
        os.remove(flag)
        return 0
    if pct < WARN_AT or os.path.exists(flag):
        return 0
    open(flag, "w").close()
    rule = own_rule()
    steps = ("run the \"on a context warning from the hook\" steps in %s" % rule) if rule else WARN_STEPS
    sys.stderr.write("Context window is at %d %%. Before anything else, %s, then continue the work."
                     % (pct, steps))
    return 2


def precompact(data):
    print("Preserve in the summary: every decision and its reason, approaches tried and "
          "abandoned and why, files being edited and their state, open questions, and the "
          "exact next step. Name files by path.")
    return 0


def find_handoff_dir(cwd):
    """The nearest folder at or above cwd holding handoff.md, not climbing past the git root."""
    d = cwd
    while True:
        if os.path.isfile(os.path.join(d, "handoff.md")):
            return d
        parent = os.path.dirname(d)
        if os.path.exists(os.path.join(d, ".git")) or parent == d:
            return None
        d = parent


def project_docs(data):
    """('handoff.md and memory.md in <dir>', 'last written ... (today)', [full paths]), or None."""
    root = find_handoff_dir(data.get("cwd") or os.getcwd())
    if not root:
        return None
    written = os.path.getmtime(os.path.join(root, "handoff.md"))
    days = int((time.time() - written) // 86400)
    age = "today" if days == 0 else "1 day ago" if days == 1 else "%d days ago" % days
    names = ["handoff.md"] + (["memory.md"] if os.path.isfile(os.path.join(root, "memory.md")) else [])
    return ("%s in %s" % (" and ".join(names), root),
            "last written %s (%s)" % (time.strftime("%Y-%m-%d %H:%M", time.localtime(written)), age),
            [os.path.join(root, n) for n in names])


NAME_THEM = (" Then, before anything else, tell the user in one line which files you read, by full "
             "path, or that you read none.")


def answer(to_user, to_claude):
    """SessionStart JSON: systemMessage is shown to the user, additionalContext goes to Claude."""
    print(json.dumps({"systemMessage": to_user,
                      "hookSpecificOutput": {"hookEventName": "SessionStart",
                                             "additionalContext": to_claude}}))


def resumed(data):
    docs = project_docs(data)
    read = ("read %s (handoff.md %s), because the summary is short and the documents are the "
            "full record, then " % docs[:2]) if docs else ""
    if docs:
        to_user = "claude-plus: after compaction Claude reads " + ", ".join(docs[2])
    else:
        to_user = ("claude-plus: after compaction no handoff.md was found above %s; Claude runs the "
                   "resume steps only" % (data.get("cwd") or os.getcwd()))
    rule = own_rule()
    steps = ("run the \"on resuming a session that opens with a summary\" steps in %s" % rule
             if rule else RESUME_STEPS)
    answer(to_user, "This session continues from a compaction summary. Before continuing, %s%s.%s"
           % (read, steps, NAME_THEM))
    return 0


def started(data):
    docs = project_docs(data)
    if docs:
        answer("claude-plus: this project has a handoff.md (%s); Claude reads %s first"
               % (docs[1], ", ".join(docs[2])),
               "This project has a handoff.md, %s. Before the first task, read %s to see where the "
               "previous session stopped. Check git log for commits made after it.%s"
               % (docs[1], docs[0], NAME_THEM))
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    handler = {"stop": stop, "precompact": precompact, "resumed": resumed,
               "started": started}.get(mode)
    sys.exit(handler(json.load(sys.stdin)) if handler else 0)
