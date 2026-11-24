# Unit tests for the tray click bringing back the last session terminal: which terminal is remembered
# (note_terminal()), which one a tray click picks (last_terminal()), what the click does (Panel.tray_click()), that
# bring_front() restores a minimised window, and that only a left click on the icon (not the menu's Show or Hide)
# asks for it (tray()).
# Run: python -I tools/test-tray-front.py
# The .pyw is never imported (that would trip its one-instance mutex and raise the running panel): the functions are
# taken from it by ast and run against fake windows and a fake user32, so no window is raised or focused and no key
# is sent.
import ast, os, queue, sys, types, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")

with open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8") as f:
    TREE = ast.parse(f.read())


def take(names, ns, methods=()):
    """The module-level assignments and functions named, and the Panel methods named, run into namespace ns."""
    keep = [n for n in TREE.body if isinstance(n, ast.Assign) and any(getattr(t, "id", None) in names for t in n.targets)]
    keep += [n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name in names]
    for c in [n for n in TREE.body if isinstance(n, ast.ClassDef)]:
        keep += [n for n in c.body if isinstance(n, ast.FunctionDef) and n.name in methods]
    exec(compile(ast.Module(body=keep, type_ignores=[]), "agent-panel.pyw", "exec"), ns)
    return ns


class User32:
    """A fake user32: windows that exist, which are minimised, the foreground; every call is recorded."""

    def __init__(self, windows=(), iconic=(), fg=None):
        self.windows, self.iconic, self.fg, self.calls = set(windows), set(iconic), fg, []

    def IsWindow(self, h):
        return h in self.windows

    def IsIconic(self, h):
        return h in self.iconic

    def ShowWindow(self, h, cmd):
        self.calls.append(("ShowWindow", h, cmd))
        if cmd == 9:
            self.iconic.discard(h)

    def SetForegroundWindow(self, h):
        self.calls.append(("SetForegroundWindow", h))
        self.fg = h

    def GetForegroundWindow(self):
        return self.fg

    def SetWindowPos(self, h, after, *a):
        self.calls.append(("SetWindowPos", h, after))


ns = take({"RECENT_TERMINALS", "note_terminal", "last_terminal", "bring_front"}, {}, ("tray_click",))
note_terminal, last_terminal, RECENT_TERMINALS = ns["note_terminal"], ns["last_terminal"], ns["RECENT_TERMINALS"]

TERM, TERM2, TERM3, CHROME, TASKBAR = 10, 11, 12, 20, 30
SESSIONS = {TERM, TERM2, TERM3}


class Remembered(unittest.TestCase):
    def test_terminal_in_front_goes_first(self):
        r = note_terminal([], TERM, SESSIONS)
        r = note_terminal(r, TERM2, SESSIONS)
        self.assertEqual(r, [TERM2, TERM])

    def test_other_windows_leave_the_list(self):
        r = note_terminal([TERM2, TERM], CHROME, SESSIONS)
        r = note_terminal(r, TASKBAR, SESSIONS)
        r = note_terminal(r, None, SESSIONS)
        self.assertEqual(r, [TERM2, TERM])

    def test_back_to_an_earlier_terminal_moves_it_up_once(self):
        self.assertEqual(note_terminal([TERM2, TERM, TERM3], TERM, SESSIONS), [TERM, TERM2, TERM3])

    def test_kept_short(self):
        wins = set(range(100, 120))
        r = []
        for h in range(100, 120):
            r = note_terminal(r, h, wins)
        self.assertEqual(len(r), RECENT_TERMINALS)
        self.assertEqual(r[0], 119)


class Picked(unittest.TestCase):
    def test_last_foreground_terminal(self):
        self.assertEqual(last_terminal([TERM2, TERM], {TERM, TERM2}.__contains__), TERM2)

    def test_closed_window_takes_the_next_that_exists(self):
        self.assertEqual(last_terminal([TERM2, TERM, TERM3], {TERM3, TERM}.__contains__), TERM)

    def test_minimised_window_still_counts(self):
        u = User32(windows={TERM2, TERM}, iconic={TERM2})
        self.assertEqual(last_terminal([TERM2, TERM], u.IsWindow), TERM2)

    def test_none(self):
        self.assertIsNone(last_terminal([], lambda h: True))
        self.assertIsNone(last_terminal([TERM, TERM2], lambda h: False))


class BringFront(unittest.TestCase):
    """bring_front() with a fake user32: restore only when minimised, then the foreground and the top."""

    def run_on(self, u, h):
        ns["user32"], ns["press"] = u, lambda *k: self.fail("no key may be sent when the foreground is granted")
        return ns["bring_front"](h)

    def test_minimised_is_restored(self):
        u = User32(windows={TERM}, iconic={TERM}, fg=TASKBAR)
        self.assertTrue(self.run_on(u, TERM))
        self.assertEqual(u.calls[0], ("ShowWindow", TERM, 9))  # SW_RESTORE, never maximised
        self.assertIn(("SetForegroundWindow", TERM), u.calls)

    def test_shown_window_is_not_resized(self):
        u = User32(windows={TERM}, fg=TASKBAR)
        self.assertTrue(self.run_on(u, TERM))
        self.assertFalse([c for c in u.calls if c[0] == "ShowWindow"])


class Panel:
    """A stand-in for the panel: what tray_click() reads and calls."""

    def __init__(self, terminals, wins, visible_after=True):
        self.terminals, self.wins, self.visible, self.log = terminals, wins, False, []
        self.visible_after = visible_after

    def toggle(self):
        self.log.append("toggle")
        self.visible = self.visible_after

    def lift(self, h):
        self.log.append(("lift", h))


class TrayClick(unittest.TestCase):
    def click(self, panel, u):
        ns["user32"] = u
        ns["bring_front"] = lambda h: panel.log.append(("bring_front", h))
        ns["tray_click"](panel)
        return panel.log

    def test_panel_first_then_the_last_terminal(self):
        p = Panel([TERM2, TERM], {1: TERM, 2: TERM2})
        self.assertEqual(self.click(p, User32(windows={TERM, TERM2})), ["toggle", ("bring_front", TERM2), ("lift", TERM2)])

    def test_closed_terminal_skipped(self):
        p = Panel([TERM2, TERM], {1: TERM, 2: TERM2})
        self.assertEqual(self.click(p, User32(windows={TERM})), ["toggle", ("bring_front", TERM), ("lift", TERM)])

    def test_terminal_whose_session_ended_skipped(self):
        p = Panel([TERM2, TERM], {1: TERM})  # TERM2 still exists, but shows no open session any more
        self.assertEqual(self.click(p, User32(windows={TERM, TERM2})), ["toggle", ("bring_front", TERM), ("lift", TERM)])

    def test_minimised_terminal_handed_to_bring_front(self):
        p = Panel([TERM], {1: TERM})
        self.assertEqual(self.click(p, User32(windows={TERM}, iconic={TERM})), ["toggle", ("bring_front", TERM), ("lift", TERM)])

    def test_no_terminal_only_the_panel(self):
        p = Panel([], {})
        self.assertEqual(self.click(p, User32()), ["toggle"])

    def test_panel_hidden_by_the_click_brings_nothing(self):
        p = Panel([TERM], {1: TERM}, visible_after=False)
        self.assertEqual(self.click(p, User32(windows={TERM})), ["toggle"])


class TrayIcon(unittest.TestCase):
    """tray() with a fake pystray: a left click (pystray calls the icon) asks for "tray", the menu's Show or Hide
    still for "toggle"."""

    def test_left_click_and_menu(self):
        class Icon:
            def __init__(self, name, icon, title, menu=None):
                self.menu = menu

            def run_detached(self):
                pass

        class MenuItem:
            def __init__(self, text, action, **kw):
                self.text, self.action = text, action

        fake = types.ModuleType("pystray")
        fake.Icon, fake.MenuItem, fake.Menu = Icon, MenuItem, lambda *items: list(items)
        util = types.ModuleType("pystray._util")
        util.win32 = None
        saved = {k: sys.modules.get(k) for k in ("pystray", "pystray._util")}
        sys.modules["pystray"], sys.modules["pystray._util"] = fake, util
        try:
            t = take({"tray"}, {"tray_key": lambda: 1, "tray_look": lambda k: (None, "tip")})
            events = queue.Queue()
            icon = t["tray"](events, {"dock": "right", "alerts": True, "sound": True})
            icon()
            self.assertEqual(events.get_nowait(), "tray")
            [show] = [m for m in icon.menu if m.text.startswith("Show or Hide")]
            show.action()
            self.assertEqual(events.get_nowait(), "toggle")
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v


if __name__ == "__main__":
    unittest.main()
