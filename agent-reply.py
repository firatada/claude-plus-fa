# The Field Agents reply window's rules, loaded by agent-panel.pyw (importlib): Claude's replies in a session since
# the prompt the user last typed there, read from its transcript a piece at a time; the answers drafted under them,
# kept per session until they are sent; the replies pinned per session; the text Send to Session pastes into the
# session's prompt (each reply answered, whole, with the answers inside it), which Copy puts on the clipboard;
# Holding Space dictates through a local speech-to-text server (Dictation). A picture or file pasted or dropped into an
# answer box goes with its answer as a path (with_files(), image_tail(); pictures saved by clip_files() in FILES).
#
# A reply is the text of one main-thread assistant message that ended its turn (stop_reason "end_turn"): its text
# blocks, joined. Not a reply: thinking, tool calls and their results, the text Claude writes between tool calls
# (a message that ended in "tool_use"), a subagent's lines (isSidechain), Claude Code's own <synthetic> messages
# and API error messages. Several replies can follow one prompt (a background task's notification starts a turn
# of its own); every one since the prompt is kept, in order. A prompt the user types starts the list afresh.
#
# Part of claude-plus. Edit the copy in the claude-plus-fa folder and rerun install.cmd.
import ctypes, datetime, json, os, re, subprocess, threading, time, urllib.parse

TAIL = 4 * 1048576     # bytes read from a transcript's end at the first read; MORE when no prompt is in them
MORE = 32 * 1048576
DRAFTS = os.path.join(os.path.expanduser("~"), ".claude", "state", "agent-panel", "reply-drafts.json")
PINS = os.path.join(os.path.expanduser("~"), ".claude", "state", "agent-panel", "reply-pins.json")
FILES = os.path.join(os.path.expanduser("~"), ".claude", "state", "agent-panel", "reply-files")  # pictures pasted into a box
FILE_KEEP = 24 * 3600   # seconds a saved picture outlives the last answer holding it (sweep_files())
FILES_MAX = 20          # pictures and files one answer holds
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")  # what Claude Code attaches when a pasted line is only its path
PIN_MAX = 8            # pinned replies per session
MARKER = re.compile(r"(?<!\S):(\d+)(?!\S)")  # a ":N" as quote-reply.py reads it
HEAD = 'My answers inside %s (your reply is quoted with "> ", my answers start with "My answer"):'
ANSWER = 'My answer to "%s":'  # leads a block's own answer inside the quoted reply, naming the block's first line
ANSWER_BARE = "My answer:"  # leads an answer whose passage has no text to name
PLAIN = '%s, quoted with "> ":'  # leads a reply sent with no answer in it
AREA = 'My answer to all %d blocks from "%s" down to here, as a whole:'  # leads an area's answer, after its last line
NAME_CUT = 60          # characters of the first line named in ANSWER and AREA


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
    sent: {session id: {paragraph key: {"text", "passage", "at"}}}. A key is "<message id>#<block>". An answer lives
    as long as the reply it answers can be shown (stale(): pinned or under Latest)."""

    def __init__(self, path=DRAFTS):
        self.path = path
        # pywebview runs every page call on a thread of its own, beside the panel's loop (expire, get): one at a time
        self.lock = threading.RLock()
        self.data = {sid: {k: a for k, a in (d or {}).items() if isinstance(a, dict) and (a.get("text") or a.get("files"))}
                     for sid, d in load_json(path).items() if isinstance(d, dict)}

    @locked
    def get(self, sid):
        return dict(self.data.get(sid) or {})

    @locked
    def put(self, sid, key, passage, text, files=None, now=None):
        """One answer typed, changed or emptied (no text and no file: gone). files: the paths of the pictures and
        files in its box (files_of()), kept as "files" only when there are any. True when the store changed."""
        d = self.data.setdefault(sid, {})
        text, files = (text or "").strip(), files_of(files)
        old = d.get(key)
        if not text and not files:
            if not old:
                return False
            del d[key]
        elif old and old["text"] == text and old["passage"] == passage and old.get("files", []) == files:
            return False
        else:
            d[key] = {"text": text, "passage": passage, "at": now or time.time()}
            if files:
                d[key]["files"] = files
        if not d:
            del self.data[sid]
        self.save()
        return True

    @locked
    def clear(self, sid, keys=None):
        """The session's answers go (only those keys, when given): expire()'s. Sending keeps them (2026-11-25)."""
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
    def expire(self, sid, prompt_at, shown=(), gone=None):
        """Every pass (prompt_at: the session's last typed prompt) and on an unpin (gone: that reply): stale()'s
        answers go. shown: the replies the window can still show (shown_ids())."""
        d = self.data.get(sid)
        old = stale(d, prompt_at, shown, gone) if d else []
        return self.clear(sid, old) if old else False

    def save(self):
        save_json(self.path, self.data)


def files_of(files):
    """The paths an answer holds, as the page hands them: strings, in order, each once, at most FILES_MAX."""
    out = []
    for f in files if isinstance(files, (list, tuple)) else []:
        p = str((f.get("path") if isinstance(f, dict) else f) or "").strip()
        if p and p not in out:
            out.append(p)
    return out[:FILES_MAX]


def file_kind(path):
    """"image" for a picture Claude Code attaches when its path is pasted on a line of its own, else "file"."""
    return "image" if str(path).lower().endswith(IMAGE_EXT) else "file"


def with_files(text, files, images):
    """An answer's text as it goes out, with one line per picture or file of its box after it, in the box's order:
    "[Image N: <path>]" (N counts the pictures of the whole paste, images, which this extends) or "[File: <path>]".
    Such a line holds text besides the path, so Claude Code keeps it as text, where it stands (2.1.332, measured)."""
    lines = []
    for p in files_of(files):
        if file_kind(p) == "image":
            if p not in images:
                images.append(p)
            lines.append("[Image %d: %s]" % (images.index(p) + 1, p))
        else:
            lines.append("[File: %s]" % p)
    t = (text or "").rstrip()
    return "\n".join(([t] if t.strip() else []) + lines)


def image_tail(text, images):
    """The end of a paste holding pictures: each picture's path alone on a line, in the order with_files() numbered
    them (quoted when it holds a space, as Windows Terminal drops one). Claude Code takes such a line out of the
    pasted text and attaches the picture as [Image #N] (shown before the text), so Claude sees it as a picture pasted
    in the terminal; the "[Image N: <path>]" line says where it belongs."""
    if not text or not images:
        return text
    return text + "\n\n" + "\n".join('"%s"' % p if " " in p else p for p in images)


def save_picture(im, folder=None, now=None):
    """A picture from the clipboard (a PIL image) saved as a PNG in FILES; its path."""
    folder = folder or FILES
    os.makedirs(folder, exist_ok=True)
    t = now or time.time()
    base = os.path.join(folder, "answer-%s-%03d" % (time.strftime("%Y%m%d-%H%M%S", time.localtime(t)), int(t * 1000) % 1000))
    path, n = base + ".png", 2
    while os.path.exists(path):
        path, n = "%s-%d.png" % (base, n), n + 1
    if im.mode not in ("RGB", "RGBA", "L", "LA", "P"):
        im = im.convert("RGBA")
    im.save(path, "PNG", compress_level=1)
    return path


def clip_files(mode="any", grab=None, folder=None):
    """What a paste into an answer box takes from the clipboard when it holds no text: {"files": [paths]} or
    {"note": why not}. Files copied in File Explorer (CF_HDROP) go as their own paths, as Windows Terminal pastes
    them; a picture (a screenshot, an image copied in a browser) is saved in FILES as a PNG and goes as that file's
    path. mode "image" (Alt+V, Claude Code's own image paste key on Windows) takes only a picture. grab stands in for
    PIL.ImageGrab.grabclipboard in tests."""
    if grab is None:
        from PIL import ImageGrab
        grab = ImageGrab.grabclipboard
    try:
        got = grab()
    except Exception:
        got = None
    if isinstance(got, list):
        paths = [p for p in got if isinstance(p, str) and os.path.exists(p)]
        if mode == "image":
            paths = [p for p in paths if file_kind(p) == "image"]
        if paths:
            return {"files": files_of(paths)}
        got = None
    if got is not None and hasattr(got, "save"):
        try:
            return {"files": [save_picture(got, folder)]}
        except (OSError, ValueError):
            return {"note": "Could not save the picture."}
    return {"note": "No picture on the clipboard." if mode == "image" else
            "Nothing to paste: no text, picture or file on the clipboard."}


def picture_from_data(url, folder=None):
    """A picture dropped from somewhere with no file on this PC (a browser's image): its data: URL saved as a PNG in
    FILES; {"files": [path]} or {"note"}."""
    import base64, io
    from PIL import Image
    try:
        head, _, data = str(url).partition(",")
        if not head.startswith("data:image/") or ";base64" not in head:
            return {"note": "Only pictures and files from this PC can go in an answer."}
        with Image.open(io.BytesIO(base64.b64decode(data))) as im:
            im.load()
            return {"files": [save_picture(im, folder)]}
    except Exception:
        return {"note": "Could not read that picture."}


def sweep_files(data, folder=None, now=None, keep=FILE_KEEP):
    """The pictures saved in FILES that no answer holds any more go once they are keep seconds old (the session may
    still read a path it was sent for a while). data: the drafts' store ({sid: {key: answer}}). Answers how many went."""
    folder, now = folder or FILES, now or time.time()
    held = {os.path.normcase(os.path.abspath(p)) for d in (data or {}).values() for a in (d or {}).values()
            if isinstance(a, dict) for p in a.get("files") or []}
    gone = 0
    try:
        names = os.listdir(folder)
    except OSError:
        return 0
    for n in names:
        p = os.path.join(folder, n)
        try:
            if os.path.normcase(os.path.abspath(p)) in held or not os.path.isfile(p) or now - os.path.getmtime(p) < keep:
                continue
            os.remove(p)
            gone += 1
        except OSError:
            pass
    return gone


def reply_of(key):
    """The reply an answer's key names: "<message id>#<block>" or "<message id>#<a>-<b>" (an area)."""
    return key.rpartition("#")[0] or key


def shown_ids(replies, pins):
    """The replies the window can show for a session: those since its last prompt (Latest) and its pinned ones."""
    return {r["id"] for r in replies or []} | {p["id"] for p in pins or []}


def stale(answers, prompt_at, shown, gone=None):
    """The keys of the answers that go. An answer lives as long as the text it answers can be shown: one on a reply
    in shown (pinned, or under Latest) always stays, across prompts, restarts and sends. One on a reply not shown goes
    once a prompt newer than the answer was typed (prompt_at), or at once when its reply is gone (just unpinned and
    not under Latest). No prompt known: only gone's go."""
    out = []
    for k, a in (answers or {}).items():
        rid = reply_of(k)
        if rid in shown:
            continue
        if rid == gone or (prompt_at and (a.get("at") or 0) < prompt_at):
            out.append(k)
    return out


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
        lines[0] = lines[0].lstrip()  # it follows its lead ("My answer to "…": ")
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


def area_size(block):
    """The number of blocks an area answer covers, from the block part of its key ("3-6": 4); 0 for a paragraph's
    own answer ("3") or a reply sent whole ("")."""
    m = re.fullmatch(r"(\d+)-(\d+)", block or "")
    return int(m.group(2)) - int(m.group(1)) + 1 if m and int(m.group(2)) >= int(m.group(1)) else 0


def lead(passage, n):
    """What leads an answer, naming the first non-blank line of what it answers, so Claude reads which block it
    belongs to: ANSWER for a block's own answer (and a one-block area), AREA with their number for an area of several
    blocks; ANSWER_BARE when the passage has no text. The line is cut at NAME_CUT with "…", its double quotes become
    single ones, and a ":N" in it goes in backticks, so quote-reply.py never takes it for a marker."""
    first = next((l.strip() for l in (passage or "").split("\n") if l.strip()), "")
    if not first:
        return ANSWER_BARE
    if len(first) > NAME_CUT:
        first = first[:NAME_CUT].rstrip() + "…"
    first = MARKER.sub(lambda m: "`%s`" % m.group(0), first.replace('"', "'"))
    return AREA % (n, first) if n >= 2 else ANSWER % first


def quote(lines):
    return ["> " + l if l.strip() else ">" for l in lines]


def answer_block(text, when, answers, head=True):
    """One reply with the user's answers inside it, as Send to Session pastes it: a heading line, then the reply's
    lines exactly as stored, each quoted with "> ", and each answer unquoted right after the line that ends the block
    it answers, led by lead() (ANSWER: My answer to "<the block's first line>":) and parted from the quoted lines by
    a blank line (the quoted blank lines that stood there are left out). answers: [(line, passage, answer[, n])], n the blocks of an area answered as a whole
    (area_size()): its passage is the area's markdown, line its last line, and its answer is led by AREA, so Claude
    reads it as one answer to every block from the area's first line down to it; a block's own answer at that same
    line comes first. An answer whose block is not found in text (a reply no longer stored) goes at the end after its
    own passage, quoted. "" when no answer has text. No answers at all (answers []): the reply alone, quoted under
    PLAIN ("" when it holds no text). head False leaves the heading line out (area_copy())."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    if not answers:
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        name = "Your reply of " + when if when else "Your reply"
        return "\n".join([PLAIN % name, ""] + quote(lines)) if lines else ""
    at, loose = {}, []
    for line, passage, a, *n in answers:
        a = mine(a)
        if not a:
            continue
        a = lead(passage, n[0] if n else 0) + " " + a
        k = place(lines, line, passage)
        if k is None:
            loose.append((passage, a))
        else:
            at.setdefault(k, []).append(a)
    if not at and not loose:
        return ""
    out = [HEAD % ("your reply of " + when if when else "your reply"), ""] if head else []
    gap = True  # after the heading or an answer: the reply's blank lines there are left out
    for k, l in enumerate(lines if text else []):
        if gap and not l.strip():
            continue
        gap = False
        out += quote([l])
        for a in at.get(k, []):
            out += ([] if out[-1] == "" else [""]) + [a, ""]  # two answers at one line: one blank line between
            gap = True
    for passage, a in loose:
        out += [""] + quote(passage.strip("\n").split("\n")) + ["", a, ""]
    while out and out[-1] in ("", ">"):
        out.pop()
    return "\n".join(out)


def area_copy(text, answers):
    """What the area's copy puts on the clipboard (the page's areaClip()): text, the markdown of the blocks in the frame
    (or of the one paragraph a drag opened), as it is while no answer in it has text; once one has, those lines quoted
    with "> " and every answer right after its block as answer_block() places them (the area's own after its last
    line, led by AREA, or by ANSWER for one block). No heading line: the quoted lines and the leads say what it is.
    answers: [(line in text's own lines, passage, answer, n)] as in answer_block()."""
    return (answer_block(text, "", answers, head=False) if answers else "") or (text or "")


def format_answered_replies(replies):
    """Send to Session's paste and Copy's text: answer_block() of each reply answered, in order, a blank line
    between them. replies: [(text, when, answers)]."""
    return "\n\n".join(b for b in (answer_block(*r) for r in replies) if b)


def set_clipboard_text(text, expect=None, user32=None, kernel32=None):
    """Text alone on the clipboard (CF_UNICODETEXT). Answers the clipboard's sequence number after it (True when
    Windows gives none), False when another program keeps it busy. expect, a sequence number this returned before: the
    clipboard is written only while it still holds that copy (checked with the clipboard open, so nothing can come in
    between), else None and nothing changes (the area's copy following its answer never covers what the user copied
    since). user32 and kernel32 stand in for Windows' in tests."""
    user32, kernel32 = user32 or ctypes.windll.user32, kernel32 or ctypes.windll.kernel32
    kernel32.GlobalAlloc.restype = kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalLock.argtypes = kernel32.GlobalUnlock.argtypes = kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.GetClipboardSequenceNumber.restype = ctypes.c_uint32
    data = text.encode("utf-16-le") + b"\0\0"
    for _ in range(10):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return False
    try:
        if expect is not None and user32.GetClipboardSequenceNumber() != expect:
            return None
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(0x0002, len(data))
        if not h:
            return False
        ctypes.memmove(kernel32.GlobalLock(h), data, len(data))
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(13, h):
            kernel32.GlobalFree(h)
            return False
    finally:
        user32.CloseClipboard()
    return user32.GetClipboardSequenceNumber() or True


# ---------- Dictation in an answer box (Space held) ----------
# The page records the microphone while Space is held in an answer box (agent-reply.html, MediaRecorder) and hands
# the recording here when Space comes up; it goes to a local speech-to-text server (Voice to Text) and its text comes
# back to be typed at the caret. Nothing is kept: the sound lives only in memory for the one request. The server's
# contract: POST <url>/api/dictate, body the audio of one utterance (WebM/Opus or WAV), query parameters `language`
# and `prompt` (words the recogniser should spell as given), answer {"text", "language", "ms"}; errors as JSON with
# an "error" (or "message"); GET <url>/api/health answers 200 while it runs. claude-plus.local.json (outside the repo):
# "dictation_url" (default DICTATE_URL), "dictation_server" (the server's start script, started hidden when it is
# not running), "dictation_app" (what the note's link opens when the server cannot be reached), "dictation_language"
# (default DICTATE_LANGUAGE) and "dictation_terms" (the word list sent as the prompt; written with DICTATE_TERMS the
# first time it is missing, so the user can edit it there).
DICTATE_URL = "http://127.0.0.1:8765"
DICTATE_LANGUAGE = "tr"
DICTATE_TERMS = ["deploy", "commit", "push", "pull request", "merge", "branch", "rebase", "repo", "GitHub", "session",
                 "panel", "Codex", "Claude", "Claude Code", "prompt", "hook", "agent", "subagent", "workflow",
                 "README", "memory.md", "CLAUDE.md", "handoff", "terminal", "token", "context", "API", "backend",
                 "frontend", "debug", "build", "test", "bug", "fix", "screenshot", "tooltip", "dashboard", "markdown",
                 "JSON", "Python", "JavaScript", "TypeScript", "npm", "localhost", "server", "endpoint"]
DICTATE_START_WAIT = 30   # seconds the server gets to answer its health check after it is started
DICTATE_WAIT = 120        # seconds one transcription may take
DICTATE_NOT_RUNNING = "Voice to Text is not running at {url}."
DICTATE_NOT_STARTED = "Voice to Text did not start."
DICTATE_OPEN = " Open Voice to Text to see why, then hold Space again."  # after either, when "dictation_app" is set
DICTATE_START = " Start it, then hold Space again."  # after DICTATE_NOT_RUNNING without it
DICTATE_FAILED = "Voice to Text could not turn this into text: {why}"
DICTATE_EMPTY = "Nothing was heard. Hold Space and speak again."
DICTATE_CONTEXT = 200      # characters of the words already in the box sent with a piece as the prompt
# What a note's link may open (open_settings): the two Windows Settings pages the page's microphone notes name, and
# APP_LINK, the "dictation_app" the user set (its words in the note: "Open Voice to Text").
APP_LINK = "app:voice-to-text"
SETTINGS_LINKS = ("ms-settings:privacy-microphone", "ms-settings:sound")


def dictation_settings(local):
    """(url, server, app, language, terms) from claude-plus.local.json through `local` (key -> value or None)."""
    url = local("dictation_url")
    url = url.rstrip("/") if isinstance(url, str) and url.startswith(("http://", "https://")) else DICTATE_URL
    server, app = local("dictation_server"), local("dictation_app")
    lang = local("dictation_language")
    terms = local("dictation_terms")
    terms = [str(t).strip() for t in terms if str(t).strip()] if isinstance(terms, list) else list(DICTATE_TERMS)
    return (url, server if isinstance(server, str) and server else None, app if isinstance(app, str) and app else None,
            lang if isinstance(lang, str) and lang else DICTATE_LANGUAGE, terms)


def dictate_query(language, terms, context=""):
    """The query string of a dictation request: the language, and as the prompt the term list joined by ", ", then
    `context` (the words this hold already put in the box, its last DICTATE_CONTEXT characters), so a piece sent while
    Space is still held reads on from them."""
    from urllib.parse import urlencode
    q = {"language": language}
    context = " ".join(str(context or "").split())[-DICTATE_CONTEXT:]
    prompt = ", ".join(terms) if terms else ""
    if context:
        prompt = (prompt + ". " if prompt else "") + context
    if prompt:
        q["prompt"] = prompt
    return urlencode(q)


def answer_text(body):
    """The text of the server's answer, or ValueError with the server's own reason."""
    v = json.loads(body.decode("utf-8"))
    if not isinstance(v, dict):
        raise ValueError("an answer that is not a JSON object")
    if v.get("error") or v.get("message"):
        raise ValueError(str(v.get("error") or v.get("message")))
    return str(v.get("text") or "").strip()


def start_hidden(path):
    """Starts a server script with no window, in its own folder, detached from the panel (it keeps running when the
    panel ends): a .py or .pyw with the pythonw.exe beside this Python, anything else as it is."""
    import subprocess, sys
    folder = os.path.dirname(os.path.abspath(path))
    if path.lower().endswith((".py", ".pyw")):
        exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        cmd = [exe if os.path.exists(exe) else sys.executable, path]
    else:
        cmd = [path]
    subprocess.Popen(cmd, cwd=folder, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True,
                     creationflags=0x00000008 | 0x00000200 | 0x08000000)  # DETACHED, NEW_PROCESS_GROUP, NO_WINDOW


class Dictation:
    """The reply window's way to the local speech-to-text server: is it up, start it when it is not, and one
    recording in, its text out. `local` reads a key of claude-plus.local.json, `save` writes one."""

    def __init__(self, local, save=None, start=start_hidden):
        self.local, self.save, self.start = local, save, start
        self.lock = threading.Lock()
        self.started = -1e9  # monotonic time of the last start this panel made

    def settings(self):
        if self.save and self.local("dictation_terms") is None:  # the list the user edits, written the first time
            try:
                self.save("dictation_terms", list(DICTATE_TERMS))
            except OSError:
                pass
        return dictation_settings(self.local)

    @staticmethod
    def up(url, timeout=1.0):
        from urllib.request import urlopen
        try:
            with urlopen(url + "/api/health", timeout=timeout) as r:
                return r.status == 200
        except Exception:
            return False

    @staticmethod
    def note(text, app):
        """A note about the server; with "dictation_app" set it ends by naming it, and those words are the link."""
        if app:
            return {"note": text + DICTATE_OPEN, "link": {"text": "Open Voice to Text", "uri": APP_LINK}}
        return {"note": text + (DICTATE_START if text.startswith("Voice to Text is not running") else "")}

    def starting(self):
        return time.monotonic() - self.started < DICTATE_START_WAIT

    def ready(self):
        """{"up"} when the server answers; {"starting"} when it was not running and has been started (now or less
        than DICTATE_START_WAIT ago); else the note that says why it cannot be reached."""
        url, server, app, _, _ = self.settings()
        if self.up(url):
            return {"up": True}
        with self.lock:
            if self.starting():
                return {"starting": True}
            if not server or not os.path.exists(server):
                return self.note(DICTATE_NOT_RUNNING.format(url=url), app)
            try:
                self.start(server)
            except OSError:
                return self.note(DICTATE_NOT_STARTED, app)
            self.started = time.monotonic()
        return {"starting": True}

    def wait(self):
        """Waits for a server ready() started to answer its health check: {"up"} or the note."""
        url, _, app, _, _ = self.settings()
        while True:
            if self.up(url):
                return {"up": True}
            if not self.starting():
                return self.note(DICTATE_NOT_STARTED, app)
            time.sleep(0.25)

    def transcribe(self, audio, mime, kind="whole", context=""):
        """One recording (bytes) to {"text"} or the note; starts the server, or waits for it, when it is down. `kind`:
        "whole" (all that was said in one hold, as before text came while speaking), "part" (the open piece while Space
        is held, cut in the middle of speech: sent without the term list, which with a few seconds of Turkish cut
        mid-word made the recogniser loop for 6 to 32 s and answer garbage, trials.md 2026-11-24) or "close" (a piece
        that ends in a pause or where Space was let go). Only "whole" turns an empty text into the "Nothing was heard"
        note; the others answer {"text": ""}. `context`: see dictate_query()."""
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        url, _, app, language, terms = self.settings()
        if not self.up(url):
            w = self.ready()
            if w.get("starting"):
                w = self.wait()
            if not w.get("up"):
                return w
        req = Request(url + "/api/dictate?" + dictate_query(language, [] if kind == "part" else terms, context),
                      data=audio, method="POST",
                      headers={"Content-Type": mime or "application/octet-stream"})
        try:
            with urlopen(req, timeout=DICTATE_WAIT) as r:
                text = answer_text(r.read())
        except HTTPError as e:
            try:
                answer_text(e.read())
                why = f"it answered {e.code}."
            except ValueError as v:
                why = str(v)
            return {"note": DICTATE_FAILED.format(why=why)}
        except ValueError as v:
            return {"note": DICTATE_FAILED.format(why=str(v))}
        except Exception:
            return self.note(DICTATE_NOT_RUNNING.format(url=url), app)
        return {"text": text} if text or kind != "whole" else {"note": DICTATE_EMPTY}


def open_settings(uri, app=None):
    """Opens what a note's link names, in front: one of SETTINGS_LINKS, or APP_LINK, the "dictation_app" the user
    set (`app`); no other address. The click on the link made the reply window the foreground, so the panel may hand
    the foreground on (ASFW_ANY) to what it starts."""
    if uri == APP_LINK and app:
        target = app
    elif uri in SETTINGS_LINKS:
        target = uri
    else:
        return False
    ctypes.WinDLL("user32").AllowSetForegroundWindow(-1)  # ASFW_ANY
    try:
        if target.lower().endswith((".py", ".pyw")) and os.path.exists(target):
            start_hidden(target)
        else:
            os.startfile(target)
    except OSError:
        return False
    return True


# ---------- links in a reply ----------
# Claude Code prints a markdown link to a file as "text (url)" and wraps long lines itself, so Ctrl+click in Windows
# Terminal opens only the half of a long file:/// address on the first line. The reply window shows the same replies,
# so its links open here instead (ReplyApi.link): a web address in the default browser, a file or folder with
# Windows' own handler for it (os.startfile), never inside the WebView. A Windows path Claude wrote as text or inline
# code is a link only when it exists, checked here (existing_paths), never guessed by the page.
LINK_MISSING = "File not found"
LINK_FAILED = "Could not open this"
LINK_UNKNOWN = "Only web and file links open here"
# a file of these kinds is shown selected in File Explorer instead of being run: a click on a reply's text never
# starts a program or a script
RUN_EXT = {".exe", ".com", ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta",
           ".msi", ".msp", ".scr", ".pif", ".lnk", ".url", ".reg", ".py", ".pyw", ".jar", ".cpl", ".appref-ms"}
WIN_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\[^\\/\s]+[\\/][^\\/\s]+)")  # C:\ or C:/, or \\host\share
PATHS_MAX = 200        # paths checked in one call


def link_path(url):
    r"""The file or folder a link names, as a Windows path: file:///C:/a%20b/x.png -> C:\a b\x.png (escapes
    decoded), file://host/share/x -> \\host\share\x, a Windows path as written (C:\x, C:/x, \\host\share)
    with its slashes turned; None for anything else (a web address, a relative path, a bare name)."""
    u = str(url or "").strip()
    if u[:7].lower() == "file://":
        host, _, rest = u[7:].partition("/")
        rest = urllib.parse.unquote(rest)
        host = urllib.parse.unquote(host)
        u = rest if not host or host.lower() == "localhost" else "\\\\" + host + "\\" + rest
        if re.match(r"/[A-Za-z]:", u):  # file:////C:/x
            u = u[1:]
    if not WIN_PATH.match(u) or "\0" in u:
        return None
    return u.replace("/", "\\")


def existing_paths(paths):
    """Those of the paths (as the page found them in a reply) that name a file or folder on this PC."""
    out = []
    for p in list(paths or [])[:PATHS_MAX]:
        q = link_path(p) if isinstance(p, str) else None
        try:
            if q and os.path.exists(q):
                out.append(p)
        except (OSError, ValueError):
            pass
    return out


def reveal(path):
    """File Explorer with the file selected (a program or script is shown, not run)."""
    subprocess.Popen(["explorer.exe", "/select,", path])


def let_front():
    """The click on the link made the reply window the foreground; what it opens may take the front (ASFW_ANY)."""
    ctypes.WinDLL("user32").AllowSetForegroundWindow(-1)


def open_link(url, startfile=None, show=reveal, front=let_front):
    """A click on a link in a reply: {"ok": True} when it was handed on, or {"note": ...}, the short line the page
    shows at the link (LINK_MISSING when the file or folder is not there)."""
    startfile = startfile or os.startfile
    u = str(url or "").strip()
    try:
        if re.match(r"https?://", u, re.I):
            front()
            startfile(u)
            return {"ok": True}
        p = link_path(u)
        if not p:
            return {"note": LINK_UNKNOWN}
        if not os.path.exists(p):
            return {"note": LINK_MISSING}
        front()
        if os.path.isfile(p) and os.path.splitext(p)[1].lower() in RUN_EXT:
            show(p)
        else:
            startfile(p)
        return {"ok": True}
    except (OSError, ValueError):
        return {"note": LINK_FAILED}
