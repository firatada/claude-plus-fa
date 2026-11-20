# UserPromptSubmit hook: reply to a passage, or to several. Select passages of Claude's reply in the terminal
# (Windows Terminal's copyOnSelect puts each on the clipboard) and answer them in one prompt:
#   ": my answer"                 one selection: the reply to it
#   ":1 yes, fix it :3 no"        several: ":N" starts the reply to selection N (the strip shows the numbers)
# The passages go to Claude as additionalContext and a one-line systemMessage shows the user what was quoted. A
# prompt with neither form is left alone and this prints nothing.
#
# The numbered lists, one per session: while the Agent Panel's screenshot strip is on and shown, the panel adds
# every new text clipboard to the list of the session whose terminal tab it was selected in (1, 2, 3, ... in the
# order made; a copy it cannot tie to exactly one session joins no list) and shows the list of the session in front
# in the strip's band. This hook reads only its own session's list (session_id), and after ANY prompt in that
# session writes reset_path(session_id), from which the panel empties that list; other sessions' lists are never
# read or touched. Without a live list (no panel, strip off) a bare ":" quotes the clipboard itself, as before.
#
# Never quotes a secret: a clipboard a password manager marked private (any of the formats in PRIVATE) is not read.
# Never quotes the same clipboard twice: what was quoted is kept in USED. The panel loads this file as a module
# for clip(), passage() and the list's rules, so both read the clipboard and cut it the same way.
#
# Part of claude-plus. Edit the copy in the claude-plus-fa folder and rerun install.cmd.
import ctypes, json, os, re, sys, time
from ctypes import wintypes as wt

CAP = 4000      # characters of the passage sent to Claude; a longer one is cut and the context says so
PREVIEW = 60    # characters of the passage in the line the user sees
STATE = os.path.join(os.path.expanduser("~"), ".claude", "state", "context")
USED = os.path.join(STATE, "quote-used.json")   # the clipboard a bare ":" without a live list last quoted: {seq, at}
LIST = os.path.join(STATE, "quote-list.json")   # the panel's lists: {on, mutex, lists: {session_id: {gen, next, name, items}}}
RESETS = os.path.join(STATE, "quote-used")      # <session_id>.json: {gen, upto, at}, written after a prompt in that session
MUTEX = "Local\\claude-plus-fa-agent-panel"     # the panel's one-instance mutex: the list is live while it exists
MAX_ITEMS = 50  # a safety cap only: a list never used keeps its 50 newest selections (numbers are not reused)
MAX_LISTS = 20  # ... and the panel keeps the lists of the 20 sessions last selected in
MARK = re.compile(r"(?<!\S):(\d+)(?!\S)")  # ":N" at the start, after whitespace, followed by whitespace or the end
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")    # a line opening (or closing) a fenced code block
TICKS = re.compile(r"`+")                       # a run of backticks: opens or closes inline code
BARE = re.compile(r"\s*:(?!\d)")           # a prompt starting with ":" that is not a ":N" marker
# Formats a password manager (or any app) sets to keep a copy out of clipboard history, cloud sync and monitors:
# the first by being there at all, the other two when their DWORD is 0.
EXCLUDE = "ExcludeClipboardContentFromMonitorProcessing"
PRIVATE = ("CanIncludeInClipboardHistory", "CanUploadToCloudClipboard")

user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
user32.GetClipboardData.restype = ctypes.c_void_p
user32.GetClipboardData.argtypes = [wt.UINT]
user32.RegisterClipboardFormatW.argtypes = [wt.LPCWSTR]
user32.RegisterClipboardFormatW.restype = wt.UINT
user32.IsClipboardFormatAvailable.argtypes = [wt.UINT]
user32.GetClipboardSequenceNumber.restype = wt.DWORD
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.OpenMutexW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
kernel32.OpenMutexW.restype = ctypes.c_void_p
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
CF_UNICODETEXT = 13


def seq():
    """The clipboard's sequence number: it changes with every new content, and reading it opens nothing."""
    return user32.GetClipboardSequenceNumber()


def clip(tries=20):
    """(sequence number, text, private) of the clipboard right now. text is None when there is no text or it is
    private; private is True when a private format is there (nothing else is read then) and None when the
    clipboard could not be opened (another program held it all the time). Holds it open well under a millisecond."""
    n = seq()
    if user32.IsClipboardFormatAvailable(user32.RegisterClipboardFormatW(EXCLUDE)):
        return n, None, True  # its mere presence says private; no need to open the clipboard
    marks = [f for f in (user32.RegisterClipboardFormatW(p) for p in PRIVATE) if user32.IsClipboardFormatAvailable(f)]
    if not marks and not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
        return n, None, False
    for _ in range(tries):  # the program that just copied may still hold it
        if user32.OpenClipboard(None):
            break
        time.sleep(0.005)
    else:
        return n, None, None
    try:
        for f in marks:  # private when its DWORD is 0, and when it cannot be read (the safe side)
            h = user32.GetClipboardData(f)
            p = kernel32.GlobalLock(h) if h else None
            if not p:
                return n, None, True
            try:
                if kernel32.GlobalSize(h) < 4 or ctypes.c_uint32.from_address(p).value == 0:
                    return n, None, True
            finally:
                kernel32.GlobalUnlock(h)
        h = user32.GetClipboardData(CF_UNICODETEXT)
        p = kernel32.GlobalLock(h) if h else None
        if not p:
            return n, None, False
        try:
            text = ctypes.wstring_at(p, kernel32.GlobalSize(h) // 2).split("\0", 1)[0]
        finally:
            kernel32.GlobalUnlock(h)
        return n, text, False
    finally:
        user32.CloseClipboard()


def load(path):
    """A JSON file another process replaces whole (write_json), or None. A read that meets the swap is tried again."""
    for _ in range(5):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except PermissionError:
            time.sleep(0.002)
        except (OSError, ValueError):
            return None
    return None


def write_json(path, obj):
    """Writes a file whole: a temporary file swapped in, so a reader never sees half of it. The swap is retried for
    a moment, as Windows refuses to replace a file another process has open (a reader holds it well under 1 ms)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    for i in range(40):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == 39:
                os.remove(tmp)
                raise
            time.sleep(0.005)


def used():
    """The sequence number a ":" prompt last quoted, or None."""
    d = load(USED)
    return d.get("seq") if isinstance(d, dict) else None


def flat(text, n):
    """The text on one line, whitespace collapsed, cut to n characters with an ellipsis."""
    line = " ".join(text.split())
    return line if len(line) <= n else line[:n].rstrip() + "…"


def passage(text):
    """What a ":" reply sends of this clipboard text: (the passage, the number of characters CAP cut off), or None
    when there is nothing to quote. The strip's preview shows the same, so it never promises more than goes."""
    quote = (text or "").replace("\r\n", "\n").strip()
    return (quote[:CAP], max(0, len(quote) - CAP)) if quote else None


def reset_path(sid):
    """Where the hook records that a prompt was submitted in session sid (the panel then empties its list)."""
    return os.path.join(RESETS, re.sub(r"[^\w-]", "", sid) + ".json")


def add(lists, sid, name, seq_no, text, more, now):
    """A selection made in session sid joins its list (lists: the panel's {sid: list}) under the next number. Not
    when it is the same passage as that list's last entry (one selection copied twice). Past MAX_ITEMS the oldest
    drops, past MAX_LISTS the list least recently added to. True when the lists changed."""
    lst = lists.get(sid)
    if not lst:
        gen = max([int(now * 1000)] + [l["gen"] + 1 for l in lists.values()])
        lst = lists[sid] = {"gen": gen, "next": 1, "name": name, "items": []}
    last = lst["items"][-1] if lst["items"] else None
    if last and (last["text"], last["more"]) == (text, more):
        return False
    lst["name"] = name or lst.get("name")
    lst["items"] = (lst["items"] + [{"n": lst["next"], "seq": seq_no, "at": now, "text": text, "more": more}])[-MAX_ITEMS:]
    lst["next"] += 1
    lst["added"] = now
    for old in sorted(lists, key=lambda k: lists[k].get("added") or 0)[:max(0, len(lists) - MAX_LISTS)]:
        del lists[old]
    return True


def apply_reset(lists, sid, record):
    """A prompt was submitted in session sid (record: what its hook wrote): the entries that hook saw go, those
    added after its read keep their numbers. A record of an older list (another gen) changes nothing. An emptied
    list is removed, so the next selection there is :1 again under a new gen. True when the lists changed."""
    lst = lists.get(sid)
    if not lst or not isinstance(record, dict) or record.get("gen") != lst["gen"]:
        return False
    kept = [i for i in lst["items"] if i["n"] > (record.get("upto") or 0)]
    if len(kept) == len(lst["items"]):
        return False
    if kept:
        lst["items"] = kept
    else:
        del lists[sid]
    return True


def drop(lists, sid, n):
    """A selection's x in the strip's band: out of its session's list; the others keep their numbers."""
    lst = lists.get(sid)
    if not lst or not any(i["n"] == n for i in lst["items"]):
        return False
    lst["items"] = [i for i in lst["items"] if i["n"] != n]
    if not lst["items"]:
        del lists[sid]
    return True


def alive(name):
    """The Agent Panel that wrote the list still runs: its one-instance mutex exists (opening it creates nothing)."""
    h = kernel32.OpenMutexW(0x00100000, False, name)  # SYNCHRONIZE
    if h:
        kernel32.CloseHandle(h)
    return bool(h)


def listed(sid):
    """This session's live list ({gen, items: [{n, seq, at, text, more}]}, items empty when nothing was selected in
    it), or None when there is no live list at all: no session id, no file, the strip off or hidden ("on" false),
    or the panel that wrote it gone. Then a bare ":" quotes the clipboard. Other sessions' lists are not looked at."""
    d = load(LIST)
    if not sid or not isinstance(d, dict) or not d.get("on") or not alive(d.get("mutex") or MUTEX):
        return None
    lst = (d.get("lists") or {}).get(sid)
    lst = lst if isinstance(lst, dict) else {}
    return {"gen": lst.get("gen"), "items": [i for i in lst.get("items") or [] if isinstance(i, dict) and i.get("text")]}


def code_spans(prompt):
    """Where the prompt holds code, as (start, end) offsets: fenced blocks (``` or ~~~ to the matching fence, or
    the end) and inline code (a run of backticks to the next run of the same length; an unmatched run is a plain
    backtick). A ":N" inside them is text the user quotes ("`:1 yes :3 no`"), never a marker."""
    spans, prose, pos, fence, start = [], [], 0, None, 0
    for line in prompt.splitlines(True):
        m = FENCE.match(line)
        if fence is None and m:
            fence, start = m.group(1), pos
        elif fence is not None:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not line[m.end():].strip():
                spans.append((start, pos + len(line)))
                fence = None
        else:
            prose.append((pos, line))
        pos += len(line)
    if fence is not None:
        spans.append((start, len(prompt)))
    text = "".join(line for _, line in prose)  # inline code may run over lines, never into a fenced block
    where = [o + k for o, line in prose for k in range(len(line))]
    runs, k = list(TICKS.finditer(text)), 0
    while k < len(runs):
        close = next((j for j in range(k + 1, len(runs)) if len(runs[j].group()) == len(runs[k].group())), None)
        if close is None:
            k += 1
            continue
        spans.append((where[runs[k].start()], where[runs[close].end() - 1] + 1))
        k = close + 1
    return spans


def parse(prompt, known):
    """The prompt's ":N" markers against the listed numbers (known). Returns (answers, unknown, bare):
    answers [(n, reply)] in the order first answered, a number answered twice getting both replies; unknown the
    numbers typed that match nothing (they stay plain text, inside the reply around them); bare True when the
    prompt starts with a ":" that is not a marker. A reply runs from its marker to the next known marker or the
    end; a marker with nothing after it before the next one shares that one's reply (":1 :3 no")."""
    hits, unknown = [], []
    code = code_spans(prompt) if "`" in prompt or "~~~" in prompt else []
    for m in MARK.finditer(prompt):
        if any(a <= m.start() < b for a, b in code):
            continue  # inside code: text the user quotes, not a marker
        if int(m.group(1)) in known:
            hits.append(m)
        elif m.group(1) not in unknown:
            unknown.append(m.group(1))
    texts = [prompt[m.end():(hits[k + 1].start() if k + 1 < len(hits) else len(prompt))].strip()
             for k, m in enumerate(hits)]
    carry = ""
    for k in range(len(texts) - 1, -1, -1):  # an empty reply takes the next one's
        texts[k] = texts[k] or carry
        carry = texts[k]
    answers = {}
    for m, t in zip(hits, texts):
        got = answers.setdefault(int(m.group(1)), [])
        if t and t not in got:
            got.append(t)
    return [(n, "\n".join(r)) for n, r in answers.items()], unknown, bool(BARE.match(prompt))


def block(n, item, reply_text=None):
    """One <quote>: passage n as sent (and how much CAP cut off), with the user's reply when there is one."""
    out = '<quote n="%d">\n<passage>\n%s\n</passage>' % (n, item["text"])
    if item.get("more"):
        out += "\n<note>The passage was cut to its first %d of %d characters.</note>" % (CAP, CAP + item["more"])
    if reply_text is not None:
        out += "\n<reply>\n%s\n</reply>" % (reply_text or "(no text after the marker)")
    return out + "\n</quote>"


def single(quote, more):
    """The context for one passage and a bare ":" (as it always was)."""
    context = ("The user is replying to this passage from the conversation (they selected it, and the \":\" "
               "at the start of their prompt marks the reply):\n<quote>\n%s\n</quote>" % quote)
    if more:
        context += "\nThe passage was cut to its first %d of %d characters." % (CAP, CAP + more)
    return context


def answer(message, context):
    return {"systemMessage": message,
            "hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}}


def mark(record):
    """Records what was quoted (USED): the panel reads it and empties its list; the strip hides the passage."""
    try:
        write_json(USED, dict(record, at=time.time()))
    except OSError:
        pass  # the quote still goes; only the strip's preview stays up


def reset(sid, lst):
    """A prompt was submitted in this session: record the list's generation and every number this read saw, so the
    panel empties it (a selection it added after this read keeps its number). Written only when there is something
    to empty, and only this session's own file: other sessions' lists are never touched."""
    if not sid or not lst or not lst["items"]:
        return
    try:
        write_json(reset_path(sid), {"gen": lst["gen"], "upto": max(i["n"] for i in lst["items"]), "at": time.time()})
    except OSError:
        pass  # the quote still went; the strip keeps showing the list until the next prompt


def from_clipboard():
    """A bare ":" with no live list: the clipboard's text, as before the list existed."""
    n, text, private = clip()
    if private:
        note = "nothing to quote (the clipboard holds a private copy)"
    elif private is None:
        note = "nothing to quote (another program held the clipboard)"
    elif not text or not text.strip():
        note = "nothing to quote (no text on the clipboard)"
    elif n and n == used():
        note = "nothing to quote (that passage was already quoted; select it again to quote it again)"
    else:
        quote, more = passage(text)
        mark({"seq": n})
        return answer("Quoted: " + flat(quote, PREVIEW), single(quote, more))
    return {"systemMessage": "Reply: " + note}


def reply(prompt, sid=None):
    """The hook's answer for one prompt in session sid: None (prompt untouched, nothing printed), or the JSON object
    to print. Any prompt, whatever it holds, resets the session's list (reset())."""
    lst = listed(sid)
    try:
        return answer_for(prompt, lst)
    finally:
        reset(sid, lst)


def answer_for(prompt, lst):
    """The answer for one prompt given its session's list (None: no live list)."""
    if ":" not in prompt:
        return None  # the common case
    if not MARK.search(prompt) and not BARE.match(prompt):
        return None
    items = {i["n"]: i for i in (lst["items"] if lst else [])}
    answers, unknown, bare = parse(prompt, items)
    if not (answers or unknown or bare):
        return None  # its only ":N" sat in code
    nothing = " ".join(":" + u for u in unknown) + " matched nothing" if unknown else ""
    if answers:  # numbered: each answered passage with its reply; the list is used up
        context = ("The user selected passages from the conversation and replied to some of them in this prompt. "
                   "In the prompt, \":N\" starts the reply to passage N and the reply runs to the next such marker "
                   "or the end. Each <quote> below holds passage N and the user's reply to it; act on each reply "
                   "with its own passage.")
        if unknown:
            context += (" Markers that match no passage (%s) are ordinary text of the reply around them."
                        % " ".join(":" + u for u in unknown))
        context += "\n" + "\n".join(block(n, items[n], a) for n, a in answers)
        sent = " ".join(":%d" % n for n, _ in answers)
        return answer("Quoted: " + sent + (" (%s)" % nothing if nothing else ""), context)
    if bare and lst is None:
        out = from_clipboard()
    elif bare and len(items) == 1:  # one listed selection: exactly as a ":" reply always was
        item = next(iter(items.values()))
        out = answer("Quoted: " + flat(item["text"], PREVIEW), single(item["text"], item.get("more")))
    elif bare and items:  # several and no number: all of them go, and Claude is told the reply was not numbered
        context = ("The user selected %d passages from the conversation and replied with a bare \":\" at the start "
                   "of the prompt, without numbering which passage the reply is for. Each <quote> below holds one "
                   "of them. If it is not clear which passage the reply is about, ask the user which item they "
                   "mean before acting." % len(items))
        context += "\n" + "\n".join(block(n, items[n]) for n in sorted(items))
        nums = " ".join(":%d" % n for n in sorted(items))
        out = answer("Quoted all %d (%s): the reply was not numbered, so Claude may ask which one is meant"
                     % (len(items), nums), context)
    elif bare:
        out = {"systemMessage": "Reply: nothing to quote (no selection is listed)"}
    else:  # only markers, none of them listed: plain text
        return {"systemMessage": "Reply: " + nothing + ("" if items else " (no selection is listed)")}
    if nothing:
        out["systemMessage"] += "; " + nothing
    return out


if __name__ == "__main__":
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        out = reply(data.get("prompt") or "", data.get("session_id"))
        if out:
            sys.stdout.write(json.dumps(out, ensure_ascii=True))  # ASCII: the console code page cannot spoil it
    except Exception:
        pass  # never block a prompt over a convenience
    sys.exit(0)
