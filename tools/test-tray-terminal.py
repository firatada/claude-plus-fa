# Tests for a session terminal that Windows Terminal minimises to the notification area ("minimizeToNotificationArea":
# true in its settings): minimised, Windows Terminal hides the window (IsIconic and not IsWindowVisible, no taskbar
# button). The panel must still find it as the session's window, treat it as minimised (not in front, not gone), bring
# it back restored and in front on a card click, and send it back to the notification area on the card's second click.
# Run: python -I tools/test-tray-terminal.py --windows
#      Opens one throwaway Windows Terminal window (wt.exe -w new) running a sleeping Python, and only that window is
#      minimised, restored and raised; no key is sent (bring_front()'s lone Alt is stubbed and counted). Every other
#      Windows Terminal window's state is compared before and after. Skipped without --windows or without the setting.
# The .pyw is never imported (that would trip its one-instance mutex and raise the running panel): the functions are
# taken from it by ast.
import ast, ctypes, ctypes.wintypes as wt, json, os, re, subprocess, sys, tempfile, threading, time, types, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
WINDOWS = "--windows" in sys.argv
if WINDOWS:
    sys.argv.remove("--windows")

with open(os.path.join(HERE, "..", "agent-panel.pyw"), encoding="utf-8") as f:
    TREE = ast.parse(f.read())

FUNCS = {"LIFT_AGAIN", "MIN_FRONT_WAIT", "TERMINAL_CLASSES", "terminal_window", "CLAUDE_TITLE", "window_of", "bring_front", "top_window", "focus_session", "put_away", "last_terminal",
         "shown_name", "window_title"}
METHODS = ("shown_in_front", "shared", "stay_on_screen", "over", "surface", "beneath")
keep = [n for n in TREE.body if isinstance(n, ast.Assign) and any(getattr(t, "id", None) in FUNCS for t in n.targets)]
keep += [n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name in FUNCS]
for c in [n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "Panel"]:
    keep += [n for n in c.body if isinstance(n, ast.FunctionDef) and n.name in METHODS]

u = ctypes.WinDLL("user32")
k = ctypes.WinDLL("kernel32")
dwm = ctypes.WinDLL("dwmapi")
for name in ("GetForegroundWindow", "GetAncestor", "GetTopWindow", "GetWindow"):
    getattr(u, name).restype = wt.HWND
u.GetTopWindow.argtypes = [wt.HWND]
u.GetWindow.argtypes = [wt.HWND, wt.UINT]
u.SetWindowPos.argtypes = [wt.HWND, ctypes.c_ssize_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
k.GetConsoleWindow.restype = wt.HWND
dwm.DwmGetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]
PRESSED = []
ns = {"ctypes": ctypes, "wt": wt, "re": re, "time": time, "user32": u, "kernel32": k, "dwmapi": dwm,
      "console_lock": threading.Lock(), "own": lambda h: False, "press": lambda *keys: PRESSED.append(keys)}
exec(compile(ast.Module(body=keep, type_ignores=[]), "agent-panel.pyw", "exec"), ns)

with open(os.path.join(HERE, "test-minimise.py"), encoding="utf-8") as f:  # its throwaway back and panel windows
    WIN = next(n.value.value for n in ast.parse(f.read()).body
               if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", None) == "WIN")
WT_SETTINGS = os.path.expandvars(r"%LOCALAPPDATA%\Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState"
                                 r"\settings.json")
CHILD = "import os, sys, time\nopen(sys.argv[1], 'w').write(str(os.getpid()))\ntime.sleep(600)\n"


def tray_setting():
    try:
        with open(WT_SETTINGS, encoding="utf-8") as f:
            return bool(re.search(r'"minimizeToNotificationArea"\s*:\s*true', f.read()))
    except OSError:
        return False


def terminals():
    """Every Windows Terminal window: {window: (visible, minimised)}."""
    out = {}

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def each(h, _):
        buf = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(h, buf, 64)
        if buf.value == "CASCADIA_HOSTING_WINDOW_CLASS":
            out[h] = (bool(u.IsWindowVisible(h)), bool(u.IsIconic(h)))
        return True
    u.EnumWindows(each, 0)
    return out


def wait(test, secs=3.0):
    end = time.time() + secs
    while time.time() < end:
        if test():
            return True
        time.sleep(0.05)
    return test()


@unittest.skipUnless(WINDOWS and tray_setting(), "a throwaway Windows Terminal window only with --windows, and only "
                                                 "with minimizeToNotificationArea on")
class TrayTerminal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="zz-tray-")
        child, pidf = os.path.join(cls.dir, "child.py"), os.path.join(cls.dir, "pid.txt")
        with open(child, "w") as f:
            f.write(CHILD)
        cls.others = terminals()
        cls.wt = subprocess.Popen(["wt.exe", "-w", "new", "--title", "zz-tray-test", sys.executable, "-I", child, pidf])
        assert wait(lambda: os.path.exists(pidf) and set(terminals()) - set(cls.others), 15), "no test window"
        cls.hwnd = (set(terminals()) - set(cls.others)).pop()
        time.sleep(1.0)
        with open(pidf) as f:
            cls.pid = int(f.read())

    @classmethod
    def tearDownClass(cls):
        u.PostMessageW(cls.hwnd, 0x0010, 0, 0)  # WM_CLOSE: its only tab, the sleeping Python
        subprocess.run(["taskkill", "/F", "/PID", str(cls.pid)], capture_output=True)
        cls.wt.wait(5)
        after = terminals()
        assert {h: after.get(h) for h in cls.others} == cls.others, "another Windows Terminal window changed"

    def panel(self, hwnd=None):
        p = types.SimpleNamespace(front=None, wins={self.pid: self.hwnd}, hwnd=hwnd, visible=True, dragging=False)
        for m in METHODS:
            setattr(p, m, types.MethodType(ns[m], p))
        return p

    def to_tray(self):
        if u.IsWindowVisible(self.hwnd):  # never to a hidden one: see test_5
            u.ShowWindow(self.hwnd, 6)  # SW_MINIMIZE: what the card's second click sends
        self.assertTrue(wait(lambda: not u.IsWindowVisible(self.hwnd)), "Windows Terminal did not hide it")
        self.assertTrue(u.IsIconic(self.hwnd))

    def test_1_minimise_goes_to_the_notification_area(self):
        self.to_tray()
        self.assertTrue(u.IsWindow(self.hwnd))

    def test_2_hidden_window_is_still_the_sessions_and_minimised(self):
        self.to_tray()
        self.assertEqual(ns["window_of"](self.pid), self.hwnd)  # find_windows(), window_for(), in_front()
        self.assertEqual(ns["last_terminal"]([self.hwnd], lambda h: u.IsWindow(h)), self.hwnd)  # tray_click()
        s = {"id": "a", "pid": self.pid, "name": "zz"}
        p = self.panel()
        p.front = self.hwnd  # even when it was the last window in front before it went
        self.assertFalse(p.shown_in_front(s, self.hwnd))  # so a card click raises it, never minimises
        self.assertFalse(ns["put_away"](("a", self.hwnd), "a", self.hwnd, lambda: p.shown_in_front(s, self.hwnd)))
        self.assertNotEqual(ns["top_window"](), self.hwnd)

    def test_3_card_click_brings_it_back_restored_and_on_top(self):
        self.to_tray()
        s = {"id": "a", "pid": self.pid, "name": "zz"}
        self.assertTrue(ns["focus_session"](s))  # a card click, the alert, Send to Session
        self.assertTrue(wait(lambda: u.IsWindowVisible(self.hwnd) and not u.IsIconic(self.hwnd)))
        self.assertTrue(wait(lambda: ns["top_window"]() == self.hwnd))
        r = wt.RECT()
        u.GetWindowRect(self.hwnd, ctypes.byref(r))
        self.assertGreater(r.left, -32000)
        if not PRESSED:  # the foreground needs no lone Alt when Windows grants it; with the stub nothing was pressed
            self.assertEqual(u.GetForegroundWindow(), self.hwnd)

    def test_4_second_click_sends_it_back(self):
        ns["bring_front"](self.hwnd)
        self.assertTrue(wait(lambda: ns["top_window"]() == self.hwnd))
        s = {"id": "a", "pid": self.pid, "name": "zz"}
        p = self.panel()
        p.front = self.hwnd
        if u.GetForegroundWindow() == self.hwnd:
            self.assertTrue(ns["put_away"](("a", self.hwnd), "a", self.hwnd, lambda: p.shown_in_front(s, self.hwnd)))
        self.to_tray()

    def test_5_minimise_on_a_hidden_window_puts_it_on_the_taskbar(self):
        # why put_away() must see the window showing in front: SW_MINIMIZE to a window already in the notification
        # area shows it again, minimised, as a taskbar button, and Windows Terminal does not hide it a second time
        self.to_tray()
        u.ShowWindow(self.hwnd, 6)
        self.assertTrue(wait(lambda: u.IsWindowVisible(self.hwnd)))
        time.sleep(0.5)
        self.assertTrue(u.IsWindowVisible(self.hwnd) and u.IsIconic(self.hwnd))

    def test_6_band_stays_on_screen_when_it_goes_to_the_notification_area(self):
        # on_min() -> stay_on_screen(), with a back window and a tool-window panel right under the terminal
        procs = [subprocess.Popen([sys.executable, "-I", "-c", WIN, *a], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  text=True, creationflags=0x08000000) for a in (("back", "100", "100", "1200", "700"),
                                                       ("panel", "1100", "50", "250", "700"))]
        try:
            back, panel = (int(x.stdout.readline()) for x in procs)
            ns["bring_front"](self.hwnd)  # no console of their own (CREATE_NO_WINDOW), so none came forward
            self.assertTrue(wait(lambda: u.GetForegroundWindow() == self.hwnd))
            u.ShowWindow(panel, 4)  # SW_SHOWNOACTIVATE
            u.SetWindowPos(panel, self.hwnd, 0, 0, 0, 0, 0x13)  # lift()
            p = self.panel(panel)
            p.own = lambda h: h == panel
            ns["own"] = p.own
            u.PostMessageW(self.hwnd, 0x0112, 0xF020, 0)  # WM_SYSCOMMAND SC_MINIMIZE: the caption button
            p.stay_on_screen(self.hwnd)
            self.assertTrue(wait(lambda: not u.IsWindowVisible(self.hwnd)))
            self.assertTrue(wait(lambda: u.GetForegroundWindow() == back))  # Windows hands the foreground on
            self.assertTrue(p.over(back))  # the panel over the window that came forward
        finally:
            ns["own"] = lambda h: False
            for x in procs:
                x.stdin.close()
                x.stdout.close()
                x.wait(5)


if __name__ == "__main__":
    unittest.main(verbosity=2, exit=False)
    print("lone Alt needed (stubbed):", len(PRESSED))
