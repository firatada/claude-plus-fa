"""The voice host experiment (phase 1, not part of the panel yet).

A Claude Code of our own runs hidden in a pseudo-console (ConPTY through pywinpty), in its own
folder, so its own hold-to-talk records the microphone and writes the transcript into its prompt
line. We feed it held-Space key repeats and read its screen with a VT emulator (pyte).

Rules this module keeps: Enter (CR or LF) is never written to the hidden Claude Code's prompt;
the only keys sent are Space, Ctrl+U / Ctrl+K (clear the line), Ctrl+E (end of line), and the
trust question's answer when that question is on screen. Nothing calls Anthropic directly.
"""
import json
import os
import re
import shutil
import threading
import time

import pyte
from winpty import PtyProcess

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
SETTINGS = {"voice": {"enabled": True, "mode": "hold", "autoSubmit": False}}


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


class Screen(pyte.Screen):
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
        """A held Space as this PC's keyboard repeats it: one press, ~500 ms, then every ~33 ms."""
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
