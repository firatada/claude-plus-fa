# The Field Agents reply window's rules, loaded by agent-panel.pyw (importlib): Claude's replies in a session since
# the prompt the user last typed there, read from its transcript a piece at a time; the answers drafted under them,
# kept per session until they are sent; the replies pinned per session; the one line Send to Session types into the
# session's prompt, and the text Copy puts on the clipboard. Nothing here touches a window: tools/test-reply.py
# tests it on made-up transcripts.
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


class Drafts:
    """The answers typed under a session's paragraphs, kept in DRAFTS across closes and restarts until they are
    sent: {session id: {paragraph key: {"text", "passage", "at"}}}. A key is "<message id>#<block>". An answer goes
    once the user types a newer prompt in that session than the answer (the reply it answered is gone then)."""

    def __init__(self, path=DRAFTS):
        self.path = path
        self.data = {sid: {k: a for k, a in (d or {}).items() if isinstance(a, dict) and a.get("text")}
                     for sid, d in load_json(path).items() if isinstance(d, dict)}

    def get(self, sid):
        return dict(self.data.get(sid) or {})

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
    tmp = "%s.%d.tmp" % (path, os.getpid())
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


# ---------- the line sent, the text copied ----------
def one_line(text):
    """An answer as one line: lines joined with a space, whitespace collapsed. A ":N" the user typed inside it goes
    in backticks, so quote-reply.py never takes it for a marker of its own (code is never a marker)."""
    return MARKER.sub(lambda m: "`%s`" % m.group(0), " ".join((text or "").split()))


def send_line(pairs):
    """The one line typed into the session's prompt: ":N answer" for each (number in the session's list, answer)."""
    return " ".join(":%d %s" % (n, one_line(a)) for n, a in pairs if one_line(a))


def copy_text(answers):
    """Copy: the same line, numbered 1, 2, ... in the window's order, then each passage under its number, for a
    prompt or a message anywhere else. answers: [(passage, answer)]."""
    answers = [(p, a) for p, a in answers if one_line(a)]
    if not answers:
        return ""
    out = [send_line((k + 1, a) for k, (_, a) in enumerate(answers)), ""]
    for k, (p, _) in enumerate(answers):
        out += [":%d" % (k + 1)] + ["> " + line if line.strip() else ">" for line in p.strip().splitlines()] + [""]
    return "\n".join(out).rstrip() + "\n"


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
