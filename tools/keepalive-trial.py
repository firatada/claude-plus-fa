"""Measures whether a headless forked resume keeps an interactive session's prompt cache alive.

Two throwaway interactive Claude Code sessions run hidden in pseudo-consoles (ConPTY through pywinpty, as the voice
host does: no window, nothing focused), each in its own folder under %TEMP%. Session T gets keep-alives through
`claude -p --resume T --fork-session --no-session-persistence`; session C (the control) gets none. Both then take a
second prompt after a long idle. Usage is read from the transcripts and from the forks' JSON output.

  python -I tools/keepalive-trial.py [--wait1 MIN] [--wait2 MIN] [--quick]

--quick: only the first keep-alive right after the first turn (prefix match, what the fork writes), then turn 2 at
once. Results go to <trial folder>/results.json and are printed. Throwaway transcripts stay under ~/.claude/projects.
"""
import hashlib
import importlib.util
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("voice_host", os.path.join(HERE, "..", "agent-voice-host.py"))
vh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vh)

CLAUDE = os.path.expanduser("~/.claude")
PROJECTS = os.path.join(CLAUDE, "projects")
SESSIONS = os.path.join(CLAUDE, "sessions")
KEEPALIVE = "Keep-alive from claude-plus. This is not a task: use no tool, do not continue or check any work. Reply with the single word ok."
EXE = shutil.which("claude") or os.path.expanduser(r"~\.local\bin\claude.exe")
LOG = []


def log(*a):
    line = time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a)
    LOG.append(line)
    print(line, flush=True)


def slug(path):
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def transcript(cwd, sid):
    return os.path.join(PROJECTS, slug(cwd), sid + ".jsonl")


def usages(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                m = o.get("message") or {}
                if o.get("type") == "assistant" and not o.get("isSidechain") and m.get("usage"):
                    u = m["usage"]
                    out.append({"at": o.get("timestamp"), "model": m.get("model"), "id": m.get("id"),
                                "input": u.get("input_tokens"), "read": u.get("cache_read_input_tokens"),
                                "write": u.get("cache_creation_input_tokens"),
                                "write_1h": (u.get("cache_creation") or {}).get("ephemeral_1h_input_tokens"),
                                "write_5m": (u.get("cache_creation") or {}).get("ephemeral_5m_input_tokens")})
    except OSError:
        pass
    # one API message is written as several lines (one per content block): keep the last line of each id
    seen, uniq = {}, []
    for u in out:
        if u["id"] in seen:
            uniq[seen[u["id"]]] = u
        else:
            seen[u["id"]] = len(uniq)
            uniq.append(u)
    return uniq


def digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest(), os.path.getsize(path)


class Host(vh.VoiceHost):
    def __init__(self, cwd, sid):
        super().__init__(log=lambda *a: None, cols=140, rows=45)
        self.cwd, self.sid = cwd, sid

    def start(self):
        argv = [EXE, "--dangerously-skip-permissions", "--session-id", self.sid]
        env = vh.clean_env()
        self.proc = vh.PtyProcess.spawn(argv, cwd=self.cwd, env=env, dimensions=(self.rows, self.cols))
        threading.Thread(target=self._pump, daemon=True).start()

    def ready(self, timeout=90):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            t = self.text()
            if "Yes, I trust this folder" in t and "No, exit" in t:
                time.sleep(1)
                if not re.search(r"❯ Yes, I trust this folder", self.text()):
                    self.proc.write("\x1b[B")  # down to "Yes, I trust this folder"
                    time.sleep(1)
                if re.search(r"❯ Yes, I trust this folder", self.text()):
                    self.proc.write("\r")
                    time.sleep(3)
                continue
            if self.prompt() is not None and not re.search(r"trust the files", self.text(), re.I):
                return True
            self.changed.wait(0.2)
            self.changed.clear()
        return False

    def submit(self, text):
        self.proc.write("\x1b[200~" + text + "\x1b[201~")
        time.sleep(1.0)
        self.proc.write("\r")


def wait_reply(path, n, timeout=600):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        us = usages(path)
        if len(us) >= n:
            time.sleep(3)  # let the reply's last lines land
            return usages(path)
        time.sleep(1)
    return usages(path)


def fork(cwd, sid, tag):
    before_t = digest(transcript(cwd, sid))
    before_files = set(os.listdir(os.path.join(PROJECTS, slug(cwd))))
    before_sessions = set(os.listdir(SESSIONS))
    argv = [EXE, "-p", "--resume", sid, "--fork-session", "--no-session-persistence", "--output-format", "json",
            "--permission-mode", "dontAsk", "--max-turns", "1",
            "--settings", json.dumps({"disableAllHooks": True, "permissions": {"ask": ["*"]}}), KEEPALIVE]
    t0 = time.time()
    p = subprocess.Popen(argv, cwd=cwd, env=vh.clean_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         stdin=subprocess.DEVNULL, creationflags=0x08000000)
    seen_sessions = set()
    while p.poll() is None:
        for f in set(os.listdir(SESSIONS)) - before_sessions:
            if f not in seen_sessions:
                seen_sessions.add(f)
                try:
                    with open(os.path.join(SESSIONS, f), encoding="utf-8") as h:
                        log(tag, "session file during fork:", f, h.read()[:400])
                except OSError:
                    log(tag, "session file during fork:", f)
        time.sleep(0.1)
    out, err = p.communicate()
    secs = round(time.time() - t0, 1)
    try:
        res = json.loads(out.decode("utf-8", "replace"))
    except ValueError:
        res = {"raw": out.decode("utf-8", "replace")[-2000:]}
    after_t = digest(transcript(cwd, sid))
    new_files = sorted(set(os.listdir(os.path.join(PROJECTS, slug(cwd)))) - before_files)
    r = {"tag": tag, "at": t0, "secs": secs, "exit": p.returncode, "stderr": err.decode("utf-8", "replace")[-1500:],
         "result": res.get("result"), "num_turns": res.get("num_turns"), "usage": res.get("usage"),
         "model_usage": res.get("modelUsage"), "cost": res.get("total_cost_usd"), "session_id": res.get("session_id"),
         "original_untouched": before_t == after_t, "new_files": new_files, "session_files": sorted(seen_sessions)}
    log(tag, json.dumps(r)[:1500])
    return r


def sleep_until(t):
    """Waits by the wall clock, with the PC kept awake (a sleeping PC stops time.sleep() and the cache's clock does
    not stop: the first long run lost its last round that way)."""
    import ctypes
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    while time.time() < t:
        time.sleep(min(10, max(0.1, t - time.time())))


def make_folder(root, name):
    d = os.path.join(root, name)
    os.makedirs(d)
    with open(os.path.join(d, "notes.txt"), "w") as f:
        f.write("throwaway folder for the claude-plus keep-alive trial\n")
    for c in (["git", "init", "-q"], ["git", "add", "notes.txt"],
              ["git", "-c", "user.name=trial", "-c", "user.email=trial@example.invalid", "commit", "-qm", "init"]):
        subprocess.run(c, cwd=d, capture_output=True)
    return d


def blob(seed, words=3500):
    rnd = random.Random(seed)
    vocab = ["river", "copper", "lantern", "meadow", "quartz", "harbor", "violet", "ember", "cobalt", "willow",
             "summit", "fable", "orbit", "saffron", "granite", "tundra", "marble", "nectar", "pixel", "zephyr"]
    return " ".join(rnd.choice(vocab) + str(rnd.randint(0, 999)) for _ in range(words))


def main():
    args = sys.argv[1:]
    quick = "--quick" in args
    wait1 = float(args[args.index("--wait1") + 1]) if "--wait1" in args else 50
    wait2 = float(args[args.index("--wait2") + 1]) if "--wait2" in args else 50
    root = tempfile.mkdtemp(prefix="claude-plus-test-keepalive-")
    log("trial folder", root, "quick" if quick else "wait1 %s min, wait2 %s min" % (wait1, wait2))
    results = {"root": root, "keepalive": KEEPALIVE, "forks": [], "turns": {}}
    names = ["T"] if quick else ["T", "C"]
    hosts = {}
    for n in names:
        cwd = make_folder(root, n)
        h = Host(cwd, str(uuid.uuid4()))
        h.start()
        if not h.ready():
            log(n, "prompt never drawn; screen:", h.text()[-1500:])
            h.stop()
            return
        hosts[n] = h
        log(n, "ready", h.sid, cwd)
    for n, h in hosts.items():
        h.submit("Reference notes for later; no action needed now. Reply with the single word ready.\n\n"
                 + blob(n + h.sid))
    for n, h in hosts.items():
        us = wait_reply(transcript(h.cwd, h.sid), 1)
        results["turns"][n] = {"sid": h.sid, "cwd": h.cwd, "turn1": us}
        log(n, "turn 1", json.dumps(us))
    T = hosts["T"]
    results["forks"].append(fork(T.cwd, T.sid, "fork-0"))
    if not quick:
        t1 = time.time()
        sleep_until(t1 + wait1 * 60)
        results["forks"].append(fork(T.cwd, T.sid, "fork-%dmin" % wait1))
        sleep_until(t1 + (wait1 + wait2) * 60)
    for n, h in hosts.items():
        h.submit("Reply with the single word done.")
    for n, h in hosts.items():
        us = wait_reply(transcript(h.cwd, h.sid), 2)
        results["turns"][n]["turn2"] = us[1:]
        log(n, "turn 2", json.dumps(us[1:]))
    for h in hosts.values():
        h.stop()
    results["log"] = LOG
    with open(os.path.join(root, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1)
    log("results", os.path.join(root, "results.json"))


if __name__ == "__main__":
    main()
