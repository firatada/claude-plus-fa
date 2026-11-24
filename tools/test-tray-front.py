# Unit tests for the tray click bringing back the last session terminal: which terminal is remembered
# (note_terminal(), at once as a window comes forward: follow_front()), which one a tray click picks
# (last_terminal()), what the click does (Panel.tray_click()), that a click the user moved on from (a card clicked
# before the poll loop took it) is dropped (tray_stale()), that bring_front() restores a minimised window, and that
# only a left click on the icon (not the menu's Show or Hide) asks for it, with its time and the window then in front
# (tray()).
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


ns = take({"RECENT_TERMINALS", "note_terminal", "last_terminal", "bring_front", "tray_stale", "put_away"}, {},
          ("tray_click", "follow_front"))
note_terminal, last_terminal, RECENT_TERMINALS = ns["note_terminal"], ns["last_terminal"], ns["RECENT_TERMINALS"]
tray_stale, put_away = ns["tray_stale"], ns["put_away"]

TERM, TERM2, TERM3, CHROME, TASKBAR, PANEL = 10, 11, 12, 20, 30, 40
OWN = {PANEL}  # the panel's own windows
ns["own"] = OWN.__contains__
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

    def __init__(self, terminals, wins, visible_after=True, used=0.0):
        self.terminals, self.wins, self.visible, self.log = terminals, wins, False, []
        self.visible_after, self.used = visible_after, used

    def toggle(self):
        self.log.append("toggle")
        self.visible = self.visible_after

    def lift(self, h):
        self.log.append(("lift", h))


class TrayClick(unittest.TestCase):
    def click(self, panel, u, at=100.0, before=CHROME):
        ns["user32"] = u
        ns["bring_front"] = lambda h: panel.log.append(("bring_front", h))
        ns["tray_click"](panel, at, before)
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


class Stale(unittest.TestCase):
    """tray_stale(): a tray click the poll loop takes after the user has moved on is dropped."""

    def test_taken_at_once(self):
        self.assertFalse(tray_stale(100.0, 99.0, False, False))

    def test_card_clicked_after_the_tray(self):
        self.assertTrue(tray_stale(100.0, 100.4, False, False))

    def test_panel_in_front_by_the_card_click_before_its_call(self):
        self.assertTrue(tray_stale(100.0, 99.0, True, False))

    def test_panel_in_front_at_the_click_still_toggles(self):
        self.assertFalse(tray_stale(100.0, 99.0, True, True))  # the toggle hides it, as before


class TrayThenCard(unittest.TestCase):
    """The reported sequence: a tray click (panel and terminal TERM come forward), then at once a click on TERM2's
    card. However late the poll loop takes the tray click, TERM2 stays in front."""

    def tray(self, panel, fg, at=100.0, before=CHROME):
        u = User32(windows={TERM, TERM2}, fg=fg)
        ns["user32"] = u
        ns["bring_front"] = lambda h: panel.log.append(("bring_front", h))
        ns["tray_click"](panel, at, before)
        return panel.log

    def test_taken_after_the_card_brought_its_terminal(self):
        p = Panel([TERM], {1: TERM, 2: TERM2}, used=100.5)  # the card's click() came after the tray click
        self.assertEqual(self.tray(p, fg=TERM2), [])  # TERM is not raised over TERM2, the panel not toggled

    def test_taken_between_the_card_press_and_its_call(self):
        p = Panel([TERM], {1: TERM, 2: TERM2})  # the press made the panel the foreground, the hook not heard yet
        self.assertEqual(self.tray(p, fg=PANEL), [])  # not hidden under the pointer

    def test_taken_in_time_brings_the_terminal(self):
        p = Panel([TERM], {1: TERM, 2: TERM2}, used=90.0)
        self.assertEqual(self.tray(p, fg=CHROME), ["toggle", ("bring_front", TERM), ("lift", TERM)])

    def test_card_click_marks_the_panel_used(self):
        """Panel.click() (and open()) note the time first, so a tray click taken after it is stale."""
        c = take(set(), {"time": types.SimpleNamespace(time=lambda: 100.5)}, ("click", "open"))
        p = types.SimpleNamespace(used=0.0, find=lambda sid: None)
        c["click"](p, "x")
        self.assertEqual(p.used, 100.5)
        p.used = 0.0
        c["open"](p, "x")
        self.assertEqual(p.used, 100.5)
        self.assertTrue(tray_stale(100.0, p.used, False, False))

    def test_card_terminal_is_the_last_at_once(self):
        """follow_front(): a card click brings TERM2 forward; it is first in the list at once, so a tray click
        right after (before the next poll turn) brings TERM2, not TERM."""
        procs = []

        class P:
            visible, dragging, shots, wins, terminals, used, raised = True, False, None, {1: TERM, 2: TERM2}, [TERM], 0.0, None

            def lift(self, h):
                pass

            def stay_over(self, h):
                pass

        p = P()
        u = User32(windows={TERM, TERM2}, fg=CHROME)
        u.GetWindowThreadProcessId = lambda h, ref: 0
        u.UnhookWinEvent = lambda h: None
        ns.update(user32=u, WINEVENTPROC=lambda f: f, threading=types.SimpleNamespace(
            Thread=lambda target, args, daemon: types.SimpleNamespace(start=lambda: None)),
            SetWinEventHook=lambda *a: procs.append(a[3]) or 0, ctypes=types.SimpleNamespace(byref=lambda x: x),
            wt=types.SimpleNamespace(DWORD=lambda: types.SimpleNamespace(value=0)), time=__import__("time"))
        ns["follow_front"](p)  # the hook is refused (0): it returns without a message loop
        on_front = procs[0]
        on_front(None, 3, PANEL, 0, 0, 0, 0)  # the press on the card: the panel's own window
        self.assertGreater(p.used, 0)
        on_front(None, 3, TERM2, 0, 0, 0, 0)  # the card brought TERM2 forward
        self.assertEqual(p.terminals, [TERM2, TERM])


class CardAfterTray(unittest.TestCase):
    """The reported sequence: a tray click brings back the last terminal, then a click on that terminal's own card.
    It is the first click on that card: the window stays in front (raised again); minimised, it went to the taskbar
    and Windows handed the foreground to the browser under it, so no terminal was left on screen. Only a second
    click on the card, while the window its first click raised is still in front, puts it away. Panel.click() and
    the foreground hook (follow_front()) run on a fake user32; focus_session() only records."""

    SID = {TERM: "a", TERM2: "b", TERM3: "c"}

    def setUp(self):
        self.procs, self.raised_by = [], []
        u = User32(windows={TERM, TERM2, TERM3}, fg=CHROME)
        u.GetWindowThreadProcessId = lambda h, ref: 0
        u.UnhookWinEvent = lambda h: None
        self.u, self.top = u, CHROME
        c = take({"put_away", "note_terminal", "RECENT_TERMINALS"}, {}, ("click", "shown_in_front", "shared", "follow_front"))
        c.update(user32=u, own=OWN.__contains__, top_window=lambda: self.top, window_title=lambda h: "",
                 folder_exists=lambda s: True, ok=lambda t: t, err=lambda t: ("err", t),
                 focus_session=self.focus, WINEVENTPROC=lambda f: f, SetWinEventHook=lambda *a: self.procs.append(a[3]) or 0,
                 threading=types.SimpleNamespace(Thread=lambda target, args, daemon: types.SimpleNamespace(start=lambda: None)),
                 ctypes=types.SimpleNamespace(byref=lambda x: x),
                 wt=types.SimpleNamespace(DWORD=lambda: types.SimpleNamespace(value=0)), time=__import__("time"))
        sessions = {sid: {"id": sid, "status": "idle", "pid": h, "name": sid} for h, sid in self.SID.items()}
        p = types.SimpleNamespace(visible=True, dragging=False, shots=None, wins={h: h for h in self.SID}, terminals=[],
                                  used=0.0, raised=None, front=CHROME, returns={})
        p.find = sessions.get
        p.window_for = lambda s: s["pid"]
        p.other_names = lambda s: set()
        p.lift = p.stay_over = lambda h: None
        for m in ("click", "shown_in_front", "shared", "follow_front"):
            setattr(p, m, types.MethodType(c[m], p))
        p.follow_front()  # the hook is refused (0): it returns without a message loop
        self.p, self.on_front = p, self.procs[0]

    def focus(self, s, others=()):
        self.raised_by.append(s["pid"])
        self.come_forward(s["pid"])
        return True

    def come_forward(self, h):
        """Window h becomes the foreground and the top window, and the hook hears of it."""
        self.u.fg = h
        if h not in OWN:
            self.top = h
        self.on_front(None, 3, h, 0, 0, 0, 0)

    def card(self, h):
        """A click on the card of the session in window h: the press makes the panel the foreground first."""
        self.come_forward(PANEL)
        return self.p.click(self.SID[h])

    def minimised(self):
        return [c[1] for c in self.u.calls if c[0] == "ShowWindow" and c[2] == 6]

    def test_card_of_the_terminal_the_tray_brought_keeps_it_in_front(self):
        self.come_forward(TERM)    # the tray click's bring_front()
        self.card(TERM)
        self.assertEqual(self.minimised(), [])
        self.assertEqual(self.raised_by, [TERM])
        self.assertEqual(self.u.fg, TERM)

    def test_second_click_on_that_card_puts_it_away(self):
        self.come_forward(TERM)
        self.card(TERM)
        self.card(TERM)
        self.assertEqual(self.minimised(), [TERM])

    def test_third_click_brings_it_back(self):
        self.card(TERM)
        self.card(TERM)
        self.u.iconic.add(TERM)
        self.come_forward(CHROME)  # Windows hands the foreground on
        self.top = CHROME
        self.card(TERM)
        self.assertEqual(self.minimised(), [TERM])
        self.assertEqual(self.raised_by, [TERM, TERM])

    def test_terminal_windows_activated_after_a_minimise_then_the_tray_then_its_card(self):
        self.come_forward(TERM2)   # Windows made TERM2 the foreground when TERM was minimised
        self.come_forward(CHROME)  # the user went to the browser
        self.come_forward(TERM2)   # the tray brought TERM2 back as the last terminal
        self.card(TERM2)
        self.assertEqual(self.minimised(), [])
        self.assertEqual(self.u.fg, TERM2)

    def test_tray_terminal_then_another_card_brings_that_one(self):
        self.come_forward(TERM)
        self.card(TERM2)
        self.assertEqual(self.minimised(), [])
        self.assertEqual(self.raised_by, [TERM2])
        self.assertEqual(self.u.fg, TERM2)

    def test_raised_by_its_card_then_another_window_then_back_is_a_first_click(self):
        self.card(TERM)
        self.come_forward(CHROME)
        self.come_forward(TERM)    # back by the taskbar
        self.card(TERM)
        self.assertEqual(self.minimised(), [])

    def test_raised_by_another_card_is_a_first_click(self):
        self.card(TERM2)
        self.card(TERM)
        self.card(TERM2)
        self.assertEqual(self.minimised(), [])
        self.assertEqual(self.raised_by, [TERM2, TERM, TERM2])

    def test_covered_window_is_raised_not_minimised(self):
        self.card(TERM)
        self.top = CHROME          # another window covers it, the foreground unchanged
        self.card(TERM)
        self.assertEqual(self.minimised(), [])


class PutAway(unittest.TestCase):
    def test_rule(self):
        self.assertTrue(put_away(("a", TERM), "a", TERM, lambda: True))
        self.assertFalse(put_away(None, "a", TERM, lambda: True))
        self.assertFalse(put_away(("b", TERM), "a", TERM, lambda: True))   # another tab's card raised it
        self.assertFalse(put_away(("a", TERM2), "a", TERM, lambda: True))
        self.assertFalse(put_away(("a", TERM), "a", TERM, lambda: False))  # no longer in front
        self.assertFalse(put_away(("a", None), "a", None, lambda: True))


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
            t = take({"tray"}, {"tray_key": lambda: 1, "tray_look": lambda k: (None, "tip"),
                                "user32": User32(fg=CHROME), "time": types.SimpleNamespace(time=lambda: 123.25)})
            events = queue.Queue()
            icon = t["tray"](events, {"dock": "right", "alerts": True, "sound": True})
            icon()
            self.assertEqual(events.get_nowait(), "tray:123.250:%d" % CHROME)  # when, and the window in front then
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
