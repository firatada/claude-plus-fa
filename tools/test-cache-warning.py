# Unit tests for Field Agents' cache warning (agent-panel.pyw): an open session left idle gets a mark on its card
# 15 and 5 minutes before its prompt cache expires, with one sound each, and a quieter mark once it has expired.
#   - cache_of(): the cache's lifetime and the context size read from made-up transcripts (1 h, 5 min, a GLM session,
#     a subagent's line, a newest reply that wrote nothing);
#   - cache_stage(): the thresholds at 45, 55 and 60 minutes, by fake clock; closed and working sessions never warn, a
#     session waiting for an answer does; a 5-minute cache is never warned;
#   - the reset: a prompt (a newer last activity, last_at()) clears the mark and arms the sounds again;
#   - the one-sound rule (cache_rings(), check_cache()): one sound per stage per idle stretch, one sound for several
#     sessions crossing together (the more urgent), none at expiry, none on the first pass after start, none with the
#     tray's Alert Sound off or the setting off;
#   - cache_view(): what the page gets.
# Run: python -I tools/test-cache-warning.py
# Times are fake (no waiting); the .pyw is never imported (its one-instance mutex would raise the running panel): the
# functions are taken out by ast. winsound is a fake, so nothing plays.
import ast, datetime, json, os, re, sys, tempfile, types, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
NAMES = ("local", "AT_LOOK", "CACHE_LEAD", "CACHE_LOOK", "TURN_LINE", "USAGE_LINE", "last_at", "cache_of",
         "cache_stage", "cache_view", "cache_rings", "cache_warning_on", "stretch_rec", "cache_base", "keepalive_fits",
         "keepalive_on", "KEEPALIVE_LEAD", "KEEPALIVE_MAX", "KEEPALIVE_MIN_TOKENS")
T0 = 1_800_000_000.0  # a made-up "now" for the idle stretch's start
MIN = 60


def panel(tmp):
    with open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    keep = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in NAMES or
            isinstance(n, ast.Assign) and any(getattr(t, "id", None) in NAMES for t in n.targets)]
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Panel")
    keep += [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "check_cache"]
    ns = {"os": os, "json": json, "re": re, "datetime": datetime, "time": __import__("time"),
          "LOCAL": os.path.join(tmp, "claude-plus.local.json"),
          "CACHE_SOUNDS": {1: os.path.join(ROOT, "mascot", "cache-soon.wav"),
                           2: os.path.join(ROOT, "mascot", "cache-last.wav")}}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "agent-panel.pyw", "exec"), ns)
    return ns


def stamp(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def reply(t, model="claude-opus-5-5", h1=0, m5=0, inp=3, read=0, write=None, side=False, mid="m"):
    write = h1 + m5 if write is None else write
    return {"type": "assistant", "timestamp": stamp(t), "isSidechain": side,
            "message": {"id": mid, "model": model, "usage": {
                "input_tokens": inp, "cache_read_input_tokens": read, "cache_creation_input_tokens": write,
                "cache_creation": {"ephemeral_1h_input_tokens": h1, "ephemeral_5m_input_tokens": m5}}}}


def prompt(t, text="go on"):
    return {"type": "user", "timestamp": stamp(t), "message": {"role": "user", "content": text}}


class Fake:
    """The panel as check_cache() sees it."""

    def __init__(self, sessions, sound=True):
        self.sessions, self.opts, self.cache_rung = sessions, {"sound": sound}, None
        self.kept, self.returns = {}, {}


class CacheWarning(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="claude-plus-test-cache-")
        self.p = panel(self.tmp)
        self.played = []
        fake = types.ModuleType("winsound")
        fake.SND_FILENAME, fake.SND_ASYNC, fake.SND_NODEFAULT = 0x20000, 1, 2
        fake.PlaySound = lambda f, flags: self.played.append(os.path.basename(f))
        self.saved = sys.modules.get("winsound")
        sys.modules["winsound"] = fake

    def tearDown(self):
        if self.saved:
            sys.modules["winsound"] = self.saved
        else:
            sys.modules.pop("winsound", None)

    def transcript(self, lines, name="s.jsonl"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            for o in lines:
                f.write(json.dumps(o, separators=(",", ":")) + "\n")  # compact, as Claude Code writes it
        return path

    def session(self, sid="a", idle_min=0.0, status="idle", waiting=None, ttl=3600, tokens=250000):
        return {"id": sid, "status": status, "waiting": waiting, "at": T0,
                "cache": {"ttl": ttl, "tokens": tokens}, "_now": T0 + idle_min * MIN}

    def stage(self, s):
        return self.p["cache_stage"](s, s["_now"])

    # ----- reading the transcript -----
    def test_cache_of_reads_the_1h_lifetime_and_the_context(self):
        path = self.transcript([prompt(T0 - 60), reply(T0 - 50, h1=5000, mid="1"),
                                reply(T0, h1=1200, read=240000, inp=7, mid="2")])
        self.assertEqual(self.p["cache_of"](path, os.path.getsize(path)), {"ttl": 3600, "tokens": 241207, "model": "claude-opus-5-5"})

    def test_cache_of_reads_a_5_minute_cache(self):
        path = self.transcript([reply(T0, m5=900, read=50000)])
        self.assertEqual(self.p["cache_of"](path, os.path.getsize(path))["ttl"], 300)

    def test_newest_reply_that_wrote_nothing_gives_tokens_and_an_older_one_the_lifetime(self):
        path = self.transcript([reply(T0 - 99, h1=800, mid="1"), reply(T0, read=300000, write=0, mid="2")])
        self.assertEqual(self.p["cache_of"](path, os.path.getsize(path)), {"ttl": 3600, "tokens": 300003, "model": "claude-opus-5-5"})

    def test_glm_sessions_and_subagent_lines_are_not_read(self):
        path = self.transcript([reply(T0, model="glm-4.7-flash", h1=900)])
        self.assertEqual(self.p["cache_of"](path, os.path.getsize(path)), {"ttl": None, "tokens": None, "model": None})
        path = self.transcript([reply(T0 - 9, h1=100, read=1000, mid="1"),
                                reply(T0, m5=7, read=9, side=True, mid="2")], "side.jsonl")
        self.assertEqual(self.p["cache_of"](path, os.path.getsize(path)), {"ttl": 3600, "tokens": 1103, "model": "claude-opus-5-5"})

    # ----- the thresholds -----
    def test_thresholds_at_45_55_and_60_minutes(self):
        for idle, want in ((0, 0), (44.99, 0), (45, 1), (54.99, 1), (55, 2), (59.99, 2), (60, 3), (300, 3)):
            self.assertEqual(self.stage(self.session(idle_min=idle)), want, idle)

    def test_closed_and_working_sessions_never_warn_a_waiting_one_does(self):
        self.assertEqual(self.stage(self.session(idle_min=50, status=None)), 0)
        self.assertEqual(self.stage(self.session(idle_min=50, status="busy")), 0)
        self.assertEqual(self.stage(self.session(idle_min=50, status="busy", waiting="permission")), 1)
        self.assertEqual(self.stage(self.session(idle_min=56, status="waiting")), 2)

    def test_a_5_minute_cache_or_an_unknown_one_is_never_warned(self):
        for idle in (0, 4, 5, 60):
            self.assertEqual(self.stage(self.session(idle_min=idle, ttl=300)), 0, idle)
            self.assertEqual(self.stage(self.session(idle_min=idle, ttl=None)), 0, idle)

    def test_cache_view_gives_the_stage_the_end_and_the_tokens(self):
        s = self.session(idle_min=46)
        self.assertEqual(self.p["cache_view"](s, s["_now"]),
                         {"stage": 1, "ends": int((T0 + 3600) * 1000), "tokens": 250000, "kept": None})
        self.assertIsNone(self.p["cache_view"](self.session(idle_min=10), T0 + 10 * MIN))

    # ----- the reset on a prompt -----
    def test_a_prompt_clears_the_mark_and_arms_the_sounds_again(self):
        path = self.transcript([prompt(T0 - 30), reply(T0, h1=900, read=100000)])
        s = {"id": "a", "status": "idle", "waiting": None,
             "at": self.p["last_at"](path, os.path.getsize(path)), "cache": self.p["cache_of"](path, os.path.getsize(path))}
        self.assertEqual(s["at"], T0)
        rung = {}
        loud, rung = self.p["cache_rings"](rung, [s], T0 + 46 * MIN, True)
        self.assertEqual((loud, self.p["cache_stage"](s, T0 + 46 * MIN)), (1, 1))
        # the maintainer types a prompt at minute 50: last_at() moves to it, the mark goes
        t1 = T0 + 50 * MIN
        path = self.transcript([prompt(T0 - 30), reply(T0, h1=900, read=100000), prompt(t1)])
        s["at"] = self.p["last_at"](path, os.path.getsize(path))
        self.assertEqual(s["at"], t1)
        self.assertEqual(self.p["cache_stage"](s, t1 + 1), 0)
        loud, rung = self.p["cache_rings"](rung, [s], t1 + 1, True)
        self.assertEqual((loud, rung), (0, {}))
        # and 45 minutes after that the first sound comes again
        loud, rung = self.p["cache_rings"](rung, [s], t1 + 45 * MIN, True)
        self.assertEqual(loud, 1)

    # ----- the one-sound rule -----
    def test_one_sound_per_stage_never_repeated(self):
        s, rung, heard = self.session(), {}, []
        for minute in range(40, 70):
            loud, rung = self.p["cache_rings"](rung, [s], T0 + minute * MIN, True)
            heard += [(minute, loud)] if loud else []
        self.assertEqual(heard, [(45, 1), (55, 2)])

    def test_sessions_crossing_together_make_one_sound_the_more_urgent(self):
        a, b, c = self.session("a"), self.session("b"), self.session("c")
        b["at"] = T0 - 10 * MIN  # 10 minutes further on: at T0 + 45 min it is at 55
        rung = {"c": (T0, 1)}     # c was already sounded at 45
        loud, rung = self.p["cache_rings"](rung, [a, b, c], T0 + 45 * MIN, True)
        self.assertEqual(loud, 2)
        self.assertEqual(rung, {"a": (T0, 1), "b": (T0 - 10 * MIN, 2), "c": (T0, 1)})
        loud, rung = self.p["cache_rings"]({}, [a, c], T0 + 46 * MIN, True)
        self.assertEqual(loud, 1, "two sessions at the first stage: one sound")

    def test_a_panel_started_late_sounds_the_stage_it_finds_not_both(self):
        loud, rung = self.p["cache_rings"]({}, [self.session()], T0 + 57 * MIN, True)
        self.assertEqual((loud, rung), (2, {"a": (T0, 2)}))
        loud, _ = self.p["cache_rings"](rung, [self.session()], T0 + 58 * MIN, True)
        self.assertEqual(loud, 0)

    def test_expiry_and_the_switch_off_are_silent(self):
        loud, rung = self.p["cache_rings"]({}, [self.session()], T0 + 61 * MIN, True)
        self.assertEqual((loud, rung), (0, {"a": (T0, 3)}))
        self.assertEqual(self.p["cache_rings"]({}, [self.session()], T0 + 46 * MIN, False), (0, {}))

    def test_check_cache_plays_once_and_never_on_the_first_pass(self):
        import time as real_time
        s = self.session()
        panel = Fake([s])
        clock = {"now": T0 + 50 * MIN}
        self.p["time"] = types.SimpleNamespace(time=lambda: clock["now"])
        self.p["check_cache"](panel)           # first pass after start: notes stage 1, silent
        self.assertEqual(self.played, [])
        clock["now"] = T0 + 55 * MIN
        self.p["check_cache"](panel)
        self.p["check_cache"](panel)
        clock["now"] = T0 + 61 * MIN
        self.p["check_cache"](panel)
        self.assertEqual(self.played, ["cache-last.wav"])
        # a prompt, then 45 idle minutes: the soft sound
        s["at"] = T0 + 62 * MIN
        clock["now"] = s["at"] + 45 * MIN
        self.p["check_cache"](panel)
        self.assertEqual(self.played, ["cache-last.wav", "cache-soon.wav"])
        self.p["time"] = real_time

    def test_check_cache_is_silent_with_alert_sound_off_or_the_setting_off(self):
        import time as real_time
        clock = {"now": T0}
        self.p["time"] = types.SimpleNamespace(time=lambda: clock["now"])
        quiet = Fake([self.session()], sound=False)
        self.p["check_cache"](quiet)
        clock["now"] = T0 + 46 * MIN
        self.p["check_cache"](quiet)
        self.assertEqual(self.played, [])
        with open(self.p["LOCAL"], "w", encoding="utf-8") as f:
            json.dump({"cache_warning": False}, f)
        off = Fake([self.session()])
        clock["now"] = T0
        self.p["check_cache"](off)
        clock["now"] = T0 + 46 * MIN
        self.p["check_cache"](off)
        self.assertEqual(self.played, [])
        self.p["time"] = real_time

    def test_the_sound_files_exist(self):
        for f in self.p["CACHE_SOUNDS"].values():
            self.assertTrue(os.path.getsize(f) > 10000, f)


if __name__ == "__main__":
    unittest.main(verbosity=1)
