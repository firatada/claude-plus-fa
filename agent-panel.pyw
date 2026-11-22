# Agent Panel: every Claude Code session on this PC, in a narrow list over the right
# edge of the screen, or in a strip of tabs over the top edge (drag its header there; tray: Dock). Double-click a closed session to resume it in its own folder, in a new
# terminal; click an open one to bring its window forward, click it again to minimise it. While a session's own
# window is in front the panel stays over it, as if that window were part of the panel. Tray icon: the 5-hour usage
# figure in its level colour (the octopus until there is one); click it to show or hide; Ctrl+Alt+C does the same. The look is agent-panel.html, shown in a WebView2 window by pywebview.
# When a session starts waiting for an answer, agent-alert.html shows over every window and a knock
# sounds (tray menu: Alerts, Alert Sound). A card counts what its session runs in the background (child
# `claude` runs and subagents) while any run. The Status button under the list opens status.claude.com; its dot
# follows that page's public API. Screenshots the user takes are kept in Desktop\screenshots and move into a
# session's folder when pasted there (agent-panel-shots.py); the last three, and any the user pinned, stay on
# screen in a strip over every window (agent-shots.html), switched by a header button, dragged anywhere; a click
# shows one large at once, with Open and Delete (to the Recycle Bin). While a session's terminal is in front,
# Ctrl+V and Alt+V paste a copied picture as its file path, which Claude Code attaches at the cursor without its
# PowerShell clipboard read (agent-panel-paste.py, a process of its own, started and watched by watch_paste()).
# A link Ctrl+clicked in a session's terminal whose program Windows keeps behind it (the foreground lock) is brought
# to the front (follow_links()).
import base64, ctypes, ctypes.wintypes as wt, datetime, glob, importlib.util, io, json, math, os, queue, re, shutil, subprocess, sys, tempfile, threading, time
import urllib.request
import webview

HOME = os.path.expanduser("~")
CLAUDE = os.path.join(HOME, ".claude")
STATE_DIR = os.path.join(CLAUDE, "state", "agent-panel")
CACHE_FILE = os.path.join(STATE_DIR, "cache.json")
HIDDEN_FILE = os.path.join(STATE_DIR, "hidden.json")
CTX_DIR = os.path.join(CLAUDE, "state", "context")      # written by claude-plus statusline.py
LIMITS_FILE = os.path.join(CTX_DIR, "rate_limits.json")
HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent-panel.html")
MASCOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent-panel-mascot")
ALERT_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent-alert.html")
KNOCK = os.path.join(MASCOT, "knock.wav")
SHOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent-panel-shots.py")
SHOTS_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent-shots.html")
PASTE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent-panel-paste.py")
PASTE_WINDOWS = os.path.join(STATE_DIR, "paste-windows.json")  # the session windows, for agent-panel-paste.py
PASTE_CLASS = "claude-plus-fa paste"  # its message-only window
PASTE_ANSWER = 700     # ms the paste key's process gets to answer; two misses in a row end it (freeing its keys)
PASTE_RESTART = 10     # seconds between starts of the paste key's process
OPTS_FILE = os.path.join(STATE_DIR, "options.json")
LOCAL = os.path.join(CLAUDE, "claude-plus.local.json")  # the user's own settings, outside the repo


def local(key):
    """A key of claude-plus.local.json, or None."""
    try:
        with open(LOCAL, encoding="utf-8") as f:
            v = json.load(f)
        return v.get(key) if isinstance(v, dict) else None
    except (OSError, ValueError):
        return None


def strip_wanted():
    """The strip of the last three screenshots: shown unless claude-plus.local.json says "shot_strip": false."""
    return local("shot_strip") is not False


def strip_at():
    """Where the user dropped the strip: its top-right corner in screen pixels ("shot_strip_at": [right, top]), or
    None while it has never been dragged."""
    v = local("shot_strip_at")
    try:
        return int(v[0]), int(v[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return None


def strip_pins():
    """The screenshots pinned in the strip, by the keeper's key, the capture time ("shot_pins": [at, ...]), in the
    order they were pinned (the strip numbers them so); a pinned one stays however many newer ones come, and keeps
    its pin when the keeper moves it into a project. A value written before the numbers came is the same list,
    already in pin order (every version appended a new pin): it is read as it is, a repeat or a non-number dropped."""
    v, pins = local("shot_pins"), []
    for a in v if isinstance(v, list) else []:
        try:
            a = float(a)
        except (TypeError, ValueError):
            continue
        if a not in pins:
            pins.append(a)
    return pins


def save_local(key, value):
    """The user's choice, kept in claude-plus.local.json beside the user's other keys. A file that cannot be read
    as JSON is left alone (the choice then lasts until the panel restarts)."""
    try:
        with open(LOCAL, encoding="utf-8") as f:
            v = json.load(f)
    except OSError:
        v = {}
    except ValueError:
        return
    if not isinstance(v, dict):
        return
    v[key] = value
    tmp = LOCAL + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(v, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, LOCAL)


def full_src(path, cache={}):
    """A strip picture at full size, as a data URL of the file itself, and its size in pixels; cached by path, size
    and time (the strip's three, and a few that just left it)."""
    from PIL import Image
    try:
        st = os.stat(path)
        key = (path, st.st_size, st.st_mtime)
        if key not in cache:
            with open(path, "rb") as f:
                raw = f.read()
            with Image.open(io.BytesIO(raw)) as im:
                size, mime = im.size, Image.MIME.get(im.format, "image/png")
            if len(cache) > 6:
                cache.clear()
            cache[key] = ("data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii")), size)
        return cache[key]
    except Exception:
        return "", (0, 0)


def pic_size(path, cache={}):
    """A picture's size in pixels, from its header alone (where the preview goes), cached by path, size and time."""
    from PIL import Image
    try:
        st = os.stat(path)
        key = (path, st.st_size, st.st_mtime)
        if key not in cache:
            with Image.open(path) as im:
                if len(cache) > 200:
                    cache.clear()
                cache[key] = im.size
        return cache[key]
    except Exception:
        return 0, 0


def load_shots():
    """The screenshot keeper (agent-panel-shots.py beside this file), started on threads of this process;
    None when the file is missing or fails to load, and the panel runs without it."""
    try:
        spec = importlib.util.spec_from_file_location("agent_panel_shots", SHOTS)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.Shots().start()
    except Exception:
        return None


def load_quote():
    """quote-reply.py (the ":" reply hook) as a module, for its clip(): the strip shows what a ":" reply would
    quote, read the same way the hook reads it. None when it is missing, and the strip shows no passage."""
    script = next((f for f in QUOTE if os.path.exists(f)), None)
    try:
        spec = importlib.util.spec_from_file_location("quote_reply", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def mascot_js(only=None):
    """The mascot strips named in `only` (all without it) as data URLs: the page is handed over as a
    string, so it cannot load files."""
    strips = {}
    try:
        for name in sorted(os.listdir(MASCOT)):
            if name.endswith(".webp") and (only is None or name[:-5] in only):
                with open(os.path.join(MASCOT, name), "rb") as f:
                    strips[name[:-5]] = "data:image/webp;base64," + base64.b64encode(f.read()).decode("ascii")
    except OSError:
        pass  # no strips: the page keeps its plain logo
    return "window.MASCOT = %s;" % json.dumps(strips)
TITLE = "Field Agents"
SHOT_NAME = re.compile(r"^(?:(?P<pre>.*)_)?(?P<d>\d{4}-\d{2}-\d{2})_(?P<t>\d{4})(?:_\d+)?$")
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def shot_label(path, now=None):
    """(title, when) for a strip card's text, read from the file name alone, never the picture. The keeper
    names files <app>_<title>_YYYY-MM-DD_HHMM, <app>_YYYY-..., or YYYY-... alone; moved into a project,
    <session-title>_YYYY-...; a clash adds _<n>. The title is the last part before the date (a slug never holds
    an underscore), its hyphens shown as spaces, so <app> alone stands in when there is no title. A name outside
    that pattern (a file renamed by hand) shows its plain stem and the file's modified time. when: HH:MM today,
    "yesterday HH:MM", "16 Nov HH:MM" this year, "16 Nov 2025 HH:MM" before (the panel is English only)."""
    stem = os.path.splitext(os.path.basename(path))[0]
    m = SHOT_NAME.match(stem)
    try:
        if not m:
            raise ValueError
        at = datetime.datetime.strptime(m["d"] + m["t"], "%Y-%m-%d%H%M")
        title = (m["pre"] or "").rsplit("_", 1)[-1].replace("-", " ").strip()
    except ValueError:
        title = stem
        try:
            at = datetime.datetime.fromtimestamp(os.path.getmtime(path))
        except OSError:
            return title, ""
    today = (now or datetime.datetime.now()).date()
    when = at.strftime("%H:%M")
    if at.date() == today - datetime.timedelta(days=1):
        when = "yesterday " + when
    elif at.date() != today:
        when = "%d %s%s %s" % (at.day, MONTHS[at.month - 1], "" if at.year == today.year else " %d" % at.year, when)
    return title, when


ALERT_TITLE = "Field Agents Alert"
ALERT_W = 420          # logical pixels; the height follows the rows, as agent-alert.html lays them out
ALERT_ROWS = 4         # sessions listed in the alert; the rest are counted
SHOTS_TITLE = "Field Agents Screenshots"
SHOT_W, SHOT_H, SHOT_PAD = 150, 94, 6  # logical pixels: a strip card (text only, no picture) and the space around
                       # and between them, as agent-shots.html lays them out
SHOT_N = 3             # unpinned screenshots in the strip (the pinned ones come on top of these)
SHOT_GAP = 8           # logical pixels between the strip and the screen's edge or the panel
SHOT_MOVE = 4          # logical pixels a press must travel before it drags the strip instead of clicking
QUOTE_BELOW = 6        # logical pixels under the strip's band for what a ":" reply would quote (agent-shots.html
                       # #quote); the band itself is as tall as its text, wrapped, as the page measures it
QUOTE_CAP = 3          # the band grows to at most 1/QUOTE_CAP of the work area's height, then scrolls inside
QUOTE_LINE = 14        # logical pixels: one line of the band; its cap is whole lines, so none shows cut in half
QUOTE = (os.path.join(os.path.dirname(os.path.abspath(__file__)), "quote-reply.py"),  # run from the repo
         os.path.join(os.path.expanduser("~"), ".claude", "hooks", "quote-reply.py"))  # installed
VIEW_TITLE = "Field Agents Screenshot"
VIEW_HEAD, VIEW_PAD = 36, 8  # logical pixels: the preview's head row and the space round its picture
VIEW_MIN_W = 480       # logical pixels: the preview is never narrower (its head: four buttons and room for the name)
VIEW_MARGIN = 24       # logical pixels the preview keeps from the screen's edges and from the strip
SESSION_FILE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.jsonl$")
RESUME = "claude --resume %s --dangerously-skip-permissions"
WIDTH = 280            # logical pixels
STRIP = 46             # logical pixels: the top dock's strip, a 34 px tab centred in it (6 px above and below);
                       # its bottom edge meets a maximised Chrome's address box (toolbar top y = 40, box top y = 46
                       # at 100 %), so none of Chrome's tab strip shows under it
SHADE = 8              # logical pixels: the drop shadow under the strip (see Shade)
DROP_ROOM = 440        # logical pixels under the strip where its menus, tooltips and the set-aside list may open;
                       # the window region shows only the strip and whatever is open there
TOP_ZONE = 120         # logical pixels: a drag released this close to the top edge docks the panel there
SCAN_EVERY = 15        # seconds
LIVE_EVERY = 2         # seconds; only ~/.claude/sessions is read, so a closed session loses its dot quickly
STATUS_PAGE = "https://status.claude.com/"
STATUS_API = "https://status.claude.com/api/v2/summary.json"  # Statuspage's public API: status and unresolved incidents
STATUS_EVERY = 180     # seconds between reads of STATUS_API
LAUNCH_WAIT = 8        # seconds a session opened from the panel is not opened again while Claude Code starts
LIFT_AGAIN = (0.1, 0.2, 0.4, 0.8)  # seconds between looks after a session's window comes to the front (stay_over)
LINK_WAIT = 3          # seconds from a Ctrl+click in a session's terminal to the flash of the window Windows refused
                       # the foreground to; a later flash is left alone (link_raise)
CLICK_EVERY = 0.02     # seconds between looks at Ctrl and the primary mouse button while a session window is in front
LINKS_LOG = os.path.join(STATE_DIR, "links.log")  # every flash judged while a session window was in front
CREATE_NEW_CONSOLE = 0x10
CREATE_NO_WINDOW = 0x08000000

BG = "#f4f6fb"          # Spark page background, shown before the page has drawn; the other tokens are in the html

user32, kernel32, shell32 = ctypes.windll.user32, ctypes.windll.kernel32, ctypes.windll.shell32
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowPos.argtypes = [wt.HWND, ctypes.c_ssize_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.SetWindowRgn.argtypes = [wt.HWND, ctypes.c_void_p, wt.BOOL]
user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
gdi32 = ctypes.windll.gdi32
gdi32.CreateRectRgn.restype = gdi32.CreateRoundRectRgn.restype = ctypes.c_void_p
gdi32.CombineRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
dwmapi = ctypes.windll.dwmapi
dwmapi.DwmSetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]


# ---------- one instance only ----------
MUTEX = "Local\\claude-plus-fa-agent-panel"  # also named in the ":" reply's list, so the hook knows this panel runs
kernel32.CreateMutexW(None, False, MUTEX)
if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
    # started again (the desktop shortcut, the Run key): ask the running panel to come up, then leave.
    # This process was started by the user, so it may hand its right to take the foreground on.
    h = kernel32.OpenEventW(0x0002, False, "Local\\claude-plus-fa-agent-panel-show")  # EVENT_MODIFY_STATE
    if h:
        user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
        kernel32.SetEvent(h)
        kernel32.CloseHandle(h)
    sys.exit(0)
# a second start sets this event (auto-reset) to bring the running panel up
SHOW_EVENT = kernel32.CreateEventW(None, False, False, "Local\\claude-plus-fa-agent-panel-show")
# the installer sets this event to ask a running panel to quit and give its screen space back
QUIT_EVENT = kernel32.CreateEventW(None, True, False, "Local\\claude-plus-fa-agent-panel-quit")
kernel32.ResetEvent(QUIT_EVENT)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass


# ---------- reading sessions ----------
def wsl_roots():
    """projects folders of the WSL distributions that are already running (never starts one)."""
    try:
        out = subprocess.run(["wsl.exe", "-l", "--running", "-q"], capture_output=True,
                             timeout=5, creationflags=CREATE_NO_WINDOW).stdout
    except Exception:
        return []
    roots = []
    for distro in out.decode("utf-16-le", "ignore").replace("\0", "").split():
        base = "\\\\wsl.localhost\\%s" % distro
        homes = [os.path.join(base, "root")]
        try:
            homes += [os.path.join(base, "home", h) for h in os.listdir(os.path.join(base, "home"))]
        except OSError:
            pass
        for h in homes:
            p = os.path.join(h, ".claude", "projects")
            if os.path.isdir(p):
                roots.append((p, distro))
    return roots


def text_of(message):
    c = (message or {}).get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return ""


def parse(path):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        head = f.read(262144)
        f.seek(max(0, size - 1048576))
        tail = f.read()
    info = {"cwds": [], "title": None, "first": None, "entry": None}
    for line in head.split(b"\n"):
        if b'"cwd"' not in line and b'"type":"user"' not in line:
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        info["entry"] = info["entry"] or o.get("entrypoint")  # "sdk-cli" for a headless `claude -p` run
        if o.get("cwd") and o["cwd"] not in info["cwds"]:
            info["cwds"].append(o["cwd"])
        if not info["first"] and o.get("type") == "user" and not o.get("isMeta") and not o.get("isSidechain"):
            t = " ".join(text_of(o.get("message")).split())
            if t and not t.startswith("<") and not t.startswith("This session is being continued"):
                info["first"] = t[:160]
    for m in re.finditer(rb'"cwd":"((?:[^"\\]|\\.)*)"', tail):
        try:
            c = json.loads(b'"' + m.group(1) + b'"')
        except ValueError:
            continue
        if c not in info["cwds"]:
            info["cwds"].append(c)
    custom = ai = None
    for line in reversed(tail.split(b"\n")):
        if custom is None and b'"custom-title"' in line:
            try:
                custom = json.loads(line).get("customTitle")
            except ValueError:
                pass
        elif ai is None and b'"ai-title"' in line:
            try:
                ai = json.loads(line).get("aiTitle")
            except ValueError:
                pass
        if custom:
            break
    info["title"] = custom or ai
    return info


def slug(path):
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def home_of(folder, cwds):
    """The folder a session must be resumed from: the one its transcript is filed under.
    A session that moved lists several folders; Claude Code files it under where it started."""
    for c in cwds:
        if slug(c) == folder:
            return c
    return cwds[0] if cwds else None


def load_cache():
    try:
        return json.load(open(CACHE_FILE, encoding="utf-8"))
    except Exception:
        return {}


def save_cache(cache):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = CACHE_FILE + ".tmp"
        json.dump(cache, open(tmp, "w", encoding="utf-8"))
        os.replace(tmp, CACHE_FILE)
    except Exception:
        pass


def alive(pid):
    h = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = wt.DWORD()
    ok = kernel32.GetExitCodeProcess(h, ctypes.byref(code))
    kernel32.CloseHandle(h)
    return bool(ok) and code.value == 259  # STILL_ACTIVE


def running():
    out = {}
    folder = os.path.join(CLAUDE, "sessions")
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for n in names:
        if not n.endswith(".json"):
            continue
        try:
            o = json.load(open(os.path.join(folder, n), encoding="utf-8"))
        except Exception:
            continue
        if o.get("sessionId") and o.get("pid") and alive(o["pid"]):
            # waitingFor is set while a prompt in the session (a permission question, a dialog) waits
            # for an answer; the status then says "busy" (older versions) or "waiting" (2.1.319)
            out[o["sessionId"]] = {"status": o.get("status") or "idle", "name": o.get("name"), "pid": o["pid"],
                                   "waiting": o.get("waitingFor") or (o.get("status") == "waiting" or None),
                                   "cwd": o.get("cwd"), "started": o.get("startedAt"),
                                   # stamped at each status change, so while busy it is when the turn began
                                   "since": o.get("statusUpdatedAt")}
    return out


def read_ctx(sid):
    """Context used by a session, in percent, as its status line last recorded it; None if unknown."""
    try:
        with open(os.path.join(CTX_DIR, sid + ".json")) as f:
            v = json.load(f).get("used_percentage")
        return None if v is None else float(v)
    except (OSError, ValueError, AttributeError, TypeError):
        return None


def read_limits():
    try:
        with open(LIMITS_FILE) as f:
            v = json.load(f)
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def claude_status():
    """Anthropic's status from status.claude.com, or None when it could not be read (shown as unknown, never as fine).
    A plain GET with a generic user agent: nothing about this PC or its user goes with it."""
    try:
        req = urllib.request.Request(STATUS_API, headers={"User-Agent": "claude-plus-fa", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.load(r)
        st = d["status"]
        names = [i["name"] for i in d.get("incidents") or [] if i.get("name")]
        return {"level": str(st["indicator"]), "text": str(st["description"]), "incidents": names}
    except Exception:
        return None


def where_of(s):
    """The session's folder as a full path, written the Windows way, without a trailing separator
    (a drive root keeps its own). The cards' tooltips, the alert and the strip all show this form."""
    cwd = s["cwd"] or ""
    if s["distro"] and cwd.startswith("/"):
        cwd = "\\\\wsl.localhost\\%s%s" % (s["distro"], cwd.replace("/", "\\"))
    elif cwd.startswith("//"):
        cwd = cwd.replace("/", "\\")
    bare = cwd.rstrip("\\/")
    return (bare if bare and not bare.endswith(":") else cwd) or s["folder"]


def scan(cache):
    roots = [(os.path.join(CLAUDE, "projects"), None)] + wsl_roots()
    live = running()
    sessions, seen = [], set()
    for root, distro in roots:
        try:
            folders = os.listdir(root)
        except OSError:
            continue
        for folder in folders:
            fdir = os.path.join(root, folder)
            try:
                files = [f for f in os.listdir(fdir) if SESSION_FILE.match(f)]
            except OSError:
                continue
            for f in files:
                path = os.path.join(fdir, f)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                key = "%s|%s" % (st.st_mtime, st.st_size)
                hit = cache.get(path)
                if not hit or hit.get("key") != key or "entry" not in hit:
                    try:
                        hit = dict(parse(path), key=key)
                    except Exception:
                        continue
                    cache[path] = hit
                seen.add(path)
                sid = f[:-6]
                run = live.get(sid)
                cwd = home_of(folder, hit.get("cwds") or [])
                title = (run or {}).get("name") or hit.get("title") or hit.get("first") or os.path.basename(cwd or folder)
                sessions.append({"id": sid, "cwd": cwd, "distro": distro, "title": title, "path": path,
                                 "folder": os.path.basename((cwd or "").rstrip("\\/")) or folder,
                                 "mtime": st.st_mtime, "status": (run or {}).get("status"),
                                 "waiting": (run or {}).get("waiting"), "since": (run or {}).get("since"),
                                 "pid": (run or {}).get("pid"), "name": (run or {}).get("name"),
                                 "ctx": read_ctx(sid), "headless": hit.get("entry") == "sdk-cli"})
    for gone in [p for p in cache if p not in seen]:
        del cache[gone]
    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    return sessions


# ---------- opening a session ----------
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
    """The environment without the markers of the Claude session that may have started us.
    A resumed session that inherits CLAUDE_CODE_CHILD_SESSION stops saving its transcript;
    variables the user set in Windows itself are kept."""
    keep = registry_env_names()
    return {k: v for k, v in os.environ.items()
            if not k.upper().startswith(("CLAUDE", "ANTHROPIC")) or k.upper() in keep}


def clean(s):
    return re.sub(r'[&|<>^"%]', "", s)[:60]


def folder_exists(s):
    if s["distro"]:
        return True
    return bool(s["cwd"]) and os.path.isdir(s["cwd"])


def open_session(s, cmd=None):
    """Resume a session in a new console in its folder; returns the console's process."""
    cmd = cmd or RESUME % s["id"]
    title = "claude: " + clean(s["title"])
    if s["distro"]:  # a session started inside WSL: resume it there
        return subprocess.Popen(["wsl.exe", "-d", s["distro"], "--cd", s["cwd"], "--", "bash", "-lic", cmd],
                                creationflags=CREATE_NEW_CONSOLE, env=clean_env())
    if s["cwd"].startswith("\\\\"):  # cmd cannot stand in a UNC folder; PowerShell can
        return subprocess.Popen(["powershell.exe", "-NoLogo", "-NoExit", "-Command",
                                 "$Host.UI.RawUI.WindowTitle = '%s'; %s" % (title.replace("'", ""), cmd)],
                                cwd=s["cwd"], creationflags=CREATE_NEW_CONSOLE, env=clean_env())
    return subprocess.Popen(["cmd.exe", "/k", "title %s& %s" % (title, cmd)],
                            cwd=s["cwd"], creationflags=CREATE_NEW_CONSOLE, env=clean_env())


# ---------- bringing an open session's window forward ----------
kernel32.GetConsoleWindow.restype = wt.HWND
user32.GetAncestor.restype = wt.HWND
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.WindowFromPoint.restype = wt.HWND
user32.WindowFromPoint.argtypes = [wt.POINT]
user32.GetForegroundWindow.restype = wt.HWND


def window_title(hwnd):
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


console_lock = threading.Lock()  # a process has one console: attaching to two at once from two threads mixes them


def window_of(pid):
    """The top window showing a console process: the console itself, or the terminal
    (Windows Terminal) that owns its pseudo console."""
    with console_lock:
        kernel32.FreeConsole()
        if not kernel32.AttachConsole(int(pid)):
            return None
        hwnd = kernel32.GetConsoleWindow()
        kernel32.FreeConsole()
    if not hwnd:
        return None
    return user32.GetAncestor(hwnd, 3) or hwnd  # GA_ROOTOWNER


def press(*keys):
    for k in keys:
        user32.keybd_event(k, 0, 0, 0)
    for k in reversed(keys):
        user32.keybd_event(k, 0, 2, 0)


def shown_name(title):
    """The name a terminal title shows: Claude Code titles its tab "<status glyph> <name>"."""
    return re.sub(r"^\W+", "", title or "").strip()


user32.GetClipboardOwner.restype = wt.HWND


def tab_session(hwnd, title, sessions, window):
    """The one open session whose terminal tab shows in window hwnd, else None. A session counts when its terminal
    is that window (window(s): the window its process's console belongs to) and the window's title, which Claude
    Code sets to "<status glyph> <session name>" for the tab in view, shows its name. None when no listed session's
    tab shows there (another program, a shell tab, a title the user set), when the session has no name, and when
    two sessions in that window go by the shown name: never a guess."""
    shown = shown_name(title)
    if not hwnd or not shown:
        return None
    hits = [o for o in sessions if o.get("status") and o.get("pid") and not o.get("headless") and o.get("name")
            and shown_name(o["name"]) == shown]
    hits = [o for o in hits if window(o) == hwnd]
    return hits[0] if len(hits) == 1 else None


def copied_in(owner, title, before, sessions, window):
    """The session a new text clipboard was selected in, else None. owner: the window that put it on the clipboard
    (Windows Terminal names the very window the selection was made in); title: that window's title now (its tab in
    view); before: (window, shown name) in front at the previous poll turn. A tab switch in that window since then
    (its shown name changed) leaves the tab of the copy unknown: None."""
    if not owner or (before and before[0] == owner and before[1] != shown_name(title)):
        return None
    return tab_session(owner, title, sessions, window)


def bring_front(hwnd):
    """Put a window in front where it was: restored only if minimised (never maximised), the foreground (a lone
    Alt lets this process take it when Windows refuses), and the top of the ordinary windows. True when it is the
    foreground window afterwards."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE: back to the size and place it had before
    user32.SetForegroundWindow(hwnd)
    if user32.GetForegroundWindow() != hwnd:
        press(0x12)  # a lone Alt lets this process take the foreground
        user32.SetForegroundWindow(hwnd)
    # the foreground window is not always on top: another Windows Terminal window was seen staying over it, so
    # the user saw nothing come up. Put it at the top of the ordinary windows too (topmost, then not topmost).
    flags = 0x0001 | 0x0002 | 0x0040  # SWP_NOSIZE | SWP_NOMOVE | SWP_SHOWWINDOW
    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, flags)  # HWND_TOPMOST
    user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, flags)  # HWND_NOTOPMOST
    return user32.GetForegroundWindow() == hwnd


def link_raise(flash, shown, fg, wins, pid_of, click, now, used, wait=LINK_WAIT):
    """A window Windows kept behind a session's terminal right after a Ctrl+click in it (the link it opened): True
    when it is to be brought forward. flash: a window Windows flashed (HSHELL_FLASH: refused the foreground, or
    asking for attention); shown: it is a visible top-level window; fg: the foreground window now; wins: the
    session windows; click: (window, time) of the last Ctrl+click of the primary button on a session window, or
    None; used: clicks already answered. All of these must hold: the user is still in that session's terminal
    (fg is the clicked session window), the flash is another program's visible window (not the terminal, not
    another window of the terminal's process), at most wait seconds after the click, the click not answered
    before. So an app flashing for attention while the user is elsewhere or without a Ctrl+click, a late flash,
    and WSLg's hidden window that flashes every second keep flashing."""
    if not flash or not shown or not click or flash == fg or flash in wins or fg not in wins:
        return False
    win, at = click
    if win != fg or click in used or not -0.5 <= now - at <= wait:
        return False
    term = pid_of(fg)
    return bool(term) and pid_of(flash) != term


def links_log(*a):
    """A line in LINKS_LOG, cut back to its last 32 KB past 64 KB."""
    try:
        if os.path.exists(LINKS_LOG) and os.path.getsize(LINKS_LOG) > 65536:
            with open(LINKS_LOG, encoding="utf-8", errors="replace") as f:
                tail = f.read()[-32768:]
            with open(LINKS_LOG, "w", encoding="utf-8") as f:
                f.write(tail)
        with open(LINKS_LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), " ".join(str(x) for x in a)))
    except OSError:
        pass


def focus_session(s, others=()):
    """Raise the window of an open session where it was (restored if minimised). When its title shows another
    listed session's name (others: those names, through shown_name), the window is a terminal holding several
    session tabs: step through them until the one titled with this session's name is showing. Any other title
    (a new session's "✳ Claude Code", or anything else) sends no key: the window is only raised."""
    hwnd = s.get("pid") and window_of(s["pid"])
    if not hwnd:
        return False
    bring_front(hwnd)
    name = s.get("name")
    title = window_title(hwnd)
    if name and name not in title and shown_name(title) in others:
        for _ in range(20):
            if user32.GetForegroundWindow() != hwnd:
                break
            press(0x11, 0x09)  # Ctrl+Tab: next tab
            time.sleep(0.15)
            if name in window_title(hwnd):
                break
    return True


def own(hwnd):
    """A window of this process: the panel, its shadow, the alert."""
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value == os.getpid()


def top_window():
    """The window on top of the ordinary (not topmost) windows, leaving out this process's own: the one the user
    sees in front. Hidden, cloaked (a background UWP frame), minimised and empty windows do not count."""
    h = user32.GetTopWindow(None)
    while h:
        r = wt.RECT()
        cloaked = ctypes.c_int(0)
        dwmapi.DwmGetWindowAttribute(h, 14, ctypes.byref(cloaked), 4)  # DWMWA_CLOAKED
        if (user32.IsWindowVisible(h) and not cloaked.value and not user32.IsIconic(h) and not own(h)
                and not user32.GetWindowLongW(h, -20) & 0x8  # WS_EX_TOPMOST: the taskbar, the alert
                and user32.GetWindowRect(h, ctypes.byref(r)) and r.right > r.left and r.bottom > r.top):
            return h
        h = user32.GetWindow(h, 2)  # GW_HWNDNEXT
    return None


# Following the foreground window: a WinEvent hook (no polling) tells the panel each time another window comes to
# the front, so it knows which window was in front before a click on the panel took the foreground, and can stay
# over a session's own window.
WINEVENTPROC = ctypes.WINFUNCTYPE(None, wt.HANDLE, wt.DWORD, wt.HWND, wt.LONG, wt.LONG, wt.DWORD, wt.DWORD)
SetWinEventHook = ctypes.WINFUNCTYPE(wt.HANDLE, wt.DWORD, wt.DWORD, wt.HMODULE, WINEVENTPROC, wt.DWORD, wt.DWORD,
                                     wt.DWORD)(("SetWinEventHook", user32))
user32.UnhookWinEvent.argtypes = [wt.HANDLE]
user32.GetTopWindow.restype = user32.GetWindow.restype = wt.HWND
user32.GetTopWindow.argtypes = [wt.HWND]
user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
dwmapi.DwmGetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]


# ---------- hide, delete, close ----------
def load_hidden():
    try:
        return set(json.load(open(HIDDEN_FILE, encoding="utf-8")))
    except Exception:
        return set()


def save_hidden(hidden):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        json.dump(sorted(hidden), open(HIDDEN_FILE, "w", encoding="utf-8"))
    except Exception:
        pass


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [("hwnd", wt.HWND), ("wFunc", wt.UINT), ("pFrom", ctypes.c_void_p), ("pTo", ctypes.c_void_p),
                ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wt.BOOL),
                ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.c_void_p)]


def recycle(paths):
    """Move files and folders to the Recycle Bin, so a deletion can be undone."""
    buf = ctypes.create_unicode_buffer("\0".join(paths) + "\0\0")
    op = SHFILEOPSTRUCTW(None, 3, ctypes.cast(buf, ctypes.c_void_p), None,
                         0x40 | 0x10 | 0x04 | 0x400, False, None, None)  # FO_DELETE; ALLOWUNDO, NOCONFIRMATION, SILENT, NOERRORUI
    return shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not op.fAnyOperationsAborted


def session_files(s):
    """What makes up the session in ~/.claude: the transcript, its folder (subagents, large tool
    results) and its file-history snapshots. Nothing in the project folder."""
    out = [s["path"]]
    side = s["path"][:-len(".jsonl")]
    if os.path.isdir(side):
        out.append(side)
    history = os.path.join(CLAUDE, "file-history", s["id"])
    if os.path.isdir(history):
        out.append(history)
    return out


def close_session(s):
    """Close an open session: its window when it shows nothing else, otherwise end the process."""
    pid = s.get("pid")
    if not pid:
        return False
    hwnd = window_of(pid)
    others = [o for o in running().values() if o.get("pid") != pid]
    shared = hwnd and any(window_of(o["pid"]) == hwnd for o in others)
    if hwnd and not shared:
        user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        return True
    h = kernel32.OpenProcess(0x0001, False, int(pid))  # PROCESS_TERMINATE
    if not h:
        return False
    ok = kernel32.TerminateProcess(h, 1)
    kernel32.CloseHandle(h)
    return bool(ok)


# ---------- back to Claude after a GLM or Codex fallback ----------
# glm-fallback.py records every session it moves to GLM or Codex in RETURN_DIR/<session>.json ("via"). Once
# Claude's limit has reset and GLM or Codex sits idle, the panel asks, in the fallback's own themed window
# (glm-fallback.py --ask-return), whether to continue it with Claude. On Continue it resumes the session with
# Claude in a new window (from Codex with the record of what happened there as its first prompt) and closes
# the GLM or Codex window only once Claude holds the session. The record is a file, so a restart of the panel
# loses nothing; the answer comes back as a file beside it for the same reason. Since 2026-11-18 the record lives
# until a Claude process holds the session again (or the transcript is gone): Not Now and a closed GLM or Codex
# window keep it, the card says "on GLM" / "on Codex" and offers Back to Claude, and every way back resumes with
# --model and, from Codex, carries the record in exactly once (here, or glm-fallback.py's --carry hook).
RETURN_DIR = os.path.join(CLAUDE, "state", "glm-return")
FALLBACK = (os.path.join(os.path.dirname(os.path.abspath(__file__)), "glm-fallback.py"),  # run from the repo
            os.path.join(CLAUDE, "hooks", "glm-fallback.py"))                             # installed
ASK_AGAIN = 30 * 60    # seconds after Not Now before the one further question
IDLE_FOR = 4           # seconds GLM or Codex must stay idle, so a pause between two steps of a turn is no end
START_WAIT = 90        # seconds for the new Claude window to hold the session; after that GLM/Codex is left as it is
CODEX_EXES = ("codex.exe", "node.exe")  # codex.cmd -> node codex.js -> codex.exe under the Codex console
VIA = {"glm": "GLM", "codex": "Codex"}
CLAIM_KEEP = 7 * 24 * 3600  # seconds a claim on a carried Codex record is kept (glm-fallback.py claim())


def fallback():
    """glm-fallback.py loaded as a module (its helpers for the Codex way back); None if it is missing."""
    script = next((f for f in FALLBACK if os.path.exists(f)), None)
    if not script:
        return None
    spec = importlib.util.spec_from_file_location("glm_fallback", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def resume_claude(rec, title, model, prompt=None):
    """claude --resume on the session in a new console in its folder, on `model` (Claude Code would resume on
    the model the last turn used: GLM's, which Anthropic refuses). A first prompt (the Codex record, many lines)
    cannot pass through cmd, so claude.exe is started straight with it as one argument; a claude that is a
    .cmd gets a one-line prompt pointing at the record written to a file instead."""
    skip = " --dangerously-skip-permissions" if rec.get("skip") else ""
    cmd = "claude --resume %s --model %s%s" % (rec["session"], model, skip)
    s = {"id": rec["session"], "cwd": rec["cwd"], "distro": None, "title": title}
    if prompt is None:
        return open_session(s, cmd)
    exe = shutil.which("claude")
    if exe and exe.lower().endswith(".exe"):
        argv = [exe, "--resume", rec["session"], "--model", model] + (["--dangerously-skip-permissions"] if skip else [])
        return subprocess.Popen(argv + [prompt], cwd=rec["cwd"], creationflags=CREATE_NEW_CONSOLE, env=clean_env())
    path = os.path.join(rec["cwd"], "codex-record_%s.md" % time.strftime("%Y-%m-%d_%H%M"))
    with open(path, "w", encoding="utf-8") as f:
        f.write(prompt)
    return open_session(s, cmd + ' "While Claude\'s limit was out, this session continued in Codex. '
                                 'What happened there is in %s. Read it, then continue from where Codex stopped."' % path)


def read_record(path):
    """A way-back record, or None when it is gone, half written or foreign."""
    try:
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
        rec.setdefault("console", rec.get("glm_cmd"))  # a record written before Codex joined (2026-11-17)
        rec["session"], rec["console"], rec["resets_at"]
        return rec
    except Exception:
        return None


def console_alive(rec):
    """The GLM or Codex console the record names still runs (not a later process that got its number)."""
    return bool(rec.get("console")) and alive(rec["console"]) and (
        rec.get("console_born") is None or born(rec["console"]) == rec["console_born"])


def resume_cmd(s):
    """The resume a card starts: plain, or with --model when the transcript's last assistant turn was not a Claude
    model (left on GLM by any route), since Claude Code resumes on that model and Anthropic answers 404."""
    fb = fallback()
    model = fb.resume_model(s.get("path")) if fb else None
    return RESUME % s["id"] + (" --model %s" % model if model else "")


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                ("szExeFile", ctypes.c_wchar * 260)]


kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE


def procs():
    """{pid: (parent pid, exe name)} for every running process, from one Toolhelp snapshot."""
    snap = kernel32.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
    out = {}
    if not snap or snap == wt.HANDLE(-1).value:
        return out
    e = PROCESSENTRY32W(dwSize=ctypes.sizeof(PROCESSENTRY32W))
    more = kernel32.Process32FirstW(snap, ctypes.byref(e))
    while more:
        out[e.th32ProcessID] = (e.th32ParentProcessID, e.szExeFile)
        more = kernel32.Process32NextW(snap, ctypes.byref(e))
    kernel32.CloseHandle(snap)
    return out


def born(pid):
    """A process's creation time (FILETIME as an int), None if it cannot be read; with alive() it tells a
    process from a later one that got the same number."""
    h = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    t = [wt.FILETIME() for _ in range(4)]
    got = kernel32.GetProcessTimes(h, *[ctypes.byref(x) for x in t])
    kernel32.CloseHandle(h)
    return (t[0].dwHighDateTime << 32 | t[0].dwLowDateTime) if got else None


def runs_under(pid, root, par):
    """pid is root or started under it (cmd -> glm.cmd -> claude.cmd -> node is a few levels down)."""
    for _ in range(12):
        if not pid:
            return False
        if pid == root:
            return True
        pid = par.get(pid)
    return False


def end_tree(root, par):
    """End a console's process and everything started under it, the deepest first, the console's own process
    last with exit code 0: Windows Terminal then closes its tab (it keeps a tab whose process failed open) and
    a plain console window closes once nothing is attached to it. Never WM_CLOSE: under Windows Terminal the
    console's window is the terminal's own, and closing it would close every tab in it."""
    kids = {}
    for p, pp in par.items():
        kids.setdefault(pp, []).append(p)
    order, todo = [], [root]
    while todo:
        p = todo.pop(0)
        start = born(p)
        for k in kids.get(p, []):
            # a child is never older than its parent; an older one only inherited a reused number
            if k != root and k not in order and start and (born(k) or 0) >= start:
                order.append(k)
                todo.append(k)
    for p, code in [(p, 1) for p in reversed(order)] + [(root, 0)]:
        h = kernel32.OpenProcess(0x0001, False, int(p))  # PROCESS_TERMINATE
        if h:
            kernel32.TerminateProcess(h, code)
            kernel32.CloseHandle(h)


def console_root(pid, par):
    """The first process of the console pid is attached to (the shell of its terminal tab or window, or pid itself
    when it was started straight in it): the processes attached to that console (GetConsoleProcessList), walked up
    from pid. pid itself when the console cannot be read."""
    with console_lock:
        kernel32.FreeConsole()
        if not kernel32.AttachConsole(int(pid)):
            return pid
        ids = (wt.DWORD * 256)()
        n = kernel32.GetConsoleProcessList(ids, 256)
        kernel32.FreeConsole()
    members = {ids[i] for i in range(min(n, 256))} - {os.getpid()}
    root = pid
    for _ in range(16):
        up = par.get(root)
        if not up or up not in members or (born(up) or 0) > (born(root) or 0):
            break
        root = up
    return root


def session_procs(sid):
    """The live processes holding a session, from ~/.claude/sessions/<pid>.json: [{"pid", "status"}]."""
    folder, out = os.path.join(CLAUDE, "sessions"), []
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for n in names:
        if n.endswith(".json"):
            try:
                o = json.load(open(os.path.join(folder, n), encoding="utf-8"))
            except Exception:
                continue
            if o.get("sessionId") == sid and o.get("pid") and alive(o["pid"]):
                out.append({"pid": int(o["pid"]), "status": o.get("status") or "idle"})
    return out


# ---------- background work of an open session: child runs and subagents ----------
CLI_EXE = os.path.normcase(os.path.join(HOME, ".local", "bin", "claude.exe"))
CLI_VERSIONS = os.path.normcase(os.path.join(HOME, ".local", "share", "claude", "versions")) + os.sep
VERSION_NAME = re.compile(r"^\d+\.\d+\.\d+$")  # the files in versions/ are named by version and have no .exe
FT_EPOCH = 116444736000000000                   # 1970-01-01 as a FILETIME
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]


def image_path(pid):
    h = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    buf, n = ctypes.create_unicode_buffer(1024), wt.DWORD(1024)
    got = kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
    kernel32.CloseHandle(h)
    return buf.value if got else None


def claude_code(pid, name):
    """The process is Claude Code's CLI, told by its full path: the desktop app's claude.exe (under WindowsApps)
    and anything else of that name are not. The name only skips the other processes cheaply."""
    if name.lower() != "claude.exe" and not VERSION_NAME.match(name):
        return False
    p = os.path.normcase(image_path(pid) or "")
    # claude.exe.old.<ms>.<pid>: a running copy that `claude update` renamed aside
    return p == CLI_EXE or p.startswith(CLI_EXE + ".old.") or p.startswith(CLI_VERSIONS)


def child_runs(live, table):
    """{session id: [run]}: every Claude Code process whose chain of parents reaches an open session's own
    process (the nearest one counts), e.g. a `claude -p` that session started through a shell. A process
    started straight under a Claude Code process without a session of its own is the CLI's helper
    (`claude mcp serve`), not a run. Each parent must be older than its child: a number Windows gave
    again to a later process breaks the chain rather than linking a stranger."""
    by_pid = {int(r["pid"]): sid for sid, r in live.items()}
    cli = {p for p, (_, name) in table.items() if claude_code(p, name)}
    out = {}
    for pid in cli:
        mine = by_pid.get(pid)
        if not mine and table[pid][0] in cli:
            continue
        cur, t = pid, born(pid)
        t0 = t  # the run's own start: the shells between it and the session may be older
        for _ in range(16):
            up = table.get(cur, (0, ""))[0]
            bu = born(up) if up else None
            if not t or not bu or bu > t:
                break
            sid = by_pid.get(up)
            if sid and sid != mine:  # the same session (GLM taking it over) is no child run
                r = live.get(mine) or {}
                out.setdefault(sid, []).append({"kind": "run", "what": os.path.basename((r.get("cwd") or "").rstrip("\\/")),
                                                "since": (t0 - FT_EPOCH) / 1e7, "pid": pid, "sid": mine,
                                                "cwd": r.get("cwd")})
                break
            cur, t = up, bu
    return out


def subagents(s, started, cache):
    """The in-process agents (Agent/Task tool, workflow agents) of an open session that are running now.
    Claude Code writes subagents[/<subfolder>]/agent-<id>.meta.json beside the session's transcript when an
    agent starts and rewrites it with "finished": true when it ends ("stoppedByUser": true when stopped).
    Only files written since the session's process started count, so an agent cut off by a closed or
    crashed session, which never got its "finished", is not shown as running. Each meta is read again
    only when its time changes; transcripts are never opened."""
    out, todo = [], [os.path.join(os.path.dirname(s["path"]), s["id"], "subagents")]
    while todo:
        try:
            entries = list(os.scandir(todo.pop()))
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_dir():
                    todo.append(e.path)
                    continue
                if not e.name.endswith(".meta.json"):
                    continue
                st = e.stat()
                if st.st_mtime < started:
                    continue
                hit = cache.get(e.path)
                if not hit or hit[0] != st.st_mtime:
                    with open(e.path, encoding="utf-8") as f:
                        m = json.load(f)
                    run = not m.get("finished") and not m.get("stoppedByUser")
                    try:  # its transcript is created when it starts; the meta is rewritten at the end
                        since = os.stat(e.path[:-10] + ".jsonl").st_birthtime
                    except (OSError, AttributeError):
                        since = getattr(st, "st_birthtime", st.st_ctime)
                    hit = cache[e.path] = (st.st_mtime, run and {
                        "kind": "subagent", "what": m.get("description") or m.get("agentType") or "", "since": since,
                        "file": e.path[:-10] + ".jsonl"})
                if hit[1]:
                    out.append(hit[1])
            except (OSError, ValueError, AttributeError):
                continue
    return out


# ---------- the card's popover: what a session is working on ----------
# Read only for the card under the pointer (Api.peek), never on the live pass. Each reader opens a few hundred KB
# at the end (or the start) of one transcript and keeps its answer by the file's size and time, so a pointer
# resting on a card costs one stat per file every refresh. Nothing read here is written or logged anywhere.
PEEK_TAIL = 262144       # bytes read from a transcript's end for its last prompt; PEEK_MORE when none is in it
PEEK_MORE = 4 * 1048576
PEEK_HEAD = 262144       # bytes read from a subagent's or a run's transcript start for its prompt
PEEK_CHARS = 1200        # a prompt is cut here (the popover shows its first lines)
BG_TAIL = 4 * 1048576    # bytes read from a transcript's end for its background commands, as glm-fallback.py's
LATE = 20                # seconds after a session's process started: a child born later is a tool's
BG_START = re.compile(rb"running in background with ID: (\w+)")
BG_END = re.compile(rb"<task-id>(\w+)</task-id>")
KIND = {"subagent": "Subagent", "run": "claude -p", "command": "Command"}


def flat(t):
    """A prompt as one line of words (whitespace collapsed) cut at PEEK_CHARS; None when empty."""
    t = " ".join((t or "").replace("\ufeff", "").split())  # a prompt pasted from a file can start with a BOM
    return (t[:PEEK_CHARS - 1] + "…" if len(t) > PEEK_CHARS else t) or None


def typed_prompt(o):
    """The text of a transcript line when it is a prompt the user typed, else None: not a tool result, a meta
    line, a subagent's line, a slash command, a task notification or another tag Claude Code wraps its own
    messages in, nor the summary a compaction starts with."""
    if o.get("type") != "user" or o.get("isMeta") or o.get("isSidechain") or o.get("toolUseResult") is not None:
        return None
    c = (o.get("message") or {}).get("content")
    if isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
        return None
    t = text_of(o.get("message")).strip()
    if not t or t.startswith("<") or t.startswith("This session is being continued") or t.startswith("[Request interrupted"):
        return None
    return flat(t)


def last_prompt(path, cache):
    """The latest prompt typed in a session, from the end of its transcript (PEEK_TAIL, then PEEK_MORE when that
    holds none). Kept in cache by the file's size and time."""
    st = os.stat(path)
    key = (st.st_size, st.st_mtime)
    hit = cache.get(path)
    if hit and hit[0] == key:
        return hit[1]
    found = None
    for n in (PEEK_TAIL, PEEK_MORE):
        with open(path, "rb") as f:
            f.seek(max(0, st.st_size - n))
            tail = f.read(n)
        for line in reversed(tail.split(b"\n")):
            if b'"type":"user"' not in line:
                continue
            try:
                found = typed_prompt(json.loads(line))
            except ValueError:
                continue
            if found:
                break
        if found or n >= st.st_size:
            break
    cache[path] = (key, found)
    return found


def first_prompt(path, cache):
    """The prompt a subagent or a headless run was given: the first user line of its own transcript, which
    never changes once written (kept in cache for good; a file without it yet is read again next time)."""
    if path in cache:
        return cache[path]
    with open(path, "rb") as f:
        head = f.read(PEEK_HEAD)
    for line in head.split(b"\n"):
        if b'"type":"user"' not in line:
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get("type") == "user" and not o.get("isMeta") and o.get("toolUseResult") is None:
            t = flat(text_of(o.get("message")))
            if t:
                cache[path] = t
                return t
    return None


ntdll = ctypes.WinDLL("ntdll")
ntdll.NtQueryInformationProcess.argtypes = [wt.HANDLE, wt.ULONG, ctypes.c_void_p, wt.ULONG, ctypes.POINTER(wt.ULONG)]
shell32.CommandLineToArgvW.restype = ctypes.POINTER(wt.LPWSTR)
shell32.CommandLineToArgvW.argtypes = [wt.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
kernel32.LocalFree.argtypes = [ctypes.c_void_p]


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [("Length", wt.USHORT), ("MaximumLength", wt.USHORT), ("Buffer", ctypes.c_void_p)]


def command_line(pid):
    """A process's command line split into its arguments (ProcessCommandLineInformation, which needs only
    PROCESS_QUERY_LIMITED_INFORMATION); [] when it cannot be read."""
    h = kernel32.OpenProcess(0x1000, False, int(pid))
    if not h:
        return []
    try:
        buf, n = ctypes.create_string_buffer(65536), wt.ULONG(0)
        if ntdll.NtQueryInformationProcess(h, 60, buf, len(buf), ctypes.byref(n)) != 0:
            return []
        us = UNICODE_STRING.from_buffer(buf)
        line = ctypes.wstring_at(us.Buffer, us.Length // 2) if us.Buffer else ""
    finally:
        kernel32.CloseHandle(h)
    argc = ctypes.c_int(0)
    argv = shell32.CommandLineToArgvW(line, ctypes.byref(argc)) if line else None
    if not argv:
        return []
    try:
        return [argv[i] for i in range(argc.value)]
    finally:
        kernel32.LocalFree(argv)


def print_prompt(args):
    """The prompt on a `claude -p` command line: the argument after -p/--print when it is not an option, else the
    longest argument that is not one (option values are short). None when the prompt came on stdin."""
    rest = args[1:]
    for i, a in enumerate(rest):
        if a in ("-p", "--print") and i + 1 < len(rest) and not rest[i + 1].startswith("-"):
            return flat(rest[i + 1])
    words = [a for a in rest if not a.startswith("-")]
    return flat(max(words, key=len)) if words else None


def bg_commands(path, started, cache):
    """The background commands a session started since its process did (started, unix seconds) and has not seen
    end: a tool call with run_in_background whose result says "running in background with ID: X", without a later
    <task-id>X</task-id> notification or a TaskStop of X. Subagents started in the background are left out (their
    meta files count them). [{"kind": "command", "what": description, "text": command, "since": unix}], read
    from the transcript's last BG_TAIL bytes and kept in cache by the file's size and time."""
    st = os.stat(path)
    key = (st.st_size, st.st_mtime, started)
    hit = cache.get(path)
    if hit and hit[0] == key:
        return hit[1]
    with open(path, "rb") as f:
        f.seek(max(0, st.st_size - BG_TAIL))
        tail = f.read()
    calls, runs = {}, {}
    for line in tail.split(b"\n"):
        ends = BG_END.findall(line)
        if b"run_in_background" in line or b'"TaskStop"' in line or b'"KillShell"' in line or BG_START.search(line):
            try:
                o = json.loads(line)
            except ValueError:
                o = {}
            c = (o.get("message") or {}).get("content")
            for b in c if isinstance(c, list) else []:
                if not isinstance(b, dict):
                    continue
                inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                if b.get("type") == "tool_use" and b.get("name") in ("TaskStop", "KillShell"):
                    ends += [str(inp.get(k)).encode() for k in ("task_id", "shell_id") if inp.get(k)]
                elif b.get("type") == "tool_use" and inp.get("run_in_background") and b.get("name") not in ("Agent", "Task"):
                    calls[b.get("id")] = {"what": inp.get("description") or "", "text": flat(inp.get("command")
                                                                                           or inp.get("prompt") or "")}
                elif b.get("type") == "tool_result" and b.get("tool_use_id") in calls:
                    m = BG_START.search(line)
                    try:
                        t = datetime.datetime.fromisoformat(o["timestamp"].replace("Z", "+00:00")).timestamp()
                    except (KeyError, ValueError, AttributeError):
                        t = 0
                    if m and t >= started:
                        runs[m.group(1)] = dict(calls[b["tool_use_id"]], kind="command", since=t)
        for x in ends:  # ids as bytes, as the patterns find them
            runs.pop(x, None)
    out = sorted(runs.values(), key=lambda w: w["since"])
    cache[path] = (key, out)
    return out


def late_child(pid, started, table):
    """The session's process has a child born more than LATE seconds after it started (a tool's, not an MCP
    server's): without one, no background command can still be running, whatever the transcript says."""
    return any(pp == pid and (born(p) or 0) > (started + LATE) * 1e7 + FT_EPOCH for p, (pp, _) in table.items())


def lasted(ts):
    d = max(0, time.time() - ts)
    if d < 60:
        return "under 1 min"
    if d < 3600:
        return "%d min" % (d // 60)
    return "%d h %d min" % (d // 3600, d % 3600 // 60)


def save_record(path, rec):
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(rec, f)
    os.replace(path + ".tmp", path)


# ---------- repairing a failed Claude Code update ----------
# On Windows ~/.local/bin/claude.exe is a copy of the newest file in ~/.local/share/claude/versions, not a link.
# `claude update` replaces it while sessions run (it renames the running copy aside first; measured 2026-11-13),
# yet a background auto-update once failed with update_apply_exe_locked and left a claude.exe that would not
# start. When ~/.claude/.last-update-result.json records a failure the panel has not handled, it runs `update`
# from an intact versions file, moves claude.exe aside and reinstalls if it still does not start, and records the
# outcome. No session is closed: none has to be. A failure it cannot repair stays in the panel's bottom line.
UPDATE_RESULT = os.path.join(CLAUDE, ".last-update-result.json")   # Claude Code's own record; doctor reads it
UPDATE_SEEN = os.path.join(STATE_DIR, "update.json")               # the failure last handled, and how it went
CLI = os.path.join(HOME, ".local", "bin", "claude.exe")
VERSIONS = os.path.join(HOME, ".local", "share", "claude", "versions")
UPDATE_WAIT = 600      # seconds for one `update` or `install`; both download about 350 MB
REINSTALL = "irm https://claude.ai/install.ps1 | iex"


def vkey(v):
    return tuple(int(x) for x in v.split("."))


def run_cli(exe, *args, wait=60):
    """Run a Claude Code executable without a window. The files in versions/ have no .exe, so the path goes in
    as the application name, which Windows takes as it is (on the command line alone it would append .exe)."""
    try:
        # stdin from NUL: the panel's own standard input is an invalid handle, which fails the start (OSError 9)
        return subprocess.run([exe] + list(args), executable=exe, stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=wait, encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW,
                              env=clean_env())
    except (OSError, subprocess.SubprocessError):
        return None


def version_of(exe):
    """The version an executable reports, or None when it does not start or prints anything else (a claude.exe
    without its payload prints the Bun runtime's own help)."""
    r = run_cli(exe, "--version")
    m = r and r.returncode == 0 and re.match(r"\s*(\d+\.\d+\.\d+) \(Claude Code\)", r.stdout or "")
    return m.group(1) if m else None


def newest_intact():
    """(path, version) of the newest file in versions/ that starts and reports its own version."""
    try:
        names = [n for n in os.listdir(VERSIONS) if re.fullmatch(r"\d+\.\d+\.\d+", n)]
    except OSError:
        return None, None
    for n in sorted(names, key=vkey, reverse=True):
        p = os.path.join(VERSIONS, n)
        if version_of(p) == n:
            return p, n
    return None, None


def repair_update(failed):
    """After the failed attempt `failed` (the record, as read): (ok, message for the panel's bottom line)."""
    exe, _ = newest_intact()
    if not exe:
        return False, "Claude Code's update failed and no installed version starts. Reinstall it in PowerShell: " + REINSTALL
    run_cli(exe, "update", wait=UPDATE_WAIT)
    if not version_of(CLI):
        exe, v = newest_intact()  # the update may have added a newer one
        if exe:
            # `install` rewrites claude.exe only when it is missing; aside under Claude Code's own backup name
            # (claude.exe.old.<ms>), which its updater clears later. A rename works even on a file still open.
            try:
                os.replace(CLI, "%s.old.%d" % (CLI, time.time() * 1000))
            except OSError:
                pass
            run_cli(exe, "install", v, wait=UPDATE_WAIT)
    now, want = version_of(CLI), failed.get("version_to")
    if not now:
        return False, "Claude Code's update failed and claude.exe does not start. Reinstall it in PowerShell: " + REINSTALL
    if want and re.fullmatch(r"\d+\.\d+\.\d+", want) and vkey(now) < vkey(want):
        return False, "Claude Code could not update to %s; %s still works and tries again by itself." % (want, now)
    # the update is in: a success record in Claude Code's own format, so `claude doctor` stops reporting the failure
    save_record(UPDATE_RESULT, {"timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z"), "path": "native", "outcome": "success", "status": "success",
        "version_from": failed.get("version_from"), "version_to": now, "error_code": None})
    return True, "Repaired Claude Code's failed update: %s runs." % now


# ---------- placement: over the right or the top edge, without taking any screen space ----------
class APPBARDATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uCallbackMessage", wt.UINT),
                ("uEdge", wt.UINT), ("rc", wt.RECT), ("lParam", wt.LPARAM)]


class Edge:
    """The panel sits over the right edge of the work area like any other window: desktop
    icons and maximised windows keep their place, and an active window simply covers it.
    It used to reserve the edge as an AppBar; registering and removing one once at start
    makes Windows drop any edge an older, killed copy of the panel left reserved."""

    def __init__(self, hwnd):
        abd = APPBARDATA(cbSize=ctypes.sizeof(APPBARDATA), hWnd=hwnd, uEdge=2)
        shell32.SHAppBarMessage(0, ctypes.byref(abd))  # ABM_NEW
        shell32.SHAppBarMessage(1, ctypes.byref(abd))  # ABM_REMOVE

    @staticmethod
    def work():
        work = wt.RECT()
        user32.SystemParametersInfoW(0x30, 0, ctypes.byref(work), 0)  # SPI_GETWORKAREA
        return work

    def place(self, dock, scale):
        """The window's rectangle for a dock: a column down the right edge, or the full width of the top
        edge, as tall as the strip plus the room its menus open in (the window region hides that room)."""
        work = self.work()
        if dock == "top":
            return work.left, work.top, work.right - work.left, int(round((STRIP + DROP_ROOM) * scale))
        width = int(round(WIDTH * scale))
        return work.right - width, work.top, width, work.bottom - work.top

    def release(self):
        pass


# ---------- the top strip's drop shadow ----------
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
# bound as prototypes of their own, so the argtypes pystray sets on the shared user32 functions never clash
DefWindowProc = WNDPROC(("DefWindowProcW", user32))
CreateWindowEx = ctypes.WINFUNCTYPE(wt.HWND, wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID)(
    ("CreateWindowExW", user32))
CreateDIBSection = ctypes.WINFUNCTYPE(ctypes.c_void_p, wt.HDC, ctypes.c_void_p, wt.UINT,
                                      ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD)(("CreateDIBSection", gdi32))
UpdateLayeredWindow = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.HDC, ctypes.c_void_p, ctypes.c_void_p, wt.HDC,
                                         ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, wt.DWORD)(
    ("UpdateLayeredWindow", user32))
SelectObject = ctypes.WINFUNCTYPE(ctypes.c_void_p, wt.HDC, ctypes.c_void_p)(("SelectObject", gdi32))
CreateCompatibleDC = ctypes.WINFUNCTYPE(wt.HDC, wt.HDC)(("CreateCompatibleDC", gdi32))
DeleteDC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HDC)(("DeleteDC", gdi32))
GetDC = ctypes.WINFUNCTYPE(wt.HDC, wt.HWND)(("GetDC", user32))
ReleaseDC = ctypes.WINFUNCTYPE(ctypes.c_int, wt.HWND, wt.HDC)(("ReleaseDC", user32))
SHADE_RGB = (0, 0, 0)     # black, not Spark's navy: over a dark window the navy turned the shadow bluish, lighter
                          # than what lay under it; a shadow may only darken
SHADE_PEAK = 0.30         # its opacity right at the strip's edge; it fades to nothing SHADE px below


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte), ("SourceConstantAlpha", ctypes.c_ubyte),
                ("AlphaFormat", ctypes.c_ubyte)]


class Shade:
    """The drop shadow under the top strip. WebView2 draws no see-through pixels, so the shadow is a window of
    its own: layered with per-pixel alpha (UpdateLayeredWindow), so whatever is below shows through it;
    click-through (WS_EX_TRANSPARENT), never activated (WS_EX_NOACTIVATE), out of the taskbar and Alt+Tab
    (WS_EX_TOOLWINDOW). It is owned by the panel, so it rises and sinks with it; an owned window sits above its
    owner, so where the panel has something open under the strip (a menu, the hidden list) the shadow leaves
    those pixels clear, and it looks as if it lay under the panel. Its own thread creates it and pumps its
    messages."""

    CLASS = "ClaudePlusFAAgentPanelShadow"

    def __init__(self, owner):
        self.owner, self.hwnd, self.lock = owner, None, threading.Lock()
        self.shown, self.drawn = False, None
        up = threading.Event()
        threading.Thread(target=self.loop, args=(up,), daemon=True).start()
        up.wait(5)

    def loop(self, up):
        try:
            self.proc = WNDPROC(lambda h, m, w, l: DefWindowProc(h, m, w, l))  # kept alive with the window
            inst = kernel32.GetModuleHandleW(None)
            wc = WNDCLASSW(lpfnWndProc=self.proc, hInstance=inst, lpszClassName=self.CLASS)
            user32.RegisterClassW(ctypes.byref(wc))
            # WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE; WS_POPUP
            self.hwnd = CreateWindowEx(0x80000 | 0x20 | 0x80 | 0x8000000, self.CLASS, "Field Agents Shadow",
                                       0x80000000, 0, 0, 1, 1, self.owner, None, inst, None)
        finally:
            up.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def set(self, place, holes=(), scale=1.0):
        """place: (x, y, width) on screen of the strip's bottom edge, or None to hide the shadow. holes: what the
        panel has open under the strip, as the page reports it ([x, y, w, h, radius], css px, window-relative)."""
        with self.lock:
            if not self.hwnd:
                return
            if place is None:
                if self.shown:
                    user32.ShowWindow(self.hwnd, 0)  # SW_HIDE
                    self.shown = False
                return
            key = (tuple(place), tuple(tuple(h) for h in holes), scale)
            if key != self.drawn:
                self.draw(place, holes, scale)
                self.drawn = key
            if not self.shown:
                user32.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE
                self.shown = True

    def draw(self, place, holes, s):
        x, y, w = place
        h = max(1, int(round(SHADE * s)))
        r, g, b = SHADE_RGB
        rows = []
        for j in range(h):
            # darkest at the strip's edge, easing out to nothing; premultiplied by alpha, as ULW_ALPHA needs
            a = SHADE_PEAK * (1 - (j + 0.5) / h) ** 2
            row = bytearray(bytes((round(b * a), round(g * a), round(r * a), round(255 * a))) * w)
            mid = STRIP * s + j + 0.5  # this row's centre, in the panel window's pixels
            for hx, hy, hw, hh, rad in holes:
                x0, y0, x1, y1, rr = hx * s, hy * s, (hx + hw) * s, (hy + hh) * s, rad * s
                if not y0 <= mid < y1:
                    continue
                dy = max(y0 + rr - mid, mid - (y1 - rr), 0)  # how far into a rounded corner this row is
                inset = rr - max(rr * rr - dy * dy, 0) ** 0.5 if dy else 0
                c0, c1 = max(int(x0 + inset), 0), min(int(round(x1 - inset)), w)
                if c1 > c0:
                    row[c0 * 4:c1 * 4] = bytes((c1 - c0) * 4)
            rows.append(bytes(row))
        bits = b"".join(rows)
        bmi = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=-h, biPlanes=1,
                               biBitCount=32)  # top-down, BI_RGB
        screen = GetDC(None)
        mem = CreateCompatibleDC(screen)
        ptr = ctypes.c_void_p()
        bmp = CreateDIBSection(mem, ctypes.byref(bmi), 0, ctypes.byref(ptr), None, 0)
        ctypes.memmove(ptr, bits, len(bits))
        old = SelectObject(mem, bmp)
        blend = BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER, no extra fade, AC_SRC_ALPHA
        UpdateLayeredWindow(self.hwnd, screen, ctypes.byref(wt.POINT(x, y)), ctypes.byref(wt.SIZE(w, h)), mem,
                            ctypes.byref(wt.POINT(0, 0)), 0, ctypes.byref(blend), 2)  # ULW_ALPHA
        SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        DeleteDC(mem)
        ReleaseDC(None, screen)

    def close(self):
        if self.hwnd:
            user32.PostMessageW(self.hwnd, 0x0010, 0, 0)  # WM_CLOSE: destroyed on its own thread


# ---------- the paste key: agent-panel-paste.py, a process of its own ----------
user32.FindWindowExW.restype = wt.HWND
user32.FindWindowExW.argtypes = [wt.HWND, wt.HWND, wt.LPCWSTR, wt.LPCWSTR]
user32.SendMessageTimeoutW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM, wt.UINT, wt.UINT,
                                       ctypes.POINTER(ctypes.c_size_t)]
PASTE_MARK = user32.RegisterClipboardFormatW("claude-plus-fa paste")  # on every clipboard the paste key sets


def paste_wanted():
    """Ctrl+V and Alt+V paste a picture as its path in a session's terminal; off with "paste_keys": false."""
    return local("paste_keys") is not False


def start_paste():
    try:
        return subprocess.Popen([sys.executable, PASTE, str(os.getpid())], creationflags=CREATE_NO_WINDOW,
                                env=clean_env())
    except OSError:
        return None


def paste_window():
    return user32.FindWindowExW(wt.HWND(-3), None, PASTE_CLASS, None)  # HWND_MESSAGE: message-only windows


def answers(hwnd, ms=PASTE_ANSWER):
    """The window's thread handles a message within ms (a stuck one holds Ctrl+V and Alt+V)."""
    res = ctypes.c_size_t()
    return bool(user32.SendMessageTimeoutW(hwnd, 0, 0, 0, 0x0002, ms, ctypes.byref(res)))  # WM_NULL, ABORTIFHUNG


def save_paste_windows(windows):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = PASTE_WINDOWS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"windows": windows}, f)
    os.replace(tmp, PASTE_WINDOWS)


# ---------- hotkey and tray, each on its own thread ----------
def hotkey_loop(events):
    if not user32.RegisterHotKey(None, 1, 0x0002 | 0x0001 | 0x4000, ord("C")):  # Ctrl+Alt, no repeat
        return
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == 0x0312:  # WM_HOTKEY
            events.put("toggle")


def logo_image(size):
    """The octopus cut close (mascot/tray.png: the whole logo is a speck at tray size); without
    it the Spark logo tile: a blue to cyan gradient at 135 degrees, white list mark."""
    from PIL import Image, ImageDraw
    try:
        return Image.open(os.path.join(MASCOT, "tray.png")).convert("RGBA").resize((size, size), Image.LANCZOS)
    except OSError:
        pass
    big = 256
    a, b = (0x1b, 0x4d, 0xf5), (0x33, 0xc6, 0xf4)
    grad = Image.new("RGB", (big, big))
    px = grad.load()
    for y in range(big):
        for x in range(big):
            t = (x + y) / (2.0 * (big - 1))
            px[x, y] = tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, big - 1, big - 1), radius=int(big * 0.28), fill=255)
    tile = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    tile.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(tile)
    for i, y in enumerate((78, 128, 178)):
        d.ellipse((62, y - 13, 88, y + 13), fill="white")
        d.rounded_rectangle((104, y - 11, 196 - i * 18, y + 11), radius=11, fill="white")
    return tile.resize((size, size), Image.LANCZOS)


# The 5h figure in the tray: the thresholds and colours of the 5h bar (agent-panel.html bar(): --green under
# 50, --gold under 80, --red from 80; statusline.py bar() uses the same steps)
LEVELS = ((50, (0x16, 0xa3, 0x4a)), (80, (0xf5, 0xa6, 0x23)), (101, (0xe1, 0x1d, 0x48)))
INK = ((0x0a, 0x1a, 0x40), (0xff, 0xff, 0xff))  # Spark --text and white: each tile takes the one of higher contrast


def tray_size():
    """The small icon size Windows draws tray icons at (16 px at 100 %), at the system DPI the taskbar uses."""
    try:
        return user32.GetSystemMetricsForDpi(49, user32.GetDpiForSystem()) or 16  # SM_CXSMICON
    except AttributeError:
        return user32.GetSystemMetrics(49) or 16


def contrast(a, b):
    def lum(c):
        r, g, b = ((v / 255.0) / 12.92 if v <= 10 else ((v / 255.0 + 0.055) / 1.055) ** 2.4 for v in c)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def tray_font(px):
    """Bahnschrift Bold Condensed: its tall narrow digits keep two of them legible at 16 px; Segoe UI Bold if not."""
    from PIL import ImageFont
    fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    try:
        f = ImageFont.truetype(os.path.join(fonts, "bahnschrift.ttf"), px)
        f.set_variation_by_name("Bold Condensed")
        return f
    except (OSError, ValueError, AttributeError):
        pass
    try:
        return ImageFont.truetype(os.path.join(fonts, "segoeuib.ttf"), px)
    except OSError:
        return ImageFont.load_default(px)


def usage_image(pct, n):
    """The 5h figure drawn at n px: a rounded tile in the level's colour (so it shows on light and dark taskbars),
    the number on it, '!' once the limit is reached (three digits do not fit)."""
    from PIL import Image, ImageDraw
    pct = int(round(max(0, min(100, pct))))
    bg = next(c for top, c in LEVELS if pct < top)
    ink = max(INK, key=lambda t: contrast(bg, t))
    text = "!" if pct >= 100 else str(pct)
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, n - 1, n - 1), radius=max(2, n // 5), fill=bg)
    for px in range(n * 2, 4, -1):  # the largest size that leaves 1 px each side and 2 px above and below
        f = tray_font(px)
        l, t, r, b = d.textbbox((0, 0), text, font=f)
        if r - l <= n - 2 and b - t <= n - 4:
            break
    d.text(((n - (r - l)) / 2 - l, (n - (b - t)) / 2 - t), text, font=f, fill=ink)
    return im


def five_hour(lim, now):
    """(percent, reset time) of the 5h window, or None when there is no figure or it is past its reset."""
    v = lim.get("five_hour")
    try:
        if v and v.get("used_percentage") is not None and float(v.get("resets_at")) > now:
            return float(v["used_percentage"]), time.strftime("%H:%M", time.localtime(float(v["resets_at"])))
    except (TypeError, ValueError):
        pass
    return None


def tray_key():
    """What the tray shows: (icon size, 5h percent or None while there is no current figure, reset time)."""
    u = five_hour(read_limits(), time.time())
    return (tray_size(),) + ((None, None) if u is None else (int(round(max(0, min(100, u[0])))), u[1]))


def tray_look(key):
    """(image, tooltip) for a tray_key(): the 5h figure, or the octopus while there is none."""
    n, pct, reset = key
    if pct is None:
        return logo_image(n), "Field Agents: 5h no figure yet"
    return usage_image(pct, n), "Field Agents: 5h %d %%, resets %s" % (pct, reset)


def load_opts():
    opts = {"alerts": True, "sound": True, "dock": "right"}
    try:
        opts.update(json.load(open(OPTS_FILE, encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return opts


def save_opts(opts):
    os.makedirs(STATE_DIR, exist_ok=True)
    json.dump(opts, open(OPTS_FILE, "w", encoding="utf-8"))


def tray(events, opts):
    try:
        import pystray
        from pystray._util import win32
    except ImportError:
        return None

    class Icon(pystray.Icon):
        def _assert_icon_handle(self):
            """pystray asks LoadImage for the default size (SM_CXICON, 32 px at 100 %) and the tray shrinks that to
            16; the figure is drawn at the tray's own size, so it is loaded at exactly that size."""
            if self._icon_handle:
                return
            n = self.icon.width
            fd, path = tempfile.mkstemp(".ico")
            try:
                with os.fdopen(fd, "wb") as f:
                    self.icon.save(f, format="ICO", sizes=[(n, n)])
                self._icon_handle = win32.LoadImage(None, path, win32.IMAGE_ICON, n, n, win32.LR_LOADFROMFILE)
            finally:
                os.remove(path)
    menu = pystray.Menu(
        pystray.MenuItem("Show or Hide  (Ctrl+Alt+C)", lambda: events.put("toggle"), default=True),
        pystray.MenuItem("Refresh", lambda: events.put("refresh")),
        pystray.MenuItem("Dock on the Right", lambda: events.put("dock:right"),
                         checked=lambda _: opts["dock"] != "top", radio=True),
        pystray.MenuItem("Dock at the Top", lambda: events.put("dock:top"),
                         checked=lambda _: opts["dock"] == "top", radio=True),
        pystray.MenuItem("Alerts", lambda: events.put("opt:alerts"), checked=lambda _: opts["alerts"]),
        pystray.MenuItem("Alert Sound", lambda: events.put("opt:sound"), checked=lambda _: opts["sound"]),
        pystray.MenuItem("Quit", lambda: events.put("quit")))
    key = tray_key()
    icon = Icon("agent-panel", *tray_look(key), menu=menu)
    icon.key = key
    icon.run_detached()
    return icon


# ---------- the window: agent-panel.html in a WebView2 window (mindmap desktop-apps.md) ----------
def ago(ts):
    d = time.time() - ts
    if d < 60:
        return "now"
    if d < 3600:
        return "%dm" % (d // 60)
    if d < 86400:
        return "%dh" % (d // 3600)
    if d < 172800:
        return "yesterday"
    if d < 7 * 86400:
        return "%dd" % (d // 86400)
    d = datetime.datetime.fromtimestamp(ts)
    return d.strftime("%d %b" if d.year == datetime.datetime.now().year else "%d %b %Y").lstrip("0")



WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def own_window(title, visible=True):
    """The top window of this process with this title (a visible one, unless visible=False)."""
    found = []

    def each(h, _):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value == os.getpid() and (user32.IsWindowVisible(h) or not visible) and window_title(h) == title:
            found.append(h)
            return False
        return True
    user32.EnumWindows(WNDENUMPROC(each), 0)
    return found[0] if found else None


def quiet_forms():
    """pywebview (6.2) activates every window it makes, and a panel that may take the foreground (started at
    sign-in, or from the window in front) handed it to each in turn, the last (the hidden preview) keeping it:
    the form's constructor sets MinimumSize and TopMost, which WinForms applies with SetWindowPos without
    SWP_NOACTIVATE (TopMost always; MinimumSize unless it is the 0 x 0 it already has, hence min_size=(0, 0) at
    create_window); Form.Show() is SW_SHOW, a hidden window too (made with Show() then Hide()); on a TopMost form
    Show() also moves the keyboard into WebView2, which takes it into its own window about 0.4 s later; and the
    Shown handler moves it there on every window. pywebview's focus=False only adds WS_EX_NOACTIVATE, which stops
    a click, not a call. pywebview's form is replaced, before webview.start(), by a subclass that sets TopMost
    itself, with SWP_NOACTIVATE (WinForms then counts the form not TopMost, so Show() moves no focus), shows the
    form first with SW_SHOWNOACTIVATE (WinForms reads that WM_SHOWWINDOW and counts it visible, so pywebview's
    Show() has nothing left to do), and whose Shown handler keeps only its signal. A pywebview without these
    parts is left as it is."""
    try:
        from webview.platforms import winforms
        view = winforms.BrowserView
        base = view.BrowserForm
        base.on_shown
    except Exception:
        return

    class Form(base):
        def __init__(self, window, cache_dir):
            super().__init__(window, cache_dir)
            if window.hidden:
                self.Opacity = 0  # never seen in its moment on screen; pywebview hides it and sets it back to 1
            user32.ShowWindow(self.Handle.ToInt64(), 4)  # SW_SHOWNOACTIVATE

        @property
        def TopMost(self):
            return getattr(self, "quiet_top", False)

        @TopMost.setter
        def TopMost(self, on):
            self.quiet_top = on
            if self.IsHandleCreated:  # HWND_TOPMOST or HWND_NOTOPMOST; SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
                user32.SetWindowPos(self.Handle.ToInt64(), -1 if on else -2, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)

        def on_shown(self, *_):  # bound by the constructor in place of pywebview's
            self.shown.set()
    view.BrowserForm = Form


def ok(t):
    return {"t": t, "err": False}


def err(t):
    """A failure: it stays on screen until the next click or Esc, and says what to do about it."""
    return {"t": t, "err": True}


def short(title, n=40, end=""):
    """A title cut to n characters; a cut one ends in "…" and takes no further ending."""
    return title + end if len(title) <= n else title[:n].rstrip() + "…"


class Api:
    """What the page may call, as window.pywebview.api.<name>; each returns {"t": message, "err": bool}
    or None, shown under the card it was about."""

    def __init__(self, panel):
        self._p = panel

    def ready(self):
        self._p.page_ready = True
        self._p.push(force=True)

    def click(self, sid):
        return self._p.click(sid)

    def open(self, sid):
        return self._p.open(sid)

    def set_hidden(self, sid, on):
        self._p.set_hidden(sid, on)

    def close(self, sid):
        return self._p.close(sid)

    def back(self, sid):
        return self._p.back(sid)

    def delete(self, sid):
        return self._p.delete(sid)

    def close_delete(self, sid):
        return self._p.close_delete(sid)

    def hide(self):
        self._p.events.put("toggle")

    def drag(self, ox, oy):
        self._p.drag(ox, oy)

    def region(self, rects):
        self._p.region(rects)

    def peek(self, sid):
        try:
            return self._p.peek(sid)
        except Exception:
            return None

    def strip(self):
        self._p.events.put("strip")

    def status_page(self):
        try:
            os.startfile(STATUS_PAGE)  # the default browser
        except OSError:
            return err("Could not open the browser. The page is " + STATUS_PAGE)
        return None


class AlertApi:
    """What the alert page may call."""

    def __init__(self, panel):
        self._p = panel

    def ready(self):
        self._p.alert_up()

    def go(self, sid):
        self._p.alert_go(sid)

    def dismiss(self):
        self._p.alert_dismiss()


class StripApi:
    """What the screenshot strip may call; a screenshot is named by its capture time, its key in the keeper."""

    def __init__(self, panel):
        self._p = panel

    def ready(self):
        self._p.strip_up()

    def press(self):
        return self._p.strip_press()

    def release(self, n):
        return self._p.strip_release(n)

    def enlarge(self, at):
        self._p.view_toggle(at)

    def copy(self, at):
        shots = getattr(self._p, "shots", None)
        return bool(shots and shots.copy_text(at))

    def pin(self, at):
        self._p.events.put("pin:" + json.dumps(at))  # done on the poll loop's thread, as every other strip change

    def drop(self, sid, n):
        # a selection's x in the band: out of that session's ":" reply list
        self._p.events.put("qdrop:" + json.dumps([str(sid), int(n)]))


class ViewApi:
    """What the large preview may call."""

    def __init__(self, panel):
        self._p = panel

    def ready(self):
        self._p.view_up()

    def open(self, at):
        p = next((s["path"] for s in self._p.strip_items if s["at"] == at), None)
        self._p.view_close()
        if p and os.path.exists(p):
            os.startfile(p)  # the default viewer

    copy = StripApi.copy  # Copy as Text: the strip's Copy, the very same call: the same clipboard text

    def copy_image(self, at):  # Copy as Image: the picture itself
        shots = getattr(self._p, "shots", None)
        return bool(shots and shots.copy_image(at))

    def delete(self, at):
        return self._p.view_delete(at)

    def close(self):
        self._p.view_close()


def animations_on():
    """Windows' "Show animations in Windows", the setting a browser reads as prefers-reduced-motion."""
    on = wt.BOOL(True)
    user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(on), 0)  # SPI_GETCLIENTAREAANIMATION
    return bool(on.value)


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


# bound as prototypes of their own, so the argtypes pystray sets on the shared user32 functions never clash
MonitorFromRect = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.POINTER(wt.RECT), wt.DWORD)(("MonitorFromRect", user32))
MonitorFromWindow = ctypes.WINFUNCTYPE(ctypes.c_void_p, wt.HWND, wt.DWORD)(("MonitorFromWindow", user32))
GetMonitorInfo = ctypes.WINFUNCTYPE(wt.BOOL, ctypes.c_void_p, ctypes.POINTER(MONITORINFO))(("GetMonitorInfoW", user32))
HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)
SetWindowsHookEx = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_int, HOOKPROC, ctypes.c_void_p, wt.DWORD)(
    ("SetWindowsHookExW", user32))
CallNextHookEx = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_int, wt.WPARAM, wt.LPARAM)(
    ("CallNextHookEx", user32))
UnhookWindowsHookEx = ctypes.WINFUNCTYPE(wt.BOOL, ctypes.c_void_p)(("UnhookWindowsHookEx", user32))
GetModuleHandle = ctypes.WINFUNCTYPE(ctypes.c_void_p, wt.LPCWSTR)(("GetModuleHandleW", kernel32))
PostThreadMessage = ctypes.WINFUNCTYPE(wt.BOOL, wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM)(("PostThreadMessageW", user32))


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class Outside:
    """While the large preview shows, without it ever taking the keyboard: Esc closes it (and reaches no other
    window), and so does a press of any mouse button outside the given windows (the preview and the strip; that
    press still goes where it was aimed). Low-level hooks on a thread of their own, removed by stop()."""

    def __init__(self, close, hwnds):
        self.close, self.hwnds, self.tid, self.up = close, hwnds, None, threading.Event()
        threading.Thread(target=self.run, daemon=True).start()
        self.up.wait(2)

    def inside(self, pt):
        """The press lands on one of the windows, not merely inside its rectangle: the strip is not topmost, so
        another window may cover it."""
        h = user32.WindowFromPoint(pt)
        return bool(h) and user32.GetAncestor(h, 2) in self.hwnds  # GA_ROOT

    def later(self):
        threading.Thread(target=self.close, daemon=True).start()  # a hook must return at once

    def run(self):
        def key(code, wp, lp):
            if code == 0 and KBDLLHOOKSTRUCT.from_address(lp).vkCode == 0x1B:  # VK_ESCAPE
                if wp in (0x100, 0x104):  # WM_KEYDOWN, WM_SYSKEYDOWN
                    self.later()
                return 1
            return CallNextHookEx(None, code, wp, lp)

        def mouse(code, wp, lp):
            # WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN, WM_XBUTTONDOWN
            if code == 0 and wp in (0x201, 0x204, 0x207, 0x20B) and not self.inside(MSLLHOOKSTRUCT.from_address(lp).pt):
                self.later()
            return CallNextHookEx(None, code, wp, lp)
        self.procs = (HOOKPROC(key), HOOKPROC(mouse))  # kept alive as long as the hooks
        mod = GetModuleHandle(None)
        hooks = [SetWindowsHookEx(13, self.procs[0], mod, 0), SetWindowsHookEx(14, self.procs[1], mod, 0)]  # _LL
        self.tid = kernel32.GetCurrentThreadId()
        self.up.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass
        for h in hooks:
            if h:
                UnhookWindowsHookEx(h)

    def stop(self):
        if self.tid:
            PostThreadMessage(self.tid, 0x0012, 0, 0)  # WM_QUIT


class Panel:
    def __init__(self):
        self.events = queue.Queue()
        self.cache = load_cache()
        self.sessions, self.hidden = [], load_hidden()
        self.hwnd, self.icon, self.visible, self.page_ready, self.dragging = None, None, True, False, False
        self.pushed, self.lock = None, threading.Lock()
        self.scanned, self.error = False, None  # the first scan has finished; the last scan's failure
        self.opts = load_opts()
        self.rects = []  # what the page has open under the strip, in css px: [x, y, w, h, radius]
        self.idle_since = {}  # GLM sessions on their way back to Claude: since when each has been idle
        self.returns = {}  # session id -> {"via": "GLM"/"Codex", "open": its window still runs it}; see watch_return()
        self.ret_lock = threading.Lock()  # the loop's watch_return() and the page's Back to Claude touch the same records
        self.claims_swept = 0
        self.nc = None  # the DWM frame policy last set (see frame())
        self.front = None  # the last window that came to the front, not counting the panel's own (follow_front())
        self.link_click = None  # (session window, time) of the last Ctrl+click on one (watch_clicks())
        self.links_used = set()  # the clicks a window was already brought forward for (on_flash())
        self.links_told = {}  # flashing window -> when links.log last said it was left flashing
        self.wins = {}  # pid of an open session -> the window showing it (window_of), kept between scans
        self.paste_proc, self.paste_windows = None, None  # the paste key's process; the windows last written for it
        self.opening = threading.Lock()  # open() calls arrive on threads of their own, at once
        self.launched = {}  # session id -> when open() started it; cleared once ~/.claude/sessions shows it
        self.repairing, self.update_note = False, None  # a failed Claude Code update: being repaired; what is left
        self.claude_status = False  # status.claude.com, read every STATUS_EVERY by watch_status(); False: not read yet, None: unreadable
        self.turns = {}  # session id -> its current turn, for the working card's timer; see turn_start()
        self.bg_seen = {}  # session id -> first read that saw its background work (ms); see bg_start()
        self.metas = {}  # subagent meta files read so far: path -> (mtime, running agent or False); see subagents()
        self.live = {}  # the session files of the last live pass, for peek()
        self.peeks = {}  # what peek() read so far, by transcript: last prompts, first prompts, background commands
        with open(HTML, encoding="utf-8") as f:
            html = f.read().replace("/*MASCOT*/", mascot_js(("hero-run", "hero-run@2x", "hero-sleep", "hero-sleep@2x")) + " window.DOCK = %s;" % json.dumps(self.opts["dock"]), 1)
        # min_size=(0, 0) on every window: a minimum set as the form is made activates it (see quiet_forms())
        self.window = webview.create_window(TITLE, html=html, js_api=Api(self), width=WIDTH, height=600,
                                            min_size=(0, 0), frameless=True, easy_drag=False, background_color=BG)
        # the alert: its own window, made now and kept hidden until a session waits for an answer
        self.alert_hwnd, self.alert_ready, self.alert_ids, self.alerted = None, False, [], set()
        with open(ALERT_HTML, encoding="utf-8") as f:
            ahtml = f.read().replace("/*MASCOT*/", mascot_js(("asking",)), 1)
        self.alert = webview.create_window(ALERT_TITLE, html=ahtml, js_api=AlertApi(self), width=ALERT_W, height=130,
                                           min_size=(0, 0), frameless=True, easy_drag=False, on_top=True, hidden=True,
                                           focus=False, shadow=False, background_color="#FFFFFF")
        # the strip of the last three screenshots: likewise its own window, owned by the panel, never focused
        self.strip_hwnd, self.strip_ready, self.strip_items, self.strip_key = None, False, [], None
        self.strip_on, self.strip_seen, self.strip_shown = strip_wanted(), -1, False
        with open(SHOTS_HTML, encoding="utf-8") as f:
            shtml = f.read()
        # min_size 0 x 0 also because pywebview's default 200 x 100 cut the strip's bottom edge: a window held
        # taller than its region loses its bottom edge
        self.strip = webview.create_window(SHOTS_TITLE, html=shtml, js_api=StripApi(self), width=360, height=82,
                                           min_size=(0, 0), frameless=True, easy_drag=False, on_top=False,
                                           hidden=True, focus=False, shadow=False, background_color="#FFFFFF")
        self.strip_at, self.strip_drag, self.strip_now = strip_at(), None, None
        # the ":" reply's numbered lists, one per session (quote_poll): the clipboard's sequence number last read,
        # the lists ({session id: list}, kept in the hook's LIST file, read back at start so a restart keeps them)
        # and whether they are live, the mtime of each session's reset record last seen, the session whose list the
        # band shows (the one last in front), the window and shown name in front at the last turn, and what the
        # band shows now ((session id, name, ((n, text, more), ...)) or None)
        self.quote_mod, self.quote_seq, self.quote = load_quote(), None, None
        self.qlists, self.quote_on, self.qreset = self.quote_load(), False, {}
        self.quote_sid, self.quote_before = None, None
        self.band_key, self.band_h = None, 0  # what the band was last given (list, width, cap) and its height
        self.pins = strip_pins()
        if self.pins != local("shot_pins") and local("shot_pins") is not None:  # a repeat or a bad entry: written clean
            try:
                save_local("shot_pins", self.pins)
            except OSError:
                pass
        # the large preview of a strip picture: the same page in view mode, its own window, never focused
        self.view_hwnd, self.view_ready, self.view_shown, self.view_at, self.view_sent = None, False, False, None, {}
        self.view_watch, self.view_lock = None, threading.RLock()
        self.view = webview.create_window(VIEW_TITLE, html=shtml.replace("/*MODE*/", 'window.MODE = "view";', 1),
                                          js_api=ViewApi(self), width=640, height=400, min_size=(0, 0),
                                          frameless=True, easy_drag=False, on_top=True, hidden=True, focus=False,
                                          shadow=False, background_color="#FFFFFF")

    def run(self):
        quiet_forms()
        webview.start(self.main, gui="edgechromium", private_mode=True)

    def main(self):
        """Runs on its own thread once the window is up: placement, hotkey, tray, then the poll loop."""
        for _ in range(100):
            self.hwnd = own_window(TITLE)
            if self.hwnd:
                break
            time.sleep(0.1)
        if not self.hwnd:
            return self.quit()
        # out of the taskbar and Alt+Tab, like a tool window; the style takes effect on the next show
        user32.ShowWindow(self.hwnd, 0)
        ex = user32.GetWindowLongW(self.hwnd, -20)  # GWL_EXSTYLE
        user32.SetWindowLongW(self.hwnd, -20, (ex | 0x80) & ~0x40000)  # WS_EX_TOOLWINDOW, not WS_EX_APPWINDOW
        self.dock = Edge(self.hwnd)
        self.shadow = Shade(self.hwnd)
        self.place()
        user32.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE: the keyboard stays where the user has it
        threading.Thread(target=self.follow_front, daemon=True).start()
        threading.Thread(target=self.follow_links, daemon=True).start()
        threading.Thread(target=self.watch_clicks, daemon=True).start()
        threading.Thread(target=hotkey_loop, args=(self.events,), daemon=True).start()
        threading.Thread(target=self.watch_status, daemon=True).start()
        threading.Thread(target=self.watch_paste, daemon=True).start()
        self.icon = tray(self.events, self.opts)
        self.shots = load_shots()
        self.last_scan = self.last_live = 0
        while True:
            try:
                while True:
                    ev = self.events.get_nowait()
                    if ev == "toggle":
                        self.toggle()
                    elif ev == "refresh":
                        self.last_scan = 0
                    elif ev == "quit":
                        return self.quit()
                    elif ev == "strip":
                        self.toggle_strip()
                    elif ev.startswith("pin:"):
                        self.toggle_pin(float(ev[4:]))
                    elif ev.startswith("qdrop:"):
                        self.quote_drop(*json.loads(ev[6:]))
                    elif ev.startswith("dock:"):
                        self.set_dock(ev[5:])
                    elif ev.startswith("opt:"):
                        self.opts[ev[4:]] = not self.opts[ev[4:]]
                        save_opts(self.opts)
                        if not self.opts["alerts"]:
                            self.alert_dismiss()
                        if self.icon:
                            self.icon.update_menu()
            except queue.Empty:
                pass
            if kernel32.WaitForSingleObject(QUIT_EVENT, 0) == 0:
                return self.quit()
            if kernel32.WaitForSingleObject(SHOW_EVENT, 0) == 0:  # auto-reset: one show per start
                self.show()
            now = time.time()
            if now - self.last_scan > SCAN_EVERY:
                self.last_scan = self.last_live = now
                try:
                    self.sessions = scan(self.cache)
                    save_cache(self.cache)
                    self.scanned, self.error = True, None
                except Exception as e:
                    # stays on screen until a scan succeeds, so an old list is never taken for a current one
                    self.scanned = True
                    # worded once, at the first failure, so it is not re-announced every minute
                    if not self.error:
                        self.error = ("Could not read sessions at %s: %s. %s; it tries again every %d s." % (
                            time.strftime("%H:%M"), e,
                            "The list is from the last good read" if self.sessions else "No read has worked yet",
                            SCAN_EVERY))
                self.wins = {}  # every scan looks each window up afresh (a tab dragged into another window)
                self.find_windows()
                self.background(running())
                self.feed_shots()
                self.watch_update()
                self.push()
                self.check_alerts()
                self.watch_return()
                self.update_strip()  # a file deleted meanwhile, a changed screen or switched-off keeper
            elif now - self.last_live > LIVE_EVERY:
                self.last_live = now
                self.update_live()
            if self.shots and self.shots.version != self.strip_seen:  # a screenshot saved or moved: at once
                self.strip_seen = self.shots.version
                self.update_strip()
            elif self.quote_poll():  # a passage selected, or a ":" prompt used it
                self.update_strip()
            time.sleep(0.2)

    # ----- placement -----
    def scale(self):
        return user32.GetDpiForWindow(self.hwnd) / 96.0 or 1.0

    def place(self, dock=None):
        dock = dock or self.opts["dock"]
        x, y, w, h = self.dock.place(dock, self.scale())
        if dock == "top":
            self.clip(dock, w)  # shaped before it grows, so the tall window is never seen whole
        user32.SetWindowPos(self.hwnd, 0, x, y, w, h, 0x0004 | 0x0010)  # SWP_NOZORDER | SWP_NOACTIVATE
        if dock != "top":
            self.clip(dock)

    def frame(self, dock):
        """pywebview's shadow=True forces DWM's frame on (DWMWA_NCRENDERING_POLICY = enabled): its shadow and
        1 px border. DWM draws them round the whole window rectangle and ignores the window region, so on top
        they traced the hidden 476 px room as a line across the screen. On top the frame is off; on the right
        it stays on, as it always was."""
        policy = 1 if dock == "top" else 2  # DWMNCRP_DISABLED, DWMNCRP_ENABLED
        if self.nc != policy:
            self.nc = policy
            dwmapi.DwmSetWindowAttribute(self.hwnd, 2, ctypes.byref(ctypes.c_int(policy)), 4)

    def clip(self, dock, width=None):
        """On top the window shows only the strip and what the page has open under it (a menu, a tooltip,
        the set-aside list); clicks anywhere else go to the windows below. On the right it shows whole."""
        self.frame(dock)
        if dock != "top":
            user32.SetWindowRgn(self.hwnd, None, True)
            self.shade(None)
            return
        s = self.scale()
        if width is None:
            r = wt.RECT()
            user32.GetWindowRect(self.hwnd, ctypes.byref(r))
            width = r.right - r.left
        rgn = gdi32.CreateRectRgn(0, 0, width, int(round(STRIP * s)))
        for x, y, w, h, rad in self.rects:
            part = gdi32.CreateRoundRectRgn(int(x * s), int(y * s), int((x + w) * s) + 1, int((y + h) * s) + 1,
                                            int(rad * 2 * s), int(rad * 2 * s))
            gdi32.CombineRgn(rgn, rgn, part, 2)  # RGN_OR
            gdi32.DeleteObject(part)
        user32.SetWindowRgn(self.hwnd, rgn, True)  # the system owns the region from here
        self.shade("top")

    def shade(self, dock):
        """The strip's drop shadow follows the strip: under its bottom edge while the panel shows on top, gone
        while it is hidden, dragged as a column or docked on the right."""
        if not getattr(self, "shadow", None):
            return
        if dock != "top" or not self.visible:
            return self.shadow.set(None)
        s, work = self.scale(), self.dock.work()
        self.shadow.set((work.left, work.top + int(round(STRIP * s)), work.right - work.left), self.rects, s)

    def region(self, rects):
        """The page reports what it has open under the strip."""
        try:
            self.rects = [[float(v) for v in r[:5]] for r in rects]
        except (TypeError, ValueError):
            self.rects = []
        if self.opts["dock"] == "top" and not self.dragging:
            self.clip("top")

    def set_dock(self, dock, page=True):
        dock = "top" if dock == "top" else "right"
        self.opts["dock"] = dock
        save_opts(self.opts)
        self.rects = []
        if page:
            self.window.evaluate_js("app.dock(%s)" % json.dumps(dock))
        if self.visible:
            self.place()
        if self.icon:
            self.icon.update_menu()
        self.update_strip()

    def drag(self, ox, oy):
        """The header was dragged (ox, oy: where it was grabbed, in css px). Until the button is released the
        panel takes the shape it will have when dropped: near the top edge (outside the right column) the strip
        at the top, anywhere else the column, hanging from the pointer. Released, it docks in that shape."""
        if self.dragging:
            return
        self.dragging = True
        try:
            button = 0x02 if user32.GetSystemMetrics(23) else 0x01  # SM_SWAPBUTTON: the primary button
            s, work = self.scale(), self.dock.work()
            side_w = int(round(WIDTH * s))
            ox, oy = int(ox * s), int(oy * s)
            shape = self.opts["dock"]
            pt = wt.POINT()
            held = True
            while held:
                # read the button first and the pointer after: a drag the page reports late, with the button
                # already released, still lands where the pointer was let go
                held = bool(user32.GetAsyncKeyState(button) & 0x8000)
                user32.GetCursorPos(ctypes.byref(pt))
                want = "top" if pt.y < work.top + TOP_ZONE * s and pt.x < work.right - side_w else "right"
                if want != shape:
                    shape = want
                    self.window.evaluate_js("app.dock(%s)" % json.dumps(shape))
                    if shape == "top":
                        self.rects = []
                        self.place("top")
                    else:
                        # the column hangs from the pointer by its header
                        ox, oy = int(60 * s), int(26 * s)
                        self.clip("right")
                if shape == "right":
                    user32.SetWindowPos(self.hwnd, 0, pt.x - ox, pt.y - oy, side_w, work.bottom - work.top,
                                        0x0004 | 0x0010)
                if held:
                    time.sleep(0.01)
            if shape == "right":
                self.glide_to(self.dock.place("right", s))
        finally:
            self.dragging = False
        self.set_dock(shape, page=False)

    def glide_to(self, rect):
        """Move the window to its dock in 160 ms, easing out; at once when Windows animations are off."""
        r = wt.RECT()
        user32.GetWindowRect(self.hwnd, ctypes.byref(r))
        x0, y0 = r.left, r.top
        x1, y1, w, h = rect
        steps = 8 if animations_on() else 0
        for i in range(1, steps + 1):
            t = 1 - (1 - i / steps) ** 3
            user32.SetWindowPos(self.hwnd, 0, int(x0 + (x1 - x0) * t), int(y0 + (y1 - y0) * t), w, h, 0x0004 | 0x0010)
            time.sleep(0.02)

    def toggle(self):
        """Ctrl+Alt+C and the tray: a panel covered by another window comes to the front;
        a panel already in front hides; a hidden one shows."""
        if self.visible and user32.GetForegroundWindow() != self.hwnd:
            self.raise_panel()
            return
        self.visible = not self.visible
        if not self.visible:
            user32.ShowWindow(self.hwnd, 0)  # SW_HIDE
            self.shade(None)
        else:
            self.place()
            user32.ShowWindow(self.hwnd, 5)
            self.raise_panel()
        self.update_strip()  # shown beside the panel, hidden with it

    def show(self):
        """A second start (the desktop shortcut): as Ctrl+Alt+C, but never hides the panel."""
        if self.visible:
            self.raise_panel()
        else:
            self.toggle()

    def raise_panel(self):
        user32.SetWindowPos(self.hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002)  # HWND_TOPMOST, no move or size
        user32.SetWindowPos(self.hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002)  # back to HWND_NOTOPMOST
        user32.SetForegroundWindow(self.hwnd)
        if user32.GetForegroundWindow() != self.hwnd:
            press(0x12)  # a lone Alt lets this process take the foreground
            user32.SetForegroundWindow(self.hwnd)

    # ----- data to the page -----
    def update_live(self):
        """Refresh only which sessions are open and what they are doing, between full scans. An open
        session's transcript time is read again too, so a session that just started working moves to
        the top within LIVE_EVERY, not at the next full scan (up to SCAN_EVERY later)."""
        live = running()
        known = {s["id"] for s in self.sessions}
        if any(sid not in known for sid in live):
            self.last_scan = 0  # a session the list does not have yet: scan now, not in up to 15 s
        for sid in [i for i in self.launched if i in live]:
            self.launched.pop(sid, None)  # it is running now: the status guards it from here on
        for s in self.sessions:
            run = live.get(s["id"]) or {}
            s["status"], s["pid"], s["waiting"], s["since"] = run.get("status"), run.get("pid"), run.get("waiting"), run.get("since")
            if s["status"]:
                s["ctx"] = read_ctx(s["id"])
                try:
                    s["mtime"] = os.stat(s["path"]).st_mtime
                except OSError:
                    pass
        self.sessions.sort(key=lambda s: s["mtime"], reverse=True)
        self.find_windows()
        self.background(live)
        self.feed_shots()
        self.push()
        self.check_alerts()
        self.watch_return()

    def background(self, live):
        """What each open session runs in the background, for the badge on its card: its child runs (from the
        process snapshot) and its subagents (from their meta files). Every live pass; closed sessions have none."""
        self.live = live
        try:
            runs = child_runs(live, procs())
        except Exception:
            runs = {}
        for s in self.sessions:
            run = live.get(s["id"])
            work = list(runs.get(s["id"], []))
            if run:
                started = (run.get("started") or 0) / 1000.0
                if not started:
                    b = born(run["pid"])
                    started = (b - FT_EPOCH) / 1e7 if b else time.time()
                try:
                    work += subagents(s, started - 1, self.metas)
                except Exception:
                    pass
            s["bg"] = sorted(work, key=lambda w: w["since"])

    def feed_shots(self):
        """The open sessions, for the screenshot keeper: it reads what is pasted into their transcripts."""
        if getattr(self, "shots", None):
            self.shots.see([{"id": s["id"], "path": s["path"], "home": where_of(s), "title": s["title"]}
                            for s in self.sessions if s["status"] and not s["headless"]])

    # ----- a session's own window counts as part of the panel -----
    def follow_front(self):
        """Its own thread: a WinEvent hook on the foreground window. Out of context, so Windows calls back on this
        thread's message loop; the panel's own windows are skipped, so self.front keeps the window that was in front
        before a click on the panel made the panel the foreground window. Not WINEVENT_SKIPOWNPROCESS: that also
        drops the event when the panel itself brings a session's window forward (a card click), the very case
        where the panel must go right under it."""
        moves = [None]  # the hook on the front session window's moves, while one is in front

        def on_front(hook, event, hwnd, obj, child, thread, ms):
            if not hwnd or own(hwnd):
                return
            self.front = hwnd
            if getattr(self, "shots", None):
                self.shots.front(hwnd)  # a Desktop screenshot is named after the window in front
            if moves[0]:
                user32.UnhookWinEvent(moves[0])
                moves[0] = None
            if hwnd in self.wins.values():
                self.lift(hwnd)
                threading.Thread(target=self.stay_over, args=(hwnd,), daemon=True).start()
                # maximising, restoring or dragging the window already in front can raise it again without a
                # foreground event; Windows reports it as a location change. Only that window's process is followed, and only while
                # it is in front, as location changes come by the hundred while anything moves.
                pid = wt.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                moves[0] = SetWinEventHook(0x800B, 0x800B, None, self.move_proc, pid.value, 0, 0)  # LOCATIONCHANGE

        def on_move(hook, event, hwnd, obj, child, thread, ms):
            if obj == 0 and hwnd == self.front and not self.beneath(hwnd):  # OBJID_WINDOW
                self.lift(hwnd)
        self.front_proc, self.move_proc = WINEVENTPROC(on_front), WINEVENTPROC(on_move)  # kept alive with the hooks
        fg = user32.GetForegroundWindow()
        self.front = None if not fg or own(fg) else fg
        if not SetWinEventHook(3, 3, None, self.front_proc, 0, 0, 0):  # EVENT_SYSTEM_FOREGROUND, WINEVENT_OUTOFCONTEXT
            return
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def lift(self, hwnd):
        """A session's own window has come to the front: the panel, and with it the shadow it owns, go directly under
        that window, over every other ordinary window, without being activated, so the keyboard stays in the
        terminal and the terminal stays over the panel (session window > panel > the rest). Any other window in
        front covers the panel as before (it is not topmost). Never behind a topmost window: that would make the
        panel topmost too (focus_session sets the window topmost for a moment)."""
        if not self.visible or self.dragging or not hwnd or user32.GetWindowLongW(hwnd, -20) & 0x8:  # WS_EX_TOPMOST
            return
        flags = 0x0001 | 0x0002 | 0x0010  # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
        user32.SetWindowPos(self.hwnd, hwnd, 0, 0, 0, 0, flags)  # hWndInsertAfter: right below that window

    def stay_over(self, hwnd):
        """The foreground event can arrive before the terminal has raised itself (its own thread does that, a moment
        later), so the panel, put under the window where it still was, can end up under other windows too. Look
        again a few times in the next 1.5 s, while that window stays in front, and put the panel back directly
        under it whenever it is not."""
        for wait in LIFT_AGAIN:
            time.sleep(wait)
            if user32.GetForegroundWindow() != hwnd:
                return
            if not self.beneath(hwnd):
                self.lift(hwnd)

    def beneath(self, hwnd):
        """The panel is directly under this window: below it, with nothing between them but this process's own
        windows (the shadow) and hidden ones."""
        h = user32.GetWindow(hwnd, 2)  # GW_HWNDNEXT
        while h:
            if h == self.hwnd:
                return True
            if user32.IsWindowVisible(h) and not own(h):
                return False
            h = user32.GetWindow(h, 2)
        return False

    # ----- a link opened from a session's terminal comes to the front -----
    def follow_links(self):
        """Its own thread: a hidden window on the shell hook (RegisterShellHookWindow). A Ctrl+clicked link in
        Windows Terminal is opened with ShellExecute; when the program that shows it was already running (Chrome,
        VS Code, ...), that program is refused the foreground by Windows' foreground lock and its window stays
        behind the terminal, its taskbar button flashing (HSHELL_FLASH). on_flash() brings such a window forward,
        and nothing else (see link_raise())."""
        shell = user32.RegisterWindowMessageW("SHELLHOOK")

        def proc(h, m, w, l):
            if m == shell and w == 0x8006:  # HSHELL_FLASH (HSHELL_REDRAW | HSHELL_HIGHBIT); lParam: the window
                try:
                    self.on_flash(int(l))
                except Exception as e:
                    links_log("error", repr(e))
                return 0
            return DefWindowProc(h, m, w, l)
        self.links_proc = WNDPROC(proc)  # kept alive with the window
        inst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW(lpfnWndProc=self.links_proc, hInstance=inst, lpszClassName="ClaudePlusFAAgentPanelLinks")
        user32.RegisterClassW(ctypes.byref(wc))
        # never shown: WS_POPUP, WS_EX_TOOLWINDOW; top-level, as the shell hook wants (a message-only window is not)
        hwnd = CreateWindowEx(0x80, "ClaudePlusFAAgentPanelLinks", "Field Agents Links", 0x80000000, 0, 0, 0, 0,
                              None, None, inst, None)
        if not hwnd or not user32.RegisterShellHookWindow(hwnd):
            return
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def watch_clicks(self):
        """Its own thread: remembers the last Ctrl+click of the primary mouse button on a session window, as
        self.link_click = (that window, time). While a session window is in front, Ctrl and the button are read
        every CLICK_EVERY with GetAsyncKeyState (no hook: the mouse and the keyboard never wait for this process,
        and no key is read but Ctrl); otherwise it only looks every 0.25 s whether one has come to the front. A press
        is the button going down between two looks, or Windows' pressed-since-last-look bit for a tap shorter than
        CLICK_EVERY; the window is the one under the cursor (WindowFromPoint's root), not only the foreground, so
        a Ctrl+click that also activates a terminal counts."""
        down, looking, pt = False, False, wt.POINT()
        while True:
            if user32.GetForegroundWindow() not in self.wins.values():
                down = looking = False
                time.sleep(0.25)
                continue
            button = 0x02 if user32.GetSystemMetrics(23) else 0x01  # SM_SWAPBUTTON: the primary button
            state = user32.GetAsyncKeyState(button)
            if not looking:  # the pressed-since bit may be from a press made elsewhere: only a held button counts
                state, looking = state & ~1, True
            held = bool(state & 0x8000)
            if (held and not down or state & 1) and user32.GetAsyncKeyState(0x11) & 0x8000:  # VK_CONTROL
                user32.GetCursorPos(ctypes.byref(pt))
                h = user32.WindowFromPoint(pt)
                root = h and user32.GetAncestor(h, 2)  # GA_ROOT
                if root and root in self.wins.values():
                    self.link_click = (root, time.time())
            down = held
            time.sleep(CLICK_EVERY)

    def on_flash(self, flash):
        """A window flashed. When it is the link the user just Ctrl+clicked in the session window still in front
        (link_raise()), bring it forward with the same raise as a card click; otherwise leave it flashing. Only
        flashes of visible windows seen while a session window is in front are logged (WSLg's hidden window
        flashes every second, everywhere)."""
        now, fg = time.time(), user32.GetForegroundWindow()
        wins = set(self.wins.values())
        if fg not in wins or not flash or own(flash):
            return
        cloaked = ctypes.c_int(0)
        dwmapi.DwmGetWindowAttribute(flash, 14, ctypes.byref(cloaked), 4)  # DWMWA_CLOAKED
        shown = bool(user32.IsWindowVisible(flash) and not cloaked.value and user32.GetAncestor(flash, 2) == flash)
        if not shown:
            return

        def pid_of(h):
            p = wt.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(p))
            return p.value
        click = self.link_click
        self.links_used = {u for u in self.links_used if now - u[1] < 60}
        exe = os.path.basename(image_path(pid_of(flash)) or "?")
        if not link_raise(flash, shown, fg, wins, pid_of, click, now, self.links_used):
            if now - self.links_told.get(flash, 0) > 30:  # a window asking for attention flashes several times
                self.links_told = {h: t for h, t in self.links_told.items() if now - t <= 30}
                self.links_told[flash] = now
                links_log("left flashing:", exe, "(no Ctrl+click in this terminal in the last %d s)" % LINK_WAIT)
            return
        self.links_used.add(click)
        up = bring_front(flash)
        links_log("brought forward:" if up else "could not bring forward:", exe,
                  "%.2f s after the Ctrl+click" % (now - click[1]))

    def find_windows(self):
        """The window of every open session, looked up once per process and kept: lift() runs inside the hook's
        callback and must not attach to consoles. A session window already in front when it is first found (just
        opened from the panel, before Claude Code wrote its session file) gets the panel under it at once."""
        live = {s["pid"] for s in self.sessions if s["status"] and s.get("pid")}
        before = set(self.wins.values())
        wins = {p: h for p, h in self.wins.items() if p in live and user32.IsWindow(h)}
        for pid in live - set(wins):
            h = window_of(pid)
            if h:
                wins[pid] = h
        self.wins = wins
        windows = sorted(set(wins.values()))
        if windows != self.paste_windows:  # the paste key's process reads them from this file
            try:
                save_paste_windows(windows)
                self.paste_windows = windows
            except OSError:
                pass  # written again at the next pass
        fg = user32.GetForegroundWindow()
        if fg in wins.values() and fg not in before:
            self.lift(fg)

    def watch_paste(self):
        """Its own thread: keeps the paste key's process (agent-panel-paste.py) running while "paste_keys" is not
        false. Once a second its window must answer within PASTE_ANSWER; after two misses in a row (stuck, so Ctrl+V
        and Alt+V would stay dead in the terminal) it is ended, which frees its keys at once, and started again. It
        ends by itself when the panel ends."""
        misses, started = 0, 0.0
        while True:
            time.sleep(1)
            p = self.paste_proc
            alive = bool(p) and p.poll() is None
            if not paste_wanted():
                if alive:
                    h = paste_window()
                    if h:
                        user32.PostMessageW(h, 0x0010, 0, 0)  # WM_CLOSE
                    else:
                        p.kill()
                continue
            if alive:
                h = paste_window()
                if (h and answers(h)) or (not h and time.time() - started < 5):  # answering, or still starting
                    misses = 0
                    continue
                misses += 1
                if misses < 2:
                    continue
                try:
                    p.kill()
                    p.wait(3)
                except Exception:
                    pass
            misses = 0
            if time.time() - started >= PASTE_RESTART:
                self.paste_proc, started = start_paste(), time.time()

    def window_for(self, s):
        h = window_of(s["pid"]) if s.get("pid") else None
        if h:
            wins = dict(self.wins)
            wins[s["pid"]] = h
            self.wins = wins
        return h

    def shown_in_front(self, s, hwnd):
        """The session's window is showing and in front: it was the foreground window before the click on the panel
        (or still is, when the click did not activate the panel) AND it is the top ordinary window right now. The
        foreground alone is not enough: a raised window can be the foreground window while another one covers it.
        In a terminal holding several tabs, only while the session's own tab shows."""
        if user32.IsIconic(hwnd):
            return False
        fg = user32.GetForegroundWindow()
        if fg and own(fg):
            fg = self.front
        if fg != hwnd or top_window() != hwnd:
            return False
        name = s.get("name")
        return not self.shared(s, hwnd) or not name or name in window_title(hwnd)

    def other_names(self, s):
        """The names every other listed session goes by (its name, its title), as a terminal tab would show them."""
        names = {shown_name(n) for o in self.sessions if o["id"] != s["id"] for n in (o.get("name"), o.get("title"))}
        return names - {"", shown_name(s.get("name"))}

    def shared(self, s, hwnd):
        """Another open session shows in the same window (tabs of one terminal). Only then does the title tell which
        tab shows: a terminal holding one session may carry any title (a new session's says "Claude Code")."""
        return any(h == hwnd for p, h in self.wins.items() if p != s.get("pid"))

    # ----- back to Claude after a GLM or Codex fallback (see RETURN_DIR) -----
    def watch_return(self):
        """Every live pass: each session glm-fallback.py moved to GLM or Codex, one step further on its way back.
        What the cards need (on GLM / on Codex, whether that window still runs it) is kept in self.returns."""
        try:
            names = [n for n in os.listdir(RETURN_DIR) if n.endswith(".json")]
        except OSError:
            names = []
        self.drop_old_claims()
        found = {}
        if names:
            snap = procs()
            par = {p: v[0] for p, v in snap.items()}
            with self.ret_lock:
                for n in names:
                    path = os.path.join(RETURN_DIR, n)
                    rec = read_record(path)
                    if not rec:
                        continue  # half written or foreign: left alone
                    try:
                        self.step_return(path, rec, par, snap)
                    except Exception:
                        pass  # never let it stop the panel's loop; the next pass tries again
                    if os.path.exists(path):
                        found[rec["session"]] = {"via": VIA.get(rec.get("via"), "GLM"), "open": rec.get("_open", False)}
        if found != self.returns:
            self.returns = found
            self.push()

    def drop_old_claims(self):
        """Claim files (glm-fallback.py claim()) outlive their record, so a reader that loaded the record a moment
        earlier still finds it taken; after CLAIM_KEEP nobody can still hold that record."""
        now = time.time()
        if now - self.claims_swept < 3600:
            return
        self.claims_swept = now
        for p in glob.glob(os.path.join(RETURN_DIR, "*.carried")) + glob.glob(os.path.join(RETURN_DIR, "*.quiet")):
            try:
                # a carry file holds how far Codex's turns were carried: kept while its record lives
                if now - os.path.getmtime(p) > CLAIM_KEEP and not os.path.exists(
                        os.path.join(RETURN_DIR, os.path.basename(p).split(".")[0] + ".json")):
                    os.remove(p)
            except OSError:
                pass

    def step_return(self, path, rec, par, snap=None):
        """One step of one session's way back. The record lives until a Claude process holds the session again
        (or the transcript is gone): Not Now, a closed GLM or Codex window and GLM or Codex left in its window no
        longer drop it; the card then offers Back to Claude, and a resume of any kind brings it back."""
        sid, now, console = rec["session"], time.time(), rec["console"]
        via = rec.get("via") or "glm"
        answer_file = path[:-len(".json")] + ".answer"
        s = self.find(sid)
        transcript = rec.get("transcript") or (s or {}).get("path")
        fb = fallback()

        def done():
            """The session is back on Claude (or gone): the record goes, and a question still open with it."""
            self.end_asker(rec)
            for p in (path, answer_file, path[:-len(".json")] + ".watched"):
                try:
                    os.remove(p)
                except OSError:
                    pass
            self.idle_since.pop(sid, None)

        if transcript and not os.path.exists(transcript):
            return done()  # the conversation itself is gone (deleted)
        procs_ = session_procs(sid)
        console_up = console_alive(rec)
        if rec.get("claude_cmd"):
            # Continue or Back to Claude was taken and Claude started: the GLM or Codex window closes only once
            # Claude holds the session, so it is never without a window, and it is idle, so two never write it at once
            if [p for p in procs_ if runs_under(p["pid"], rec["claude_cmd"], par)]:
                if console_up:
                    end_tree(console, par)
                if via == "codex" and fb and fb.pending(rec):
                    # Codex wrote a turn after the carry, before its window closed: the --carry hook brings it in
                    # at the next prompt (the Codex window is closed now), so the record stays for it
                    for k in ("claude_cmd", "switch_at", "go", "carry"):
                        rec.pop(k, None)
                    save_record(path, rec)
                    return
                return done()
            if now - rec.get("switch_at", now) > START_WAIT or not alive(rec["claude_cmd"]):
                # Claude did not start: the record stays, GLM or Codex as it is; Codex's turns were not carried
                for k in ("claude_cmd", "switch_at", "go"):
                    rec.pop(k, None)
                if via == "codex" and fb and rec.get("carry"):
                    fb.give_back(rec, rec.pop("carry"))
                save_record(path, rec)
            return
        if via == "codex":
            # Codex holds no Claude session: its own processes under the console are the sign it still runs
            snap = snap or procs()
            agent = [p for p, (pp, exe) in snap.items()
                     if console_up and p != console and exe.lower() in CODEX_EXES and runs_under(p, console, par)]
            glm = []
        else:
            agent = glm = [p for p in procs_ if console_up and runs_under(p["pid"], console, par)]
        orig = next((p for p in procs_ if p["pid"] == rec.get("orig_pid") and p not in glm), None)
        back = [p for p in procs_ if p not in glm and p is not orig]
        changed = False
        if agent and not rec.get("agent_seen"):
            rec["agent_seen"], changed = True, True
        if orig and orig["status"] == "idle" and not rec.get("orig_idle"):
            rec["orig_idle"], changed = True, True  # the old window's turn has ended (the hook ran inside it)
        if orig and rec.get("orig_idle") and orig["status"] in ("busy", "waiting") and not rec.get("orig_used"):
            rec["orig_used"], changed = True, True  # typed into since: that window is the user's again, never closed
        # GLM or Codex still runs in its window (or is still starting there): a card click raises that window
        is_open = bool(console_up and (agent or not rec.get("agent_seen")))
        if rec.get("_open") != is_open:
            rec["_open"], changed = is_open, True
        # GLM or Codex runs in its window and the hook in the old Claude window has ended: that window closes, so it
        # can neither be typed into nor go on by itself at the reset beside them; never while it still runs work
        if fb and orig and orig["status"] == "idle" and is_open and rec.get("agent_seen") \
                and fb.watched(rec) and not rec.get("old_closed") and not rec.get("orig_used"):
            before = rec.get("old_work")
            if self.close_old(rec, orig, par, fb, transcript):
                changed, orig = True, None
            elif rec.get("old_work") != before:
                changed = True
        if back or (orig and (rec.get("orig_used") or rec.get("orig_idle") and orig["status"] in ("busy", "waiting"))):
            # a Claude window holds the session again (resumed by hand, or the old window typed into). GLM wrote
            # into the transcript itself; Codex's turns come in through the --carry hook on that window's start or
            # on a prompt typed once the Codex window has closed, so the record waits until Codex is closed and
            # nothing it did is left uncarried
            if via != "codex":
                return done()
            if self.asker_alive(rec):
                self.end_asker(rec)
                rec.pop("asker", None)
                changed = True
            if not is_open and fb and not fb.pending(rec):
                return done()
            if changed:
                save_record(path, rec)
            return
        asking = self.asker_alive(rec)
        if not is_open:
            if asking:
                self.end_asker(rec)  # its window closed under the question: nothing is left to ask about
                rec.pop("asker", None)
                changed = True
            if rec.pop("go", None):
                changed = True  # a Back to Claude waiting for it to be idle: the card's click resumes it now
            if fb and rec.get("agent_seen") and not rec.get("auto_back") and (fb.watched(rec) or not orig):
                # the GLM or Codex window was closed (or it ended there): the session comes back to Claude by itself,
                # reset or not; from Codex with everything it did, GLM wrote into the transcript itself. The old
                # window first; while it still runs work this waits, pass after pass (a prompt typed there makes it
                # the session's Claude window: Codex's turns then come in through the --carry hook)
                if orig and orig["status"] == "idle" and not rec.get("old_closed") and \
                        self.close_old(rec, orig, par, fb, transcript):
                    orig = None
                if not orig and (not s or folder_exists(s)):
                    rec["auto_back"], changed = now, True
                    if via == "codex" and now < rec["resets_at"]:
                        fb.set_quiet(sid, rec["resets_at"])  # its first prompt meets the limit: no way-out window
                    title = short(s["title"], 60) if s else rec.get("folder") or sid
                    try:
                        self.launch_back(rec, title, fb, transcript)
                    finally:
                        save_record(path, rec)
                    return
            if changed:
                save_record(path, rec)
            return
        answer = None
        try:
            with open(answer_file, encoding="utf-8") as f:
                answer = f.read().strip()
            os.remove(answer_file)
        except OSError:
            if rec.get("asker") and not asking:
                answer = "later"  # the window went without an answer
        if answer:
            rec.pop("asker", None)
            asking, changed = False, True
            if answer == "continue":
                rec["go"] = True
            elif rec.get("asked", 0) >= 2:
                rec["no_more"] = True  # Not Now twice: no more questions; the card keeps Back to Claude
            else:
                rec["next_ask"] = now + ASK_AGAIN
        if changed:
            save_record(path, rec)
        if asking:
            return
        if not rec.get("go") and (rec.get("no_more") or now < rec["resets_at"] or now < rec.get("next_ask", 0)):
            return
        # never cut GLM or Codex off mid-turn: only when it is idle, and has been for a moment. GLM: every GLM
        # process of the session says idle. Codex: the last turn in its thread file has ended and the file has
        # not been written for IDLE_FOR (a Codex just started has written its settings but no turn yet).
        if not self.agent_idle(rec, glm, agent, fb):
            self.idle_since.pop(sid, None)
            return
        if now - self.idle_since.setdefault(sid, now) < IDLE_FOR:
            return
        title = short(s["title"], 60) if s else rec.get("folder") or sid
        if rec.get("go"):
            if fb is None:
                return
            self.launch_back(rec, title, fb, transcript)
        else:
            script = next((f for f in FALLBACK if os.path.exists(f)), None)
            if not script:
                return
            name = VIA.get(via, "GLM")
            later = ("Not Now stays on %s and asks once more in %d minutes." % (name, ASK_AGAIN // 60)
                     if rec.get("asked", 0) == 0 else
                     "Not Now stays on %s and does not ask again; Back to Claude in its card's menu in "
                     "Field Agents brings it back." % name)
            p = subprocess.Popen([sys.executable, script, "--ask-return", answer_file, rec.get("folder") or "",
                                  title, later, name], creationflags=CREATE_NO_WINDOW, env=clean_env())
            rec["asker"], rec["asker_born"], rec["asked"] = p.pid, born(p.pid), rec.get("asked", 0) + 1
        self.idle_since.pop(sid, None)
        save_record(path, rec)

    def agent_idle(self, rec, glm, agent, fb):
        """GLM or Codex has no turn under way in its window (see step_return)."""
        if (rec.get("via") or "glm") == "codex":
            try:
                quiet = time.time() - os.path.getmtime(rec["codex_file"]) >= IDLE_FOR
            except (OSError, KeyError, TypeError):
                quiet = False
            return bool(agent) and quiet and fb is not None and fb.codex_idle(rec)
        return bool(glm) and all(p["status"] == "idle" for p in glm)

    def launch_back(self, rec, title, fb, transcript):
        """Resume the session with Claude in a new window: on its last Claude model (else default), and from Codex
        with the record of what happened there as the first prompt, unless the --carry hook has taken it already."""
        model = fb.claude_model(transcript)
        prompt = span = None
        if rec.get("via") == "codex":
            span = fb.take_carry(rec, "panel")  # every Codex turn not carried yet; None: nothing new
            if span:
                try:
                    prompt, rec["record_file"] = fb.codex_back_prompt(rec, span=span)
                except Exception:
                    fb.give_back(rec, span)
                    raise
        try:
            p = resume_claude(rec, title, model, prompt)
        except Exception:
            if span:
                fb.give_back(rec, span)
            raise
        rec["claude_cmd"], rec["switch_at"] = p.pid, time.time()
        if span:
            rec["carry"] = span
        return p

    def close_old(self, rec, orig, par, fb, transcript):
        """Close the old Claude window of a session moved to GLM or Codex: its console's whole tree, the console's first
        process last with exit code 0 (end_tree()), never WM_CLOSE. Not while it still runs work (fb.busy_work());
        that is kept in the record (old_work) and the window is looked at again on the next pass. True when it
        was closed."""
        work = fb.busy_work(rec["session"], orig["pid"], transcript)
        if work:
            rec["old_work"] = work
            return False
        root = console_root(orig["pid"], par)
        if runs_under(os.getpid(), root, par) or (rec.get("console") and runs_under(rec["console"], root, par)):
            root = orig["pid"]  # never the panel's own or the Codex window's ancestors
        end_tree(root, par)
        rec["old_closed"] = time.time()
        rec.pop("old_work", None)
        return True

    def asker_alive(self, rec):
        return bool(rec.get("asker") and alive(rec["asker"]) and born(rec["asker"]) == rec.get("asker_born"))

    def end_asker(self, rec):
        if self.asker_alive(rec):
            h = kernel32.OpenProcess(0x0001, False, int(rec["asker"]))
            if h:
                kernel32.TerminateProcess(h, 0)
                kernel32.CloseHandle(h)

    def back(self, sid):
        """Back to Claude from the card's menu. While GLM or Codex still runs in its window it goes the way the
        automatic way back goes (waits until GLM or Codex is idle, then resumes and closes that window), without
        waiting for the limit's reset: the user asked for it. With that window closed it resumes at once."""
        path = os.path.join(RETURN_DIR, sid + ".json")
        s = self.find(sid)
        title = short(s["title"], 60) if s else sid
        with self.ret_lock:
            if not read_record(path):
                return ok("It is already back on Claude.")
            snap = procs()
            par = {p: v[0] for p, v in snap.items()}
            try:
                self.step_return(path, read_record(path), par, snap)  # up to date first: it may be back already
            except Exception:
                pass
            rec = read_record(path)
            if not rec:
                return ok("It is back on Claude.")
            if rec.get("claude_cmd"):
                return ok("Going back to Claude…")
            fb = fallback()
            if fb is None:
                return err("glm-fallback.py is missing; reinstall claude-plus-fa.")
            name = VIA.get(rec.get("via"), "GLM")
            if rec.get("_open"):
                self.end_asker(rec)
                rec.pop("asker", None)
                rec["go"] = True
                save_record(path, rec)
                console = rec["console"]
                glm = [p for p in session_procs(sid) if runs_under(p["pid"], console, par)]
                agent = glm if rec.get("via") != "codex" else [
                    p for p, (pp, exe) in snap.items() if p != console and exe.lower() in CODEX_EXES
                    and runs_under(p, console, par)]
                if not self.agent_idle(rec, glm, agent, fb):
                    return ok("%s is in the middle of a turn; it goes back to Claude as soon as that turn ends." % name)
                return ok("Going back to Claude…")
            if s and not folder_exists(s):
                return err("Its folder was deleted.")
            try:
                self.launch_back(rec, title, fb, rec.get("transcript") or (s or {}).get("path"))
            except Exception as e:
                return err("Could not start Claude (%s)." % e)
            save_record(path, rec)
        return ok("Opening " + short(title, end="…") + " with Claude.")

    def return_window(self, sid):
        """The GLM or Codex window a card click raises while it still runs the session, else None."""
        r = self.returns.get(sid)
        if not r or not r.get("open"):
            return None
        rec = read_record(os.path.join(RETURN_DIR, sid + ".json"))
        return (rec and console_alive(rec) and window_of(rec["console"])) or None

    def watch_update(self):
        """Every scan: a failed Claude Code update not handled yet starts repair_update() on a thread of its own;
        one it could not repair keeps its message in the bottom line until Claude Code records a newer attempt."""
        if self.repairing:
            return
        try:
            with open(UPDATE_RESULT, encoding="utf-8") as f:
                rec = json.load(f)
        except Exception:
            rec = {}
        if rec.get("outcome") != "failed":
            self.update_note = None
            return
        try:
            with open(UPDATE_SEEN, encoding="utf-8") as f:
                seen = json.load(f)
        except Exception:
            seen = {}
        if seen.get("timestamp") == rec.get("timestamp"):
            self.update_note = None if seen.get("ok") else seen.get("message")
            return
        self.repairing, self.update_note = True, "Claude Code's last update failed; repairing it in the background."

        def work():
            try:
                good, msg = repair_update(rec)
            except Exception as e:
                good, msg = False, "Could not repair Claude Code's failed update (%s). Reinstall it in PowerShell: %s" % (
                    e, REINSTALL)
            os.makedirs(STATE_DIR, exist_ok=True)
            save_record(UPDATE_SEEN, {"timestamp": rec.get("timestamp"), "ok": good, "message": msg, "at": time.time()})
            self.update_note = None if good else msg
            self.repairing = False
            self.last_scan = 0  # the next pass pushes the bottom line at once
        threading.Thread(target=work, daemon=True).start()

    # ----- the alert: a session waiting for an answer shows over every window, with a knock -----
    def check_alerts(self):
        """A session that has just started waiting joins the alert, which shows and knocks; one that
        no longer waits leaves it, and the alert goes once none is left. A session whose own window is
        already in front is not announced: its question is on screen."""
        waiting = [s for s in self.sessions if s["status"] and s.get("waiting")]
        ids = {s["id"] for s in waiting}
        self.alerted &= ids  # a session that stops waiting and asks again is announced again
        if not self.alert_ready:
            return  # nothing counts as announced before the alert can show it (a session waiting at start)
        # one whose window is in front is not marked announced, so it is announced once that window
        # is no longer in front, if it still waits
        fresh = [s for s in waiting if s["id"] not in self.alerted and not self.in_front(s)]
        self.alerted |= {s["id"] for s in fresh}
        before = list(self.alert_ids)
        self.alert_ids = [i for i in self.alert_ids if i in ids]
        if self.opts["alerts"]:
            self.alert_ids += [s["id"] for s in fresh if s["id"] not in self.alert_ids]
        if self.alert_ids != before or fresh:
            self.show_alert(knock=bool(fresh) and self.opts["alerts"])

    def in_front(self, s):
        hwnd = s.get("pid") and window_of(s["pid"])
        if not hwnd or user32.GetForegroundWindow() != hwnd:
            return False
        name = s.get("name")
        return not name or name in window_title(hwnd)  # a terminal showing another tab does not count

    def alert_up(self):
        """The alert page has loaded. Its window is looked up only now: pywebview makes a second window
        on another thread while main() runs, so at main()'s start it may not exist yet."""
        self.alert_hwnd = own_window(ALERT_TITLE, visible=False)
        if self.alert_hwnd:
            ex = user32.GetWindowLongW(self.alert_hwnd, -20)
            # a tool window (no taskbar button) that never takes the keyboard: WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
            user32.SetWindowLongW(self.alert_hwnd, -20, (ex | 0x80 | 0x08000000) & ~0x40000)
            self.alert_ready = True

    def show_alert(self, knock=False):
        if not self.alert_hwnd or not self.alert_ready:
            return
        rows = [s for i in self.alert_ids for s in self.sessions if s["id"] == i]
        if not rows:
            self.alert.hide()
            return
        data = [{"id": s["id"], "title": s["title"], "path": where_of(s)} for s in rows]
        self.alert.evaluate_js("alertCard.update(%s, %s)" % (json.dumps(data), "true" if knock else "false"))
        # the card's height, as agent-alert.html lays it out: frame and top 93, 36 a row, 28 for "and n more"
        n = min(len(rows), ALERT_ROWS)
        h_css = 93 + 36 * n - 4 + (28 if len(rows) > ALERT_ROWS else 0)
        scale = user32.GetDpiForWindow(self.hwnd) / 96.0 or 1.0
        w, h = int(round(ALERT_W * scale)), int(round(h_css * scale))
        # top centre of the screen the user is working on (the one holding the window in front)
        mi = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        user32.GetMonitorInfoW(user32.MonitorFromWindow(user32.GetForegroundWindow(), 2), ctypes.byref(mi))
        wa = mi.rcWork
        # below the top strip when the panel is docked there, so it covers none of its tabs
        gap = 24 + (STRIP if self.opts["dock"] == "top" and self.visible else 0)
        x, y = wa.left + (wa.right - wa.left - w) // 2, wa.top + int(gap * scale)
        rgn = gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, int(32 * scale), int(32 * scale))
        user32.SetWindowRgn(self.alert_hwnd, rgn, True)
        # HWND_TOPMOST, SWP_NOACTIVATE: over everything, the keyboard stays where it was. Shown by ShowWindow,
        # whose WM_SHOWWINDOW tells WinForms and so WebView2 (which draws nothing in a window shown by SetWindowPos
        # alone); not pywebview's show(), which activates it (Form.Activate) and so takes the keyboard
        user32.SetWindowPos(self.alert_hwnd, -1, x, y, w, h, 0x0010)
        if not user32.IsWindowVisible(self.alert_hwnd):
            user32.ShowWindow(self.alert_hwnd, 4)  # SW_SHOWNOACTIVATE
            user32.SetWindowPos(self.alert_hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        if knock and self.opts["sound"] and os.path.exists(KNOCK):
            import winsound
            winsound.PlaySound(KNOCK, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)

    def alert_go(self, sid):
        """A session clicked in the alert: its window comes forward, and it leaves the alert."""
        s = self.find(sid)
        self.alert_ids = [i for i in self.alert_ids if i != sid]
        self.show_alert()
        if s and s["status"]:
            focus_session(s, self.other_names(s))

    def alert_dismiss(self):
        self.alert_ids = []
        if self.alert_hwnd:
            self.alert.hide()

    # ----- the strip: the pinned screenshots and the last three, in the panel's z-order, where the user dragged it -----
    def keeping(self):
        """The screenshot keeper is loaded and switched on ("screenshots" in claude-plus.local.json)."""
        shots = getattr(self, "shots", None)
        return bool(shots and shots.on())

    @staticmethod
    def float_style(hwnd):
        """A tool window (no taskbar button) that never takes the keyboard: WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE."""
        ex = user32.GetWindowLongW(hwnd, -20)
        user32.SetWindowLongW(hwnd, -20, (ex | 0x80 | 0x08000000) & ~0x40000)

    def strip_up(self):
        """The strip's page has loaded; its window, made on pywebview's thread, is looked up only now (as alert_up)."""
        self.strip_hwnd = own_window(SHOTS_TITLE, visible=False)
        if self.strip_hwnd:
            self.float_style(self.strip_hwnd)
            self.strip_ready = True
            self.strip_seen = -1  # the poll loop draws it on its next turn

    def view_up(self):
        """The preview's page has loaded (as strip_up); the poll loop hands it the strip's pictures next."""
        self.view_hwnd = own_window(VIEW_TITLE, visible=False)
        if self.view_hwnd:
            self.float_style(self.view_hwnd)
            self.view_sent, self.view_ready = {}, True
            self.strip_key = None

    def toggle_strip(self):
        """The header button: the strip on or off, the choice kept in claude-plus.local.json."""
        self.strip_on = not self.strip_on
        try:
            save_local("shot_strip", self.strip_on)
        except OSError:
            pass
        self.update_strip()
        self.push()

    def toggle_pin(self, at):
        """A Pin button: the screenshot pinned (it stays in the strip, the pinned ones before the recent ones), or
        unpinned; kept in claude-plus.local.json and handed to the keeper, which then never trims it off its index."""
        self.save_pins([a for a in self.pins if a != at] if at in self.pins else self.pins + [at])
        self.update_strip()

    def save_pins(self, pins):
        self.pins = pins
        if getattr(self, "shots", None):
            self.shots.keep = set(pins)
        try:
            save_local("shot_pins", pins)
        except OSError:
            pass

    def wake(self, hwnd, topmost):
        """Shown once, off screen, by ShowWindow, and hidden again: WinForms reads WM_SHOWWINDOW and tells WebView2
        it is visible, after which WebView2 draws in it however it is shown. The panel later shows it with
        SetWindowPos, which sends no WM_SHOWWINDOW: a window only ever shown that way stays blank. Not pywebview's
        show(): that activates it (Form.Activate) and took the foreground."""
        if topmost:
            user32.SetWindowPos(hwnd, -1, -32000, -32000, 0, 0, 0x0001 | 0x0010)  # off screen meanwhile
        else:
            user32.SetWindowPos(hwnd, 0, -32000, -32000, 0, 0, 0x0001 | 0x0004 | 0x0010)  # the same, SWP_NOZORDER
        user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
        user32.ShowWindow(hwnd, 0)

    def work_area(self, rect=None):
        """The work area of the screen nearest a rectangle (x, y, w, h), else of the panel's screen."""
        mi = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        if rect:
            x, y, w, h = rect
            mon = MonitorFromRect(ctypes.byref(wt.RECT(x, y, x + w, y + h)), 2)  # MONITOR_DEFAULTTONEAREST
        else:
            mon = MonitorFromWindow(self.hwnd, 2)
        GetMonitorInfo(mon, ctypes.byref(mi))
        return mi.rcWork

    def strip_rect(self, n, quote=None):
        """Screen pixels for a strip of n, with the ":" reply's band under the cards when quote (its numbered list of
        passages; then at least two cards wide, and as tall as the list wrapped, up to 1/QUOTE_CAP of
        the work area, past which it scrolls inside; n may be 0). Where the user dropped it, its top-right corner kept (so a strip of more
        pictures grows to the left), pulled wholly back onto the nearest screen when it would leave it (a monitor
        gone, a smaller resolution). Never dragged: at the top of the panel's screen, just left of the column when
        the panel is docked on the right (it covers none of it), just under the bar on the right when it is docked
        on top. More pictures than fit between its right edge and the screen's left edge wrap into further rows
        below (the page's flex-wrap breaks at the same width), in either dock, as the strip lies along the top.
        Only asked while the panel shows: the strip is hidden with it."""
        s = self.scale()
        if self.strip_at:
            right, top = self.strip_at
            wa = self.work_area((right - 1, top, 1, 1))
            right = min(max(right, wa.left + 1), wa.right)
        else:
            wa, gap = self.work_area(), int(round(SHOT_GAP * s))
            right, top = wa.right, wa.top
            if self.opts["dock"] == "top":
                top += int(round(STRIP * s))
            else:
                r = wt.RECT()
                user32.GetWindowRect(self.hwnd, ctypes.byref(r))
                right = min(right, r.left)
            right, top = right - gap, top + gap
        cols = max(1, min(max(n, 2 if quote else 1), int(((right - wa.left) / s - SHOT_PAD) // (SHOT_W + SHOT_PAD))))
        rows = -(-n // cols)
        lw = SHOT_PAD + cols * (SHOT_W + SHOT_PAD)
        w = math.ceil(lw * s)  # never a pixel short: the page would wrap early
        # the band spans the strip inside its 1 px border and 5 px padding; the page measures it at that width
        cap = int((wa.bottom - wa.top) / s / QUOTE_CAP) // QUOTE_LINE * QUOTE_LINE
        band = self.band(quote, lw - 2 * SHOT_PAD, cap) + QUOTE_BELOW if quote else 0
        h = math.ceil((SHOT_PAD + rows * (SHOT_H + SHOT_PAD) + band) * s)
        x, y = right - w, top
        wa = self.work_area((x, y, w, h))
        return max(wa.left, min(x, wa.right - w)), max(wa.top, min(y, wa.bottom - h)), w, h

    def update_strip(self):
        """Draws the strip again when what it shows or where it sits has changed; hides it when it is switched off,
        the keeper is off, no screenshot is left or the panel is hidden. Left alone while it is being dragged.
        The strip is owned by the panel (GWLP_HWNDPARENT), so Windows keeps it directly above the panel in z-order:
        it sinks when another window covers the panel and comes forward with it (lift(), a click on the panel,
        Ctrl+Alt+C), never above a window the panel is under. Never topmost, never activated."""
        if not self.strip_ready or not self.hwnd or (self.strip_drag and self.strip_drag["thread"].is_alive()):
            return
        if not self.strip_shown:
            user32.SetWindowLongPtrW(self.strip_hwnd, -8, self.hwnd)  # GWLP_HWNDPARENT: the owner, before it shows
            self.wake(self.strip_hwnd, False)
            self.strip_shown = True
        if self.view_ready and not self.view_shown:
            self.wake(self.view_hwnd, True)
            self.view_shown = True
        if self.pins and self.keeping():  # a pinned file deleted (here or elsewhere) or gone loses its pin
            gone = [a for a in self.pins if not self.shots.alive(a)]
            if gone:
                self.save_pins([a for a in self.pins if a not in gone])
        on = self.strip_on and self.keeping() and self.visible
        items = self.shots.latest(SHOT_N, self.pins) if on else []
        quote = self.quote if on else None  # its band shows even with no screenshot left: the strip is then that band
        counts = {True: 0, False: 0}
        for i in items:  # two sequences: pinned 1.. by pin order, recent 1.. from the newest back
            counts[i["pin"]] += 1
            i["num"] = counts[i["pin"]]
            i["title"], i["when"] = shot_label(i["path"])  # in the key: "14:32" becomes "yesterday 14:32" at midnight
        rect = self.strip_rect(len(items), quote) if items or quote else None
        if not quote:
            self.band(None)
        key = (json.dumps(items), rect, quote)
        if key == self.strip_key:
            return
        fresh = self.strip_key is None or key[0] != self.strip_key[0]
        self.strip_key, self.strip_items, self.strip_now = key, items, rect
        # the page first, hidden or not: a strip shown again later must not bring back cards it no longer has
        if fresh:
            data = [{"at": i["at"], "name": os.path.basename(i["path"]), "pin": i["pin"],
                     "num": i["num"], "title": i["title"], "when": i["when"]} for i in items]
            self.strip.evaluate_js("shotStrip.update(%s)" % json.dumps(data))
            self.strip.evaluate_js("shotStrip.open(%s)" % json.dumps(self.view_at))
        if not items:
            self.view_close()
        if not items and not quote:
            user32.ShowWindow(self.strip_hwnd, 0)  # SW_HIDE
            return
        x, y, w, h = rect
        r = int(round(24 * self.scale()))
        user32.SetWindowRgn(self.strip_hwnd, gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, r, r), True)
        # SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW: the owner keeps it above the panel; the keyboard stays put
        user32.SetWindowPos(self.strip_hwnd, 0, x, y, w, h, 0x0004 | 0x0010 | 0x0040)
        if fresh:
            self.feed_view(items)  # after the strip shows: a large new picture takes a moment to hand over

    def band(self, quote, width=0, cap=0):
        """Hands the strip's band one session's ":" reply list (quote: (session id, its name, ((n, text, more), ...)),
        each passage as sent and the characters cut off; None hides it), to be laid out width logical pixels wide
        under the session's name and at most cap tall, and returns the height it takes (the page measures the
        wrapped text, so the window is sized to it). Asked again only when one of the three changed."""
        key = (quote, width, cap) if quote else None
        if key != self.band_key:
            sid, name, items = quote or ("", "", ())
            rows = [{"n": n, "text": t, "more": m} for n, t, m in items]
            try:
                h = self.strip.evaluate_js("shotStrip.quote(%s, %d, %d, %s, %s)" % (
                    json.dumps(rows), width, cap, json.dumps(name), json.dumps(sid)))
                self.band_h = max(QUOTE_LINE, min(int(math.ceil(float(h))), cap)) if quote else 0
            except Exception:
                self.band_h = 2 * QUOTE_LINE if quote else 0  # the band still shows, scrolling what does not fit
            self.band_key = key
        return self.band_h

    def quote_load(self):
        """The ":" reply's lists as the last run left them (LIST: {session id: list}), so a restart (a reinstall)
        keeps what each session had selected; none when there are none. A file of the one machine-wide list kept
        before 2026-11-20 holds no "lists" and is not read back (its selections have no session)."""
        q = self.quote_mod
        d = q.load(q.LIST) if q else None
        lists = {}
        for sid, lst in ((d.get("lists") if isinstance(d, dict) else None) or {}).items():
            try:
                items = [{"n": int(i["n"]), "seq": i.get("seq"), "at": float(i["at"]), "text": str(i["text"]),
                          "more": int(i.get("more") or 0)} for i in lst["items"]]
                if items:
                    lists[str(sid)] = {"gen": int(lst["gen"]), "name": lst.get("name"), "added": lst.get("added"),
                                       "next": max(int(lst.get("next") or 1), items[-1]["n"] + 1), "items": items}
            except (KeyError, TypeError, ValueError, AttributeError):
                pass
        return lists

    def quote_poll(self):
        """Keeps the ":" reply's numbered lists, one per session (quote-reply.py, the UserPromptSubmit hook, reads its
        own session's from LIST), while the strip is switched on, the keeper is on and the panel shows (else they are
        empty and "off", and a ":" prompt quotes the clipboard itself). Every new text clipboard joins the list of the
        session it was selected in (copied_in(): the window Windows Terminal names as the clipboard's owner, and the
        session whose name that window's title shows), as 1, 2, 3, ...; a copy that cannot be tied to exactly one
        session joins no list. Not listed either: no text, a password manager's private format, the same text as that
        list's last entry, and what was on the clipboard when the lists went live (copied while nobody watched). A
        prompt in a session (the hook writes its reset record, with the list's generation and its highest number)
        empties that session's list only. The band shows the list of the session in front, or of the one last in
        front while no session is. The clipboard is opened only when its sequence number moved (well under a
        millisecond, once per copy); a clipboard another program still holds is read on a later turn. True when what
        the band shows changed."""
        q = self.quote_mod
        if not q:
            return False
        lists, now, changed = self.qlists, time.time(), False
        for sid in list(lists):  # a prompt submitted in that session: its hook wrote a reset record
            path = q.reset_path(sid)
            try:
                m = os.stat(path).st_mtime
            except OSError:
                m = None
            if m != self.qreset.get(sid):
                self.qreset[sid] = m
                if m is not None and q.apply_reset(lists, sid, q.load(path)):
                    changed = True
        live = self.strip_on and self.keeping() and self.visible
        if not live:
            self.quote_seq = None
            if self.quote_on or lists:
                self.quote_on = False
                lists.clear()
                changed = True
        else:
            if not self.quote_on:
                self.quote_on, changed = True, True
                self.quote_seq = q.seq()  # on the clipboard before the lists went live: no telling where it was made
            fg = user32.GetForegroundWindow()
            front = (fg, shown_name(window_title(fg))) if fg else None
            n = q.seq()
            if n != self.quote_seq and user32.IsClipboardFormatAvailable(PASTE_MARK):
                self.quote_seq = n  # the paste key's own clipboard (a path, or the user's put back): no passage
            elif n != self.quote_seq:
                n, text, private = q.clip(tries=1)
                owner = user32.GetClipboardOwner()
                owner = owner and (user32.GetAncestor(owner, 3) or owner)  # GA_ROOTOWNER, as window_of() gives
                if private is not None and q.seq() == n:  # else another copy came meanwhile: read on the next turn
                    self.quote_seq = n
                    p = q.passage(text) if text and not private else None
                    o = p and copied_in(owner, window_title(owner), self.quote_before, self.sessions,
                                        lambda o: window_of(o["pid"]))
                    if o and q.add(lists, o["id"], o["name"], n, p[0], p[1], now):
                        changed = True
            if front and not own(fg):
                o = tab_session(fg, window_title(fg), self.sessions, lambda o: self.wins.get(o["pid"]))
                if o:
                    self.quote_sid = o["id"]
            self.quote_before = front
        if changed:
            self.quote_save()
        lst = lists.get(self.quote_sid)
        shown = None
        if lst and lst["items"]:
            name = next((o.get("name") for o in self.sessions if o["id"] == self.quote_sid), None) or lst.get("name")
            shown = (self.quote_sid, name or "", tuple((i["n"], i["text"], i["more"]) for i in lst["items"]))
        if shown == self.quote:
            return False
        self.quote = shown
        return True

    def quote_save(self):
        """Writes the lists for the hook (whole, swapped in)."""
        try:
            self.quote_mod.write_json(self.quote_mod.LIST, {"on": self.quote_on, "mutex": MUTEX, "lists": self.qlists})
        except OSError:
            pass  # the hook then reads the last lists written; the next change writes again

    def quote_drop(self, sid, n):
        """A selection's x in the strip's band: out of that session's list; the others keep their numbers."""
        if self.quote_mod and self.quote_mod.drop(self.qlists, sid, n):
            self.quote_save()
        if self.quote_poll():  # what the band shows, from the lists as they are now
            self.update_strip()

    # ----- dragging the strip -----
    def strip_press(self):
        """A press anywhere on the strip. Returns its number, which the page hands back with strip_release()."""
        d = self.strip_drag
        if d and d["thread"].is_alive():
            return d["n"]
        d = {"n": (d["n"] + 1) if d else 1, "moved": False}
        d["thread"] = threading.Thread(target=self.strip_follow, args=(d,), daemon=True)
        self.strip_drag = d
        d["thread"].start()
        return d["n"]

    def strip_follow(self, d):
        """While the button is held: once the pointer has gone SHOT_MOVE px, the strip moves with it (moved with
        SWP_NOACTIVATE, so the keyboard stays in the window the user works in); where it is let go is kept in
        claude-plus.local.json and used from then on."""
        button = 0x02 if user32.GetSystemMetrics(23) else 0x01  # SM_SWAPBUTTON: the primary button
        far = (SHOT_MOVE * self.scale()) ** 2
        p0, pt, r = wt.POINT(), wt.POINT(), wt.RECT()
        user32.GetCursorPos(ctypes.byref(p0))
        user32.GetWindowRect(self.strip_hwnd, ctypes.byref(r))
        dx = dy = 0
        held = True
        while held:
            # the button first, the pointer after: a release seen late still lands where the pointer was let go
            held = bool(user32.GetAsyncKeyState(button) & 0x8000)
            user32.GetCursorPos(ctypes.byref(pt))
            dx, dy = pt.x - p0.x, pt.y - p0.y
            if not d["moved"] and dx * dx + dy * dy >= far:
                d["moved"] = True
                self.view_close()
            if d["moved"]:
                user32.SetWindowPos(self.strip_hwnd, 0, r.left + dx, r.top + dy, 0, 0, 0x0001 | 0x0004 | 0x0010)
            if held:
                time.sleep(0.01)
        if d["moved"]:
            self.strip_at = (r.right + dx, r.top + dy)
            try:
                save_local("shot_strip_at", list(self.strip_at))
            except OSError:
                pass
            self.strip_seen = -1  # the poll loop places it again: pulled back onto the screen if it was let go off it

    def strip_release(self, n):
        """The page's release: True when press n became a drag (then the page fires no click)."""
        d = self.strip_drag
        if not d or d["n"] != n:
            return False
        d["thread"].join(2)
        return d["moved"]

    # ----- the large preview -----
    def feed_view(self, items):
        """The preview holds the strip's pictures at full size, decoded, so one shows at once on a click; only a new
        or changed file is sent."""
        if not self.view_ready:
            return
        data, sent = [], {}
        for i in items:
            try:
                st = os.stat(i["path"])
                k = (i["path"], st.st_size, st.st_mtime)
            except OSError:
                continue
            d = {"at": i["at"], "name": os.path.basename(i["path"]), "pin": i["pin"], "num": i["num"]}
            if self.view_sent.get(i["at"]) != k:
                d["src"] = full_src(i["path"])[0]
            sent[i["at"]] = k
            data.append(d)
        self.view.evaluate_js("shotView.update(%s)" % json.dumps(data))
        self.view_sent = sent
        if self.view_at is not None and self.view_at not in sent:
            self.view_close()

    def view_toggle(self, at):
        """A click on a strip picture: shown large, or closed when it is the one already shown."""
        if self.view_at == at:
            self.view_close()
        else:
            self.view_show(at)

    def view_show(self, at):
        """The picture at up to its own size, in the largest free part of the strip's screen beside the strip (above,
        below, left or right of it), so it never covers the strip. Topmost, never activated: a popup the user just
        opened, which closes on any press outside it and the strip, so it never stays over a window the user turned
        to."""
        item = next((i for i in self.strip_items if i["at"] == at), None)
        if not item or not self.view_ready or not self.strip_now:
            return
        iw, ih = pic_size(item["path"])
        if not iw or not ih:
            return
        s = self.scale()
        sx, sy, sw, sh = self.strip_now
        wa = self.work_area(self.strip_now)
        m, head, pad = int(round(VIEW_MARGIN * s)), VIEW_HEAD * s, VIEW_PAD * s
        L, T, R, B = wa.left + m, wa.top + m, wa.right - m, wa.bottom - m
        best = None
        for l, t, r, b in ((L, T, R, sy - m), (L, sy + sh + m, R, B), (L, T, sx - m, B), (sx + sw + m, T, R, B)):
            bw, bh = r - l - 2 * pad, b - t - head - pad
            if bw > 0 and bh > 0:
                f = min(bw / iw, bh / ih, 1.0)
                if not best or f > best[0]:
                    best = (f, l, t, r, b)
        if not best:
            return
        f, l, t, r, b = best
        w = min(max(int(iw * f + 2 * pad), int(VIEW_MIN_W * s)), r - l)
        h = int(ih * f + head + pad)
        x, y = l + (r - l - w) // 2, t + (b - t - h) // 2
        with self.view_lock:
            self.view_at = at
            if not self.view_watch:  # watching before it shows, so an Esc right away already closes it
                self.view_watch = Outside(self.view_close, (self.view_hwnd, self.strip_hwnd))
            self.view.evaluate_js("shotView.show(%s)" % json.dumps(at))
            rr = int(round(24 * s))
            user32.SetWindowRgn(self.view_hwnd, gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, rr, rr), True)
            user32.SetWindowPos(self.view_hwnd, -1, x, y, w, h, 0x0010 | 0x0040)  # topmost, shown, not activated
        self.strip.evaluate_js("shotStrip.open(%s)" % json.dumps(at))

    def view_delete(self, at):
        """Delete on the preview: the file, wherever it is now (the Desktop or a project), goes to the Recycle Bin,
        never deleted for good, and without asking, as the Recycle Bin is the undo. The preview closes and the
        strip fills up with the next older screenshot. A file already gone only leaves the strip. False when the
        Recycle Bin refused it (the preview then stays)."""
        shots = getattr(self, "shots", None)
        if not shots:
            return False
        p = shots.path(at)
        if p and not (recycle([p]) and not os.path.exists(p)):
            return False
        shots.drop(at)  # the poll loop redraws the strip at once (Shots.version)
        self.view_close()
        return True

    def view_close(self):
        """Hides the preview: Esc, a click on it or outside it, a second click on its picture, Open, Delete, a
        drag."""
        with self.view_lock:
            if self.view_at is None:
                return
            self.view_at = None
            user32.ShowWindow(self.view_hwnd, 0)  # SW_HIDE
            watch, self.view_watch = self.view_watch, None
        if watch:
            watch.stop()
        self.view.evaluate_js("shotView.hide()")
        self.strip.evaluate_js("shotStrip.open(null)")

    def turn_start(self, s, turns):
        """When the session's current turn began, in ms since the epoch, for the timer on a working card; None out of
        a turn. Claude Code stamps statusUpdatedAt at each status change and leaves it alone while a turn runs, so a
        change to busy is the turn's start. A question inside the turn (status "waiting", or waitingFor) changes the
        stamp twice: the session keeps its start while it asks and when it works again after a read that saw it
        asking, so the timer runs on through the answer. A new stamp on a working session seen working at the last
        read means an end missed between two reads: a new turn. Without a stamp (older Claude Code), the first read
        that saw it working stands in."""
        if s["status"] not in ("busy", "waiting"):
            return None
        t, since, ask = self.turns.get(s["id"]), s.get("since"), bool(s.get("waiting"))
        if not t or (since != t["since"] and not t["ask"] and not ask):
            t = {"start": since or int(time.time() * 1000)}
        t["since"], t["ask"] = since, ask
        turns[s["id"]] = t
        return t["start"]

    def peek(self, sid):
        """What the card's popover shows for one session, read now for the card under the pointer only: its last
        prompt, and each piece of background work still running with the prompt or command it was given
        ({"kind", "what", "text", "since" ms}), oldest first. None for a session not in the list."""
        s = next((x for x in self.sessions if x["id"] == sid), None)
        if not s:
            return None
        c = self.peeks
        out = {"id": sid, "prompt": None, "bg": []}
        try:
            out["prompt"] = last_prompt(s["path"], c.setdefault("last", {}))
        except OSError:
            pass
        if not s["status"]:
            return out
        work = list(s.get("bg") or [])
        run = self.live.get(sid) or {}
        if run.get("pid"):
            started = (run.get("started") or 0) / 1000.0
            if not started:
                b = born(run["pid"])
                started = (b - FT_EPOCH) / 1e7 if b else time.time()
            try:
                cmds = bg_commands(s["path"], started - 1, c.setdefault("commands", {}))
                if cmds and late_child(int(run["pid"]), started, procs()):
                    work += cmds
            except OSError:
                pass
        for w in work:
            text = w.get("text")
            try:
                if w["kind"] == "subagent" and w.get("file"):
                    text = first_prompt(w["file"], c.setdefault("first", {}))
                elif w["kind"] == "run":
                    try:  # a run started with --no-session-persistence has no transcript: its command line then
                        text = first_prompt(os.path.join(CLAUDE, "projects", slug(w["cwd"]), w["sid"] + ".jsonl"),
                                            c.setdefault("first", {}))
                    except (OSError, KeyError, TypeError):
                        text = None
                    text = text or print_prompt(command_line(w["pid"]))
            except (OSError, KeyError, TypeError):
                pass
            out["bg"].append({"kind": KIND[w["kind"]], "what": w.get("what") or "", "text": text,
                              "since": int(w["since"] * 1000) if w.get("since") else None})
        out["bg"].sort(key=lambda w: w["since"] or 0)
        return out

    def bg_start(self, s, seen):
        """When an open session's background work began, in ms since the epoch, for the timer on a card that shows as
        working because of it: the start of the oldest child run or subagent still running (a run's own process
        creation time, an agent's transcript creation time; see child_runs() and subagents()). None when nothing
        runs. Work without a start of its own falls back as the turn timer does: the first read that saw the
        session's background work stands in."""
        bg = s.get("bg") or []
        if not s["status"] or not bg:
            return None
        first = seen[s["id"]] = self.bg_seen.get(s["id"]) or int(time.time() * 1000)
        known = [int(w["since"] * 1000) for w in bg if w.get("since")]
        return min(known) if len(known) == len(bg) else min(known + [first])

    def state(self):
        lim, now = read_limits(), time.time()
        turns, seen = {}, {}

        def usage(v, fmt):
            try:
                if v and float(v.get("resets_at")) > now:  # past its reset, the figure is stale
                    return {"pct": v.get("used_percentage"),
                            "note": time.strftime(fmt, time.localtime(float(v["resets_at"])))}
            except (TypeError, ValueError):
                pass
            return None
        out = {"sessions": [{"id": s["id"], "title": s["title"], "folder": s["folder"], "path": where_of(s),
                             "status": s["status"], "waiting": bool(s["status"] and s.get("waiting")),
                             "ctx": s.get("ctx"), "hidden": s["id"] in self.hidden, "turn": self.turn_start(s, turns),
                             "bgSince": self.bg_start(s, seen),
                             "here": folder_exists(s), "distro": s["distro"], "age": ago(s["mtime"]),
                             "headless": s.get("headless", False),
                             "via": (self.returns.get(s["id"]) or {}).get("via"),
                             "viaOpen": bool((self.returns.get(s["id"]) or {}).get("open")),
                             "bg": [{"kind": w["kind"], "what": w["what"], "for": lasted(w["since"])} for w in s.get("bg", [])]}
                            for s in self.sessions],
               "limits": {"five_hour": usage(lim.get("five_hour"), "%H:%M"),
                          "seven_day": usage(lim.get("seven_day"), "%a %H:%M")},
               "scanned": self.scanned, "error": self.error or self.update_note, "dbl": user32.GetDoubleClickTime(),
               "claude": self.claude_status, "strip": self.strip_on if self.keeping() else None}
        self.turns = turns  # only the sessions in a turn now: one that left its turn starts afresh next time
        self.bg_seen = seen
        return out

    def watch_status(self):
        """Reads status.claude.com now and every STATUS_EVERY on a thread of its own, so a slow network never holds
        up the list."""
        while True:
            self.claude_status = claude_status()
            self.push()
            time.sleep(STATUS_EVERY)

    def tray_update(self):
        """The tray follows the 5h figure: redrawn only when the number, its reset or the icon size changes,
        and back to the octopus once the figure is gone or past its reset."""
        key = tray_key()
        if self.icon and key != self.icon.key:
            self.icon.key = key
            self.icon.icon, self.icon.title = tray_look(key)

    def push(self, force=False):
        self.tray_update()
        if not self.page_ready:
            return
        with self.lock:
            data = json.dumps(self.state())
            if data == self.pushed and not force:
                return
            self.pushed = data
            self.window.evaluate_js("app.update(%s)" % data)

    def find(self, sid):
        return next((s for s in self.sessions if s["id"] == sid), None)

    # ----- what the page asks for -----
    def click(self, sid):
        s = self.find(sid)
        if not s:
            return None
        if not folder_exists(s):
            return err("Its folder was deleted. Hide or delete it from its menu.")
        if sid in self.returns and self.returns[sid]["open"]:
            return self.raise_return(sid)
        if s["status"]:
            # a second click on the card of a window already in front puts it away again
            hwnd = self.window_for(s)
            if hwnd and self.shown_in_front(s, hwnd):
                user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
                return None
            if not focus_session(s, self.other_names(s)):
                return err("Already open, but its window was not found. Look for it on the taskbar.")
            self.lift(hwnd or self.window_for(s))  # the window was just put on top: the panel goes right under it
            return None
        return ok("Double-click to open it.")

    def open(self, sid):
        """Start a closed session, once. Each call runs on its own thread, and a held Enter or Space on a card sends
        one call per key repeat (about 30 a second), so a launch under way blocks every further call for the same
        session until Claude Code registers it in ~/.claude/sessions (read here afresh, not the list's status, which
        can be LIVE_EVERY old) or LAUNCH_WAIT passes."""
        s = self.find(sid)
        if not s or not folder_exists(s):
            return None
        if sid in self.returns:
            # on GLM or Codex: its window is raised while it still runs there, otherwise it comes back to Claude
            if self.returns[sid]["open"]:
                return self.raise_return(sid)
            if s["status"]:
                return None
            with self.opening:
                if sid in running() or time.time() - self.launched.get(sid, 0) < LAUNCH_WAIT:
                    return None
                self.launched[sid] = time.time()
            return self.back(sid)
        if s["status"]:
            return None
        with self.opening:
            if sid in running() or time.time() - self.launched.get(sid, 0) < LAUNCH_WAIT:
                return None
            self.launched[sid] = time.time()
            open_session(s, resume_cmd(s) if not s["distro"] else None)
        return ok("Opening " + short(s["title"], end="…"))

    def raise_return(self, sid):
        """A click on the card of a session still running on GLM or Codex: that window comes up (a second click
        while it is in front puts it away), as for an open session; the model does not change."""
        hwnd = self.return_window(sid)
        if not hwnd:
            return err("Its %s window was not found. Look for it on the taskbar." % self.returns[sid]["via"])
        if user32.GetForegroundWindow() == hwnd:
            user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
            return None
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        if user32.GetForegroundWindow() != hwnd:
            press(0x12)  # a lone Alt lets this process take the foreground
            user32.SetForegroundWindow(hwnd)
        self.lift(hwnd)
        return None

    def set_hidden(self, sid, on):
        (self.hidden.add if on else self.hidden.discard)(sid)
        save_hidden(self.hidden)
        self.push()

    def close(self, sid):
        s = self.find(sid)
        if s and close_session(s):
            threading.Timer(1.5, lambda: self.events.put("refresh")).start()
            return ok("Closed " + short(s["title"], end="."))
        return err("Could not close it. Close its window yourself.")

    def delete(self, sid):
        s = self.find(sid)
        if not s:
            return None
        if s["status"]:
            return err("It is still open. Use Delete again; it closes the session first.")
        if s["distro"]:
            return err("Stored inside WSL, so it cannot go to the Recycle Bin.")
        if recycle(session_files(s)) and not os.path.exists(s["path"]):
            self.hidden.discard(sid)
            save_hidden(self.hidden)
            self.sessions = [x for x in self.sessions if x["id"] != sid]
            self.push()
            return ok("Moved " + short(s["title"]) + " to the Recycle Bin.")
        return err("Could not delete it. A file in it may be in use; try again in a moment.")

    def close_delete(self, sid):
        """Delete on an open session: close it, wait for its process to end, then delete it."""
        s = self.find(sid)
        if not s or not close_session(s):
            return err("Could not close it, so it was not deleted. Close its window yourself, then delete it.")
        for _ in range(40):  # up to 10 s
            time.sleep(0.25)
            if sid not in running():
                s["status"] = s["pid"] = None
                return self.delete(sid)
        return err("It did not close in time, so it was not deleted. Try again once its window is gone.")

    def quit(self):
        if getattr(self, "dock", None):
            self.dock.release()
        if self.icon:
            self.icon.stop()
        if getattr(self, "shadow", None):
            self.shadow.close()
        if getattr(self, "shots", None):
            self.shots.stop()
        if self.paste_proc and self.paste_proc.poll() is None and paste_window():
            user32.PostMessageW(paste_window(), 0x0010, 0, 0)  # WM_CLOSE; it also ends when this process does
        self.view_close()
        self.alert.destroy()  # a hidden window left behind keeps the program running
        self.strip.destroy()
        self.view.destroy()
        self.window.destroy()


if __name__ == "__main__":
    Panel().run()
