# Unit tests for agent-panel-paste.py (the paste key) and its hooks in the keeper: when Ctrl+V and Alt+V are taken
# (foreground changes, a tab switched, a paste under way, a key on its way through), the keys sent, what counts as a
# picture alone, the terminal-only render of the path, the temp folder's cleanup, the keeper's lookup of its own file
# and its skipping of the paste key's clipboards. Run: python tools/test-paste.py
# Registers no hotkey, sends no key, never touches the clipboard, any window, or the real ~/.claude: stand-ins
# throughout, files in a temporary folder.
import ast, importlib.util, json, os, shutil, sys, tempfile, time, unittest
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pp = load("agent_panel_paste", "agent-panel-paste.py")
shots = load("agent_panel_shots", "agent-panel-shots.py")
TERM, OTHER = 1001, 2002


class Rules(unittest.TestCase):
    def test_claude_title(self):
        for t in ("✳ Claude Code", "✳ mindmap", "⠐ İki görsel açıklaması", "◑ sistem", "· money_transfer"):
            self.assertTrue(pp.claude_title(t), t)
        for t in ("", None, "Windows PowerShell", "C:\\WINDOWS\\system32\\cmd.exe", "~/src", "mindmap", "✳"):
            self.assertFalse(pp.claude_title(t), t)

    def test_wanted(self):
        self.assertTrue(pp.wanted(TERM, {TERM}, "✳ x"))
        self.assertFalse(pp.wanted(TERM, {TERM}, "Windows PowerShell"))  # a shell tab of a session's window
        self.assertFalse(pp.wanted(OTHER, {TERM}, "✳ x"))  # not a listed session's window
        self.assertFalse(pp.wanted(0, {TERM}, "✳ x"))

    def test_image_only(self):
        png = pp.user32.RegisterClipboardFormatW("PNG")
        has = lambda *fs: (lambda f: f in fs)
        self.assertTrue(pp.image_only(has(pp.CF_DIB)))
        self.assertTrue(pp.image_only(has(png, pp.CF_DIBV5)))
        self.assertFalse(pp.image_only(has(pp.CF_DIB, pp.CF_UNICODETEXT)))  # a picture with text: text wins
        self.assertFalse(pp.image_only(has(pp.CF_UNICODETEXT)))
        self.assertFalse(pp.image_only(has()))

    def test_keys_for(self):
        C, A, V = pp.VK_CONTROL, pp.VK_MENU, pp.VK_V
        d, u = False, True
        # passing through: what the user pressed, the modifier they still hold not pressed again
        self.assertEqual(pp.keys_for(pp.CTRL_V, True, False), [(V, d), (V, u)])
        self.assertEqual(pp.keys_for(pp.CTRL_V, False, False), [(C, d), (V, d), (V, u), (C, u)])
        self.assertEqual(pp.keys_for(pp.ALT_V, False, True), [(V, d), (V, u)])
        self.assertEqual(pp.keys_for(pp.ALT_V, False, False), [(A, d), (V, d), (V, u), (A, u)])
        # the paste itself: always Ctrl+V; a held Alt let go after Ctrl went down (no menu)
        self.assertEqual(pp.keys_for(pp.CTRL_V, True, False, paste=True), [(V, d), (V, u)])
        self.assertEqual(pp.keys_for(pp.ALT_V, False, True, paste=True), [(C, d), (A, u), (V, d), (V, u), (C, u)])
        self.assertEqual(pp.keys_for(pp.ALT_V, False, False, paste=True), [(C, d), (V, d), (V, u), (C, u)])


class Temp(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cpfa-paste-test-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_clean_temp(self):
        now = time.time()
        for n, age in (("paste-old.png", 2 * 86400), ("paste-new.png", 60), ("mine.png", 9 * 86400),
                       ("paste-old.txt", 9 * 86400)):
            p = os.path.join(self.dir, n)
            open(p, "wb").close()
            os.utime(p, (now - age, now - age))
        self.assertEqual(pp.clean_temp(self.dir, now), 1)
        self.assertEqual(sorted(os.listdir(self.dir)), ["mine.png", "paste-new.png", "paste-old.txt"])
        self.assertEqual(pp.clean_temp(os.path.join(self.dir, "missing"), now), 0)

    def test_write_temp_unique_and_cleans(self):
        img = Image.new("RGB", (40, 30), (200, 10, 10))
        old = os.path.join(self.dir, "paste-20000101-000000-000.png")
        open(old, "wb").close()
        os.utime(old, (0, 0))
        now = time.time()
        a, b = pp.write_temp(img, self.dir, now), pp.write_temp(img, self.dir, now)
        self.assertNotEqual(a, b)
        self.assertFalse(os.path.exists(old))
        with Image.open(a) as got:
            self.assertEqual((got.size, got.convert("RGB").getpixel((0, 0))), ((40, 30), (200, 10, 10)))
        self.assertNotIn(" ", a[len(self.dir):])


class State(unittest.TestCase):
    """When the keys are taken: stand-ins for RegisterHotKey/UnregisterHotKey, titles and the panel's list file."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cpfa-paste-test-")
        self.file = os.path.join(self.dir, "paste-windows.json")
        self.write([TERM])
        self.titles = {TERM: "✳ mindmap", OTHER: "Untitled - Notepad"}
        self.calls = []
        self.k = pp.Keys(windows_file=self.file, temp=self.dir, title=lambda h: self.titles.get(h, ""),
                         register=lambda i: self.calls.append(("reg", i)) or True,
                         unregister=lambda i: self.calls.append(("unreg", i)))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, windows):
        with open(self.file, "w", encoding="utf-8") as f:
            json.dump({"windows": windows}, f)
        os.utime(self.file, (time.time() + len(windows), time.time() + len(windows) + 0.001 * id(windows) % 7))

    def test_follows_the_foreground(self):
        self.assertTrue(self.k.check(TERM))
        self.assertEqual(self.calls, [("reg", 1), ("reg", 2)])
        self.assertTrue(self.k.check(TERM))  # already taken: nothing again
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(self.k.check(OTHER))  # another window in front: given back
        self.assertEqual(self.calls[2:], [("unreg", 1), ("unreg", 2)])
        self.assertFalse(self.k.check(0))
        self.assertEqual(len(self.calls), 4)
        self.assertTrue(self.k.check(TERM))

    def test_tab_switched_to_a_shell(self):
        self.assertTrue(self.k.check(TERM))
        self.titles[TERM] = "Windows PowerShell"
        self.assertFalse(self.k.check(TERM))
        self.titles[TERM] = "⠂ mindmap"  # back, busy glyph
        self.assertTrue(self.k.check(TERM))

    def test_list_changes(self):
        self.assertFalse(self.k.check(OTHER))
        self.write([TERM, OTHER])
        self.titles[OTHER] = "✳ new session"
        self.assertTrue(self.k.check(OTHER))
        with open(self.file, "w") as f:
            f.write("{broken")  # mid-write or broken: the last list stays
        os.utime(self.file, (time.time() + 99, time.time() + 99))
        self.assertTrue(self.k.check(OTHER))

    def test_off_while_busy_passing_or_closing(self):
        self.k.busy = True
        self.assertFalse(self.k.check(TERM))
        self.k.busy, self.k.until = False, time.time() + 5
        self.assertFalse(self.k.check(TERM))
        self.k.until = 0
        self.assertTrue(self.k.check(TERM))
        self.k.busy = True
        self.assertFalse(self.k.check(TERM))  # an image paste began: the keys are given back for its duration
        self.k.busy, self.k.closing = False, True
        self.assertFalse(self.k.check(TERM))

    def test_taken_by_another_program(self):
        logged = []
        k = pp.Keys(windows_file=self.file, temp=self.dir, title=lambda h: "✳ x", log=lambda *a: logged.append(a),
                    register=lambda i: i == pp.ALT_V, unregister=lambda i: None)
        self.assertTrue(k.check(TERM))
        self.assertEqual(logged[0][1], [pp.CTRL_V])

    def test_render_only_for_the_terminal(self):
        put, pid_of = pp.put, pp.pid_of
        given = []
        try:
            pp.put = lambda f, data: given.append((f, data))
            opener = {"pid": 0}
            pp.pid_of = lambda h: opener["pid"]
            self.k.text, self.k.target_pid, self.k.refused = "C:\\x\\a.png", 77, []
            self.k.rendered.clear()
            opener["pid"] = 55  # WSLg's msrdc, a clipboard manager
            self.k.render(pp.CF_UNICODETEXT)
            self.assertEqual((given, self.k.rendered.is_set(), self.k.refused), ([], False, [55]))
            opener["pid"] = 77  # the terminal
            self.k.render(pp.CF_UNICODETEXT)
            self.assertEqual(given, [(pp.CF_UNICODETEXT, "C:\\x\\a.png\0".encode("utf-16-le"))])
            self.assertTrue(self.k.rendered.is_set())
            self.k.render(pp.CF_DIB)  # nothing else is delayed
            self.assertEqual(len(given), 1)
        finally:
            pp.put, pp.pid_of = put, pid_of


class Keeper(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cpfa-paste-test-")
        self.state = os.path.join(self.dir, "screenshots.json")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_saved_file(self):
        a = Image.new("RGB", (64, 48), (10, 120, 200))
        b = a.copy()
        b.putpixel((5, 5), (255, 255, 255))  # one pixel off: not the same picture
        desk, proj = os.path.join(self.dir, "desk.png"), os.path.join(self.dir, "proj.png")
        a.save(proj)
        k = shots.Shots(state=self.state, desk=self.dir, shot_dirs=[])
        k.remember(desk, 100.0, shots.sig(a), a.size)  # its Desktop file moved into a project since
        k.index[-1]["file"], k.index[-1]["places"] = None, [proj]
        k.save()
        self.assertEqual(shots.saved_file(a, self.state), proj)
        self.assertIsNone(shots.saved_file(b, self.state))
        self.assertIsNone(shots.saved_file(a.resize((32, 24)), self.state))
        os.remove(proj)
        self.assertIsNone(shots.saved_file(a, self.state))
        self.assertIsNone(shots.saved_file(a, os.path.join(self.dir, "none.json")))

    def test_own_paste_is_never_a_screenshot(self):
        logged = []
        k = shots.Shots(state=self.state, desk=self.dir, shot_dirs=[], log=lambda *a: logged.append(a))
        grab = shots.ImageGrab.grabclipboard
        shots.ImageGrab.grabclipboard = lambda: self.fail("read the clipboard")
        try:
            for formats in ([shots.CF_DIB, shots.CF_DIBV5, "claude-plus-fa paste"],  # the user's picture put back
                            [13, "claude-plus-fa paste", "CanIncludeInClipboardHistory"]):  # the path
                k.clipboard({"at": time.time(), "seq": 1, "owner": False, "fg": 0, "exe": "", "running": set(),
                             "formats": formats})
        finally:
            shots.ImageGrab.grabclipboard = grab
        self.assertEqual(k.pending, [])
        self.assertEqual([a[0] for a in logged], ["ignored own paste", "ignored own paste"])

    def test_one_mark_everywhere(self):
        self.assertEqual(pp.MARK, shots.PASTE_MARK)
        with open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn('RegisterClipboardFormatW("%s")' % pp.MARK, src)
        self.assertIn('PASTE_CLASS = "%s"' % pp.CLASS, src)


if __name__ == "__main__":
    unittest.main(verbosity=1)
