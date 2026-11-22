# Unit tests for the Agent Panel's card state: a card whose background work runs sorts with the working cards,
# while its dot, tint and words keep the session's own state and its badge carries the work.
# Run: python -I tools/test-agent-panel-state.py   (needs node for the page's half)
# The .pyw is never imported (that would trip its one-instance mutex and raise the running panel): the functions
# under test are taken from it by ast and given a fake process table and fake creation times, so no process is
# started or read. The page's state functions are cut out of agent-panel.html and run in node.
import ast, json, os, subprocess, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")


def pyw_functions(*names, cls_methods=()):
    with open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    keep = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    for c in tree.body:
        if isinstance(c, ast.ClassDef):
            keep += [m for m in c.body if isinstance(m, ast.FunctionDef) and m.name in cls_methods]
    ns = {"os": os, "time": time, "json": json, "FT_EPOCH": 116444736000000000}
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
  badgeTip: bgTip(S({title: "b", status: "idle", bg: [run], bgSince: T0})),
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
        self.assertEqual(r["badgeTip"], "1 background run, 3:10\nrun · mindmap · 3 min")
        self.assertEqual(r["labelIdleChild"], "b, open, waiting for you, C:/b, 1 background run, 3:10")

    def test_order(self):
        self.assertEqual(self.r["order"], ["asking-with-child", "working", "old-delegating", "new-idle", "old-idle", "closed"])


class Mascot(unittest.TestCase):
    """The chibi hero: runs only while its session works, sleeps in every other state; each element gets the
    smallest shipped strip that still covers its device pixels; the page's frame counts match the strips."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "agent-panel.html"), encoding="utf-8") as f:
            html = f.read()
        code = html[html.index("const RUN = "):html.index("const frameAt = ")]
        test = r"""
const MASCOT = {"hero-run": "r1", "hero-run@2x": "r2", "hero-sleep": "s1", "hero-sleep@2x": "s2"};
const window = {devicePixelRatio: 1};
const pick = (w, dpr, key) => { window.devicePixelRatio = dpr; return stripOf({clientWidth: w}, key); };
console.log(JSON.stringify({
  heroes: ["busy", "ask", "idle", ""].map(st => heroOf(st)[0]),
  frames: {run: RUN[1], sleep: SLEEP[1]},
  sleepMs: SLEEP[2].map(s => s[1]), runMs: RUN[2].map(s => s[1]),
  picks: [pick(56, 1, "hero-run"), pick(56, 2, "hero-run"), pick(36, 1, "hero-sleep"), pick(36, 2, "hero-sleep"),
          pick(28, 1, "hero-run"), pick(28, 2, "hero-run"), pick(28, 1.5, "hero-sleep"), pick(56, 1.25, "hero-sleep")],
}));
"""
        out = subprocess.run(["node", "-e", code + test], capture_output=True, encoding="utf-8", check=True)
        cls.r = json.loads(out.stdout)

    def test_runs_only_while_working(self):
        self.assertEqual(self.r["heroes"], ["hero-run", "hero-sleep", "hero-sleep", "hero-sleep"])

    def test_timing(self):
        self.assertTrue(all(ms == 90 for ms in self.r["runMs"]))
        self.assertTrue(all(600 <= ms <= 900 for ms in self.r["sleepMs"]))

    def test_strip_size_for_the_screen(self):
        # card 56 px at 1x: the 56 px frames; at 2x or 1.25x the 112 px ones; header and tab at 1x the 56 px
        self.assertEqual(self.r["picks"], ["r1", "r2", "s1", "s2", "r1", "r1", "s1", "s2"])

    def test_shipped_strips_match_the_page(self):
        from PIL import Image
        for name, n in (("hero-run", self.r["frames"]["run"]), ("hero-sleep", self.r["frames"]["sleep"])):
            for suffix, px in (("", 56), ("@2x", 112)):
                with Image.open(os.path.join(ROOT, "mascot", name + suffix + ".webp")) as im:
                    self.assertEqual(im.size, (px * n, px), name + suffix)

    def test_panel_inlines_all_four(self):
        with open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8") as f:
            src = f.read()
        ns = pyw_functions("mascot_js")
        ns.update(MASCOT=os.path.join(ROOT, "mascot"), base64=__import__("base64"))
        keys = ("hero-run", "hero-run@2x", "hero-sleep", "hero-sleep@2x")
        self.assertIn("mascot_js((%s))" % ", ".join('"%s"' % k for k in keys), src)
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
