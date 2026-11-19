# Windows Terminal's "copyOnSelect", turned on once by install.py and given back by uninstall.py.
#
# Reply to a passage (quote-reply.py) quotes the clipboard, so a selection must reach it as it is made: that is
# Windows Terminal's "copyOnSelect": true. The installer sets it in every Windows Terminal settings file it finds,
# once per file: the record (STATE) says which files it has seen and what each held before, and a file it has seen
# is never touched again, so a user who turns it off later keeps it off through every reinstall. A file that already
# had it on is only recorded. The uninstaller puts the old value back only where the installer changed it and the
# value is still the one it wrote.
#
# The settings file is JSON with comments and trailing commas. Only the one member is edited, as text: every other
# byte stays as it was. Part of claude-plus; not installed, install.py and uninstall.py import it from this folder.
import glob, json, os, shutil, time

KEY = "copyOnSelect"
STATE = os.path.join(os.path.expanduser("~"), ".claude", "state", "copy-on-select.json")
BACKUP = ".before-claude-plus"  # the file as it was before the first change, next to it


def files():
    """Every Windows Terminal settings file on this machine (absent ones skipped, each file once): the Store
    builds (stable, Preview, Canary), the unpackaged one (winget zip, Chocolatey) and Scoop's portable one."""
    local = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    scoop = os.environ.get("SCOOP") or os.path.join(os.path.expanduser("~"), "scoop")
    found = sorted(glob.glob(os.path.join(local, "Packages", "Microsoft.WindowsTerminal*_8wekyb3d8bbwe",
                                          "LocalState", "settings.json")))
    found.append(os.path.join(local, "Microsoft", "Windows Terminal", "settings.json"))
    found += sorted(glob.glob(os.path.join(scoop, "apps", "windows-terminal*", "current", "settings", "settings.json")))
    out, seen = [], set()
    for f in found:
        real = os.path.normcase(os.path.realpath(f))
        if os.path.isfile(f) and real not in seen:
            seen.add(real)
            out.append(f)
    return out


# ----- the text, read as JSON with comments -----
def tokens(text):
    """(kind, start, end) of every token: one of {}[]:, , "string" or "literal"; white space and comments skipped."""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n\ufeff":
            i += 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                raise ValueError("comment not closed")
            i = j + 2
        elif c in "{}[]:,":
            yield c, i, i + 1
            i += 1
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            if j >= n:
                raise ValueError("string not closed")
            yield "string", i, j + 1
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in " \t\r\n,:{}[]/\"":
                j += 1
            yield "literal", i, j
            i = j


def members(text):
    """The root object's members as dicts (key, start of the key, value start and end, comma: the end of the comma
    after it or None), and the root's own braces. ValueError when the text is not one object."""
    t = list(tokens(text))
    if not t or t[0][0] != "{":
        raise ValueError("not an object")
    out, k = [], 1
    while k < len(t) and t[k][0] != "}":
        if t[k][0] != "string" or k + 2 >= len(t) or t[k + 1][0] != ":":
            raise ValueError("bad member at %d" % t[k][1])
        key, vs = json.loads(text[t[k][1]:t[k][2]]), k + 2
        depth, v = 0, vs
        while True:  # an object or array value runs to its closing bracket
            kind = t[v][0]
            depth += kind in "{[" and 1 or kind in "}]" and -1 or 0
            if depth <= 0:
                break
            v += 1
            if v >= len(t):
                raise ValueError("value not closed")
        if depth < 0:
            raise ValueError("bad value at %d" % t[vs][1])
        m = {"key": key, "start": t[k][1], "vs": t[vs][1], "ve": t[v][2], "comma": None}
        k = v + 1
        if k < len(t) and t[k][0] == ",":
            m["comma"] = t[k][2]
            k += 1
        out.append(m)
    if k >= len(t) or len(t) > k + 1:
        raise ValueError("text after the root object")
    return out, t[0][1], t[k][1]


def value(text):
    """The member's value as written ("true", "false", ...), or None when the file has no such member."""
    ms = [m for m in members(text)[0] if m["key"] == KEY]
    return text[ms[-1]["vs"]:ms[-1]["ve"]] if ms else None  # the last one counts, as in the terminal's own reader


def turn_on(text):
    """The text with the member set to true: its value replaced where it is (the last one), or a new first member
    on its own line, indented like the others, with the file's own line ending."""
    ms, open_, close = members(text)
    last = [m for m in ms if m["key"] == KEY]
    if last:
        m = last[-1]
        return text[:m["vs"]] + "true" + text[m["ve"]:]
    nl = "\r\n" if "\r\n" in text else "\n"
    indent = "    "
    if ms:
        line = text[text.rfind("\n", 0, ms[0]["start"]) + 1:ms[0]["start"]]
        if line.strip() == "":
            indent = line
        return text[:open_ + 1] + nl + indent + '"%s": true,' % KEY + text[open_ + 1:]
    return text[:open_ + 1] + nl + indent + '"%s": true' % KEY + nl + text[close:]


def put_back(text, before):
    """The text with the member's earlier value written back (before: the value as it was written), or with the
    member taken out when there was none (the line turn_on() added goes whole, its comma with it)."""
    ms, open_, close = members(text)
    last = [i for i, m in enumerate(ms) if m["key"] == KEY]
    if not last:
        return text
    i = last[-1]
    m = ms[i]
    if before is not None:
        return text[:m["vs"]] + before + text[m["ve"]:]
    if m["comma"] is not None:
        start, end = m["start"], m["comma"]
        line = text.rfind("\n", 0, start)
        rest = text.find("\n", end)
        if text[line + 1:start].strip() == "" and text[end:rest if rest >= 0 else len(text)].strip() == "":
            start = line - (1 if line > 0 and text[line - 1] == "\r" else 0)  # the line with its line break
        return text[:start] + text[end:]
    if i > 0:  # the last member: the comma before it goes instead
        prev = ms[i - 1]
        return text[:prev["ve"]] + text[m["ve"]:]
    if (text[open_ + 1:m["start"]] + text[m["ve"]:close]).strip() == "":  # the only member: "{}" as turn_on() found it
        return text[:open_ + 1] + text[close:]
    return text[:m["start"]] + text[m["ve"]:]


# ----- the record, and the two steps -----
def load(state=STATE):
    try:
        with open(state, encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def save(record, state=STATE):
    os.makedirs(os.path.dirname(state), exist_ok=True)
    with open(state + ".tmp", "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(state + ".tmp", state)


def read(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8")  # a BOM stays as U+FEFF and goes back out the same


def write(path, text):
    with open(path, "wb") as f:  # in place, so a linked settings file stays linked
        f.write(text.encode("utf-8"))


def apply(paths=None, state=STATE):
    """install.py: copyOnSelect on in every settings file not seen before. Returns the lines to print."""
    record, lines = load(state), []
    for path in files() if paths is None else paths:
        key = os.path.normcase(os.path.abspath(path))
        if key in record:
            lines.append("ok      copyOnSelect: left as you have it in %s (set up once already)" % path)
            continue
        try:
            text = read(path)
            before = value(text)
            if before == "true":
                record[key] = {"path": path, "before": "true", "changed": False,
                               "at": time.strftime("%Y-%m-%d %H:%M:%S")}
                save(record, state)
                lines.append("ok      copyOnSelect: already on in %s" % path)
                continue
            new = turn_on(text)
            if value(new) != "true":
                raise ValueError("the edit did not take")
            if not os.path.exists(path + BACKUP):
                shutil.copy2(path, path + BACKUP)
            write(path, new)
        except (OSError, ValueError, UnicodeError) as e:
            lines.append("SKIPPED copyOnSelect in %s: %s" % (path, e))
            continue
        record[key] = {"path": path, "before": before, "changed": True, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        save(record, state)
        lines.append("ok      copyOnSelect turned on in %s (once; turn it off there and it stays off)" % path)
    return lines


def restore(state=STATE):
    """uninstall.py: the earlier value back where apply() changed it and the value is still true; the record of a
    file put back is dropped (a later install may set it again), every other record kept, so a choice the user made
    survives a reinstall. Returns the lines to print."""
    record, lines = load(state), []
    for key, r in list(record.items()):
        if not isinstance(r, dict) or not r.get("changed"):
            continue
        path = r.get("path") or key
        try:
            text = read(path)
            if value(text) != "true":
                lines.append("kept    copyOnSelect as you set it in %s" % path)
                continue
            new = put_back(text, r.get("before"))
            write(path, new)
        except FileNotFoundError:
            del record[key]
            continue
        except (OSError, ValueError, UnicodeError) as e:
            lines.append("SKIPPED copyOnSelect in %s: %s" % (path, e))
            continue
        del record[key]
        lines.append("restored copyOnSelect in %s (%s)" % (path, "taken out again" if r.get("before") is None
                                                             else "back to " + r["before"]))
    if record or os.path.exists(state):
        save(record, state)
    return lines
