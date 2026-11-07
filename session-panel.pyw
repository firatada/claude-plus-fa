# Session panel: every Claude Code session on this PC, in a narrow list docked to the right
# edge of the screen. Click a session to resume it in its own folder, in a new terminal.
# Tray icon: click to show or hide; Ctrl+Alt+C does the same. Read-only: it never changes
# or deletes a session.
import ctypes, ctypes.wintypes as wt, datetime, json, os, queue, re, subprocess, sys, threading, time
import tkinter as tk
from tkinter import font as tkfont

HOME = os.path.expanduser("~")
CLAUDE = os.path.join(HOME, ".claude")
STATE_DIR = os.path.join(CLAUDE, "state", "session-panel")
CACHE_FILE = os.path.join(STATE_DIR, "cache.json")
SESSION_FILE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.jsonl$")
RESUME = "claude --resume %s --dangerously-skip-permissions"
WIDTH = 280            # logical pixels
SCAN_EVERY = 15        # seconds
CREATE_NEW_CONSOLE = 0x10
CREATE_NO_WINDOW = 0x08000000

C = {"bg": "#f4f6fb", "panel": "#ffffff", "line": "#e2e8f5", "text": "#0a1a40", "muted": "#697692",
     "hover": "#eef2fb", "blue": "#1b4df5", "busy": "#f5a623", "idle": "#22a06b", "gone": "#b5bdd0"}

user32, kernel32, shell32 = ctypes.windll.user32, ctypes.windll.kernel32, ctypes.windll.shell32


# ---------- one instance only ----------
kernel32.CreateMutexW(None, False, "Local\\claude-plus-fa-session-panel")
if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
    sys.exit(0)
# the installer sets this event to ask a running panel to quit and give its screen space back
QUIT_EVENT = kernel32.CreateEventW(None, True, False, "Local\\claude-plus-fa-session-panel-quit")
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
            out[o["sessionId"]] = {"status": o.get("status") or "idle", "name": o.get("name")}
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
                sessions.append({"id": sid, "cwd": cwd, "distro": distro, "title": title,
                                 "folder": os.path.basename((cwd or "").rstrip("\\/")) or folder,
                                 "mtime": st.st_mtime, "status": (run or {}).get("status")})
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


# ---------- docking: reserve the right edge like the taskbar does ----------
class APPBARDATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uCallbackMessage", wt.UINT),
                ("uEdge", wt.UINT), ("rc", wt.RECT), ("lParam", wt.LPARAM)]


class Dock:
    def __init__(self, hwnd):
        self.abd = APPBARDATA(cbSize=ctypes.sizeof(APPBARDATA), hWnd=hwnd, uCallbackMessage=0x0400 + 77, uEdge=2)
        self.on = False

    def place(self, width):
        if not self.on:
            # NEW, REMOVE, NEW: the remove makes Windows drop space still held by a panel
            # that was killed without releasing it
            shell32.SHAppBarMessage(0, ctypes.byref(self.abd))  # ABM_NEW
            shell32.SHAppBarMessage(1, ctypes.byref(self.abd))  # ABM_REMOVE
            shell32.SHAppBarMessage(0, ctypes.byref(self.abd))
            self.on = True
        work = wt.RECT()
        user32.SystemParametersInfoW(0x30, 0, ctypes.byref(work), 0)  # SPI_GETWORKAREA
        sw = user32.GetSystemMetrics(0)
        self.abd.rc = wt.RECT(sw - width, work.top, sw, work.bottom)
        shell32.SHAppBarMessage(2, ctypes.byref(self.abd))  # ABM_QUERYPOS
        self.abd.rc.left = self.abd.rc.right - width
        shell32.SHAppBarMessage(3, ctypes.byref(self.abd))  # ABM_SETPOS
        r = self.abd.rc
        return r.left, r.top, r.right - r.left, r.bottom - r.top

    def release(self):
        if self.on:
            shell32.SHAppBarMessage(1, ctypes.byref(self.abd))  # ABM_REMOVE
            self.on = False


# ---------- hotkey and tray, each on its own thread ----------
def hotkey_loop(events):
    if not user32.RegisterHotKey(None, 1, 0x0002 | 0x0001 | 0x4000, ord("C")):  # Ctrl+Alt, no repeat
        return
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == 0x0312:  # WM_HOTKEY
            events.put("toggle")


def tray(events):
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 62, 62), radius=14, fill=C["blue"])
    for y in (18, 32, 46):
        d.rounded_rectangle((16, y - 3, 48, y + 3), radius=3, fill="white")
    menu = pystray.Menu(
        pystray.MenuItem("Show or hide  (Ctrl+Alt+C)", lambda: events.put("toggle"), default=True),
        pystray.MenuItem("Refresh", lambda: events.put("refresh")),
        pystray.MenuItem("Quit", lambda: events.put("quit")))
    icon = pystray.Icon("claude-sessions", img, "Claude sessions", menu)
    icon.run_detached()
    return icon


# ---------- the window ----------
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


class Panel:
    def __init__(self):
        self.events = queue.Queue()
        self.cache = load_cache()
        self.sessions, self.shown_key = [], None
        self.root = tk.Tk()
        self.scale = self.root.winfo_fpixels("1i") / 96.0
        self.root.overrideredirect(True)
        self.root.configure(bg=C["bg"])
        self.f_title = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        self.f_small = tkfont.Font(family="Segoe UI", size=8)
        self.f_head = tkfont.Font(family="Segoe UI", size=9, weight="bold")
        self.build()
        self.root.update_idletasks()
        self.dock = Dock(user32.GetParent(self.root.winfo_id()) or self.root.winfo_id())
        self.visible = True
        self.place()
        threading.Thread(target=hotkey_loop, args=(self.events,), daemon=True).start()
        self.icon = tray(self.events)
        # Windows sometimes keeps space held by a panel that was killed; docking again a
        # moment later makes it recount and drop it
        self.root.after(2500, self.redock)
        self.refresh()
        self.root.after(200, self.poll)

    def px(self, n):
        return int(round(n * self.scale))

    def build(self):
        head = tk.Frame(self.root, bg=C["bg"])
        head.pack(fill="x", padx=self.px(12), pady=(self.px(12), self.px(6)))
        self.count = tk.Label(head, text="Sessions", font=self.f_head, fg=C["text"], bg=C["bg"])
        self.count.pack(side="left")
        x = tk.Label(head, text="✕", font=self.f_small, fg=C["muted"], bg=C["bg"], cursor="hand2")
        x.pack(side="right")
        x.bind("<Button-1>", lambda e: self.toggle())
        self.query = tk.StringVar()
        entry = tk.Entry(self.root, textvariable=self.query, font=self.f_small, relief="flat",
                         bg=C["panel"], fg=C["text"], insertbackground=C["text"],
                         highlightthickness=1, highlightbackground=C["line"], highlightcolor=C["blue"])
        entry.pack(fill="x", padx=self.px(12), pady=(0, self.px(8)), ipady=self.px(4))
        hint = tk.Label(entry, text="Search", font=self.f_small, fg=C["muted"], bg=C["panel"])
        hint.place(x=self.px(4), rely=0.5, anchor="w")
        hint.bind("<Button-1>", lambda e: entry.focus_set())

        def changed(*a):
            if self.query.get():
                hint.place_forget()
            else:
                hint.place(x=self.px(4), rely=0.5, anchor="w")
            self.render(force=True)
        self.query.trace_add("write", changed)
        self.note = tk.Label(self.root, text="", font=self.f_small, fg=C["muted"], bg=C["bg"], anchor="w")
        self.note.pack(fill="x", padx=self.px(12))
        body = tk.Frame(self.root, bg=C["bg"])
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, bg=C["bg"], highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=C["bg"])
        self.win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.win, width=e.width))
        self.root.bind_all("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-e.delta / 120) * 3, "units"))

    def place(self):
        x, y, w, h = self.dock.place(self.px(WIDTH))
        self.root.geometry("%dx%d+%d+%d" % (w, h, x, y))

    def redock(self):
        if self.visible:
            self.dock.release()
            self.place()

    def toggle(self):
        if self.visible:
            self.dock.release()
            self.root.withdraw()
        else:
            self.root.deiconify()
            self.root.update_idletasks()
            self.place()
        self.visible = not self.visible

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

    def render(self, force=False):
        q = self.query.get().strip().lower()
        rows = [s for s in self.sessions
                if not q or q in s["title"].lower() or q in (s["folder"] or "").lower()]
        key = (q, tuple((s["id"], s["title"], s["status"], int(s["mtime"] // 60)) for s in rows))
        if key == self.shown_key and not force:
            return
        self.shown_key = key
        self.count.configure(text="Sessions  %d" % len(self.sessions))
        for w in self.inner.winfo_children():
            w.destroy()
        width = self.px(WIDTH - 24 - 22)
        for s in rows:
            self.row(s, width)

    def row(self, s, width):
        here = folder_exists(s)
        card = tk.Frame(self.inner, bg=C["panel"], cursor="hand2" if here else "arrow",
                        highlightthickness=1, highlightbackground=C["line"])
        card.pack(fill="x", padx=self.px(12), pady=(0, self.px(6)))
        dot = tk.Canvas(card, width=self.px(10), height=self.px(10), bg=C["panel"], highlightthickness=0)
        dot.grid(row=0, column=0, rowspan=2, padx=(self.px(8), self.px(4)), sticky="n", pady=(self.px(10), 0))
        if s["status"]:
            colour = C["busy"] if s["status"] == "busy" else C["idle"]
            dot.create_oval(1, 1, self.px(9), self.px(9), fill=colour, outline=colour)
        fg = C["text"] if here else C["gone"]
        t = tk.Label(card, text=self.fit(s["title"], self.f_title, width), font=self.f_title,
                     fg=fg, bg=C["panel"], anchor="w")
        t.grid(row=0, column=1, sticky="we", padx=(0, self.px(8)), pady=(self.px(6), 0))
        where = s["folder"] + ("  (WSL)" if s["distro"] or (s["cwd"] or "").startswith("\\\\wsl") else "")
        if not here:
            where += "  · folder missing"
        meta = "%s  ·  %s" % (self.fit(where, self.f_small, width - self.px(60)), ago(s["mtime"]))
        m = tk.Label(card, text=meta, font=self.f_small, fg=C["muted"] if here else C["gone"],
                     bg=C["panel"], anchor="w")
        m.grid(row=1, column=1, sticky="we", padx=(0, self.px(8)), pady=(0, self.px(6)))
        card.grid_columnconfigure(1, weight=1)
        parts = (card, dot, t, m)

        def enter(e):
            for p in parts:
                p.configure(bg=C["hover"])

        def leave(e):
            for p in parts:
                p.configure(bg=C["panel"])

        def click(e):
            if not here:
                self.flash("That folder no longer exists.")
            elif s["status"]:
                self.flash("Already open in another window.")
            else:
                open_session(s)
                self.flash("Opening " + s["title"][:40])
        for p in parts:
            p.bind("<Enter>", enter)
            p.bind("<Leave>", leave)
            p.bind("<Button-1>", click)

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
