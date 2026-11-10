# Agent Panel: every Claude Code session on this PC, in a narrow list over the right
# edge of the screen, or in a strip of tabs over the top edge (drag its header there; tray: Dock). Double-click a closed session to resume it in its own folder, in a new
# terminal; click an open one to bring its window forward. Tray icon: click to show or hide;
# Ctrl+Alt+C does the same. The look is agent-panel.html, shown in a WebView2 window by pywebview.
# When a session starts waiting for an answer, agent-alert.html shows over every window and a knock
# sounds (tray menu: Alerts, Alert Sound).
import base64, ctypes, ctypes.wintypes as wt, datetime, json, os, queue, re, subprocess, sys, threading, time
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
OPTS_FILE = os.path.join(STATE_DIR, "options.json")


def mascot_js(only=None):
    """The octopus strips as data URLs: the page is handed over as a string, so it cannot load files."""
    strips = {}
    try:
        for name in sorted(os.listdir(MASCOT)):
            if name.endswith(".webp") and (only is None or name[:-5] in only):
                with open(os.path.join(MASCOT, name), "rb") as f:
                    strips[name[:-5]] = "data:image/webp;base64," + base64.b64encode(f.read()).decode("ascii")
    except OSError:
        pass  # no strips: the page keeps its plain logo
    return "window.MASCOT = %s;" % json.dumps(strips)
TITLE = "Agent Panel"
ALERT_TITLE = "Agent Panel Alert"
ALERT_W = 420          # logical pixels; the height follows the rows, as agent-alert.html lays them out
ALERT_ROWS = 4         # sessions listed in the alert; the rest are counted
SESSION_FILE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.jsonl$")
RESUME = "claude --resume %s --dangerously-skip-permissions"
WIDTH = 280            # logical pixels
STRIP = 45             # logical pixels: the top dock's strip, a 33 px tab centred in it (6 px above and below);
                       # its bottom edge sits one pixel above a maximised Chrome's address box
                       # (toolbar top y = 40, box top y = 46 at 100 %), so none of Chrome's tab strip shows under it
DROP_ROOM = 440        # logical pixels under the strip where its menus, tooltips and the set-aside list may open;
                       # the window region shows only the strip and whatever is open there
TOP_ZONE = 120         # logical pixels: a drag released this close to the top edge docks the panel there
SCAN_EVERY = 15        # seconds
LIVE_EVERY = 2         # seconds; only ~/.claude/sessions is read, so a closed session loses its dot quickly
CREATE_NEW_CONSOLE = 0x10
CREATE_NO_WINDOW = 0x08000000

BG = "#f4f6fb"          # Spark page background, shown before the page has drawn; the other tokens are in the html

user32, kernel32, shell32 = ctypes.windll.user32, ctypes.windll.kernel32, ctypes.windll.shell32
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowPos.argtypes = [wt.HWND, ctypes.c_ssize_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.SetWindowRgn.argtypes = [wt.HWND, ctypes.c_void_p, wt.BOOL]
gdi32 = ctypes.windll.gdi32
gdi32.CreateRectRgn.restype = gdi32.CreateRoundRectRgn.restype = ctypes.c_void_p
gdi32.CombineRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
dwmapi = ctypes.windll.dwmapi
dwmapi.DwmSetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]


# ---------- one instance only ----------
kernel32.CreateMutexW(None, False, "Local\\claude-plus-fa-agent-panel")
if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
    sys.exit(0)
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
                                   "waiting": o.get("waitingFor") or (o.get("status") == "waiting" or None)}
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


def where_of(s):
    """The session's folder as a full path, written the Windows way."""
    cwd = s["cwd"] or ""
    if s["distro"] and cwd.startswith("/"):
        return "\\\\wsl.localhost\\%s%s" % (s["distro"], cwd.replace("/", "\\"))
    if cwd.startswith("//"):
        cwd = cwd.replace("/", "\\")
    return cwd or s["folder"]


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
                                 "waiting": (run or {}).get("waiting"),
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


def open_session(s):
    cmd = RESUME % s["id"]
    title = "claude: " + clean(s["title"])
    if s["distro"]:  # a session started inside WSL: resume it there
        subprocess.Popen(["wsl.exe", "-d", s["distro"], "--cd", s["cwd"], "--", "bash", "-lic", cmd],
                         creationflags=CREATE_NEW_CONSOLE, env=clean_env())
    elif s["cwd"].startswith("\\\\"):  # cmd cannot stand in a UNC folder; PowerShell can
        subprocess.Popen(["powershell.exe", "-NoLogo", "-NoExit", "-Command",
                          "$Host.UI.RawUI.WindowTitle = '%s'; %s" % (title.replace("'", ""), cmd)],
                         cwd=s["cwd"], creationflags=CREATE_NEW_CONSOLE, env=clean_env())
    else:
        subprocess.Popen(["cmd.exe", "/k", "title %s& %s" % (title, cmd)],
                         cwd=s["cwd"], creationflags=CREATE_NEW_CONSOLE, env=clean_env())


# ---------- bringing an open session's window forward ----------
kernel32.GetConsoleWindow.restype = wt.HWND
user32.GetAncestor.restype = wt.HWND
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.GetForegroundWindow.restype = wt.HWND


def window_title(hwnd):
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def window_of(pid):
    """The top window showing a console process: the console itself, or the terminal
    (Windows Terminal) that owns its pseudo console."""
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


def focus_session(s):
    """Raise the window of an open session where it was (restored if minimised). In a terminal holding several tabs,
    step through them until the one titled with the session's name is showing."""
    hwnd = s.get("pid") and window_of(s["pid"])
    if not hwnd:
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE: back to the size and place it had before
    user32.SetForegroundWindow(hwnd)
    if user32.GetForegroundWindow() != hwnd:
        press(0x12)  # a lone Alt lets this process take the foreground
        user32.SetForegroundWindow(hwnd)
    name = s.get("name")
    if name and name not in window_title(hwnd):
        for _ in range(20):
            if user32.GetForegroundWindow() != hwnd:
                break
            press(0x11, 0x09)  # Ctrl+Tab: next tab
            time.sleep(0.15)
            if name in window_title(hwnd):
                break
    return True


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
        img = logo_image(64)
    except ImportError:
        return None
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
    icon = pystray.Icon("agent-panel", img, "Agent Panel", menu)
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


def animations_on():
    """Windows' "Show animations in Windows", the setting a browser reads as prefers-reduced-motion."""
    on = wt.BOOL(True)
    user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(on), 0)  # SPI_GETCLIENTAREAANIMATION
    return bool(on.value)


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


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
        self.nc = None  # the DWM frame policy last set (see frame())
        with open(HTML, encoding="utf-8") as f:
            html = f.read().replace("/*MASCOT*/", mascot_js() + " window.DOCK = %s;" % json.dumps(self.opts["dock"]), 1)
        self.window = webview.create_window(TITLE, html=html, js_api=Api(self), width=WIDTH, height=600,
                                            frameless=True, easy_drag=False, background_color=BG)
        # the alert: its own window, made now and kept hidden until a session waits for an answer
        self.alert_hwnd, self.alert_ready, self.alert_ids, self.alerted = None, False, [], set()
        with open(ALERT_HTML, encoding="utf-8") as f:
            ahtml = f.read().replace("/*MASCOT*/", mascot_js(("asking",)), 1)
        self.alert = webview.create_window(ALERT_TITLE, html=ahtml, js_api=AlertApi(self), width=ALERT_W, height=130,
                                           frameless=True, easy_drag=False, on_top=True, hidden=True,
                                           focus=False, shadow=False, background_color="#FFFFFF")

    def run(self):
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
        self.place()
        user32.ShowWindow(self.hwnd, 5)  # SW_SHOW
        threading.Thread(target=hotkey_loop, args=(self.events,), daemon=True).start()
        self.icon = tray(self.events, self.opts)
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
                self.push()
                self.check_alerts()
            elif now - self.last_live > LIVE_EVERY:
                self.last_live = now
                self.update_live()
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
        if self.visible:
            user32.ShowWindow(self.hwnd, 0)  # SW_HIDE
        else:
            self.place()
            user32.ShowWindow(self.hwnd, 5)
            self.raise_panel()
        self.visible = not self.visible

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
        for s in self.sessions:
            run = live.get(s["id"]) or {}
            s["status"], s["pid"], s["waiting"] = run.get("status"), run.get("pid"), run.get("waiting")
            if s["status"]:
                s["ctx"] = read_ctx(s["id"])
                try:
                    s["mtime"] = os.stat(s["path"]).st_mtime
                except OSError:
                    pass
        self.sessions.sort(key=lambda s: s["mtime"], reverse=True)
        self.push()
        self.check_alerts()

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
        # HWND_TOPMOST, SWP_NOACTIVATE: over everything, the keyboard stays where it was. pywebview shows
        # it (focus=False: without activating), as WebView2 draws nothing in a window shown behind its back
        user32.SetWindowPos(self.alert_hwnd, -1, x, y, w, h, 0x0010)
        if not user32.IsWindowVisible(self.alert_hwnd):
            self.alert.show()
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
            focus_session(s)

    def alert_dismiss(self):
        self.alert_ids = []
        if self.alert_hwnd:
            self.alert.hide()

    def state(self):
        lim, now = read_limits(), time.time()

        def usage(v, fmt):
            try:
                if v and float(v.get("resets_at")) > now:  # past its reset, the figure is stale
                    return {"pct": v.get("used_percentage"),
                            "note": time.strftime(fmt, time.localtime(float(v["resets_at"])))}
            except (TypeError, ValueError):
                pass
            return None
        return {"sessions": [{"id": s["id"], "title": s["title"], "folder": s["folder"], "path": where_of(s),
                              "status": s["status"], "waiting": bool(s["status"] and s.get("waiting")),
                              "ctx": s.get("ctx"), "hidden": s["id"] in self.hidden,
                              "here": folder_exists(s), "distro": s["distro"], "age": ago(s["mtime"]),
                              "headless": s.get("headless", False)}
                             for s in self.sessions],
                "limits": {"five_hour": usage(lim.get("five_hour"), "%H:%M"),
                           "seven_day": usage(lim.get("seven_day"), "%a %H:%M")},
                "scanned": self.scanned, "error": self.error, "dbl": user32.GetDoubleClickTime()}

    def push(self, force=False):
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
        if s["status"]:
            return None if focus_session(s) else err("Already open, but its window was not found. Look for it on the taskbar.")
        return ok("Double-click to open it.")

    def open(self, sid):
        s = self.find(sid)
        if s and folder_exists(s) and not s["status"]:
            open_session(s)
            return ok("Opening " + short(s["title"], end="…"))
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
        self.alert.destroy()  # a hidden window left behind keeps the program running
        self.window.destroy()


if __name__ == "__main__":
    Panel().run()
