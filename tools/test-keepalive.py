# Unit tests for Field Agents' keep-alive (agent-panel.pyw): an open idle session's prompt cache refreshed by a
# headless forked resume 10 minutes before it expires, 3 times at most per idle stretch.
#   - the conditions (keepalive_fits(), keepalive_due()) by fake clock: open, idle, not waiting, not on GLM or Codex,
#     not in WSL, not headless, a 1 h cache, a context over 100k, the setting on; due at 50 min, not at 49 nor once
#     the cache has expired;
#   - the count: 3 per stretch, each 50 min after the last one; a failed one ends the stretch;
#   - the reset: a real prompt (a newer `at`) starts a new stretch with 3 again;
#   - the cache warning (cache_stage(), cache_view(), cache_rings()): stage 1 neither shown nor sounded while a
#     keep-alive is to come, the warning counted from the keep-alive, the kept mark's figures, the warning back after
#     the last one or a failure;
#   - the run (Panel.keep_alive() with a fake claude): the command, the fork listed before it starts and gone after,
#     the record and the log, the session's own `at` and transcript untouched, outcomes (refreshed, missed, failed);
#   - every consumer leaving the keep-alive out: running() (the panel's sessions), drive-pause.py's live sessions,
#     its prompt and Stop hooks, prompt-recall.py; the prefix the hooks test is the text the panel sends.
# Run: python -I tools/test-keepalive.py
# Times are fake; nothing is sent to Anthropic, no process is started (subprocess is a fake), every path is on a
# temp folder. The .pyw is never imported (its one-instance mutex would raise the running panel): the functions are
# taken out by ast.
import ast, datetime, hashlib, importlib.util, json, os, re, subprocess, sys, tempfile, threading, time, unittest, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
NAMES = ("local", "CACHE_LEAD", "CACHE_LOOK", "USAGE_LINE", "KEEPALIVE_LEAD", "KEEPALIVE_MAX", "KEEPALIVE_MIN_TOKENS",
         "KEEPALIVE_WAIT", "KEEPALIVE_TEXT", "stretch_rec", "cache_base", "keepalive_fits", "keepalive_due",
         "cache_stage", "cache_view", "cache_rings", "keepalive_on", "keepalive_argv", "k_tokens", "keepalive_outcome",
         "keepalive_forks", "keepalive_load", "keepalive_save", "keepalive_log", "append_log", "cache_of", "running")
T0 = 1_800_000_000.0
MIN = 60


def panel(tmp):
    with open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    keep = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in NAMES or
            isinstance(n, ast.Assign) and any(getattr(t, "id", None) in NAMES for t in n.targets)]
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Panel")
    meths = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in ("check_keepalive", "keep_alive")]
    state = os.path.join(tmp, "state")
    ns = {"os": os, "json": json, "re": re, "datetime": datetime, "time": time, "threading": threading,
          "subprocess": subprocess, "shutil": __import__("shutil"), "uuid": uuid, "CREATE_NO_WINDOW": 0x08000000,
          "LOCAL": os.path.join(tmp, "claude-plus.local.json"), "STATE_DIR": state, "CLAUDE": tmp,
          "KEEPALIVE_FILE": os.path.join(state, "keepalive.json"), "KEEPALIVE_LOG": os.path.join(state, "keepalive.log"),
          "KEEPING": set(), "clean_env": lambda: {"X": "1"}, "folder_exists": lambda s: True,
          "alive": lambda pid: True, "is_voice_dir": lambda cwd: False, "is_test_dir": lambda cwd: False}
    # the module-level constants first (their own assignments name STATE_DIR, which the test sets instead)
    body = [n for n in keep if not (isinstance(n, ast.Assign) and any(
        getattr(t, "id", None) in ("KEEPALIVE_FILE", "KEEPALIVE_LOG", "KEEPING") for t in n.targets))]
    exec(compile(ast.Module(body=body, type_ignores=[]), "agent-panel.pyw", "exec"), ns)
    exec(compile(ast.Module(body=meths, type_ignores=[]), "agent-panel.pyw", "exec"), ns)
    return ns


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def stamp(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def session(idle_min=0, status="idle", tokens=250000, ttl=3600, now=T0, **kw):
    s = {"id": "s1", "status": status, "waiting": None, "at": now - idle_min * MIN, "distro": None, "headless": False,
         "cache": {"ttl": ttl, "tokens": tokens}, "cwd": "C:\\work", "path": None}
    s.update(kw)
    return s


def sent(s, n, last, ok=True):
    r = {"stretch": s["at"], "sent": n, "last": last}
    if ok:
        r["refreshed"] = last
    else:
        r["failed"] = "missed the cache"
    return r


class Conditions(unittest.TestCase):
    def setUp(self):
        self.p = panel(tempfile.mkdtemp(prefix="claude-plus-test-ka-"))

    def due(self, s, rec=None, at=None, on=True, away=False):
        return self.p["keepalive_due"](s, rec, at if at is not None else T0, on, away)

    def test_due_at_50_minutes_not_at_49_nor_after_expiry(self):
        s = session()
        self.assertFalse(self.due(s, at=T0 + 49 * MIN))
        self.assertTrue(self.due(s, at=T0 + 50 * MIN))
        self.assertTrue(self.due(s, at=T0 + 59 * MIN))
        self.assertFalse(self.due(s, at=T0 + 60 * MIN))  # expired: a keep-alive would write it all, as a prompt would

    def test_only_an_open_idle_session_on_claude(self):
        later = T0 + 52 * MIN
        for kw in ({"status": None}, {"status": "busy"}, {"status": "shell"}, {"status": "waiting"},
                   {"waiting": "permission"}, {"distro": "Ubuntu"}, {"headless": True}):
            self.assertFalse(self.due(session(**kw), at=later), kw)
        self.assertFalse(self.due(session(), at=later, away=True))  # on GLM or Codex
        self.assertTrue(self.due(session(), at=later))

    def test_context_over_100k_and_a_long_cache_only(self):
        later = T0 + 52 * MIN
        self.assertFalse(self.due(session(tokens=100000), at=later))
        self.assertTrue(self.due(session(tokens=100001), at=later))
        self.assertFalse(self.due(session(tokens=None), at=later))
        self.assertFalse(self.due(session(ttl=300), at=T0 + 1 * MIN))  # a 5-minute cache: never
        self.assertFalse(self.due(session(ttl=None), at=later))

    def test_off_by_default_and_with_the_setting(self):
        self.assertFalse(self.p["keepalive_on"]())
        self.assertFalse(self.due(session(), at=T0 + 52 * MIN, on=False))
        with open(self.p["LOCAL"], "w") as f:
            json.dump({"keepalive": True}, f)
        self.assertTrue(self.p["keepalive_on"]())
        with open(self.p["LOCAL"], "w") as f:
            json.dump({"keepalive": "yes"}, f)
        self.assertFalse(self.p["keepalive_on"]())  # only true turns it on

    def test_three_per_stretch_each_50_minutes_after_the_last(self):
        s = session()
        self.assertTrue(self.due(s, sent(s, 1, T0 + 50 * MIN), at=T0 + 100 * MIN))
        self.assertFalse(self.due(s, sent(s, 1, T0 + 50 * MIN), at=T0 + 99 * MIN))
        self.assertTrue(self.due(s, sent(s, 2, T0 + 100 * MIN), at=T0 + 150 * MIN))
        self.assertFalse(self.due(s, sent(s, 3, T0 + 150 * MIN), at=T0 + 200 * MIN))  # the fourth: never

    def test_a_failed_one_ends_the_stretch(self):
        s = session()
        self.assertFalse(self.due(s, sent(s, 1, T0 + 50 * MIN, ok=False), at=T0 + 55 * MIN))
        self.assertFalse(self.due(s, sent(s, 1, T0 + 50 * MIN, ok=False), at=T0 + 100 * MIN))

    def test_a_real_prompt_resets_the_count(self):
        s = session()
        rec = sent(s, 3, T0 + 150 * MIN)
        self.assertFalse(self.due(s, rec, at=T0 + 200 * MIN))
        s2 = dict(s, at=T0 + 190 * MIN)  # a prompt (and its reply) at 190 min: a new idle stretch
        self.assertIsNone(self.p["stretch_rec"](s2, rec))
        self.assertTrue(self.due(s2, rec, at=T0 + 240 * MIN))
        self.assertFalse(self.due(s2, rec, at=T0 + 239 * MIN))


class Warning(unittest.TestCase):
    def setUp(self):
        self.p = panel(tempfile.mkdtemp(prefix="claude-plus-test-ka-"))

    def stage(self, s, now, rec=None, keep=True):
        return self.p["cache_stage"](s, now, rec, keep and self.p["keepalive_fits"](s, rec))

    def test_stage_one_waits_for_the_keep_alive(self):
        s = session()
        self.assertEqual(self.stage(s, T0 + 46 * MIN), 0)  # a keep-alive comes at 50
        self.assertEqual(self.stage(s, T0 + 46 * MIN, keep=False), 1)
        self.assertEqual(self.stage(s, T0 + 56 * MIN), 2)  # it did not come: the last call shows
        self.assertEqual(self.stage(session(tokens=90000), T0 + 46 * MIN), 1)  # too small for one: warned as before

    def test_counted_from_the_keep_alive(self):
        s = session()
        rec = sent(s, 1, T0 + 50 * MIN)
        self.assertEqual(self.p["cache_base"](s, rec), T0 + 50 * MIN)
        self.assertEqual(self.stage(s, T0 + 100 * MIN, rec), 0)
        self.assertEqual(self.stage(s, T0 + 106 * MIN, rec), 2)
        self.assertEqual(self.stage(s, T0 + 111 * MIN, rec), 3)

    def test_after_the_last_one_or_a_failure_the_warning_is_back(self):
        s = session()
        last = sent(s, 3, T0 + 150 * MIN)
        self.assertEqual(self.stage(s, T0 + 196 * MIN, last), 1)
        failed = sent(s, 1, T0 + 50 * MIN, ok=False)
        self.assertEqual(self.p["cache_base"](s, failed), T0)  # a failed one refreshed nothing
        self.assertEqual(self.stage(s, T0 + 50.2 * MIN, failed), 1)

    def test_view_of_a_kept_cache(self):
        s = session()
        rec = sent(s, 1, T0 + 50 * MIN)
        v = self.p["cache_view"](s, T0 + 60 * MIN, rec, True, True)
        self.assertEqual(v, {"stage": 0, "ends": int((T0 + 110 * MIN) * 1000), "tokens": 250000,
                             "kept": {"n": 1, "of": 3, "last": int((T0 + 50 * MIN) * 1000), "ok": True, "left": 2}})
        v = self.p["cache_view"](s, T0 + 106 * MIN, sent(s, 3, T0 + 50 * MIN), True, True)
        self.assertEqual((v["stage"], v["kept"]["left"]), (2, 0))
        # the warning's setting off: what keep-alives did still shows, no stage
        v = self.p["cache_view"](s, T0 + 106 * MIN, rec, True, False)
        self.assertEqual(v["stage"], 0)
        # a failed one at stage 0: nothing to show yet (its stage 1 comes at once, as above)
        self.assertIsNone(self.p["cache_view"](s, T0 + 30 * MIN, sent(s, 1, T0 + 20 * MIN, ok=False), True, True))
        # the record of an older stretch shows nothing
        self.assertIsNone(self.p["cache_view"](dict(s, at=T0 + 55 * MIN), T0 + 60 * MIN, rec, True, True))
        # closed: nothing
        self.assertIsNone(self.p["cache_view"](dict(s, status=None), T0 + 60 * MIN, rec, True, True))

    def test_no_chime_while_a_keep_alive_is_to_come(self):
        rings = self.p["cache_rings"]
        s = session()
        loud, rung = rings({}, [s], T0 + 46 * MIN, True, {}, True)
        self.assertEqual(loud, 0)
        loud, rung = rings({}, [s], T0 + 46 * MIN, True, {}, False)  # keep-alive off: as before
        self.assertEqual(loud, 1)
        # after the third, the warning counts from it and sounds again
        rec = {"s1": sent(s, 3, T0 + 150 * MIN)}
        loud, rung = rings(rung, [s], T0 + 196 * MIN, True, rec, True)
        self.assertEqual(loud, 1)
        self.assertEqual(rung["s1"], (T0 + 150 * MIN, 1))
        loud, rung = rings(rung, [s], T0 + 197 * MIN, True, rec, True)
        self.assertEqual(loud, 0)  # once per stage


class Outcome(unittest.TestCase):
    def setUp(self):
        self.p = panel(tempfile.mkdtemp(prefix="claude-plus-test-ka-"))

    def res(self, read, wrote=1404, result="ok", turns=1, error=False):
        return json.dumps({"result": result, "num_turns": turns, "is_error": error,
                           "usage": {"input_tokens": 2, "cache_read_input_tokens": read,
                                     "cache_creation_input_tokens": wrote}})

    def test_outcomes(self):
        out = self.p["keepalive_outcome"]
        self.assertEqual(out(0, self.res(118630), "", 118632)[:2], (True, None))  # the measured run
        good, why, _ = out(0, self.res(31694), "", 118632)
        self.assertFalse(good)
        self.assertIn("missed the cache: read 32k of 119k", why)
        self.assertFalse(out(1, self.res(0, result="API Error: 529"), "", 118632)[0])
        self.assertIn("no answer (exit 1)", out(1, "", "boom", 118632)[1])
        self.assertIn("did more than reply", out(0, self.res(118630, turns=2), "", 118632)[1])
        self.assertFalse(out(None, "", "stopped after 180 s", 118632)[0])
        # a model that tried a tool anyway: refused, then --max-turns 1 ends the run (measured: exit 1,
        # subtype error_max_turns, is_error, no result, 2 turns): failed, so the stretch ends
        capped = json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True, "num_turns": 2,
                             "usage": {"input_tokens": 2, "cache_read_input_tokens": 104516,
                                       "cache_creation_input_tokens": 15529}})
        good, why, _ = out(1, capped, "", 104518)
        self.assertFalse(good)
        self.assertIn("failed (exit 1)", why)
        self.assertEqual(out(0, self.res(104516, 12814, result="ok"), "", 104518)[:2], (True, None))  # measured

    def test_text(self):
        text = self.p["KEEPALIVE_TEXT"]
        self.assertEqual(text, "Keep-alive from claude-plus. This is not a task: use no tool, do not continue or "
                               "check any work. Reply with the single word ok.")
        self.assertTrue(text.startswith("Keep-alive from claude-plus"))  # what the hooks know it by

    def test_command(self):
        argv = self.p["keepalive_argv"]("claude.exe", "SID", "FORK", "claude-opus-5-5")
        self.assertEqual(argv[:4], ["claude.exe", "-p", "--resume", "SID"])
        for flag in ("--fork-session", "--no-session-persistence"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--session-id") + 1], "FORK")
        self.assertEqual(json.loads(argv[argv.index("--settings") + 1]),
                         {"disableAllHooks": True, "permissions": {"ask": ["*"]}})  # every tool would ask ...
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")  # ... and nobody may: refused
        self.assertEqual(argv[argv.index("--max-turns") + 1], "1")  # one refused round at most
        for flag in ("--tools", "--disallowedTools", "--allowedTools", "--setting-sources"):
            self.assertNotIn(flag, argv)  # the tool list stays as it is, so the cache prefix does
        self.assertEqual(argv[argv.index("--model") + 1], "claude-opus-5-5")
        self.assertEqual(argv[-1], self.p["KEEPALIVE_TEXT"])
        self.assertNotIn("--dangerously-skip-permissions", argv)  # the fork may not run tools unasked
        self.assertNotIn("--model", self.p["keepalive_argv"]("claude.exe", "SID", "FORK", None))


class FakePopen:
    """claude -p as keep_alive() starts it: records the call and what the world looked like while it ran."""
    calls, answer, seen = [], None, []

    def __init__(self, argv, **kw):
        FakePopen.calls.append((argv, kw))
        self.pid, self.returncode = 4242, None
        self.argv = argv

    def communicate(self, timeout=None):
        ns = FakePopen.ns
        fork = self.argv[self.argv.index("--session-id") + 1]
        FakePopen.seen.append({"listed": fork in ns["keepalive_forks"](), "keeping": fork in ns["KEEPING"]})
        code, out = FakePopen.answer
        self.returncode = code
        return out.encode(), b""


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="claude-plus-test-ka-")
        self.p = panel(self.tmp)
        FakePopen.ns, FakePopen.calls, FakePopen.seen = self.p, [], []
        fake = type(subprocess)("subprocess")
        fake.Popen, fake.PIPE, fake.DEVNULL = FakePopen, subprocess.PIPE, subprocess.DEVNULL
        fake.TimeoutExpired, fake.run = subprocess.TimeoutExpired, lambda *a, **k: None
        self.p["subprocess"] = fake
        self.p["shutil"] = type(sys)("shutil")
        self.p["shutil"].which = lambda name: r"C:\fake\claude.exe"
        self.path = os.path.join(self.tmp, "s1.jsonl")
        with open(self.path, "w", encoding="utf-8") as f:
            compact = (",", ":")  # as Claude Code writes its lines
            f.write(json.dumps({"type": "user", "timestamp": stamp(T0 - 1), "message": {"content": "go"}},
                               separators=compact) + "\n")
            f.write(json.dumps({"type": "assistant", "timestamp": stamp(T0), "message": {
                "id": "m", "model": "claude-opus-5-5", "usage": {
                    "input_tokens": 2, "cache_read_input_tokens": 31694, "cache_creation_input_tokens": 86936,
                    "cache_creation": {"ephemeral_1h_input_tokens": 86936}}}}, separators=compact) + "\n")
        p = self.p

        class P:
            check_keepalive, keep_alive = p["check_keepalive"], p["keep_alive"]
            returns = {}

            def push(self):
                pass
        self.panel = P()
        self.panel.kept, self.panel.keep_busy = p["keepalive_load"](), False
        self.s = session(path=self.path, tokens=118632, now=T0)

    def answer(self, read, code=0):
        FakePopen.answer = (code, json.dumps({"result": "ok", "num_turns": 1, "is_error": code != 0, "usage": {
            "input_tokens": 2, "cache_read_input_tokens": read, "cache_creation_input_tokens": 1404}}))

    def test_a_refresh_is_recorded_and_the_fork_listed_only_while_it_runs(self):
        self.answer(118630)
        before = digest(self.path)
        self.panel.keep_alive(dict(self.s))
        argv, kw = FakePopen.calls[0]
        self.assertEqual(argv[argv.index("--model") + 1], "claude-opus-5-5")  # the transcript's model
        self.assertEqual(kw["cwd"], "C:\\work")
        self.assertEqual(kw["creationflags"], 0x08000000)  # no window
        self.assertEqual(FakePopen.seen, [{"listed": True, "keeping": True}])
        self.assertEqual(self.p["keepalive_forks"](), set())
        self.assertEqual(self.p["KEEPING"], set())
        rec = self.p["keepalive_load"]()["s1"]
        self.assertEqual((rec["stretch"], rec["sent"]), (T0, 1))
        self.assertTrue(rec["refreshed"] and "failed" not in rec)
        self.assertEqual(digest(self.path), before)
        log = text(self.p["KEEPALIVE_LOG"])
        self.assertRegex(log, r"s1 keep-alive 1/3, context 119k: refreshed \(read 119k, wrote 1k, reply 'ok'")
        self.assertFalse(self.panel.keep_busy)

    def test_a_miss_ends_the_stretch(self):
        self.answer(31694)
        self.panel.keep_alive(dict(self.s))
        rec = self.p["keepalive_load"]()["s1"]
        self.assertIn("missed the cache", rec["failed"])
        self.assertNotIn("refreshed", rec)
        self.assertIn("no more in this idle stretch", text(self.p["KEEPALIVE_LOG"]))
        self.assertFalse(self.p["keepalive_due"](self.s, rec, T0 + 55 * MIN, True))

    def test_a_run_that_cannot_start_still_counts(self):
        def boom(*a, **k):
            raise OSError("not found")
        self.p["subprocess"].Popen = boom
        self.panel.keep_alive(dict(self.s))
        rec = self.p["keepalive_load"]()["s1"]
        self.assertEqual(rec["sent"], 1)
        self.assertIn("could not run", rec["failed"])
        self.assertEqual(self.p["keepalive_forks"](), set())

    def test_check_starts_one_when_due_and_only_when_on(self):
        started = []
        self.p["threading"] = type(sys)("threading")
        self.p["threading"].Thread = lambda target, args, daemon: type("T", (), {"start": lambda _s: started.append(args[0]["id"])})()
        real_time = self.p["time"]
        self.p["time"] = type(sys)("time")
        self.p["time"].time = lambda: T0 + 50 * MIN
        try:
            self.panel.sessions = [self.s]
            self.panel.check_keepalive()
            self.assertEqual(started, [])  # off
            with open(self.p["LOCAL"], "w") as f:
                json.dump({"keepalive": True}, f)
            self.panel.returns = {"s1": {"via": "codex"}}
            self.panel.check_keepalive()
            self.assertEqual(started, [])  # on Codex
            self.panel.returns = {}
            self.panel.check_keepalive()
            self.assertEqual(started, ["s1"])
            self.panel.check_keepalive()
            self.assertEqual(started, ["s1"])  # one at a time
        finally:
            self.p["time"] = real_time

    def test_counts_survive_a_restart(self):
        self.answer(118630)
        self.panel.keep_alive(dict(self.s))
        self.assertEqual(self.p["keepalive_load"]()["s1"]["sent"], 1)
        self.panel.kept = self.p["keepalive_load"]()  # a restarted panel
        s = dict(self.s)
        self.assertFalse(self.p["keepalive_due"](s, self.panel.kept["s1"], T0 + 60 * MIN, True))  # refreshed at "now"


class Consumers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="claude-plus-test-ka-")
        self.p = panel(self.tmp)
        self.sessions = os.path.join(self.tmp, "sessions")
        os.makedirs(self.sessions)
        for pid, sid in ((100, "real"), (200, "fork")):
            with open(os.path.join(self.sessions, "%d.json" % pid), "w") as f:
                json.dump({"pid": pid, "sessionId": sid, "cwd": "C:\\work", "status": "idle",
                           "statusUpdatedAt": T0 * 1000}, f)

    def test_the_panel_leaves_a_running_fork_out(self):
        self.p["KEEPING"].add("fork")
        self.assertEqual(set(self.p["running"]()), {"real"})
        self.p["KEEPING"].clear()
        self.assertEqual(set(self.p["running"]()), {"real", "fork"})

    def test_drive_pause_leaves_keep_alives_out(self):
        dp = load("drive_pause", os.path.join(ROOT, "drive-pause.py"))
        dp.SESSIONS, dp.LOCAL = self.sessions, os.path.join(self.tmp, "claude-plus.local.json")
        dp.STATE_DIR = os.path.join(self.tmp, "dp")
        dp.ACTIVITY, dp.STATE = os.path.join(dp.STATE_DIR, "activity.json"), os.path.join(dp.STATE_DIR, "state.json")
        dp.LOG = os.path.join(dp.STATE_DIR, "drive-pause.log")
        dp.KEEPALIVE_FILE = self.p["KEEPALIVE_FILE"]
        dp.alive = lambda pid: True
        self.assertEqual(len(dp.sessions()), 2)
        self.p["keepalive_save"]({}, {"fork": {"session": "real", "at": T0}})  # as keep_alive() lists it
        self.assertEqual(len(dp.sessions()), 1)
        with open(dp.LOCAL, "w") as f:
            json.dump({"drive_pause": True}, f)
        spawned = []
        dp.spawn = lambda argv: spawned.append(argv)
        dp.on_prompt({"session_id": "fork", "prompt": self.p["KEEPALIVE_TEXT"]})
        dp.on_stop({"session_id": "fork"})
        self.assertFalse(os.path.exists(dp.ACTIVITY))
        self.assertEqual(spawned, [])
        dp.on_stop({"session_id": "real"})  # a real reply still counts
        self.assertTrue(os.path.exists(dp.ACTIVITY))
        self.assertTrue(self.p["KEEPALIVE_TEXT"].startswith(dp.KEEPALIVE_PREFIX))

    def test_prompt_recall_leaves_the_keep_alive_out(self):
        home = os.path.join(self.tmp, "home")
        env = dict(os.environ, USERPROFILE=home, HOME=home)
        script = os.path.join(ROOT, "prompt-recall.py")
        for sid, text in (("a", self.p["KEEPALIVE_TEXT"]), ("b", "a real prompt")):
            subprocess.run([sys.executable, "-I", script], input=json.dumps({"session_id": sid, "prompt": text}).encode(),
                           env=env, capture_output=True, timeout=30)
        state = os.path.join(home, ".claude", "state", "context")
        self.assertFalse(os.path.exists(os.path.join(state, "a.prompt.json")))
        self.assertTrue(os.path.exists(os.path.join(state, "b.prompt.json")))


if __name__ == "__main__":
    unittest.main(verbosity=1)
