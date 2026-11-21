# Unit tests for the Agent Panel's link raise: which flashing window is brought to the front after a Ctrl+click in a
# session's terminal (link_raise() in agent-panel.pyw), and which keep flashing.
# Run: python -I tools/test-link-front.py
# The .pyw is never imported (that would trip its one-instance mutex and raise the running panel): link_raise() and
# LINK_WAIT are taken from it by ast and fed event sequences with fake windows, processes and times, so no window
# is touched, no process read, no key sent.
import ast, os, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")


def pyw(name):
    with open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    keep = [n for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "LINK_WAIT" for t in n.targets)]
    keep += [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name]
    ns = {}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "agent-panel.pyw", "exec"), ns)
    return ns


ns = pyw("link_raise")
link_raise, LINK_WAIT = ns["link_raise"], ns["LINK_WAIT"]

# windows -> their process
TERM, TERM2, PLAIN_TERM, CHROME, CODE, WSLG = 10, 11, 12, 20, 30, 50
OWNER = {TERM: 100, TERM2: 100, PLAIN_TERM: 100, CHROME: 200, CODE: 300, WSLG: 500}
SESSIONS = {TERM, TERM2}  # PLAIN_TERM: a window of the same Windows Terminal with no listed session
T0 = 1_000_000.0


class Seq:
    """One event sequence: the foreground window, the Ctrl+clicks seen (window, time), then flashes."""

    def __init__(self, fg=TERM, click=(TERM, T0)):
        self.fg, self.click, self.used = fg, click, set()

    def flash(self, win, at, shown=True):
        hit = link_raise(win, shown, self.fg, SESSIONS, OWNER.get, self.click, at, self.used)
        if hit:
            self.used.add(self.click)
        return hit


class Raises(unittest.TestCase):
    def test_flash_after_ctrl_click_raises(self):
        # a Ctrl+click on a link at T0: the running Chrome is handed the URL and refused the foreground
        self.assertTrue(Seq().flash(CHROME, T0 + 0.5))

    def test_file_link_in_vs_code_raises(self):
        self.assertTrue(Seq().flash(CODE, T0 + 1.5))

    def test_flash_at_the_edge_of_the_interval(self):
        self.assertTrue(Seq().flash(CHROME, T0 + LINK_WAIT))

    def test_click_noted_a_moment_after_the_flash(self):
        # the click is seen on the next look (every 20 ms), which can come after a very quick flash
        self.assertTrue(Seq().flash(CHROME, T0 - 0.1))

    def test_click_in_another_session_window_in_front(self):
        self.assertTrue(Seq(fg=TERM2, click=(TERM2, T0)).flash(CHROME, T0 + 0.5))


class StaysFlashing(unittest.TestCase):
    def test_another_app_in_front(self):
        self.assertFalse(Seq(fg=CODE).flash(CHROME, T0 + 0.5))

    def test_late_flash(self):
        self.assertFalse(Seq().flash(CHROME, T0 + LINK_WAIT + 0.1))

    def test_flash_of_the_terminal_itself(self):
        self.assertFalse(Seq().flash(TERM, T0 + 0.5))

    def test_flash_of_another_window_of_the_terminal(self):
        self.assertFalse(Seq().flash(TERM2, T0 + 0.5))   # another session's window
        self.assertFalse(Seq().flash(PLAIN_TERM, T0 + 0.5))  # the same terminal's window without a session

    def test_no_ctrl_click(self):
        self.assertFalse(Seq(click=None).flash(CHROME, T0 + 0.5))

    def test_click_was_in_another_window_than_the_one_in_front(self):
        # Ctrl+click in session window A, then the user went to session window B: B did not open the link
        self.assertFalse(Seq(fg=TERM2, click=(TERM, T0)).flash(CHROME, T0 + 0.5))

    def test_click_long_before_the_flash(self):
        self.assertFalse(Seq().flash(CHROME, T0 - 1))

    def test_hidden_window_flashing(self):
        # WSLg's msrdc.exe "RemoteApp" is invisible and flashes every second
        self.assertFalse(Seq().flash(WSLG, T0 + 0.5, shown=False))

    def test_one_click_raises_once(self):
        s = Seq()
        self.assertTrue(s.flash(CHROME, T0 + 0.4))
        self.assertFalse(s.flash(CHROME, T0 + 1))
        self.assertFalse(s.flash(CODE, T0 + 1.2))

    def test_no_session_window_in_front(self):
        self.assertFalse(Seq(fg=None).flash(CHROME, T0 + 0.5))
        self.assertFalse(Seq(fg=PLAIN_TERM, click=(PLAIN_TERM, T0)).flash(CHROME, T0 + 0.5))


if __name__ == "__main__":
    unittest.main()
