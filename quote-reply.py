# UserPromptSubmit hook: reply to a passage. Select a passage of Claude's reply in the terminal (Windows
# Terminal's copyOnSelect puts it on the clipboard), type ": my answer" and press Enter: the clipboard's text
# goes to Claude as additionalContext, the passage being replied to, and a one-line systemMessage shows the user
# what was quoted. A prompt without the leading ":" is left alone and this prints nothing.
#
# Never quotes a secret: a clipboard a password manager marked private (any of the formats in PRIVATE) is not read.
# Never quotes the same clipboard twice: the sequence number it quoted is kept in USED, and the Agent Panel's
# screenshot strip, which shows what a ":" reply would quote, hides the passage once it is used. The panel loads
# this file as a module for clip() and passage(), so both read the clipboard and cut it the same way.
#
# Part of claude-plus. Edit the copy in the claude-plus-fa folder and rerun install.cmd.
import ctypes, json, os, sys, time
from ctypes import wintypes as wt

CAP = 4000      # characters of the passage sent to Claude; a longer one is cut and the context says so
PREVIEW = 60    # characters of the passage in the line the user sees
USED = os.path.join(os.path.expanduser("~"), ".claude", "state", "context", "quote-used.json")
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


def used():
    """The sequence number a ":" prompt last quoted, or None."""
    try:
        with open(USED, encoding="utf-8") as f:
            return json.load(f).get("seq")
    except (OSError, ValueError, AttributeError):
        return None


def flat(text, n):
    """The text on one line, whitespace collapsed, cut to n characters with an ellipsis."""
    line = " ".join(text.split())
    return line if len(line) <= n else line[:n].rstrip() + "…"


def passage(text):
    """What a ":" reply sends of this clipboard text: (the passage, the number of characters CAP cut off), or None
    when there is nothing to quote. The strip's preview shows the same, so it never promises more than goes."""
    quote = (text or "").replace("\r\n", "\n").strip()
    return (quote[:CAP], max(0, len(quote) - CAP)) if quote else None


def reply(prompt):
    """The hook's answer for one prompt: None (prompt untouched, nothing printed), or the JSON object to print."""
    if not prompt.lstrip().startswith(":"):
        return None
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
        context = ("The user is replying to this passage from the conversation (they selected it, and the \":\" "
                   "at the start of their prompt marks the reply):\n<quote>\n%s\n</quote>" % quote)
        if more:
            context += "\nThe passage was cut to its first %d of %d characters." % (CAP, CAP + more)
        try:
            os.makedirs(os.path.dirname(USED), exist_ok=True)
            with open(USED + ".tmp", "w", encoding="utf-8") as f:
                json.dump({"seq": n, "at": time.time()}, f)
            os.replace(USED + ".tmp", USED)
        except OSError:
            pass  # the quote still goes; only the strip's preview stays up
        return {"systemMessage": "Quoted: " + flat(quote, PREVIEW),
                "hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}}
    return {"systemMessage": "Reply: " + note}


if __name__ == "__main__":
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        out = reply(data.get("prompt") or "")
        if out:
            sys.stdout.write(json.dumps(out, ensure_ascii=True))  # ASCII: the console code page cannot spoil it
    except Exception:
        pass  # never block a prompt over a convenience
    sys.exit(0)
