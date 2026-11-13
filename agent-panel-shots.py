# Screenshots for the Agent Panel (loaded by agent-panel.pyw; no process of its own).
# Every screenshot the user takes (Win+Shift+S / Snipping Tool, PrintScreen) is saved at once to
# <Desktop>\screenshots\YYYY-MM-DD_HHMM.png. When the user pastes it into a Claude Code session, the
# file moves to <that session's folder>\screenshots\YYYY-MM-DD_HHMM_<session-title>.png and that folder
# gets screenshots/ in its .gitignore (and .assetsignore, when it has one). Pasted into a second session
# in another folder, it is copied there. Nothing is deleted; nothing is added to Claude's input; the
# clipboard is only read, never written.
# Off: "screenshots": false in ~/.claude/claude-plus.local.json.
import base64, ctypes, ctypes.wintypes as wt, datetime, io, json, os, queue, re, shutil, threading, time, unicodedata
from PIL import Image, ImageChops, ImageGrab

HOME = os.path.expanduser("~")
CONFIG = os.path.join(HOME, ".claude", "claude-plus.local.json")
STATE = os.path.join(HOME, ".claude", "state", "agent-panel", "screenshots.json")
FOLDER = "screenshots"
# Programs that put a screenshot on the clipboard. Windows 10's Win+Shift+S runs ScreenClippingHost, which
# hands the picture to the clipboard broker (the Clipboard User Service in an svchost), so the broker owns it.
SNIP_TOOLS = {"screenclippinghost.exe", "snippingtool.exe", "screensketch.exe"}
BROKER = "svchost.exe"
CF_BITMAP, CF_DIB, CF_PALETTE, CF_DIBV5 = 2, 8, 9, 17
# formats Windows' clipboard history adds by itself; they say nothing about where an image came from
QUIET = {"ExcludeClipboardContentFromMonitorProcessing", "CanIncludeInClipboardHistory", "CanUploadToCloudClipboard"}
HOLD = 3.0             # seconds a clipboard capture waits for the same picture as a file in Windows' Screenshots
SNIP_RECENT = 5.0      # seconds after a snipping tool was seen running that the broker's images still count:
                       # it sets the clipboard twice, the second time (0.5 s later) after ScreenClippingHost ended
SAME = 1.0             # mean channel difference (0..255) at 24 x 24 below which two pictures are the same capture
MATCH = 6.0            # ... below which a pasted image (maybe shrunk, maybe JPEG) is taken for a saved screenshot;
                       # measured on a real screen: the same picture shrunk to 0.3 and saved as JPEG q20 or palette
                       # PNG <= 3.0; other regions 15 and up; the same region 20 px further over 3.0 too, so near
                       # twins are told apart by exact pixels (an unshrunk PNG) or else by time (CLOSE)
CLOSE = 1.5            # matches this close to the best one are told apart by time (the latest before the paste)
KEEP = 300             # screenshots remembered for matching
TICK = 1.0             # seconds between looks at Windows' Screenshots folder and the open transcripts
WM_CLIPBOARDUPDATE, WM_DESTROY, WM_CLOSE = 0x031D, 0x0002, 0x0010

user32, kernel32, shell32 = ctypes.windll.user32, ctypes.windll.kernel32, ctypes.windll.shell32
user32.GetClipboardOwner.restype = wt.HWND
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                ("szExeFile", ctypes.c_wchar * 260)]


# ---------- where things are ----------
def enabled():
    """On unless claude-plus.local.json says "screenshots": false."""
    try:
        with open(CONFIG, encoding="utf-8") as f:
            v = json.load(f)
        return not (isinstance(v, dict) and v.get("screenshots") is False)
    except (OSError, ValueError):
        return True


def desktop():
    """The user's real Desktop; it may be redirected (to OneDrive, say), so the shell is asked."""
    buf = ctypes.create_unicode_buffer(260)
    if shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0:  # CSIDL_DESKTOPDIRECTORY
        return buf.value
    return os.path.join(HOME, "Desktop")


class GUID(ctypes.Structure):
    _fields_ = [("a", wt.DWORD), ("b", wt.WORD), ("c", wt.WORD), ("d", ctypes.c_ubyte * 8)]


def windows_shot_dirs():
    """Where Windows itself writes a PrintScreen as a file: the Screenshots known folder (Win+PrintScreen) and
    OneDrive's Pictures\\Screenshots (OneDrive's "save screenshots" option). Usually the same folder."""
    dirs = []
    g = GUID(0xB7BEDE81, 0xDF94, 0x4682, (ctypes.c_ubyte * 8)(0xA7, 0xD8, 0x57, 0xA5, 0x26, 0x20, 0xB8, 0x6F))
    p = ctypes.c_wchar_p()
    if shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(p)) == 0:
        dirs.append(p.value)
        ctypes.windll.ole32.CoTaskMemFree(p)
    for var in ("OneDrive", "OneDriveConsumer"):
        if os.environ.get(var):
            dirs.append(os.path.join(os.environ[var], "Pictures", "Screenshots"))
    out = []
    for d in dirs:
        if os.path.normcase(d) not in map(os.path.normcase, out):
            out.append(d)
    return out


# ---------- telling a screenshot from any other copied image ----------
def exe_of(pid):
    h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    buf, n = ctypes.create_unicode_buffer(1024), wt.DWORD(1024)
    ok = kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
    kernel32.CloseHandle(h)
    return buf.value if ok else ""


def running_names():
    names = set()
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if snap in (None, wt.HANDLE(-1).value):
        return names
    e = PROCESSENTRY32W()
    e.dwSize = ctypes.sizeof(e)
    ok = kernel32.Process32FirstW(snap, ctypes.byref(e))
    while ok:
        names.add(e.szExeFile.lower())
        ok = kernel32.Process32NextW(snap, ctypes.byref(e))
    kernel32.CloseHandle(snap)
    return names


def format_names():
    """What the clipboard holds now, read without opening it (so no other program is kept waiting)."""
    arr, n = (wt.UINT * 128)(), wt.UINT()
    if not user32.GetUpdatedClipboardFormats(arr, 128, ctypes.byref(n)):
        return []
    out = []
    for f in arr[:n.value]:
        buf = ctypes.create_unicode_buffer(256)
        out.append(buf.value if user32.GetClipboardFormatNameW(f, buf, 256) else f)
    return out


def clip_event():
    """Who put what on the clipboard, read the moment it changed: a snipping tool may end soon after."""
    owner = user32.GetClipboardOwner()
    pid = wt.DWORD()
    if owner:
        user32.GetWindowThreadProcessId(owner, ctypes.byref(pid))
    exe = exe_of(pid.value) if pid.value else ""
    return {"at": time.time(), "seq": user32.GetClipboardSequenceNumber(), "owner": bool(owner),
            "exe": os.path.basename(exe).lower(), "formats": format_names(), "running": running_names()}


def is_screenshot(ev, snip_at=0.0):
    """A screenshot, not an image copied from a browser, an editor or a file:
    - owned by a snipping tool;
    - owned by the clipboard broker (how Windows 10's Win+Shift+S delivers) while a snipping tool runs or
      ran a moment ago;
    - owned by no window and holding nothing but a bitmap: PrintScreen, which Windows itself puts there."""
    f = [x for x in ev["formats"] if x not in QUIET]
    image = any(x in (CF_BITMAP, CF_DIB, CF_DIBV5, "PNG") for x in f)
    if not image:
        return False
    if ev["exe"] in SNIP_TOOLS:
        return True
    if ev["exe"] == BROKER and (ev["running"] & SNIP_TOOLS or ev["at"] - snip_at < SNIP_RECENT):
        return True
    return not ev["owner"] and set(f) <= {CF_BITMAP, CF_DIB, CF_DIBV5, CF_PALETTE}


# ---------- comparing pictures ----------
def sig(img):
    """24 x 24 colour thumbnail: survives Claude Code's shrinking and JPEG or palette re-encoding. Colour, not
    grey: two pictures of different hues but equal brightness have the same grey thumbnail."""
    if img.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        bg.paste(rgba, mask=rgba.split()[3])
        img = bg
    return img.convert("RGB").resize((24, 24), Image.BOX).tobytes()


def diff(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / float(len(a))


def same_shape(w1, h1, w2, h2):
    """Claude Code only shrinks, keeping the shape (to a pixel's rounding)."""
    return w2 <= w1 + 1 and h2 <= h1 + 1 and abs(w1 * h2 - w2 * h1) <= max(w1, h1, w2, h2) * 1.5


def identical(path, img):
    try:
        with Image.open(path) as a:
            return a.size == img.size and ImageChops.difference(a.convert("RGB"), img.convert("RGB")).getbbox() is None
    except Exception:
        return False


# ---------- names ----------
def slugify(title):
    t = (title or "").replace("ı", "i").replace("İ", "I")
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("ascii").lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    if len(t) > 40:
        t = t[:40].rsplit("-", 1)[0] if "-" in t[:40] else t[:40]
    return t or "session"


def free_name(folder, stem):
    p = os.path.join(folder, stem + ".png")
    n = 2
    while os.path.exists(p):
        p = os.path.join(folder, "%s_%d.png" % (stem, n))
        n += 1
    return p


def stamp(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d_%H%M")


def paste_time(e):
    try:
        return datetime.datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).timestamp()
    except Exception:
        return time.time()


# ---------- keeping screenshots out of git and published sites ----------
IGNORED = {"screenshots", "screenshots/", "/screenshots", "/screenshots/", "screenshots/*", "/screenshots/*",
           "screenshots/**", "/screenshots/**"}


def add_line(path, create=False):
    """Append screenshots/ to an ignore file unless it already ignores the folder. Returns True if written."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        if not create:
            return False
        raw = None
    except OSError:
        return False
    if raw is not None:
        lines = raw.decode("utf-8", "replace").splitlines()
        if any(l.strip() in IGNORED for l in lines):
            return False
    nl = b"\r\n" if raw and b"\r\n" in raw else b"\n"
    add = (nl if raw and not raw.endswith(b"\n") else b"") + b"screenshots/" + nl
    try:
        with open(path, "ab") as f:
            f.write(add)
        return True
    except OSError:
        return False


def in_git(folder):
    d = os.path.abspath(folder)
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return True
        up = os.path.dirname(d)
        if up == d:
            return False
        d = up


def keep_private(project):
    """The folder's .gitignore (made only when the folder is in a git repo without one) and its .assetsignore
    (only when it has one: a site that publishes its root, as Cloudflare's static assets do)."""
    gi = os.path.join(project, ".gitignore")
    add_line(gi, create=not os.path.exists(gi) and in_git(project))
    add_line(os.path.join(project, ".assetsignore"))


# ---------- the work ----------
class Shots:
    """Owned by the Agent Panel: start() runs a clipboard listener and a worker on threads of the panel's
    process; see() hands it the open sessions on every live pass. Arguments exist for the test harness."""

    def __init__(self, state=STATE, desk=None, shot_dirs=None, on=enabled, log=None):
        self.state, self.on, self.log = state, on, log or (lambda *a: None)
        self.desk = os.path.join(desk or desktop(), FOLDER)
        self.shot_dirs = windows_shot_dirs() if shot_dirs is None else shot_dirs
        self.events = queue.Queue()
        self.lock = threading.Lock()
        self.open = []         # open sessions: {"id", "path", "home", "title"}
        self.offsets = {}      # transcript path -> bytes already read
        self.pending = []      # clipboard captures waiting HOLD for a matching file
        self.files = {}        # Windows' screenshot file -> (size, seen at) while it is being written
        self.known = None      # files in Windows' Screenshots folders when the panel started (left alone)
        self.started = time.time()
        self.index = self.load()
        self.hwnd = None
        self.snip_at = 0.0     # when a snipping tool was last seen running at a clipboard change

    # ----- state -----
    def load(self):
        try:
            with open(self.state, encoding="utf-8") as f:
                v = json.load(f)
            for e in v:
                e["sig"] = base64.b64decode(e["sig"])
            return v
        except Exception:
            return []

    def save(self):
        self.index = self.index[-KEEP:]
        try:
            os.makedirs(os.path.dirname(self.state), exist_ok=True)
            tmp = self.state + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump([dict(e, sig=base64.b64encode(e["sig"]).decode("ascii")) for e in self.index], f)
            os.replace(tmp, self.state)
        except OSError:
            pass

    # ----- threads -----
    def start(self):
        threading.Thread(target=self.listen, daemon=True).start()
        threading.Thread(target=self.work, daemon=True).start()
        return self

    def stop(self):
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

    def see(self, sessions):
        with self.lock:
            self.open = list(sessions)

    def listen(self):
        """A message-only window told of every clipboard change (AddClipboardFormatListener); no polling."""
        def proc(h, msg, wp, lp):
            if msg == WM_CLIPBOARDUPDATE:
                try:
                    self.events.put(clip_event())
                except Exception:
                    pass
                return 0
            if msg == WM_CLOSE:
                user32.RemoveClipboardFormatListener(h)
                user32.DestroyWindow(h)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(h, msg, wp, lp)
        self._proc = WNDPROC(proc)  # kept alive as long as the window
        wc = WNDCLASSW(lpfnWndProc=self._proc, hInstance=kernel32.GetModuleHandleW(None),
                       lpszClassName="claude-plus-fa screenshots")
        user32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(0, wc.lpszClassName, None, 0, 0, 0, 0, 0, wt.HWND(-3), None,
                                           wc.hInstance, None)  # HWND_MESSAGE
        if not self.hwnd or not user32.AddClipboardFormatListener(self.hwnd):
            return
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def work(self):
        while True:
            try:
                ev = self.events.get(timeout=TICK)
            except queue.Empty:
                ev = None
            try:
                self.step(ev)
            except Exception as e:
                self.log("error", repr(e))

    def step(self, ev=None):
        if not self.on():
            self.pending, self.offsets, self.known = [], {}, None  # turned back on: only what comes after
            return
        if ev:
            self.clipboard(ev)
        self.windows_files()
        self.flush()
        self.pastes()

    # ----- captures -----
    def clipboard(self, ev):
        if ev["running"] & SNIP_TOOLS:
            self.snip_at = ev["at"]
        if not is_screenshot(ev, self.snip_at):
            self.log("ignored", ev["exe"] or "(no owner)", [str(f) for f in ev["formats"]])
            return
        img = None
        for _ in range(5):  # another program may hold the clipboard open for a moment
            try:
                img = ImageGrab.grabclipboard()
                break
            except Exception:
                time.sleep(0.1)
        if user32.GetClipboardSequenceNumber() != ev["seq"] or not isinstance(img, Image.Image):
            return  # it changed again meanwhile, or held no picture after all
        img.load()
        self.pending.append({"img": img, "at": ev["at"], "sig": sig(img), "size": img.size})
        self.log("captured", ev["exe"] or "(no owner)", img.size)

    def windows_files(self):
        """PNGs Windows writes on (Win+)PrintScreen, moved to the Desktop folder once they are written whole."""
        now, seen = time.time(), set()
        for d in self.shot_dirs:
            try:
                for n in os.listdir(d):
                    if n.lower().endswith(".png"):
                        seen.add(os.path.join(d, n))
            except OSError:
                pass
        if self.known is None:
            self.known = seen  # what was there before: not ours to move
            return
        for p in seen - self.known:
            try:
                st = os.stat(p)
            except OSError:
                continue
            last = self.files.get(p)
            if not last or last[0] != st.st_size or st.st_size == 0:
                self.files[p] = (st.st_size, now)
                continue
            try:
                with Image.open(p) as im:
                    im.load()
                    img = im.copy()
            except Exception:
                continue  # still being written
            self.files.pop(p, None)
            self.known.add(p)
            s = sig(img)
            # the same capture on the clipboard is dropped; the file is the one kept
            self.pending = [c for c in self.pending if not (c["size"] == img.size and diff(c["sig"], s) < SAME
                                                            and abs(c["at"] - st.st_mtime) < 30)]
            dup = self.recent(img.size, s, st.st_mtime)
            if dup and dup.get("file") and identical(dup["file"], img):
                os.replace(p, dup["file"])  # written from the clipboard before the file came: the very same pixels
                self.log("file replaced clipboard copy", dup["file"])
                continue
            os.makedirs(self.desk, exist_ok=True)
            dest = free_name(self.desk, stamp(st.st_mtime))
            try:
                shutil.move(p, dest)
            except OSError:
                self.known.discard(p)
                continue
            self.remember(dest, st.st_mtime, s, img.size)
            self.log("moved windows file", dest)

    def recent(self, size, s, at, within=30):
        for e in reversed(self.index):
            if tuple(e["size"]) == tuple(size) and abs(e["at"] - at) < within and diff(e["sig"], s) < SAME:
                return e
        return None

    def flush(self):
        now, keep = time.time(), []
        for c in self.pending:
            if now - c["at"] < HOLD:
                keep.append(c)
                continue
            if self.recent(c["size"], c["sig"], c["at"], within=600):
                self.log("same picture again, not saved")  # set again (clipboard history, a second copy)
                continue
            os.makedirs(self.desk, exist_ok=True)
            dest = free_name(self.desk, stamp(c["at"]))
            c["img"].save(dest, "PNG")
            self.remember(dest, c["at"], c["sig"], c["size"])
            self.log("saved", dest)
        self.pending = keep

    def remember(self, path, at, s, size):
        self.index.append({"file": path, "at": at, "sig": s, "size": list(size), "places": []})
        self.save()

    # ----- pastes -----
    def pastes(self):
        with self.lock:
            sessions = list(self.open)
        for s in sessions:
            path = s["path"]
            try:
                size = os.path.getsize(path)
            except OSError:
                size = None
            if path not in self.offsets:
                # first sight: only what is pasted from now on, unless the transcript is younger than the panel
                young = size is None or os.path.getctime(path) >= self.started
                self.offsets[path] = 0 if young else size
            if size is None:
                continue
            off = self.offsets[path]
            if size < off:
                self.offsets[path] = size
                continue
            if size == off:
                continue
            with open(path, "rb") as f:
                f.seek(off)
                chunk = f.read(size - off)
            end = chunk.rfind(b"\n")
            if end < 0:
                continue  # a line still being written
            self.offsets[path] = off + end + 1
            for line in chunk[:end].split(b"\n"):
                if b'"imagePasteIds"' in line:  # Claude Code marks an entry holding pasted images so
                    try:
                        e = json.loads(line)
                        if e.get("type") == "user":
                            self.pasted(s, e)
                    except Exception as e:
                        self.log("paste error", repr(e))

    def pasted(self, s, entry):
        content = (entry.get("message") or {}).get("content")
        if not isinstance(content, list) or not s.get("home"):
            return
        when = paste_time(entry)
        for b in content:
            src = b.get("source") if isinstance(b, dict) and b.get("type") == "image" else None
            if not src or src.get("type") != "base64":
                continue
            img = Image.open(io.BytesIO(base64.b64decode(src["data"])))
            img.load()
            e = self.match(img, when)
            if e:
                self.place(e, s)
            else:
                self.log("pasted image is no saved screenshot", img.size)

    def where(self, e):
        """Where the screenshot is now: the Desktop file, else a project copy that still exists."""
        if e.get("file") and os.path.exists(e["file"]):
            return e["file"]
        for p in e["places"]:
            if os.path.exists(p):
                return p
        return None

    def match(self, img, when):
        s, (w, h) = sig(img), img.size
        hits = []
        for e in self.index:
            ew, eh = e["size"]
            if not same_shape(ew, eh, w, h):
                continue
            d = diff(e["sig"], s)
            if d < MATCH and self.where(e):
                hits.append((d, e))
        if not hits:
            return None
        exact = [e for d, e in hits if tuple(e["size"]) == (w, h) and identical(self.where(e), img)]
        if exact:
            hits = [(0.0, e) for e in exact]
        best = min(d for d, _ in hits)
        near = [e for d, e in hits if d <= best + CLOSE]
        before = [e for e in near if e["at"] <= when + 5]
        return max(before or near, key=lambda e: e["at"])

    def place(self, e, s):
        home = s["home"]
        folder = os.path.join(home, FOLDER)
        for p in e["places"]:
            if os.path.normcase(os.path.dirname(p)) == os.path.normcase(folder) and os.path.exists(p):
                self.log("already in", p)
                return
        src = self.where(e)
        if not src:
            return
        os.makedirs(folder, exist_ok=True)
        dest = free_name(folder, "%s_%s" % (stamp(e["at"]), slugify(s.get("title"))))
        if e.get("file") and src == e["file"]:
            shutil.move(src, dest)  # first paste: it leaves the Desktop
            e["file"] = None
            self.log("moved", dest)
        else:
            shutil.copy2(src, dest)  # pasted again elsewhere: a copy
            self.log("copied", dest)
        e["places"].append(dest)
        self.save()
        keep_private(home)
