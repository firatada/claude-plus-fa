# Unit tests for the Agent Panel's card state: a card whose background work runs sorts with the working cards,
# while its dot, tint and words keep the session's own state and its badge carries the work.
# Run: python -I tools/test-agent-panel-state.py   (needs node for the page's half)
# The .pyw is never imported (that would trip its one-instance mutex and raise the running panel): the functions
# under test are taken from it by ast and given a fake process table and fake creation times, so no process is
# started or read. The page's state functions are cut out of agent-panel.html and run in node.
import ast, datetime, json, os, re, shutil, subprocess, tempfile, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")


def pyw_functions(*names, cls_methods=()):
    """The module-level functions and assignments named, and the class methods named, run into one namespace."""
    with open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    keep = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names or
            isinstance(n, ast.Assign) and any(getattr(t, "id", None) in names for t in n.targets)]
    for c in tree.body:
        if isinstance(c, ast.ClassDef):
            keep += [m for m in c.body if isinstance(m, ast.FunctionDef) and m.name in cls_methods]
    ns = {"os": os, "time": time, "json": json, "re": re, "datetime": datetime, "FT_EPOCH": 116444736000000000}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "agent-panel.pyw", "exec"), ns)
    return ns


FT = lambda unix: int(unix * 1e7) + 116444736000000000  # a unix time as a FILETIME


class ChildRunStart(unittest.TestCase):
    """A run's start is its own process's creation time, not that of the shells between it and the session."""

    def test_since_is_the_runs_own_start(self):
        ns = pyw_functions("child_runs")
        # session claude.exe 100 -> powershell 200 (an old shell) -> cmd 300 -> claude.exe 400 (the run)
        table = {100: (1, "claude.exe"), 200: (100, "powershell.exe"), 300: (200, "cmd.exe"), 400: (300, "claude.exe")}
        times = {1: FT(0), 100: FT(1000), 200: FT(1100), 300: FT(1500), 400: FT(1501)}
        ns["born"] = lambda pid: times.get(pid)
        ns["claude_code"] = lambda pid, name: name == "claude.exe"
        live = {"S": {"pid": 100, "cwd": "C:\\work\\mindmap"}, "R": {"pid": 400, "cwd": "C:\\work\\run"}}
        out = ns["child_runs"](live, table)
        self.assertEqual(list(out), ["S"])
        self.assertEqual(out["S"][0]["what"], "run")
        self.assertAlmostEqual(out["S"][0]["since"], 1501, places=3)

    def test_reused_pid_breaks_the_chain(self):
        ns = pyw_functions("child_runs")
        table = {100: (1, "claude.exe"), 400: (100, "claude.exe")}
        times = {1: FT(0), 100: FT(2000), 400: FT(1501)}  # the "parent" is younger: a reused number
        ns["born"] = lambda pid: times.get(pid)
        ns["claude_code"] = lambda pid, name: name == "claude.exe"
        out = ns["child_runs"]({"S": {"pid": 100}, "R": {"pid": 400}}, table)
        self.assertEqual(out, {})


class BgStart(unittest.TestCase):
    def setUp(self):
        ns = pyw_functions(cls_methods=("bg_start",))

        class P:
            bg_start = ns["bg_start"]
        self.p = P()
        self.p.bg_seen = {}

    def test_none_without_work_or_closed(self):
        self.assertIsNone(self.p.bg_start({"id": "a", "status": "idle", "bg": []}, {}))
        self.assertIsNone(self.p.bg_start({"id": "a", "status": None, "bg": [{"since": 5}]}, {}))

    def test_oldest_still_running(self):
        s = {"id": "a", "status": "idle", "bg": [{"kind": "run", "since": 1000.5}, {"kind": "subagent", "since": 900.25}]}
        self.assertEqual(self.p.bg_start(s, {}), 900250)

    def test_oldest_ends_then_the_next(self):
        s = {"id": "a", "status": "idle", "bg": [{"since": 1000}]}
        self.assertEqual(self.p.bg_start(s, {}), 1000000)

    def test_fallback_first_read_stands_in(self):
        seen = {}
        s = {"id": "a", "status": "busy", "bg": [{"kind": "run", "since": None}]}
        t1 = self.p.bg_start(s, seen)
        self.assertAlmostEqual(t1 / 1000, time.time(), delta=2)
        self.p.bg_seen = seen  # the next read keeps the first one's time
        time.sleep(0.02)
        self.assertEqual(self.p.bg_start(s, {}), t1)

    def test_fallback_never_later_than_a_known_start(self):
        self.p.bg_seen = {"a": 5_000_000}
        s = {"id": "a", "status": "idle", "bg": [{"since": None}, {"since": 4000}]}
        self.assertEqual(self.p.bg_start(s, {}), 4_000_000)

    def test_work_gone_forgets_first_read(self):
        self.p.bg_seen = {"a": 1}
        seen = {}
        self.assertIsNone(self.p.bg_start({"id": "a", "status": "idle", "bg": []}, seen))
        self.assertEqual(seen, {})


PAGE_TESTS = r"""
const T0 = 1_000_000_000_000, NOW = T0 + 190_000;
Date.now = () => NOW;
const S = (o) => Object.assign({id: o.title, here: true, bg: []}, o);
const run = {kind: "run", what: "mindmap", for: "3 min"};
const cases = {
  // the look: the session's own state only
  idleNoChild: stateOf(S({title: "a", status: "idle"})),
  idleChild: stateOf(S({title: "b", status: "idle", bg: [run], bgSince: T0})),
  busyChild: stateOf(S({title: "c", status: "busy", turn: T0 + 150_000, bg: [run], bgSince: T0})),
  askChild: stateOf(S({title: "d", status: "busy", waiting: true, bg: [run], bgSince: T0})),
  closed: stateOf(S({title: "e", status: null, bg: [run]})),
  // the sort group: background work puts an open session with the working ones
  gIdleNoChild: groupOf(S({title: "a", status: "idle"})),
  gIdleChild: groupOf(S({title: "b", status: "idle", bg: [run], bgSince: T0})),
  gBusyChild: groupOf(S({title: "c", status: "busy", turn: T0 + 150_000, bg: [run], bgSince: T0})),
  gAskChild: groupOf(S({title: "d", status: "busy", waiting: true, bg: [run], bgSince: T0})),
  gClosed: groupOf(S({title: "e", status: null, bg: [run]})),
  idleChildSince: runSince(S({title: "b", status: "idle", bg: [run], bgSince: T0})),
  busyChildSince: runSince(S({title: "c", status: "busy", turn: T0 + 150_000, bg: [run], bgSince: T0})),
  busyNoTurnSince: runSince(S({title: "c", status: "busy", bg: [run], bgSince: T0})),
  askSince: runSince(S({title: "d", status: "busy", waiting: true, bg: [run], bgSince: T0})),
  idleNoChildSince: runSince(S({title: "a", status: "idle"})),
  saidIdleChild: said(S({title: "b", status: "idle", bg: [run], bgSince: T0}), "idle"),
  saidBusyChild: said(S({title: "c", status: "busy", turn: T0 + 150_000, bg: [run], bgSince: T0}), "busy"),
  saidBusyNoTurn: said(S({title: "c", status: "busy", bg: [run], bgSince: T0}), "busy"),
  badgeOne: bgSaid(S({title: "b", status: "idle", bg: [run], bgSince: T0})),
  badgeTwo: bgSaid(S({title: "b", status: "busy", turn: T0 + 150_000, bg: [run, {kind: "subagent", what: "Explore"}], bgSince: T0})),
  badgeNoStart: bgSaid(S({title: "b", status: "idle", bg: [run]})),
  labelIdleChild: cardLabel(S({title: "b", status: "idle", path: "C:/b", age: "2h", bg: [run], bgSince: T0}), "idle"),
  order: byState([
    S({title: "new-idle", status: "idle"}),
    S({title: "closed", status: null}),
    S({title: "working", status: "busy", turn: T0}),
    S({title: "old-delegating", status: "idle", bg: [run], bgSince: T0}),
    S({title: "asking-with-child", status: "busy", waiting: true, bg: [run], bgSince: T0}),
    S({title: "old-idle", status: "idle"}),
  ]).map(s => s.title),
};
console.log(JSON.stringify(cases));
"""


class Page(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "agent-panel.html"), encoding="utf-8") as f:
            html = f.read()
        cut = lambda a, b: html[html.index(a):html.index(b)]
        code = (cut("// hidden by the user, its folder is gone", "// green under 50 %") +
                cut("const pct = ", "\n// the search hits") +
                cut('// "on GLM" / "on Codex"', "// Cards are kept and patched") +
                cut("// what a screen reader hears for a card", "// Each second the timers move on"))
        out = subprocess.run(["node", "-e", code + PAGE_TESTS], capture_output=True, encoding="utf-8", check=True)
        cls.r = json.loads(out.stdout)

    def test_look_is_the_sessions_own_state(self):
        r = self.r  # background work never turns the dot green or tints the card
        self.assertEqual((r["idleNoChild"], r["idleChild"], r["busyChild"], r["askChild"], r["closed"]),
                         ("idle", "idle", "busy", "ask", ""))

    def test_group_counts_background_work(self):
        r = self.r
        self.assertEqual((r["gIdleNoChild"], r["gIdleChild"], r["gBusyChild"], r["gAskChild"], r["gClosed"]),
                         ("idle", "busy", "busy", "ask", ""))

    def test_timer_source(self):
        r, T0 = self.r, 1_000_000_000_000
        self.assertEqual(r["idleChildSince"], T0)               # delegated only: the oldest background work
        self.assertEqual(r["busyChildSince"], T0 + 150_000)     # working itself: its own turn
        self.assertEqual(r["busyNoTurnSince"], T0)              # working, no turn stamp yet: the work
        self.assertIsNone(r["askSince"])                        # asking: no timer
        self.assertIsNone(r["idleNoChildSince"])

    def test_words(self):
        r = self.r  # the dot never says working for background work alone; the badge says it, with its time
        self.assertEqual(r["saidIdleChild"], "Open, waiting for you")
        self.assertEqual(r["saidBusyChild"], "Working, 0:40")
        self.assertEqual(r["saidBusyNoTurn"], "Working")
        self.assertEqual(r["badgeOne"], "1 background run, 3:10")
        self.assertEqual(r["badgeTwo"], "2 background runs, 3:10")
        self.assertEqual(r["badgeNoStart"], "1 background run")
        self.assertEqual(r["labelIdleChild"], "b, open, waiting for you, C:/b, 1 background run, 3:10")

    def test_order(self):
        self.assertEqual(self.r["order"], ["asking-with-child", "working", "old-delegating", "new-idle", "old-idle", "closed"])


class Mascot(unittest.TestCase):
    """The chibi hero: one figure per state, first match wins (asking waves, background work conducts, working runs,
    open stands, closed sleeps); the moving figures glance at the viewer in place of a rest step, then that step
    plays; each element gets the smallest shipped strip that still covers its device pixels; the page's frame
    counts match the strips."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "agent-panel.html"), encoding="utf-8") as f:
            html = f.read()
        code = html[html.index("const RUN = "):html.index("const frameAt = ")]
        code += html[html.index("function advance(s, now)"):html.index("function tick(now)")]
        test = r"""
const MASCOT = {"hero-run": "r1", "hero-run@2x": "r2", "hero-sleep": "s1", "hero-sleep@2x": "s2"};
const window = {devicePixelRatio: 1};
const delegated = s => !!s.status && !!(s.bg || []).length;
const pick = (w, dpr, key) => { window.devicePixelRatio = dpr; return stripOf({clientWidth: w}, key); };
const sessions = [{status: "busy", waiting: true, bg: [1]}, {status: "busy", bg: [1]}, {status: "idle", bg: [1]},
                  {status: "busy"}, {status: "idle"}, {status: "waiting", waiting: true}, {status: "", bg: [1]}, {}];
// play a figure for `ms` from a moment at which its glance is already due: the frames shown, in order
function play([key, n, seq, glance], ms) {
  const s = {seq, glance, i: 0, g: -1, look: 0}, shown = [];
  for (let t = 0; t < ms;) { const [f, d] = advance(s, t); shown.push(f); t += d; }
  return shown;
}
console.log(JSON.stringify({
  figures: sessions.map(s => FIGURE[figureOf(s)][0]),
  frames: Object.fromEntries([RUN, CONDUCT, ASK, IDLE, SLEEP].map(f => [f[0], f[1]])),
  used: Object.fromEntries([RUN, CONDUCT, ASK, IDLE, SLEEP].map(f =>
    [f[0], [...new Set([...f[2], ...(f[3] || [])].map(x => x[0]))].sort((a, b) => a - b)])),
  glanceMs: Object.fromEntries([RUN, CONDUCT, IDLE].map(f => [f[0], f[3].reduce((a, x) => a + x[1], 0)])),
  runMs: RUN[2].map(s => s[1]), sleepMs: SLEEP[2].map(s => s[1]),
  runPlay: play(RUN, 2000), idlePlay: play(IDLE, 3000), askPlay: play(ASK, 1000), every: GLANCE_EVERY,
  picks: [pick(56, 1, "hero-run"), pick(56, 2, "hero-run"), pick(36, 1, "hero-sleep"), pick(36, 2, "hero-sleep"),
          pick(28, 1, "hero-run"), pick(28, 2, "hero-run"), pick(28, 1.5, "hero-sleep"), pick(56, 1.25, "hero-sleep")],
}));
"""
        out = subprocess.run(["node", "-e", code + test], capture_output=True, encoding="utf-8", check=True)
        cls.r = json.loads(out.stdout)

    def test_figure_per_state(self):
        # asking beats background work, which beats working; closed sleeps even with background runs listed
        self.assertEqual(self.r["figures"], ["hero-ask", "hero-conduct", "hero-conduct", "hero-run", "hero-idle",
                                             "hero-ask", "hero-sleep", "hero-sleep"])

    def test_timing(self):
        self.assertTrue(all(ms == 90 for ms in self.r["runMs"]))
        self.assertTrue(all(600 <= ms <= 900 for ms in self.r["sleepMs"]))
        for name, ms in self.r["glanceMs"].items():
            self.assertTrue(700 <= ms <= 1300, (name, ms))  # about 1 s
        self.assertEqual(self.r["every"], [6000, 10000])

    def test_glance_stands_in_for_a_rest_step(self):
        # the run: one loop step, then (step 0 coming round) the four glance frames, then step 0 and on
        run = self.r["runPlay"]
        self.assertEqual(run[:14], [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 0, 1, 2])
        self.assertEqual(sum(f >= 8 for f in run), 4)  # once only, the next one 6 to 10 s later
        idle = self.r["idlePlay"]
        k = idle.index(4)
        self.assertEqual(idle[k:k + 3], [4, 5, 0])
        self.assertNotIn(4, self.r["askPlay"])  # asking already faces the viewer: no glance

    def test_strip_size_for_the_screen(self):
        # card 56 px at 1x: the 56 px frames; at 2x or 1.25x the 112 px ones; header and tab at 1x the 56 px
        self.assertEqual(self.r["picks"], ["r1", "r2", "s1", "s2", "r1", "r1", "s1", "s2"])

    def test_shipped_strips_match_the_page(self):
        from PIL import Image
        for name, n in self.r["frames"].items():
            self.assertEqual(self.r["used"][name], list(range(n)), name)  # every frame played, none past the strip
            for suffix, px in (("", 56), ("@2x", 112)):
                with Image.open(os.path.join(ROOT, "mascot", name + suffix + ".webp")) as im:
                    self.assertEqual(im.size, (px * n, px), name + suffix)

    def test_panel_inlines_every_figure(self):
        ns = pyw_functions("mascot_js")
        ns.update(MASCOT=os.path.join(ROOT, "mascot"), base64=__import__("base64"))
        with open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8") as f:
            src = f.read()
        heroes = re.search(r"^HEROES = (\(.*?\))", src, re.M).group(1)
        self.assertEqual(sorted(eval(heroes)), sorted(self.r["frames"]))
        keys = tuple(k + x for k in eval(heroes) for x in ("", "@2x"))
        js = ns["mascot_js"](keys)
        self.assertEqual(sorted(json.loads(js[len("window.MASCOT = "):-1])), sorted(keys))


class FolderForm(unittest.TestCase):
    """One form of a session's folder for the cards, the alert and the strip: Windows separators, no trailing one."""

    def where(self, cwd, distro=None, folder="f"):
        return pyw_functions("where_of")["where_of"]({"cwd": cwd, "distro": distro, "folder": folder})

    def test_forms(self):
        self.assertEqual(self.where("/home/dev/my-app/", "Ubuntu"), "\\\\wsl.localhost\\Ubuntu\\home\\dev\\my-app")
        self.assertEqual(self.where("/home/dev/my-app", "Ubuntu"), "\\\\wsl.localhost\\Ubuntu\\home\\dev\\my-app")
        self.assertEqual(self.where("/", "Ubuntu"), "\\\\wsl.localhost\\Ubuntu")
        self.assertEqual(self.where("\\\\wsl$\\Ubuntu\\home\\dev\\my-app\\"), "\\\\wsl$\\Ubuntu\\home\\dev\\my-app")
        self.assertEqual(self.where("//wsl.localhost/Ubuntu/home/dev/my-app/"), "\\\\wsl.localhost\\Ubuntu\\home\\dev\\my-app")
        self.assertEqual(self.where("C:\\work\\my-app\\"), "C:\\work\\my-app")
        self.assertEqual(self.where("C:\\work\\my-app"), "C:\\work\\my-app")
        self.assertEqual(self.where("C:\\"), "C:\\")
        self.assertEqual(self.where(None, folder="my-app"), "my-app")


class StripFrame(unittest.TestCase):
    """The screenshot strip's window is edged as the panel's: DWM's frame (pywebview shadow=True), square corners
    and the shadow along them, never a rounded window region nor an edge drawn by its own page."""

    def setUp(self):
        with open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8") as f:
            self.tree = ast.parse(f.read())
        with open(os.path.join(ROOT, "agent-shots.html"), encoding="utf-8") as f:
            self.page = f.read()

    def window(self, title):
        for n in ast.walk(self.tree):
            if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "create_window" and n.args \
                    and getattr(n.args[0], "id", "") == title:
                return {k.arg: k.value for k in n.keywords}
        self.fail("no create_window for " + title)

    def shadow(self, title):
        kw = self.window(title)
        return kw["shadow"].value if "shadow" in kw else True  # pywebview's default is True

    def test_same_frame_as_the_panel(self):
        self.assertTrue(self.shadow("TITLE"))
        self.assertEqual(self.shadow("SHOTS_TITLE"), self.shadow("TITLE"))
        self.assertIs(self.window("SHOTS_TITLE")["frameless"].value, True)

    def test_no_region_on_the_strip(self):
        for n in ast.walk(self.tree):
            if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "SetWindowRgn":
                self.assertNotEqual(getattr(n.args[0], "attr", ""), "strip_hwnd", "the strip must keep no region")

    def test_page_draws_no_outer_edge(self):
        rule = re.search(r"\n#strip \{([^}]*)\}", self.page).group(1)
        self.assertNotIn("border", rule)
        self.assertIn("padding: 6px", rule)  # SHOT_PAD: the cards keep their place


class HeadlessShown(unittest.TestCase):
    """A headless run (`claude -p`) that is not running leaves the panel once its transcript is older than
    HEADLESS_HOURS: out of the set-aside section, the strip's dropdown and the link's count. Hidden and
    folder-deleted sessions keep showing. Panel.state() is run with stubs, then the page's own `aside` counts."""
    NOW = 2_000_000_000

    def setUp(self):
        ns = pyw_functions("shown", "headless_hours", cls_methods=("state",))
        ns.update(HEADLESS_HOURS=24, local=lambda k: None, read_limits=lambda: {}, where_of=lambda s: s["cwd"],
                  folder_exists=lambda s: s["cwd"] != "C:/gone", ago=lambda t: "", lasted=lambda t: "",
                  user32=type("U", (), {"GetDoubleClickTime": staticmethod(lambda: 500)}))
        ns["time"] = type("T", (), {"time": staticmethod(lambda: self.NOW), "strftime": time.strftime,
                                    "localtime": time.localtime})
        self.ns = ns
        H = 3600
        mk = lambda sid, **kw: dict({"id": sid, "title": sid, "folder": "f", "cwd": "C:/w", "status": None,
                                     "distro": None, "headless": False, "at": self.NOW - 60}, **kw)
        self.sessions = [
            mk("plain"),                                                         # main list
            mk("old-plain", at=self.NOW - 30 * 24 * H),                       # main list, however old
            mk("run-old", headless=True, status="busy", pid=1, at=self.NOW - 50 * H),  # running: always shown
            mk("done-new", headless=True, at=self.NOW - 23 * H),              # finished under 24 h: shown
            mk("done-edge", headless=True, at=self.NOW - 24 * H),             # exactly 24 h: still shown
            mk("done-old", headless=True, at=self.NOW - 25 * H),              # finished over 24 h: gone
            mk("done-old-hidden", headless=True, at=self.NOW - 49 * H),       # headless rule wins over hidden
            mk("hidden-old", at=self.NOW - 90 * 24 * H),                      # hidden: unchanged
            mk("deleted-old", cwd="C:/gone", at=self.NOW - 90 * 24 * H),     # folder deleted: unchanged
        ]

        class P:
            state = ns["state"]
            hidden = {"hidden-old", "done-old-hidden"}
            returns, update_note, error, scanned, claude_status, strip_on = {}, None, None, True, None, None
            turn_start = lambda self, s, turns: None
            bg_start = lambda self, s, seen: None
            keeping = lambda self: False
        self.p = P()
        self.p.sessions = self.sessions

    def ids(self):
        return [s["id"] for s in self.p.state()["sessions"]]

    def test_shown(self):
        self.assertEqual(self.ids(), ["plain", "old-plain", "run-old", "done-new", "done-edge", "hidden-old",
                                      "deleted-old"])

    def test_nothing_dropped_from_the_panels_own_list(self):
        self.p.state()
        self.assertEqual(len(self.p.sessions), 9)  # child runs, alerts, reply tabs still see every session

    def test_override(self):
        self.ns["local"] = lambda k: 48 if k == "headless_hours" else None
        self.assertIn("done-old", self.ids())
        self.assertNotIn("done-old-hidden", self.ids())  # 49 h: still past 48
        self.ns["local"] = lambda k: 0
        self.assertEqual([i for i in self.ids() if i.startswith(("run", "done"))], ["run-old"])
        for bad in ("12", True, -1, None):
            self.ns["local"] = lambda k, bad=bad: bad
            self.assertEqual(self.ns["headless_hours"](), 24)

    def test_link_count_is_what_the_section_shows(self):
        with open(os.path.join(ROOT, "agent-panel.html"), encoding="utf-8") as f:
            html = f.read()
        aside = re.search(r"const aside = [^\n]*", html).group(0)
        pushed = json.dumps(self.p.state()["sessions"])
        js = aside + "\nconst all = " + pushed + ";\nconsole.log(JSON.stringify(all.filter(aside).map(s => s.id)));"
        out = subprocess.run(["node", "-e", js], capture_output=True, encoding="utf-8", check=True)
        self.assertEqual(json.loads(out.stdout), ["run-old", "done-new", "done-edge", "hidden-old", "deleted-old"])


class LastActivity(unittest.TestCase):
    """A card's age and its place in the list come from the session's last real activity (a prompt, a reply, a
    tool's result), never from the transcript file's time: Claude Code appends lines with no turn to an open
    session's transcript (the metadata block, bridge-session, data-flow-records, away_summary), which put idle
    sessions at the top with a fresh age (project A: no prompt since the day before, shown "5m" second from the top).
    Made-up transcripts in a temp folder; scan() and update_live() run on them with stubs, nothing real is read."""
    NOW = time.time()
    DAY = 86400

    def setUp(self):
        self.ns = pyw_functions("scan", "parse", "text_of", "last_at", "TURN_LINE", "AT_LOOK", "SESSION_FILE",
                                cls_methods=("update_live",))
        self.tmp = tempfile.mkdtemp(prefix="cpfa-at-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ns.update(CLAUDE=self.tmp, wsl_roots=lambda: [], running=lambda: {}, read_ctx=lambda sid: None,
                       home_of=lambda folder, cwds: cwds[0] if cwds else None)
        self.dir = os.path.join(self.tmp, "projects", "C--work")
        os.makedirs(self.dir)

    @staticmethod
    def stamp(t):
        return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + "123Z"

    def turn(self, kind, t, text="hi"):
        return {"type": kind, "timestamp": self.stamp(t), "cwd": "C:\\work", "sessionId": "x",
                "message": {"role": kind, "content": text}}

    def write(self, n, lines, mtime=None):
        path = os.path.join(self.dir, "%08d-0000-0000-0000-000000000000.jsonl" % n)
        with open(path, "w", encoding="utf-8") as f:
            for o in lines:
                f.write(json.dumps(o, separators=(",", ":")) + "\n")
        if mtime:
            os.utime(path, (mtime, mtime))
        return path

    @staticmethod
    def meta():
        """The lines Claude Code appends to an idle open session's transcript, none with a timestamp."""
        return [{"type": t, "sessionId": "x"} for t in ("last-prompt", "custom-title", "agent-name", "mode",
                                                        "permission-mode", "session-owner", "atis-latch")] + [
            {"type": "bridge-session", "sessionId": "x", "lastSequenceNum": 6217},
            {"type": "data-flow-records", "v": 1, "sessionId": "x", "records": []}]

    def test_appended_lines_do_not_move_an_idle_session_up(self):
        old = self.NOW - self.DAY - 3600  # yesterday's last reply
        idle = self.write(1, [self.turn("user", old - 60), self.turn("assistant", old),
                              {"type": "system", "subtype": "away_summary", "timestamp": self.stamp(old + 180)}]
                          + self.meta(), mtime=self.NOW - 300)  # the file written 5 minutes ago
        recent = self.write(2, [self.turn("user", self.NOW - 1200), self.turn("assistant", self.NOW - 1100)],
                            mtime=self.NOW - 1100)
        out = self.ns["scan"]({})
        self.assertEqual([s["path"] for s in out], [recent, idle])
        self.assertAlmostEqual(out[1]["at"], int(old) + 0.123, places=2)
        self.assertAlmostEqual(out[0]["at"], int(self.NOW - 1100) + 0.123, places=2)

    def test_tool_result_counts_as_activity(self):
        t = self.NOW - 500
        path = self.write(3, [self.turn("user", t - 100), self.turn("assistant", t - 50),
                              {"type": "user", "timestamp": self.stamp(t), "toolUseResult": {"type": "text"},
                               "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}}]
                          + self.meta(), mtime=self.NOW)
        self.assertAlmostEqual(self.ns["last_at"](path, os.path.getsize(path)), int(t) + 0.123, places=2)

    def test_found_behind_a_long_tail_and_across_blocks(self):
        t = self.NOW - 7 * self.DAY
        pad = {"type": "bridge-session", "sessionId": "x", "pad": "p" * 300000}  # longer than one read block
        path = self.write(4, [self.turn("assistant", t, "z" * 200000)] + [pad] * 5, mtime=self.NOW)
        size = os.path.getsize(path)
        self.assertGreater(size, 1048576)  # past parse()'s 1 MB tail
        self.assertAlmostEqual(self.ns["last_at"](path, size), int(t) + 0.123, places=2)
        with open(path, "rb") as f:
            f.seek(size - 1048576)
            tail = f.read()
        self.assertAlmostEqual(self.ns["last_at"](path, size, tail), int(t) + 0.123, places=2)

    def test_nested_type_is_not_a_turn(self):
        t = self.NOW - 3 * self.DAY
        later = {"type": "attachment", "timestamp": self.stamp(self.NOW - 10),
                 "attachment": {"type": "user", "x": {"type": "assistant"}}}
        path = self.write(5, [self.turn("user", t), later], mtime=self.NOW)
        self.assertAlmostEqual(self.ns["last_at"](path, os.path.getsize(path)), int(t) + 0.123, places=2)

    def test_no_turn_falls_back_to_the_files_time(self):
        path = self.write(6, self.meta(), mtime=self.NOW - 4000)
        self.assertIsNone(self.ns["last_at"](path, os.path.getsize(path)))
        self.assertAlmostEqual(self.ns["scan"]({})[0]["at"], self.NOW - 4000, places=2)

    def test_live_pass_keeps_an_open_idle_session_in_place(self):
        """update_live() reads an open session again every LIVE_EVERY: appended lines change the file, not `at`."""
        old = self.NOW - self.DAY
        idle = self.write(7, [self.turn("assistant", old)], mtime=old)
        busy = self.write(8, [self.turn("assistant", self.NOW - 600)], mtime=self.NOW - 600)
        sessions = self.ns["scan"]({})
        self.assertEqual([s["path"] for s in sessions], [busy, idle])
        with open(idle, "a", encoding="utf-8") as f:  # Claude Code's bookkeeping lands in the idle one
            for o in self.meta():
                f.write(json.dumps(o) + "\n")
        os.utime(idle, (self.NOW, self.NOW))
        ids = {s["path"]: s["id"] for s in sessions}
        self.ns["running"] = lambda: {ids[idle]: {"status": "idle", "pid": 1}, ids[busy]: {"status": "idle", "pid": 2}}
        noop = lambda *a: None

        class P:
            update_live = self.ns["update_live"]
            find_windows = background = feed_shots = push = check_alerts = watch_return = keep_voice = noop
        p = P()
        p.sessions, p.launched, p.last_scan = sessions, {}, 1
        p.update_live()
        self.assertEqual([s["path"] for s in p.sessions], [busy, idle])
        self.assertAlmostEqual(p.sessions[1]["at"], int(old) + 0.123, places=2)
        with open(idle, "a", encoding="utf-8") as f:  # a real prompt in it now (compact, as Claude Code writes): it rises
            f.write(json.dumps(self.turn("user", self.NOW - 5), separators=(",", ":")) + "\n")
        p.update_live()
        self.assertEqual([s["path"] for s in p.sessions], [idle, busy])


if __name__ == "__main__":
    unittest.main(verbosity=2)
