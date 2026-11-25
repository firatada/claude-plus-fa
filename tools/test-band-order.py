# Tests for the band (the panel with the strip and the reply window it owns) in z-order against a session's
# terminal: a terminal in front is never covered by the band (Panel.keep_under(), each poll turn, through covers() and
# lift()); the band comes over it only while one of its own windows is in front (a click on the reply window).
# Run: python -I tools/test-band-order.py            (fake user32: no window is touched)
#      python -I tools/test-band-order.py --windows  (also real throwaway windows in processes of their own: a "panel"
#                                                     owning a "strip" and a "reply" window, a "terminal" and a "back"
#                                                     window; only these are raised or activated, no key is sent, the
#                                                     mouse is not used)
# The .pyw is never imported (that would trip its one-instance mutex and raise the running panel): the functions are
# taken from it by ast.
import ast, ctypes, os, subprocess, sys, time, types, unittest

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


METHODS = ("keep_under", "covers", "lift", "beneath", "surface", "place_reply")
ns = take({"REPLY_BELOW", "bring_front"}, {"math": __import__("math")}, METHODS)

TERM, TERM2, BACK, PANEL, STRIP, REPLY, ALERT = 10, 11, 20, 40, 41, 42, 43
OWN = {PANEL, STRIP, REPLY, ALERT}


class User32:
    """A fake user32: a z-order (top first), which windows are topmost, minimised or hidden; SetWindowPos moves the
    panel and the windows it owns as Windows does (inserted after a window: right under it, owned ones above it)."""

    def __init__(self, z, topmost=(), iconic=(), hidden=()):
        self.z, self.top, self.iconic, self.hidden, self.calls = list(z), set(topmost), set(iconic), set(hidden), []

    def GetWindow(self, h, cmd):
        i = self.z.index(h) + (1 if cmd == 2 else -1)  # GW_HWNDNEXT / GW_HWNDPREV
        return self.z[i] if 0 <= i < len(self.z) else 0

    def IsWindowVisible(self, h):
        return h not in self.hidden

    def IsIconic(self, h):
        return h in self.iconic

    def GetWindowLongW(self, h, i):
        return 0x8 if i == -20 and h in self.top else 0

    def SetWindowPos(self, h, after, *a):
        self.calls.append(("SetWindowPos", h, after))
        group = [w for w in (REPLY, STRIP) if w in self.z] + [h] if h == PANEL else [h]
        for w in group:
            self.z.remove(w)
        at = 0 if after in (-1, -2) else self.z.index(after) + 1
        self.z[at:at] = group
        if h == PANEL and after not in (-1, -2) and after not in self.top:
            self.top -= set(group)  # after an ordinary window: no longer topmost, nor the windows it owns


def panel(u, **kw):
    p = types.SimpleNamespace(hwnd=PANEL, visible=True, dragging=False, wins={1: TERM, 2: TERM2})
    p.__dict__.update(kw)
    for m in METHODS:
        setattr(p, m, types.MethodType(ns[m], p))
    ns.update(user32=u, own=lambda h: h in OWN)
    return p


class KeepUnder(unittest.TestCase):
    def lifts(self, u):
        return [c for c in u.calls if c[0] == "SetWindowPos"]

    def test_band_over_the_terminal_in_front_goes_under_it(self):
        u = User32([REPLY, STRIP, PANEL, TERM, BACK])  # surfaced over it, the terminal still in front
        self.assertTrue(panel(u).keep_under(TERM))
        self.assertEqual(u.z, [TERM, REPLY, STRIP, PANEL, BACK])

    def test_reply_window_alone_over_the_terminal(self):
        u = User32([REPLY, TERM, STRIP, PANEL, BACK])
        self.assertTrue(panel(u).keep_under(TERM))
        self.assertEqual(u.z[0], TERM)

    def test_band_already_under_it_is_left_alone(self):
        u = User32([TERM, REPLY, STRIP, PANEL, BACK])
        self.assertFalse(panel(u).keep_under(TERM))
        self.assertEqual(self.lifts(u), [])

    def test_band_under_another_window_too_is_left_alone(self):
        u = User32([TERM, BACK, REPLY, STRIP, PANEL])  # nothing of the band over the terminal
        self.assertFalse(panel(u).keep_under(TERM))
        self.assertEqual(self.lifts(u), [])

    def test_reply_window_in_front_keeps_the_band_over_the_terminal(self):
        u = User32([REPLY, STRIP, PANEL, TERM, BACK])  # a click on the reply window: it is the window in front
        self.assertFalse(panel(u).keep_under(REPLY))
        self.assertEqual(u.z, [REPLY, STRIP, PANEL, TERM, BACK])

    def test_panel_in_front_keeps_the_band_over_the_terminal(self):
        u = User32([REPLY, STRIP, PANEL, TERM, BACK])
        self.assertFalse(panel(u).keep_under(PANEL))
        self.assertEqual(self.lifts(u), [])

    def test_other_window_in_front_is_left_alone(self):
        u = User32([REPLY, STRIP, PANEL, BACK, TERM])  # the band surfaced over the browser after a minimise
        self.assertFalse(panel(u).keep_under(BACK))
        self.assertEqual(self.lifts(u), [])

    def test_hidden_or_dragged_panel_is_left_alone(self):
        for kw in ({"visible": False}, {"dragging": True}):
            u = User32([REPLY, STRIP, PANEL, TERM])
            self.assertFalse(panel(u, **kw).keep_under(TERM))
            self.assertEqual(self.lifts(u), [])

    def test_topmost_terminal_waits(self):
        u = User32([TERM, REPLY, STRIP, PANEL], topmost={TERM})  # bring_front()'s moment: lift() would be refused
        self.assertFalse(panel(u).keep_under(TERM))

    def test_hidden_band_windows_do_not_count(self):
        u = User32([REPLY, STRIP, TERM, PANEL], hidden={REPLY, STRIP})
        self.assertFalse(panel(u).keep_under(TERM))

    def test_topmost_alert_over_the_terminal_is_meant_to_be(self):
        u = User32([ALERT, TERM, REPLY, STRIP, PANEL], topmost={ALERT})
        self.assertFalse(panel(u).keep_under(TERM))

    def test_panel_made_topmost_by_mistake_goes_under_and_loses_it(self):
        u = User32([REPLY, STRIP, PANEL, TERM, BACK], topmost={REPLY, STRIP, PANEL})
        self.assertTrue(panel(u).keep_under(TERM))
        self.assertEqual(u.z[0], TERM)
        self.assertEqual(u.top, set())

    def test_no_window_in_front(self):
        u = User32([REPLY, STRIP, PANEL, TERM])
        self.assertFalse(panel(u).keep_under(None))


class PollLoop(unittest.TestCase):
    def test_every_turn_checks_the_window_in_front(self):
        with open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8") as f:
            src = f.read()
        turn = src[src.index("fg = user32.GetForegroundWindow()\n            self.keep_under(fg)"):]
        self.assertIn("self.events.get(timeout=0.2)", turn[:1200])


WIN = r'''
import ctypes, ctypes.wintypes as wt, sys, threading
u = ctypes.windll.user32; k = ctypes.windll.kernel32
P = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
u.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]; u.DefWindowProcW.restype = ctypes.c_ssize_t
proc = P(lambda h, m, w, l: u.DefWindowProcW(h, m, w, l))
class C(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("p", P), ("a", ctypes.c_int), ("b", ctypes.c_int), ("i", wt.HINSTANCE),
                ("ic", wt.HICON), ("cu", wt.HANDLE), ("br", wt.HBRUSH), ("m", wt.LPCWSTR), ("n", wt.LPCWSTR)]
c = C(p=proc, i=k.GetModuleHandleW(None), n="ClaudePlusFABandTest", br=6)
u.RegisterClassW(ctypes.byref(c))
u.CreateWindowExW.restype = wt.HWND
u.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD] + [ctypes.c_int] * 4 + [wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
u.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
def make(name, ex, style, x, y, w, h):
    return u.CreateWindowExW(ex, "ClaudePlusFABandTest", "claude-plus-fa band test " + name, style, x, y, w, h,
                             None, None, None, None)
if sys.argv[1] == "panel":  # as the real band: tool windows; the strip never activates, the reply window does
    hs = [make("panel", 0x80, 0x80000000, 1300, 100, 250, 600), make("strip", 0x80 | 0x8000000, 0x80000000, 700, 100, 500, 100),
          make("reply", 0x80, 0x80000000, 300, 100, 380, 600)]
    for h in hs[1:]:
        u.SetWindowLongPtrW(h, -8, hs[0])  # GWLP_HWNDPARENT: owned by the panel
    for h in hs:
        u.ShowWindow(h, 4)  # SW_SHOWNOACTIVATE
else:
    hs = [make(sys.argv[1], 0, 0x00CF0000 | 0x10000000, int(sys.argv[2]), 150, 1000, 500)]
print(*hs, flush=True)
threading.Thread(target=lambda: (sys.stdin.read(), k.ExitProcess(0)), daemon=True).start()
m = wt.MSG()
while u.GetMessageW(ctypes.byref(m), None, 0, 0) > 0:
    u.TranslateMessage(ctypes.byref(m)); u.DispatchMessageW(ctypes.byref(m))
'''


@unittest.skipUnless(WINDOWS, "real windows only with --windows")
class RealWindows(unittest.TestCase):
    """The band, a terminal and a back window, each in a process of its own as on the desktop; the panel's methods run
    in this fourth process, as the poll loop and the hooks do in the panel's."""

    def spawn(self, *a):
        p = subprocess.Popen([sys.executable, "-I", "-c", WIN, *map(str, a)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True)
        self.procs.append(p)
        return [int(x) for x in p.stdout.readline().split()]

    def setUp(self):
        import ctypes.wintypes as wt
        self.procs = []
        u = ctypes.windll.user32
        u.SetWindowPos.argtypes = [wt.HWND, ctypes.c_ssize_t] + [ctypes.c_int] * 4 + [wt.UINT]
        u.GetWindow.restype = u.GetForegroundWindow.restype = wt.HWND
        u.GetWindow.argtypes = [wt.HWND, wt.UINT]
        u.SwitchToThisWindow.argtypes = [wt.HWND, wt.BOOL]
        self.u = u
        (self.back,) = self.spawn("back", 100)
        self.panel_, self.strip, self.reply = self.spawn("panel")
        (self.term,) = self.spawn("terminal", 250)
        time.sleep(0.3)
        band = {self.panel_, self.strip, self.reply}
        ns.update(user32=u, own=lambda h: h in band, press=lambda *k: None)  # never a key: no Alt for the foreground
        self.p = types.SimpleNamespace(hwnd=self.panel_, visible=True, dragging=False, wins={1: self.term})
        for m in METHODS:
            setattr(self.p, m, types.MethodType(ns[m], self.p))

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

    def term_over_band(self):
        return all(self.above(self.term, w) for w in (self.reply, self.strip, self.panel_))

    def test_card_click_puts_the_terminal_over_the_reply_window_and_the_strip(self):
        self.p.surface()  # the panel clicked: the band over everything
        self.assertTrue(self.above(self.reply, self.term) and self.above(self.strip, self.term))
        ns["bring_front"](self.term)  # click(): focus_session() raises the session's window, then lift()
        self.p.lift(self.term)
        time.sleep(0.1)
        self.assertTrue(self.term_over_band())
        self.assertTrue(self.p.beneath(self.term))

    def test_strip_and_head_resizing_leave_the_terminal_over_them(self):
        ns["bring_front"](self.term)
        self.p.lift(self.term)
        time.sleep(0.1)
        self.assertTrue(self.term_over_band())
        # update_strip(): the strip placed and shown again as its rows or its card height change
        for w, h in ((500, 100), (620, 180), (500, 100)):
            self.u.SetWindowPos(self.strip, 0, 700, 100, w, h, 0x0004 | 0x0010 | 0x0040)
        # place_reply(): the reply window as tall as its page reports (a new head height), then hidden and shown again
        r = self.p
        r.reply_ready, r.reply_shown, r.reply_hwnd, r.reply_now = True, True, self.reply, None
        r.head_key, r.reply_sid, r.reply_box = {"tabs": []}, "s", (300, 100, 380)
        r.scale = lambda: 1.0
        r.work_area = lambda rect: types.SimpleNamespace(bottom=1000)
        for h in (300, 640, 120):
            r.reply_h = h
            r.place_reply()
        self.u.ShowWindow(self.reply, 0)
        r.reply_now = None
        r.place_reply()
        time.sleep(0.1)
        self.assertTrue(self.u.IsWindowVisible(self.reply))
        self.assertTrue(self.term_over_band())
        self.assertFalse(r.keep_under(self.term))  # nothing to do

    def test_band_raised_while_the_terminal_stays_in_front_goes_back_under_it(self):
        ns["bring_front"](self.term)
        self.p.lift(self.term)
        self.p.surface()  # whatever raised it (stay_on_screen() before this fix): the band over the terminal
        time.sleep(0.1)
        self.assertFalse(self.term_over_band())
        self.assertTrue(self.p.keep_under(self.term))  # the next poll turn, the terminal in front
        time.sleep(0.1)
        self.assertTrue(self.term_over_band())

    def test_click_on_the_reply_window_brings_it_over_the_terminal(self):
        ns["bring_front"](self.term)
        self.p.lift(self.term)
        time.sleep(0.1)
        self.u.SwitchToThisWindow(self.reply, True)  # activated as a click activates it
        time.sleep(0.3)
        fg = self.u.GetForegroundWindow()
        if fg != self.reply:
            self.skipTest("Windows refused the reply window the foreground here")
        self.assertFalse(self.p.keep_under(fg))  # the poll turn leaves it
        self.assertTrue(self.above(self.reply, self.term))
        self.assertTrue(self.above(self.panel_, self.term))  # the band comes up with it


if __name__ == "__main__":
    unittest.main()
