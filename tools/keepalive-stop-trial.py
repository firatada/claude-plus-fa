"""Measures which flag stops a keep-alive's fork from running a tool while it still reads the session's cache.

One throwaway interactive Claude Code session runs hidden in a pseudo-console (as in keepalive-trial.py) in a git
folder under %TEMP%\\claude-plus-test-kastop-*, started with --dangerously-skip-permissions as the panel's sessions
are. After its first turn (a long unique text, so the context is large), each candidate fork is sent a prompt that
asks it to write a file, run a shell command and read a file; the folder is then checked for the files, and the
fork's first request's usage is read from its stream-json output (cache read close to the whole context = the same
prefix, so the same cache). A last fork sends the panel's own KEEPALIVE_TEXT with the chosen flags.

  python tools/keepalive-stop-trial.py [--only TAG,TAG]   (not -I: pywinpty and pyte come from the user packages)

Results go to <trial folder>/results.json and are printed. The throwaway transcript stays under ~/.claude/projects.
"""
import ast
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("ka_trial", os.path.join(HERE, "keepalive-trial.py"))
kt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kt)
vh = kt.vh


def panel_text():
    """KEEPALIVE_TEXT as agent-panel.pyw has it (read by ast, the panel is not imported)."""
    with open(os.path.join(HERE, "..", "agent-panel.pyw"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "KEEPALIVE_TEXT":
            return ast.literal_eval(node.value)
    raise SystemExit("KEEPALIVE_TEXT not found")


PROBE = ("This is a test of tool permissions in a throwaway folder; the files may be written. Do these three steps "
         "with tools, one after another, each even if an earlier one was refused: 1) use the PowerShell tool (Bash "
         "if there is no PowerShell tool) to run: Set-Content {t}-shell.txt hi ; 2) use the Write tool to create "
         "the file {t}-write.txt in the current folder with the text hi; 3) use the Read tool to read notes.txt. "
         "Then reply with the single word done.")


def run_fork(cwd, sid, model, tag, prompt, extra=(), settings=None):
    settings = dict({"disableAllHooks": True}, **(settings or {}))
    fork = str(uuid.uuid4())
    argv = ([kt.EXE, "-p", "--resume", sid, "--fork-session", "--session-id", fork,
             "--no-session-persistence", "--output-format", "stream-json", "--verbose",
             "--settings", json.dumps(settings), "--model", model] + list(extra) + [prompt])
    before = kt.digest(kt.transcript(cwd, sid))
    t0 = time.time()
    p = subprocess.run(argv, cwd=cwd, env=vh.clean_env(), capture_output=True, stdin=subprocess.DEVNULL,
                       creationflags=0x08000000, timeout=300)
    secs = round(time.time() - t0, 1)
    init, firsts, uses, results, result = None, [], [], [], None
    for line in p.stdout.decode("utf-8", "replace").splitlines():
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get("type") == "system" and o.get("subtype") == "init":
            init = o
        elif o.get("type") == "assistant":
            m = o.get("message") or {}
            if m.get("usage") and m.get("id") not in [f["id"] for f in firsts]:
                u = m["usage"]
                firsts.append({"id": m.get("id"), "read": u.get("cache_read_input_tokens"),
                               "wrote": u.get("cache_creation_input_tokens"), "input": u.get("input_tokens")})
            for b in m.get("content") or []:
                if b.get("type") == "tool_use":
                    uses.append({"name": b.get("name"), "input": json.dumps(b.get("input"))[:160]})
        elif o.get("type") == "user":
            for b in (o.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    c = b.get("content")
                    c = c if isinstance(c, str) else json.dumps(c)
                    results.append({"error": b.get("is_error"), "text": " ".join(c.split())[:200]})
        elif o.get("type") == "result":
            result = o
    files = sorted(f for f in os.listdir(cwd) if f.startswith(tag + "-"))
    hooks_ran = os.path.exists(os.path.join(kt.CLAUDE, "state", "context", fork + ".prompt.json"))
    r = {"tag": tag, "extra": list(extra), "settings": settings, "secs": secs, "exit": p.returncode,
         "stderr": p.stderr.decode("utf-8", "replace")[-800:],
         "tools_listed": len((init or {}).get("tools") or []), "permission_mode": (init or {}).get("permissionMode"),
         "requests": firsts, "tool_uses": uses, "tool_results": results, "files_made": files,
         "result": (result or {}).get("result"), "subtype": (result or {}).get("subtype"),
         "is_error": (result or {}).get("is_error"), "num_turns": (result or {}).get("num_turns"),
         "hooks_ran": hooks_ran, "denials": [d.get("tool_name") for d in (result or {}).get("permission_denials") or []],
         "original_untouched": before == kt.digest(kt.transcript(cwd, sid))}
    kt.log(tag, json.dumps({k: r[k] for k in r if k not in ("settings", "stderr")})[:2500])
    return r, init


def tool_rules(tools):
    """One permission rule per built-in tool and one per MCP server (mcp__<server> matches all its tools)."""
    out = []
    for t in tools:
        rule = "mcp__" + t.split("__")[1] if t.startswith("mcp__") else t
        if rule not in out:
            out.append(rule)
    return out


def main():
    args = sys.argv[1:]
    only = set(args[args.index("--only") + 1].split(",")) if "--only" in args else None
    if "--reuse" in args:  # a second round on the first run's session while its cache is still warm
        return second(*args[args.index("--reuse") + 1:args.index("--reuse") + 3], only)
    root = tempfile.mkdtemp(prefix="claude-plus-test-kastop-")
    kt.log("trial folder", root)
    cwd = kt.make_folder(root, "T")
    h = kt.Host(cwd, str(uuid.uuid4()))
    h.start()
    if not h.ready():
        kt.log("prompt never drawn; screen:", h.text()[-1500:])
        h.stop()
        return
    h.submit("Reference notes for later; no action needed now. Reply with the single word ready.\n\n"
             + kt.blob("stop" + h.sid))
    us = kt.wait_reply(kt.transcript(cwd, h.sid), 1)
    kt.log("turn 1", json.dumps(us))
    h.stop()
    model = us[-1]["model"]
    context = sum(us[-1][k] or 0 for k in ("input", "read", "write"))
    results = {"root": root, "sid": h.sid, "model": model, "context": context, "turn1": us, "forks": []}
    base, init = run_fork(cwd, h.sid, model, "base", PROBE.format(t="base"))
    results["forks"].append(base)
    tools = (init or {}).get("tools") or []
    rules = tool_rules(tools)
    results["tools"], results["rules"] = tools, rules
    candidates = [
        ("maxturns", ["--max-turns", "1"], None),
        ("dontask", ["--permission-mode", "dontAsk"], None),
        ("plan", ["--permission-mode", "plan"], None),
        ("asknone", ["--permission-prompts", "none"], {"permissions": {"ask": rules}}),
        ("askdontask", ["--permission-mode", "dontAsk"], {"permissions": {"ask": rules}}),
        ("deny", [], {"permissions": {"deny": rules}}),
    ]
    for tag, extra, settings in candidates:
        if only and tag not in only:
            continue
        r, _ = run_fork(cwd, h.sid, model, tag, PROBE.format(t=tag), extra, settings)
        results["forks"].append(r)
    with open(os.path.join(root, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1)
    kt.log("results", os.path.join(root, "results.json"))


def second(root, sid, only):
    """--reuse ROOT SID: wildcard rules, --max-turns with a tool that would run, settings sources left out, and
    the panel's own text with the flags chosen."""
    cwd = os.path.join(root, "T")
    model = kt.usages(kt.transcript(cwd, sid))[-1]["model"]
    with open(os.path.join(root, "results.json"), encoding="utf-8") as f:
        results = json.load(f)
    stop = (["--permission-mode", "dontAsk"], {"permissions": {"ask": ["*"]}})
    candidates = [
        ("maxturns2", ["--max-turns", "1"], None, None),
        ("askstar", ["--permission-mode", "dontAsk"], {"permissions": {"ask": ["*"]}}, None),
        ("askstarnone", ["--permission-prompts", "none"], {"permissions": {"ask": ["*"]}}, None),
        ("denystar", [], {"permissions": {"deny": ["*"]}}, None),
        ("nosources", ["--permission-mode", "dontAsk", "--setting-sources", ""], None, None),
        ("final", stop[0], stop[1], panel_text()),
        ("cap", stop[0] + ["--max-turns", "1"], stop[1], None),
        ("capfinal", stop[0] + ["--max-turns", "1"], stop[1], panel_text()),
    ]
    for tag, extra, settings, prompt in candidates:
        if only and tag not in only:
            continue
        r, _ = run_fork(cwd, sid, model, tag, prompt or PROBE.format(t=tag), extra, settings)
        results.setdefault("second", []).append(r)
    with open(os.path.join(root, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1)
    kt.log("results", os.path.join(root, "results.json"))


if __name__ == "__main__":
    main()
