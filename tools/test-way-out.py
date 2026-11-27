# Unit tests for the way-out window's gate and the Codex window's start (glm-fallback.py):
#   - one limit of one session asks once: never for a subagent (agent_id), a `claude -p` or SDK run, a second
#     StopFailure while the window is open, or after the user answered it (until the limit resets);
#   - the window is titled with the session's name;
#   - Codex starts on the session's folder (-C), with the update screen off and the folder trusted beforehand,
#     so nothing stops its window on a question.
# Run: python -I tools/test-way-out.py
# glm-fallback.py is loaded as a module with HOME, RETURN_DIR, LIMITS_FILE and CODEX_HOME on a temp folder; the
# dialog, Codex's app server and every window are replaced: nothing real is read or written, nothing opens.
import importlib.util, io, json, os, sys, tempfile, threading, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="claude-plus-test-wayout-")


def load():
    spec = importlib.util.spec_from_file_location("glm_fallback", os.path.join(HERE, "..", "glm-fallback.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.HOME = TMP
    m.RETURN_DIR = os.path.join(TMP, "glm-return")
    m.LIMITS_FILE = os.path.join(TMP, "rate_limits.json")
    m.KEY_FILE = os.path.join(TMP, "glm.env")
    return m


fb = load()
sys.modules.setdefault("webview", type(sys)("webview"))  # main() checks pywebview is there; no window is made
os.environ.pop("ANTHROPIC_BASE_URL", None)
os.environ["CODEX_HOME"] = os.path.join(TMP, "codex")
os.makedirs(os.path.join(TMP, ".claude", "sessions"), exist_ok=True)
os.makedirs(os.environ["CODEX_HOME"], exist_ok=True)
PID = 4242


def session_file(sid, entry="cli", name="my-session"):
    with open(os.path.join(TMP, ".claude", "sessions", "%d.json" % PID), "w", encoding="utf-8") as f:
        json.dump({"pid": PID, "sessionId": sid, "kind": "interactive", "entrypoint": entry, "name": name}, f)


class Hook(unittest.TestCase):
    """main() fed a StopFailure input, the dialog replaced by a counter that answers Cancel."""

    def setUp(self):
        self.asked = []
        self.wait = 0
        fb.claude_pid = lambda sid: PID
        fb.ask_native = lambda folder: self.asked.append(("native", folder)) or False

        def ask_out(name, **kw):
            self.asked.append(name)
            time.sleep(self.wait)
            return None, None, False  # Cancel
        fb.ask_out = ask_out
        with open(fb.LIMITS_FILE, "w") as f:
            json.dump({"five_hour": {"used_percentage": 100, "resets_at": time.time() + 3600}}, f)

    def fire(self, sid, **extra):
        data = dict({"session_id": sid, "transcript_path": os.path.join(TMP, "none.jsonl"), "cwd": TMP,
                     "hook_event_name": "StopFailure", "error": "rate_limit"}, **extra)
        old = sys.stdin
        sys.stdin = io.StringIO(json.dumps(data))
        try:
            fb.main()
        finally:
            sys.stdin = old

    def test_main_thread_asks_once_titled_with_name(self):
        session_file("s-main")
        self.fire("s-main")
        self.assertEqual(self.asked, ["my-session"])
        self.fire("s-main")  # the same limit again, after Cancel
        self.assertEqual(len(self.asked), 1)
        self.assertTrue(os.path.exists(fb.quiet_path("s-main")))

    def test_subagent_never_asks(self):
        session_file("s-sub")
        for i in range(5):
            self.fire("s-sub", agent_id="a%d" % i, agent_type="general-purpose")
        self.assertEqual(self.asked, [])
        self.assertFalse(os.path.exists(fb.quiet_path("s-sub")))  # the main thread still asks
        self.fire("s-sub")
        self.assertEqual(len(self.asked), 1)

    def test_headless_never_asks(self):
        session_file("s-p", entry="sdk-cli")
        self.fire("s-p")
        self.assertEqual(self.asked, [])

    def test_headless_from_transcript_without_process(self):
        fb.claude_pid = lambda sid: None
        path = os.path.join(TMP, "headless.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "entrypoint": "sdk-cli", "sessionId": "s-t"}) + "\n")
        self.fire("s-t", transcript_path=path)
        self.assertEqual(self.asked, [])
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "entrypoint": "cli", "sessionId": "s-t"}) + "\n")
        self.fire("s-t", transcript_path=path)
        self.assertEqual(len(self.asked), 1)

    def test_second_trigger_while_open_shows_nothing(self):
        session_file("s-race")
        self.wait = 0.6
        hooks = [threading.Thread(target=self.fire, args=("s-race",)) for _ in range(4)]
        for t in hooks:
            t.start()
        for t in hooks:
            t.join()
        self.assertEqual(len(self.asked), 1)

    def test_held_window_blocks_another_hook(self):
        session_file("s-held")
        h = fb.hold_dialog("s-held")
        self.assertTrue(h)
        try:
            self.fire("s-held")
            self.assertEqual(self.asked, [])
        finally:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(h))
        self.fire("s-held")  # the holder ended: this one asks
        self.assertEqual(len(self.asked), 1)

    def test_asks_again_after_the_reset(self):
        session_file("s-reset")
        fb.set_quiet("s-reset", time.time() - 1)  # answered for a limit that has reset since
        self.fire("s-reset")
        self.assertEqual(len(self.asked), 1)

    def test_quiet_until_the_reset_time(self):
        session_file("s-until")
        self.fire("s-until")
        with open(fb.quiet_path("s-until")) as f:
            until = json.load(f)["until"]
        with open(fb.LIMITS_FILE) as f:
            self.assertAlmostEqual(until, json.load(f)["five_hour"]["resets_at"], delta=1)


class Title(unittest.TestCase):
    def test_page_heading_names_the_session(self):
        pages = []
        real_show, real_ask = fb.show, fb.ask_out
        fb.show = lambda title, page, api, forward, activate=True, width=440: pages.append(page)
        try:
            spec = importlib.util.spec_from_file_location("glm_fallback2", os.path.join(HERE, "..", "glm-fallback.py"))
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)  # a fresh copy: the Hook tests replace ask_out on fb
            m.show = fb.show
            m.ask_out("Fix the <login> page", forward=False, preview={})
        finally:
            fb.show, fb.ask_out = real_show, real_ask
        self.assertIn('<h1 id="t">Usage Limit Reached: Fix the &lt;login&gt; page</h1>', pages[0])


class CodexStart(unittest.TestCase):
    def setUp(self):
        self.cmds = []
        fb.open_window = lambda cmd, cwd, title, mark, wait=15: self.cmds.append((cmd, cwd, title)) or None

    def test_folder_and_no_update_screen(self):
        cwd = os.path.join(TMP, "my project", "src", "sub")
        fb.open_codex("T1", cwd, True, name="my session")
        cmd = self.cmds[0][0]
        self.assertIn('codex resume T1 -C "%s" ' % cwd, cmd)
        self.assertIn("-c check_for_update_on_startup=false", cmd)
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", cmd)
        self.assertLess(cmd.index("-C "), cmd.index(' "' + fb.CONTINUE[:20]))  # options before the prompt
        self.assertEqual(self.cmds[0][2], "my session")

    def test_drive_root_and_trailing_separator(self):
        fb.open_codex("T2", "D:\\", False)
        self.assertIn('-C "D:\\."', self.cmds[0][0])
        fb.open_codex("T3", "D:\\work\\", False)
        self.assertIn('-C "D:\\work" ', self.cmds[1][0])
        self.assertNotIn("--dangerously", self.cmds[1][0])


class Trust(unittest.TestCase):
    def setUp(self):
        self.repo = os.path.join(TMP, "Repo Two.dir")
        self.sub = os.path.join(self.repo, "src", "sub")
        os.makedirs(self.sub, exist_ok=True)
        os.makedirs(os.path.join(self.repo, ".git"), exist_ok=True)
        self.config = os.path.join(os.environ["CODEX_HOME"], "config.toml")
        if os.path.exists(self.config):
            os.remove(self.config)

    def write(self, text):
        with open(self.config, "w", encoding="utf-8") as f:
            f.write(text)

    def test_root_is_the_repository_lower_case(self):
        self.assertEqual(fb.trust_root(self.sub), os.path.normcase(self.repo))
        plain = os.path.join(TMP, "NoGit")
        os.makedirs(plain, exist_ok=True)
        if not any(os.path.exists(os.path.join(p, ".git")) for p in (TMP, os.path.dirname(TMP))):
            self.assertEqual(fb.trust_root(plain), os.path.normcase(plain))

    def test_call_when_codex_has_no_answer(self):
        call = fb.trust_call(self.sub)
        self.assertEqual(call[0], "config/value/write")
        root = os.path.normcase(self.repo)
        self.assertEqual(call[1], {"keyPath": "projects.%s.trust_level" % json.dumps(root, ensure_ascii=False),
                                   "value": "trusted", "mergeStrategy": "replace"})
        self.write("[tui]\nscreen_reader_detection_done = true\n")
        self.assertIsNotNone(fb.trust_call(self.sub))

    def test_known_answer_is_kept(self):
        key = self.repo.upper().replace("\\", "\\\\")
        self.write('[projects."%s"]\ntrust_level = "untrusted"\n' % key)
        self.assertIsNone(fb.trust_call(self.sub))  # the user's own "untrusted" stays
        self.write("[projects.'%s']\ntrust_level = \"trusted\"\n" % os.path.normcase(self.repo))
        self.assertIsNone(fb.trust_call(self.sub))

    def test_unreadable_config_is_left_to_codex(self):
        self.write("[projects\nbroken")
        self.assertIsNone(fb.trust_call(self.sub))

    def test_prepare_sends_name_and_trust_in_one_run(self):
        seen = []
        fb.codex_rpc = lambda calls, cwd, wait=30: seen.append(calls) or [{} for _ in calls]
        self.assertEqual(fb.prepare_codex("T9", "my session", self.sub), (True, True))
        self.assertEqual([c[0] for c in seen[0]], ["thread/name/set", "config/value/write"])
        self.write("[projects.'%s']\ntrust_level = \"trusted\"\n" % os.path.normcase(self.repo))
        self.assertEqual(fb.prepare_codex("T9", "my session", self.sub), (True, None))
        self.assertEqual([c[0] for c in seen[1]], ["thread/name/set"])
        fb.codex_rpc = lambda calls, cwd, wait=30: [None for _ in calls]  # Codex did not answer
        os.remove(self.config)
        self.assertEqual(fb.prepare_codex("T9", "", self.sub), (False, False))


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        import shutil
        shutil.rmtree(TMP, ignore_errors=True)
