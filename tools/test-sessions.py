# Unit tests for Field Agents' session discovery (scan(), find_home(), open_session()): a session whose transcript
# names its own folder only far from its start and its end still gets that folder (and its card in the list), the
# deeper search runs once per file, a "//host/share" folder opens in PowerShell, and a transcript over 1 GB is read
# in well under a second.
# Run: python -I tools/test-sessions.py
# The .pyw is never imported (that would trip its one-instance mutex and raise the running panel): the functions
# under test are taken from it by ast and run against made-up transcripts in a temp folder; no process is started
# (subprocess is a fake) and no real session is read.
import ast, ctypes, datetime, json, msvcrt, os, re, shutil, tempfile, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
NAMES = ("TURN_LINE", "AT_LOOK", "HOME_LOOK", "USAGE_LINE", "CACHE_LOOK", "cache_of", "SESSION_FILE", "CWD", "RESUME", "CREATE_NEW_CONSOLE",
         "text_of", "PASTED", "prompt_words", "last_at", "parse", "slug", "find_home", "home_of", "scan", "folder_exists", "clean", "open_session",
         "TEST_PREFIX", "OLD_TEST_PREFIXES", "temp_roots", "TEMP_ROOTS", "is_test_dir")
SID = "0f0f0f0f-1111-2222-3333-444444444444"
HOME = "\\\\host\\share\\proj"      # where the session lives now (a UNC folder, as a WSL project is)
OLD = "C:\\gone\\proj"              # where it started, long gone
FAR = "C:\\Windows"                 # where a resume from a UNC folder in cmd landed


def pyw():
    with open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    keep = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in NAMES or
            isinstance(n, ast.Assign) and any(getattr(t, "id", None) in NAMES for t in n.targets)]
    ns = {"os": os, "re": re, "json": json, "datetime": datetime, "time": time, "tempfile": tempfile, "ctypes": ctypes}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "agent-panel.pyw", "exec"), ns)
    ns.update(wsl_roots=lambda: [], running=lambda: {}, read_ctx=lambda sid: None)
    return ns


def line(cwd, kind="assistant", text="x", pad=0):
    o = {"type": kind, "cwd": cwd, "sessionId": SID, "timestamp": "2026-01-02T03:04:05.000Z",
         "message": {"role": kind, "content": text + " " * pad}}
    return (json.dumps(o, separators=(",", ":")) + "\n").encode()  # compact, as Claude Code writes


def sparse(path, size):
    """An empty file of `size` bytes that takes no disk space (its middle reads as zeros)."""
    f = open(path, "wb")
    h = msvcrt.get_osfhandle(f.fileno())
    ctypes.windll.kernel32.DeviceIoControl(ctypes.c_void_p(h), 0x900C4, None, 0, None, 0,  # FSCTL_SET_SPARSE
                                           ctypes.byref(ctypes.c_ulong()), None)
    f.truncate(size)
    return f


class Discovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="claude-plus-test-sessions-")
        self.ns = pyw()
        self.ns["CLAUDE"] = self.tmp
        self.folder = os.path.join(self.tmp, "projects", re.sub(r"[^A-Za-z0-9]", "-", HOME))
        os.makedirs(self.folder)
        self.path = os.path.join(self.folder, SID + ".jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_moved(self):
        """Started in OLD (its first 300 KB), lived in HOME, then resumed in FAR (its last 1.2 MB)."""
        with open(self.path, "wb") as f:
            f.write(line(OLD, "user", "first prompt"))
            while f.tell() < 300000:
                f.write(line(OLD, pad=4000))
            for _ in range(20):
                f.write(line(HOME, pad=4000))
            end = f.tell() + 1200000
            while f.tell() < end:
                f.write(line(FAR, pad=4000))

    def test_folder_named_only_in_the_middle(self):
        self.write_moved()
        s = self.ns["scan"]({})
        self.assertEqual(len(s), 1)
        self.assertEqual(s[0]["cwd"], HOME)  # before the fix: OLD, so the card sat under "folder deleted"
        self.assertEqual(s[0]["folder"], "proj")

    def test_test_folders_are_never_listed(self):
        temp = os.path.join(self.tmp, "Temp")
        self.ns["TEMP_ROOTS"] = {os.path.normcase(temp)}
        is_test = self.ns["is_test_dir"]
        for d in ("claude-plus-test-paste-ab12", "claude-plus-test-keepalive-x\\T", "claude-plus-keepalive-trial-ie\\C",
                  "cpfa-wayback", "CPF-paste-probe-work"):
            self.assertTrue(is_test(os.path.join(temp, d)), d)
        for d in (os.path.join(temp, "my-project"), os.path.join(temp, "x", "claude-plus-test-a"), temp,
                  "C:\\apps\\claude-plus-test-a", None, ""):
            self.assertFalse(is_test(d), d)
        for cwd, sid in ((os.path.join(temp, "claude-plus-test-paste-ab12"), SID), (HOME, SID[:-1] + "5")):
            folder = os.path.join(self.tmp, "projects", re.sub(r"[^A-Za-z0-9]", "-", cwd))
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, sid + ".jsonl"), "wb") as f:
                f.write(line(cwd, "user", "a prompt"))
        self.assertEqual([s["cwd"] for s in self.ns["scan"]({})], [HOME])

    def test_a_session_that_starts_with_a_paste_is_named_by_it(self):
        pasted = '\n\n<pasted_content id="6f11">\nThree live sites: registrar moves\n</pasted_content id="6f11">\n\n --- where does https come from?'
        with open(self.path, "wb") as f:
            f.write(line(HOME, "user", "<command-name>/model</command-name>"))
            f.write(line(HOME, "user", pasted))
        self.assertEqual(self.ns["parse"](self.path)["first"],
                         "Three live sites: registrar moves --- where does https come from?")

    def test_searched_once_per_file(self):
        self.write_moved()
        calls = []
        real = self.ns["find_home"]
        self.ns["find_home"] = lambda *a: calls.append(a) or real(*a)
        cache = {}
        self.ns["scan"](cache)
        with open(self.path, "ab") as f:  # the session goes on: the file changes, its parse is redone
            f.write(line(FAR, pad=10))
        s = self.ns["scan"](cache)
        self.assertEqual(len(calls), 1)
        self.assertEqual(s[0]["cwd"], HOME)

    def test_named_nowhere_falls_back_and_is_not_searched_again(self):
        with open(self.path, "wb") as f:
            f.write(line(OLD, "user", "first prompt"))
        calls = []
        real = self.ns["find_home"]
        self.ns["find_home"] = lambda *a: calls.append(a) or real(*a)
        cache = {}
        self.assertEqual(self.ns["scan"](cache)[0]["cwd"], OLD)
        self.ns["scan"](cache)
        self.assertEqual(len(calls), 1)

    def test_value_cut_at_a_block_edge(self):
        """A cwd that straddles find_home()'s 4 MB block edge is still found."""
        rec = line(HOME)
        at = rec.index(b'"cwd"')
        with open(self.path, "wb") as f:
            f.write(line(OLD, "user", "first prompt"))
            while f.tell() < 2000000:
                f.write(line(OLD, pad=4000))
            f.write(rec)
            # the last block starts 4 MB before the end: make that 5 bytes into HOME's "cwd":"...
            f.write(line(FAR, pad=at + 5 + 4194304 - len(rec) - len(line(FAR))))
        found = self.ns["find_home"](self.path, os.path.basename(self.folder), os.path.getsize(self.path))
        self.assertEqual(found, HOME)

    def test_huge_transcript_is_quick(self):
        """1.2 GB, its folder named nowhere near its ends, no turn line in its last 8 MB: still found fast."""
        size = 1200000000
        f = sparse(self.path, size)
        f.seek(0)
        f.write(line(OLD, "user", "first prompt"))
        tail = line(FAR, "system")
        f.seek(size - 30 * len(tail))
        f.write(tail * 30)
        f.close()
        open(self.path, "rb").close()  # the first open of a new file is the virus scanner's (1 to 4 s), not ours
        t = time.time()
        s = self.ns["scan"]({})
        took = time.time() - t
        self.assertEqual(s[0]["cwd"], OLD)  # named nowhere it is read: the first folder stands in
        self.assertLess(took, 1.0, "scan of a 1.2 GB transcript took %.2f s" % took)


class Opening(unittest.TestCase):
    def open(self, cwd):
        ns = pyw()
        seen = []

        class Sub:
            @staticmethod
            def Popen(args, **kw):
                seen.append((args, kw))

        ns.update(subprocess=Sub, clean_env=lambda: {})
        ns["open_session"]({"id": SID, "title": "proj", "distro": None, "cwd": cwd})
        return seen[0]

    def test_forward_slash_unc_opens_in_powershell(self):
        args, kw = self.open("//host/share/proj")
        self.assertEqual(args[0], "powershell.exe")  # cmd there would stand in C:\Windows
        self.assertEqual(kw["cwd"], HOME)

    def test_backslash_unc_unchanged(self):
        args, kw = self.open(HOME)
        self.assertEqual((args[0], kw["cwd"]), ("powershell.exe", HOME))

    def test_drive_folder_in_cmd(self):
        args, kw = self.open("C:\\work\\proj")
        self.assertEqual((args[0], kw["cwd"]), ("cmd.exe", "C:\\work\\proj"))


if __name__ == "__main__":
    unittest.main()
