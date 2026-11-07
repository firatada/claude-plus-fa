# Agent Panel: every Claude Code session on this PC, in a narrow list over the right
# edge of the screen. Click a session to resume it in its own folder, in a new terminal.
# Tray icon: click to show or hide; Ctrl+Alt+C does the same. Read-only: it never changes
# or deletes a session.
import ctypes, ctypes.wintypes as wt, datetime, json, os, queue, re, subprocess, sys, threading, time
import tkinter as tk
from tkinter import font as tkfont

HOME = os.path.expanduser("~")
CLAUDE = os.path.join(HOME, ".claude")
STATE_DIR = os.path.join(CLAUDE, "state", "agent-panel")
CACHE_FILE = os.path.join(STATE_DIR, "cache.json")
HIDDEN_FILE = os.path.join(STATE_DIR, "hidden.json")
SESSION_FILE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.jsonl$")
RESUME = "claude --resume %s --dangerously-skip-permissions"
WIDTH = 280            # logical pixels
SCAN_EVERY = 15        # seconds
CREATE_NEW_CONSOLE = 0x10
CREATE_NO_WINDOW = 0x08000000

# Afiniti Spark tokens (mindmap/design_themes/spark_theme). Gold is for rewards only, so "working" is blue.
C = {"bg": "#f4f6fb", "panel": "#ffffff", "surface2": "#eef2fb", "line": "#e2e8f5", "text": "#0a1a40",
     "muted": "#4b5672", "blue": "#1b4df5", "busy": "#1b4df5", "idle": "#16a34a", "gone": "#b5bdd0"}

user32, kernel32, shell32 = ctypes.windll.user32, ctypes.windll.kernel32, ctypes.windll.shell32


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
    info = {"cwds": [], "title": None, "first": None}
    for line in head.split(b"\n"):
        if b'"cwd"' not in line and b'"type":"user"' not in line:
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
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
            out[o["sessionId"]] = {"status": o.get("status") or "idle", "name": o.get("name"), "pid": o["pid"]}
    return out


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
                if not hit or hit.get("key") != key:
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
                                 "pid": (run or {}).get("pid"), "name": (run or {}).get("name")})
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


# ---------- placement: over the right edge, without taking any screen space ----------
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

    def place(self, width):
        work = wt.RECT()
        user32.SystemParametersInfoW(0x30, 0, ctypes.byref(work), 0)  # SPI_GETWORKAREA
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
    """The Spark logo tile: a blue to cyan gradient at 135 degrees, white list mark."""
    from PIL import Image, ImageDraw
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


def tray(events):
    try:
        import pystray
        img = logo_image(64)
    except ImportError:
        return None
    menu = pystray.Menu(
        pystray.MenuItem("Show or hide  (Ctrl+Alt+C)", lambda: events.put("toggle"), default=True),
        pystray.MenuItem("Refresh", lambda: events.put("refresh")),
        pystray.MenuItem("Quit", lambda: events.put("quit")))
    icon = pystray.Icon("agent-panel", img, "Agent Panel", menu)
    icon.run_detached()
    return icon


# ---------- the window (Spark theme, drawn on canvases: Tk has no rounded widgets) ----------
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
    return datetime.datetime.fromtimestamp(ts).strftime("%d %b").lstrip("0")


def rounded(canvas, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


class Panel:
    def __init__(self):
        self.events = queue.Queue()
        self.cache = load_cache()
        self.sessions, self.shown_key, self.rows = [], None, []
        self.hidden, self.show_hidden = load_hidden(), False
        self.root = tk.Tk()
        self.scale = self.root.winfo_fpixels("1i") / 96.0
        self.root.overrideredirect(True)
        self.root.configure(bg=C["bg"])
        self.f_head = tkfont.Font(family="Segoe UI Semibold", size=11)
        self.f_title = tkfont.Font(family="Segoe UI Semibold", size=10)
        self.f_meta = tkfont.Font(family="Segoe UI", size=8)
        self.f_pill = tkfont.Font(family="Segoe UI Semibold", size=8)
        self.f_body = tkfont.Font(family="Segoe UI", size=9)
        self.build()
        self.root.update_idletasks()
        self.hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
        self.dock = Edge(self.hwnd)
        self.visible = True
        self.place()
        threading.Thread(target=hotkey_loop, args=(self.events,), daemon=True).start()
        self.icon = tray(self.events)
        self.refresh()
        self.root.after(200, self.poll)

    def px(self, n):
        return int(round(n * self.scale))

    # ----- layout -----
    def build(self):
        p = self.px
        self.head = tk.Canvas(self.root, height=p(52), bg=C["bg"], highlightthickness=0, bd=0)
        self.head.pack(fill="x")
        try:
            from PIL import ImageTk
            self.logo = ImageTk.PhotoImage(logo_image(p(26)))
        except ImportError:
            self.logo = None
        self.head.bind("<Configure>", lambda e: self.draw_head())

        self.search = tk.Canvas(self.root, height=p(36), bg=C["bg"], highlightthickness=0, bd=0)
        self.search.pack(fill="x")
        self.query = tk.StringVar()
        self.entry = tk.Entry(self.search, textvariable=self.query, font=self.f_meta, relief="flat", bd=0,
                              bg=C["panel"], fg=C["text"], insertbackground=C["text"])
        self.search_focus = False
        self.entry.bind("<FocusIn>", lambda e: self.set_search_focus(True))
        self.entry.bind("<FocusOut>", lambda e: self.set_search_focus(False))
        self.search.bind("<Configure>", lambda e: self.draw_search())
        self.search.bind("<Button-1>", lambda e: self.activate_search())
        self.query.trace_add("write", lambda *a: (self.draw_search(), self.render(force=True)))

        # messages show at the bottom, so the list starts right under the search box
        self.note = tk.Label(self.root, text="", font=self.f_meta, fg=C["muted"], bg=C["bg"], anchor="w")
        self.footer = tk.Label(self.root, text="", font=self.f_meta, fg=C["blue"], bg=C["bg"],
                               anchor="w", cursor="hand2")
        self.footer.pack(side="bottom", fill="x", padx=p(16), pady=(0, p(10)))
        self.footer.bind("<Button-1>", lambda e: self.toggle_hidden())
        self.note.pack(side="bottom", fill="x", padx=p(16), pady=(0, p(4)))
        tk.Frame(self.root, height=p(8), bg=C["bg"]).pack(fill="x")

        self.list = tk.Canvas(self.root, bg=C["bg"], highlightthickness=0, bd=0)
        self.list.pack(fill="both", expand=True)
        self.list.bind("<Configure>", lambda e: self.render(force=True))
        self.root.bind_all("<MouseWheel>", lambda e: self.list.yview_scroll(int(-e.delta / 120) * 3, "units"))
        self.list.configure(yscrollincrement=p(8))

    def draw_head(self):
        p, c = self.px, self.head
        c.delete("all")
        w = c.winfo_width()
        x = p(16)
        if self.logo:
            c.create_image(x, p(26), image=self.logo, anchor="w")
            x += p(26) + p(10)
        c.create_text(x, p(26), text="Agent Panel", font=self.f_head, fill=C["text"], anchor="w")
        x += self.f_head.measure("Agent Panel") + p(8)
        n = str(sum(1 for s in self.sessions if s["id"] not in self.hidden))
        pw = self.f_pill.measure(n) + p(14)
        rounded(c, x, p(17), x + pw, p(35), p(9), fill=C["surface2"], outline="")
        c.create_text(x + pw / 2, p(26), text=n, font=self.f_pill, fill=C["muted"])
        close = c.create_text(w - p(18), p(26), text="✕", font=self.f_meta, fill=C["muted"])
        c.tag_bind(close, "<Button-1>", lambda e: self.toggle())
        c.tag_bind(close, "<Enter>", lambda e: c.itemconfigure(close, fill=C["blue"]))
        c.tag_bind(close, "<Leave>", lambda e: c.itemconfigure(close, fill=C["muted"]))

    def activate_search(self):
        self.search.itemconfigure(self.entry_win, state="normal")
        self.entry.focus_force()
        self.set_search_focus(True)

    def set_search_focus(self, on):
        self.search_focus = on
        self.draw_search()

    def draw_search(self):
        p, c = self.px, self.search
        c.delete("box")
        w = c.winfo_width()
        rounded(c, p(16), p(2), w - p(16), p(34), p(10), fill=C["panel"],
                outline=C["blue"] if self.search_focus else C["line"], width=p(1.5) if self.search_focus else 1,
                tags="box")
        c.create_oval(p(28), p(12), p(37), p(21), outline=C["muted"], width=p(1.5), tags="box")
        c.create_line(p(36), p(20), p(40), p(24), fill=C["muted"], width=p(1.5), capstyle="round", tags="box")
        if not self.query.get():
            c.create_text(p(48), p(18), text="Search sessions", font=self.f_meta, fill=C["muted"],
                          anchor="w", tags="box")
        if not getattr(self, "entry_win", None):
            self.entry_win = c.create_window(p(46), p(18), window=self.entry, anchor="w")
        c.itemconfigure(self.entry_win, width=w - p(46) - p(26), height=p(22))
        c.tag_raise(self.entry_win)
        self.entry.configure(width=1)
        if not self.query.get() and not self.search_focus:
            c.itemconfigure(self.entry_win, state="hidden")
        else:
            c.itemconfigure(self.entry_win, state="normal")

    def place(self):
        x, y, w, h = self.dock.place(self.px(WIDTH))
        self.root.geometry("%dx%d+%d+%d" % (w, h, x, y))

    def toggle(self):
        """Ctrl+Alt+C and the tray: a panel covered by another window comes to the front;
        a panel already in front hides; a hidden one shows."""
        if self.visible and user32.GetForegroundWindow() != self.hwnd:
            self.raise_panel()
            return
        if self.visible:
            self.root.withdraw()
        else:
            self.root.deiconify()
            self.root.update_idletasks()
            self.place()
            self.raise_panel()
        self.visible = not self.visible

    def raise_panel(self):
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.attributes("-topmost", False)
        user32.SetForegroundWindow(self.hwnd)

    # ----- data -----
    def refresh(self):
        def work():
            try:
                found = scan(self.cache)
                save_cache(self.cache)
                self.events.put(("sessions", found))
            except Exception as e:
                self.events.put(("error", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        try:
            while True:
                ev = self.events.get_nowait()
                if ev == "toggle":
                    self.toggle()
                elif ev == "refresh":
                    self.refresh()
                elif ev == "quit":
                    self.quit()
                    return
                elif ev[0] == "sessions":
                    self.sessions = ev[1]
                    self.draw_head()
                    self.render()
                elif ev[0] == "error":
                    self.flash("Could not read sessions: " + ev[1])
        except queue.Empty:
            pass
        if kernel32.WaitForSingleObject(QUIT_EVENT, 0) == 0:
            self.quit()
            return
        now = time.time()
        if now - getattr(self, "last_scan", 0) > SCAN_EVERY:
            self.last_scan = now
            self.refresh()
        self.root.after(200, self.poll)

    def flash(self, text):
        self.note.configure(text=text)
        self.root.after(4000, lambda: self.note.configure(text=""))

    def fit(self, text, font, width):
        if font.measure(text) <= width:
            return text
        while text and font.measure(text + "…") > width:
            text = text[:-1]
        return text + "…"

    # ----- the list -----
    def render(self, force=False):
        q = self.query.get().strip().lower()
        n_hidden = sum(1 for s in self.sessions if s["id"] in self.hidden)
        self.footer.configure(text=("Hide the hidden sessions" if self.show_hidden else
                                    "Show hidden sessions (%d)" % n_hidden) if n_hidden else "")
        rows = [s for s in self.sessions
                if (self.show_hidden or s["id"] not in self.hidden)
                and (not q or q in s["title"].lower() or q in (s["folder"] or "").lower())]
        width = self.list.winfo_width()
        key = (q, width, self.show_hidden, tuple(sorted(self.hidden)),
               tuple((s["id"], s["title"], s["status"], int(s["mtime"] // 60)) for s in rows))
        if key == self.shown_key and not force:
            return
        self.shown_key = key
        c, p = self.list, self.px
        c.delete("all")
        x1, x2, y, h, gap = p(16), width - p(16), p(4), p(54), p(8)
        for i, s in enumerate(rows):
            self.card(s, i, x1, y, x2, y + h)
            y += h + gap
        if not rows:
            c.create_text(width / 2, p(40), text="No sessions match." if q else "No sessions yet.",
                          font=self.f_meta, fill=C["muted"])
        c.configure(scrollregion=(0, 0, width, y + p(8)))

    def card(self, s, i, x1, y1, x2, y2):
        c, p = self.list, self.px
        here = folder_exists(s)
        hidden = s["id"] in self.hidden
        tag = "card%d" % i
        box = rounded(c, x1, y1, x2, y2, p(14), fill=C["panel"], outline=C["line"], width=1, tags=tag)
        tx = x1 + p(14)
        if s["status"]:
            colour = C["busy"] if s["status"] == "busy" else C["idle"]
            r = p(4)
            c.create_oval(tx - r, y1 + p(18) - r, tx + r, y1 + p(18) + r, fill=colour, outline="", tags=tag)
        tx += p(14)
        room = x2 - p(14) - tx
        c.create_text(tx, y1 + p(18), text=self.fit(s["title"], self.f_title, room), font=self.f_title,
                      fill=C["text"] if here and not hidden else C["gone"], anchor="w", tags=tag)
        where = s["folder"] + ("  (WSL)" if s["distro"] or (s["cwd"] or "").startswith("\\\\wsl") else "")
        if not here:
            where += "  · folder missing"
        if hidden:
            where = "hidden  · " + where
        when = ago(s["mtime"])
        c.create_text(x2 - p(14), y1 + p(38), text=when, font=self.f_meta,
                      fill=C["muted"] if here else C["gone"], anchor="e", tags=tag)
        c.create_text(tx, y1 + p(38), text=self.fit(where, self.f_meta, room - self.f_meta.measure(when) - p(10)),
                      font=self.f_meta, fill=C["muted"] if here else C["gone"], anchor="w", tags=tag)

        def enter(e):
            c.itemconfigure(box, outline=C["blue"], width=p(1.5))
            c.configure(cursor="hand2" if here else "arrow")

        def leave(e):
            c.itemconfigure(box, outline=C["line"], width=1)
            c.configure(cursor="arrow")

        # An open session comes forward on a single click; a closed one opens only on a
        # double click, so a stray click never starts a session.
        def click(e):
            if not here:
                self.flash("That folder no longer exists.")
            elif s["status"]:
                if not focus_session(s):
                    self.flash("Already open, but its window was not found.")
            else:
                self.flash("Double-click to open it.")

        def double(e):
            if here and not s["status"]:
                open_session(s)
                self.flash("Opening " + s["title"][:40])
        c.tag_bind(tag, "<Enter>", enter)
        c.tag_bind(tag, "<Leave>", leave)
        c.tag_bind(tag, "<Button-1>", click)
        c.tag_bind(tag, "<Double-Button-1>", double)
        c.tag_bind(tag, "<Button-3>", lambda e: self.menu(e, s))

    # ----- right-click menu -----
    def menu(self, e, s):
        m = tk.Menu(self.root, tearoff=0, font=self.f_body, bg=C["panel"], fg=C["text"],
                    activebackground=C["surface2"], activeforeground=C["text"], bd=0)
        if s["id"] in self.hidden:
            m.add_command(label="Unhide", command=lambda: self.set_hidden(s, False))
        else:
            m.add_command(label="Hide", command=lambda: self.set_hidden(s, True))
        if s["status"]:
            m.add_command(label="Close", command=lambda: self.close(s))
        m.add_separator()
        m.add_command(label="Delete", foreground="#e11d48", activeforeground="#e11d48",
                      command=lambda: self.delete(s))
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def set_hidden(self, s, on):
        (self.hidden.add if on else self.hidden.discard)(s["id"])
        save_hidden(self.hidden)
        if not any(x["id"] in self.hidden for x in self.sessions):
            self.show_hidden = False
        self.draw_head()
        self.render(force=True)

    def toggle_hidden(self):
        self.show_hidden = not self.show_hidden
        self.render(force=True)

    def close(self, s):
        if not self.confirm("Close this session?",
                            "Its window closes. Anything Claude is doing in it right now stops; "
                            "the conversation so far is kept and can be resumed.", "Close"):
            return
        if close_session(s):
            self.flash("Closed " + s["title"][:40])
            self.root.after(1500, self.refresh)
        else:
            self.flash("Could not close it.")

    def delete(self, s):
        if s["status"]:
            self.flash("Close the session first, then delete it.")
            return
        if s["distro"]:
            self.flash("A session stored inside WSL cannot go to the Recycle Bin.")
            return
        if not self.confirm("Delete this session?",
                            "This session will be deleted: its conversation is moved to the Recycle Bin. "
                            "Files in the project folder (CLAUDE.md, memory.md and the rest) are not touched.",
                            "Delete", danger=True):
            return
        if not self.confirm("Are you sure?",
                            "\"%s\" will no longer be in this list and cannot be resumed, "
                            "unless you restore it from the Recycle Bin." % s["title"][:60],
                            "Yes, delete", danger=True):
            return
        if recycle(session_files(s)) and not os.path.exists(s["path"]):
            self.hidden.discard(s["id"])
            save_hidden(self.hidden)
            self.sessions = [x for x in self.sessions if x["id"] != s["id"]]
            self.draw_head()
            self.render(force=True)
            self.flash("Moved to the Recycle Bin.")
        else:
            self.flash("Could not delete it.")

    def button(self, parent, text, colour, fg, command):
        p = self.px
        w = self.f_title.measure(text) + p(28)
        b = tk.Canvas(parent, width=w, height=p(34), bg=C["panel"], highlightthickness=0, bd=0, cursor="hand2")
        rounded(b, 1, 1, w - 1, p(34) - 1, p(10), fill=colour, outline=C["line"] if colour == C["panel"] else colour)
        b.create_text(w / 2, p(17), text=text, font=self.f_title, fill=fg)
        b.bind("<Button-1>", lambda e: command())
        return b

    def confirm(self, title, text, ok_label, danger=False):
        """A Spark-styled yes/no dialog in the middle of the screen. Cancel is the default."""
        p = self.px
        top = tk.Toplevel(self.root)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.configure(bg=C["line"])
        box = tk.Frame(top, bg=C["panel"])
        box.pack(padx=1, pady=1)
        tk.Label(box, text=title, font=self.f_head, fg=C["text"], bg=C["panel"], anchor="w").pack(
            fill="x", padx=p(22), pady=(p(20), p(6)))
        tk.Label(box, text=text, font=self.f_body, fg=C["muted"], bg=C["panel"], justify="left",
                 wraplength=p(320), anchor="w").pack(fill="x", padx=p(22), pady=(0, p(18)))
        row = tk.Frame(box, bg=C["panel"])
        row.pack(fill="x", padx=p(22), pady=(0, p(20)))
        result = {"ok": False}

        def done(ok):
            result["ok"] = ok
            top.destroy()
        self.button(row, ok_label, "#e11d48" if danger else C["blue"], "#ffffff", lambda: done(True)).pack(side="right")
        self.button(row, "Cancel", C["panel"], C["text"], lambda: done(False)).pack(side="right", padx=(0, p(8)))
        top.bind("<Escape>", lambda e: done(False))
        top.update_idletasks()
        w, h = top.winfo_reqwidth(), top.winfo_reqheight()
        top.geometry("+%d+%d" % ((top.winfo_screenwidth() - w) // 2, (top.winfo_screenheight() - h) // 3))
        top.grab_set()
        top.focus_force()
        self.root.wait_window(top)
        return result["ok"]

    def quit(self):
        self.dock.release()
        if self.icon:
            self.icon.stop()
        self.root.destroy()


if __name__ == "__main__":
    p = Panel()
    try:
        p.root.mainloop()
    finally:
        p.dock.release()
