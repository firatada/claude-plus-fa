# Unit tests for agent-panel-paste.py (the paste key) and its hooks in the keeper: when Ctrl+V and Alt+V are taken
# (foreground changes, a tab switched, a paste under way, a key on its way through), the keys sent, what counts as a
# picture alone, the terminal-only render of the path, the temp folder's cleanup, the keeper's lookup of its own file
# and its skipping of the paste key's clipboards, and the preview's Copy as Image formats.
# Run: python tools/test-paste.py
# Registers no hotkey, sends no key, never touches the clipboard, any window, or the real ~/.claude: stand-ins
# throughout, files in a temporary folder. --clipboard adds a real round trip of Copy as Image through this
# process's own clipboard (written, read back as PNG, CF_DIBV5, CF_DIB and the CF_BITMAP Windows makes, pixels
# compared); the text on the clipboard before is put back after.
import ast, ctypes, importlib.util, io, json, os, shutil, struct, sys, tempfile, time, unittest
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
CLIPBOARD = "--clipboard" in sys.argv
if CLIPBOARD:
    sys.argv.remove("--clipboard")


def picture(path, alpha=False):
    """A test picture with detail all over (odd width: DIB rows need padding); alpha: a see-through part."""
    a = (255,) if alpha else ()
    img = Image.new("RGBA" if alpha else "RGB", (101, 37), (10, 120, 200) + a)
    for x in range(101):
        for y in range(37):
            if (x * 7 + y * 3) % 11 == 0:
                img.putpixel((x, y), (x * 2, y * 6, 255 - x) + a)
    if alpha:
        for x in range(60, 101):
            img.putpixel((x, 0), (200, 0, 0, 0))  # fully clear: white in CF_DIB
            img.putpixel((x, 1), (0, 0, 0, 128))  # half: grey in CF_DIB
    img.save(path)
    return img


def dib_image(data):
    """A CF_DIB or CF_DIBV5 block read by Pillow (a BMP file header put in front)."""
    head = struct.unpack_from("<I", data)[0]
    off = 14 + head
    return Image.open(io.BytesIO(b"BM" + struct.pack("<IHHI", 14 + len(data), 0, 0, off) + data))


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
        self.dir = tempfile.mkdtemp(prefix="claude-plus-test-paste-")

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
        self.dir = tempfile.mkdtemp(prefix="claude-plus-test-paste-")
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

    def test_a_typing_request_pastes_its_pieces_one_after_another(self):
        # Send to Session: the text up to a picture, the picture's path alone, the rest; one Ctrl+V each, in order,
        # PIECE_GAP apart, the clipboard saved once and put back once (stand-ins for the clipboard and the keys)
        names = ("save_clip", "send_keys", "PIECE_GAP")
        old = {n: getattr(pp, n) for n in names}
        old_fg = pp.user32.GetForegroundWindow
        pasted, front = [], {"h": TERM}
        try:
            pp.save_clip = lambda h: ([], [])
            pp.send_keys = lambda keys: pasted.append(self.k.text) or self.k.rendered.set()
            pp.PIECE_GAP = 0.01
            pp.user32.GetForegroundWindow = lambda: front["h"]
            self.k.set_path = lambda text, target: setattr(self.k, "text", text) or self.k.rendered.clear() or True
            self.k.restore = lambda saved: pasted.append("restored") or "restored"
            self.k.hwnd = 0
            self.k.type_text(7, TERM, ["My answer: this ", r"C:\s\one.png", " and that"])
            self.assertEqual(pasted, ["My answer: this ", r"C:\s\one.png", " and that", "restored"])
            self.assertEqual(self.k.typed[7], pp.TYPED_READ)
            pasted.clear()
            self.k.set_path = lambda text, target: (front.update(h=OTHER) if text.endswith(".png") else None) or \
                setattr(self.k, "text", text) or self.k.rendered.clear() or True
            self.k.type_text(8, TERM, ["a ", r"C:\s\one.png", " b"])  # the terminal left the front: it stops there
            self.assertEqual(pasted, ["a ", r"C:\s\one.png", "restored"])
            self.assertEqual(self.k.typed[8], pp.TYPED_FAILED)
        finally:
            for n, v in old.items():
                setattr(pp, n, v)
            pp.user32.GetForegroundWindow = old_fg

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
        self.dir = tempfile.mkdtemp(prefix="claude-plus-test-paste-")
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

    def test_image_formats(self):
        p = os.path.join(self.dir, "shot.png")
        picture(p)
        f = shots.image_formats(p)
        self.assertEqual([n for n, _ in f], ["PNG", shots.CF_DIBV5, shots.CF_DIB, shots.PASTE_MARK])
        d = dict(f)
        with open(p, "rb") as fh:
            self.assertEqual(d["PNG"], fh.read())  # the file's own PNG, not encoded again
        v5 = d[shots.CF_DIBV5]
        self.assertEqual(struct.unpack_from("<IiiHHI", v5), (124, 101, 37, 1, 32, 3))  # bottom-up, BI_BITFIELDS
        self.assertEqual(struct.unpack_from("<IIIII", v5, 40), (0xFF0000, 0xFF00, 0xFF, 0xFF000000, 0x73524742))
        self.assertEqual(len(v5), 124 + 101 * 37 * 4)
        dib = d[shots.CF_DIB]
        self.assertEqual(struct.unpack_from("<IiiHHI", dib), (40, 101, 37, 1, 24, 0))
        self.assertEqual(len(dib), 40 + 304 * 37)  # rows of 303 bytes padded to 304
        for block in (v5, dib):
            self.assertTrue(shots.identical(p, dib_image(block)))

    def test_image_formats_alpha_and_jpeg(self):
        p = os.path.join(self.dir, "clear.png")
        img = picture(p, alpha=True)
        d = dict(shots.image_formats(p))
        v5 = dib_image(d[shots.CF_DIBV5]).convert("RGBA")
        self.assertEqual(v5.getpixel((70, 0))[3], 0)  # straight alpha kept
        self.assertEqual(v5.getpixel((5, 5)), img.getpixel((5, 5)))
        rgb = dib_image(d[shots.CF_DIB]).convert("RGB")
        self.assertEqual(rgb.getpixel((70, 0)), (255, 255, 255))  # laid on white
        self.assertTrue(all(120 <= c <= 135 for c in rgb.getpixel((70, 1))), rgb.getpixel((70, 1)))
        j = os.path.join(self.dir, "shot.jpg")
        Image.new("RGB", (20, 10), (1, 2, 3)).save(j)
        self.assertTrue(dict(shots.image_formats(j))["PNG"].startswith(b"\x89PNG"))  # encoded as a PNG

    def test_copy_image_is_never_a_screenshot(self):
        logged = []
        k = shots.Shots(state=self.state, desk=self.dir, shot_dirs=[], log=lambda *a: logged.append(a))
        p = os.path.join(self.dir, "shot.png")
        picture(p)
        formats = [n for n, _ in shots.image_formats(p)] + [shots.CF_BITMAP]
        k.clipboard({"at": time.time(), "seq": 1, "owner": False, "fg": 0, "exe": "", "running": set(),
                     "formats": formats})
        self.assertEqual(k.pending, [])
        self.assertEqual(logged[0][0], "ignored own paste")
        self.assertFalse(k.copy_image(12345))  # no such screenshot: nothing written

    def test_one_mark_everywhere(self):
        self.assertEqual(pp.MARK, shots.PASTE_MARK)
        with open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn('RegisterClipboardFormatW("%s")' % pp.MARK, src)
        self.assertIn('PASTE_CLASS = "%s"' % pp.CLASS, src)


@unittest.skipUnless(CLIPBOARD, "real clipboard: run with --clipboard")
class RoundTrip(unittest.TestCase):
    """Copy as Image through the real clipboard, in this process: what Paint (CF_BITMAP, CF_DIB), browsers and
    Word (PNG, CF_DIBV5) and the paste key (Pillow's grabclipboard, then saved_file()) read."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="claude-plus-test-clip-")
        self.before = clip_text()

    def tearDown(self):
        if self.before is not None:
            shots.set_clipboard_text(self.before)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_round_trip(self):
        u, k, g = ctypes.windll.user32, ctypes.windll.kernel32, ctypes.windll.gdi32
        u.GetClipboardData.restype = k.GlobalLock.restype = ctypes.c_void_p
        k.GlobalLock.argtypes = k.GlobalUnlock.argtypes = k.GlobalSize.argtypes = [ctypes.c_void_p]
        k.GlobalSize.restype = ctypes.c_size_t
        g.GetObjectW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        png = u.RegisterClipboardFormatW("PNG")
        for alpha in (False, True):
            p = os.path.join(self.dir, "shot%d.png" % alpha)
            img = picture(p, alpha)
            self.assertTrue(shots.set_clipboard(shots.image_formats(p)))
            self.assertTrue(pp.image_only(u.IsClipboardFormatAvailable))  # the paste key takes it
            got = {}
            self.assertTrue(u.OpenClipboard(None))
            try:
                for fmt in (png, shots.CF_DIBV5, shots.CF_DIB):
                    h = u.GetClipboardData(fmt)
                    self.assertTrue(h, fmt)
                    got[fmt] = ctypes.string_at(k.GlobalLock(h), k.GlobalSize(h))
                    k.GlobalUnlock(h)
                bmp = u.GetClipboardData(shots.CF_BITMAP)  # made by Windows from the DIBs: what Paint pastes
                info = (ctypes.c_long * 8)()  # BITMAP (32 bytes on 64 bit): type, width, height, ...
                self.assertTrue(bmp and g.GetObjectW(bmp, ctypes.sizeof(info), info))
                self.assertEqual((info[1], abs(info[2])), img.size)
            finally:
                u.CloseClipboard()
            rgba = img.convert("RGBA").tobytes()
            self.assertEqual(Image.open(io.BytesIO(got[png])).convert("RGBA").tobytes(), rgba)
            self.assertEqual(dib_image(got[shots.CF_DIBV5]).convert("RGBA").tobytes(), rgba)
            if not alpha:
                self.assertTrue(shots.identical(p, dib_image(got[shots.CF_DIB])))
                self.assertTrue(shots.identical(p, pp.grab()))  # the paste key finds the keeper's own file


def clip_text():
    """The clipboard's text, put back after the round trip (None: no text)."""
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    u.GetClipboardData.restype = k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = k.GlobalUnlock.argtypes = [ctypes.c_void_p]
    if not u.OpenClipboard(None):
        return None
    try:
        h = u.GetClipboardData(13)
        if not h:
            return None
        t = ctypes.wstring_at(k.GlobalLock(h))
        k.GlobalUnlock(h)
        return t
    finally:
        u.CloseClipboard()


if __name__ == "__main__":
    unittest.main(verbosity=1)
