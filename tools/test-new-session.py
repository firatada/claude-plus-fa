# Unit tests for Field Agents' "+" (a new session), its Python half in agent-panel.pyw:
#   - the menu's folders (recent_folders()): newest activity first, each folder once (Windows ignores case), only
#     folders still there, headless runs left out, at most NEW_MAX, a WSL distro's folder in its \\wsl.localhost form;
#   - the start (start_new()): claude --dangerously-skip-permissions, never --resume, in a new console in the folder,
#     through the same open_session() as a resumed session: cmd /k, PowerShell for a UNC folder, wsl.exe --cd for a
#     folder inside a distro; a folder gone at launch is said so and nothing starts;
#   - Choose folder… (the panel's pick_folder()): the picker opens in the latest session's folder, Cancel does nothing,
#     a picked folder starts a session, a picker that fails is said so.
# Run: python -I tools/test-new-session.py
# The .pyw is never imported (its one-instance mutex would raise the running panel): the functions are taken out by
# ast. subprocess.Popen is a fake that records, so no console is ever opened; folders are made on a temp folder with
# the claude-plus-test- prefix and removed after.
import ast, os, re, shutil, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
NAMES = ("RESUME", "NEW", "NEW_MAX", "WSL_UNC", "CREATE_NEW_CONSOLE", "clean", "folder_exists", "open_session",
         "where_of", "folder_name", "wsl_folder", "recent_folders", "start_new", "ok", "err")


class Popen:
    """subprocess.Popen as open_session() uses it: every start recorded, nothing run."""
    starts = []

    def __init__(self, args, **kw):
        Popen.starts.append((args, kw))


class Webview:
    class FileDialog:
        FOLDER = 20


def panel():
    with open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    keep = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in NAMES or
            isinstance(n, ast.Assign) and any(getattr(t, "id", None) in NAMES for t in n.targets)]
    pick = [f for c in tree.body if isinstance(c, ast.ClassDef) for f in c.body
            if isinstance(f, ast.FunctionDef) and f.name == "pick_folder" and len(f.args.args) == 2]  # the panel's, not Api's
    assert len(pick) == 1, "pick_folder() not found once"
    ns = {"os": os, "re": re, "subprocess": type("S", (), {"Popen": Popen}), "clean_env": lambda: {},
          "webview": Webview}
    exec(compile(ast.Module(body=keep + pick, type_ignores=[]), "agent-panel.pyw", "exec"), ns)
    return ns


P = panel()


def sess(cwd, at, distro=None, headless=False):
    return {"cwd": cwd, "at": at, "distro": distro, "headless": headless, "folder": os.path.basename(cwd)}


class Base(unittest.TestCase):
    def setUp(self):
        Popen.starts.clear()
        self.tmp = tempfile.mkdtemp(prefix="claude-plus-test-new-")
        self.dirs = {}
        for n in ("alpha", "beta", "gamma", "gone", "with space"):
            self.dirs[n] = os.path.join(self.tmp, n)
            os.mkdir(self.dirs[n])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class Folders(Base):
    def test_newest_first_once_and_present(self):
        d = self.dirs
        shutil.rmtree(d["gone"])
        ss = [sess(d["alpha"], 10), sess(d["beta"], 30), sess(d["alpha"].upper(), 40), sess(d["gone"], 50),
              sess(d["gamma"], 20), sess(d["beta"] + "\\", 5), sess(d["with space"], 1, headless=True)]
        got = P["recent_folders"](ss)
        self.assertEqual([f["path"].lower() for f in got], [d["alpha"].lower(), d["beta"].lower(), d["gamma"].lower()])
        self.assertEqual([f["name"].lower() for f in got], ["alpha", "beta", "gamma"])

    def test_at_most_ten(self):
        ss = []
        for i in range(15):
            p = os.path.join(self.tmp, "f%02d" % i)
            os.mkdir(p)
            ss.append(sess(p, i))
        got = P["recent_folders"](ss)
        self.assertEqual(len(got), 10)
        self.assertEqual(got[0]["name"], "f14")
        self.assertEqual(got[-1]["name"], "f05")

    def test_wsl_and_root(self):
        ss = [sess("/home/u/proj", 3, distro="Ubuntu"), sess("C:\\", 2), {"cwd": None, "at": 9, "distro": None,
              "folder": "x"}]
        got = P["recent_folders"](ss, exists=lambda s: True)
        self.assertEqual(got, [{"path": "\\\\wsl.localhost\\Ubuntu\\home\\u\\proj", "name": "proj"},
                               {"path": "C:\\", "name": "C:\\"}])

    def test_wsl_folder(self):
        w = P["wsl_folder"]
        self.assertEqual(w("\\\\wsl.localhost\\Ubuntu\\home\\u"), ("Ubuntu", "/home/u"))
        self.assertEqual(w("\\\\wsl$\\Debian"), ("Debian", "/"))
        self.assertEqual(w("//wsl.localhost/Ubuntu-22.04/srv/x"), ("Ubuntu-22.04", "/srv/x"))
        self.assertEqual(w("\\\\server\\share\\x"), (None, "\\\\server\\share\\x"))
        self.assertEqual(w("C:\\work"), (None, "C:\\work"))


class Start(Base):
    def test_plain_folder_cmd(self):
        r = P["start_new"](self.dirs["with space"])
        self.assertFalse(r["err"])
        self.assertIn("with space", r["t"])
        self.assertEqual(len(Popen.starts), 1)
        args, kw = Popen.starts[0]
        self.assertEqual(args[:2], ["cmd.exe", "/k"])
        self.assertTrue(args[2].endswith("& claude --dangerously-skip-permissions"), args[2])
        self.assertIn("title claude: with space", args[2])
        self.assertNotIn("--resume", args[2])
        self.assertEqual(kw["cwd"], self.dirs["with space"])
        self.assertEqual(kw["creationflags"], P["CREATE_NEW_CONSOLE"])

    def test_gone_at_launch(self):
        p = self.dirs["gone"]
        shutil.rmtree(p)
        r = P["start_new"](p)
        self.assertTrue(r["err"])
        self.assertIn(p, r["t"])
        self.assertEqual(Popen.starts, [])

    def test_empty(self):
        self.assertIsNone(P["start_new"]("  "))
        self.assertEqual(Popen.starts, [])

    def test_launch_fails(self):
        def bad(s, cmd):
            raise FileNotFoundError(2, "The system cannot find the file specified")
        r = P["start_new"](self.dirs["alpha"], launch=bad)
        self.assertTrue(r["err"])
        self.assertIn("could not start", r["t"])

    def test_same_launcher_unc_and_wsl(self):
        # the kinds start_new() hands on, through open_session() as a resumed session uses it
        P["open_session"]({"id": None, "cwd": "\\\\host\\share\\proj", "distro": None, "title": "proj"}, P["NEW"])
        P["open_session"]({"id": None, "cwd": "/home/u/proj", "distro": "Ubuntu", "title": "proj"}, P["NEW"])
        (a1, k1), (a2, k2) = Popen.starts
        self.assertEqual(a1[0], "powershell.exe")
        self.assertTrue(a1[-1].endswith("; claude --dangerously-skip-permissions"))
        self.assertEqual(k1["cwd"], "\\\\host\\share\\proj")
        self.assertEqual(a2, ["wsl.exe", "-d", "Ubuntu", "--cd", "/home/u/proj", "--", "bash", "-lic",
                              "claude --dangerously-skip-permissions"])

    def test_wsl_path_goes_inside(self):
        seen = []
        real_isdir = os.path.isdir
        P["os"].path.isdir = lambda p: True if p.startswith("\\\\wsl") else real_isdir(p)
        try:
            r = P["start_new"]("\\\\wsl.localhost\\Ubuntu\\home\\u\\proj", launch=lambda s, c: seen.append((s, c)))
        finally:
            P["os"].path.isdir = real_isdir
        self.assertFalse(r["err"])
        self.assertEqual(seen[0][0]["distro"], "Ubuntu")
        self.assertEqual(seen[0][0]["cwd"], "/home/u/proj")
        self.assertEqual(seen[0][1], "claude --dangerously-skip-permissions")


class Pick(Base):
    def me(self):
        d = self.dirs
        return type("Panel", (), {"sessions": [sess(d["alpha"], 1), sess(d["beta"], 9)]})()

    def test_starts_in_latest_and_launches(self):
        asked = []

        def dialog(kind, directory):
            asked.append((kind, directory))
            return (self.dirs["gamma"],)
        r = P["pick_folder"](self.me(), dialog)
        self.assertEqual(asked, [(20, self.dirs["beta"])])
        self.assertFalse(r["err"])
        self.assertEqual(Popen.starts[0][1]["cwd"], self.dirs["gamma"])

    def test_cancel(self):
        self.assertIsNone(P["pick_folder"](self.me(), lambda kind, directory: None))
        self.assertEqual(Popen.starts, [])

    def test_no_sessions_starts_home(self):
        asked = []
        P["pick_folder"](type("Panel", (), {"sessions": []})(), lambda kind, directory: asked.append(directory))
        self.assertEqual(asked, [os.path.expanduser("~")])

    def test_picker_fails(self):
        def dialog(kind, directory):
            raise RuntimeError("no")
        r = P["pick_folder"](self.me(), dialog)
        self.assertTrue(r["err"])
        self.assertEqual(Popen.starts, [])

    def test_picked_then_gone(self):
        p = self.dirs["gone"]
        r = P["pick_folder"](self.me(), lambda kind, directory: (shutil.rmtree(p), (p,))[1])
        self.assertTrue(r["err"])
        self.assertEqual(Popen.starts, [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
