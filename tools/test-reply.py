# Unit tests for agent-reply.py, the reply window's rules: which transcript lines are a reply (end_turn text of the
# main thread; not tool_use narration, thinking, a subagent, <synthetic> or an API error), every reply since the
# last typed prompt, two text blocks of one message joined, reading on from where the last read stopped; the
# drafts kept per session; the pinned replies; the line Send to Session types and the text Copy puts on the
# clipboard; which session's terminal came to the front (front_session, taken from agent-panel.pyw by ast); the
# dictation client against a stand-in speech-to-text server on 127.0.0.1 (no microphone, nothing recorded).
# Run: python -I tools/test-reply.py
# Every transcript here is made up, in a temporary folder; the drafts file too. The prompt rule is the panel's own
# typed_prompt(), taken out of agent-panel.pyw by ast (importing it would trip its one-instance mutex).
import ast, http.server, importlib.util, json, os, re, shutil, tempfile, threading, time, unittest

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

    def test_page_calls_on_threads_of_their_own_lose_nothing(self):
        # pywebview runs each api.draft() on a thread of its own, beside the panel's loop
        import threading
        d = ar.Drafts(self.file)

        def typing(t):
            for k in range(40):
                d.put("A", "m#%d-%d" % (t, k), "p", "answer %d" % k)
                d.expire("A", 1)  # the loop's pass meanwhile (an old prompt: nothing goes)
        threads = [threading.Thread(target=typing, args=(t,)) for t in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(d.get("A")), 240)
        self.assertEqual(len(ar.Drafts(self.file).get("A")), 240)  # the file as well
        self.assertEqual([f for f in os.listdir(os.path.dirname(self.file)) if f.endswith(".tmp")], [])

    def test_a_broken_file_starts_empty(self):
        os.makedirs(os.path.dirname(self.file))
        with open(self.file, "w") as f:
            f.write("{not json")
        self.assertEqual(ar.Drafts(self.file).data, {})


PLAN = ("Here is the plan for the release.\n\n1. **Pricing:** monthly or yearly?\n2. Ship it on Thursday?\n"
        "   It depends on QA.\n3. Tell the team.\n\n| Item | Owner |\n|---|---|\n| Docs | Ada |\n| Tests | Deniz |\n\n"
        "Reply with :1 yes or :2 no if you like.")
PLAN_ENDS = [0, 2, 4, 5, 10, 12]  # the last line of each block of PLAN, as the page's blocks() counts them (Markdown)
PLAN_SENT = (
    'My answers inside your reply of 17:42 (your reply is quoted with "> ", my answers are marked "My answer:"):\n'
    "\n"
    "> Here is the plan for the release.\n"
    "\n"
    "My answer: Both, monthly first.\n"
    "\n"
    "> 1. **Pricing:** monthly or yearly?\n"
    "> 2. Ship it on Thursday?\n"
    ">    It depends on QA.\n"
    "\n"
    "My answer: Yes,\n"
    "if QA passes `:3` cases\n"
    "\n"
    "> 3. Tell the team.\n"
    ">\n"
    "> | Item | Owner |\n"
    "> |---|---|\n"
    "> | Docs | Ada |\n"
    "> | Tests | Deniz |\n"
    "\n"
    "My answer: Add a row for Release notes.\n"
    "\n"
    "> Reply with :1 yes or :2 no if you like.")
PLAN_ANSWERS = [(0, "Here is the plan for the release.", "Both, monthly first."),
                (4, "2. Ship it on Thursday?\n   It depends on QA.", "  Yes,\nif QA passes :3 cases\n\n"),
                (10, "| Item | Owner |\n|---|---|\n| Docs | Ada |\n| Tests | Deniz |", "Add a row for Release notes.")]


def panel_methods(*names):
    """Methods of agent-panel.pyw's Panel, in a class of their own (importing it would trip its one-instance mutex)."""
    with open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    panel = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Panel")
    keep = [n for n in panel.body if isinstance(n, ast.FunctionDef) and n.name in names]
    ns = {"SEEN_MAX": 400}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "agent-panel.pyw", "exec"), ns)
    return type("P", (), {n: ns[n] for n in names})


class Sent(unittest.TestCase):
    """What Send to Session pastes and Copy copies: each reply answered, whole, quoted, with the answers inside."""

    def test_one_reply_three_answers_in_a_paragraph_a_list_item_and_a_table(self):
        self.assertEqual(ar.format_answered_replies([(PLAN, "17:42", PLAN_ANSWERS)]), PLAN_SENT)

    def test_the_hook_takes_no_marker_from_the_pasted_reply(self):
        # a live list holding :1 :2 :3 (terminal selections): the ":1" and ":2" of Claude's quoted text and the ":3"
        # inside the answer are no markers, so the prompt goes untouched; a ":2" typed after the paste still is one
        qr = load_quote_reply()
        self.assertEqual(qr.parse(PLAN_SENT, {1, 2, 3}), ([], [], False))
        answers, unknown, _ = qr.parse(PLAN_SENT + "\n\n:2 and this selection is fine", {1, 2, 3})
        self.assertEqual((answers, unknown), ([(2, "and this selection is fine")], []))

    def test_two_replies_each_its_own_block_in_order_unanswered_left_out(self):
        text = ar.format_answered_replies([("First reply.\n\nSecond paragraph.", "09:05", [(2, "Second paragraph.", "ok")]),
                               ("Not answered.", "09:10", [(0, "Not answered.", "   ")]),
                               ("Last one.", "", [(0, "Last one.", "fine")])])
        self.assertEqual(text, 'My answers inside your reply of 09:05 (your reply is quoted with "> ", my answers are '
                               'marked "My answer:"):\n\n> First reply.\n>\n> Second paragraph.\n\nMy answer: ok\n\n'
                               'My answers inside your reply (your reply is quoted with "> ", my answers are marked '
                               '"My answer:"):\n\n> Last one.\n\nMy answer: fine')
        self.assertEqual(ar.format_answered_replies([("x", "1", [(0, "x", " ")])]), "")

    def test_a_block_found_by_its_text_when_the_line_does_not_match_and_one_not_found_goes_last(self):
        text = ar.answer_block("A\n\nB\n\nC", "", [(0, "B", "b!"), (9, "Gone", "g!")])
        self.assertEqual(text.split("\n")[2:], ["> A", ">", "> B", "", "My answer: b!", "", "> C", "", "> Gone", "",
                                                "My answer: g!"])

    def test_a_quoted_line_typed_in_an_answer_stays_the_users(self):
        self.assertEqual(ar.mine("> not Claude's\nmine"), "\\> not Claude's\nmine")

    def test_time_today_and_another_day(self):
        import time as t
        now = t.mktime((2026, 11, 23, 18, 0, 0, 0, 0, -1))
        self.assertEqual(ar.when_of(t.mktime((2026, 11, 23, 17, 42, 0, 0, 0, -1)), now), "17:42")
        self.assertEqual(ar.when_of(t.mktime((2026, 11, 22, 9, 5, 0, 0, 0, -1)), now), "22 Nov 09:05")
        self.assertEqual(ar.when_of(None, now), "")

    def test_the_panel_uses_the_text_the_window_was_handed_pinned_replies_too(self):
        P = panel_methods("seen", "reply_text")
        p = P()
        p.reply_mod, p.reply_seen = ar, {}
        p.seen("s1", "m1", PLAN, None)
        p.seen("s1", "pin", "An old pinned reply.\n\nIts question?", None)
        p.seen("s2", "m1", "another session's text", None)
        answers = [{"key": "m1#%d" % [0, 2, 4][k], "passage": a[1], "text": a[2], "line": a[0]}
                   for k, a in enumerate(PLAN_ANSWERS)]
        answers.append({"key": "pin#1", "passage": "Its question?", "text": "Yes.", "line": 2})
        text = p.reply_text("s1", answers)
        self.assertTrue(text.startswith(PLAN_SENT.replace("reply of 17:42", "reply")))
        self.assertTrue(text.endswith("> An old pinned reply.\n>\n> Its question?\n\nMy answer: Yes."))
        self.assertNotIn("another session", text)

    def test_the_store_keeps_the_newest(self):
        P = panel_methods("seen")
        p = P()
        p.reply_seen = {}
        for k in range(405):
            p.seen("s", "m%d" % k, "t", None)
        self.assertEqual(len(p.reply_seen), 400)
        self.assertNotIn(("s", "m4"), p.reply_seen)
        p.seen("s", "m5", "t", None)  # seen again: the newest, so the next to go is m6
        p.seen("s", "m405", "t", None)
        self.assertIn(("s", "m5"), p.reply_seen)
        self.assertNotIn(("s", "m6"), p.reply_seen)

    def test_a_300_line_reply(self):
        lines = []
        for k in range(100):
            lines += ["Paragraph %d says :%d here." % (k, k % 7), "and goes on.", ""]
        text = "\n".join(lines[:-1])  # 299 lines
        text += "\n" + "Last line."   # 300
        self.assertEqual(len(text.split("\n")), 300)
        out = ar.format_answered_replies([(text, "12:00", [(1, "Paragraph 0 says :0 here.\nand goes on.", "first"),
                                               (151, "Paragraph 50 says :1 here.\nand goes on.", "middle"),
                                               (299, "Last line.", "last")])])
        quoted = [l[2:] if l.startswith("> ") else "" for l in out.split("\n") if l.startswith(">")]
        self.assertEqual([l for l in quoted if l], [l for l in text.split("\n") if l])
        self.assertEqual(out.count("My answer: "), 3)
        self.assertTrue(out.endswith("> Last line.\n\nMy answer: last"))
        self.assertIn("> and goes on.\n\nMy answer: middle\n\n> Paragraph 51", out)
        self.assertEqual(load_quote_reply().parse(out, set(range(10))), ([], [], False))


def load_quote_reply():
    spec = importlib.util.spec_from_file_location("qr", os.path.join(ROOT, "quote-reply.py"))
    qr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qr)
    return qr


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
# clickAction(), paraTip() and tabTarget(), the answer box's click rule, the paragraph's hint and Tab's next block.
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
out.tips = [paraTip("p"), paraTip("li"), paraTip("table")];
out.tab = [tabTarget(5, 0, false), tabTarget(5, 3, false), tabTarget(5, 4, false), tabTarget(5, 4, true), tabTarget(5, 0, true),
  tabTarget(1, 0, false), tabTarget(5, -1, false)];
const run = (evs) => { let s = null; const acts = []; for (const e of evs) { const r = spaceRule(s, e); s = r.s; acts.push(r.act); } return [s && s.st, acts]; };
const D = {type: "down", repeat: false}, R = {type: "down", repeat: true}, U = {type: "up"}, T = {type: "timer"},
  K = {type: "key"}, B = {type: "blur"}, F = {type: "fail"};
out.space = {consts: [WARM, START, HOLD_MS], tap: run([D, U]), tapRepeat: run([D, R, U]), hold: run([D, R, R, R, R, R, U]),
  noRepeat: run([D, T, U]), roll: run([D, K]), rollWarm: run([D, R, K]), lateTimer: run([D, U, T]), blurWarm: run([D, R, B]),
  blurRec: run([D, T, B]), fail: run([D, T, F, R, U]), recKey: run([D, T, K]), stray: run([R])};
out.join = [joinDictation("", "", "merhaba"), joinDictation("abc", "", "x"), joinDictation("abc ", "def", "x"),
  joinDictation("abc", " def", "x"), joinDictation("abc\n", "", "x")];
// the answers Send to Session takes: paragraphs, list items, a table, two replies; one under another chip and an
// emptied one stay out; in the order the blocks stand, whatever order they were typed in
const ans = {"m2#0": {text: "six"}, "m1#9": {text: "table"}, "m1#0": {text: "intro"}, "pin#1": {text: "other chip"},
  "m1#6": {text: "item two"}, "m1#5": {text: "item one"}, "m1#7": {text: "  "}};
const src = {"m1#0": "Intro.", "m1#5": "1. one", "m1#6": "2. two", "m1#7": "3. three", "m1#9": "| a |\n|---|\n| 1 |", "m2#0": "Second."};
out.send = sendList(ans, src, ["m1#0", "m1#5", "m1#6", "m1#7", "m1#9", "m2#0"], {"m1#0": 0, "m1#5": 7, "m1#9": 12});
out.planEnds = blocks(PLAN).map(b => b.last);
out.sendUnordered = sendList(ans, src, []).length;
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
    keep.append("const PLAN = %s;" % json.dumps(PLAN))
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
                                           "Click to answer this table"])

    def test_send_takes_every_answer_shown_in_block_order(self):
        self.assertEqual([(a["key"], a["text"], a["passage"]) for a in self.md["send"]],
                         [("m1#0", "intro", "Intro."), ("m1#5", "item one", "1. one"), ("m1#6", "item two", "2. two"),
                          ("m1#9", "table", "| a |\n|---|\n| 1 |"), ("m2#0", "six", "Second.")])
        self.assertEqual(self.md["sendUnordered"], 5)  # the bar's count: the same answers
        self.assertEqual([a["line"] for a in self.md["send"]], [0, 7, -1, 12, -1])  # -1: no line known

    def test_each_block_names_its_last_line_where_its_answer_goes(self):
        self.assertEqual(self.md["planEnds"], PLAN_ENDS)

    def test_tab_goes_to_the_next_block_and_shift_tab_back_ending_at_either_end(self):
        self.assertEqual(self.md["tab"], [1, 4, -1, 3, -1, -1, -1])


    def test_space_tap_types_one_space_and_a_hold_listens_as_claude_code_does(self):
        sp = self.md["space"]
        self.assertEqual(sp["consts"], [2, 5, 700])  # Claude Code 2.1.330: warm-up at 2 rapid Spaces, listening at 5
        self.assertEqual(sp["tap"], [None, ["space", ""]])  # the space goes in at once and stays
        self.assertEqual(sp["tapRepeat"], [None, ["space", "warm", "cold"]])  # let go while warming: the space stays
        # held: the first Space types, the 2nd says keep holding, the 5th takes the space out and listens; up stops
        self.assertEqual(sp["hold"], [None, ["space", "warm", "", "", "start", "", "stop"]])
        self.assertEqual(sp["noRepeat"], [None, ["space", "start", "stop"]])  # a keyboard without repeat: HOLD_MS
        self.assertEqual(sp["stray"], [None, [""]])  # a repeat whose press was not seen types nothing

    def test_space_rolled_over_by_the_next_key_stays_a_space(self):
        self.assertEqual(self.md["space"]["roll"], [None, ["space", ""]])
        self.assertEqual(self.md["space"]["rollWarm"], [None, ["space", "warm", "cold"]])

    def test_space_timer_after_release_leaving_the_box_and_a_failed_start(self):
        sp = self.md["space"]
        self.assertEqual(sp["lateTimer"], [None, ["space", "", ""]])
        self.assertEqual(sp["blurWarm"], [None, ["space", "warm", "cold"]])
        self.assertEqual(sp["blurRec"], [None, ["space", "start", "stop"]])  # what was said is still sent
        self.assertEqual(sp["fail"], [None, ["space", "start", "", "", ""]])  # the note stays; repeats type nothing
        self.assertEqual(sp["recKey"], ["rec", ["space", "start", ""]])  # keys typed while listening go in as typed

    def test_dictated_text_gets_a_space_where_the_text_round_it_needs_one(self):
        self.assertEqual(self.md["join"], [{"s": "merhaba", "caret": 7}, {"s": " x", "caret": 2},
                                           {"s": "x ", "caret": 1}, {"s": " x", "caret": 2}, {"s": "x", "caret": 1}])


class Stub(http.server.BaseHTTPRequestHandler):
    """A stand-in for the speech-to-text server, with its contract: GET /api/health, POST /api/dictate."""
    answer = (200, {"text": "merhaba deploy", "language": "tr", "ms": 12})
    seen = []

    def do_GET(self):
        self.reply(200, {"app": "stub"}) if self.path == "/api/health" else self.reply(404, {"error": "not found"})

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        Stub.seen.append((self.path, self.headers.get("Content-Type"), body))
        self.reply(*Stub.answer)

    def reply(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


def serve(port=0):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def free_port():
    import socket
    with socket.socket() as k:
        k.bind(("127.0.0.1", 0))
        return k.getsockname()[1]


class Dictation(unittest.TestCase):
    """agent-reply.py Dictation against a stand-in server on 127.0.0.1; settings from a made-up dict."""

    def setUp(self):
        self.srv = serve()
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]
        self.conf, self.saved = {"dictation_url": self.url}, {}
        Stub.seen, Stub.answer = [], (200, {"text": "merhaba deploy", "language": "tr", "ms": 12})

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def client(self, **kw):
        self.conf.update(kw)
        return ar.Dictation(self.conf.get, lambda k, v: (self.saved.__setitem__(k, v), self.conf.__setitem__(k, v)))

    def test_the_term_list_is_written_once_with_the_defaults_and_read_from_the_file(self):
        d = self.client()
        self.assertEqual(d.ready(), {"up": True})
        self.assertEqual(self.saved["dictation_terms"], ar.DICTATE_TERMS)
        self.saved.clear()
        self.conf["dictation_terms"] = ["Fable", "  ", "Opus"]
        self.assertEqual(d.settings()[4], ["Fable", "Opus"])
        self.assertEqual(self.saved, {})  # an existing list is never overwritten

    def test_the_recording_goes_with_its_type_the_language_and_the_terms(self):
        d = self.client(dictation_terms=["deploy", "memory.md"])
        self.assertEqual(d.transcribe(b"OggS-audio", "audio/webm;codecs=opus"), {"text": "merhaba deploy"})
        path, mime, body = Stub.seen[0]
        self.assertTrue(path.startswith("/api/dictate?"))
        from urllib.parse import parse_qs, urlparse
        self.assertEqual(parse_qs(urlparse(path).query), {"language": ["tr"], "prompt": ["deploy, memory.md"]})
        self.assertEqual((mime, body), ("audio/webm;codecs=opus", b"OggS-audio"))

    def test_an_empty_text_and_a_server_error_are_notes(self):
        d = self.client()
        Stub.answer = (200, {"text": "  ", "language": "tr", "ms": 3})
        self.assertEqual(d.transcribe(b"x", "audio/wav"), {"note": ar.DICTATE_EMPTY})
        Stub.answer = (500, {"error": "could not decode the audio"})
        self.assertEqual(d.transcribe(b"x", "audio/wav"),
                         {"note": "Voice to Text could not turn this into text: could not decode the audio"})

    def test_a_server_that_is_down_and_not_set_up_to_start_is_a_note(self):
        self.tearDown()
        self.setUp()
        port = free_port()
        d = self.client(dictation_url="http://127.0.0.1:%d" % port)
        self.assertEqual(d.ready(), {"note": "Voice to Text is not running at http://127.0.0.1:%d. Start it, then hold "
                                             "Space again." % port})
        d = self.client(dictation_app="C:\\made-up\\launcher.pyw")
        n = d.ready()
        self.assertEqual(n["link"], {"text": "Open Voice to Text", "uri": ar.APP_LINK})
        self.assertIn(n["link"]["text"], n["note"])  # the words drawn as the link stand in the note

    def test_a_server_that_is_down_is_started_once_and_waited_for(self):
        port, started, srv = free_port(), [], []
        script = os.path.join(tempfile.mkdtemp(), "server.py")
        open(script, "w").close()

        def start(path):  # stands in for start_hidden: the stand-in server comes up a moment later
            started.append(path)
            threading.Timer(0.6, lambda: srv.append(serve(port))).start()
        d = ar.Dictation({"dictation_url": "http://127.0.0.1:%d" % port, "dictation_server": script,
                          "dictation_terms": []}.get, None, start)
        try:
            self.assertEqual(d.ready(), {"starting": True})
            self.assertEqual(d.ready(), {"starting": True})  # a second hold meanwhile starts nothing more
            self.assertEqual(started, [script])
            self.assertEqual(d.wait(), {"up": True})
            self.assertEqual(d.transcribe(b"x", "audio/wav"), {"text": "merhaba deploy"})
            self.assertNotIn("prompt=", Stub.seen[-1][0])  # an empty term list sends no prompt
        finally:
            for k in srv:
                k.shutdown()
                k.server_close()

    def test_a_server_that_never_comes_up_ends_in_a_note(self):
        script = os.path.join(tempfile.mkdtemp(), "server.py")
        open(script, "w").close()
        d = ar.Dictation({"dictation_url": "http://127.0.0.1:%d" % free_port(), "dictation_server": script}.get,
                         None, lambda path: None)
        self.assertEqual(d.ready(), {"starting": True})
        d.started -= ar.DICTATE_START_WAIT - 0.5  # as if it had been starting for nearly the whole wait
        self.assertEqual(d.wait(), {"note": ar.DICTATE_NOT_STARTED})

    def test_open_settings_opens_no_other_address(self):
        for uri in ("ms-settings:windowsupdate", "https://example.com", "C:\\Windows\\notepad.exe", "", ar.APP_LINK):
            self.assertFalse(ar.open_settings(uri))  # the app link only with a "dictation_app" set


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


class SendFront(unittest.TestCase):
    """front_state(): whether Send to Session may type into the session's terminal (10) and the user sees it come
    up: the window in front, nothing over it, a Claude Code tab, its own tab when the window holds several."""

    def setUp(self):
        with open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        keep = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "front_state"]
        keep += [n for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "CLAUDE_TITLE" for t in n.targets)]
        ns = {"re": re}
        exec(compile(ast.Module(body=keep, type_ignores=[]), "agent-panel.pyw", "exec"), ns)
        self.f = ns["front_state"]

    def test_in_front_and_on_top(self):
        self.assertEqual(self.f(10, 10, 10, "✳ alpha", "alpha", False), "")
        self.assertEqual(self.f(10, 10, 10, "✳ alpha", "alpha", True), "")

    def test_the_foreground_covered_by_another_window_is_not_enough(self):
        self.assertEqual(self.f(10, 10, 40, "✳ alpha", "alpha", False), "another window covers it")

    def test_not_in_front(self):
        self.assertEqual(self.f(10, 30, 10, "✳ alpha", "alpha", False), "it is not the window in front")

    def test_tabs(self):
        self.assertEqual(self.f(10, 10, 10, "✳ beta", "alpha", True), "another tab is showing")
        self.assertEqual(self.f(10, 10, 10, "✳ beta", "alpha", False), "")  # its only session, whatever its title
        self.assertEqual(self.f(10, 10, 10, "Windows PowerShell", "alpha", False), "it shows no Claude Code tab")


if __name__ == "__main__":
    unittest.main(verbosity=1)
