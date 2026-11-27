# Fast image paste for the Agent Panel's Claude Code sessions: a small process of its own, started and watched by
# agent-panel.pyw (python agent-panel-paste.py <panel pid>), ended with the panel.
# Claude Code's own image paste (Alt+V) on Windows runs PowerShell scripts, and the first one after a quiet spell
# can stall about 10 s in Defender's scan. Claude Code also takes a pasted TEXT that is the full path of an existing
# .png/.jpg/.gif/.webp file as that image, at the cursor, without PowerShell. So while the foreground window is a
# listed session's terminal showing a Claude Code tab, Ctrl+V and Alt+V are hotkeys here (RegisterHotKey: no
# keyboard hook, no other key is seen). With an image and no text on the clipboard, the picture becomes a PNG file
# (the keeper's own file when it already saved this very screenshot), its path goes on the clipboard (kept out of
# clipboard history and the cloud clipboard), Ctrl+V goes to that terminal, and once the terminal has read the path
# the previous clipboard comes back. Anything else passes the same keys through unchanged.
# A process of its own so a stalled panel can never hold the keys: it lives on with the panel's last list of
# session windows, the panel ends it (Windows then frees its hotkeys) and starts it again if it stops answering,
# and it ends when the panel ends. Off: "paste_keys": false in ~/.claude/claude-plus.local.json.
# The reply window's Send to Session uses the same machinery for a line of text: the panel brings the session's
# terminal to the front and hands the line over (WM_COPYDATA); it goes on the clipboard the same quiet way, Ctrl+V
# goes to that terminal, and the clipboard comes back once the terminal has read it. Enter is never pressed.
import ctypes, ctypes.wintypes as wt, importlib.util, json, os, re, sys, tempfile, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
WINDOWS = os.path.join(HOME, ".claude", "state", "agent-panel", "paste-windows.json")  # written by the panel
LOG = os.path.join(HOME, ".claude", "state", "agent-panel", "paste.log")
TEMP = os.path.join(tempfile.gettempdir(), "claude-plus-fa-paste")
KEEP_TEMP = 24 * 3600  # seconds a written picture stays (Claude Code reads it at the paste; the model sees the path)
MARK = "claude-plus-fa paste"  # format on every clipboard this process sets: the panel's own listeners skip them
QUIET = ("ExcludeClipboardContentFromMonitorProcessing",  # clipboard history, the cloud clipboard and well-behaved
         "CanIncludeInClipboardHistory", "CanUploadToCloudClipboard")  # clipboard managers leave it alone
CLASS = "claude-plus-fa paste"
MUTEX = "Local\\claude-plus-fa-agent-panel-paste"
CTRL_V, ALT_V = 1, 2   # hotkey ids
MODS = {CTRL_V: 0x0002, ALT_V: 0x0001}  # MOD_CONTROL, MOD_ALT (exact: Ctrl+Shift+V and the like are never taken)
VK_V, VK_CONTROL, VK_MENU = 0x56, 0x11, 0x12
REARM = 0.08           # seconds the hotkeys stay off after passing a key through, while Windows delivers it
READ_WAIT = 2.0        # seconds the terminal gets to read the path before the clipboard comes back anyway
PIECE_GAP = 0.5        # seconds between two pastes of one typing request: Claude Code takes one before the next comes
                       # (a picture's path pasted alone turns into [Image #N] there; 0.3 s was measured enough)
CHECK_EVERY = 1000     # ms: the foreground and the panel's list looked at again (a missed event, a new session)
CLAUDE_TITLE = re.compile(r"^[^\w\s]{1,2}\s+\S")  # Claude Code titles its tab "<status glyph> <name>"
CF_BITMAP, CF_DIB, CF_DIBV5, CF_UNICODETEXT, CF_ENHMETAFILE = 2, 8, 17, 13, 14
# not kept by save_clip(): GDI handles (bitmap, metafile picture, palette; CF_BITMAP comes back on its own from
# CF_DIB), owner-display and private formats (only the program that set them can read them) and OLE's own
SKIP = {CF_BITMAP, 3, 9, 0x80, 0x82, 0x83, 0x8E}
OLE = {"DataObject", "Ole Private Data"}
WM_CLOSE, WM_DESTROY, WM_TIMER, WM_HOTKEY = 0x0010, 0x0002, 0x0113, 0x0312
WM_RENDERFORMAT, WM_RENDERALLFORMATS, WM_DESTROYCLIPBOARD = 0x0305, 0x0306, 0x0307
WM_DONE = 0x8001       # WM_APP + 1: an image paste finished
WM_TYPED = 0x8002      # WM_APP + 2: the panel asks how typing request wParam went (TYPED_*)
WM_COPYDATA = 0x004A
TYPE_TEXT = 0x54595045  # COPYDATASTRUCT.dwData of a typing request: JSON {"id", "hwnd", "text"}
# answers to a typing request (WM_COPYDATA) and to WM_TYPED
TYPED_STARTED, TYPED_BUSY, TYPED_NOT_FRONT, TYPED_NOT_CLAUDE, TYPED_BAD = 1, 2, 3, 4, 5
TYPED_PENDING, TYPED_READ, TYPED_FAILED = 0, 1, 2
T_CHECK, T_REARM = 1, 2

user32 = ctypes.WinDLL("user32", use_last_error=True)  # own instances: the keeper module sets windll's prototypes
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
WINEVENTPROC = ctypes.WINFUNCTYPE(None, wt.HANDLE, wt.DWORD, wt.HWND, wt.LONG, wt.LONG, wt.DWORD, wt.DWORD)
for f, res, args in (
        ("GetForegroundWindow", wt.HWND, []), ("GetClipboardOwner", wt.HWND, []),
        ("GetOpenClipboardWindow", wt.HWND, []), ("GetClipboardData", ctypes.c_void_p, [wt.UINT]),
        ("SetClipboardData", ctypes.c_void_p, [wt.UINT, ctypes.c_void_p]), ("OpenClipboard", wt.BOOL, [wt.HWND]),
        ("EnumClipboardFormats", wt.UINT, [wt.UINT]), ("IsClipboardFormatAvailable", wt.BOOL, [wt.UINT]),
        ("GetClipboardFormatNameW", ctypes.c_int, [wt.UINT, wt.LPWSTR, ctypes.c_int]),
        ("RegisterClipboardFormatW", wt.UINT, [wt.LPCWSTR]), ("GetClipboardSequenceNumber", wt.DWORD, []),
        ("RegisterHotKey", wt.BOOL, [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]),
        ("UnregisterHotKey", wt.BOOL, [wt.HWND, ctypes.c_int]), ("GetAsyncKeyState", ctypes.c_short, [ctypes.c_int]),
        ("InternalGetWindowText", ctypes.c_int, [wt.HWND, wt.LPWSTR, ctypes.c_int]),
        ("GetWindowThreadProcessId", wt.DWORD, [wt.HWND, ctypes.POINTER(wt.DWORD)]),
        ("DefWindowProcW", ctypes.c_ssize_t, [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]),
        ("CreateWindowExW", wt.HWND, [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]),
        ("PostMessageW", wt.BOOL, [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]),
        ("SetTimer", ctypes.c_size_t, [wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p]),
        ("KillTimer", wt.BOOL, [wt.HWND, ctypes.c_size_t]), ("MapVirtualKeyW", wt.UINT, [wt.UINT, wt.UINT]),
        ("SetWinEventHook", wt.HANDLE, [wt.DWORD, wt.DWORD, wt.HMODULE, WINEVENTPROC, wt.DWORD, wt.DWORD, wt.DWORD]),
        ("UnhookWinEvent", wt.BOOL, [wt.HANDLE])):
    fn = getattr(user32, f)
    fn.restype, fn.argtypes = res, args
kernel32.GlobalAlloc.restype = kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
kernel32.GlobalLock.argtypes = kernel32.GlobalUnlock.argtypes = kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.CreateMutexW.restype = wt.HANDLE
gdi32 = ctypes.WinDLL("gdi32")
gdi32.CopyEnhMetaFileW.restype = ctypes.c_void_p
gdi32.CopyEnhMetaFileW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class COPYDATASTRUCT(ctypes.Structure):
    _fields_ = [("dwData", ctypes.c_size_t), ("cbData", wt.DWORD), ("lpData", ctypes.c_void_p)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]
    _fields_ = [("type", wt.DWORD), ("u", _U)]


# ---------- rules (no Windows state: unit-tested) ----------
def claude_title(title):
    """The tab in view is a Claude Code session: its title is "<status glyph> <name>" ("✳ Claude Code" before it
    has a name). A shell tab ("Windows PowerShell", "C:\\...", "~/src") or a title the user set is not."""
    return bool(title and CLAUDE_TITLE.match(title))


def wanted(hwnd, windows, title):
    """Ctrl+V and Alt+V are taken while this window is in front: a window the panel lists as a session's terminal,
    with a Claude Code tab in view."""
    return bool(hwnd) and hwnd in windows and claude_title(title)


def image_only(has):
    """The clipboard holds a picture and no text (has(format) -> bool, without opening the clipboard)."""
    png = user32.RegisterClipboardFormatW("PNG")
    return any(has(f) for f in (CF_DIB, CF_DIBV5, CF_BITMAP, png)) and not has(CF_UNICODETEXT)


def keys_for(combo, ctrl, alt, paste=False):
    """The key events to send, [(vk, up)]: the user's own combination again (passing it through), or, with paste,
    Ctrl+V for the terminal's paste. ctrl, alt: held right now. A modifier still held is not pressed again; an Alt
    still held for a paste is let go after Ctrl went down, so its release opens no menu."""
    want_ctrl, want_alt = paste or combo == CTRL_V, not paste and combo == ALT_V
    ev = []
    if want_ctrl and not ctrl:
        ev.append((VK_CONTROL, False))
    if want_alt and not alt:
        ev.append((VK_MENU, False))
    if paste and alt:
        ev.append((VK_MENU, True))
    ev += [(VK_V, False), (VK_V, True)]
    if want_alt and not alt:
        ev.append((VK_MENU, True))
    if want_ctrl and not ctrl:
        ev.append((VK_CONTROL, True))
    return ev


def clean_temp(folder=TEMP, now=None, keep=KEEP_TEMP):
    """Pictures this process wrote more than keep seconds ago are deleted; nothing else in the folder."""
    now, gone = now or time.time(), 0
    try:
        names = os.listdir(folder)
    except OSError:
        return 0
    for n in names:
        p = os.path.join(folder, n)
        try:
            if n.startswith("paste-") and n.endswith(".png") and now - os.path.getmtime(p) > keep:
                os.remove(p)
                gone += 1
        except OSError:
            pass
    return gone


def write_temp(img, folder=TEMP, now=None):
    """The picture as a PNG in the temp folder, under a name no other one has; the old ones go first."""
    now = now or time.time()
    os.makedirs(folder, exist_ok=True)
    clean_temp(folder, now)
    stem = "paste-%s-%03d" % (time.strftime("%Y%m%d-%H%M%S", time.localtime(now)), int(now * 1000) % 1000)
    path, n = os.path.join(folder, stem + ".png"), 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(folder, "%s-%d.png" % (stem, n))
    img.save(path, "PNG", compress_level=1)
    return path


# ---------- Windows ----------
def window_text(hwnd):
    buf = ctypes.create_unicode_buffer(512)
    user32.InternalGetWindowText(hwnd, buf, 512)  # sends the window no message
    return buf.value


def pid_of(hwnd):
    pid = wt.DWORD()
    if hwnd:
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def held(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def send_keys(events):
    arr = (INPUT * len(events))()
    for i, (vk, up) in enumerate(events):
        arr[i].type = 1  # INPUT_KEYBOARD
        arr[i].u.ki = KEYBDINPUT(vk, user32.MapVirtualKeyW(vk, 0), 0x0002 if up else 0, 0, 0)
    user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))


def fmt_name(f):
    buf = ctypes.create_unicode_buffer(256)
    return buf.value if user32.GetClipboardFormatNameW(f, buf, 256) else None


def open_clip(hwnd, wait=1.0):
    """Opens the clipboard, waiting while another program holds it."""
    end = time.time() + wait
    while True:
        if user32.OpenClipboard(hwnd):
            return True
        if time.time() > end:
            return False
        time.sleep(0.01)


def put(f, data):
    h = kernel32.GlobalAlloc(0x0002, max(len(data), 1))  # GMEM_MOVEABLE
    p = kernel32.GlobalLock(h)
    ctypes.memmove(p, data, len(data))
    kernel32.GlobalUnlock(h)
    if not user32.SetClipboardData(f, h):  # once set, the clipboard owns the memory
        kernel32.GlobalFree(h)
        return False
    return True


def save_clip(hwnd):
    """Every format of the clipboard that can be put back, read now: ([(format, name or None, bytes or EMF handle)],
    [names of the formats that cannot]). Delayed formats are rendered by their program here."""
    if not open_clip(hwnd):
        return None, []
    kept, lost, f = [], [], 0
    try:
        while True:
            f = user32.EnumClipboardFormats(f)
            if not f:
                break
            name = fmt_name(f)
            if f in SKIP or 0x200 <= f <= 0x3FF or name in OLE:
                lost.append(name or str(f))
                continue
            h = user32.GetClipboardData(f)
            if not h:
                lost.append(name or str(f))  # only an OLE stream holds it, or its program gave nothing
                continue
            if f == CF_ENHMETAFILE:
                kept.append((f, None, gdi32.CopyEnhMetaFileW(h, None)))
                continue
            p, n = kernel32.GlobalLock(h), kernel32.GlobalSize(h)
            if not p:
                lost.append(name or str(f))
                continue
            kept.append((f, name, ctypes.string_at(p, n)))
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()
    return kept, lost


def mark_quiet():
    """The mark and the three formats that keep a clipboard out of history, the cloud and clipboard managers."""
    put(user32.RegisterClipboardFormatW(MARK), b"\1\0\0\0")
    for n in QUIET:
        put(user32.RegisterClipboardFormatW(n), b"\0\0\0\0")


# ---------- the process ----------
class Keys:
    """The hotkeys and the paste. Arguments exist for the tests: a list file and a temp folder of their own, and
    stand-ins for registering, the window title and the keeper's lookup."""

    def __init__(self, panel_pid=None, windows_file=WINDOWS, temp=TEMP, saved_file=None, log=None,
                 register=None, unregister=None, title=window_text):
        self.panel_pid, self.windows_file, self.temp = panel_pid, windows_file, temp
        self.saved_file, self.log, self.title = saved_file, log or (lambda *a: None), title
        self._register, self._unregister = register, unregister
        self.windows, self.windows_stamp = set(), None
        self.hwnd = None
        self.registered = False
        self.busy = False      # an image paste is under way: the hotkeys stay off until it ends
        self.until = 0.0       # the hotkeys stay off until then: a key passed through is still on its way
        self.closing = False
        self.last = None       # (clipboard sequence number after a restore, path): the same picture again
        self.text, self.target_pid, self.refused = "", 0, []  # the path, the terminal's process, others refused
        self.rendered, self.lost, self.emptying = threading.Event(), False, False
        self.hooks = []
        self.typed = {}  # typing request id -> TYPED_PENDING / TYPED_READ / TYPED_FAILED

    # ----- which window -----
    def session_windows(self):
        """The panel's list of session windows, read again when the file changed (kept when it cannot be read)."""
        try:
            st = os.stat(self.windows_file)
            stamp = (st.st_mtime_ns, st.st_size)
            if stamp != self.windows_stamp:
                with open(self.windows_file, encoding="utf-8") as f:
                    self.windows = {int(h) for h in json.load(f).get("windows", [])}
                self.windows_stamp = stamp
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        return self.windows

    def want(self, hwnd):
        return (not self.busy and not self.closing and time.time() >= self.until
                and wanted(hwnd, self.session_windows(), self.title(hwnd)))

    def check(self, hwnd=None):
        """Takes the keys while a session's terminal is in front, gives them back as soon as anything else is."""
        on = self.want(user32.GetForegroundWindow() if hwnd is None else hwnd)
        if on and not self.registered:
            ok = [i for i in (CTRL_V, ALT_V) if self.reg(i)]
            self.registered = bool(ok)
            if len(ok) < 2:
                self.log("hotkey taken by another program", [i for i in (CTRL_V, ALT_V) if i not in ok])
        elif not on and self.registered:
            self.unreg()
        return self.registered

    def reg(self, i):
        if self._register:
            return self._register(i)
        return user32.RegisterHotKey(self.hwnd, i, MODS[i], VK_V)  # repeats too, as the plain keys would

    def unreg(self):
        for i in (CTRL_V, ALT_V):
            if self._unregister:
                self._unregister(i)
            else:
                user32.UnregisterHotKey(self.hwnd, i)
        self.registered = False

    # ----- a key -----
    def hotkey(self, combo):
        """Ctrl+V or Alt+V in a session's terminal: a picture alone is pasted as its path (on a thread, the keys
        given back meanwhile), anything else goes through unchanged."""
        fg = user32.GetForegroundWindow()
        ctrl, alt = held(VK_CONTROL), held(VK_MENU)
        self.unreg()
        if wanted(fg, self.session_windows(), self.title(fg)) and image_only(user32.IsClipboardFormatAvailable):
            self.busy = True
            threading.Thread(target=self.paste, args=(fg, combo), daemon=True).start()
            return
        self.pass_through(combo, ctrl, alt)
        self.rearm()

    def pass_through(self, combo, ctrl, alt):
        """The same keys again, with the hotkeys off until Windows has delivered them (else they would come
        straight back as the hotkey)."""
        self.until = time.time() + REARM
        send_keys(keys_for(combo, ctrl, alt))
        self.log("passed", "ctrl+v" if combo == CTRL_V else "alt+v")

    def rearm(self):
        """On this window's thread: the hotkeys come back once a key passed through is delivered."""
        wait = self.until - time.time()
        if wait > 0:
            user32.SetTimer(self.hwnd, T_REARM, int(wait * 1000) + 10, None)
        else:
            self.check()

    def paste(self, target, combo):
        """On a thread of its own: the picture as a file, its path pasted, the clipboard put back."""
        t0, how, done = time.time(), "", "failed"
        try:
            seq = user32.GetClipboardSequenceNumber()
            path = self.last[1] if self.last and self.last[0] == seq and os.path.exists(self.last[1]) else None
            how = "again" if path else ""
            if not path:
                img = grab()
                if img is None:  # no picture after all (another program changed it meanwhile)
                    done = "passed"
                    return self.pass_through(combo, held(VK_CONTROL), held(VK_MENU))
                path = self.saved_file(img) if self.saved_file else None
                how = "keeper" if path else "temp"
                path = path or write_temp(img, self.temp)
            if user32.GetForegroundWindow() != target:
                done = "front changed"
                return
            saved, lost = save_clip(self.hwnd)
            if saved is None:
                done = "clipboard busy"
                return
            t1 = time.time()
            if not self.set_path(path, target):
                return
            send_keys(keys_for(combo, held(VK_CONTROL), held(VK_MENU), paste=True))
            read = self.rendered.wait(READ_WAIT)
            t2 = time.time()
            done = ("read" if read else "not read") + ", " + self.restore(saved)
            lost = [n for n in lost if n != str(CF_BITMAP)]  # made again from CF_DIB by Windows
            self.log("paste", how, done, "file %.0f ms, read %.0f ms" % ((t1 - t0) * 1000, (t2 - t1) * 1000),
                     "lost: %s" % (", ".join(lost) or "none"), "refused: %s" % self.refused, path)
            done = None
        except Exception as e:
            self.log("paste error", repr(e))
        finally:
            if done:
                self.log("paste", how, done)
            user32.PostMessageW(self.hwnd, WM_DONE, 0, 0)

    def request(self, data):
        """A typing request from the panel (WM_COPYDATA): the line goes into the terminal in front, at its cursor,
        through the clipboard as a picture's path does. Only into a listed session window that is the foreground
        window and shows a Claude Code tab. Returns TYPED_STARTED (the paste runs on a thread) or why not."""
        try:
            d = json.loads(data.decode("utf-8"))
            rid, target = int(d["id"]), int(d["hwnd"])
            pieces = [str(x) for x in d["pieces"]] if "pieces" in d else [str(d["text"])]
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            return TYPED_BAD
        if not pieces:
            return TYPED_BAD
        if self.busy or self.closing:
            return TYPED_BUSY
        if user32.GetForegroundWindow() != target:
            return TYPED_NOT_FRONT
        if not wanted(target, self.session_windows(), self.title(target)):
            return TYPED_NOT_CLAUDE
        self.busy = True
        self.unreg()
        self.typed = {k: v for k, v in self.typed.items() if k > rid - 20}
        self.typed[rid] = TYPED_PENDING
        threading.Thread(target=self.type_text, args=(rid, target, pieces), daemon=True).start()
        return TYPED_STARTED

    def type_text(self, rid, target, pieces):
        """On a thread of its own: the pieces pasted into the terminal in front, one paste each, in order (Send to
        Session pastes a picture's path alone where it stands, so Claude Code attaches it there), PIECE_GAP apart;
        then the clipboard put back. It stops at a piece the terminal did not read or once the terminal left the
        front."""
        done = TYPED_FAILED
        try:
            saved, lost = save_clip(self.hwnd)
            if saved is None:
                self.log("type", "clipboard busy")
                return
            n = 0
            for k, text in enumerate(pieces):
                if k:
                    time.sleep(PIECE_GAP)
                if user32.GetForegroundWindow() != target or not self.set_path(text, target):
                    self.log("type", "front changed at piece %d of %d" % (k + 1, len(pieces)))
                    break
                send_keys(keys_for(CTRL_V, held(VK_CONTROL), held(VK_MENU), paste=True))
                if not self.rendered.wait(READ_WAIT):
                    break
                n += 1
            read = n == len(pieces)
            self.log("type", ("read" if read else "not read") + ", " + self.restore(saved),
                     "%d characters in %d of %d pieces" % (sum(len(x) for x in pieces[:n]), n, len(pieces)))
            done = TYPED_READ if read else TYPED_FAILED
        except Exception as e:
            self.log("type error", repr(e))
        finally:
            self.typed[rid] = done
            user32.PostMessageW(self.hwnd, WM_DONE, 0, 0)

    def set_path(self, path, target):
        """The path as the clipboard's text, rendered only when asked for (so the moment the terminal reads it is
        known), with the mark and the quiet formats."""
        self.text, self.target_pid, self.refused = path, pid_of(target), []
        self.rendered.clear()
        if not open_clip(self.hwnd):
            return False
        try:
            self.emptying = True  # this window may own it already (a restore): that is no other program's change
            user32.EmptyClipboard()  # this window owns the clipboard now
            self.emptying, self.lost = False, False
            user32.SetClipboardData(CF_UNICODETEXT, None)  # delayed: WM_RENDERFORMAT when read
            mark_quiet()
        finally:
            self.emptying = False
            user32.CloseClipboard()
        return True

    def render(self, f):
        """WM_RENDERFORMAT: a program reads the path. Only the terminal gets it (its process has the clipboard open,
        or no window does), and its read is the signal to put the clipboard back. Any other program reading at once
        (WSLg's msrdc.exe copies every text into Linux, the quiet formats notwithstanding) gets nothing: the text
        stays unrendered, so the terminal's read still asks for it."""
        if f != CF_UNICODETEXT:
            return
        opener = pid_of(user32.GetOpenClipboardWindow())
        if opener and opener != self.target_pid:
            self.refused.append(opener)
            return
        put(CF_UNICODETEXT, (self.text + "\0").encode("utf-16-le"))
        self.rendered.set()

    def restore(self, saved):
        """The clipboard as it was, unless another program has set it meanwhile; marked, and out of history (its
        first copy is there already)."""
        if not open_clip(self.hwnd, wait=1.5):
            return "not restored: clipboard busy"
        try:
            if self.lost or user32.GetClipboardOwner() != self.hwnd:
                return "not restored: changed meanwhile"
            self.emptying = True
            user32.EmptyClipboard()
            self.emptying = False
            for f, name, data in saved:
                if f == CF_ENHMETAFILE:
                    user32.SetClipboardData(f, data)
                else:
                    put(user32.RegisterClipboardFormatW(name) if name else f, data)
            mark_quiet()
        finally:
            self.emptying = False
            user32.CloseClipboard()
        self.last = (user32.GetClipboardSequenceNumber(), self.text)
        return "restored"

    # ----- the window, the hooks, the loop -----
    def proc(self, h, msg, wp, lp):
        try:
            if msg == WM_HOTKEY:
                self.hotkey(wp)
                return 0
            if msg == WM_COPYDATA:
                cds = ctypes.cast(lp, ctypes.POINTER(COPYDATASTRUCT)).contents
                if cds.dwData != TYPE_TEXT:
                    return 0
                return self.request(ctypes.string_at(cds.lpData, cds.cbData))
            if msg == WM_TYPED:
                return self.typed.get(int(wp), TYPED_FAILED)
            if msg == WM_RENDERFORMAT:
                self.render(wp)
                return 0
            if msg == WM_RENDERALLFORMATS:  # closing while the path is still delayed
                if user32.OpenClipboard(h):
                    if user32.GetClipboardOwner() == h:
                        put(CF_UNICODETEXT, (self.text + "\0").encode("utf-16-le"))
                    user32.CloseClipboard()
                return 0
            if msg == WM_DESTROYCLIPBOARD:
                if not self.emptying:
                    self.lost = True  # another program emptied it: never put the old one back over theirs
                return 0
            if msg == WM_TIMER:
                if wp == T_REARM:
                    user32.KillTimer(h, T_REARM)
                self.check()
                return 0
            if msg == WM_DONE:
                self.busy = False
                if self.closing:
                    user32.DestroyWindow(h)
                else:
                    self.rearm()
                return 0
            if msg == WM_CLOSE:
                self.closing = True
                self.unreg()
                if not self.busy:
                    user32.DestroyWindow(h)
                return 0
            if msg == WM_DESTROY:
                for hk in self.hooks:
                    user32.UnhookWinEvent(hk)
                user32.PostQuitMessage(0)
                return 0
        except Exception as e:
            self.log("error", repr(e))
        return user32.DefWindowProcW(h, msg, wp, lp)

    def run(self):
        self._proc = WNDPROC(self.proc)
        wc = WNDCLASSW(lpfnWndProc=self._proc, hInstance=kernel32.GetModuleHandleW(None), lpszClassName=CLASS)
        user32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(0, CLASS, None, 0, 0, 0, 0, 0, wt.HWND(-3), None, wc.hInstance, None)
        if not self.hwnd:
            return
        names = [None]  # the hook on title changes of the window in front (a tab switched), while one is followed

        def on_front(hook, event, hwnd, obj, child, thread, ms):
            if names[0]:
                user32.UnhookWinEvent(names[0])
                self.hooks.remove(names[0])
                names[0] = None
            if hwnd in self.session_windows():
                names[0] = user32.SetWinEventHook(0x800C, 0x800C, None, self._name, pid_of(hwnd), 0, 0)  # NAMECHANGE
                if names[0]:
                    self.hooks.append(names[0])
            self.check(hwnd)

        def on_name(hook, event, hwnd, obj, child, thread, ms):
            if obj == 0 and hwnd == user32.GetForegroundWindow():  # OBJID_WINDOW
                self.check(hwnd)
        self._front, self._name = WINEVENTPROC(on_front), WINEVENTPROC(on_name)
        hk = user32.SetWinEventHook(3, 3, None, self._front, 0, 0, 0)  # EVENT_SYSTEM_FOREGROUND, out of context
        if hk:
            self.hooks.append(hk)
        user32.SetTimer(self.hwnd, T_CHECK, CHECK_EVERY, None)
        if self.panel_pid:
            threading.Thread(target=self.follow_panel, daemon=True).start()
        self.check()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def follow_panel(self):
        """Ends with the panel, however the panel ends."""
        h = kernel32.OpenProcess(0x00100000, False, self.panel_pid)  # SYNCHRONIZE
        if h:
            kernel32.WaitForSingleObject(h, 0xFFFFFFFF)
            kernel32.CloseHandle(h)
        user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)


def grab():
    """The clipboard's picture (Pillow reads PNG or DIB), or None."""
    from PIL import Image, ImageGrab
    for _ in range(10):  # the program that just copied may hold the clipboard a moment
        try:
            img = ImageGrab.grabclipboard()
            return img if isinstance(img, Image.Image) else None
        except Exception:
            time.sleep(0.05)
    return None


def keeper():
    """The keeper's lookup of a picture it already saved (agent-panel-shots.py beside this file), or None."""
    try:
        spec = importlib.util.spec_from_file_location("agent_panel_shots", os.path.join(HERE, "agent-panel-shots.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.saved_file
    except Exception:
        return None


def file_log(*a):
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > 65536:
            with open(LOG, encoding="utf-8", errors="replace") as f:
                tail = f.read()[-32768:]
            with open(LOG, "w", encoding="utf-8") as f:
                f.write(tail)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), " ".join(str(x) for x in a)))
    except OSError:
        pass


def main(argv):
    pid = int(argv[1]) if len(argv) > 1 else None
    for _ in range(30):  # the previous one may still be ending (the panel restarted)
        mutex = kernel32.CreateMutexW(None, False, MUTEX)
        if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
            break
        kernel32.CloseHandle(mutex)
        time.sleep(0.1)
    else:
        return
    Keys(pid, saved_file=keeper(), log=file_log).run()


if __name__ == "__main__":
    main(sys.argv)
