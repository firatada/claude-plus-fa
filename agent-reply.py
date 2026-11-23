# The Field Agents reply window's rules, loaded by agent-panel.pyw (importlib): Claude's replies in a session since
# the prompt the user last typed there, read from its transcript a piece at a time; the answers drafted under them,
# kept per session until they are sent; the replies pinned per session; the text Send to Session pastes into the
# session's prompt (each reply answered, whole, with the answers inside it), which Copy puts on the clipboard;
# Windows voice typing for an answer box held with Space (Voice, the one part that sends keys: Win+H, only while the
# reply window is in front). tools/test-reply.py tests the rest on made-up transcripts.
#
# A reply is the text of one main-thread assistant message that ended its turn (stop_reason "end_turn"): its text
# blocks, joined. Not a reply: thinking, tool calls and their results, the text Claude writes between tool calls
# (a message that ended in "tool_use"), a subagent's lines (isSidechain), Claude Code's own <synthetic> messages
# and API error messages. Several replies can follow one prompt (a background task's notification starts a turn
# of its own); every one since the prompt is kept, in order. A prompt the user types starts the list afresh.
#
# Part of claude-plus. Edit the copy in the claude-plus-fa folder and rerun install.cmd.
import ctypes, datetime, json, os, re, threading, time

TAIL = 4 * 1048576     # bytes read from a transcript's end at the first read; MORE when no prompt is in them
MORE = 32 * 1048576
DRAFTS = os.path.join(os.path.expanduser("~"), ".claude", "state", "agent-panel", "reply-drafts.json")
PINS = os.path.join(os.path.expanduser("~"), ".claude", "state", "agent-panel", "reply-pins.json")
PIN_MAX = 8            # pinned replies per session
MARKER = re.compile(r"(?<!\S):(\d+)(?!\S)")  # a ":N" as quote-reply.py reads it
HEAD = 'My answers inside %s (your reply is quoted with "> ", my answers are marked "My answer:"):'
ANSWER = "My answer:"  # leads each answer inside the quoted reply


def stamp(ts):
    """An ISO time from a transcript line ("2026-11-23T10:15:00.123Z") as epoch seconds, or None."""
    try:
        return datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def reply_part(o):
    """(message id, [text, ...], stop_reason, epoch) of a transcript line that is part of a main-thread assistant
    message, else None. Claude Code writes one line per content block, each carrying the message's stop_reason."""
    if o.get("type") != "assistant" or o.get("isSidechain") or o.get("isApiErrorMessage"):
        return None
    m = o.get("message")
    if not isinstance(m, dict) or m.get("model") == "<synthetic>":
        return None
    c = m.get("content")
    texts = [c] if isinstance(c, str) else [b.get("text") or "" for b in c or []
                                             if isinstance(b, dict) and b.get("type") == "text"]
    return m.get("id") or o.get("uuid"), [t for t in texts if t.strip()], m.get("stop_reason"), stamp(o.get("timestamp"))


class Transcript:
    """One session's transcript, read from where the last read stopped: the prompt last typed ({"at", "text"}) and
    the assistant messages since it. typed(o) is the panel's typed_prompt(): the text of a line the user typed, else
    None."""

    def __init__(self, path, typed):
        self.path, self.typed = path, typed
        self.pos, self.prompt, self.msgs, self.seen = None, None, {}, set()

    def read(self):
        """Reads what was written since the last read (the first time, the end of the file back to the prompt);
        a file that got shorter is read again from the start. True when anything new was read."""
        size = os.path.getsize(self.path)
        if self.pos is not None and size < self.pos:
            self.pos, self.prompt, self.msgs, self.seen = None, None, {}, set()
        if self.pos == size:
            return False
        if self.pos is None:
            for n in (TAIL, MORE):
                start = max(0, size - n)
                self.prompt, self.msgs, self.seen = None, {}, set()
                end = self.feed(start, size, whole=start == 0)
                if self.prompt or start == 0:
                    break
        else:
            end = self.feed(self.pos, size, whole=True)
        changed = self.pos != end
        self.pos = end
        return changed

    def feed(self, start, size, whole):
        """Reads bytes start..size, line by line; returns the offset after the last whole line (a line still being
        written waits for the next read). whole: start is at the beginning of a line."""
        with open(self.path, "rb") as f:
            f.seek(start)
            data = f.read(size - start)
        cut = data.rfind(b"\n") + 1
        lines = data[:cut].split(b"\n")
        if not whole and lines:
            lines = lines[1:]  # a tail starts inside a line
        for line in lines:
            if b'"assistant"' not in line and b'"user"' not in line and b'"queued_command"' not in line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if isinstance(o, dict):
                self.line(o)
        return start + cut

    def line(self, o):
        uid = o.get("uuid")
        if uid and uid in self.seen:
            return
        if uid:
            self.seen.add(uid)
        t = self.typed(o)
        if t:
            self.prompt, self.msgs = {"at": stamp(o.get("timestamp")), "text": t}, {}
            return
        part = reply_part(o)
        if not part:
            return
        mid, texts, stop, at = part
        m = self.msgs.setdefault(mid, {"texts": [], "stop": None, "at": at})
        m["texts"] += texts
        m["stop"] = stop or m["stop"]
        m["at"] = at or m["at"]

    def replies(self):
        """[{"id", "at", "text"}], every reply since the prompt, oldest first."""
        return [{"id": mid, "at": m["at"], "text": "\n\n".join(m["texts"])} for mid, m in self.msgs.items()
                if m["stop"] == "end_turn" and m["texts"]]


# ---------- drafts ----------
def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def locked(f):
    """A store's method, run under its lock."""
    def run(self, *a, **k):
        with self.lock:
            return f(self, *a, **k)
    run.__doc__ = f.__doc__
    return run


class Drafts:
    """The answers typed under a session's paragraphs, kept in DRAFTS across closes and restarts until they are
    sent: {session id: {paragraph key: {"text", "passage", "at"}}}. A key is "<message id>#<block>". An answer goes
    once the user types a newer prompt in that session than the answer (the reply it answered is gone then)."""

    def __init__(self, path=DRAFTS):
        self.path = path
        # pywebview runs every page call on a thread of its own, beside the panel's loop (expire, get): one at a time
        self.lock = threading.RLock()
        self.data = {sid: {k: a for k, a in (d or {}).items() if isinstance(a, dict) and a.get("text")}
                     for sid, d in load_json(path).items() if isinstance(d, dict)}

    @locked
    def get(self, sid):
        return dict(self.data.get(sid) or {})

    @locked
    def put(self, sid, key, passage, text, now=None):
        """One answer typed, changed or emptied (empty: gone). True when the store changed."""
        d = self.data.setdefault(sid, {})
        text = (text or "").strip()
        old = d.get(key)
        if not text:
            if not old:
                return False
            del d[key]
        elif old and old["text"] == text and old["passage"] == passage:
            return False
        else:
            d[key] = {"text": text, "passage": passage, "at": now or time.time()}
        if not d:
            del self.data[sid]
        self.save()
        return True

    @locked
    def clear(self, sid, keys=None):
        """Sent: the session's answers go (only those keys, when given)."""
        d = self.data.get(sid)
        if not d:
            return False
        for k in list(d) if keys is None else keys:
            d.pop(k, None)
        if not d:
            del self.data[sid]
        self.save()
        return True

    @locked
    def expire(self, sid, prompt_at):
        """A prompt typed at prompt_at: the answers written before it go."""
        d = self.data.get(sid)
        if not d or not prompt_at:
            return False
        old = [k for k, a in d.items() if (a.get("at") or 0) < prompt_at]
        return self.clear(sid, old) if old else False

    def save(self):
        save_json(self.path, self.data)


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())  # two threads never share one
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


# ---------- pinned replies ----------
class Pins:
    """The replies pinned per session, kept in PINS across restarts and after the session closes: {session id:
    [{"id", "at", "text", "pinned"}]}, in the order they were pinned. A pin holds the reply's own text, so it stays
    whatever the transcript does afterwards (newer prompts, compaction). At most PIN_MAX per session: a pin past it
    is refused ("full") until one goes; swap() unpins the oldest and pins the new one in one step. The page's API
    thread changes it while the poll loop reads it: a lock."""

    def __init__(self, path=None):
        self.path = path or PINS
        self.lock = threading.Lock()
        self.data = {}
        for sid, lst in load_json(self.path).items():
            keep = [{"id": str(p["id"]), "at": p.get("at"), "text": str(p["text"]), "pinned": p.get("pinned") or 0}
                    for p in lst or [] if isinstance(p, dict) and p.get("id") and p.get("text")]
            if keep:
                self.data[str(sid)] = keep[-PIN_MAX:]

    def get(self, sid):
        with self.lock:
            return [dict(p) for p in self.data.get(sid) or []]

    def add(self, sid, rid, at, text, now=None):
        """"added", "already" (that reply is pinned), "full" (PIN_MAX pinned: nothing changed) or "empty" (no text)."""
        if not (text or "").strip():
            return "empty"
        with self.lock:
            lst = self.data.setdefault(sid, [])
            if any(p["id"] == rid for p in lst):
                return "already"
            if len(lst) >= PIN_MAX:
                return "full"
            lst.append({"id": rid, "at": at, "text": text, "pinned": now or time.time()})
            self.save()
        return "added"

    def swap(self, sid, rid, at, text, now=None):
        """The oldest pin goes and this reply is pinned in its place (the answer to "full")."""
        with self.lock:
            lst = self.data.get(sid) or []
            if lst and len(lst) >= PIN_MAX and not any(p["id"] == rid for p in lst):
                lst.pop(0)
        return self.add(sid, rid, at, text, now)

    def remove(self, sid, rid):
        with self.lock:
            lst = self.data.get(sid) or []
            keep = [p for p in lst if p["id"] != rid]
            if len(keep) == len(lst):
                return False
            if keep:
                self.data[sid] = keep
            else:
                self.data.pop(sid, None)
            self.save()
        return True

    def save(self):
        save_json(self.path, self.data)


def pin_label(text, words=5, cap=32):
    """A pin's chip: the first words of the reply's first line with text, markdown's marks left out."""
    for line in (text or "").splitlines():
        line = re.sub(r"^\s*(#{1,6}\s+|>\s?|[-*+]\s+|\d{1,3}[.)]\s+|\|)", "", line)
        line = " ".join(re.sub(r"[`*_~|]|\[([^\]]*)\]\([^)]*\)", lambda m: m.group(1) or "", line).split())
        if line:
            cut = " ".join(line.split(" ")[:words])
            if len(cut) > cap:
                cut = cut[:cap].rstrip()
            return cut + ("…" if cut != line else "")
    return "Reply"


# ---------- the text sent, the text copied ----------
def when_of(at, now=None):
    """A reply's time as its heading names it: "17:42" today, "22 Nov 17:42" another day; "" when unknown."""
    if not at:
        return ""
    lt, now = time.localtime(at), time.localtime(now if now is not None else time.time())
    return time.strftime("%H:%M" if lt[:3] == now[:3] else "%d %b %H:%M", lt)


def mine(text):
    """An answer as it goes inside the reply: its lines as typed (trailing spaces and blank lines at either end
    left out). A ":N" in it goes in backticks and a line starting with ">" gets a backslash, so quote-reply.py
    never takes it for a marker and nobody takes it for a quoted line of the reply."""
    lines = [l.rstrip() for l in (text or "").replace("\r\n", "\n").split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if lines:
        lines[0] = lines[0].lstrip()  # it follows "My answer: "
    lines =["\\" + l if l.lstrip().startswith(">") else l for l in lines]
    return MARKER.sub(lambda m: "`%s`" % m.group(0), "\n".join(lines))


def place(lines, line, passage):
    """Where an answer goes: after line (the index of its block's last line, as the page's blocks() counted it)
    when that line is the passage's last; else after the first line that is; None when no line is."""
    want = [l for l in (passage or "").split("\n") if l.strip()]
    if not want:
        return None
    key = lambda l: l.strip().lstrip(">").strip()
    if isinstance(line, int) and 0 <= line < len(lines) and key(lines[line]) == key(want[-1]):
        return line
    return next((k for k, l in enumerate(lines) if l.strip() and key(l) == key(want[-1])), None)


def quote(lines):
    return ["> " + l if l.strip() else ">" for l in lines]


def answer_block(text, when, answers):
    """One reply with the user's answers inside it, as Send to Session pastes it: a heading line, then the reply's
    lines exactly as stored, each quoted with "> ", and each answer unquoted right after the line that ends the block
    it answers, led by "My answer:" and parted from the quoted lines by a blank line (the quoted blank lines that
    stood there are left out). answers: [(line, passage, answer)]. An answer whose block is not found in text (a
    reply no longer stored) goes at the end after its own passage, quoted. "" when no answer has text."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    at, loose = {}, []
    for line, passage, a in answers:
        a = mine(a)
        if not a:
            continue
        k = place(lines, line, passage)
        if k is None:
            loose.append((passage, a))
        else:
            at.setdefault(k, []).append(a)
    if not at and not loose:
        return ""
    out = [HEAD % ("your reply of " + when if when else "your reply"), ""]
    gap = True  # after the heading or an answer: the reply's blank lines there are left out
    for k, l in enumerate(lines if text else []):
        if gap and not l.strip():
            continue
        gap = False
        out += quote([l])
        for a in at.get(k, []):
            out += ["", ANSWER + " " + a, ""]
            gap = True
    for passage, a in loose:
        out += [""] + quote(passage.strip("\n").split("\n")) + ["", ANSWER + " " + a, ""]
    while out and out[-1] in ("", ">"):
        out.pop()
    return "\n".join(out)


def answer_text(replies):
    """Send to Session's paste and Copy's text: answer_block() of each reply answered, in order, a blank line
    between them. replies: [(text, when, answers)]."""
    return "\n\n".join(b for b in (answer_block(*r) for r in replies) if b)


def set_clipboard_text(text):
    """Text alone on the clipboard (CF_UNICODETEXT). False when another program keeps it busy."""
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    kernel32.GlobalAlloc.restype = kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalLock.argtypes = kernel32.GlobalUnlock.argtypes = kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    data = text.encode("utf-16-le") + b"\0\0"
    for _ in range(10):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return False
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(0x0002, len(data))
        if not h:
            return False
        ctypes.memmove(kernel32.GlobalLock(h), data, len(data))
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(13, h):
            kernel32.GlobalFree(h)
            return False
        return True
    finally:
        user32.CloseClipboard()


# ---------- Windows voice typing in an answer box (Space held) ----------
# Windows' own dictation (Win+H) types into the box that has the keyboard; nothing here records or sends sound. The
# page asks for it when Space is held in an answer box (agent-reply.html) and stops it when Space comes up. Win+H
# opens and closes Windows' dictation bar, a no-activate, topmost ApplicationFrameWindow of explorer.exe that is
# cloaked while closed; EnumWindows does not list it, FindWindowEx does. Explorer keeps several such flyouts (the
# emoji panel's among them), so the bar is the one that came out of its cloak after our Win+H (Voice.start()).
SPEECH_KEY = r"Software\Microsoft\Speech_OneCore\Settings\OnlineSpeechPrivacy"
SPEECH_POLICY = r"SOFTWARE\Policies\Microsoft\InputPersonalization"
MIC_KEY = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
VOICE_WAIT = 1.5  # seconds for the dictation bar to show after Win+H
VOICE_OFF = ("Windows voice typing is off. Turn on Online speech recognition in Settings › Privacy › Speech, "
             "then hold Space again.")
VOICE_POLICY = "Windows voice typing is turned off by a policy on this PC."
VOICE_NO_MIC = "No microphone found. Connect one or turn it on in Settings › System › Sound, then hold Space again."
VOICE_MIC_DENIED = "Windows blocks the microphone. Allow it in Settings › Privacy › Microphone, then hold Space again."
VOICE_NOT_STARTED = "Windows voice typing did not start."
# The Settings page a note names, opened by a click on its name in the note (open_settings). VOICE_POLICY has none:
# nothing there the user can change.
VOICE_LINKS = {VOICE_OFF: ("Settings › Privacy › Speech", "ms-settings:privacy-speech"),
               VOICE_MIC_DENIED: ("Settings › Privacy › Microphone", "ms-settings:privacy-microphone"),
               VOICE_NO_MIC: ("Settings › System › Sound", "ms-settings:sound")}


def voice_note(why):
    """What the page is answered when voice typing cannot start: the note, and the Settings page it names, if any."""
    link = VOICE_LINKS.get(why)
    return {"note": why, "link": {"text": link[0], "uri": link[1]}} if link else {"note": why}


def open_settings(uri):
    """Opens a Settings page a note names (no other address), in front: the click on its name made the reply window
    the foreground, so the panel may hand the foreground on (ASFW_ANY) to Settings, which the shell starts."""
    if uri not in {u for _, u in VOICE_LINKS.values()}:
        return False
    ctypes.WinDLL("user32").AllowSetForegroundWindow(-1)  # ASFW_ANY
    try:
        os.startfile(uri)
    except OSError:
        return False
    return True


def reg_value(root, path, name):
    import winreg
    try:
        with winreg.OpenKey(root, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def voice_block(read=reg_value, mics=None):
    """None when Windows voice typing can start here, else the note that says why not. Checked before every start:
    online speech recognition (Windows 10 dictation runs only online), a policy against it, the microphone's
    privacy switch, and at least one recording device."""
    import winreg
    if read(winreg.HKEY_LOCAL_MACHINE, SPEECH_POLICY, "AllowInputPersonalization") == 0:
        return VOICE_POLICY
    if read(winreg.HKEY_CURRENT_USER, SPEECH_KEY, "HasAccepted") != 1:
        return VOICE_OFF
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        if str(read(root, MIC_KEY, "Value") or "").lower() == "deny":
            return VOICE_MIC_DENIED
    if (ctypes.windll.winmm.waveInGetNumDevs() if mics is None else mics) == 0:
        return VOICE_NO_MIC
    return None


class _KI(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("extra", ctypes.c_size_t)]


class _IN(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KI), ("pad", ctypes.c_byte * 32)]
    _fields_ = [("type", ctypes.c_ulong), ("u", _U)]


def flyouts():
    """{hwnd: shown} of explorer's no-activate, topmost ApplicationFrameWindows (the dictation bar is one)."""
    user32, dwm = ctypes.WinDLL("user32"), ctypes.WinDLL("dwmapi")  # own instances: the panel's argtypes stay its own
    user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    user32.FindWindowExW.restype = ctypes.c_void_p
    user32.FindWindowExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p]
    out, h = {}, None
    for _ in range(200):
        h = user32.FindWindowExW(None, h, "ApplicationFrameWindow", None)
        if not h:
            break
        if user32.GetWindowLongW(h, -20) & 0x08000008 != 0x08000008:  # WS_EX_NOACTIVATE | TOPMOST
            continue
        cloak = ctypes.c_ulong()
        dwm.DwmGetWindowAttribute(ctypes.c_void_p(h), 14, ctypes.byref(cloak), 4)  # DWMWA_CLOAKED
        out[h] = bool(user32.IsWindowVisible(h)) and not cloak.value
    return out


def win_h():
    """Win+H as one SendInput call, so no key the user presses lands between its four events."""
    user32 = ctypes.WinDLL("user32")
    user32.SendInput.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_int]
    keys = [(0x5B, 0), (0x48, 0), (0x48, 2), (0x5B, 2)]  # LWin down, H down, H up, LWin up
    arr = (_IN * 4)()
    for i, (vk, up) in enumerate(keys):
        arr[i].type = 1  # INPUT_KEYBOARD
        arr[i].u.ki = _KI(vk, user32.MapVirtualKeyW(vk, 0), up | (1 if vk == 0x5B else 0), 0, 0)  # LWin is extended
    return user32.SendInput(4, arr, ctypes.sizeof(_IN)) == 4


class Voice:
    """Starts and stops Windows voice typing for the reply window. Keys go only while `front()` says the reply window
    is the foreground window; never to another window."""

    def __init__(self, front):
        self.front, self.bar, self.lock = front, None, threading.Lock()

    def start(self):
        with self.lock:
            why = voice_block()
            if why:
                return voice_note(why)
            if not self.front():
                return {"note": VOICE_NOT_STARTED}
            before = flyouts()
            if not win_h():
                return {"note": VOICE_NOT_STARTED}
            end = time.monotonic() + VOICE_WAIT
            while time.monotonic() < end:
                time.sleep(0.05)
                new = [h for h, shown in flyouts().items() if shown and not before.get(h)]
                if new:
                    self.bar = new[0]
                    return {"on": True}
            return {"note": VOICE_NOT_STARTED}

    def stop(self):
        """Win+H again only while the bar it opened still shows (dictation that stopped by itself is not started
        again) and the reply window is in front."""
        with self.lock:
            bar, self.bar = self.bar, None
            if bar and flyouts().get(bar) and self.front():
                return {"off": win_h()}
            return {"off": False}
