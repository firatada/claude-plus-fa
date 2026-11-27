# Unit tests for Field Agents' settings view (the gear right of Status), its Python half in agent-panel.pyw:
#   - the rows: the cache warning's switch (on unless set to false, saved like the others, no task run); the Google Drive pause's switch and its idle delay, with the values in force, the default when unset or
#     not a positive number, and the switch blocked with its reason when Google Drive for desktop is not installed
#     (still able to turn off a pause that was turned on by hand);
#   - persistence: a change is written to claude-plus.local.json at once, beside the user's other keys, and read back
#     by a fresh start; a file that is not valid JSON is left alone and the change is refused with what to do;
#   - effect: turning the pause on or off runs drive-pause.py's `task` mode (the watcher task registered or removed),
#     and drive-pause.py itself then reads the new choice; nothing is saved for a value out of range;
#   - the installer never writes claude-plus.local.json, so a reinstall keeps the choice.
# Run: python -I tools/test-settings.py
# The .pyw is never imported (its one-instance mutex would raise the running panel): the functions are taken out by
# ast. Every path is on a temp folder; subprocess is a fake, so no scheduled task is made and Drive is never touched.
import ast, importlib.util, json, os, subprocess, sys, tempfile, threading, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
NAMES = ("local", "save_local", "DRIVE_MINUTES", "MINUTES_MAX", "NO_DRIVE", "drive_pause_py", "drive_found",
         "idle_minutes", "settings_rows", "drive_task", "set_setting", "ok", "err", "cache_warning_on", "keepalive_on")

FAKE_DRIVE = """IDLE_MINUTES = 30
import os
def find_exe():
    flag = os.path.join(os.path.dirname(os.path.abspath(__file__)), "installed")
    return r"C:\\fake\\GoogleDriveFS.exe" if os.path.exists(flag) else None
"""


class Proc:
    """subprocess as the panel uses it: run() recorded, never started."""
    DEVNULL, SubprocessError = subprocess.DEVNULL, subprocess.SubprocessError

    def __init__(self):
        self.runs = []

    def run(self, args, **kw):
        self.runs.append((args, kw))
        return subprocess.CompletedProcess(args, 0, "", "")


class Thread:
    """threading.Thread that runs its target at once, so a test sees what it did."""

    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


def panel(tmp, proc):
    """The settings functions out of agent-panel.pyw, with claude-plus.local.json and drive-pause.py on `tmp`."""
    with open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    keep = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in NAMES or
            isinstance(n, ast.Assign) and any(getattr(t, "id", None) in NAMES for t in n.targets)]
    fake_threading = type("T", (), {"Thread": Thread, "Lock": threading.Lock})
    ns = {"os": os, "json": json, "sys": sys, "importlib": importlib, "subprocess": proc, "threading": fake_threading,
          "LOCAL": os.path.join(tmp, "claude-plus.local.json"), "DRIVE_PY": (os.path.join(tmp, "drive-pause.py"),),
          "CREATE_NO_WINDOW": 0x08000000, "task_lock": threading.Lock()}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "agent-panel.pyw", "exec"), ns)
    return ns


def real_drive_pause(local):
    """The real drive-pause.py, reading `local` as its claude-plus.local.json."""
    spec = importlib.util.spec_from_file_location("drive_pause_real", os.path.join(ROOT, "drive-pause.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.LOCAL = local
    return m


class Settings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cpfa-settings-")
        with open(os.path.join(self.tmp, "drive-pause.py"), "w", encoding="utf-8") as f:
            f.write(FAKE_DRIVE)
        self.proc = Proc()
        self.p = panel(self.tmp, self.proc)
        self.local = self.p["LOCAL"]

    def installed(self, on=True):
        flag = os.path.join(self.tmp, "installed")
        if on:
            open(flag, "w").close()
        elif os.path.exists(flag):
            os.remove(flag)

    def write_local(self, obj_or_text):
        with open(self.local, "w", encoding="utf-8") as f:
            f.write(obj_or_text if isinstance(obj_or_text, str) else json.dumps(obj_or_text))

    def read_local(self):
        with open(self.local, encoding="utf-8") as f:
            return json.load(f)

    def rows(self):
        return {r["key"]: r for r in self.p["settings_rows"]()}

    # ----- the rows -----
    def test_default_is_off_with_30_minutes(self):
        self.installed()
        r = self.rows()
        self.assertEqual(list(r), ["cache_warning", "keepalive", "drive_pause", "drive_idle_minutes"])
        self.assertIs(r["drive_pause"]["value"], False)
        self.assertEqual(r["drive_pause"]["kind"], "switch")
        self.assertIsNone(r["drive_pause"]["blocked"])
        self.assertEqual(r["drive_idle_minutes"]["value"], 30)
        self.assertEqual(r["drive_idle_minutes"]["default"], 30)
        self.assertEqual(r["drive_idle_minutes"]["kind"], "minutes")
        self.assertFalse(os.path.exists(self.local), "showing the rows writes nothing")

    def test_rows_show_the_values_in_force(self):
        self.installed()
        self.write_local({"drive_pause": True, "drive_idle_minutes": 45, "rule": "keep"})
        r = self.rows()
        self.assertIs(r["drive_pause"]["value"], True)
        self.assertEqual(r["drive_idle_minutes"]["value"], 45)
        self.assertIn("Turns it off", r["drive_pause"]["tip"])

    def test_bad_minutes_read_as_the_default(self):
        self.installed()
        for bad in (0, -5, "45", True, None, [1]):
            self.write_local({"drive_idle_minutes": bad})
            self.assertEqual(self.rows()["drive_idle_minutes"]["value"], 30, bad)

    def test_no_drive_blocks_both_rows_with_the_reason(self):
        self.installed(False)
        r = self.rows()
        self.assertEqual(r["drive_pause"]["blocked"], self.p["NO_DRIVE"])
        self.assertEqual(r["drive_idle_minutes"]["blocked"], self.p["NO_DRIVE"])
        out = self.p["set_setting"]("drive_pause", True)
        self.assertTrue(out["err"])
        self.assertFalse(os.path.exists(self.local), "a refused change writes nothing")
        self.assertEqual(self.proc.runs, [])

    def test_no_drive_but_on_by_hand_can_still_be_turned_off(self):
        self.installed(False)
        self.write_local({"drive_pause": True})
        r = self.rows()["drive_pause"]
        self.assertIsNone(r["blocked"])
        self.assertEqual(r["why"], self.p["NO_DRIVE"])
        out = self.p["set_setting"]("drive_pause", False)
        self.assertFalse(out["err"])
        self.assertIs(self.read_local()["drive_pause"], False)

    # ----- the cache warning's switch -----
    def test_cache_warning_is_on_by_default(self):
        r = self.rows()["cache_warning"]
        self.assertEqual(r["kind"], "switch")
        self.assertIs(r["value"], True)
        self.assertIsNone(r["blocked"], "it needs nothing installed")
        self.assertIn("Turns it off", r["tip"])
        self.assertFalse(os.path.exists(self.local), "showing the rows writes nothing")
        for v in (None, 0, "no", True):  # only false turns it off
            self.write_local({"cache_warning": v})
            self.assertIs(self.rows()["cache_warning"]["value"], True, v)

    def test_cache_warning_off_is_saved_and_survives_a_restart(self):
        self.write_local({"rule": "keep"})
        out = self.p["set_setting"]("cache_warning", False)
        self.assertFalse(out["err"], out)
        self.assertEqual(self.read_local(), {"rule": "keep", "cache_warning": False})
        self.assertIs(self.p["cache_warning_on"](), False)
        again = panel(self.tmp, Proc())
        self.assertIs({r["key"]: r for r in again["settings_rows"]()}["cache_warning"]["value"], False)
        self.assertIn("Turns it on", self.rows()["cache_warning"]["tip"])
        out = self.p["set_setting"]("cache_warning", True)
        self.assertFalse(out["err"], out)
        self.assertIs(self.p["cache_warning_on"](), True)
        self.assertEqual(self.proc.runs, [], "no task: the panel reads the switch on every pass")

    def test_cache_warning_wrong_type_or_bad_file_is_refused(self):
        for bad in (1, "off", None):
            self.assertTrue(self.p["set_setting"]("cache_warning", bad)["err"], bad)
        self.assertFalse(os.path.exists(self.local))
        self.write_local("{not json")
        self.assertTrue(self.p["set_setting"]("cache_warning", False)["err"])
        self.assertIs(self.p["cache_warning_on"](), True, "an unreadable file leaves the default")

    # ----- persistence -----
    def test_switch_on_is_saved_beside_other_keys_and_survives_a_restart(self):
        self.installed()
        self.write_local({"rule": "C:/somewhere/CLAUDE.md", "paste_keys": False})
        out = self.p["set_setting"]("drive_pause", True)
        self.assertFalse(out["err"], out)
        self.assertIn("30 min", out["t"])
        self.assertEqual(self.read_local(), {"rule": "C:/somewhere/CLAUDE.md", "paste_keys": False, "drive_pause": True})
        self.assertIs({r["key"]: r for r in out["rows"]}["drive_pause"]["value"], True)
        # a fresh panel (a restart) reads the same choice
        again = panel(self.tmp, Proc())
        self.assertIs({r["key"]: r for r in again["settings_rows"]()}["drive_pause"]["value"], True)

    def test_minutes_saved_and_read_back(self):
        self.installed()
        out = self.p["set_setting"]("drive_idle_minutes", 45)
        self.assertFalse(out["err"], out)
        self.assertEqual(self.read_local(), {"drive_idle_minutes": 45})
        self.assertEqual(self.rows()["drive_idle_minutes"]["value"], 45)
        self.assertEqual(self.proc.runs, [], "the delay needs no task change: drive-pause.py reads it every run")

    def test_minutes_out_of_range_are_refused(self):
        self.installed()
        for bad in (0, -1, 1000, 4.5, "45", True, None):
            out = self.p["set_setting"]("drive_idle_minutes", bad)
            self.assertTrue(out["err"], bad)
        self.assertFalse(os.path.exists(self.local))
        self.assertFalse(self.p["set_setting"]("drive_idle_minutes", 999)["err"])
        self.assertFalse(self.p["set_setting"]("drive_idle_minutes", 1)["err"])

    def test_invalid_json_is_left_alone(self):
        self.installed()
        self.write_local("{not json")
        out = self.p["set_setting"]("drive_pause", True)
        self.assertTrue(out["err"])
        self.assertIn("not valid JSON", out["t"])
        with open(self.local, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{not json")
        self.assertEqual(self.proc.runs, [])

    def test_unknown_key_and_wrong_type_are_refused(self):
        self.installed()
        self.assertTrue(self.p["set_setting"]("nope", 1)["err"])
        self.assertTrue(self.p["set_setting"]("drive_pause", 1)["err"])
        self.assertFalse(os.path.exists(self.local))

    # ----- effect -----
    def test_switch_runs_the_task_mode_of_drive_pause(self):
        self.installed()
        self.p["set_setting"]("drive_pause", True)
        self.p["set_setting"]("drive_pause", False)
        self.assertEqual(len(self.proc.runs), 2)
        for args, kw in self.proc.runs:
            self.assertEqual(args, [sys.executable, os.path.join(self.tmp, "drive-pause.py"), "task"])
            self.assertEqual(kw.get("creationflags"), 0x08000000, "no console window")
            self.assertEqual(kw.get("stdin"), subprocess.DEVNULL)

    def test_drive_pause_reads_the_choice_and_its_task_mode_follows_it(self):
        self.installed()
        self.p["set_setting"]("drive_pause", True)
        self.p["set_setting"]("drive_idle_minutes", 12)
        dp = real_drive_pause(self.local)
        self.assertEqual(dp.settings(), (True, 12 * 60))
        did = []
        dp.register_task = lambda: did.append("register") or True
        dp.remove_task = lambda: did.append("remove") or True
        dp.find_exe = lambda: r"C:\fake\GoogleDriveFS.exe"
        dp.print = lambda *a, **k: None
        dp.main(["drive-pause.py", "task"])
        self.p["set_setting"]("drive_pause", False)
        self.assertEqual(dp.settings(), (False, 12 * 60))
        dp.main(["drive-pause.py", "task"])
        self.assertEqual(did, ["register", "remove"])

    def test_installer_never_writes_the_local_settings(self):
        for name in ("install.py", "uninstall.py"):
            with open(os.path.join(ROOT, name), encoding="utf-8") as f:
                src = f.read()
            for line in src.splitlines():
                if "local.json" in line:
                    self.assertNotRegex(line, r"open\(.*['\"]w['\"]|os\.remove|unlink", name + ": " + line)


if __name__ == "__main__":
    unittest.main(verbosity=1)
