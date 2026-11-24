# Tests for a minimised session terminal leaving the Field Agents panel on screen: follow_front() hooks
# EVENT_SYSTEM_MINIMIZESTART; on_min() acts only for a session window the panel rides under while it shows;
# stay_on_screen() waits for the window Windows makes the foreground next and puts the panel over it (surface(),
# not activated), unless the panel itself or another session window came forward or the user moved on.
# Run: python -I tools/test-minimise.py            (fake user32: no window is touched)
#      python -I tools/test-minimise.py --windows  (also real throwaway windows in processes of their own: a "back"
#                                                   window, a "terminal" over it and a tool-window "panel"; only
#                                                   these are activated and minimised, no key is sent)
# The .pyw is never imported (that would trip its one-instance mutex and raise the running panel): the functions are
# taken from it by ast.
import ast, os, subprocess, sys, time, types, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
WINDOWS = "--windows" in sys.argv
if WINDOWS:
    sys.argv.remove("--windows")

with open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8") as f:
    TREE = ast.parse(f.read())


def take(names, ns, methods=()):
    """The module-level assignments and functions named, and the Panel methods named, run into namespace ns."""
    keep = [n for n in TREE.body if isinstance(n, ast.Assign) and any(getattr(t, "id", None) in names for t in n.targets)]
    keep += [n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name in names]
    for c in [n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "Panel"]:
        keep += [n for n in c.body if isinstance(n, ast.FunctionDef) and n.name in methods]
    exec(compile(ast.Module(body=keep, type_ignores=[]), "agent-panel.pyw", "exec"), ns)
    return ns


METHODS = ("follow_front", "stay_on_screen", "over", "surface", "beneath")
ns = take({"LIFT_AGAIN", "MIN_FRONT_WAIT"}, {}, METHODS)

TERM, TERM2, BACK, OTHER, PANEL = 10, 11, 20, 21, 40


class User32:
    """A fake user32: a z-order (top first), the foreground and its changes over time; calls recorded."""

    def __init__(self, z, fg, later=()):
        self.z, self.fg, self.later, self.calls = list(z), fg, list(later), []

    def GetForegroundWindow(self):
        if self.later and self.later[0][0] <= len([c for c in self.calls if c[0] == "fg?"]):
            self.fg = self.later.pop(0)[1]
        self.calls.append(("fg?",))
        return self.fg

    def GetWindow(self, h, cmd):
        i = self.z.index(h) + 1
        return self.z[i] if cmd == 2 and i < len(self.z) else 0

    def IsWindowVisible(self, h):
        return True

    def SetWindowPos(self, h, after, *a):
        self.calls.append(("SetWindowPos", h, after))
        if after == -1:
            self.z.remove(h)
            self.z.insert(0, h)


def panel(u, **kw):
    p = types.SimpleNamespace(hwnd=PANEL, visible=True, dragging=False, wins={1: TERM, 2: TERM2})
    p.__dict__.update(kw)
    for m in METHODS:
        setattr(p, m, types.MethodType(ns[m], p))
    return p


def fake(u):
    ns.update(user32=u, own=lambda h: h == PANEL, time=types.SimpleNamespace(time=time.time, sleep=lambda s: None))


class StayOnScreen(unittest.TestCase):
    def raised(self, u):
        return [c for c in u.calls if c[0] == "SetWindowPos"]

    def test_next_window_raised_over_the_panel_gets_the_panel_over_it(self):
        u = User32([BACK, TERM, PANEL, OTHER], fg=BACK)
        fake(u)
        panel(u).stay_on_screen(TERM)
        self.assertEqual(self.raised(u), [("SetWindowPos", PANEL, -1), ("SetWindowPos", PANEL, -2)])
        self.assertEqual(u.z[0], PANEL)

    def test_waits_for_the_terminal_to_hand_the_foreground_on(self):
        u = User32([TERM, PANEL, BACK], fg=TERM, later=[(3, BACK)])
        fake(u)
        p = panel(u)

        def gw(_u=u):
            n = len([c for c in _u.calls if c[0] == "fg?"])
            if n == 3:
                _u.z = [BACK, TERM, PANEL]  # Windows raises the next window as it takes the foreground
            return User32.GetForegroundWindow(_u)
        u.GetForegroundWindow = gw
        p.stay_on_screen(TERM)
        self.assertEqual(u.z[0], PANEL)

    def test_panel_already_over_it_is_left_alone(self):
        u = User32([PANEL, BACK, TERM], fg=BACK)
        fake(u)
        panel(u).stay_on_screen(TERM)
        self.assertEqual(self.raised(u), [])

    def test_another_session_window_in_front_is_left_to_lift(self):
        u = User32([TERM2, TERM, PANEL], fg=TERM2)
        fake(u)
        panel(u).stay_on_screen(TERM)
        self.assertEqual(self.raised(u), [])

    def test_panel_in_front_does_nothing(self):
        u = User32([PANEL, TERM, BACK], fg=PANEL)  # the second card click minimised it from the panel
        fake(u)
        panel(u).stay_on_screen(TERM)
        self.assertEqual(self.raised(u), [])

    def test_user_moved_on_stops(self):
        u = User32([BACK, TERM, PANEL, OTHER], fg=BACK, later=[(2, OTHER)])
        fake(u)
        panel(u).stay_on_screen(TERM)
        self.assertEqual(self.raised(u), [])

    def test_hidden_panel_stays_hidden(self):
        u = User32([BACK, TERM, PANEL], fg=BACK)
        fake(u)
        panel(u, visible=False).stay_on_screen(TERM)
        self.assertEqual(self.raised(u), [])


class Hook(unittest.TestCase):
    def setUp(self):
        self.hooks, self.started = {}, []
        u = User32([BACK, TERM, PANEL, OTHER], fg=BACK)
        u.UnhookWinEvent = lambda h: None
        ns.update(user32=u, own=lambda h: h == PANEL, WINEVENTPROC=lambda f: f,
                  threading=types.SimpleNamespace(Thread=lambda target, args, daemon: types.SimpleNamespace(
                      start=lambda: self.started.append((target.__name__, args)))),
                  SetWinEventHook=self.hook,
                  ctypes=types.SimpleNamespace(byref=lambda x: x),
                  wt=types.SimpleNamespace(MSG=lambda: None), time=__import__("time"))
        ns["user32"].GetMessageW = lambda *a: 0
        self.u = u

    def hook(self, lo, hi, mod, proc, *a):
        self.hooks[lo] = proc
        return 1

    def run_hook(self, **kw):
        p = panel(self.u, **kw)
        ns["follow_front"](p)
        return p

    def test_minimise_hook_registered(self):
        self.run_hook()
        self.assertIn(0x16, self.hooks)

    def test_session_window_the_panel_rides_under(self):
        self.run_hook()
        self.hooks[0x16](None, 0x16, TERM, 0, 0, 0, 0)
        self.assertEqual(self.started, [("stay_on_screen", (TERM,))])

    def test_window_not_a_session_ignored(self):
        self.u.z = [BACK, OTHER, PANEL]
        self.run_hook()
        self.hooks[0x16](None, 0x16, OTHER, 0, 0, 0, 0)
        self.assertEqual(self.started, [])

    def test_session_window_the_panel_was_not_under_ignored(self):
        self.u.z = [TERM, BACK, PANEL]  # the panel was covered already: nothing to keep
        self.run_hook()
        self.hooks[0x16](None, 0x16, TERM, 0, 0, 0, 0)
        self.assertEqual(self.started, [])

    def test_hidden_panel_ignored(self):
        self.run_hook(visible=False)
        self.hooks[0x16](None, 0x16, TERM, 0, 0, 0, 0)
        self.assertEqual(self.started, [])

    def test_child_object_ignored(self):
        self.run_hook()
        self.hooks[0x16](None, 0x16, TERM, -4, 0, 0, 0)
        self.assertEqual(self.started, [])


WIN = r'''
import ctypes, ctypes.wintypes as wt, sys, threading
u = ctypes.windll.user32; k = ctypes.windll.kernel32
P = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
u.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]; u.DefWindowProcW.restype = ctypes.c_ssize_t
proc = P(lambda h, m, w, l: u.DefWindowProcW(h, m, w, l))
class C(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("p", P), ("a", ctypes.c_int), ("b", ctypes.c_int), ("i", wt.HINSTANCE),
                ("ic", wt.HICON), ("cu", wt.HANDLE), ("br", wt.HBRUSH), ("m", wt.LPCWSTR), ("n", wt.LPCWSTR)]
c = C(p=proc, i=k.GetModuleHandleW(None), n="ClaudePlusFAMinTest", br=6)
u.RegisterClassW(ctypes.byref(c))
u.CreateWindowExW.restype = wt.HWND
u.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD] + [ctypes.c_int] * 4 + [wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
u.AllowSetForegroundWindow(0xFFFFFFFF)
ex, style = (0x80 | 0x8000000, 0x80000000) if sys.argv[1] == "panel" else (0, 0x00CF0000)
h = u.CreateWindowExW(ex, "ClaudePlusFAMinTest", "claude-plus-fa minimise test " + sys.argv[1], style | 0x10000000,
                      *map(int, sys.argv[2:6]), None, None, None, None)
if sys.argv[1] != "panel":
    u.SetForegroundWindow(h)
print(h, flush=True)
threading.Thread(target=lambda: (sys.stdin.read(), k.ExitProcess(0)), daemon=True).start()
m = wt.MSG()
while u.GetMessageW(ctypes.byref(m), None, 0, 0) > 0:
    u.TranslateMessage(ctypes.byref(m)); u.DispatchMessageW(ctypes.byref(m))
'''


@unittest.skipUnless(WINDOWS, "real windows only with --windows")
class RealWindows(unittest.TestCase):
    """Three processes as on the desktop: a back window (Chrome), a terminal over it, the panel right under the
    terminal. The terminal is minimised as by its caption button; the panel's methods run in this fourth process,
    as the panel is never the process in front."""

    def spawn(self, *a):
        p = subprocess.Popen([sys.executable, "-I", "-c", WIN, *map(str, a)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True)
        self.procs.append(p)
        return int(p.stdout.readline())

    def setUp(self):
        import ctypes, ctypes.wintypes as wt
        self.procs = []
        u = ctypes.windll.user32
        u.SetWindowPos.argtypes = [wt.HWND, ctypes.c_ssize_t] + [ctypes.c_int] * 4 + [wt.UINT]
        u.GetWindow.restype = u.GetForegroundWindow.restype = wt.HWND
        self.u = u
        self.back = self.spawn("back", 100, 100, 1200, 700)
        time.sleep(0.4)
        self.panel = self.spawn("panel", 1100, 50, 250, 700)
        self.term = self.spawn("term", 200, 200, 600, 400)
        time.sleep(0.4)
        u.ShowWindow(self.panel, 4)  # SW_SHOWNOACTIVATE
        u.SetWindowPos(self.panel, self.term, 0, 0, 0, 0, 0x13)  # lift(): right under the terminal
        time.sleep(0.2)
        ns.update(user32=u, own=lambda h: h == self.panel, time=time)

    def tearDown(self):
        for p in self.procs:
            p.stdin.close()
            p.stdout.close()
            p.wait(5)

    def above(self, a, b):
        h = self.u.GetWindow(a, 2)
        while h:
            if h == b:
                return True
            h = self.u.GetWindow(h, 2)
        return False

    def minimise(self):
        self.assertEqual(self.u.GetForegroundWindow(), self.term)
        self.assertTrue(self.above(self.term, self.panel) and self.above(self.panel, self.back))
        self.u.PostMessageW(self.term, 0x0112, 0xF020, 0)  # WM_SYSCOMMAND SC_MINIMIZE, as the caption button

    def test_without_the_fix_the_back_window_covers_the_panel(self):
        self.minimise()
        time.sleep(0.6)
        self.assertEqual(self.u.GetForegroundWindow(), self.back)
        self.assertTrue(self.above(self.back, self.panel))

    def test_panel_stays_over_the_window_that_comes_forward(self):
        p = types.SimpleNamespace(hwnd=self.panel, visible=True, dragging=False, wins={1: self.term})
        for m in METHODS:
            setattr(p, m, types.MethodType(ns[m], p))
        self.minimise()
        p.stay_on_screen(self.term)
        self.assertTrue(self.u.IsIconic(self.term))
        self.assertEqual(self.u.GetForegroundWindow(), self.back)  # the keyboard went where Windows put it
        self.assertTrue(self.above(self.panel, self.back))


if __name__ == "__main__":
    unittest.main()
