"""The voice host: Claude Code's own hold-to-talk for the reply window's answer boxes.

A Claude Code of the panel's own runs hidden in a pseudo-console (ConPTY through pywinpty), in its own folder
(HOST_DIR), so its hold-to-talk records the microphone and writes the transcript into its prompt line. Space held in
an answer box goes to it as key repeats; its screen is read with a VT emulator (pyte). VoiceHost is the process and its
screen; Dictation is one take after another for the panel (agent-panel.pyw ReplyApi.voice). The experiment tools in
tools/ (voice-host-probe.py, voice-trial.pyw) load this file too.

Rules this module keeps: Enter (CR or LF) is never written to the hidden Claude Code's prompt; the only keys sent are
Space, Ctrl+U / Ctrl+K (clear the line), Ctrl+E (end of line), Ctrl+C (to end it), and the trust question's answer
when that question is on screen. Nothing is submitted, so no transcript is written. Nothing calls Anthropic directly.
"""
import json
import os
import re
import shutil
import threading
import time

try:  # user packages (install.py); without them the panel's dictation stays on Voice to Text
    import pyte
    from winpty import PtyProcess
    MISSING = ""
except Exception as _e:  # noqa: BLE001
    pyte = PtyProcess = None
    MISSING = "pywinpty or pyte is not installed (%s)" % _e

HOST_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "claude-plus", "voice-host")
COLS, ROWS = 120, 40
VOICE_WORDS = ("keep holding", "listening", "transcribing", "speech", "audio", "voice", "microphone",
               "sign in", "log in", "/login", "not available", "unavailable", "failed", "error")
METER = "▁▂▃▄▅▆▇█"
# Sequences pyte does not know and would draw as text ('u', 'q') or misread as colours:
# the kitty keyboard protocol (CSI < u, CSI > 5 u, CSI ? u), XTVERSION (CSI > 0 q),
# modifyOtherKeys (CSI > 4 ; 2 m).
UNKNOWN = re.compile(r"\x1b\[[<>=?][0-9;]*[uqm]")
PARTIAL = re.compile(r"\x1b(\[[<>=?]?[0-9;]*)?$")
# voice on, hold mode, never submit; no hooks and no status line (the maintainer's SessionStart hooks and status
# line have nothing to do for a prompt that is never sent)
SETTINGS = {"voice": {"enabled": True, "mode": "hold", "autoSubmit": False}, "disableAllHooks": True}


def registry_env_names():
    import winreg
    names = set()
    for hive, path in ((winreg.HKEY_CURRENT_USER, "Environment"),
                       (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
        try:
            with winreg.OpenKey(hive, path) as k:
                i = 0
                while True:
                    try:
                        names.add(winreg.EnumValue(k, i)[0].upper())
                    except OSError:
                        break
                    i += 1
        except OSError:
            pass
    return names


def clean_env():
    """As the panel's: no markers of a Claude session that may have started us."""
    keep = registry_env_names()
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("CLAUDE", "ANTHROPIC")) or k.upper() in keep}
    env["TERM"] = "xterm-256color"
    env.pop("NO_COLOR", None)
    return env


class Screen(pyte.Screen if pyte else object):
    """A pyte screen that answers the terminal's reports (cursor position, device attributes)."""

    def __init__(self, cols, rows, reply):
        super().__init__(cols, rows)
        self._reply = reply

    def write_process_input(self, data):
        self._reply(data)

    def set_title(self, param):
        self.title_text = param

    def select_graphic_rendition(self, *attrs, **kw):
        # pyte drops SGR 2 (dim); Claude Code draws its placeholder dim, so keep it as italics
        out = []
        for a in attrs:
            if a == 2:
                out.append(3)
            elif a == 22:
                out += [22, 23]
            else:
                out.append(a)
        super().select_graphic_rendition(*out, **kw)


class VoiceHost:
    def __init__(self, log=None, cols=COLS, rows=ROWS):
        self.log = log or (lambda *a: None)
        self.cols, self.rows = cols, rows
        self.lock = threading.Lock()
        self.proc = None
        self.screen = Screen(cols, rows, self._answer)
        self.stream = pyte.Stream(self.screen)
        self.changed = threading.Event()
        self.raw = []          # (t, chunk) of everything read, for the log
        self.t0 = time.monotonic()
        self.trusted = False
        self.pending = ""      # an escape sequence cut by a read, kept for the next one

    # ---- process ----
    def start(self):
        if MISSING:
            raise RuntimeError(MISSING)
        os.makedirs(HOST_DIR, exist_ok=True)
        exe = shutil.which("claude") or os.path.expanduser(r"~\.local\bin\claude.exe")
        settings = os.path.join(HOST_DIR, "voice-host-settings.json")
        with open(settings, "w", encoding="utf-8") as f:
            json.dump(SETTINGS, f)
        # No tools, no MCP servers, a small model: should a prompt ever be submitted by accident
        # (Claude Code's double tap after release), it can do nothing and costs little.
        argv = [exe, "--tools", "", "--strict-mcp-config", "--model", "haiku", "--settings", settings]
        self.proc = PtyProcess.spawn(argv, cwd=HOST_DIR, env=clean_env(), dimensions=(self.rows, self.cols))
        self.log("spawned", exe, "pid", self.proc.pid, "in", HOST_DIR)
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        while True:
            try:
                data = self.proc.read(65536)
            except EOFError:
                self.log("pty closed")
                break
            except Exception as e:  # noqa: BLE001
                self.log("read failed", repr(e))
                break
            if not data:
                time.sleep(0.01)
                continue
            with self.lock:
                self.raw.append((self.now(), data))
                data = self.pending + data
                m = PARTIAL.search(data)
                self.pending, data = (data[m.start():], data[:m.start()]) if m else ("", data)
                try:
                    self.stream.feed(UNKNOWN.sub("", data))
                except Exception as e:  # noqa: BLE001
                    self.log("pyte failed", repr(e))
            self.changed.set()

    def _answer(self, data):
        # replies to the terminal's queries (DSR, DA) go back as input; never CR/LF
        try:
            self.proc.write(data)
        except Exception:  # noqa: BLE001
            pass

    def alive(self):
        return bool(self.proc) and self.proc.isalive()

    def stop(self):
        if not self.proc:
            return
        pid = self.proc.pid
        # Ctrl+C twice on an empty prompt ends Claude Code itself, which removes its session file
        try:
            self.clear_line()
            self.proc.write("\x03")
            time.sleep(0.3)
            self.proc.write("\x03")
        except Exception:  # noqa: BLE001
            pass
        end = time.monotonic() + 4
        while time.monotonic() < end and self.alive():
            time.sleep(0.1)
        try:
            self.proc.terminate(force=True)
        except Exception:  # noqa: BLE001
            pass
        # the claude.exe tree: kill by pid in case terminate left it
        os.system(f"taskkill /PID {pid} /T /F >NUL 2>&1")
        self.log("stopped pid", pid)

    def now(self):
        return time.monotonic() - self.t0

    # ---- keys ----
    def key(self, s):
        assert "\r" not in s and "\n" not in s, "Enter is never sent"
        self.proc.write(s)

    def clear_line(self):
        """Empty the prompt: end of line, then delete to the start, a few times for wrapped text."""
        for _ in range(3):
            self.key("\x05")   # Ctrl+E
            self.key("\x15")   # Ctrl+U
            time.sleep(0.03)

    def hold_space(self, seconds, delay=0.5, rate=0.033, on_tick=None):
        """A held Space as this PC's keyboard repeats it: one press, ~500 ms, then every ~33 ms. (Dictation sends
        no first gap: the answer box has already waited for the keyboard's own repeat.)"""
        t_end = time.monotonic() + seconds
        self.key(" ")
        n = 1
        time.sleep(delay)
        while time.monotonic() < t_end:
            self.key(" ")
            n += 1
            if on_tick:
                on_tick(n)
            time.sleep(rate)
        return n

    # ---- screen ----
    def lines(self):
        with self.lock:
            return [line.rstrip() for line in self.screen.display]

    def text(self):
        return "\n".join(self.lines()).rstrip()

    def prompt_cells(self):
        """(row, [(char, dim)]) of the prompt's first line, or None."""
        with self.lock:
            buf = self.screen.buffer
            for y in range(self.rows):
                row = buf[y]
                s = "".join(row[x].data for x in range(self.cols))
                if s.lstrip().startswith(("❯", ">")) and y > 0:
                    above = "".join(buf[y - 1][x].data for x in range(self.cols)).strip()
                    if above.startswith("─"):
                        return y, [(row[x].data, row[x].italics) for x in range(self.cols)]
        return None

    def typed(self):
        """What is really in the prompt: the prompt's lines with dim text (the placeholder) left out.
        None when no prompt is drawn."""
        pc = self.prompt_cells()
        if not pc:
            return None
        y0, _ = pc
        out = []
        with self.lock:
            buf = self.screen.buffer
            y = y0
            while y < self.rows:
                row = buf[y]
                s = "".join(row[x].data for x in range(self.cols))
                if y > y0 and s.strip().startswith("─"):
                    break
                chars = "".join(row[x].data if not row[x].italics else " " for x in range(self.cols))
                if y == y0:
                    chars = re.sub(r"^\s*[>❯]\s?", "", chars, count=1)
                out.append(chars.strip())
                y += 1
        text = " ".join(x for x in out if x).replace("\xa0", " ")
        return text.strip().strip(METER).strip()

    def prompt(self):
        """The prompt line's text, or None when no prompt is drawn.
        Claude Code draws the input between two horizontal rules, first line led by '>' (or '❯')."""
        ls = self.lines()
        rule = [i for i, s in enumerate(ls) if re.match(r"^\s*[─━]{10,}\s*$", s)]
        for a, b in zip(rule, rule[1:]):
            body = ls[a + 1:b]
            if body and re.match(r"^\s*[>❯›]\s?", body[0]):
                out = [re.sub(r"^\s*[>❯›]\s?", "", body[0], count=1)] + [s.strip() for s in body[1:]]
                return " ".join(x.strip() for x in out).strip()
        # older look: a box │ > text │
        for s in ls:
            m = re.match(r"^\s*│\s*[>❯]\s?(.*?)\s*│\s*$", s)
            if m:
                return m.group(1).strip()
        return None

    def status(self):
        """Claude Code's voice line under the prompt: 'keep holding…', 'listening…', 'Transcribing…',
        'No speech detected.' and its other notes, as drawn; '' when none."""
        pc = self.prompt_cells()
        ls = self.lines()
        below = ls[pc[0] + 1:] if pc else ls
        found = []
        for line in below:
            for seg in re.split(r"\s{2,}|\s·\s", line.strip()):
                low = seg.lower()
                if any(w in low for w in VOICE_WORDS):
                    found.append(seg.strip())
        return " | ".join(found)

    def wait_for(self, pred, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if pred():
                return True
            self.changed.wait(0.1)
            self.changed.clear()
        return pred()

    def answer_trust(self):
        """The folder-trust question ('Do you trust the files in this folder?' / 'Yes, proceed')
        is answered with its own number key, only while it is on screen."""
        t = self.text()
        if re.search(r"trust the files|Yes, proceed|Yes, I trust", t, re.I):
            self.log("trust question on screen; answering 1")
            self.proc.write("1")
            self.trusted = True
            return True
        return False


# ---------- the panel's takes ----------
# Space held in an answer box of the reply window (agent-reply.html) drives the hidden Claude Code: the page asks for
# a take when its own rule sees a real hold ("hold"), reads the prompt line about every 100 ms while it lasts ("read":
# the words shown dim at the caret), says when Space is let go ("release") or when it gave the take to Voice to Text
# ("drop"). Every way out ends with the hidden prompt emptied; Enter is never sent.
RATE_S = 0.033         # one Space this often while held, as this PC's keyboard repeats it
GUARD_S = 0.6          # no Space this long after a release: Claude Code's double tap submits
SETTLE_S = 0.35        # after the release, the text must stay the same this long, with no voice word shown, to be final
RELEASE_MIN_S = 0.3    # ... and at least this long after the release (Claude Code waits 200 ms for repeats to stop)
FINAL_WAIT_S = 15      # no final text this long after the release: an error, the take goes to Voice to Text
LISTEN_WAIT_S = 2.5    # no "listening…" this long after the first Space: an error (measured 0.2 to 0.3 s)
BEAT_S = 1.5           # the page reads at least this often while Space is held; past it the hold is let go
MAX_HOLD_S = 300       # a hold is let go after this long whatever the page says
SLOT_WAIT_S = 2.0      # a new hold waits this long for the last take's prompt to be emptied
START_WAIT_S = 30      # the prompt must be drawn this long after the start
RESTART_GAPS = (30, 60, 120, 300, 900, 1800)  # seconds before each further start after a start failed or it died
STEADY_S = 300         # up this long: the next death restarts after the first gap again
BUSY_WORDS = ("keep holding", "listening", "transcribing")
QUIET_NOTES = ("no speech detected",)  # Claude Code's notes that are not errors: shown as a short note
ERROR_WORDS = ("failed", "error", "not available", "unavailable", "sign in", "log in", "/login", "denied",
               "permission", "microphone", "not supported", "timed out")


def alive(pid):
    import ctypes
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    ok = k.GetExitCodeProcess(h, ctypes.byref(code))
    k.CloseHandle(h)
    return bool(ok) and code.value == 259  # STILL_ACTIVE


def same_dir(a, b):
    return os.path.normcase(os.path.normpath(a or "")) == os.path.normcase(os.path.normpath(b or ""))


def host_pids(claude_dir=None):
    """Live Claude Codes started in HOST_DIR (their ~/.claude/sessions/<pid>.json says so): a host left by a panel
    that was stopped by force, or this one's own."""
    folder = os.path.join(claude_dir or os.path.expanduser(r"~\.claude"), "sessions")
    out = []
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for n in names:
        if not n.endswith(".json"):
            continue
        try:
            with open(os.path.join(folder, n), encoding="utf-8") as f:
                o = json.load(f)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(o, dict) and o.get("pid") and same_dir(o.get("cwd"), HOST_DIR) and alive(o["pid"]):
            out.append(int(o["pid"]))
    return out


def kill_tree(pid):
    import subprocess
    subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"], capture_output=True,
                   creationflags=0x08000000)  # CREATE_NO_WINDOW


def segments(status):
    return [x.strip() for x in (status or "").split(" | ") if x.strip()]


def base_of(status):
    """What the voice line shows before a take: left out of what the take reports, except Claude Code's quiet notes
    ("No speech detected." of the last take), which must count when they come again."""
    return {x for x in segments(status) if not any(q in x.lower() for q in QUIET_NOTES)}


class Dictation:
    """The panel's one hidden Claude Code and its takes. `log(line)` writes the panel's voice.log (never the text);
    `make` builds the host (tests pass a fake). Every method is safe from any thread."""

    def __init__(self, log=None, make=None, pids=None):
        self.log = log or (lambda s: None)
        self.make = make or (lambda: VoiceHost(log=lambda *a: self.log("host: " + " ".join(map(str, a)))))
        self.pids = pids or host_pids
        self.lock = threading.RLock()
        self.host = None
        self.state = "off"        # off, starting, ready, failed
        self.why = "the hidden Claude Code has not started yet"
        self.fails, self.next_start, self.up_since = 0, 0.0, 0.0
        self.take = None          # the take in hand, until its prompt is emptied
        self.guard_until = 0.0
        self.n = 0                # takes so far, for the log

    # ---- the process ----
    def keep(self, want):
        """Every live pass: start it when wanted and not running (again after RESTART_GAPS when it died or did not
        start); stop it when not wanted. Never blocks."""
        with self.lock:
            if not want:
                if self.host:
                    self.log("not wanted (dictation is set to whisper): stopping")
                    self.stop(wait=False)
                return
            if MISSING:
                self.why = MISSING
                return
            now = time.monotonic()
            if self.state == "starting":
                return
            if self.host and self.host.alive() and self.state == "ready":
                if self.fails and now - self.up_since > STEADY_S:
                    self.fails = 0
                return
            if self.host:  # it stopped, or its screen could not be read
                self.log("the hidden Claude Code %s; restarting in %d s" % (
                    "stopped" if not self.host.alive() else "is not usable (%s)" % self.why, self.gap()))
                old, self.host = self.host, None
                threading.Thread(target=self._end, args=(old,), daemon=True).start()
                self.next_start = now + self.gap()
                self.fails += 1
                self.state, self.why = "off", "the hidden Claude Code stopped; it restarts soon"
                return
            if now < self.next_start:
                return
            self.state, self.why = "starting", "the hidden Claude Code is starting"
            threading.Thread(target=self._start, daemon=True).start()

    def gap(self, i=None):
        i = self.fails if i is None else i
        return RESTART_GAPS[min(max(i, 0), len(RESTART_GAPS) - 1)]

    def _start(self):
        t0 = time.monotonic()
        for pid in self.pids():
            self.log("a hidden Claude Code left by an earlier panel (pid %d): ended" % pid)
            kill_tree(pid)
        try:
            h = self.make()
            h.start()
        except Exception as e:  # noqa: BLE001
            return self._failed(None, "could not start Claude Code: %s" % e)

        def drawn():
            if not h.trusted and h.answer_trust():
                return False
            return h.prompt_cells() is not None
        if not h.wait_for(drawn, START_WAIT_S):
            return self._failed(h, "Claude Code did not show its prompt within %d s" % START_WAIT_S)
        time.sleep(1.0)  # let it finish drawing
        h.clear_line()
        h.wait_for(lambda: h.typed() == "", 3)
        with self.lock:
            if self.state != "starting":  # stopped meanwhile
                threading.Thread(target=self._end, args=(h,), daemon=True).start()
                return
            self.host, self.state, self.why, self.up_since = h, "ready", "", time.monotonic()
        self.log("ready in %.2f s (pid %s)" % (time.monotonic() - t0, h.proc.pid))

    def _failed(self, h, why):
        with self.lock:
            gap = self.gap()
            self.fails += 1
            self.next_start = time.monotonic() + gap
            self.state, self.why = "off", why
        self.log("%s; next try in %d s" % (why, gap))
        if h:
            self._end(h)

    def _end(self, h):
        try:
            h.stop()
        except Exception as e:  # noqa: BLE001
            self.log("stop failed: %r" % e)

    def stop(self, wait=True):
        """The panel exits (or dictation is set to whisper): the take let go, the hidden Claude Code ended."""
        with self.lock:
            t, self.take = self.take, None
            if t:
                t["stop"].set()
            h, self.host = self.host, None
            self.state, self.why = "off", "the hidden Claude Code was stopped"
        if h:
            if wait:
                self._end(h)
            else:
                threading.Thread(target=self._end, args=(h,), daemon=True).start()

    def usable(self):
        """'' when a take can start now, else why not."""
        h = self.host
        if self.state != "ready" or not h:
            return self.why or "the hidden Claude Code is not ready"
        if not h.alive():
            self.state, self.why = "failed", "the hidden Claude Code stopped"
            return self.why
        if h.prompt_cells() is None:
            self.state, self.why = "failed", "its screen could not be read (no prompt line)"
            return self.why
        return ""

    # ---- takes ----
    def hold(self):
        """A real hold began in an answer box: Space goes to the hidden Claude Code from now on (after the guard).
        {"ok": True}, or {"whisper": why} when this take must go to Voice to Text."""
        end = time.monotonic() + SLOT_WAIT_S
        while True:
            with self.lock:
                if not self.take:
                    break
            if time.monotonic() > end:
                return self._whisper("the last take's prompt was not emptied yet")
            time.sleep(0.05)
        with self.lock:
            why = self.usable()
            if why:
                return self._whisper(why)
            h = self.host
            self.n += 1
            now = time.monotonic()
            t = self.take = {"n": self.n, "t0": now, "beat": now, "released": None, "listened": None, "first": None,
                             "text": "", "changed": now, "base": base_of(h.status()), "stop": threading.Event(),
                             "host": h, "end": None, "spaces": 0}
        threading.Thread(target=self._spaces, args=(t,), daemon=True).start()
        return {"ok": True}

    def _whisper(self, why):
        self.log("take to Voice to Text: %s" % why)
        return {"whisper": why}

    def _spaces(self, t):
        h = t["host"]
        wait = self.guard_until - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        while not t["stop"].is_set():
            now = time.monotonic()
            if now - t["beat"] > BEAT_S or now - t["t0"] > MAX_HOLD_S:
                self.log("take %d: let go (%s)" % (t["n"], "the page stopped reading" if now - t["beat"] > BEAT_S
                                                   else "held over %d s" % MAX_HOLD_S))
                self.release()
                break
            try:
                h.key(" ")
                t["spaces"] += 1
            except Exception as e:  # noqa: BLE001
                self.log("take %d: Space could not be sent: %r" % (t["n"], e))
                break
            t["stop"].wait(RATE_S)

    def release(self):
        """Space was let go: no more Spaces; the take ends when the text settles (read())."""
        with self.lock:
            t = self.take
            if not t or t["released"]:
                return {"ok": bool(t)}
            t["released"] = time.monotonic()
            t["stop"].set()
            self.guard_until = t["released"] + GUARD_S
        return {"ok": True}

    def drop(self, why=""):
        """The page gave this take to Voice to Text, or the hold ended before anything was heard: let go, and empty
        the prompt once whatever Claude Code still writes into it has settled."""
        with self.lock:
            t = self.take
            if not t or t["end"]:
                return {"ok": False}
            t["end"] = time.monotonic()
            self.log("take %d dropped%s" % (t["n"], ": " + why if why else ""))
        self.release()
        threading.Thread(target=self._settle_then_clear, args=(t,), daemon=True).start()
        return {"ok": True}

    def _settle_then_clear(self, t):
        h = t["host"]
        end = time.monotonic() + FINAL_WAIT_S
        last, since = None, time.monotonic()
        while time.monotonic() < end and h.alive():
            cur = (h.typed(), h.status())
            if cur != last:
                last, since = cur, time.monotonic()
            busy = any(w in (cur[1] or "").lower() for w in BUSY_WORDS)
            if (not busy and time.monotonic() - since >= SETTLE_S
                    and time.monotonic() - (t["released"] or 0) >= RELEASE_MIN_S):
                break
            time.sleep(0.05)
        self._clear(t)

    def _clear(self, t):
        h = t["host"]
        try:
            for _ in range(3):
                if not h.alive():
                    break
                h.clear_line()
                if h.wait_for(lambda: h.typed() in ("", None), 1.0):
                    break
        except Exception as e:  # noqa: BLE001
            self.log("take %d: the prompt could not be emptied: %r" % (t["n"], e))
        with self.lock:
            if self.take is t:
                self.take = None

    def read(self):
        """The take as the page shows it: {"phase": "warm" | "listening" | "transcribing" | "done" | "error" |
        "idle", "text": the prompt's words, "note": a short note (Claude Code's "No speech detected."),
        "why": the error}. "done" and "error" end the take (its prompt is emptied right after)."""
        with self.lock:
            t = self.take
            if not t or t["end"]:
                return {"phase": "idle", "text": "", "note": "", "why": ""}
            now = time.monotonic()
            t["beat"] = now
            h = t["host"]
            err, status = "", ""
            text = t["text"]
            if not h.alive():
                err = "the hidden Claude Code stopped"
                self.state, self.why = "failed", err
            else:
                typed, status = h.typed(), h.status()
                if typed is None:
                    err = "its screen could not be read (no prompt line)"
                    self.state, self.why = "failed", err
                else:
                    text = typed
            new = [s for s in segments(status) if s not in t["base"]]
            low = " | ".join(new).lower()
            note = "" if (text or "").strip() else next((s for s in new if any(q in s.lower() for q in QUIET_NOTES)), "")
            if not err and not note:
                bad = [s for s in new if any(e in s.lower() for e in ERROR_WORDS)]
                if bad:
                    err = "Claude Code says: " + " | ".join(bad)
            if "listening" in low and t["listened"] is None:
                t["listened"] = now
            if text != t["text"]:
                t["text"], t["changed"] = text, now
                if text.strip() and t["first"] is None:
                    t["first"] = now
            r = t["released"]
            if not err and r is None and t["listened"] is None and now - t["t0"] > LISTEN_WAIT_S:
                err = "no \"listening…\" within %.1f s of the first Space" % LISTEN_WAIT_S
            if not err and r is not None and now - r > FINAL_WAIT_S:
                err = "no final text within %d s of the release" % FINAL_WAIT_S
            done = (not err and r is not None and now - r >= RELEASE_MIN_S and now - t["changed"] >= SETTLE_S
                    and not any(w in status.lower() for w in BUSY_WORDS))
            if err or done:
                t["end"] = now
                t["stop"].set()
                if r is None:
                    t["released"] = now
                    self.guard_until = now + GUARD_S
                self._log_take(t, err, note)
                threading.Thread(target=self._settle_then_clear if err else self._clear, args=(t,),
                                 daemon=True).start()
                return {"phase": "error" if err else "done", "text": t["text"].strip(), "note": note, "why": err}
            phase = "transcribing" if r is not None else "listening" if t["listened"] is not None else "warm"
            return {"phase": phase, "text": text.strip(), "note": "", "why": ""}

    def _log_take(self, t, err, note):
        def at(k):
            return ("%.2f s" % (t[k] - t["t0"])) if t[k] is not None else "never"
        self.log("take %d: %s; listening at %s, first words at %s, held %.2f s (%d Spaces), ended %.2f s after the "
                 "release, %d characters%s" % (
                     t["n"], "ERROR " + err if err else "done", at("listened"), at("first"),
                     t["released"] - t["t0"], t["spaces"], t["end"] - t["released"], len(t["text"].strip()),
                     ", note %r" % note if note else ""))
