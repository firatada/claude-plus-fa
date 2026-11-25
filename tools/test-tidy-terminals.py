# Tests for tidy(): when a Windows Terminal window comes to the front, every other Windows Terminal window showing is
# minimised, so with "minimizeToNotificationArea" on only the front one keeps a taskbar button.
# Run: python -I tools/test-tidy-terminals.py [--windows]
#      Without --windows: to_tidy(), wt_tray_on() on made-up settings files, and tidy()'s conditions on a fake user32.
#      --windows adds real runs on three throwaway Windows Terminal windows (wt.exe -w new, each a sleeping Python) and
#      one throwaway plain window. wt_states() is narrowed to the throwaway windows, so no other window is ever
#      minimised; every other Windows Terminal window's state is compared before and after. No key is sent
#      (bring_front()'s lone Alt is stubbed and counted). Skipped without the Windows Terminal setting.
# The .pyw is never imported (that would trip its one-instance mutex and raise the running panel): the functions are
# taken from it by ast.
import ast, ctypes, ctypes.wintypes as wt, glob, os, re, shutil, subprocess, sys, tempfile, threading, time, types, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
WINDOWS = "--windows" in sys.argv
if WINDOWS:
    sys.argv.remove("--windows")

with open(os.path.join(HERE, "..", "agent-panel.pyw"), encoding="utf-8") as f:
    TREE = ast.parse(f.read())

FUNCS = {"TIDY_WAIT", "TERMINAL_CLASSES", "WT_CLASS", "wt_window", "wt_tray_on", "wt_states", "to_tidy", "bring_front"}
METHODS = ("tidy_soon", "tidy")
keep = [n for n in TREE.body if isinstance(n, ast.Assign) and any(getattr(t, "id", None) in FUNCS for t in n.targets)]
keep += [n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name in FUNCS]
for c in [n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "Panel"]:
    keep += [n for n in c.body if isinstance(n, ast.FunctionDef) and n.name in METHODS]
CODE = compile(ast.Module(body=keep, type_ignores=[]), "agent-panel.pyw", "exec")

u = ctypes.WinDLL("user32")
dwm = ctypes.WinDLL("dwmapi")
for name in ("GetForegroundWindow",):
    getattr(u, name).restype = wt.HWND
u.SetWindowPos.argtypes = [wt.HWND, ctypes.c_ssize_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
dwm.DwmGetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]
PRESSED = []
SETTINGS = {}


def module(user32):
    ns = {"ctypes": ctypes, "wt": wt, "re": re, "os": os, "glob": glob, "time": time, "threading": threading,
          "user32": user32, "dwmapi": dwm, "HOME": os.path.expanduser("~"), "local": SETTINGS.get,
          "press": lambda *keys: PRESSED.append(keys)}
    exec(CODE, ns)
    return ns


def panel(ns):
    p = types.SimpleNamespace(tidy_gen=0)
    for m in METHODS:
        setattr(p, m, types.MethodType(ns[m], p))
    return p


class ToTidy(unittest.TestCase):
    def test_others_showing_go(self):
        states = [(1, True, False, False), (2, True, False, False), (3, True, False, False)]
        self.assertEqual(module(u)["to_tidy"](1, states), [2, 3])

    def test_front_never(self):
        self.assertEqual(module(u)["to_tidy"](1, [(1, True, False, False)]), [])

    def test_minimised_hidden_and_cloaked_stay(self):
        states = [(1, True, False, False), (2, False, True, False), (3, True, True, False), (4, True, False, True),
                  (5, False, False, False), (6, True, False, False)]
        self.assertEqual(module(u)["to_tidy"](1, states), [6])


class TrayOn(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="zz-tidy-")
        self.old = {k: os.environ.get(k) for k in ("LOCALAPPDATA", "SCOOP")}
        os.environ["LOCALAPPDATA"] = self.dir
        os.environ["SCOOP"] = os.path.join(self.dir, "scoop")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def write(self, rel, text):
        path = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_none(self):
        self.assertFalse(module(u)["wt_tray_on"]())

    def test_store_build_on(self):
        self.write(r"Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json",
                   '{\n    "copyOnSelect": true,\n    "minimizeToNotificationArea": true,\n}')
        self.assertTrue(module(u)["wt_tray_on"]())

    def test_off_or_commented(self):
        self.write(r"Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json",
                   '{\n    "minimizeToNotificationArea": false,\n    // "minimizeToNotificationArea": true\n}')
        self.assertFalse(module(u)["wt_tray_on"]())

    def test_unpackaged_on(self):
        self.write(r"Microsoft\Windows Terminal\settings.json", '{"minimizeToNotificationArea" : true}')
        self.assertTrue(module(u)["wt_tray_on"]())


class FakeUser32:
    def __init__(self, fg, iconic=(), hidden=()):
        self.fg, self.iconic, self.hidden, self.shown = fg, set(iconic), set(hidden), []

    def GetForegroundWindow(self):
        return self.fg

    def IsIconic(self, h):
        return h in self.iconic

    def IsWindowVisible(self, h):
        return h not in self.hidden

    def ShowWindowAsync(self, h, cmd):
        self.shown.append((h, cmd))


class TidyRules(unittest.TestCase):
    STATES = [(1, True, False, False), (2, True, False, False), (3, False, True, False)]

    def run_tidy(self, fake, gen_after=0, setting=None, tray=True):
        ns = module(fake)
        ns["TIDY_WAIT"] = 0.01
        ns["wt_states"] = lambda: self.STATES
        ns["wt_tray_on"] = lambda: tray
        SETTINGS.clear()
        if setting is not None:
            SETTINGS["minimize_other_terminals"] = setting
        p = panel(ns)
        p.tidy_gen = 5
        if gen_after:
            p.tidy_gen += gen_after
        return p.tidy(1, 5)

    def tearDown(self):
        SETTINGS.clear()

    def test_others_minimised_without_activation(self):
        fake = FakeUser32(1)
        self.assertTrue(self.run_tidy(fake))
        self.assertEqual(fake.shown, [(2, 7)])  # SW_SHOWMINNOACTIVE; 3 already in the notification area

    def test_setting_true_same_as_default(self):
        fake = FakeUser32(1)
        self.assertTrue(self.run_tidy(fake, setting=True))
        self.assertEqual(fake.shown, [(2, 7)])

    def test_setting_off(self):
        fake = FakeUser32(1)
        self.assertFalse(self.run_tidy(fake, setting=False))
        self.assertEqual(fake.shown, [])

    def test_windows_terminal_keeps_taskbar_buttons(self):
        fake = FakeUser32(1)
        self.assertFalse(self.run_tidy(fake, tray=False))
        self.assertEqual(fake.shown, [])

    def test_newer_foreground_change_wins(self):
        fake = FakeUser32(1)
        self.assertFalse(self.run_tidy(fake, gen_after=1))
        self.assertEqual(fake.shown, [])

    def test_front_moved_on(self):
        fake = FakeUser32(9)  # a browser, the panel, the reply window: nothing minimised
        self.assertFalse(self.run_tidy(fake))
        self.assertEqual(fake.shown, [])

    def test_front_minimised_or_hidden(self):
        for fake in (FakeUser32(1, iconic={1}), FakeUser32(1, hidden={1})):
            self.assertFalse(self.run_tidy(fake))
            self.assertEqual(fake.shown, [])

    def test_trigger_is_only_windows_terminal(self):
        with open(os.path.join(HERE, "..", "agent-panel.pyw"), encoding="utf-8") as f:
            text = f.read()
        src = ast.get_source_segment(text,
                                     next(n for c in TREE.body if isinstance(c, ast.ClassDef) and c.name == "Panel"
                                          for n in c.body if isinstance(n, ast.FunctionDef)
                                          and n.name == "follow_front"))
        on_front = src[src.index("def on_front"):src.index("def on_move")]
        self.assertLess(on_front.index("if own(hwnd):"), on_front.index("if wt_window(hwnd):"))  # own windows first
        self.assertIn("self.tidy_soon(hwnd)", on_front)


# ---------- real windows ----------
REAL = module(u) if WINDOWS else {}
ALL_STATES = REAL.get("wt_states")  # every Windows Terminal window, kept before the tests narrow REAL's to theirs
CHILD = "import os, sys, time\nopen(sys.argv[1], 'w').write(str(os.getpid()))\ntime.sleep(600)\n"
with open(os.path.join(HERE, "test-minimise.py"), encoding="utf-8") as f:  # its throwaway plain window
    WIN = next(n.value.value for n in ast.parse(f.read()).body
               if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", None) == "WIN")


def terminals():
    """Every Windows Terminal window: {window: (visible, minimised)}."""
    return {h: (v, i) for h, v, i, _ in ALL_STATES()}


def wait(test, secs=3.0):
    end = time.time() + secs
    while time.time() < end:
        if test():
            return True
        time.sleep(0.05)
    return test()


WINEVENTPROC = ctypes.WINFUNCTYPE(None, wt.HANDLE, wt.DWORD, wt.HWND, wt.LONG, wt.LONG, wt.DWORD, wt.DWORD)
SetWinEventHook = ctypes.WINFUNCTYPE(wt.HANDLE, wt.DWORD, wt.DWORD, wt.HMODULE, WINEVENTPROC, wt.DWORD, wt.DWORD,
                                     wt.DWORD)(("SetWinEventHook", u))


class Foreground:
    """A foreground WinEvent hook on its own thread, as follow_front() has: records every foreground change, and calls
    on_front(hwnd) for each (the panel's trigger)."""

    def __init__(self, on_front=None):
        self.seen, self.on_front = [], on_front
        self.proc = WINEVENTPROC(self.event)
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()
        self.ready.wait(2)

    def event(self, hook, ev, hwnd, obj, child, thread, ms):
        if hwnd:
            self.seen.append(hwnd)
            if self.on_front:
                self.on_front(hwnd)

    def loop(self):
        self.tid = ctypes.WinDLL("kernel32").GetCurrentThreadId()
        self.hook = SetWinEventHook(3, 3, None, self.proc, 0, 0, 0)
        self.ready.set()
        msg = wt.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))
        u.UnhookWinEvent(self.hook)

    def stop(self):
        u.PostThreadMessageW(self.tid, 0x0012, 0, 0)  # WM_QUIT
        self.thread.join(2)


@unittest.skipUnless(WINDOWS and REAL.get("wt_tray_on", lambda: False)(),
                     "throwaway Windows Terminal windows only with --windows, and only with minimizeToNotificationArea "
                     "on")
class RealTerminals(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="zz-tidy-")
        child = os.path.join(cls.dir, "child.py")
        with open(child, "w") as f:
            f.write(CHILD)
        cls.others = terminals()
        cls.mine, cls.pids, cls.wts = [], [], []
        for n in range(3):
            pidf = os.path.join(cls.dir, "pid%d.txt" % n)
            before = set(terminals())
            cls.wts.append(subprocess.Popen(["wt.exe", "-w", "new", "--title", "zz-tidy-%d" % n, sys.executable, "-I",
                                             child, pidf]))
            assert wait(lambda: os.path.exists(pidf) and set(terminals()) - before - set(cls.mine), 15), "no window"
            cls.mine.append((set(terminals()) - before).pop())
            time.sleep(0.5)
            with open(pidf) as f:
                cls.pids.append(int(f.read()))
        cls.back = subprocess.Popen([sys.executable, "-I", "-c", WIN, "back", "200", "200", "600", "400"],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, creationflags=0x08000000)
        cls.back_hwnd = int(cls.back.stdout.readline())
        real = REAL["wt_states"]
        REAL["wt_states"] = lambda: [s for s in real() if s[0] in cls.mine]  # never anyone else's terminal
        REAL["TIDY_WAIT"] = 0.3

    @classmethod
    def tearDownClass(cls):
        cls.back.stdin.close()
        cls.back.stdout.close()
        cls.back.wait(5)
        for h, pid in zip(cls.mine, cls.pids):
            u.PostMessageW(h, 0x0010, 0, 0)  # WM_CLOSE: its only tab, the sleeping Python
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
        for w in cls.wts:
            w.wait(5)
        time.sleep(0.5)
        shutil.rmtree(cls.dir, ignore_errors=True)
        after = terminals()
        assert {h: after.get(h) for h in cls.others} == cls.others, "another Windows Terminal window changed"
        assert not set(after) & set(cls.mine), "a throwaway window was left"

    def setUp(self):
        SETTINGS.clear()

    def shown(self, h):
        return bool(u.IsWindowVisible(h)) and not u.IsIconic(h)

    def in_tray(self, h):
        return not u.IsWindowVisible(h) and bool(u.IsIconic(h))

    def front(self, h):
        REAL["bring_front"](h)
        self.assertTrue(wait(lambda: u.GetForegroundWindow() == h and self.shown(h)), "not in front")

    def tidy(self, h):
        p = panel(REAL)
        p.tidy_gen = 1
        return p.tidy(h, 1)

    def test_1_others_go_to_the_notification_area_and_nothing_loops(self):
        a, b, c = self.mine
        for h in (c, b):
            self.front(h)
        self.front(a)
        self.assertTrue(self.shown(b) and self.shown(c))
        hook = Foreground()
        try:
            self.assertTrue(self.tidy(a))
            self.assertTrue(wait(lambda: self.in_tray(b) and self.in_tray(c)), "not in the notification area")
            time.sleep(0.8)
            self.assertEqual(hook.seen, [])  # no foreground change: nothing for the hook to tidy again
        finally:
            hook.stop()
        self.assertTrue(self.shown(a))
        self.assertEqual(u.GetForegroundWindow(), a)

    def test_2_one_brought_back_sends_the_front_one_away(self):
        a, b, c = self.mine
        self.front(b)  # a card click: restored from the notification area
        self.assertTrue(self.tidy(b))
        self.assertTrue(wait(lambda: self.in_tray(a)))
        time.sleep(0.3)
        self.assertTrue(self.in_tray(c))  # left alone: not put on the taskbar
        self.assertTrue(self.shown(b))
        self.assertEqual(u.GetForegroundWindow(), b)

    def test_3_setting_off(self):
        a, b, c = self.mine
        self.front(c)
        SETTINGS["minimize_other_terminals"] = False
        self.assertFalse(self.tidy(c))
        time.sleep(0.3)
        self.assertTrue(self.shown(b) and self.shown(c))

    def test_4_other_window_in_front(self):
        a, b, c = self.mine
        self.assertFalse(REAL["wt_window"](self.back_hwnd))  # never a trigger
        self.front(self.back_hwnd)
        self.assertFalse(self.tidy(c))  # c came forward but the plain window took the front before TIDY_WAIT
        time.sleep(0.3)
        self.assertTrue(self.shown(b) and self.shown(c))

    def test_5_through_the_hook_as_the_panel_does(self):
        a, b, c = self.mine
        p = panel(REAL)
        hook = Foreground(lambda h: REAL["wt_window"](h) and h in self.mine and p.tidy_soon(h))
        try:
            self.front(a)
            self.assertTrue(wait(lambda: self.in_tray(b) and self.in_tray(c), 4), "not tidied")
            time.sleep(1.0)
            self.assertEqual(hook.seen[-1], a)  # the last foreground change is the one that came forward
            self.assertTrue(self.shown(a))
            self.assertEqual(u.GetForegroundWindow(), a)
        finally:
            hook.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2, exit=False)
    print("lone Alt needed (stubbed):", len(PRESSED))
