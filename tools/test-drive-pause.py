# Unit tests for the Google Drive pause (drive-pause.py):
#   - off by default: the hooks note nothing and start nothing, the watcher removes its task;
#   - "in use" = a live session mid-turn (busy capped at BUSY_CAP, dead pids and the dictation host ignored), or a
#     prompt, finished reply or session status change within drive_idle_minutes;
#   - idle: Drive is started once per idle stretch, never when it already runs, never again after the user quit it
#     in the same stretch, and "not installed" is logged once;
#   - a prompt quits only the Drive claude-plus started (pid + creation time), through a detached helper; a Drive
#     the user started is left alone and logged once;
#   - the quit asks Drive (--quit) first and stops it with its children only when it stays;
#   - the watcher task's XML.
# Run: python -I tools/test-drive-pause.py
# drive-pause.py is loaded as a module with every path on a temp folder; processes, Drive, schtasks and spawning are
# fakes: no real process is started, stopped or touched, and no scheduled task is made.
import importlib.util, json, os, sys, tempfile, time, unittest
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="cpfa-drive-")


def load():
    spec = importlib.util.spec_from_file_location("drive_pause", os.path.join(HERE, "..", "drive-pause.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


dp = load()
MIN = 60


class World:
    """Fake processes: pid -> creation time; which of them are GoogleDriveFS.exe."""

    def __init__(self):
        self.procs, self.drive, self.spawned, self.started, self.quits = {}, set(), [], [], []
        self.tasks, self.exe, self.next_pid = [], r"C:\fake\GoogleDriveFS.exe", 5000

    def new(self, drive=False):
        self.next_pid += 4
        self.procs[self.next_pid] = self.next_pid * 1000
        if drive:
            self.drive.add(self.next_pid)
        return self.next_pid

    def end(self, pid):
        self.procs.pop(pid, None)
        self.drive.discard(pid)


class Base(unittest.TestCase):
    def setUp(self):
        root = tempfile.mkdtemp(dir=TMP)
        dp.LOCAL = os.path.join(root, "claude-plus.local.json")
        dp.SESSIONS = os.path.join(root, "sessions")
        dp.STATE_DIR = os.path.join(root, "state")
        dp.STATE = os.path.join(dp.STATE_DIR, "state.json")
        dp.ACTIVITY = os.path.join(dp.STATE_DIR, "activity.json")
        dp.LOG = os.path.join(dp.STATE_DIR, "drive-pause.log")
        dp.VOICE_DIR = os.path.join(root, "voice-host")
        os.makedirs(dp.SESSIONS)
        w = self.w = World()
        dp.created = lambda pid: w.procs.get(int(pid))
        dp.alive = lambda pid: int(pid) in w.procs
        dp.drive_procs = lambda: {p: 1 for p in w.drive}
        dp.drive_running = lambda: bool(w.drive)
        dp.image = lambda pid: w.exe if int(pid) in w.drive else None
        dp.find_exe = lambda: w.exe
        dp.spawn = lambda args, cwd=None: w.spawned.append(args)
        dp.register_task = lambda: w.tasks.append("register") or self.note_task()
        dp.remove_task = lambda: w.tasks.append("remove")

        def start(exe):
            pid = w.new(drive=True)
            w.started.append(exe)
            return pid, w.procs[pid]
        dp.start_drive = start

        def quit_(state):
            w.quits.append(state["pid"])
            w.end(state["pid"])
            return "quit"
        dp.quit_drive = quit_

    def note_task(self):
        dp.write(os.path.join(dp.STATE_DIR, "task.json"), {"script": os.path.abspath(dp.__file__)})
        return True

    def config(self, **o):
        with open(dp.LOCAL, "w") as f:
            json.dump(o, f)

    def on(self, **extra):
        self.config(drive_pause=True, **extra)
        self.note_task()

    def activity(self, ago):
        dp.write(dp.ACTIVITY, {"at": time.time() - ago, "what": "prompt"})

    def session(self, status="idle", since_ago=None, alive=True, cwd=None, waiting=None):
        pid = self.w.new() if alive else 99999
        o = {"pid": pid, "sessionId": "s%d" % pid, "status": status, "cwd": cwd or TMP}
        if since_ago is not None:
            o["statusUpdatedAt"] = (time.time() - since_ago) * 1000
        if waiting:
            o["waitingFor"] = waiting
        with open(os.path.join(dp.SESSIONS, "%d.json" % pid), "w") as f:
            json.dump(o, f)
        return pid

    def state(self):
        return dp.read(dp.STATE)

    def logged(self):
        try:
            with open(dp.LOG, encoding="utf-8") as f:
                return f.read().splitlines()
        except OSError:
            return []

    def ours_running(self):
        pid = self.w.new(drive=True)
        dp.write(dp.STATE, {"pid": pid, "created": self.w.procs[pid], "started_for": time.time() - 40 * MIN})
        return pid


class Settings(Base):
    def test_off_by_default(self):
        self.assertEqual(dp.settings(), (False, 30 * MIN))
        self.config(drive_pause=True)
        self.assertEqual(dp.settings(), (True, 30 * MIN))
        self.config(drive_pause="yes")
        self.assertFalse(dp.settings()[0])

    def test_idle_minutes(self):
        for value, seconds in ((10, 10 * MIN), (0.5, 30), (0, 30 * MIN), (-5, 30 * MIN), ("20", 30 * MIN),
                               (True, 30 * MIN), (None, 30 * MIN)):
            self.config(drive_pause=True, drive_idle_minutes=value)
            self.assertEqual(dp.settings()[1], seconds, value)

    def test_not_json(self):
        with open(dp.LOCAL, "w") as f:
            f.write("{nope")
        self.assertEqual(dp.settings(), (False, 30 * MIN))


class Off(Base):
    def test_hooks_do_nothing(self):
        self.ours_running()
        dp.on_prompt({"session_id": "abcdef123"})
        dp.on_stop({"session_id": "abcdef123"})
        self.assertFalse(os.path.exists(dp.ACTIVITY))
        self.assertEqual(self.w.spawned, [])
        self.assertEqual(self.w.quits, [])

    def test_tick_removes_task_and_leaves_drive(self):
        pid = self.ours_running()
        dp.tick()
        dp.tick()
        self.assertEqual(self.w.tasks, ["remove", "remove"])
        self.assertEqual(self.w.quits, [])
        self.assertIn(pid, self.w.drive)
        self.assertEqual(len(self.logged()), 1)
        self.assertIn("off", self.logged()[0])


class Prompt(Base):
    def test_notes_activity(self):
        self.on()
        dp.on_prompt({"session_id": "abcdef123"})
        a = dp.read(dp.ACTIVITY)
        self.assertAlmostEqual(a["at"], time.time(), delta=2)
        self.assertEqual((a["what"], a["session"]), ("prompt", "abcdef12"))
        dp.on_stop({"session_id": "x"})
        self.assertEqual(dp.read(dp.ACTIVITY)["what"], "reply")

    def test_ours_quit_by_helper(self):
        self.on()
        self.ours_running()
        dp.on_prompt({"session_id": "abcdef123"})
        self.assertEqual(len(self.w.spawned), 1)
        self.assertEqual(self.w.spawned[0][-2:], ["quit", "abcdef12"])
        self.assertEqual(self.w.quits, [], "the hook itself never waits for the quit")

    def test_helper_quits(self):
        self.on()
        pid = self.ours_running()
        dp.on_quit("abcdef12")
        self.assertEqual(self.w.quits, [pid])
        self.assertIsNone(self.state()["pid"])
        self.assertTrue(any("prompt in session abcdef12" in l for l in self.logged()))

    def test_users_drive_left_alone(self):
        self.on()
        self.w.new(drive=True)
        dp.on_prompt({"session_id": "abcdef123"})
        dp.on_prompt({"session_id": "abcdef123"})
        self.assertEqual(self.w.spawned, [])
        self.assertEqual(self.w.quits, [])
        lines = self.logged()
        self.assertEqual(len(lines), 1)
        self.assertIn("started by the user", lines[0])

    def test_reused_pid_is_not_ours(self):
        self.on()
        pid = self.w.new(drive=True)
        dp.write(dp.STATE, {"pid": pid, "created": self.w.procs[pid] - 1})
        dp.on_prompt({"session_id": "abcdef123"})
        self.assertEqual(self.w.spawned, [])

    def test_registers_missing_task(self):
        self.config(drive_pause=True)
        dp.on_prompt({"session_id": "abcdef123"})
        self.assertEqual(len(self.w.spawned), 1)
        dp.on_quit("abcdef12")
        self.assertEqual(self.w.tasks, ["register"])
        dp.on_prompt({"session_id": "abcdef123"})
        self.assertEqual(len(self.w.spawned), 1, "registered once, then no helper")


class Tick(Base):
    def test_starts_after_idle(self):
        self.on()
        self.activity(31 * MIN)
        dp.tick()
        self.assertEqual(self.w.started, [self.w.exe])
        s = self.state()
        self.assertTrue(dp.ours(s))
        self.assertAlmostEqual(s["started_for"], time.time() - 31 * MIN, delta=2)
        dp.tick()
        self.assertEqual(len(self.w.started), 1, "running: not started twice")

    def test_not_before_delay(self):
        self.on()
        self.activity(29 * MIN)
        dp.tick()
        self.assertEqual(self.w.started, [])

    def test_own_delay(self):
        self.on(drive_idle_minutes=10)
        self.activity(11 * MIN)
        dp.tick()
        self.assertEqual(len(self.w.started), 1)

    def test_working_session_blocks(self):
        self.on()
        self.activity(60 * MIN)
        self.session("busy", since_ago=120 * MIN)
        dp.tick()
        self.assertEqual(self.w.started, [])

    def test_waiting_for_answer_blocks(self):
        self.on()
        self.activity(60 * MIN)
        self.session("busy", since_ago=50 * MIN, waiting="permission")
        dp.tick()
        self.assertEqual(self.w.started, [])

    def test_hung_session_capped(self):
        self.on()
        self.activity(5 * 3600)
        self.session("busy", since_ago=dp.BUSY_CAP + MIN)
        dp.tick()
        self.assertEqual(len(self.w.started), 1)

    def test_dead_session_ignored(self):
        self.on()
        self.activity(60 * MIN)
        self.session("busy", since_ago=MIN, alive=False)
        dp.tick()
        self.assertEqual(len(self.w.started), 1)

    def test_dictation_host_ignored(self):
        self.on()
        self.activity(60 * MIN)
        self.session("busy", since_ago=MIN, cwd=dp.VOICE_DIR)
        dp.tick()
        self.assertEqual(len(self.w.started), 1)

    def test_session_status_change_is_activity(self):
        self.on()
        self.activity(60 * MIN)
        self.session("idle", since_ago=10 * MIN)  # a turn that ended 10 min ago (Esc, or no hook in that session)
        dp.tick()
        self.assertEqual(self.w.started, [])

    def test_users_drive_left_alone(self):
        self.on()
        self.activity(60 * MIN)
        self.w.new(drive=True)
        dp.tick()
        dp.tick()
        self.assertEqual(self.w.started, [])
        self.assertEqual(len(self.logged()), 1)
        self.assertIn("started by the user", self.logged()[0])

    def test_users_drive_left_alone_while_in_use(self):
        self.on()
        self.activity(MIN)
        self.w.new(drive=True)
        dp.tick()
        self.assertEqual(self.w.quits, [])
        self.assertIn("left alone", self.logged()[0])

    def test_not_again_after_user_quit(self):
        self.on()
        self.activity(31 * MIN)
        dp.tick()
        pid = self.state()["pid"]
        self.w.end(pid)  # the user quits Drive
        dp.tick()
        dp.tick()
        self.assertEqual(len(self.w.started), 1)
        self.assertEqual(sum("not started again" in l for l in self.logged()), 1)
        # Claude used, then idle again: a new stretch
        self.activity(31 * MIN - 5)
        dp.tick()
        self.assertEqual(len(self.w.started), 2)

    def test_not_again_without_any_activity(self):
        self.on()  # never used since: last activity 0
        dp.tick()
        self.w.end(self.state()["pid"])
        dp.tick()
        self.assertEqual(len(self.w.started), 1)

    def test_closed_session_does_not_open_a_stretch(self):
        self.on()
        self.activity(60 * MIN)
        sfile = self.session("idle", since_ago=40 * MIN)
        dp.tick()
        self.w.end(self.state()["pid"])
        os.remove(os.path.join(dp.SESSIONS, "%d.json" % sfile))  # last activity drops back to 60 min
        dp.tick()
        self.assertEqual(len(self.w.started), 1)

    def test_in_use_quits_ours(self):
        self.on()
        pid = self.ours_running()
        self.activity(2 * MIN)
        dp.tick()
        self.assertEqual(self.w.quits, [pid])
        self.assertTrue(any("activity 2 min ago" in l for l in self.logged()))

    def test_working_quits_ours(self):
        self.on()
        pid = self.ours_running()
        self.activity(60 * MIN)
        self.session("busy", since_ago=45 * MIN)
        dp.tick()
        self.assertEqual(self.w.quits, [pid])

    def test_not_installed_logged_once(self):
        self.on()
        self.activity(60 * MIN)
        dp.find_exe = lambda: None
        dp.tick()
        dp.tick()
        self.assertEqual(self.w.started, [])
        self.assertEqual(len(self.logged()), 1)
        self.assertIn("not installed", self.logged()[0])

    def test_full_cycle(self):
        self.on()
        self.activity(31 * MIN)
        dp.tick()                                   # idle: started
        dp.on_prompt({"session_id": "abcdef123"})   # used again: the helper is spawned
        self.assertEqual(self.w.spawned[-1][-2], "quit")
        dp.on_quit("abcdef12")                      # the helper quits it
        self.assertFalse(self.w.drive)
        dp.tick()                                   # in use: nothing
        self.assertEqual(len(self.w.started), 1)
        self.activity(31 * MIN)                     # idle again
        dp.tick()
        self.assertEqual(len(self.w.started), 2)


class QuitDrive(unittest.TestCase):
    """The real quit_drive() with subprocess and processes faked."""

    def setUp(self):
        self.dp = load()
        self.calls, self.alive_after = [], None
        d = self.dp
        d.QUIT_WAIT = 1
        d.image = lambda pid: r"C:\fake\GoogleDriveFS.exe"
        d.find_exe = lambda: None
        self.t0 = time.time()

        def created(pid):
            if self.alive_after is not None and any(c[1:2] == ["--quit"] for c in self.calls):
                return None if time.time() > self.quit_at + self.alive_after else 7
            return 7
        d.created = created

        class Sub:
            DEVNULL = -3
            SubprocessError = Exception

            @staticmethod
            def run(args, **kw):
                self.calls.append(list(args))
                if args[1:2] == ["--quit"]:
                    self.quit_at = time.time()
        d.subprocess = Sub

    def test_clean_quit(self):
        self.alive_after = 0.2
        self.assertEqual(self.dp.quit_drive({"pid": 42, "created": 7}), "quit")
        self.assertEqual(self.calls, [[r"C:\fake\GoogleDriveFS.exe", "--quit"]])

    def test_forced_when_it_stays(self):
        self.alive_after = 999
        self.assertEqual(self.dp.quit_drive({"pid": 42, "created": 7}), "stopped")
        self.assertEqual(self.calls[-1], ["taskkill", "/F", "/T", "/PID", "42"])

    def test_gone_already(self):
        self.dp.created = lambda pid: None
        self.assertEqual(self.dp.quit_drive({"pid": 42, "created": 7}), "gone")
        self.assertEqual(self.calls, [])


class TaskXml(unittest.TestCase):
    def test_xml(self):
        x = dp.task_xml()
        root = ET.fromstring(x.split("?>", 1)[1])
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        self.assertEqual(root.find(".//t:Repetition/t:Interval", ns).text, "PT%dM" % dp.TICK_MINUTES)
        self.assertEqual(root.find(".//t:DisallowStartIfOnBatteries", ns).text, "false")
        self.assertEqual(root.find(".//t:MultipleInstancesPolicy", ns).text, "IgnoreNew")
        self.assertEqual(root.find(".//t:LogonType", ns).text, "InteractiveToken")
        self.assertTrue(root.find(".//t:Command", ns).text.lower().endswith("pythonw.exe"))
        self.assertEqual(root.find(".//t:Arguments", ns).text, '"%s" tick' % os.path.abspath(dp.__file__))


if __name__ == "__main__":
    unittest.main(verbosity=1)
