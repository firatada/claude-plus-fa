# Unit tests for agent-reply.py, the reply window's rules: which transcript lines are a reply (end_turn text of the
# main thread; not tool_use narration, thinking, a subagent, <synthetic> or an API error), every reply since the
# last typed prompt, two text blocks of one message joined, reading on from where the last read stopped; the
# drafts kept per session; the pinned replies; the line Send to Session types and the text Copy puts on the
# clipboard; which session's terminal came to the front (front_session, taken from agent-panel.pyw by ast).
# Run: python -I tools/test-reply.py
# Every transcript here is made up, in a temporary folder; the drafts file too. The prompt rule is the panel's own
# typed_prompt(), taken out of agent-panel.pyw by ast (importing it would trip its one-instance mutex).
import ast, importlib.util, json, os, re, shutil, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
spec = importlib.util.spec_from_file_location("agent_reply", os.path.join(ROOT, "agent-reply.py"))
ar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ar)


def pyw(*names):
    with open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    keep = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    keep += [n for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "PEEK_CHARS" for t in n.targets)]
    ns = {"re": re}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "agent-panel.pyw", "exec"), ns)
    return ns


typed = pyw("typed_prompt", "text_of", "flat")["typed_prompt"]
n = [0]


def ts(sec):
    return "2026-11-23T10:%02d:%02d.000Z" % (sec // 60, sec % 60)


def user(text, sec=0):
    n[0] += 1
    return {"type": "user", "uuid": "u%d" % n[0], "timestamp": ts(sec), "message": {"role": "user", "content": text}}


def tool_result(sec=0):
    n[0] += 1
    return {"type": "user", "uuid": "u%d" % n[0], "timestamp": ts(sec), "toolUseResult": {"stdout": "ok"},
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}]}}


def part(mid, block, stop, sec=0, **extra):
    """One line of an assistant message: Claude Code writes one per content block."""
    n[0] += 1
    o = {"type": "assistant", "uuid": "a%d" % n[0], "timestamp": ts(sec),
         "message": {"id": mid, "model": extra.pop("model", "claude-opus-5-5"), "role": "assistant",
                     "stop_reason": stop, "content": [block]}}
    o.update(extra)
    return o


def text(t):
    return {"type": "text", "text": t}


THINK = {"type": "thinking", "thinking": "hmm"}
TOOL = {"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": "ls"}}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "s.jsonl")
        open(self.path, "w").close()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def add(self, *objs, raw=b""):
        with open(self.path, "ab") as f:
            for o in objs:
                f.write(json.dumps(o).encode("utf-8") + b"\n")
            f.write(raw)

    def replies(self, t=None):
        t = t or ar.Transcript(self.path, typed)
        t.read()
        return [r["text"] for r in t.replies()]


class Replies(Base):
    def test_end_turn_text_is_the_reply(self):
        self.add(user("fix the build", 1), part("m1", THINK, "end_turn", 2), part("m1", text("Done. It builds."), "end_turn", 3))
        self.assertEqual(self.replies(), ["Done. It builds."])

    def test_tool_use_narration_and_tool_results_skipped(self):
        self.add(user("fix it", 1),
                 part("m1", THINK, "tool_use", 2), part("m1", text("Let me look at the file."), "tool_use", 2),
                 part("m1", TOOL, "tool_use", 2), tool_result(3),
                 part("m2", text("Fixed in two places."), "end_turn", 4))
        self.assertEqual(self.replies(), ["Fixed in two places."])

    def test_synthetic_api_error_and_sidechain_skipped(self):
        self.add(user("go", 1),
                 part("m1", text("No response requested."), "end_turn", 2, model="<synthetic>"),
                 part("m2", text("API Error: 529 overloaded"), "end_turn", 3, isApiErrorMessage=True),
                 part("m3", text("subagent's summary"), "end_turn", 4, isSidechain=True),
                 part("m4", text("The real answer."), "end_turn", 5))
        self.assertEqual(self.replies(), ["The real answer."])

    def test_two_replies_since_the_prompt(self):
        # a background task's notification is not a typed prompt: its turn's reply joins the list
        self.add(user("start the tests in the background", 1), part("m1", text("Started; I will report."), "end_turn", 2),
                 user("<task-notification>\n<task-id>b1</task-id> done</task-notification>", 30),
                 part("m2", text("All 54 tests pass."), "end_turn", 31))
        t = ar.Transcript(self.path, typed)
        self.assertEqual(self.replies(t), ["Started; I will report.", "All 54 tests pass."])
        self.assertEqual(t.prompt["text"], "start the tests in the background")

    def test_two_text_blocks_of_one_message_joined(self):
        self.add(user("explain", 1), part("m1", text("First part."), "end_turn", 2), part("m1", THINK, "end_turn", 2),
                 part("m1", text("Second part."), "end_turn", 2))
        self.assertEqual(self.replies(), ["First part.\n\nSecond part."])

    def test_a_new_prompt_starts_afresh(self):
        self.add(user("one", 1), part("m1", text("Reply one."), "end_turn", 2), user("two", 3),
                 part("m2", text("Reply two."), "end_turn", 4))
        self.assertEqual(self.replies(), ["Reply two."])

    def test_no_reply_yet(self):
        self.add(user("one", 1), part("m1", text("Reply one."), "end_turn", 2), user("two", 3),
                 part("m2", text("Looking."), "tool_use", 4))
        t = ar.Transcript(self.path, typed)
        self.assertEqual(self.replies(t), [])
        self.assertEqual(t.prompt["text"], "two")

    def test_reads_on_from_where_it_stopped(self):
        t = ar.Transcript(self.path, typed)
        self.add(user("one", 1), part("m1", text("Reply one."), "end_turn", 2))
        self.assertEqual(self.replies(t), ["Reply one."])
        self.assertFalse(t.read())  # nothing new
        line = json.dumps(part("m2", text("Reply two."), "end_turn", 3)).encode("utf-8")
        self.add(raw=line[:20])  # a line still being written waits
        self.assertEqual(self.replies(t), ["Reply one."])
        self.add(raw=line[20:] + b"\n")
        self.assertEqual(self.replies(t), ["Reply one.", "Reply two."])
        self.add(user("three", 5))
        self.assertEqual(self.replies(t), [])

    def test_a_line_seen_twice_counts_once(self):
        o = part("m1", text("Once."), "end_turn", 2)
        self.add(user("go", 1), o, o)
        self.assertEqual(self.replies(), ["Once."])

    def test_first_read_starts_inside_a_line(self):
        old = ar.TAIL
        try:
            self.add(user("x" * 3000, 1), part("m0", text("old"), "end_turn", 2), user("now", 3),
                     part("m1", text("new reply"), "end_turn", 4))
            ar.TAIL = 600  # the tail starts inside a line; the prompt is in it
            t = ar.Transcript(self.path, typed)
            self.assertEqual(self.replies(t), ["new reply"])
            ar.TAIL = 100  # no prompt in the tail: read further back
            t = ar.Transcript(self.path, typed)
            self.assertEqual(self.replies(t), ["new reply"])
            self.assertEqual(t.prompt["text"], "now")
        finally:
            ar.TAIL = old

    def test_a_shorter_file_is_read_again(self):
        t = ar.Transcript(self.path, typed)
        self.add(user("one", 1), part("m1", text("Reply one."), "end_turn", 2))
        self.replies(t)
        with open(self.path, "w") as f:
            f.write(json.dumps(user("b", 1)) + "\n" + json.dumps(part("m9", text("B."), "end_turn", 2)) + "\n")
        self.assertEqual(self.replies(t), ["B."])


class Drafts(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.file = os.path.join(self.dir, "sub", "reply-drafts.json")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_kept_across_restarts_until_cleared(self):
        d = ar.Drafts(self.file)
        self.assertTrue(d.put("A", "m1#0", "Ship it today?", "  yes, behind the flag  ", now=100))
        self.assertFalse(d.put("A", "m1#0", "Ship it today?", "yes, behind the flag", now=101))  # unchanged
        d.put("B", "m7#2", "Other?", "no", now=100)
        again = ar.Drafts(self.file)
        self.assertEqual(again.get("A"), {"m1#0": {"text": "yes, behind the flag", "passage": "Ship it today?", "at": 100}})
        self.assertTrue(again.clear("A"))
        self.assertEqual(ar.Drafts(self.file).get("A"), {})
        self.assertEqual(list(ar.Drafts(self.file).get("B")), ["m7#2"])  # another session's draft stays

    def test_emptied_answer_goes(self):
        d = ar.Drafts(self.file)
        d.put("A", "k", "p", "x", now=1)
        self.assertTrue(d.put("A", "k", "p", "   ", now=2))
        self.assertEqual(ar.Drafts(self.file).data, {})
        self.assertFalse(d.put("A", "k", "p", ""))

    def test_a_newer_prompt_expires_older_answers(self):
        d = ar.Drafts(self.file)
        d.put("A", "old", "p", "x", now=100)
        d.put("A", "new", "p", "y", now=300)
        self.assertTrue(d.expire("A", 200))
        self.assertEqual(list(d.get("A")), ["new"])
        self.assertFalse(d.expire("A", 200))

    def test_a_broken_file_starts_empty(self):
        os.makedirs(os.path.dirname(self.file))
        with open(self.file, "w") as f:
            f.write("{not json")
        self.assertEqual(ar.Drafts(self.file).data, {})


class Line(unittest.TestCase):
    def test_send_line(self):
        self.assertEqual(ar.send_line([(1, "Monthly price,\n  billed yearly."), (4, "Wait for Thursday.")]),
                         ":1 Monthly price, billed yearly. :4 Wait for Thursday.")

    def test_empty_answers_left_out(self):
        self.assertEqual(ar.send_line([(1, "  "), (2, "ok")]), ":2 ok")

    def test_a_typed_marker_inside_an_answer_goes_in_code(self):
        line = ar.send_line([(1, "as in :3 before"), (2, "ok :x fine 10:30")])
        self.assertEqual(line, ":1 as in `:3` before :2 ok :x fine 10:30")
        spec = importlib.util.spec_from_file_location("qr", os.path.join(ROOT, "quote-reply.py"))
        qr = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(qr)
        self.assertEqual(qr.parse(line, {1, 2, 3})[0], [(1, "as in `:3` before"), (2, "ok :x fine 10:30")])

    def test_copy_text(self):
        self.assertEqual(ar.copy_text([("1. Should the yearly plan\n   show monthly?", "Monthly."), ("Skip", "  "),
                                       ("Ship it today?", "Yes,\nbehind the flag.")]),
                         ":1 Monthly. :2 Yes, behind the flag.\n\n:1\n> 1. Should the yearly plan\n>    show monthly?\n\n"
                         ":2\n> Ship it today?\n")
        self.assertEqual(ar.copy_text([("p", " ")]), "")


def layout():
    """band_layout() and the constants it reads, taken out of agent-panel.pyw by ast."""
    with open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    names = {"SHOT_W", "SHOT_H", "SHOT_PAD", "SHOT_GAP", "REPLY_W", "REPLY_MIN"}
    keep = [n for n in tree.body if isinstance(n, ast.Assign) and any(
        getattr(t, "id", "") in names or any(getattr(e, "id", "") in names for e in getattr(t, "elts", [])) for t in n.targets)]
    keep += [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "band_layout"]
    ns = {}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "agent-panel.pyw", "exec"), ns)
    return ns


class Layout(unittest.TestCase):
    """The reply window at the screen's left edge and the strip of screenshots right of it, SHOT_GAP apart."""
    ns = layout()

    def check(self, room, n, head):
        cols, rows, sw, rw = self.ns["band_layout"](room, n, head)
        if head and n and rw:
            self.assertLessEqual(rw + self.ns["SHOT_GAP"] + sw, room)  # never overlapping, the gap kept
        return cols, rows, sw, rw

    def test_screenshots_wrap_before_the_reply_window_narrows(self):
        self.assertEqual(self.check(1600, 6, True), (4, 2, 630, 820))
        self.assertEqual(self.check(1000, 3, True), (1, 3, 162, 820))

    def test_no_screenshot_leaves_the_reply_window_alone(self):
        self.assertEqual(self.check(1600, 0, True), (0, 0, 0, 820))

    def test_no_session_is_the_strip_alone(self):
        self.assertEqual(self.check(1600, 3, False), (3, 1, 474, 0))

    def test_a_narrow_screen_narrows_the_reply_window(self):
        self.assertEqual(self.check(600, 2, True), (1, 2, 162, 430))


# The reply page's markdown (blocks(), cells(), inline(), blockHtml()), run in node when it is installed: the part of
# agent-reply.html between its markdown and drawing headings, with esc and the chevron it uses; that part also holds
# clickAction() and paraTip(), the answer box's click rule and the paragraph's hint.
JS = r"""
const out = {};
const tbl = blocks("Spend:\n\n| Job | Calls | Tokens |\n|---|--:|:-:|\n| News **scoring** | 63 | 1,55 m |\n| Trend `cut` | 23 | 485 k |\n\nAfter.");
out.kinds = tbl.map(b => b.kind);
out.table = tbl[1];
out.tableHtml = blockHtml(tbl[1], "k");
out.noSep = blocks("| a | b |\n| c | d |").map(b => b.kind);
out.loose = blocks("a | b\n--|--\n1 | 2").map(b => [b.kind, b.head, b.rows]);
out.pipe = cells("| x \\| y | z |");
out.inline = inline("**bold** `code` ~~gone~~ *it* [docs](https://ex.com/a?b=1&c=2) see https://ex.com/p. \"https://q.com\"");
out.lists = blocks("1. one\n2. two\n   more\n- dot\n  - nested").map(b => [b.kind, b.marker, b.indent, b.text]);
out.quote = blocks("> a\n> b")[0];
out.heads = blocks("# H1\n## H2\n### H3").map(b => blockHtml(b, "k"));
const C = (o) => clickAction(Object.assign({own: false, moved: false, selected: false, detail: 1, box: false, text: false}, o));
out.click = {open: C({}), close: C({box: true}), keep: C({box: true, text: true}), drag: C({moved: true}),
  dragShut: C({moved: true, box: true}), selected: C({selected: true}), dbl: C({detail: 2}), own: C({own: true}),
  ownOpen: C({own: true, box: true})};
out.tips = [paraTip("p", false), paraTip("li", false), paraTip("table", false), paraTip("table", true)];
const run = (evs) => { let st = null; const acts = []; for (const e of evs) { const r = spaceRule(st, e); st = r.st; acts.push(r.act); } return [st, acts]; };
const D = {type: "down", repeat: false}, R = {type: "down", repeat: true}, U = {type: "up"}, T = {type: "timer"},
  K = {type: "key"}, B = {type: "blur"};
out.space = {hold: HOLD, tap: run([D, R, U]), hold2: run([D, R, T, R, R, U]), roll: run([D, K, R, U]),
  lateTimer: run([D, U, T]), blurWait: run([D, B]), blurOn: run([D, T, B]), noteUp: (() => { let r = spaceRule("note", U); return [r.st, r.act]; })(),
  onKey: (() => { const r = spaceRule("on", K); return [r.st, r.act]; })(), upEarly: run([D, T, U])};
console.log(JSON.stringify(out));
"""


def node_markdown():
    node = shutil.which("node")
    if not node:
        return None
    with open(os.path.join(ROOT, "agent-reply.html"), encoding="utf-8") as f:
        page = f.read()
    keep = [l for l in page.splitlines() if l.startswith(("const esc =", "const CHEV ="))]
    a = page.index("// ---------- Claude's markdown")
    b = page.index("// ---------- drawing ----------")
    import subprocess
    r = subprocess.run([node, "-e", "\n".join(keep) + "\n" + page[a:b] + JS], capture_output=True, text=True,
                       encoding="utf-8", timeout=30)
    if r.returncode:
        raise AssertionError(r.stderr)
    return json.loads(r.stdout)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class Markdown(unittest.TestCase):
    md = None

    @classmethod
    def setUpClass(cls):
        cls.md = node_markdown()

    def test_a_table_is_one_block_with_its_source(self):
        self.assertEqual(self.md["kinds"], ["p", "table", "p"])
        t = self.md["table"]
        self.assertEqual(t["head"], ["Job", "Calls", "Tokens"])
        self.assertEqual(t["align"], ["", "right", "center"])
        self.assertEqual(t["rows"], [["News **scoring**", "63", "1,55 m"], ["Trend `cut`", "23", "485 k"]])
        self.assertTrue(t["src"].startswith("| Job |") and t["src"].endswith("| 485 k |"))

    def test_a_table_is_drawn_as_a_table(self):
        h = self.md["tableHtml"]
        self.assertIn("<table><thead><tr><th>Job</th>", h)
        self.assertIn('<td style="text-align:right">63</td>', h)
        self.assertIn("<td>News <b>scoring</b></td>", h)
        self.assertIn("<td>Trend <code>cut</code></td>", h)
        self.assertNotIn("|", h)

    def test_pipes_without_a_dash_row_stay_as_written(self):
        self.assertEqual(self.md["noSep"], ["pre"])

    def test_a_table_without_outer_pipes(self):
        self.assertEqual(self.md["loose"], [["table", ["a", "b"], [["1", "2"]]]])

    def test_an_escaped_pipe_stays_in_its_cell(self):
        self.assertEqual(self.md["pipe"], ["x | y", "z"])

    def test_inline(self):
        h = self.md["inline"]
        for part in ("<b>bold</b>", "<code>code</code>", "<s>gone</s>", "<i>it</i>",
                     '<a class="lnk" data-url="https://ex.com/a?b=1&amp;c=2" data-tip="https://ex.com/a?b=1&amp;c=2">docs</a>',
                     '<a class="lnk" data-url="https://ex.com/p" data-tip="https://ex.com/p">https://ex.com/p</a>.',
                     '&quot;<a class="lnk" data-url="https://q.com" data-tip="https://q.com">https://q.com</a>&quot;'):
            self.assertIn(part, h)

    def test_lists_quotes_headings(self):
        self.assertEqual(self.md["lists"], [["li", "1.", 0, "one"], ["li", "2.", 0, "two more"], ["li", "•", 0, "dot"],
                                            ["li", "•", 2, "nested"]])
        self.assertEqual(self.md["quote"]["text"], "a\nb")
        tip = 'data-tip="Click to answer this paragraph"'
        self.assertEqual(self.md["heads"], [f'<div class="para h h1" data-key="k" {tip}>H1</div>',
                                            f'<div class="para h h2" data-key="k" {tip}>H2</div>',
                                            f'<div class="para h" data-key="k" {tip}>H3</div>'])

    def test_a_click_opens_and_a_second_closes_only_an_empty_box(self):
        c = self.md["click"]
        self.assertEqual((c["open"], c["close"], c["keep"]), ("open", "close", "focus"))

    def test_drag_double_click_and_own_targets_never_toggle(self):
        c = self.md["click"]
        for k in ("drag", "dragShut", "selected", "dbl", "own", "ownOpen"):
            self.assertEqual(c[k], "", k)

    def test_the_hint_says_what_a_click_does(self):
        self.assertEqual(self.md["tips"], ["Click to answer this paragraph", "Click to answer this item",
                                           "Click to answer this table", "Click to close the answer box"])


    def test_space_tap_types_one_space_and_a_hold_starts_and_stops_voice_typing(self):
        sp = self.md["space"]
        self.assertEqual(sp["hold"], 400)
        self.assertEqual(sp["tap"], [None, ["timer", "", "space"]])  # key repeat never types; the space at key up
        self.assertEqual(sp["hold2"], [None, ["timer", "", "start", "", "", "stop"]])  # no space from the repeats
        self.assertEqual(sp["upEarly"], [None, ["timer", "start", "stop"]])  # let go while the panel was asked

    def test_space_rolled_over_by_the_next_key_goes_in_first(self):
        self.assertEqual(self.md["space"]["roll"], [None, ["timer", "space", "", ""]])

    def test_space_timer_after_release_and_leaving_the_box(self):
        sp = self.md["space"]
        self.assertEqual(sp["lateTimer"], [None, ["timer", "space", ""]])
        self.assertEqual(sp["blurWait"], [None, ["timer", ""]])
        self.assertEqual(sp["blurOn"], [None, ["timer", "start", "stop"]])
        self.assertEqual(sp["noteUp"], [None, ""])  # could not start: nothing to stop, nothing typed
        self.assertEqual(sp["onKey"], ["on", ""])  # keys typed while listening go in as typed


class Voice(unittest.TestCase):
    """voice_block() on made-up registry values; nothing here sends a key."""

    def block(self, speech=1, policy=None, mic_hklm="Allow", mic_hkcu="Allow", mics=2):
        import winreg
        vals = {(winreg.HKEY_CURRENT_USER, ar.SPEECH_KEY, "HasAccepted"): speech,
                (winreg.HKEY_LOCAL_MACHINE, ar.SPEECH_POLICY, "AllowInputPersonalization"): policy,
                (winreg.HKEY_LOCAL_MACHINE, ar.MIC_KEY, "Value"): mic_hklm,
                (winreg.HKEY_CURRENT_USER, ar.MIC_KEY, "Value"): mic_hkcu}
        return ar.voice_block(read=lambda root, path, name: vals.get((root, path, name)), mics=mics)

    def test_ready(self):
        self.assertIsNone(self.block())

    def test_each_reason(self):
        self.assertEqual(self.block(speech=None), ar.VOICE_OFF)  # never turned on (this PC on 2026-11-23)
        self.assertEqual(self.block(speech=0), ar.VOICE_OFF)
        self.assertEqual(self.block(policy=0), ar.VOICE_POLICY)
        self.assertEqual(self.block(mic_hklm="Deny"), ar.VOICE_MIC_DENIED)
        self.assertEqual(self.block(mic_hkcu="Deny"), ar.VOICE_MIC_DENIED)
        self.assertEqual(self.block(mics=0), ar.VOICE_NO_MIC)


class Pins(unittest.TestCase):
    """The pinned replies: per session, in pin order, PIN_MAX at most, kept with their own text."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.file = os.path.join(self.dir, "sub", "reply-pins.json")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_add_in_pin_order_and_already(self):
        p = ar.Pins(self.file)
        self.assertEqual(p.add("A", "m1", 10, "First reply.", now=100), "added")
        self.assertEqual(p.add("A", "m2", 20, "Second reply.", now=200), "added")
        self.assertEqual(p.add("A", "m1", 10, "First reply.", now=300), "already")
        self.assertEqual(p.add("A", "m3", 30, "   ", now=300), "empty")
        self.assertEqual([x["id"] for x in p.get("A")], ["m1", "m2"])
        self.assertEqual(p.get("B"), [])

    def test_the_limit_refuses_then_swap_drops_the_oldest(self):
        p = ar.Pins(self.file)
        for k in range(ar.PIN_MAX):
            self.assertEqual(p.add("A", "m%d" % k, k, "Reply %d." % k, now=k), "added")
        self.assertEqual(p.add("A", "m9", 9, "The ninth.", now=50), "full")
        self.assertEqual(len(p.get("A")), ar.PIN_MAX)  # refused: nothing changed
        self.assertNotIn("m9", [x["id"] for x in ar.Pins(self.file).get("A")])
        self.assertEqual(p.swap("A", "m9", 9, "The ninth.", now=60), "added")
        ids = [x["id"] for x in ar.Pins(self.file).get("A")]
        self.assertEqual(len(ids), ar.PIN_MAX)
        self.assertEqual(ids[0], "m1")  # m0, pinned first, went
        self.assertEqual(ids[-1], "m9")
        self.assertEqual(p.add("B", "x", 1, "Another session's.", now=1), "added")  # the limit is per session

    def test_swap_of_a_pinned_reply_drops_nothing(self):
        p = ar.Pins(self.file)
        for k in range(ar.PIN_MAX):
            p.add("A", "m%d" % k, k, "Reply %d." % k, now=k)
        self.assertEqual(p.swap("A", "m3", 3, "Reply 3.", now=99), "already")
        self.assertEqual(len(p.get("A")), ar.PIN_MAX)

    def test_kept_across_restarts_and_removed(self):
        p = ar.Pins(self.file)
        p.add("A", "m1", 10, "Kept **reply**.", now=100)
        p.add("closed", "m5", 50, "A closed session's pin.", now=100)
        again = ar.Pins(self.file)
        self.assertEqual(again.get("A"), [{"id": "m1", "at": 10, "text": "Kept **reply**.", "pinned": 100}])
        self.assertEqual(len(again.get("closed")), 1)  # kept for when it opens again
        self.assertTrue(again.remove("A", "m1"))
        self.assertFalse(again.remove("A", "m1"))
        self.assertEqual(ar.Pins(self.file).get("A"), [])
        self.assertEqual(len(ar.Pins(self.file).get("closed")), 1)

    def test_a_broken_file_starts_empty(self):
        os.makedirs(os.path.dirname(self.file))
        with open(self.file, "w") as f:
            f.write('{"A": [{"id": "m1"}, "x", {"id": "m2", "text": "ok"}], "B": "nope"')
        self.assertEqual(ar.Pins(self.file).data, {})
        with open(self.file, "w") as f:
            f.write('{"A": [{"id": "m1"}, "x", {"id": "m2", "text": "ok"}], "B": "nope"}')
        self.assertEqual([x["id"] for x in ar.Pins(self.file).get("A")], ["m2"])

    def test_survives_the_transcript_growing_and_shrinking(self):
        path = os.path.join(self.dir, "t.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for o in (user("one", 1), part("m1", text("## Plan\nShip on **Thursday**."), "end_turn", 2)):
                f.write(json.dumps(o) + "\n")
        t = ar.Transcript(path, typed)
        t.read()
        r = t.replies()[0]
        p = ar.Pins(self.file)
        p.add("A", r["id"], r["at"], r["text"])
        with open(path, "a", encoding="utf-8") as f:  # a newer prompt and reply: m1 is no longer a current reply
            for o in (user("two", 3), part("m2", text("Something else."), "end_turn", 4)):
                f.write(json.dumps(o) + "\n")
        t.read()
        self.assertEqual([x["id"] for x in t.replies()], ["m2"])
        with open(path, "w", encoding="utf-8") as f:  # compacted: the file is shorter and read again
            f.write(json.dumps(user("summary", 5)) + "\n")
        t.read()
        self.assertEqual(t.replies(), [])
        kept = ar.Pins(self.file).get("A")
        self.assertEqual([(x["id"], x["text"]) for x in kept], [("m1", "## Plan\nShip on **Thursday**.")])

    def test_label(self):
        self.assertEqual(ar.pin_label("## Plan for the **yearly** plan release next week\nmore"), "Plan for the yearly plan…")
        self.assertEqual(ar.pin_label("\n\n- `cut` it"), "cut it")
        self.assertEqual(ar.pin_label("See [the docs](https://x.y) now."), "See the docs now.")
        self.assertEqual(ar.pin_label("Averyveryveryveryveryverylongwordthatgoesonandon"), "Averyveryveryveryveryverylongwor…")
        self.assertEqual(ar.pin_label("  "), "Reply")


def follow():
    """front_session() and what it reads (shown_name, tab_session, CLAUDE_TITLE), taken out of agent-panel.pyw by ast."""
    with open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    keep = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("shown_name", "tab_session", "front_session")]
    keep += [n for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "CLAUDE_TITLE" for t in n.targets)]
    ns = {"re": re}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "agent-panel.pyw", "exec"), ns)
    return ns["front_session"]


class Follow(unittest.TestCase):
    """Which session's terminal came to the front, for the reply window to show. Windows are made-up numbers:
    10 holds session a alone, 20 holds b and c as two tabs, 30 is the panel's own (the reply window), 40 is any
    other program."""

    def setUp(self):
        self.f = follow()
        self.s = [{"id": "a", "status": "idle", "pid": 1, "name": "alpha"},
                  {"id": "b", "status": "busy", "pid": 2, "name": "beta"},
                  {"id": "c", "status": "idle", "pid": 3, "name": "gamma"},
                  {"id": "h", "status": "idle", "pid": 4, "name": "headless", "headless": True},
                  {"id": "z", "status": None, "pid": 5, "name": "closed"}]
        self.win = {1: 10, 2: 20, 3: 20, 4: 50, 5: 60}

    def go(self, front, before=None):
        s = self.f(front, before, self.s, lambda o: self.win.get(o.get("pid")), lambda h: h == 30)
        return s and s["id"]

    def test_a_lone_session_terminal(self):
        self.assertEqual(self.go((10, "✳ alpha")), "a")
        self.assertEqual(self.go((10, "⠂ some other title")), "a")  # its only session, whatever it is titled

    def test_a_shell_tab_in_its_window_is_not_followed(self):
        self.assertIsNone(self.go((10, "Windows PowerShell")))

    def test_a_shared_terminal_follows_the_tab_in_view(self):
        self.assertEqual(self.go((20, "✳ beta")), "b")
        self.assertEqual(self.go((20, "✳ gamma"), (20, "beta")), "c")  # another tab of the same window
        self.assertIsNone(self.go((20, "✳ delta")))  # no listed session shows there: never a guess

    def test_only_a_change_counts(self):
        self.assertIsNone(self.go((10, "⠐ alpha"), (10, "alpha")))  # the spinner glyph turned
        self.assertEqual(self.go((10, "✳ alpha"), (30, "Field Agents Reply")), "a")  # back from the reply window

    def test_own_windows_and_other_programs_leave_it(self):
        self.assertIsNone(self.go((30, "✳ alpha")))  # the panel, the strip, the reply window
        self.assertIsNone(self.go((40, "✳ alpha")))  # not a session's terminal
        self.assertIsNone(self.go((50, "✳ headless")))  # a headless run
        self.assertIsNone(self.go((60, "✳ closed")))  # a closed session
        self.assertIsNone(self.go(None))
        self.assertIsNone(self.go((0, "")))


if __name__ == "__main__":
    unittest.main(verbosity=1)
