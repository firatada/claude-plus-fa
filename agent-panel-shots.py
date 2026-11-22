# Screenshots for the Agent Panel (loaded by agent-panel.pyw; no process of its own).
# Every screenshot the user takes (Win+Shift+S / Snipping Tool, PrintScreen) is saved at once to
# <Desktop>\screenshots\<app>_<window-title>_YYYY-MM-DD_HHMM.png, named after the window that was in front
# when it was taken. When the user pastes it into a Claude Code session, the
# file moves to <that session's folder>\screenshots\<session-title>_YYYY-MM-DD_HHMM.png and that folder
# gets screenshots/ in its .gitignore (and .assetsignore, when it has one). Pasted into a second session
# in another folder, it is copied there. Nothing is deleted; nothing is added to Claude's input; the
# keeper only reads the clipboard. The panel's strip (latest(): the pinned ones and the last three) writes TEXT to it on Copy
# (copy_text(): the file's path and its OCR lines, read by a resident OCR worker); the preview's Copy as Image
# (copy_image()) writes the picture with the paste mark, so the keeper never takes either for a screenshot.
# Off: "screenshots": false in ~/.claude/claude-plus.local.json.
import base64, collections, ctypes, ctypes.wintypes as wt, datetime, io, json, os, queue, re, shutil, struct, \
    threading, time, unicodedata
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
PASTE_MARK = "claude-plus-fa paste"  # agent-panel-paste.py marks the clipboards it sets (a path, then the user's own
                                     # content put back): never a new screenshot
HOLD = 3.0             # seconds a clipboard capture waits for the same picture as a file in Windows' Screenshots
SNIP_RECENT = 5.0      # seconds after a snipping tool was seen running that the broker's images still count:
                       # it sets the clipboard twice, the second time (0.5 s later) after ScreenClippingHost ended
SAME = 1.0             # mean channel difference (0..255) at 24 x 24 below which two pictures are the same capture
MATCH = 6.0            # ... below which a pasted image (maybe shrunk, maybe JPEG) is taken for a saved screenshot;
                       # measured on a real screen: the same picture shrunk to 0.3 and saved as JPEG q20 or palette
                       # PNG <= 3.0; other regions 15 and up; the same region 20 px further over 3.0 too, so near
                       # twins are told apart by exact pixels (an unshrunk PNG) or else by time (CLOSE)
DRAG_POLL = 0.015      # seconds between looks at the mouse while a snipping tool is in front (only then)
CLOSE = 1.5            # matches this close to the best one are told apart by time (the latest before the paste)
KEEP = 300             # screenshots remembered for matching
TICK = 1.0             # seconds between looks at Windows' Screenshots folder and the open transcripts
WM_CLIPBOARDUPDATE, WM_DESTROY, WM_CLOSE = 0x031D, 0x0002, 0x0010
# A snip is named after the window under the rectangle the user dragged (followed with the mouse while a snipping
# tool is in front), not after the window in front: the focus may be in another window than the one snipped (seen
# 2026-11-13: a snip of one terminal named after the terminal beside it that had the focus). PrintScreen and a snip
# whose drag was not seen are named after the window in front before the capture started. Windows that come to the
# front but are never what was photographed (the taskbar, Alt+Tab, a snipping overlay; the panel's own windows,
# by process) are not remembered, so the last one remembered is the window in front under the overlay.
PASSING = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "MultitaskingViewFrame", "ForegroundStaging",
           "XamlExplorerHostIslandWindow", "TaskSwitcherWnd"}
DESKTOP = {"Progman", "WorkerW"}
# other names an app goes by at the end of its window titles ("... - Microsoft Edge"); its exe name is always one
ALIAS = {"msedge": ["edge"], "winword": ["word"], "powerpnt": ["powerpoint"], "notepad": ["not defteri"],
         "explorer": ["dosya gezgini"]}

user32, kernel32, shell32 = ctypes.windll.user32, ctypes.windll.kernel32, ctypes.windll.shell32
dwmapi = ctypes.windll.dwmapi
user32.GetClipboardOwner.restype = user32.GetForegroundWindow.restype = user32.GetTopWindow.restype = wt.HWND
user32.GetWindow.restype = wt.HWND
user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
user32.GetTopWindow.argtypes = [wt.HWND]
user32.GetAsyncKeyState.restype = ctypes.c_short
dwmapi.DwmGetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]
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


def pins():
    """The screenshots pinned in the panel's strip, by capture time ("shot_pins" in claude-plus.local.json; the panel
    writes it). Kept in the index however many newer ones come."""
    try:
        with open(CONFIG, encoding="utf-8") as f:
            v = json.load(f)
        return {float(a) for a in v.get("shot_pins") or []}
    except (OSError, ValueError, TypeError, AttributeError):
        return set()


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
            "fg": user32.GetForegroundWindow(),
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


# ---------- the window a screenshot was taken of ----------
ENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def window_app(hwnd):
    """The program behind a window, as (pid, short name): its exe name, lower case. A Store app's window belongs
    to ApplicationFrameHost, so the app is the process of the child window it frames."""
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = os.path.basename(exe_of(pid.value)).lower()
    if exe == "applicationframehost.exe":
        found = []

        def child(h, _):
            p = wt.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(p))
            if p.value != pid.value:
                found.append(p.value)
                return False
            return True
        user32.EnumChildWindows(hwnd, ENUMPROC(child), 0)
        if found:
            exe = os.path.basename(exe_of(found[0])).lower()
    return pid.value, re.sub(r"[^a-z0-9]", "", exe[:-4] if exe.endswith(".exe") else exe)


def window_text(hwnd):
    """The title, read without sending the window a message (a hung window cannot hold this up)."""
    buf = ctypes.create_unicode_buffer(512)
    user32.InternalGetWindowText(hwnd, buf, 512)
    return buf.value


def class_of(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_at(x, y, skip=None):
    """The window a screenshot shows at this point of the screen: the first one in z-order under it that skip()
    does not refuse. Hidden, cloaked (a background UWP frame), minimised and click-through windows do not count."""
    h = user32.GetTopWindow(None)
    while h:
        r, cloaked = wt.RECT(), ctypes.c_int(0)
        dwmapi.DwmGetWindowAttribute(h, 14, ctypes.byref(cloaked), 4)  # DWMWA_CLOAKED
        if (user32.IsWindowVisible(h) and not cloaked.value and not user32.IsIconic(h)
                and not user32.GetWindowLongW(h, -20) & 0x20  # WS_EX_TRANSPARENT
                and user32.GetWindowRect(h, ctypes.byref(r)) and r.left <= x < r.right and r.top <= y < r.bottom
                and not (skip and skip(h))):
            return h
        h = user32.GetWindow(h, 2)  # GW_HWNDNEXT
    return None


def title_part(app, title):
    """The window title without the app's own name at its end (" - Google Chrome", " - Excel"), as a slug; empty
    when nothing else is left."""
    names = [n.replace(" ", "-") for n in [app] + ALIAS.get(app, [])]

    def is_app(seg):
        s = slugify(seg, "")
        return any(n == s or n in s.split("-") for n in names)
    parts = re.split(r"\s+[-–—]\s+", (title or "").replace("​", "").strip())
    while len(parts) > 1 and is_app(parts[-1]):
        parts.pop()
    t = " - ".join(parts)
    return "" if is_app(t) or slugify(t, "") == app else slugify(t, "")


def shot_stem(front, at):
    """<app>_<title>_YYYY-MM-DD_HHMM, or <app>_YYYY-MM-DD_HHMM, or the time alone when no window is known."""
    if not front or not front.get("app"):
        return stamp(at)
    t = title_part(front["app"], front.get("title"))
    return "_".join([front["app"]] + ([t] if t else []) + [stamp(at)])


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
def slugify(title, empty="session"):
    t = (title or "").replace("ı", "i").replace("İ", "I")
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("ascii").lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    if len(t) > 40:
        t = t[:40].rsplit("-", 1)[0] if "-" in t[:40] else t[:40]
    return t or empty


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


# ---------- the strip's Copy: the path and the OCR text, as text ----------
# Windows' own OCR (Windows.Media.Ocr), driven from Windows PowerShell 5.1, which reaches WinRT without a package.
# Turkish first: measured, it reads Turkish right and English with small l/I slips; en-US garbles Turkish letters.
# One PowerShell stays resident (Ocr): started with the panel, it loads WinRT and makes the engine once, then waits
# on stdin for "<path><TAB><times>" and answers with the factor it read at, every word with its box, and END. It
# reads a picture only when Copy asks for one; starting a PowerShell per Copy cost 1.2 to 2.8 s. read() reads the
# picture as it is, and when its words are in a fixed-pitch face (a terminal: fixed_pitch()) reads it again enlarged,
# cubic, by the largest whole factor up to OCR_UP that keeps it within 10000 px a side (OcrEngine.MaxImageDimension)
# and 25 megapixels (time). Measured on rendered pictures: at a terminal's text size Windows' OCR misreads digits,
# case and punctuation ("10." as "18.", "2048" as "2848"); enlarged 3x it reads them best (2x less well, 4x worse
# again), while Segoe UI text reads best as it is (3x lost the dots of "ö"). The text is rebuilt from the words'
# boxes (ocr_rows()), not from the OCR's own lines, which split a list's number column from its text.
TAB = chr(9)  # between the path and the factor (no Windows file name holds one)
END = "\x1e"  # closes each answer; never in OCR text
OCR_LANG, OCR_UP = "tr", 3
OCR_PS = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
[Console]::InputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$as = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and
  $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } |
  Select-Object -First 1
function Wait($op, [Type]$t) { $k = $as.MakeGenericMethod($t).Invoke($null, @($op)); $k.Wait() | Out-Null; $k.Result }
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
$I = [Windows.Graphics.Imaging.BitmapInterpolationMode]::Cubic
$e = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('@LANG@'))
if (-not $e) { $e = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if (-not $e) { exit 3 }
# warm the engine on a blank bitmap made here (no picture of the user's is read): the first real read is then fast
$w = [Windows.Graphics.Imaging.SoftwareBitmap]::new([Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8, 64, 64)
$null = Wait ($e.RecognizeAsync($w)) ([Windows.Media.Ocr.OcrResult]); $w.Dispose()
[Console]::Out.WriteLine('READY'); [Console]::Out.Flush()
while ($null -ne ($p = [Console]::In.ReadLine())) {
  try {
    $p, $Up = $p.Split("`t"); $Up = [int]$Up
    $f = Wait ([Windows.Storage.StorageFile]::GetFileFromPathAsync($p)) ([Windows.Storage.StorageFile])
    $s = Wait ($f.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $d = Wait ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($s)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $k = [Math]::Min($Up, [Math]::Floor(10000 / [Math]::Max($d.PixelWidth, $d.PixelHeight)))
    $k = [Math]::Min($k, [Math]::Floor([Math]::Sqrt(25e6 / ([double]$d.PixelWidth * $d.PixelHeight))))
    $t = [Windows.Graphics.Imaging.BitmapTransform]::new()
    if ($k -gt 1) { $t.ScaledWidth = $d.PixelWidth * $k; $t.ScaledHeight = $d.PixelHeight * $k; $t.InterpolationMode = $I }
    else { $k = 1 }
    $b = Wait ($d.GetSoftwareBitmapAsync([Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
      [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied, $t,
      [Windows.Graphics.Imaging.ExifOrientationMode]::IgnoreExifOrientation,
      [Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage)) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $r = Wait ($e.RecognizeAsync($b)) ([Windows.Media.Ocr.OcrResult])
    $b.Dispose(); $s.Dispose()
    $o = [Text.StringBuilder]::new("$k`n")
    foreach ($l in $r.Lines) { foreach ($x in $l.Words) { $q = $x.BoundingRect
      $null = $o.Append([int]$q.X).Append("`t").Append([int]$q.Y).Append("`t").Append([int]$q.Width).Append("`t").
        Append([int]$q.Height).Append("`t").Append($x.Text).Append("`n") } }
    [Console]::Out.Write($o.ToString())
  } catch { }
  [Console]::Out.WriteLine([char]0x1e); [Console]::Out.Flush()
}
"""


class Ocr:
    """The resident OCR worker. start() launches it in the background; read() hands it one path and waits for the
    answer. A worker that died or hangs is replaced at the next read (which then pays the start, 1 to 3 s)."""

    def __init__(self):
        self.proc, self.lock = None, threading.Lock()

    def start(self):
        threading.Thread(target=self._ensure_locked, daemon=True).start()
        return self

    def _ensure_locked(self):
        with self.lock:
            self._ensure()

    def _ensure(self):
        import subprocess
        if self.proc and self.proc.poll() is None:
            return True
        self.proc = None
        enc = base64.b64encode(OCR_PS.replace("@LANG@", OCR_LANG).encode("utf-16-le")).decode("ascii")
        try:
            p = subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                  "-EncodedCommand", enc], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, creationflags=0x08000000)  # CREATE_NO_WINDOW
        except OSError:
            return False
        if self._line(p, 60) != "READY":
            p.kill()
            return False
        self.proc = p
        return True

    @staticmethod
    def _line(p, timeout):
        """One line of the worker's answer, or None when it ends or says nothing for timeout seconds."""
        out = []
        t = threading.Thread(target=lambda: out.append(p.stdout.readline()), daemon=True)
        t.start()
        t.join(timeout)
        if not out or not out[0]:
            return None
        return out[0].decode("utf-8", "replace").rstrip("\r\n")

    def read(self, path):
        """The picture's text as rows (ocr_rows()); [] when it finds none or cannot run (it reads pictures up to
        OcrEngine.MaxImageDimension, 10000 px here)."""
        w = self.words(path)
        if OCR_UP > 1 and fixed_pitch(w):
            w = self.words(path, OCR_UP) or w
        return ocr_rows(w)

    def words(self, path, times=1):
        """Every word Windows' OCR found in the picture enlarged up to times: (x, y, w, h, text), the box in the
        picture's own pixels."""
        out, k = [], 1.0
        for i, l in enumerate(self._answer(os.path.abspath(path) + TAB + str(int(times)))):
            if i == 0:
                try:
                    k = float(l) or 1.0
                except ValueError:
                    return []
                continue
            f = l.split("\t", 4)
            if len(f) == 5 and f[4].strip():
                try:
                    out.append((int(f[0]) / k, int(f[1]) / k, int(f[2]) / k, int(f[3]) / k, f[4].strip()))
                except ValueError:
                    pass
        return out

    def _answer(self, ask):
        """The worker's raw answer lines for one request."""
        with self.lock:
            for _ in range(2):  # a dead worker is started again once
                if not self._ensure():
                    return []
                p = self.proc
                try:
                    p.stdin.write(ask.encode("utf-8") + b"\n")
                    p.stdin.flush()
                except OSError:
                    self.proc = None
                    continue
                out = []  # one reader for the whole answer (a thread per line cost 0.3 s on 500 words)
                t = threading.Thread(target=self._until_end, args=(p, out), daemon=True)
                t.start()
                t.join(30)
                if not out:  # died or hangs: replaced next time
                    p.kill()
                    self.proc = None
                    return []
                return out[0]
            return []

    @staticmethod
    def _until_end(p, out):
        lines = []
        for l in iter(p.stdout.readline, b""):
            l = l.decode("utf-8", "replace").rstrip("\r\n")
            if l == END:
                out.append(lines)
                return
            lines.append(l)

    def stop(self):
        if self.proc:
            try:
                self.proc.kill()
            except OSError:
                pass


kernel32.GlobalAlloc.restype = kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
kernel32.GlobalLock.argtypes = kernel32.GlobalUnlock.argtypes = kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
user32.SetClipboardData.argtypes = [wt.UINT, ctypes.c_void_p]
user32.SetClipboardData.restype = ctypes.c_void_p


def set_clipboard(items):
    """The clipboard emptied and set to items [(format, bytes)], in that order; False when it stays busy or a
    format is refused."""
    for _ in range(10):  # another program may hold the clipboard open for a moment
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return False
    try:
        user32.EmptyClipboard()
        for fmt, data in items:
            if isinstance(fmt, str):
                fmt = user32.RegisterClipboardFormatW(fmt)
            h = kernel32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
            if not h:
                return False
            ctypes.memmove(kernel32.GlobalLock(h), data, len(data))
            kernel32.GlobalUnlock(h)
            if not user32.SetClipboardData(fmt, h):  # once set, the clipboard owns the memory
                kernel32.GlobalFree(h)
                return False
        return True
    finally:
        user32.CloseClipboard()


def set_clipboard_text(text):
    """Text alone on the clipboard (CF_UNICODETEXT): no image format, so is_screenshot() refuses it."""
    return set_clipboard([(13, text.encode("utf-16-le") + b"\0\0")])


def image_formats(path):
    """Copy as Image: the picture in the forms Windows programs read, in the order they are offered. PNG (the
    file's own bytes when it is one: browsers, Word, chat apps), CF_DIBV5 (32 bit, straight alpha, sRGB), CF_DIB
    (24 bit, alpha laid on white: Paint and older programs; Windows makes CF_BITMAP from it), each bottom-up at
    the picture's own size; and the paste mark, so the keeper never takes it for a new screenshot."""
    with open(path, "rb") as f:
        raw = f.read()
    with Image.open(io.BytesIO(raw)) as im:
        im.load()
        rgba = im.convert("RGBA")
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        buf = io.BytesIO()
        rgba.save(buf, "PNG", compress_level=1)
        raw = buf.getvalue()
    w, h = rgba.size
    bgra = rgba.tobytes("raw", "BGRA", 0, -1)  # bottom-up, 4 bytes a pixel: rows need no padding
    v5 = struct.pack("<IiiHHIIiiIIIIII", 124, w, h, 1, 32, 3, len(bgra), 2835, 2835, 0, 0,  # BI_BITFIELDS
                     0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000) + struct.pack("<I", 0x73524742)  # 'sRGB'
    v5 += bytes(36 + 12) + struct.pack("<IIII", 4, 0, 0, 0)  # endpoints, gamma; LCS_GM_IMAGES; no profile
    rgb = Image.new("RGB", rgba.size, (255, 255, 255))
    rgb.paste(rgba, mask=rgba.getchannel("A"))
    stride = (w * 3 + 3) & ~3
    bgr = rgb.tobytes("raw", "BGR", stride, -1)
    dib = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, len(bgr), 2835, 2835, 0, 0)  # BI_RGB
    return [("PNG", raw), (CF_DIBV5, v5 + bgra), (CF_DIB, dib + bgr), (PASTE_MARK, b"\1\0\0\0")]


def fixed_pitch(words):
    """Whether the words are set in a fixed-pitch face: the width per letter of words of five letters or more is
    the same for all but a few (median spread under 3 %; measured 0.7 to 1.0 % on Consolas at 14 to 24 px, 7.8 % on
    Segoe UI)."""
    r = [w[2] / len(w[4]) for w in words if len(w[4]) >= 5 and w[4].isalpha()]
    if len(r) < 8:
        return False
    m = sorted(r)[len(r) // 2]
    return sorted(abs(v - m) / m for v in r)[len(r) // 2] < 0.03


MARK = re.compile(r"^(\d{1,3}[.),]|[-*+>•●○■▪►✓✔])(\s|$)")  # a row that opens a list item
NUMBERED = re.compile(r"^\d{1,3}\.\s")


def ocr_rows(words):
    """The text of OCR words (x, y, w, h, text) rebuilt from their boxes alone:
    rows:   words grouped by vertical position across the whole width (a word whose middle lies within half a word
            height of a row's middle is on it; small ones, like a lone comma, go to the row their middle falls in),
            each row read left to right;
    spaces: two neighbours touch (no space) when their gap is under half the picture's usual gap between words;
    blanks: one empty line where the gap to the next row is over 1.6 times the usual row pitch;
    wraps:  a row is joined to the one before it, with a space, when none of the rules above put a blank between
            them, it does not open a list item (a number, a bullet), it starts where the row before starts (or
            where its text starts, after its number), and its first word would not have fitted in the room left at
            the end of the row before, up to the text's right edge: the terminal moved it down. Only on a picture
            where at least two rows end that way, so that the right edge is the text's own and not just its
            longest line;
    fixes:  on a picture with a numbered list, a row's leading "14," or "14 ," is read as "14.".
    Nothing else is corrected."""
    if not words:
        return []
    med = lambda v: sorted(v)[len(v) // 2]
    h = med([w[3] for w in words])
    mid = lambda w: w[1] + w[3] / 2
    rows = []  # [middle, top, bottom, words]
    for w in sorted((w for w in words if w[3] >= 0.6 * h), key=mid):
        if rows and mid(w) - rows[-1][0] <= 0.5 * h:
            r = rows[-1]
            r[3].append(w)
            r[0] = sum(mid(x) for x in r[3]) / len(r[3])
            r[1], r[2] = min(r[1], w[1]), max(r[2], w[1] + w[3])
        else:
            rows.append([mid(w), w[1], w[1] + w[3], [w]])
    for w in (w for w in words if w[3] < 0.6 * h):
        if not rows:
            rows.append([mid(w), w[1], w[1] + w[3], []])
        inside = [r for r in rows if r[1] <= mid(w) <= r[2]]
        min(inside or rows, key=lambda r: abs(r[0] - mid(w)))[3].append(w)
    for r in rows:
        r[3].sort(key=lambda w: w[0])
    gaps = [b[0] - (a[0] + a[2]) for r in rows for a, b in zip(r[3], r[3][1:])]
    gaps = [g for g in gaps if 0 < g < 2 * h]
    g = med(gaps) if gaps else 0.5 * h
    pitch = [b[0] - a[0] for a, b in zip(rows, rows[1:])]
    pitch = med(pitch) if pitch else h
    ends = [r[3][-1][0] + r[3][-1][2] for r in rows]
    right = max(ends)
    # rows that end the way a wrapped row does; with only one the right edge may be just the longest line
    fits = lambda i: right - ends[i - 1] < rows[i][3][0][2] + 1.5 * g  # row i's first word did not fit above
    wraps = sum(fits(i) for i in range(1, len(rows))) >= 2
    texts = []
    for r in rows:
        ws, text = r[3], r[3][0][4]
        for a, b in zip(ws, ws[1:]):
            text += ("" if b[0] - (a[0] + a[2]) < 0.5 * g else " ") + b[4]
        texts.append(text)
    if any(NUMBERED.match(t) for t in texts):
        texts = [re.sub(r"^(\d{1,3}) ?,(?=\s|$)", r"\1.", t) for t in texts]
    out, prev = [], None  # prev: (left, text start) of the row that began the last line put out
    for i, (r, text) in enumerate(zip(rows, texts)):
        ws = r[3]
        blank = i > 0 and r[0] - rows[i - 1][0] > 1.6 * pitch
        left = ws[0][0]
        start = ws[1][0] if MARK.match(text) and len(ws) > 1 else left
        if (wraps and prev and not blank and not MARK.match(text)
                and min(abs(left - prev[0]), abs(left - prev[1])) <= g and fits(i)):
            out[-1] += " " + text
            continue
        if blank and out:
            out.append("")
        out.append(text)
        prev = (left, start)
    return out


def shot_text(path, lines):
    """What Copy puts on the clipboard: a few tokens in a prompt, where the picture itself costs many."""
    return "\n".join(["[screenshot: %s]" % path] + list(lines))


def load_index(state=STATE):
    try:
        with open(state, encoding="utf-8") as f:
            v = json.load(f)
        for e in v:
            e["sig"] = base64.b64decode(e["sig"])
        return v
    except Exception:
        return []


def where(e):
    """Where the screenshot is now: the Desktop file, else a project copy that still exists."""
    if e.get("file") and os.path.exists(e["file"]):
        return e["file"]
    for p in e["places"]:
        if os.path.exists(p):
            return p
    return None


def saved_file(img, state=STATE):
    """For the paste key (agent-panel-paste.py, its own process): the file of a screenshot the keeper saved that is
    this very picture, pixel for pixel, wherever it is now (newest first); None when there is none. Read from the
    index file; a capture still waiting HOLD is not there yet."""
    s, size = sig(img), list(img.size)
    for e in reversed(load_index(state)):
        if e["size"] == size and diff(e["sig"], s) < SAME:
            p = where(e)
            if p and identical(p, img):
                return p
    return None


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
        self.fronts = collections.deque(maxlen=32)  # windows that came to the front: {"at", "hwnd", "app", "title"}
        self.drag = None       # the last rectangle dragged while a snipping tool was in front: (x, y, ended at)
        self.tracking = False  # follow_drag() is running
        self.version = 0       # goes up on every change to the index: the panel's strip redraws then
        self.ocr = Ocr()       # the resident OCR worker: Copy's reading
        self.keep = pins()     # capture times the panel's strip has pinned: never trimmed off the index

    # ----- state -----
    def load(self):
        return load_index(self.state)

    def save(self):
        cut, keep = len(self.index) - KEEP, self.keep
        if cut > 0:  # the oldest go, except pinned ones
            self.index = [e for i, e in enumerate(self.index) if i >= cut or e["at"] in keep]
        try:
            os.makedirs(os.path.dirname(self.state), exist_ok=True)
            tmp = self.state + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump([dict(e, sig=base64.b64encode(e["sig"]).decode("ascii")) for e in self.index], f)
            os.replace(tmp, self.state)
        except OSError:
            pass
        self.version += 1

    def latest(self, n=3, pinned=()):
        """The panel's strip: every pinned screenshot that still exists, in the order it was pinned (pinned: the
        capture times in that order), then the last n unpinned ones, newest first, each where it is now."""
        pinned = list(pinned)
        pins, recent = [], []
        for e in reversed(list(self.index)):
            pin = e["at"] in pinned
            if not pin and len(recent) == n:
                continue
            p = not e.get("dropped") and self.where(e)
            if p:
                (pins if pin else recent).append({"at": e["at"], "path": p, "pin": pin})
        pins.sort(key=lambda i: pinned.index(i["at"]))
        return pins + recent

    def alive(self, at):
        """The screenshot is still kept: in the index, not deleted on the preview, its file somewhere."""
        e = next((e for e in reversed(list(self.index)) if e["at"] == at), None)
        return bool(e and not e.get("dropped") and self.where(e))

    def path(self, at):
        """Where the screenshot taken at `at` is now (None when it is gone), looked up at this moment: it may have
        moved into a project since the strip was drawn."""
        e = next((e for e in reversed(list(self.index)) if e["at"] == at), None)
        return e and self.where(e)

    def drop(self, at):
        """Delete on the preview: the file went to the Recycle Bin (or was already gone), so the screenshot leaves
        the strip for good, even when a copy pasted into another project still exists (that copy stays)."""
        for e in self.index:
            if e["at"] == at:
                e["dropped"] = True
        self.save()

    def copy_text(self, at):
        """Copy on a strip card: its current path and OCR text on the clipboard, as text."""
        p = self.path(at)
        if not p:
            return False
        ok = set_clipboard_text(shot_text(p, self.ocr.read(p)))
        self.log("copied text" if ok else "copy failed", p)
        return ok

    def copy_image(self, at):
        """Copy as Image on the preview: the picture itself on the clipboard (image_formats())."""
        p = self.path(at)
        if not p:
            return False
        try:
            ok = set_clipboard(image_formats(p))
        except Exception:  # unreadable, or gone meanwhile
            ok = False
        self.log("copied image" if ok else "copy image failed", p)
        return ok

    # ----- threads -----
    def start(self):
        self.front(user32.GetForegroundWindow())
        self.ocr.start()
        threading.Thread(target=self.listen, daemon=True).start()
        threading.Thread(target=self.work, daemon=True).start()
        return self

    def stop(self):
        self.ocr.stop()
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

    @staticmethod
    def describe(hwnd, at=None):
        """{"at", "hwnd", "app", "title"} for a window a screenshot can show; None for one it never shows: the
        panel's own, the taskbar, Alt+Tab, a snipping overlay. The desktop is "desktop", without a title."""
        try:
            if not hwnd:
                return None
            cls = class_of(hwnd)
            if cls in PASSING:
                return None
            if cls in DESKTOP:
                app, title = "desktop", ""
            else:
                pid, app = window_app(hwnd)
                if pid == os.getpid() or not app or app + ".exe" in SNIP_TOOLS:
                    return None
                title = window_text(hwnd)
            return {"at": time.time() if at is None else at, "hwnd": hwnd, "app": app, "title": title}
        except Exception:
            return None

    def front(self, hwnd, at=None):
        """A window came to the front (the panel's foreground hook calls this). A snipping tool's overlay: follow
        the mouse while it stays in front, to know where the snip was dragged."""
        f = self.describe(hwnd, at)
        if f:
            self.fronts.append(f)
        elif hwnd and not self.tracking and self.snipping(hwnd):
            self.tracking = True
            threading.Thread(target=self.follow_drag, daemon=True).start()

    @staticmethod
    def snipping(hwnd):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return os.path.basename(exe_of(pid.value)).lower() in SNIP_TOOLS

    def follow_drag(self):
        """While a snipping tool is in front: the box the pointer covered with the primary button held, kept as the
        middle of that box and when the button was let go. A click (a window snip) gives its own point."""
        try:
            button = 0x02 if user32.GetSystemMetrics(23) else 0x01  # SM_SWAPBUTTON: the primary button
            pt, box, until = wt.POINT(), None, time.time() + 120
            while time.time() < until and self.snipping(user32.GetForegroundWindow()):
                held = bool(user32.GetAsyncKeyState(button) & 0x8000)
                user32.GetCursorPos(ctypes.byref(pt))
                if held:
                    b = box or [pt.x, pt.y, pt.x, pt.y]
                    box = [min(b[0], pt.x), min(b[1], pt.y), max(b[2], pt.x), max(b[3], pt.y)]
                elif box:
                    self.drag = ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2, time.time())
                    box = None
                time.sleep(DRAG_POLL)
        finally:
            self.tracking = False

    def snipped(self, at):
        """The window under the middle of the snip dragged just before this capture, or None."""
        d = self.drag
        if not d or not 0 <= at - d[2] < SNIP_RECENT:
            return None
        h = window_at(d[0], d[1], skip=lambda w: not self.describe(w, at))
        return self.describe(h, at) if h else None

    def taken_in(self, at, fg=None, snip=False):
        """The window a screenshot taken at this time shows. A snip: the window under the rectangle dragged, when
        the drag was seen (the focus may be elsewhere). Otherwise the foreground window read at that
        moment, when it is one a screenshot can show (PrintScreen; a snip once its overlay has closed): a
        foreground event can come for a window that never reached the front (seen: a Notepad started by another
        program). Otherwise, as while a snipping overlay is in front, the last window remembered no later than
        that, its title read again while it is still open (a browser changes it with the tab)."""
        f = (snip and self.snipped(at)) or self.describe(fg, at)
        if f:
            return f
        for f in reversed(list(self.fronts)):
            if f["at"] <= at:
                if f["app"] != "desktop" and user32.IsWindow(f["hwnd"]):
                    return dict(f, title=window_text(f["hwnd"]) or f["title"])
                return f
        return None

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
        if PASTE_MARK in ev["formats"]:
            self.log("ignored own paste")  # the paste key's path, or the clipboard it put back
            return
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
        self.pending.append({"img": img, "at": ev["at"], "sig": sig(img), "size": img.size,
                             "stem": shot_stem(self.taken_in(ev["at"], ev.get("fg"), snip=ev["owner"]), ev["at"])})
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
            dest = free_name(self.desk, shot_stem(self.taken_in(st.st_mtime), st.st_mtime))
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
            dest = free_name(self.desk, c["stem"])
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
        return where(e)

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
        dest = free_name(folder, "%s_%s" % (slugify(s.get("title")), stamp(e["at"])))
        e["places"].append(dest)  # listed first, so where() never finds it nowhere mid-move (a pin would be lost)
        if e.get("file") and src == e["file"]:
            shutil.move(src, dest)  # first paste: it leaves the Desktop
            e["file"] = None
            self.log("moved", dest)
        else:
            shutil.copy2(src, dest)  # pasted again elsewhere: a copy
            self.log("copied", dest)
        self.save()
        keep_private(home)
