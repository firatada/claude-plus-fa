# Agent Panel: every Claude Code session on this PC, in a narrow list over the right
# edge of the screen, or in a strip of tabs over the top edge (drag its header there; tray: Dock). Double-click a closed session to resume it in its own folder, in a new
# terminal; click an open one to bring its window forward, click it again to minimise it. While a session's own
# window is in front the panel stays over it, as if that window were part of the panel. Tray icon: the 5-hour usage
# figure in its level colour (the octopus until there is one); click it to show or hide; Ctrl+Alt+C does the same. The look is agent-panel.html, shown in a WebView2 window by pywebview.
# When a session starts waiting for an answer, agent-alert.html shows over every window and a knock
# sounds (tray menu: Alerts, Alert Sound). A card counts what its session runs in the background (child
# `claude` runs and subagents) while any run.
import base64, ctypes, ctypes.wintypes as wt, datetime, json, os, queue, re, subprocess, sys, tempfile, threading, time
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
STRIP = 46             # logical pixels: the top dock's strip, a 34 px tab centred in it (6 px above and below);
                       # its bottom edge meets a maximised Chrome's address box (toolbar top y = 40, box top y = 46
                       # at 100 %), so none of Chrome's tab strip shows under it
SHADE = 8              # logical pixels: the drop shadow under the strip (see Shade)
DROP_ROOM = 440        # logical pixels under the strip where its menus, tooltips and the set-aside list may open;
                       # the window region shows only the strip and whatever is open there
TOP_ZONE = 120         # logical pixels: a drag released this close to the top edge docks the panel there
SCAN_EVERY = 15        # seconds
LIVE_EVERY = 2         # seconds; only ~/.claude/sessions is read, so a closed session loses its dot quickly
LAUNCH_WAIT = 8        # seconds a session opened from the panel is not opened again while Claude Code starts
LIFT_AGAIN = (0.1, 0.2, 0.4, 0.8)  # seconds between looks after a session's window comes to the front (stay_over)
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
                                   "waiting": o.get("waitingFor") or (o.get("status") == "waiting" or None),
                                   "cwd": o.get("cwd"), "started": o.get("startedAt")}
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


def focus_session(s, others=()):
    """Raise the window of an open session where it was (restored if minimised). When its title shows another
    listed session's name (others: those names, through shown_name), the window is a terminal holding several
    session tabs: step through them until the one titled with this session's name is showing. Any other title
    (a new session's "✳ Claude Code", or anything else) sends no key: the window is only raised."""
    hwnd = s.get("pid") and window_of(s["pid"])
    if not hwnd:
        return False
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


# ---------- back to Claude after a GLM fallback ----------
# glm-fallback.py records every session it moves to GLM in RETURN_DIR/<session>.json. Once Claude's limit has
# reset and the GLM session sits idle, the panel asks, in the fallback's own themed window (glm-fallback.py
# --ask-return), whether to continue it with Claude. On Continue it resumes the session with Claude in a new
# window and closes the GLM window only once Claude holds the session. The record is a file, so a restart of
# the panel loses nothing; the answer comes back as a file beside it for the same reason.
RETURN_DIR = os.path.join(CLAUDE, "state", "glm-return")
FALLBACK = (os.path.join(os.path.dirname(os.path.abspath(__file__)), "glm-fallback.py"),  # run from the repo
            os.path.join(CLAUDE, "hooks", "glm-fallback.py"))                             # installed
ASK_AGAIN = 30 * 60    # seconds after Not Now before the one further question
IDLE_FOR = 4           # seconds the GLM session must stay idle, so a pause between two steps of a turn is no end
START_WAIT = 90        # seconds for the new Claude window to hold the session; after that GLM is left as it is


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


def parents():
    """{pid: parent pid} for every running process."""
    return {p: v[0] for p, v in procs().items()}


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
        for _ in range(16):
            up = table.get(cur, (0, ""))[0]
            bu = born(up) if up else None
            if not t or not bu or bu > t:
                break
            sid = by_pid.get(up)
            if sid and sid != mine:  # the same session (GLM taking it over) is no child run
                r = live.get(mine) or {}
                out.setdefault(sid, []).append({"kind": "run", "what": os.path.basename((r.get("cwd") or "").rstrip("\\/")),
                                                "since": (t - FT_EPOCH) / 1e7})
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
                        "kind": "subagent", "what": m.get("description") or m.get("agentType") or "", "since": since})
                if hit[1]:
                    out.append(hit[1])
            except (OSError, ValueError, AttributeError):
                continue
    return out


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
            self.hwnd = CreateWindowEx(0x80000 | 0x20 | 0x80 | 0x8000000, self.CLASS, "Agent Panel Shadow",
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
        return logo_image(n), "Agent Panel: 5h no figure yet"
    return usage_image(pct, n), "Agent Panel: 5h %d %%, resets %s" % (pct, reset)


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
        self.idle_since = {}  # GLM sessions on their way back to Claude: since when each has been idle
        self.nc = None  # the DWM frame policy last set (see frame())
        self.front = None  # the last window that came to the front, not counting the panel's own (follow_front())
        self.wins = {}  # pid of an open session -> the window showing it (window_of), kept between scans
        self.opening = threading.Lock()  # open() calls arrive on threads of their own, at once
        self.launched = {}  # session id -> when open() started it; cleared once ~/.claude/sessions shows it
        self.repairing, self.update_note = False, None  # a failed Claude Code update: being repaired; what is left
        self.metas = {}  # subagent meta files read so far: path -> (mtime, running agent or False); see subagents()
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
        self.shadow = Shade(self.hwnd)
        self.place()
        user32.ShowWindow(self.hwnd, 5)  # SW_SHOW
        threading.Thread(target=self.follow_front, daemon=True).start()
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
                self.wins = {}  # every scan looks each window up afresh (a tab dragged into another window)
                self.find_windows()
                self.background(running())
                self.watch_update()
                self.push()
                self.check_alerts()
                self.watch_return()
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
            s["status"], s["pid"], s["waiting"] = run.get("status"), run.get("pid"), run.get("waiting")
            if s["status"]:
                s["ctx"] = read_ctx(s["id"])
                try:
                    s["mtime"] = os.stat(s["path"]).st_mtime
                except OSError:
                    pass
        self.sessions.sort(key=lambda s: s["mtime"], reverse=True)
        self.find_windows()
        self.background(live)
        self.push()
        self.check_alerts()
        self.watch_return()

    def background(self, live):
        """What each open session runs in the background, for the badge on its card: its child runs (from the
        process snapshot) and its subagents (from their meta files). Every live pass; closed sessions have none."""
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
        fg = user32.GetForegroundWindow()
        if fg in wins.values() and fg not in before:
            self.lift(fg)

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

    # ----- back to Claude after a GLM fallback (see RETURN_DIR) -----
    def watch_return(self):
        """Every live pass: each session glm-fallback.py moved to GLM, one step further on its way back."""
        try:
            names = [n for n in os.listdir(RETURN_DIR) if n.endswith(".json")]
        except OSError:
            return
        if not names:
            return
        par = parents()
        for n in names:
            path = os.path.join(RETURN_DIR, n)
            try:
                with open(path, encoding="utf-8") as f:
                    rec = json.load(f)
                rec["session"], rec["glm_cmd"], rec["resets_at"]
            except Exception:
                continue  # half written or foreign: left alone
            try:
                self.step_return(path, rec, par)
            except Exception:
                pass  # never let it stop the panel's loop; the next pass tries again

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

    def step_return(self, path, rec, par):
        sid, now = rec["session"], time.time()
        answer_file = path[:-len(".json")] + ".answer"

        def done():
            """Nothing more to do for this session: the record goes, and a question still open with it."""
            if rec.get("asker") and alive(rec["asker"]) and born(rec["asker"]) == rec.get("asker_born"):
                h = kernel32.OpenProcess(0x0001, False, int(rec["asker"]))
                if h:
                    kernel32.TerminateProcess(h, 0)
                    kernel32.CloseHandle(h)
            for p in (path, answer_file):
                try:
                    os.remove(p)
                except OSError:
                    pass
            self.idle_since.pop(sid, None)

        procs = session_procs(sid)
        if rec.get("claude_cmd"):
            # Continue was taken and Claude started: the GLM window closes only once Claude holds the session,
            # so it is never without a window, and GLM is idle, so two windows never write it at once
            if [p for p in procs if runs_under(p["pid"], rec["claude_cmd"], par)]:
                end_tree(rec["glm_cmd"], par)
                return done()
            if now - rec.get("switch_at", now) > START_WAIT or not alive(rec["claude_cmd"]):
                return done()  # Claude did not start: GLM stays as it is
            return
        glm = [p for p in procs if runs_under(p["pid"], rec["glm_cmd"], par)]
        orig = next((p for p in procs if p["pid"] == rec.get("orig_pid") and p not in glm), None)
        back = [p for p in procs if p not in glm and p is not orig]
        if (back                                                  # open in another (Claude) window
                or not alive(rec["glm_cmd"])                      # the GLM window was closed
                or (rec.get("glm_pid") and not glm)               # GLM was left (/exit) in its window
                or (orig and rec.get("orig_idle") and orig["status"] in ("busy", "waiting"))):  # typed into the old window
            return done()
        changed = False
        if glm and not rec.get("glm_pid"):
            rec["glm_pid"], changed = glm[0]["pid"], True
        if orig and orig["status"] == "idle" and not rec.get("orig_idle"):
            rec["orig_idle"], changed = True, True  # the old window's turn has ended (the hook ran inside it)
        asking = rec.get("asker") and alive(rec["asker"]) and born(rec["asker"]) == rec.get("asker_born")
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
            changed = asking = False
            if answer == "continue":
                rec["go"] = True
            elif rec.get("asked", 0) >= 2:
                return done()  # Not Now twice: it stays on GLM and is not asked again
            else:
                rec["next_ask"] = now + ASK_AGAIN
            save_record(path, rec)
        if changed:
            save_record(path, rec)
        if asking or now < rec["resets_at"] or now < rec.get("next_ask", 0):
            return
        # never cut GLM off mid-turn: only when every GLM process of the session is idle, and has been for a moment
        if not glm or any(p["status"] != "idle" for p in glm):
            self.idle_since.pop(sid, None)
            return
        if now - self.idle_since.setdefault(sid, now) < IDLE_FOR:
            return
        s = self.find(sid)
        title = short(s["title"], 60) if s else rec.get("folder") or sid
        if rec.get("go"):
            cmd = "claude --resume %s%s" % (sid, " --dangerously-skip-permissions" if rec.get("skip") else "")
            p = open_session({"id": sid, "cwd": rec["cwd"], "distro": None, "title": title}, cmd)
            rec["claude_cmd"], rec["switch_at"] = p.pid, now
        else:
            script = next((f for f in FALLBACK if os.path.exists(f)), None)
            if not script:
                return
            later = ("Not Now stays on GLM and asks once more in %d minutes." % (ASK_AGAIN // 60)
                     if rec.get("asked", 0) == 0 else "Not Now stays on GLM and does not ask again.")
            p = subprocess.Popen([sys.executable, script, "--ask-return", answer_file, rec.get("folder") or "",
                                  title, later], creationflags=CREATE_NO_WINDOW, env=clean_env())
            rec["asker"], rec["asker_born"], rec["asked"] = p.pid, born(p.pid), rec.get("asked", 0) + 1
        self.idle_since.pop(sid, None)
        save_record(path, rec)

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
            focus_session(s, self.other_names(s))

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
                              "headless": s.get("headless", False),
                              "bg": [{"kind": w["kind"], "what": w["what"], "for": lasted(w["since"])} for w in s.get("bg", [])]}
                             for s in self.sessions],
                "limits": {"five_hour": usage(lim.get("five_hour"), "%H:%M"),
                           "seven_day": usage(lim.get("seven_day"), "%a %H:%M")},
                "scanned": self.scanned, "error": self.error or self.update_note, "dbl": user32.GetDoubleClickTime()}

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
        if not s or not folder_exists(s) or s["status"]:
            return None
        with self.opening:
            if sid in running() or time.time() - self.launched.get(sid, 0) < LAUNCH_WAIT:
                return None
            self.launched[sid] = time.time()
            open_session(s)
        return ok("Opening " + short(s["title"], end="…"))

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
        self.alert.destroy()  # a hidden window left behind keeps the program running
        self.window.destroy()


if __name__ == "__main__":
    Panel().run()
