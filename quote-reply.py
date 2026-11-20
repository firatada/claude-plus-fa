# UserPromptSubmit hook: reply to a passage, or to several. Select passages of Claude's reply in the terminal
# (Windows Terminal's copyOnSelect puts each on the clipboard) and answer them in one prompt:
#   ": my answer"                 one selection: the reply to it
#   ":1 yes, fix it :3 no"        several: ":N" starts the reply to selection N (the strip shows the numbers)
# The passages go to Claude as additionalContext and a one-line systemMessage shows the user what was quoted. A
# prompt with neither form is left alone and this prints nothing.
#
# The numbered list: while the Agent Panel's screenshot strip is on and shown, the panel adds every new text
# clipboard to LIST (1, 2, 3, ... in the order made) and shows it in the strip's band; this hook only reads it,
# and after a prompt that used it writes USED, from which the panel empties the list. Without that live list (no
# panel, strip off) a bare ":" quotes the clipboard itself, as before the list.
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
USED = os.path.join(STATE, "quote-used.json")   # what the last ":" prompt quoted: {seq, at} or {seq, gen, upto, at}
LIST = os.path.join(STATE, "quote-list.json")   # the panel's numbered list: {on, mutex, gen, next, items: [...]}
MUTEX = "Local\\claude-plus-fa-agent-panel"     # the panel's one-instance mutex: the list is live while it exists
MAX_ITEMS = 9   # a list never used: past 9 selections the oldest drops (its number is not reused)
AGE = 3600      # ... and a selection older than an hour drops; numbering restarts at 1 only when the list is empty
MARK = re.compile(r"(?<!\S):(\d+)(?!\S)")  # ":N" at the start, after whitespace, followed by whitespace or the end
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


def fresh(items, now):
    """The list's items still in it: none older than AGE, at most MAX_ITEMS (the newest). Numbers are kept."""
    items = [i for i in items if now - i.get("at", 0) < AGE]
    return items[-MAX_ITEMS:]


def alive(name):
    """The Agent Panel that wrote the list still runs: its one-instance mutex exists (opening it creates nothing)."""
    h = kernel32.OpenMutexW(0x00100000, False, name)  # SYNCHRONIZE
    if h:
        kernel32.CloseHandle(h)
    return bool(h)


def listed(now):
    """The panel's live list ({gen, items: [{n, seq, at, text, more}]}), or None when there is none: no file, the
    strip off or hidden ("on" false), or the panel that wrote it gone. Then a bare ":" quotes the clipboard."""
    d = load(LIST)
    if not isinstance(d, dict) or not d.get("on") or not alive(d.get("mutex") or MUTEX):
        return None
    d["items"] = fresh([i for i in d.get("items") or [] if isinstance(i, dict) and i.get("text")], now)
    return d


def parse(prompt, known):
    """The prompt's ":N" markers against the listed numbers (known). Returns (answers, unknown, bare):
    answers [(n, reply)] in the order first answered, a number answered twice getting both replies; unknown the
    numbers typed that match nothing (they stay plain text, inside the reply around them); bare True when the
    prompt starts with a ":" that is not a marker. A reply runs from its marker to the next known marker or the
    end; a marker with nothing after it before the next one shares that one's reply (":1 :3 no")."""
    hits, unknown = [], []
    for m in MARK.finditer(prompt):
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


def used_up(lst, items):
    """The USED record for a prompt that used the live list: its generation and every number in it, so the panel
    empties the list (numbers it added after this read stay)."""
    mark({"seq": max(i.get("seq") or 0 for i in items.values()), "gen": lst.get("gen"), "upto": max(items)})


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


def reply(prompt, now=None):
    """The hook's answer for one prompt: None (prompt untouched, nothing printed), or the JSON object to print."""
    if ":" not in prompt:
        return None  # the common case, decided without reading anything
    if not MARK.search(prompt) and not BARE.match(prompt):
        return None
    now = time.time() if now is None else now
    lst = listed(now)
    items = {i["n"]: i for i in (lst["items"] if lst else [])}
    answers, unknown, bare = parse(prompt, items)
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
        used_up(lst, items)
        sent = " ".join(":%d" % n for n, _ in answers)
        return answer("Quoted: " + sent + (" (%s)" % nothing if nothing else ""), context)
    if bare and lst is None:
        out = from_clipboard()
    elif bare and len(items) == 1:  # one listed selection: exactly as a ":" reply always was
        item = next(iter(items.values()))
        used_up(lst, items)
        out = answer("Quoted: " + flat(item["text"], PREVIEW), single(item["text"], item.get("more")))
    elif bare and items:  # several and no number: all of them go, and Claude is told the reply was not numbered
        context = ("The user selected %d passages from the conversation and replied with a bare \":\" at the start "
                   "of the prompt, without numbering which passage the reply is for. Each <quote> below holds one "
                   "of them. If it is not clear which passage the reply is about, ask the user which item they "
                   "mean before acting." % len(items))
        context += "\n" + "\n".join(block(n, items[n]) for n in sorted(items))
        used_up(lst, items)
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
        out = reply(data.get("prompt") or "")
        if out:
            sys.stdout.write(json.dumps(out, ensure_ascii=True))  # ASCII: the console code page cannot spoil it
    except Exception:
        pass  # never block a prompt over a convenience
    sys.exit(0)
