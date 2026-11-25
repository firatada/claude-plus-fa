"""Unit tests of agent-voice-host.py's Dictation (the panel's takes through the hidden Claude Code), with a fake host
that plays Claude Code's hold-to-talk from the Spaces it gets: no Claude Code, no microphone, no window.

    python -I tools/test-voice-host.py
"""
import ast
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("agent_voice_host", os.path.join(ROOT, "agent-voice-host.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.MISSING = ""  # under -I pywinpty and pyte (user packages) are not importable; the fake host needs neither
# everything ten times faster or so
m.RATE_S, m.GUARD_S, m.SETTLE_S, m.RELEASE_MIN_S = 0.01, 0.2, 0.08, 0.08
m.FINAL_WAIT_S, m.LISTEN_WAIT_S, m.BEAT_S, m.SLOT_WAIT_S = 1.2, 0.5, 0.4, 0.6
m.RESTART_GAPS = (0.2, 0.4)

fails = 0
count = 0


def check(name, ok, detail=""):
    global fails, count
    count += 1
    if not ok:
        fails += 1
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else "  " + str(detail)))


class Fake:
    """Claude Code's hold-to-talk as phase 1 measured it: the 5th quick Space starts listening; words come while
    Spaces keep coming; 200 ms without one ends the hold, "Transcribing…" for a moment, then the final text."""

    def __init__(self, words="merhaba dünya nasılsın", listen=True, note="", error="", unreadable=False,
                 start_fails=False):
        self.words, self.listen, self.note, self.error = words.split() if words else [], listen, note, error
        self.unreadable, self.start_fails = unreadable, start_fails
        self.keys, self.spaces, self.cleared, self.dead = [], [], 0, False
        self.text, self.st, self.trusted = "", "", True
        self.proc = SimpleNamespace(pid=4242)
        self.lock = threading.Lock()
        self.base_status = "ctrl+y to paste deleted text"

    def start(self):
        if self.start_fails:
            raise OSError("no claude here")

    def alive(self):
        return not self.dead

    def stop(self):
        self.dead = True

    def answer_trust(self):
        return False

    def key(self, s):
        assert "\r" not in s and "\n" not in s, "Enter is never sent"
        with self.lock:
            self.keys.append(s)
            if s == " ":
                self.spaces.append(time.monotonic())

    def clear_line(self):
        with self.lock:
            self.text, self.cleared, self.spaces = "", self.cleared + 1, []
            self.st = ""

    def prompt_cells(self):
        return None if self.unreadable else (5, [])

    def sim(self):
        with self.lock:
            sp = self.spaces
            if len(sp) < 5:
                self.st = "keep holding…" if len(sp) >= 2 else self.st
                return
            now = time.monotonic()
            if self.error:
                self.st = self.error
                return
            if not self.listen:
                self.st = "keep holding…"
                return
            held = sp[-1] - sp[4]
            if now - sp[-1] < 0.06:  # still held
                self.st = "listening…"
                n = min(len(self.words), int(held / 0.05))
                self.text = " ".join(self.words[:n])
            elif now - sp[-1] < 0.2:
                self.st = "Transcribing…"
            else:
                self.st = self.note
                self.text = " ".join(self.words)

    def typed(self):
        self.sim()
        return None if self.unreadable else self.text

    def status(self):
        self.sim()
        return " | ".join(x for x in (self.base_status, self.st) if x)

    def wait_for(self, pred, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if pred():
                return True
            time.sleep(0.01)
        return pred()


def make_dictation(*fakes):
    made = list(fakes)
    log = []
    d = m.Dictation(log=log.append, make=lambda: made.pop(0), pids=lambda: [])
    return d, log


def ready(d, timeout=4):
    d.keep(True)
    end = time.monotonic() + timeout
    while time.monotonic() < end and d.state != "ready":
        time.sleep(0.02)
    return d.state == "ready"


def take(d, held, read_every=0.04, released_cb=None):
    """Hold for `held` s reading as the page does, let go, read until done or error: (phases, last read)."""
    phases, r = [], None
    t0 = time.monotonic()
    a = d.hold()
    if not a.get("ok"):
        return a, None
    while time.monotonic() - t0 < held:
        r = d.read()
        if not phases or phases[-1] != r["phase"]:
            phases.append(r["phase"])
        if r["phase"] in ("done", "error"):
            return phases, r
        time.sleep(read_every)
    d.release()
    if released_cb:
        released_cb()
    end = time.monotonic() + 3
    while time.monotonic() < end:
        r = d.read()
        if not phases or phases[-1] != r["phase"]:
            phases.append(r["phase"])
        if r["phase"] in ("done", "error", "idle"):
            break
        time.sleep(read_every)
    return phases, r


def slot_free(d, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end and d.take:
        time.sleep(0.02)
    return d.take is None


# 1. an ordinary take: warm, listening with words, transcribing, done; the prompt emptied; never Enter
f = Fake()
d, log = make_dictation(f)
check("starts and gets ready", ready(d), d.why)
check("emptied the prompt once ready", f.cleared >= 1)
live = []
phases, r = take(d, 0.5)
check("phases warm, listening, transcribing, done", phases[:1] == ["warm"] and "listening" in phases
      and "transcribing" in phases and phases[-1] == "done", phases)
check("final text is what Claude Code wrote", r and r["text"] == "merhaba dünya nasılsın", r)
check("no note, no error", r and not r["note"] and not r["why"], r)
check("prompt emptied after the take", slot_free(d) and f.text == "", (d.take, f.text))
check("only Spaces and line keys sent, never Enter", all(k in (" ",) for k in f.keys), set(f.keys))
check("the log has the take, no text", any(x.startswith("take 1: done") for x in log)
      and not any("merhaba" in x for x in log), log)

# 2. words while held come through read()
f = Fake(words="bir iki üç dört beş altı")
d, _ = make_dictation(f)
ready(d)
d.hold()
seen = set()
for _ in range(20):
    r = d.read()
    if r["text"]:
        seen.add(r["text"])
    time.sleep(0.04)
d.release()
while d.read()["phase"] not in ("done", "error", "idle"):
    time.sleep(0.03)
check("several interim texts while held", len(seen) >= 3, seen)

# 3. "No speech detected." is a note, not an error
f = Fake(words="", note="No speech detected.")
d, _ = make_dictation(f)
ready(d)
phases, r = take(d, 0.4)
check("no speech: done with the note", r and r["phase"] == "done" and r["note"] == "No speech detected."
      and r["text"] == "", r)

# 4. an error on Claude Code's voice line ends the take as an error
f = Fake(error="Voice transcription failed: network error")
d, log = make_dictation(f)
ready(d)
phases, r = take(d, 0.6)
check("error on the voice line: error phase", r and r["phase"] == "error" and "failed" in r["why"], r)
check("error take released at once", d.take is None or d.take["released"] is not None)
check("error logged", any("ERROR" in x for x in log), log)
check("prompt emptied after an error", slot_free(d) and f.text == "")

# 5. no "listening…" within LISTEN_WAIT_S: error, even while still held
f = Fake(listen=False)
d, _ = make_dictation(f)
ready(d)
t0 = time.monotonic()
phases, r = take(d, 2.0)
check("never listening: error while held", r and r["phase"] == "error" and "listening" in r["why"]
      and time.monotonic() - t0 < 1.2, (r, time.monotonic() - t0))

# 6. a screen that cannot be read: the take goes to Voice to Text
f = Fake()
d, log = make_dictation(f)
ready(d)
f.unreadable = True
a = d.hold()
check("unreadable screen: hold answers whisper", "whisper" in a and "screen" in a["whisper"], a)
check("fallback logged with its reason", any("Voice to Text" in x and "screen" in x for x in log), log)

# 7. not started / starting: whisper with the reason
d, _ = make_dictation(Fake())
a = d.hold()
check("not started: whisper", "whisper" in a, a)

# 8. it dies: hold says whisper, keep() restarts after the gap
f1, f2 = Fake(), Fake()
d, log = make_dictation(f1, f2)
ready(d)
f1.dead = True
a = d.hold()
check("dead host: whisper", "whisper" in a and "stopped" in a["whisper"], a)
d.keep(True)  # notices, schedules
check("restart scheduled, not at once", d.host is None and d.state == "off", d.state)
time.sleep(0.25)
check("restarted after the gap", ready(d) and d.host is f2, (d.state, d.why))
phases, r = take(d, 0.4)
check("the restarted host takes holds", r and r["phase"] == "done" and r["text"], r)

# 9. a start that fails backs off
d, log = make_dictation(Fake(start_fails=True), Fake())
d.keep(True)
time.sleep(0.1)
check("failed start: off with the reason", d.state == "off" and "could not start" in d.why, d.why)
d.keep(True)
check("no new start inside the gap", d.state == "off")
time.sleep(0.25)
check("next start after the gap works", ready(d))

# 10. dictation set to whisper: stopped
f = Fake()
d, log = make_dictation(f)
ready(d)
d.keep(False)
time.sleep(0.1)
check("not wanted: host stopped", f.dead and d.host is None and d.state == "off", d.state)

# 11. the page stops reading while Space is "held": let go after BEAT_S
f = Fake()
d, log = make_dictation(f)
ready(d)
d.hold()
time.sleep(0.7)
n = len(f.spaces)
time.sleep(0.15)
check("no reads: Spaces stop", d.take["released"] is not None and len(f.spaces) == n, (d.take["released"], n,
                                                                                      len(f.spaces)))
check("logged why", any("stopped reading" in x for x in log), log)

# 12. the guard: a hold right after a release sends no Space for GUARD_S
f = Fake()
d, _ = make_dictation(f)
ready(d)
phases, r = take(d, 0.3)
slot_free(d)
rel = d.guard_until - m.GUARD_S
d.hold()
time.sleep(0.05)
first = f.spaces[0] if f.spaces else None
check("first Space waits for the guard", first is None or first >= rel + m.GUARD_S - 0.01, (first, rel))
time.sleep(m.GUARD_S)
check("Spaces flow after the guard", len(f.spaces) > 0)
d.release()

# 13. drop: let go, the prompt emptied once settled, the slot free; a second hold waits for it
f = Fake()
d, log = make_dictation(f)
ready(d)
d.hold()
for _ in range(10):
    d.read()
    time.sleep(0.04)
d.drop("given to Voice to Text")
check("dropped take reads idle", d.read()["phase"] == "idle")
check("drop logged", any("dropped: given to Voice to Text" in x for x in log), log)
a = d.hold()  # waits for the slot (SLOT_WAIT_S)
check("hold after drop waits, then works", a.get("ok") and f.text == "", (a, f.text))
d.release()

# 14. pywinpty or pyte missing: never starts, whisper with the reason
m.MISSING = "pywinpty or pyte is not installed (test)"
d, _ = make_dictation(Fake())
d.keep(True)
a = d.hold()
check("missing packages: whisper with the reason", "not installed" in a.get("whisper", ""), a)
m.MISSING = ""

# 15. host_pids(): a session file in HOST_DIR whose pid is alive
tmp = tempfile.mkdtemp()
try:
    os.makedirs(os.path.join(tmp, "sessions"))
    for name, o in (("a.json", {"pid": os.getpid(), "cwd": m.HOST_DIR + "\\"}),
                    ("b.json", {"pid": os.getpid(), "cwd": r"C:\elsewhere"}),
                    ("c.json", {"pid": 999999, "cwd": m.HOST_DIR})):
        with open(os.path.join(tmp, "sessions", name), "w") as fh:
            json.dump(o, fh)
    check("host_pids finds the live one in HOST_DIR alone", m.host_pids(tmp) == [os.getpid()], m.host_pids(tmp))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# 16. the panel: the hidden Claude Code is never one of its sessions; "dictation" read as claude unless whisper
src = open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8").read()
tree = ast.parse(src)
ns = {"os": os, "VOICE_DIR": m.HOST_DIR}
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name in ("is_voice_dir", "dictation_mode"):
        exec(compile(ast.Module([node], []), "agent-panel.pyw", "exec"), ns)
check("is_voice_dir: the host's folder", ns["is_voice_dir"](m.HOST_DIR.lower() + "\\"))
check("is_voice_dir: not another folder", not ns["is_voice_dir"](r"C:\Users\x\apps") and not ns["is_voice_dir"](None))
for value, want in ((None, "claude"), ("claude", "claude"), ("Whisper", "whisper"), (" whisper ", "whisper"),
                    ("other", "claude"), (1, "claude")):
    ns["local"] = lambda k, v=value: v
    check("dictation_mode(%r) is %s" % (value, want), ns["dictation_mode"]() == want)

print("%d checks, %d failed" % (count, fails))
sys.exit(1 if fails else 0)
