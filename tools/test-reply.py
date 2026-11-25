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

    def test_an_answer_lives_as_long_as_its_text_can_be_shown(self):
        # the pure rule: a reply shown (pinned or under Latest) keeps its answers whatever their age
        A = {"pin#0": {"at": 100}, "pin#2-4": {"at": 100}, "cur#1": {"at": 100}, "old#0": {"at": 100}, "new#0": {"at": 300}}
        self.assertEqual(ar.stale(A, 200, {"pin", "cur"}), ["old#0"])
        self.assertEqual(ar.stale(A, None, {"pin", "cur"}), [])  # no prompt known: nothing goes
        self.assertEqual(sorted(ar.stale(A, None, {"cur"}, gone="pin")), ["pin#0", "pin#2-4"])  # unpinned
        self.assertEqual(ar.stale(A, None, {"pin", "cur"}, gone="cur"), [])  # unpinned but still under Latest
        self.assertEqual(ar.reply_of("a#b#12-30"), "a#b")
        self.assertEqual(ar.shown_ids([{"id": "cur"}], [{"id": "pin"}]), {"cur", "pin"})

    def test_a_new_prompt_keeps_a_pinned_replys_answers(self):
        d = ar.Drafts(self.file)
        d.put("A", "pin#0", "p", "keep me", now=100)
        d.put("A", "pin#1-3", "p", "the area's", now=100)
        d.put("A", "latest#0", "p", "goes", now=100)
        d.put("B", "latest#0", "p", "another session's", now=100)
        # prompt at 200: Latest is now "new"; "pin" is pinned and no longer in the transcript's Latest
        self.assertTrue(d.expire("A", 200, ar.shown_ids([{"id": "new"}], [{"id": "pin"}])))
        self.assertEqual(sorted(d.get("A")), ["pin#0", "pin#1-3"])
        self.assertFalse(d.expire("A", 900, {"pin"}))  # more prompts: still there
        self.assertEqual(list(d.get("B")), ["latest#0"])

    def test_restart_keeps_a_pinned_replys_answers(self):
        pins_file = os.path.join(self.dir, "sub", "reply-pins.json")
        ar.Pins(pins_file).add("A", "pin", 50, "Ship it today?", now=60)
        ar.Drafts(self.file).put("A", "pin#0", "Ship it today?", "yes", now=100)
        # a restart: both files read back, the first pass with a newer prompt and no reply under Latest yet
        d, pins = ar.Drafts(self.file), ar.Pins(pins_file)
        self.assertFalse(d.expire("A", 500, ar.shown_ids([], pins.get("A"))))
        self.assertEqual(ar.Drafts(self.file).get("A")["pin#0"]["text"], "yes")

    def test_unpin_drops_its_answers_unless_the_reply_is_under_latest(self):
        d = ar.Drafts(self.file)
        d.put("A", "pin#0", "p", "typed after the prompt", now=300)
        d.put("A", "cur#0", "p", "on Latest", now=300)
        d.put("A", "other#0", "p", "another pin", now=100)
        self.assertTrue(d.expire("A", None, {"cur", "other"}, gone="pin"))  # unpinned, not under Latest: goes
        self.assertEqual(sorted(d.get("A")), ["cur#0", "other#0"])
        self.assertFalse(d.expire("A", None, {"cur", "other"}, gone="cur"))  # a pin of a reply still under Latest
        self.assertEqual(sorted(ar.Drafts(self.file).get("A")), ["cur#0", "other#0"])

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
    'My answers inside your reply of 17:42 (your reply is quoted with "> ", my answers start with "My answer"):\n'
    "\n"
    "> Here is the plan for the release.\n"
    "\n"
    'My answer to "Here is the plan for the release.": Both, monthly first.\n'
    "\n"
    "> 1. **Pricing:** monthly or yearly?\n"
    "> 2. Ship it on Thursday?\n"
    ">    It depends on QA.\n"
    "\n"
    'My answer to "2. Ship it on Thursday?": Yes,\n'
    "if QA passes `:3` cases\n"
    "\n"
    "> 3. Tell the team.\n"
    ">\n"
    "> | Item | Owner |\n"
    "> |---|---|\n"
    "> | Docs | Ada |\n"
    "> | Tests | Deniz |\n"
    "\n"
    'My answer to "| Item | Owner |": Add a row for Release notes.\n'
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
        self.assertEqual(text, 'My answers inside your reply of 09:05 (your reply is quoted with "> ", my answers '
                               'start with "My answer"):\n\n> First reply.\n>\n> Second paragraph.\n\nMy answer to "Second paragraph.": ok\n\n'
                               'My answers inside your reply (your reply is quoted with "> ", my answers start with '
                               '"My answer"):\n\n> Last one.\n\nMy answer to "Last one.": fine')
        self.assertEqual(ar.format_answered_replies([("x", "1", [(0, "x", " ")])]), "")

    def test_a_block_found_by_its_text_when_the_line_does_not_match_and_one_not_found_goes_last(self):
        text = ar.answer_block("A\n\nB\n\nC", "", [(0, "B", "b!"), (9, "Gone", "g!")])
        self.assertEqual(text.split("\n")[2:], ["> A", ">", "> B", "", 'My answer to "B": b!', "", "> C", "", "> Gone", "",
                                                'My answer to "Gone": g!'])

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
        self.assertTrue(text.endswith("> An old pinned reply.\n>\n> Its question?\n\nMy answer to \"Its question?\": Yes."))
        self.assertNotIn("another session", text)

    def test_with_no_answer_the_reply_goes_as_it_is(self):
        # Copy and Send to Session are never off: with no answer the page hands "<message id>#" per reply shown
        self.assertEqual(ar.answer_block("\nFirst.\n\nSecond?\n", "09:05", []),
                         'Your reply of 09:05, quoted with "> ":\n\n> First.\n>\n> Second?')
        self.assertEqual(ar.answer_block("", "09:05", []), "")
        P = panel_methods("seen", "reply_text")
        p = P()
        p.reply_mod, p.reply_seen = ar, {}
        p.seen("s1", "m1", "One.", None)
        p.seen("s1", "m2", "Two.", None)
        self.assertEqual(p.reply_text("s1", [{"key": "m1#", "passage": "", "text": "", "line": -1},
                                             {"key": "m2#", "passage": "", "text": "", "line": -1}]),
                         'Your reply, quoted with "> ":\n\n> One.\n\nYour reply, quoted with "> ":\n\n> Two.')
        self.assertEqual(p.reply_text("s1", [{"key": "m1#0", "passage": "One.", "text": "  ", "line": 0}]), "")

    def test_a_send_keeps_the_drafts(self):
        # asked 2026-11-25: a paste deleted by mistake in the terminal must not take the answers with it
        with open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        panel = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Panel")
        send = next(n for n in panel.body if isinstance(n, ast.FunctionDef) and n.name == "reply_send")
        self.assertNotIn("self.drafts", ast.unparse(send))

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
        self.assertEqual(out.count('My answer to "'), 3)
        self.assertTrue(out.endswith('> Last line.\n\nMy answer to "Last line.": last'))
        self.assertIn('> and goes on.\n\nMy answer to "Paragraph 50 says `:1` here.": middle\n\n> Paragraph 51', out)
        self.assertEqual(load_quote_reply().parse(out, set(range(10))), ([], [], False))


def load_quote_reply():
    spec = importlib.util.spec_from_file_location("qr", os.path.join(ROOT, "quote-reply.py"))
    qr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qr)
    return qr


def layout():
    """band_layout(), Panel.card_h() (as a function of a stub) and the constants they read, taken out of
    agent-panel.pyw by ast."""
    with open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    names = {"SHOT_W", "SHOT_H", "SHOT_MIN", "SHOT_PAD", "SHOT_GAP", "REPLY_W", "REPLY_MIN", "REPLY_MIN_W", "REPLY_MIN_H"}
    keep = [n for n in tree.body if isinstance(n, ast.Assign) and any(
        getattr(t, "id", "") in names or any(getattr(e, "id", "") in names for e in getattr(t, "elts", [])) for t in n.targets)]
    keep += [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("band_layout", "reply_height", "resized")]
    keep += [m for n in tree.body if isinstance(n, ast.ClassDef) for m in n.body
             if isinstance(m, ast.FunctionDef) and m.name in ("card_h", "strip_rect")]
    keep.insert(0, ast.parse("import math").body[0])
    ns = {}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "agent-panel.pyw", "exec"), ns)
    return ns


class Layout(unittest.TestCase):
    """The reply window flush with the screen's left edge and the strip of screenshots right of it, SHOT_GAP apart,
    one width. room is from the strip's right end to the screen's left edge: no gap at the left (2026-11-25)."""
    ns = layout()

    def check(self, room, n, head, want=None):
        cols, rows, sw, rw = self.ns["band_layout"](room, n, head, want)
        if head and n and rw:
            self.assertLessEqual(rw + self.ns["SHOT_GAP"] + sw, room)  # never overlapping, the gap kept
        return cols, rows, sw, rw

    def test_a_full_row_is_as_wide_as_the_reply_window(self):
        self.assertEqual(self.check(1632, 6, True), (5, 2, 812, 812))  # 1920 screen, panel docked right
        self.assertEqual(self.check(1632, 5, True), (5, 1, 812, 812))
        self.assertEqual(self.check(2400, 7, True), (5, 2, 820, 820))  # room to spare: both REPLY_W
        self.assertEqual(self.check(1000, 3, True), (3, 1, 496, 496))

    def test_fewer_than_a_row_keep_their_width_and_the_reply_window_its_own(self):
        self.assertEqual(self.check(1632, 3, True), (3, 1, 474, 812))

    def test_the_users_width_is_the_strips_full_row_too(self):
        self.assertEqual(self.check(1632, 6, True, 600), (3, 2, 600, 600))  # narrower: three columns spread over 600
        self.assertEqual(self.check(2400, 9, True, 1100), (7, 2, 1100, 1100))  # wider, room to spare
        self.assertEqual(self.check(1632, 6, True, 1100), (5, 2, 812, 812))  # wider than half the room: held there
        self.assertEqual(self.check(1632, 0, True, 1100), (0, 0, 0, 1100))  # no screenshot: the whole width
        self.assertEqual(self.check(1632, 0, True, 2000), (0, 0, 0, 1632))  # never past the room

    def test_the_reply_window_stands_flush_with_the_left_edge(self):
        wa = type("R", (), {"left": -1920, "top": 0, "right": 0, "bottom": 1040})()  # a screen left of the main one
        for scale, n, want in ((1.0, 6, None), (1.25, 2, 700), (1.5, 0, None)):
            P = type("P", (), {"scale": lambda self: scale, "head_h": 85, "reply_want": [want, None],
                               "strip_anchor": lambda self: (-288, 0, wa), "work_area": lambda self, r=None: wa,
                               "card_h": self.ns["card_h"], "strip_rect": self.ns["strip_rect"]})()
            rect, cols = P.strip_rect(n, True)
            x, y, w = P.reply_box
            self.assertEqual((x, y), (wa.left, 0))  # no gap at the left
            if rect:  # the strip SHOT_GAP right of it
                self.assertGreaterEqual(rect[0] - (x + w), int(self.ns["SHOT_GAP"] * scale) - 1)

    def test_sizing_keeps_the_minimum_and_leaves_the_side_not_dragged(self):
        rz, mw, mh = self.ns["resized"], self.ns["REPLY_MIN_W"], self.ns["REPLY_MIN_H"]
        self.assertEqual(rz("r", 812, 500, 100.4, 50), [912, None])
        self.assertEqual(rz("b", 812, 500, 100, -60), [None, 440])
        self.assertEqual(rz("rb", 812, 500, -700, -700), [mw, mh])
        self.assertEqual((mw, mh), (540, 240))

    def test_a_drag_of_the_corner_follows_the_pointer_and_keeps_the_size_shown(self):
        """reply_sizing_follow(), reply_size_save() and reply_unsize() with a scripted pointer (no real input)."""
        import ctypes, queue
        from ctypes import wintypes as wt
        with open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        meths = [m for n in tree.body if isinstance(n, ast.ClassDef) for m in n.body if isinstance(m, ast.FunctionDef)
                 and m.name in ("reply_sizing_follow", "reply_size_save", "reply_unsize")]
        path = [(100, 100, True), (160, 140, True), (-900, -900, True), (220, 180, False)]  # x, y, button held
        at, saved = [0], {}

        class U:  # the button is read first each turn, then the pointer: each read of the button is the next step
            def GetSystemMetrics(self, i): return 0
            def GetAsyncKeyState(self, b):
                at[0] += 1
                return 0x8000 if path[at[0]][2] else 0
            def GetCursorPos(self, ref):
                ref._obj.x, ref._obj.y = path[at[0]][:2]
        ns = dict(self.ns, user32=U(), wt=wt, ctypes=ctypes, time=type("T", (), {"sleep": staticmethod(lambda t: None)}),
                  save_local=lambda k, v: saved.__setitem__(k, v))
        cls = ast.ClassDef(name="P", bases=[], keywords=[], body=meths, decorator_list=[], type_params=[])
        exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), "agent-panel.pyw", "exec"), ns)
        p = ns["P"]()
        p.scale, p.events, p.size_queued, p.reply_want = (lambda: 1.25), queue.Queue(), False, [None, None]
        p.update_strip = lambda: None
        seen = []
        p.events.put = lambda e, q=p.events: (seen.append((e, list(p.reply_want))), queue.Queue.put(q, e))
        p.reply_sizing_follow("rb", (0, 0, 1000, 625))  # 800 x 500 logical at 125 %
        self.assertEqual(seen[0], ("reply-size", [848, 532]))  # 60, 40 physical px = 48, 32 logical
        self.assertEqual(seen[-1][0], "reply-sized:rb")
        self.assertEqual(p.reply_want, [896, 564])  # the pointer where the button was let go
        p.reply_now = (0, 0, 1000, 700)  # what the window shows: the screen held it to 1000 px wide
        p.reply_size_save("rb")
        self.assertEqual(saved, {"reply_width": 800, "reply_height": 560})
        p.reply_unsize("b")
        self.assertEqual((p.reply_want, saved["reply_height"]), ([800, None], None))

    def test_height_is_the_users_when_sized_else_the_contents(self):
        rh = self.ns["reply_height"]
        self.assertEqual(rh(300, None, 1024, False), 300)  # never sized: the content's
        self.assertEqual(rh(0, None, 1024, False), 120)  # not measured yet
        self.assertEqual(rh(2000, None, 1024, False), 1024)  # longer than the screen: scrolls inside
        self.assertEqual(rh(300, 700, 1024, False), 700)  # sized: the user's, the replies leave room below
        self.assertEqual(rh(3000, 700, 1024, False), 700)  # ... or scroll inside
        self.assertEqual(rh(300, 1500, 1024, False), 1024)  # never past the screen
        self.assertEqual(rh(85, 700, 1024, True), 85)  # folded: the head alone
        self.assertEqual(rh(10, None, 1024, True), 40)

    def test_no_screenshot_leaves_the_reply_window_alone(self):
        self.assertEqual(self.check(1600, 0, True), (0, 0, 0, 820))

    def test_no_session_is_the_strip_alone(self):
        self.assertEqual(self.check(1600, 3, False), (3, 1, 474, 0))

    def test_a_narrow_screen_narrows_the_reply_window(self):
        self.assertEqual(self.check(600, 2, True), (1, 2, 162, 430))

    def card(self, head_h, head=True):
        return self.ns["card_h"](type("P", (), {"head_h": head_h})(), head)

    def test_a_row_of_the_strip_is_as_tall_as_the_reply_windows_head(self):
        pad = self.ns["SHOT_PAD"]
        for head_h in (85, 84, 119, 137, 171):  # one tab row; folded; with pins; two tab rows; both
            self.assertEqual(pad + self.card(head_h) + pad, head_h)
        self.assertEqual(self.ns["SHOT_H"] + 2 * pad, 85)  # without a session: the head's usual height

    def test_without_a_measured_head_the_card_keeps_its_own_height(self):
        self.assertEqual(self.card(0), self.ns["SHOT_H"])
        self.assertEqual(self.card(137, head=False), self.ns["SHOT_H"])
        self.assertEqual(self.card(20), self.ns["SHOT_MIN"])


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
out.codes = blocks("```js\nconst a = 1;\n```\n\n~~~\n" + Array.from({length: 60}, (_, i) => "line " + i).join("\n") + "\n~~~").map(b => blockHtml(b, "k"));
const C = (o) => clickAction(Object.assign({own: false, moved: false, detail: 1, box: false, text: false}, o));
out.click = {open: C({}), close: C({box: true}), keep: C({box: true, text: true}), drag: C({moved: true}),
  dragShut: C({moved: true, box: true}), dbl: C({detail: 2}), triple: C({detail: 3, box: true}), own: C({own: true}),
  ownOpen: C({own: true, box: true})};
out.tips = [paraTip("p"), paraTip("li"), paraTip("table")];
out.tab = [tabTarget(5, 0, false), tabTarget(5, 3, false), tabTarget(5, 4, false), tabTarget(5, 4, true), tabTarget(5, 0, true),
  tabTarget(1, 0, false), tabTarget(5, -1, false)];
// what the panel sends is held only while a box has the keyboard of the window in front, or words are being dictated
out.hold = [[true, true, false], [true, false, false], [false, true, false], [false, false, true], [true, false, true],
  [false, false, false]].map(([box, focused, dictating]) => holdsUpdates({box, focused, dictating}));
const run = (evs) => { let s = null; const acts = []; for (const e of evs) { const r = spaceRule(s, e); s = r.s; acts.push(r.act); } return [s && s.st, acts]; };
const D = {type: "down", repeat: false}, R = {type: "down", repeat: true}, U = {type: "up"}, T = {type: "timer"},
  K = {type: "key"}, B = {type: "blur"}, F = {type: "fail"};
out.space = {consts: [WARM, START, HOLD_MS], tap: run([D, U]), tapRepeat: run([D, R, U]), hold: run([D, R, R, R, R, R, U]),
  noRepeat: run([D, T, U]), roll: run([D, K]), rollWarm: run([D, R, K]), lateTimer: run([D, U, T]), blurWarm: run([D, R, B]),
  blurRec: run([D, T, B]), fail: run([D, T, F, R, U]), recKey: run([D, T, K]), stray: run([R])};
// text while Space is held: the commit rule (agreement, the tail rewritten only), cuts at a pause, the last pass
const Z = n => new Array(n).fill(0), V = (ms, x) => new Array(Math.round(ms / 20)).fill(x);  // frames of quiet / speech
let pc = {done: [], prev: []};
const a1 = agreeStep(pc, "Yes, please merge"); pc = a1.piece;
const a2 = agreeStep(pc, "Yes, please merge the pull"); pc = a2.piece;
const a3 = agreeStep(pc, "yes please merge the pull request then"); pc = a3.piece;
const a4 = agreeStep(pc, "Yes, plead merge the pull request, then deploy"); const pc4 = a4.piece;
const c1 = closeStep(pc4, "Yes, please merge the pull request, then deploy it.");
const c2 = closeStep({done: ["Evet", "bunu"], prev: []}, "Evet, bunu yapalım.");
const c3 = closeStep({done: ["Evet", "bunu"], prev: []}, "Peki evet bunu yapalım.");
out.agree = {a1: [a1.commit, a1.interim], a2: [a2.commit, a2.interim], a3: [a3.commit, a3.interim],
  a4: [a4.commit, a4.interim, pc4.done], c1: [c1.commit, c1.interim, c1.piece], c2: c2.commit, c3: c3.commit,
  shrink: agreeStep({done: ["a", "b", "c"], prev: ["a", "b", "c", "d"]}, "a b").commit,
  keys: [wordKey("Request,"), wordKey("İstek."), wordKey("—")]};
const P = (lv, o) => planPass(Object.assign({lv, thr: 0.01, cut: 0, n: lv.length, lastTo: -1, since: 1e9}, o));
const talk4 = Z(10).concat(V(4000, 0.1));
out.plan = {
  quiet: P(Z(40)), silence: P(Z(60)), early: P(Z(10).concat(V(1000, 0.1))),
  part: P(talk4), tooSoon: P(talk4, {since: 500}), nothingNew: P(talk4, {lastTo: talk4.length - 1}),
  hush: P(talk4.concat(Z(12))), endQuiet: P(talk4.concat(Z(16)), {since: 500}), earlyPause: P(Z(10).concat(V(2000, 0.1), Z(30))),
  close: P(Z(10).concat(V(3500, 0.1), Z(30), V(500, 0.1))),
  closeLater: P(Z(10).concat(V(1000, 0.1), Z(30), V(2500, 0.1), Z(30))),
  fromCut: P(Z(200).concat(V(3500, 0.1), Z(30)), {cut: 190}),
  long: P(V(10000, 0.1).concat(V(400, 0.02), V(11000, 0.1))),
  quietest: quietest(V(10000, 0.1).concat(V(400, 0.02), V(11000, 0.1)), 500, 1030)};
out.final = [finalPlan({last: null, cut: 100, n: 300, lastSpeech: 90}), finalPlan({last: null, cut: 0, n: 300, lastSpeech: 250}),
  finalPlan({last: {from: 0, to: 270, text: "x"}, cut: 0, n: 300, lastSpeech: 250}),
  finalPlan({last: {from: 0, to: 255, text: "x"}, cut: 0, n: 300, lastSpeech: 250}),
  finalPlan({last: {from: 0, to: 260, text: "x"}, cut: 0, n: 260, lastSpeech: 259}),
  finalPlan({last: {from: 50, to: 290, text: "x"}, cut: 0, n: 300, lastSpeech: 250})];
const H = lvs => { const c = new Array(BINS).fill(0); lvs.forEach(v => c[levelBin(v)]++); return c; };
out.line = [quietLine(H([])), quietLine(H(Z(50).concat(V(3000, 0.1)))), quietLine(H(V(1000, 0.003).concat(V(3000, 0.1)))),
  quietLine(H(V(1000, 0.003).concat(V(3000, 0.1), [0.9, 0.9, 0.9]))), quietLine(H(V(3000, 0.05)))];
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
// links: a markdown link to a file shows its text, a bare file:/// address, a Windows path in text and in inline code
// (candidates until agent-panel.pyw says they exist), and what is not a path
out.links = inline("See [the shot](file:///C:/Users/x/a%20b/shot.png), file:///C:/Users/x/a%20b/shot.png. Also " +
  "C:\\Users\\x\\plain.txt and (C:/Users/x/notes.md). Code `C:\\Users\\x\\a b\\shot.png` and `npm run C:` and https://ex.com/q");
// the area: what a press starts, the rectangle after a drag, and what its Copy takes
const AP = (o) => areaPress(Object.assign({button: 0, ctl: false, scrollbar: false, detail: 1}, o));
out.areaPress = [AP({}), AP({detail: 0}), AP({ctl: true}), AP({scrollbar: true}), AP({button: 2}), AP({detail: 2}), AP({detail: 3})];
const AB = {w: 800, h: 1000}, p0 = {x: 50, y: 60};
out.areaRect = {draw: areaRect(p0, {x: 170, y: 140}, AB), drawBack: areaRect(p0, {x: 20, y: 20}, AB),
  clamp: areaRect({x: 100, y: 100}, {x: 2000, y: 2000}, AB), out: areaRect({x: 100, y: 100}, {x: -40, y: -9}, AB)};
const T1 = "Intro.\n\n| a | b |\n|---|--:|\n| [x](https://ex.com) | 2 |\n\n1. one\n2. two\n   more\n\n```js\nconst a = 1;\n```\n\n---\n\nEnd.",
  T2 = "## Two\r\n\r\nSecond reply.\r\n\r\n> quoted\r\n> on",
  T3 = "```\nnever closed\nstill code";
out.areaSpans = blocks(T1).map(b => [b.kind, b.from, b.to]);
out.areaMd = {run: areaMarkdown([T1], [[0, [1, 2, 3, 4]]]), gap: areaMarkdown([T1], [[0, [0, 4]]]),
  two: areaMarkdown([T1, T2], [[0, [6, 5]], [1, [0, 1, 2]]]), unclosed: areaMarkdown([T3], [[0, [0]]]),
  none: areaMarkdown([T1], [[0, [99]]]), empty: areaMarkdown([], [])};
// an area answered as a whole: its key, and the merge rule (two areas never overlap: a new one takes in what it touches)
out.areaKeys = [areaKey("m1", 1, 4), areaRange("m1#1-4"), areaRange("a#b#12-30"), areaRange("m1#3"), areaRange("m1#"), areaRange("")];
out.areaMerge = {fresh: areaMerge([], "m1", 2, 3), over: areaMerge(["m1#1-4"], "m1", 3, 3),
  extend: areaMerge(["m1#1-4"], "m1", 4, 6), chain: areaMerge(["m1#5-6", "m1#1-2", "m1#8-9"], "m1", 2, 5),
  apart: areaMerge(["m1#1-2", "m2#3-4"], "m1", 3, 4), ownPara: areaMerge(["m1#3"], "m1", 3, 5)};
// Send to Session's list: an area's answer stands after its blocks, after the last block's own answer
out.areaSend = sendList({"m1#1-4": {text: "Onaylıyorum"}, "m1#4": {text: "only this"}, "m1#0": {text: "intro"}},
  {"m1#0": "Intro.", "m1#4": "3. c", "m1#1-4": "## H\n\n1. a\n2. b\n3. c"}, ["m1#0", "m1#1", "m1#2", "m1#3", "m1#4", "m1#1-4", "m1#5"],
  {"m1#0": 0, "m1#4": 6, "m1#1-4": 6}).map(a => [a.key, a.line]);
// a quick answer put in the box: an empty box becomes the text; otherwise at the caret, over a selection
// Esc in an answer box: a take is cancelled whatever the box holds; text is cleared only by a second Esc in time
const E = (o) => escAction(Object.assign({take: false, text: false, armed: false}, o));
out.esc = [ESC_MS, E({take: true}), E({take: true, text: true, armed: true}), E({text: true}), E({text: true, armed: true}),
  E({}), E({armed: true})];
out.quick = [quickInsert("", 0, 0, "Onaylıyorum"), quickInsert("ab", 1, 1, "X"), quickInsert("abcd", 1, 3, "X"),
  quickInsert("ab", 9, 9, "X")];
// the area's copy: what its frame holds, with every answer inside it (the blocks' own first, the area's last), lines
// counted in the copied text's own lines; a paragraph's box alone; a key not in the reply
const RC = "Intro.\n\n## Plan\n\n1. one\n2. two\n   more\n\nAfter.";
out.areaClip = {area: areaClip("m1#1-3", RC, {"m1#1-3": {text: "Onaylıyorum"}, "m1#3": {text: "own"}, "m1#0": {text: "outside"}, "m1#1-2": {text: "x"}}),
  bare: areaClip("m1#1-3", RC, {"m1#1-3": {text: "  "}}), para: areaClip("m1#4", RC, {"m1#4": {text: "ok"}}),
  one: areaClip("m1#2-2", RC, {"m1#2-2": {text: "yes"}}), gone: areaClip("m1#7-9", RC, {}), none: areaClip("x", RC, {})};
// the page's copy of the answers after the store expired some: kept when stored, or while its reply is shown
out.kept = Object.keys(keptAnswers({"pin#0": {}, "pin#1-3": {}, "cur#2": {}, "old#0": {}, "gone#0": {}},
  {"pin#0": {}, "pin#1-3": {}}, new Set(["cur"]))).sort();
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
    # from a file: the script outgrew Windows' command line (32,767 characters)
    d = tempfile.mkdtemp()
    try:
        path = os.path.join(d, "markdown.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(keep) + "\n" + page[a:b] + JS)
        r = subprocess.run([node, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
    finally:
        shutil.rmtree(d, ignore_errors=True)
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

    def test_a_code_block_is_drawn_open_short_or_long(self):
        short, long = self.md["codes"]
        for h in (short, long):
            self.assertTrue(h.startswith('<div class="code open"><button type="button" data-tip="Hides the code">'), h[:80])
        self.assertIn("<span>1 line</span>", short)
        self.assertIn("<span>60 lines</span>", long)
        self.assertIn("line 59</pre>", long)

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

    def test_file_links_and_windows_paths(self):
        h = self.md["links"]
        u = "file:///C:/Users/x/a%20b/shot.png"
        for part in (f'<a class="lnk" data-url="{u}" data-tip="{u}">the shot</a>,',  # its text, not the address
                     f' <a class="lnk" data-url="{u}" data-tip="{u}">{u}</a>.',  # bare, the full stop left out
                     '<span class="cand" data-path="C:\\Users\\x\\plain.txt">C:\\Users\\x\\plain.txt</span> and',
                     '(<span class="cand" data-path="C:/Users/x/notes.md">C:/Users/x/notes.md</span>).',
                     '<span class="cand" data-path="C:\\Users\\x\\a b\\shot.png"><code>C:\\Users\\x\\a b\\shot.png</code></span>',
                     '<code>npm run C:</code>',  # not a path
                     '<a class="lnk" data-url="https://ex.com/q" data-tip="https://ex.com/q">https://ex.com/q</a>'):
            self.assertIn(part, h)
        self.assertEqual(h.count('class="cand"'), 3)  # nothing inside a link's address became a path

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
        for k in ("drag", "dragShut", "dbl", "triple", "own", "ownOpen"):
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

    def test_updates_wait_for_a_box_only_while_its_window_has_the_keyboard(self):
        # a box in a window behind the terminal (it keeps the focus) holds nothing: the tab follows the terminal in
        # front; words still being transcribed hold the page in any case, so they land in their own session's box
        self.assertEqual(self.md["hold"], [True, False, False, True, True, False])

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


    def test_the_pages_answers_follow_the_store_but_keep_what_is_on_screen(self):
        self.assertEqual(self.md["kept"], ["cur#2", "pin#0", "pin#1-3"])

    def test_esc_cancels_a_take_clears_on_a_second_press_and_leaves_an_empty_box(self):
        self.assertEqual(self.md["esc"], [2000, "cancel", "cancel", "arm", "clear", "leave", "leave"])

    def test_words_two_passes_agree_on_are_committed_and_only_the_tail_changes(self):
        ag = self.md["agree"]
        self.assertEqual(ag["a1"], [[], ["Yes,", "please", "merge"]])  # the first pass is all interim
        # "Yes, please merge" agreed (its last word too: this pass heard past it) go in; the rest stays dim
        self.assertEqual(ag["a2"], [["Yes,", "please", "merge"], ["the", "pull"]])
        # case and punctuation do not break agreement; the newest pass's last word stays dim (cut at the edge)
        self.assertEqual(ag["a3"], [["the", "pull"], ["request", "then"]])
        # a later pass that hears a committed word differently ("plead") rewrites nothing before the tail
        self.assertEqual(ag["a4"], [["request,", "then"], ["deploy"],
                                    ["Yes,", "please", "merge", "the", "pull", "request,", "then"]])
        self.assertEqual(ag["shrink"], [])  # a shorter pass commits nothing and takes nothing back
        self.assertEqual(ag["keys"], ["request", "istek", ""])  # marks dropped too: İ and I read alike

    def test_a_closing_pass_commits_the_rest_of_its_piece_after_the_committed_words(self):
        ag = self.md["agree"]
        self.assertEqual(ag["c1"], [["deploy", "it."], [], {"done": [], "prev": []}])
        self.assertEqual(ag["c2"], ["yapalım."])
        self.assertEqual(ag["c3"], ["yapalım."])  # an extra word in front: found again by the last committed word

    def test_a_pass_is_sent_while_speaking_and_a_piece_is_cut_at_a_pause(self):
        pl, n = self.md["plan"], lambda ms: int(ms / 20 + 0.5)  # as Math.round
        self.assertEqual(pl["quiet"], {"kind": "", "to": 0})  # under a second of quiet: wait
        self.assertEqual(pl["silence"], {"kind": "skip", "to": 60 - n(300)})  # quiet only: dropped, the last 300 ms kept
        self.assertEqual(pl["early"]["kind"], "")  # under FIRST_MS of speech
        self.assertEqual(pl["part"], {"kind": "part", "to": 10 + n(4000)})  # the open piece up to the newest sound
        self.assertEqual(pl["tooSoon"]["kind"], "")  # TICK_MS since the last pass not yet passed
        self.assertEqual(pl["nothingNew"]["kind"], "")  # no speech newer than the last pass
        self.assertEqual(pl["hush"]["kind"], "")  # HUSH_MS into a quiet spell: a pause may be coming
        # END_MS into a quiet spell: a part pass at once (TICK_MS or not), so letting go soon after needs none
        self.assertEqual(pl["endQuiet"], {"kind": "part", "to": 10 + n(4000) + 16})
        # a pause too early in the piece to close it: a part pass at once, over all of it
        self.assertEqual(pl["earlyPause"], {"kind": "part", "to": 10 + n(2000) + 30})
        # a pause MIN_CLOSE_MS into the piece closes it in its middle; the speech after it waits for the next piece
        self.assertEqual(pl["close"], {"kind": "close", "to": 10 + n(3500) + n(250)})
        self.assertEqual(pl["closeLater"], {"kind": "close", "to": 10 + n(1000) + 30 + n(2500) + n(250)})
        self.assertEqual(pl["fromCut"], {"kind": "close", "to": 200 + n(3500) + n(250)})  # counted from the cut
        # no pause in MAX_PIECE_MS: cut at the quietest QUIET_WIN after half of it
        self.assertEqual(pl["long"], {"kind": "close", "to": n(10000) + 7})  # the quiet 300 ms (15 frames) starts at 10 s
        self.assertEqual(pl["quietest"], n(10000) + 7)

    def test_letting_go_sends_one_pass_only_when_the_last_did_not_hear_it_all(self):
        self.assertEqual(self.md["final"], ["none", "send", "use", "send", "use", "send"])

    def test_the_quiet_line_follows_the_floor_and_the_speech_not_one_loud_frame(self):
        ln = self.md["line"]
        self.assertAlmostEqual(ln[0], 0.004)  # nothing heard yet: QUIET_MIN
        self.assertAlmostEqual(ln[1], 0.004, places=4)  # digital silence: QUIET_MIN
        self.assertTrue(0.008 < ln[2] < 0.013, ln[2])  # a 0.003 floor: about 10 dB over it
        self.assertAlmostEqual(ln[3], ln[2])  # a knock does not move it
        self.assertTrue(ln[4] < 0.05 * 0.35, ln[4])  # speech alone: at most QUIET_SPEECH_X of it, so speech stays speech


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

    def test_a_piece_while_speaking_goes_without_the_terms_and_with_the_words_so_far(self):
        from urllib.parse import parse_qs, urlparse
        d = self.client(dictation_terms=["deploy", "memory.md"])
        Stub.answer = (200, {"text": "", "language": "tr", "ms": 3})
        # a part pass (cut mid-speech): no term list, the hold's words as the prompt; empty is no note
        self.assertEqual(d.transcribe(b"RIFF", "audio/wav", "part", "Evet,  bunu\nyapalım"), {"text": ""})
        self.assertEqual(parse_qs(urlparse(Stub.seen[-1][0]).query), {"language": ["tr"], "prompt": ["Evet, bunu yapalım"]})
        self.assertEqual(d.transcribe(b"RIFF", "audio/wav", "part"), {"text": ""})
        self.assertNotIn("prompt=", Stub.seen[-1][0])
        # a closing pass: the terms, then the words so far; empty is no note either
        self.assertEqual(d.transcribe(b"RIFF", "audio/wav", "close", "Evet"), {"text": ""})
        self.assertEqual(parse_qs(urlparse(Stub.seen[-1][0]).query)["prompt"], ["deploy, memory.md. Evet"])
        # the words so far are cut to their last DICTATE_CONTEXT characters
        d.transcribe(b"RIFF", "audio/wav", "close", "x" * 50 + "y" * ar.DICTATE_CONTEXT)
        self.assertEqual(parse_qs(urlparse(Stub.seen[-1][0]).query)["prompt"],
                         ["deploy, memory.md. " + "y" * ar.DICTATE_CONTEXT])
        # all of a hold in one pass (nothing put in the box while speaking): empty is the note, as before
        self.assertEqual(d.transcribe(b"RIFF", "audio/wav", "whole"), {"note": ar.DICTATE_EMPTY})

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


class Links(unittest.TestCase):
    """open_link(), link_path(), existing_paths(): what a click on a link in a reply opens. Nothing is opened: the
    open call, File Explorer and the foreground grant are stand-ins; the files are made up in a temporary folder."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.sub = os.path.join(self.dir, "a b")
        os.makedirs(self.sub)
        self.shot = os.path.join(self.sub, "shot 1.png")
        self.tool = os.path.join(self.sub, "run.cmd")
        for p in (self.shot, self.tool):
            open(p, "w").close()
        self.opened, self.shown = [], []

    def tearDown(self):
        shutil.rmtree(self.dir)

    def url(self, path):
        return "file:///" + path.replace("\\", "/").replace(" ", "%20")

    def go(self, url):
        return ar.open_link(url, startfile=self.opened.append, show=self.shown.append, front=lambda: None)

    def test_decoding(self):
        self.assertEqual(ar.link_path("file:///C:/Users/x/a%20b/shot%231.png"), "C:\\Users\\x\\a b\\shot#1.png")
        self.assertEqual(ar.link_path("file://localhost/D:/q"), "D:\\q")
        self.assertEqual(ar.link_path("file:////C:/x"), "C:\\x")
        self.assertEqual(ar.link_path("file://server/share/d%20x"), "\\\\server\\share\\d x")
        self.assertEqual(ar.link_path("C:/Users/x"), "C:\\Users\\x")
        self.assertEqual(ar.link_path("C:\\Users\\x"), "C:\\Users\\x")
        for no in ("https://ex.com", "./memory.md", "memory.md", "C:", "\\\\server", "", None, "file:///etc/passwd"):
            self.assertIsNone(ar.link_path(no), no)

    def test_a_web_address_goes_to_the_browser(self):
        self.assertEqual(self.go("https://ex.com/a?b=1"), {"ok": True})
        self.assertEqual(self.opened, ["https://ex.com/a?b=1"])

    def test_a_file_link_with_escapes_opens_the_file_and_a_folder_opens_too(self):
        self.assertEqual(self.go(self.url(self.shot)), {"ok": True})
        self.assertEqual(self.go(self.url(self.sub) + "/"), {"ok": True})
        self.assertEqual(self.go(self.shot), {"ok": True})  # a Windows path as written
        self.assertEqual([os.path.normcase(p.rstrip("\\")) for p in self.opened],
                         [os.path.normcase(self.shot), os.path.normcase(self.sub), os.path.normcase(self.shot)])
        self.assertEqual(self.shown, [])

    def test_a_missing_file_is_a_note_and_opens_nothing(self):
        self.assertEqual(self.go(self.url(os.path.join(self.sub, "gone.png"))), {"note": "File not found"})
        self.assertEqual(self.go(os.path.join(self.dir, "nope", "x.txt")), {"note": "File not found"})
        self.assertEqual(self.opened, [])

    def test_the_heads_folder_as_shown_opens_in_explorer(self):
        # the head's line hands where_of() as written: spaces kept, a folder named like a script still opened
        odd = os.path.join(self.dir, "game club", "app.py")
        os.makedirs(odd)
        self.assertEqual(self.go(odd), {"ok": True})
        self.assertEqual(self.go(odd + "\\"), {"ok": True})
        self.assertEqual([os.path.normcase(p.rstrip("\\")) for p in self.opened], [os.path.normcase(odd)] * 2)
        self.assertEqual(self.shown, [])
        self.assertEqual(self.go(os.path.join(self.dir, "moved away")), {"note": "File not found"})
        self.assertEqual(len(self.opened), 2)
        self.assertEqual(ar.link_path(r"\\wsl.localhost\Ubuntu\home\x"), r"\\wsl.localhost\Ubuntu\home\x")

    def test_a_program_or_script_is_shown_in_explorer_not_run(self):
        self.assertEqual(self.go(self.url(self.tool)), {"ok": True})
        self.assertEqual(self.opened, [])
        self.assertEqual([os.path.normcase(p) for p in self.shown], [os.path.normcase(self.tool)])

    def test_anything_else_is_a_note(self):
        self.assertEqual(self.go("./memory.md"), {"note": ar.LINK_UNKNOWN})
        self.assertEqual(self.go("javascript:alert(1)"), {"note": ar.LINK_UNKNOWN})
        self.assertEqual(self.opened, [])

    def test_a_failed_open_is_a_note(self):
        def boom(_):
            raise OSError("no handler")
        self.assertEqual(ar.open_link(self.shot, startfile=boom, front=lambda: None), {"note": ar.LINK_FAILED})

    def test_only_paths_that_exist_are_named(self):
        fwd = self.shot.replace("\\", "/")
        got = ar.existing_paths([self.shot, fwd, os.path.join(self.dir, "gone.txt"), "C:", "https://ex.com", 5, None])
        self.assertEqual(got, [self.shot, fwd])


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class AreaRules(unittest.TestCase):
    """The area: a drag that starts anywhere (text too) draws a rectangle, a double or triple click selects text; the
    rectangle spans the press and the pointer; the copy takes the blocks touched as Claude wrote them."""
    md = None

    @classmethod
    def setUpClass(cls):
        cls.md = node_markdown()

    def test_a_press_anywhere_starts_the_area_but_controls_scroll_bars_and_double_clicks(self):
        # text or blank space alike (a pointerdown's detail is 0): a rectangle; a control, a scroll bar, another
        # button: their own; the second and third press of a double or triple click: the browser's word or paragraph
        self.assertEqual(self.md["areaPress"], ["area", "area", "", "", "", "", ""])

    def test_the_rectangle_while_it_is_drawn(self):
        r = self.md["areaRect"]
        self.assertEqual(r["draw"], {"x": 50, "y": 60, "w": 120, "h": 80})
        self.assertEqual(r["drawBack"], {"x": 20, "y": 20, "w": 30, "h": 40})  # drawn up and left of the press
        self.assertEqual(r["clamp"], {"x": 100, "y": 100, "w": 700, "h": 900})  # never past the replies
        self.assertEqual(r["out"], {"x": 0, "y": 0, "w": 100, "h": 100})

    def test_every_block_knows_its_lines(self):
        self.assertEqual(self.md["areaSpans"], [["p", 0, 0], ["table", 2, 4], ["li", 6, 6], ["li", 7, 8], ["code", 10, 12],
                                                ["hr", 14, 14], ["p", 16, 16]])

    def test_an_area_answer_key_names_one_replys_blocks(self):
        self.assertEqual(self.md["areaKeys"], ["m1#1-4", {"rid": "m1", "a": 1, "b": 4}, {"rid": "a#b", "a": 12, "b": 30},
                                               None, None, None])

    def test_two_areas_never_overlap_a_new_one_takes_in_what_it_touches(self):
        m = self.md["areaMerge"]
        self.assertEqual(m["fresh"], {"key": "m1#2-3", "a": 2, "b": 3, "from": []})
        self.assertEqual(m["over"]["key"], "m1#1-4")  # inside an answered area: that area, its answer kept
        self.assertEqual((m["over"]["from"], m["extend"]["key"], m["extend"]["from"]), (["m1#1-4"], "m1#1-6", ["m1#1-4"]))
        # the grown range takes in what it then touches; one not touched stays apart
        self.assertEqual((m["chain"]["key"], m["chain"]["from"]), ("m1#1-6", ["m1#1-2", "m1#5-6"]))
        self.assertEqual(m["apart"]["key"], "m1#3-4")  # another reply's area is never taken in
        self.assertEqual(m["ownPara"]["from"], [])  # a paragraph's own answer is never taken into an area

    def test_an_area_answer_goes_after_its_last_blocks_own_answer(self):
        self.assertEqual(self.md["areaSend"], [["m1#0", 0], ["m1#4", 6], ["m1#1-4", 6]])

    def test_copy_takes_the_blocks_as_claude_wrote_them(self):
        m = self.md["areaMd"]
        # blocks side by side: one stretch of the reply, so a table, a link, list items and a code fence stay intact
        self.assertEqual(m["run"], "| a | b |\n|---|--:|\n| [x](https://ex.com) | 2 |\n\n1. one\n2. two\n   more\n\n"
                                   "```js\nconst a = 1;\n```")
        self.assertEqual(m["gap"], "Intro.\n\n```js\nconst a = 1;\n```")  # pieces apart: a blank line between
        # two replies, each in its own order (picks given out of order), a blank line between them, CRLF as LF
        self.assertEqual(m["two"], "---\n\nEnd.\n\n## Two\n\nSecond reply.\n\n> quoted\n> on")
        self.assertEqual(m["unclosed"], "```\nnever closed\nstill code")
        self.assertEqual(m["none"], "")
        self.assertEqual(m["empty"], "")

    def test_the_areas_copy_takes_every_answer_inside_its_frame(self):
        c = self.md["areaClip"]
        md = "## Plan\n\n1. one\n2. two\n   more"
        # the blocks' own answer first, the area's last, lines counted in the copied text; outside ones left out
        self.assertEqual(c["area"], {"text": md, "answers": [
            {"key": "m1#3", "passage": "2. two\n   more", "text": "own", "line": 4},
            {"key": "m1#1-3", "passage": md, "text": "Onaylıyorum", "line": 4}]})
        self.assertEqual(c["bare"], {"text": md, "answers": []})
        self.assertEqual(c["para"], {"text": "After.", "answers": [{"key": "m1#4", "passage": "After.", "text": "ok", "line": 0}]})
        self.assertEqual(c["one"], {"text": "1. one", "answers": [{"key": "m1#2-2", "passage": "1. one", "text": "yes", "line": 0}]})
        self.assertIsNone(c["gone"])
        self.assertIsNone(c["none"])


EDGE = next((p for p in (os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
                         os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe")) if os.path.exists(p)), None)

# The real page in headless Edge (no window): agent-reply.html with a stand-in for agent-panel.pyw's API before its
# script (paths() says which paths exist, link() records the address and answers "File not found" for one), and a
# test after it that shows a reply, waits for the paths, clicks links and a paragraph and writes what it saw.
PAGE_STUB = r"""<script>
window.T = {links: [], asked: [], tabs: [], folds: 0};
const T_API = {
  paths(ps) { T.asked.push(ps); return Promise.resolve(ps.filter(p => /shot|plain/.test(p))); },
  link(u) { T.links.push(u); return Promise.resolve(/gone/.test(u) ? {note: "File not found"} : {ok: true}); },
  tab(sid) { T.tabs.push(sid); return Promise.resolve(null); },
  fold() { T.folds++; return Promise.resolve(null); },
  send(sid, list) { (T.sends = T.sends || []).push([sid, list]); return Promise.resolve({t: "Pasted", err: false}); },
  copy(sid, list) { (T.copies = T.copies || []).push([sid, list]); return Promise.resolve(true); },
  copy_area(text) { (T.area = T.area || []).push(text); return Promise.resolve(true); },
  draft(sid, key, passage, text) { (T.drafts = T.drafts || []).push([sid, key, passage, text]); return Promise.resolve(null); },
};
window.pywebview = {api: new Proxy(T_API, {get: (t, k) => k in t ? t[k] : () => Promise.resolve(null)})};
</script>
"""
PAGE_TEST = r"""<pre id="tout"></pre><script>
const later = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const R = "See [the shot](file:///C:/x/a%20b/shot.png) or file:///C:/x/a%20b/shot.png.\n\n" +
    "Path `C:\\x\\a b\\shot.png`, C:\\x\\plain.txt and C:\\x\\missing.txt.\n\n- an item [gone](file:///C:/x/gone.png)\n\n" +
    "| file |\n|---|\n| [cell](file:///C:/x/a%20b/shot.png) |";
  reply.show({sid: "s1", name: "x", replies: [{id: "m1", when: "10:00", text: R}], answers: {}, selections: [], note: "",
    pins: [{id: "p1", label: "pinned", when: "09:00", text: "Pinned [old](file:///C:/x/shot-old.png) and `C:\\x\\plain.txt`"}]});
  await later(50);
  const o = {};
  const links = () => [...document.querySelectorAll("#replies a.lnk")].map(a => [a.textContent, a.dataset.url, a.closest("td, .li") ? a.closest("td, .li").tagName + (a.closest(".li") ? ".li" : "") : ""]);
  o.links = links();
  o.cands = document.querySelectorAll("#replies span.cand").length;
  o.asked = T.asked;
  const boxes = () => document.querySelectorAll("#replies .ansrow").length;
  for (const a of document.querySelectorAll("#replies a.lnk")) a.click();
  await later(50);
  o.opened = T.links.slice();
  o.boxesAfterLinks = boxes();
  o.note = [...document.querySelectorAll("#replies .lnote")].map(n => [n.textContent, n.previousElementSibling.textContent]);
  document.querySelector("#replies .lnote").click();
  await later(20);
  o.boxesAfterNote = boxes();
  document.querySelector("#replies .para").click();
  await later(20);
  o.boxesAfterPara = boxes();
  // no spell check in any answer box (a paragraph's here; an area's box is the same boxHtml())
  o.spell = [...document.querySelectorAll("textarea")].map(t => [t.spellcheck, t.getAttribute("autocorrect"), t.getAttribute("autocapitalize")]);
  o.spellArea = boxHtml("area").includes('spellcheck="false"');
  document.activeElement.blur();  // the box opened has the keyboard: nothing is redrawn until it is left
  showView("p1");
  await later(50);
  o.pinned = links();
  document.getElementById("tout").textContent = JSON.stringify(o);
})().catch(e => { document.getElementById("tout").textContent = JSON.stringify({error: String(e && e.stack || e)}); });
</script>
"""


def page_run(test=PAGE_TEST, size="800,600", budget=3000, flags=()):
    with open(os.path.join(ROOT, "agent-reply.html"), encoding="utf-8") as f:
        page = f.read()
    at = page.index("<script>")
    page = page[:at] + PAGE_STUB + page[at:]
    at = page.rindex("</body>")
    page = page[:at] + test + page[at:]
    d = tempfile.mkdtemp()
    try:
        path = os.path.join(d, "page.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(page)
        import subprocess
        r = subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                            "--user-data-dir=" + os.path.join(d, "profile"), "--virtual-time-budget=%d" % budget, "--dump-dom",
                            "--window-size=" + size, "--force-device-scale-factor=1", *flags,
                            "file:///" + path.replace("\\", "/")], capture_output=True, text=True, encoding="utf-8",
                           timeout=90)
        m = re.search(r'<pre id="tout">(.*?)</pre>', r.stdout, re.S)
        if not m or not m.group(1):
            raise AssertionError("the page wrote nothing: " + r.stderr[-2000:])
        import html
        return json.loads(html.unescape(m.group(1)))
    finally:
        shutil.rmtree(d, ignore_errors=True)


@unittest.skipUnless(EDGE, "Microsoft Edge is not installed")
class Page(unittest.TestCase):
    """The links as the reply window draws them and what a click on one does, in the real page."""
    o = None

    @classmethod
    def setUpClass(cls):
        cls.o = page_run()

    def test_no_error(self):
        self.assertNotIn("error", self.o)

    def test_links_show_their_text_and_only_paths_that_exist_become_links(self):
        u = "file:///C:/x/a%20b/shot.png"
        self.assertEqual(self.o["links"], [["the shot", u, ""], [u, u, ""], ["C:\\x\\a b\\shot.png", "C:\\x\\a b\\shot.png", ""],
                                           ["C:\\x\\plain.txt", "C:\\x\\plain.txt", ""],
                                           ["gone", "file:///C:/x/gone.png", "DIV.li"], ["cell", u, "TD"]])
        self.assertEqual(self.o["cands"], 0)  # the missing path is plain text again
        self.assertEqual(sorted(self.o["asked"][0]), ["C:\\x\\a b\\shot.png", "C:\\x\\missing.txt", "C:\\x\\plain.txt"])

    def test_a_click_opens_through_the_panel_and_never_opens_an_answer_box(self):
        self.assertEqual(self.o["opened"], [l[1] for l in self.o["links"]])
        self.assertEqual(self.o["boxesAfterLinks"], 0)
        self.assertEqual(self.o["boxesAfterNote"], 0)
        self.assertEqual(self.o["boxesAfterPara"], 1)  # a click elsewhere in the paragraph still opens its box

    def test_answer_boxes_have_no_spell_check(self):
        self.assertEqual(self.o["spell"], [[False, "off", "off"]])
        self.assertTrue(self.o["spellArea"])

    def test_a_missing_file_shows_one_short_note_at_its_link(self):
        self.assertEqual(self.o["note"], [["File not found", "gone"]])

    def test_links_work_in_a_pinned_reply(self):
        self.assertEqual(self.o["pinned"], [["old", "file:///C:/x/shot-old.png", ""], ["C:\\x\\plain.txt", "C:\\x\\plain.txt", ""]])


# The area in the real page (headless Edge, events dispatched to the page, never to a window): a drag from the margin
# across two replies shows the rectangle and tints the blocks it touches while it lasts; let go, the rectangle and the
# tint go at once, the panel gets their markdown (once), and a quiet "Copied" shows at the blocks' corner for about a
# second (no Copy button, no corner handles); Esc while drawing drops the drag and copies nothing; one block alone
# opens its own box and no frame; a click on a paragraph still opens its box (the empty box the drag opened closes);
# a drag that starts on text draws the rectangle too (one paragraph: its own box, copied).
AREA_TEST = r"""<pre id="tout"></pre><script>
const later = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const R1 = "Intro line.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```js\nconst a = 1;\n```";
  reply.show({sid: "s1", name: "x", replies: [{id: "m1", when: "10:00", text: R1}, {id: "m2", when: "10:01", text: "Second reply.\n\nLast."}],
    answers: {}, selections: [], note: "", pins: []});
  await later(50);
  const ev = (el, type, x, y, o) => el.dispatchEvent(new (type.startsWith("pointer") ? PointerEvent : MouseEvent)(type,
    Object.assign({bubbles: true, cancelable: true, composed: true, button: 0, buttons: type === "pointerup" ? 0 : 1, pointerId: 1,
      pointerType: "mouse", isPrimary: true, clientX: x, clientY: y}, o)));
  const drag = async (el, x0, y0, x1, y1, mid) => {
    ev(el, "pointerdown", x0, y0); ev(el, "mousedown", x0, y0);
    for (let k = 1; k <= 5; k++) ev(el, "pointermove", x0 + (x1 - x0) * k / 5, y0 + (y1 - y0) * k / 5);
    if (mid) mid();
    ev(el, "pointerup", x1, y1); ev(el, "mouseup", x1, y1); ev(el, "click", x1, y1);
    await later(30);
  };
  const box = s => document.querySelector(s).getBoundingClientRect();
  const tinted = () => [...document.querySelectorAll(".inarea")].map(e => e.closest(".reply").dataset.id + "#" + e.dataset.n);
  const o = {}, body = document.getElementById("body"), area = document.getElementById("area"), nt = document.getElementById("areaNote");
  o.button = !!document.getElementById("areaCopy");
  o.handles = area.children.length;
  const tbl = box("#replies .para.table"), second = [...document.querySelectorAll("#replies .reply")][1].querySelector(".para").getBoundingClientRect();
  await drag(body, box("#body").left + 4, tbl.top + 2, 300, second.top + 4, () => {
    o.during = {shown: !area.hidden, tinted: tinted(), copied: (T.area || []).length, note: !nt.hidden};
  });
  const els = ["m1#1", "m1#2", "m2#0"].map(k => document.querySelector(`.reply[data-id="${k.split("#")[0]}"] [data-n="${k.split("#")[1]}"]`).getBoundingClientRect());
  const top = Math.min(...els.map(q => q.top)), right = Math.max(...els.map(q => q.right)), nq = nt.getBoundingClientRect();
  o.released = {shown: !area.hidden, tinted: tinted(), boxes: document.querySelectorAll(".ansrow").length, frames: document.querySelectorAll(".agroup").length,
    sel: getSelection().toString(), copied: (T.area || []).slice(),
    note: {shown: !nt.hidden, text: nt.textContent, button: nt.tagName === "BUTTON" || nt.classList.contains("b"),
      onEdge: Math.abs(nq.top + nq.height / 2 - (top - 4)) <= 1, atRight: nq.right <= right + 10 && nq.right >= right - 20}};
  await later(1200);
  o.noteGone = nt.hidden;
  // Esc while drawing: the drag is dropped, nothing copied
  const n1 = T.area.length;
  await drag(body, box("#body").left + 4, tbl.top + 2, 300, tbl.bottom, () =>
    document.body.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true, cancelable: true})));
  o.esc = [area.hidden, nt.hidden, tinted().length, T.area.length - n1, document.querySelectorAll(".ansrow").length];
  // one block alone: its own box, no frame, still copied, no rectangle left
  await drag(body, box("#body").left + 4, tbl.top + 2, 300, tbl.bottom);
  o.lone = [document.activeElement.closest(".ansrow") && document.activeElement.closest(".ansrow").dataset.for, document.querySelectorAll(".agroup").length,
    area.hidden, tinted().length, T.area.slice(n1), !nt.hidden];
  // a click on the first paragraph: the empty box the drag opened closes, the paragraph's own box opens
  const p1 = document.querySelector("#replies .para"), q = p1.getBoundingClientRect();
  ev(p1, "pointerdown", q.left + 3, q.top + q.height / 2); document.activeElement.blur();  // what a real press does
  ev(p1, "pointerup", q.left + 3, q.top + q.height / 2); ev(p1, "click", q.left + 3, q.top + q.height / 2);
  await later(20);
  o.click = [...document.querySelectorAll(".ansrow")].map(r => r.dataset.for);
  document.activeElement.blur();
  const t = document.querySelector("#replies .reply:nth-child(2) .para"), tq = t.getBoundingClientRect(), n0 = document.querySelectorAll(".ansrow").length;
  const nCopies = T.area.length;
  await drag(t, tq.left + 2, tq.top + tq.height / 2, tq.left + 60, tq.top + tq.height / 2);
  o.textDrag = [area.hidden, [...document.querySelectorAll(".ansrow")].length - n0, T.area.slice(nCopies),
    document.activeElement.closest(".ansrow") && document.activeElement.closest(".ansrow").dataset.for];
  document.getElementById("tout").textContent = JSON.stringify(o);
})().catch(e => { document.getElementById("tout").textContent = JSON.stringify({error: String(e && e.stack || e)}); });
</script>
"""


@unittest.skipUnless(EDGE, "Microsoft Edge is not installed")
class AreaPage(unittest.TestCase):
    o = None

    @classmethod
    def setUpClass(cls):
        cls.o = page_run(AREA_TEST, budget=6000)

    def test_no_error(self):
        self.assertNotIn("error", self.o)

    def test_while_drawn_the_rectangle_shows_and_tints_what_it_touches(self):
        d = self.o["during"]
        self.assertTrue(d["shown"])
        self.assertEqual(d["tinted"], ["m1#1", "m1#2", "m2#0"])  # the table, the code block, the second reply's first
        self.assertEqual((d["copied"], d["note"]), (0, False))  # nothing copied before the release
        self.assertEqual(self.o["handles"], 0)  # no corner or edge handles

    def test_let_go_the_rectangle_and_the_tint_go_and_the_blocks_are_copied_once(self):
        d = self.o["released"]
        self.assertEqual((d["shown"], d["tinted"]), (False, []))
        self.assertEqual((d["boxes"], d["frames"], d["sel"]), (0, 0, ""))  # two replies: copied only, no box
        self.assertEqual(d["copied"], ["| a | b |\n|---|---|\n| 1 | 2 |\n\n```js\nconst a = 1;\n```\n\nSecond reply."])

    def test_a_quiet_copied_shows_at_the_corner_for_about_a_second(self):
        n = self.o["released"]["note"]
        self.assertEqual(n, {"shown": True, "text": "Copied", "button": False, "onEdge": True, "atRight": True})
        self.assertTrue(self.o["noteGone"])
        self.assertFalse(self.o["button"])

    def test_esc_while_drawing_drops_it_and_copies_nothing(self):
        self.assertEqual(self.o["esc"], [True, True, 0, 0, 0])

    def test_one_block_alone_opens_its_own_box_and_no_frame(self):
        self.assertEqual(self.o["lone"], ["m1#1", 0, True, 0, ["| a | b |\n|---|---|\n| 1 | 2 |"], True])

    def test_a_click_opens_its_box_and_a_drag_from_text_draws_the_area(self):
        self.assertEqual(self.o["click"], ["m1#0"])  # the empty box the drag opened closed; the clicked one opened
        # the rectangle gone on release, the paragraph's own box opened with the caret, its text copied
        self.assertEqual(self.o["textDrag"], [True, 1, ["Second reply."], "m2#0"])


# What a release copied stays tinted (headless Edge, events dispatched to the page): the blocks keep the drag's tint
# for AREA_TINT_MS 1.5 s, then fade over AREA_FADE_MS 300 ms; for one block (no frame), for several (the frame stays,
# the tint inside it fades) and across two replies (copied only). A new drag drops it at once. With reduced motion
# the tint goes after the same time, with no fade.
TINT_TEST = r"""<pre id="tout"></pre><script>
const later = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  reply.show({sid: "s1", name: "x", replies: [{id: "m1", when: "10:00", text: "Intro.\n\n- one\n- two\n- three\n\n```js\nx\n```"},
    {id: "m2", when: "10:01", text: "Second reply."}], answers: {}, selections: [], note: "", pins: []});
  await later(50);
  const ev = (el, type, x, y) => el.dispatchEvent(new (type.startsWith("pointer") ? PointerEvent : MouseEvent)(type,
    {bubbles: true, cancelable: true, composed: true, button: 0, buttons: type === "pointerup" ? 0 : 1, pointerId: 1,
     pointerType: "mouse", isPrimary: true, clientX: x, clientY: y, detail: 1}));
  const body = document.getElementById("body"), left = body.getBoundingClientRect().left + 4;
  const B = (r, n) => document.querySelector(`.reply[data-id="${r}"] [data-n="${n}"]`).getBoundingClientRect();
  const drag = (y0, y1) => {
    ev(body, "pointerdown", left, y0); ev(body, "mousedown", left, y0);
    for (let k = 1; k <= 5; k++) ev(body, "pointermove", left + (400 - left) * k / 5, y0 + (y1 - y0) * k / 5);
    ev(body, "pointerup", 400, y1); ev(body, "mouseup", 400, y1); ev(body, "click", 400, y1);
    return performance.now();
  };
  const ids = s => [...document.querySelectorAll(s)].map(e => e.closest(".reply").dataset.id + "#" + e.dataset.n);
  const st = () => ({copied: ids(".copied"), fading: ids(".fading"), inarea: document.querySelectorAll(".inarea").length,
    frames: document.querySelectorAll(".agroup").length});
  const el = (r, n) => document.querySelector(`.reply[data-id="${r}"] [data-n="${n}"]`);
  const bg = (r, n) => getComputedStyle(el(r, n)).backgroundColor;
  const o = {rest: bg("m1", 2)};
  const at = (t0, ms) => later(Math.max(0, ms - (performance.now() - t0)));
  // one block: its own box, no frame, tinted
  let t0 = drag(B("m1", 2).top + 4, B("m1", 2).top + 8);
  o.one = st(); o.oneBg = bg("m1", 2);
  await at(t0, 1000); o.one1 = st();
  await at(t0, 1600); o.one16 = st(); o.oneFade = getComputedStyle(el("m1", 2)).transitionDuration; o.one16Bg = bg("m1", 2);
  await at(t0, 2000); o.one20 = st(); o.oneAfter = bg("m1", 2);
  document.activeElement.blur();
  await later(30);
  // several blocks: one frame, every block in it tinted, the frame stays when the tint has gone
  t0 = drag(B("m1", 1).top + 4, B("m1", 4).top + 6);
  o.many = st();
  await at(t0, 1600); o.many16 = st();
  await at(t0, 2000); o.many20 = st();
  document.activeElement.blur();
  await later(30);
  // two replies: copied only, tinted all the same; a new drag drops the tint at once
  drag(B("m1", 4).top + 4, B("m2", 0).top + 6);
  o.two = st();
  ev(body, "pointerdown", left, B("m1", 0).top + 4); ev(body, "mousedown", left, B("m1", 0).top + 4);
  ev(body, "pointermove", 400, B("m1", 0).top + 12);
  o.redrag = st();
  ev(body, "pointerup", 400, B("m1", 0).top + 12);
  o.copies = (T.area || []).length;
  document.getElementById("tout").textContent = JSON.stringify(o);
})().catch(e => { document.getElementById("tout").textContent = JSON.stringify({error: String(e && e.stack || e)}); });
</script>
"""


@unittest.skipUnless(EDGE, "Microsoft Edge is not installed")
class TintPage(unittest.TestCase):
    """What a release copied keeps the drag's tint for a moment, so the user sees what went on the clipboard."""
    o = r = None
    TINT = "rgba(27, 77, 245, 0.09)"

    @classmethod
    def setUpClass(cls):
        cls.o = page_run(TINT_TEST, budget=9000)
        cls.r = page_run(TINT_TEST, budget=9000, flags=("--force-prefers-reduced-motion",))

    def test_no_error(self):
        self.assertNotIn("error", self.o)
        self.assertNotIn("error", self.r)

    def test_one_block_stays_tinted_then_fades(self):
        o = self.o
        self.assertEqual(o["one"], {"copied": ["m1#2"], "fading": [], "inarea": 0, "frames": 0})
        self.assertEqual(o["oneBg"], self.TINT)  # the drag's own tint
        self.assertEqual(o["one1"]["copied"], ["m1#2"])  # still there after a second
        self.assertEqual((o["one16"]["copied"], o["one16"]["fading"], o["oneFade"]), ([], ["m1#2"], "0.3s, 0.3s"))  # background and border
        # gone after 1.8 s (the colour itself is checked under reduced motion: headless --dump-dom never runs a
        # transition, so here it would read the tint still; the fade itself was seen in a rendered browser)
        self.assertEqual((o["one20"]["copied"], o["one20"]["fading"]), ([], []))

    def test_several_blocks_in_a_frame_the_tint_fades_the_frame_stays(self):
        o = self.o
        self.assertEqual(o["many"], {"copied": ["m1#1", "m1#2", "m1#3", "m1#4"], "fading": [], "inarea": 0, "frames": 1})
        self.assertEqual((o["many16"]["copied"], o["many16"]["fading"]), ([], ["m1#1", "m1#2", "m1#3", "m1#4"]))
        self.assertEqual((o["many20"]["copied"], o["many20"]["fading"], o["many20"]["frames"]), ([], [], 1))

    def test_two_replies_are_tinted_and_a_new_drag_drops_it(self):
        self.assertEqual(self.o["two"]["copied"], ["m1#4", "m2#0"])
        self.assertEqual(self.o["redrag"]["copied"], [])
        self.assertEqual(self.o["copies"], 4)  # one per release, the last drag too

    def test_reduced_motion_the_tint_goes_after_the_same_time_without_a_fade(self):
        r = self.r
        self.assertEqual(r["one1"]["copied"], ["m1#2"])
        self.assertEqual((r["oneBg"], r["oneFade"]), (self.TINT, "0s"))
        self.assertEqual(r["one16Bg"], r["rest"])  # gone at 1.5 s, at once
        self.assertEqual(r["one20"]["copied"] + r["one20"]["fading"], [])


# An area answered as a whole, in the real page (headless Edge, events dispatched to the page): a drag across a
# heading and three items frames them and opens one box under the last, caret in it; the answer typed there is kept
# as a draft under the area's key, counted, survives a redraw, and goes to Send to Session after the last item's own
# answer; a drag over part of it reopens that area; one reaching past it extends it (the answer moves along); emptied
# and left, it goes with its frame.
AREA_ANSWER_TEST = r"""<pre id="tout"></pre><script>
const later = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const R = "Intro.\n\n## 1. İki bölümü kaldırmak\n\n1. Birinci madde\n2. İkinci madde\n3. Üçüncü madde\n\nAfter.";
  const st = (answers, extra) => ({sid: "s1", name: "x", replies: [{id: "m1", when: "10:00", text: R}].concat(extra || []), answers, selections: [], note: "", pins: []});
  reply.show(st({}));
  await later(50);
  const ev = (el, type, x, y) => el.dispatchEvent(new (type.startsWith("pointer") ? PointerEvent : MouseEvent)(type,
    {bubbles: true, cancelable: true, composed: true, button: 0, buttons: type === "pointerup" ? 0 : 1, pointerId: 1,
     pointerType: "mouse", isPrimary: true, clientX: x, clientY: y}));
  const drag = async (x0, y0, x1, y1) => {
    const el = document.getElementById("body");
    ev(el, "pointerdown", x0, y0); ev(el, "mousedown", x0, y0);
    for (let k = 1; k <= 5; k++) ev(el, "pointermove", x0 + (x1 - x0) * k / 5, y0 + (y1 - y0) * k / 5);
    ev(el, "pointerup", x1, y1); ev(el, "mouseup", x1, y1); ev(el, "click", x1, y1);
    await later(30);
  };
  const B = n => document.querySelector(`#replies [data-n="${n}"]`).getBoundingClientRect(), left = document.getElementById("body").getBoundingClientRect().left + 4;
  const frame = () => [...document.querySelectorAll(".agroup")].map(g => [...g.querySelectorAll("[data-n]")].map(e => +e.dataset.n));
  const typeIn = (t, v) => { t.value = v; t.dispatchEvent(new Event("input", {bubbles: true})); };
  const key = (t, k) => t.dispatchEvent(new KeyboardEvent("keydown", {key: k, bubbles: true, cancelable: true}));
  const o = {};
  await drag(left, B(1).top + 4, 400, B(4).top + 6);
  const t = document.activeElement, nt = document.getElementById("areaNote");
  o.opened = {focus: t.closest(".ansrow") && t.closest(".ansrow").dataset.for, isArea: t.closest(".ansrow").classList.contains("area"),
    frame: frame(), last: t.closest(".ansrow").previousElementSibling.dataset.n, ph: t.placeholder, note: !nt.hidden,
    noteAbove: Math.abs(nt.getBoundingClientRect().top + nt.offsetHeight / 2 - (document.querySelector(".agroup").getBoundingClientRect().top - 4)) <= 1, copied: (T.area || []).length,
    caret: [t.selectionStart, t.selectionEnd, t.value.length]};
  typeIn(t, "Onaylıyorum");
  await later(400);
  o.typed = {count: document.getElementById("count").textContent, draft: T.drafts.slice(-1)[0]};
  key(t, "Enter");
  await later(20);
  o.left = [document.activeElement === document.body, frame(), document.querySelectorAll(".ansrow.area").length];
  document.body.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true, cancelable: true}));
  await later(20);
  const it3 = document.querySelector('#replies [data-n="4"]'), q = it3.getBoundingClientRect();
  ev(it3, "pointerdown", q.left + 40, q.top + q.height / 2); ev(it3, "pointerup", q.left + 40, q.top + q.height / 2); ev(it3, "click", q.left + 40, q.top + q.height / 2);
  await later(20);
  typeIn(document.activeElement, "Only this one needs a test.");
  document.activeElement.blur();
  await later(400);
  o.both = {count: document.getElementById("count").textContent, rows: [...document.querySelectorAll("#replies .ansrow")].map(r => r.dataset.for), frame: frame()};
  document.getElementById("send").click();
  await later(30);
  o.sent = T.sends[0][1];
  // a redraw (a reply more): the frame, its answer and the item's own come back from the drafts
  const kept = {};
  for (const [, k, passage, text] of T.drafts) { if (text) kept[k] = {text, passage}; else delete kept[k]; }
  reply.show(st(kept, [{id: "m2", when: "10:05", text: "Another reply."}]));
  await later(50);
  o.redrawn = {frame: frame(), area: document.querySelector('.ansrow.area .ans').value, own: document.querySelector('.ansrow[data-for="m1#4"] .ans').value,
    count: document.getElementById("count").textContent};
  // a drag over one item inside it: that area again, its answer in the box, no second frame
  await drag(left, B(3).top + 4, 400, B(3).top + 8);
  o.inside = [document.activeElement.closest(".ansrow").dataset.for, document.activeElement.value, frame()];
  document.activeElement.blur();
  // a drag from the last item down to "After.": the area grows to hold both, its answer goes along
  T.drafts = [];
  await drag(left, B(4).top + 4, 400, B(5).top + 6);
  o.extended = [document.activeElement.closest(".ansrow").dataset.for, document.activeElement.value, frame(), T.drafts.map(d => [d[1], d[3]])];
  // emptied and left: the area goes with its frame; the item's own answer stays
  typeIn(document.activeElement, "");
  document.activeElement.blur();
  await later(400);
  o.emptied = [frame(), document.querySelectorAll(".ansrow.area").length, document.getElementById("count").textContent];
  document.getElementById("tout").textContent = JSON.stringify(o);
})().catch(e => { document.getElementById("tout").textContent = JSON.stringify({error: String(e && e.stack || e)}); });
</script>
"""


@unittest.skipUnless(EDGE, "Microsoft Edge is not installed")
class AreaAnswerPage(unittest.TestCase):
    o = None

    @classmethod
    def setUpClass(cls):
        cls.o = page_run(AREA_ANSWER_TEST, budget=6000)

    def test_no_error(self):
        self.assertNotIn("error", self.o)

    def test_a_drag_frames_the_blocks_copies_them_and_opens_one_box_under_the_last(self):
        o = self.o["opened"]
        self.assertEqual((o["focus"], o["isArea"], o["frame"], o["last"]), ("m1#1-4", True, [[1, 2, 3, 4]], "4"))
        self.assertEqual(o["ph"], "Type your answer, or hold Space to speak…")
        self.assertEqual(o["copied"], 1)  # copied at once; the box keeps the keyboard and the caret
        self.assertEqual(o["caret"], [0, 0, 0])
        self.assertTrue(o["note"] and o["noteAbove"])  # "Copied" on the frame's top edge, so it covers no answer

    def test_the_answer_is_a_draft_under_the_areas_key_and_counted(self):
        sid, key, passage, text = self.o["typed"]["draft"]
        self.assertEqual((sid, key, text), ("s1", "m1#1-4", "Onaylıyorum"))
        self.assertEqual(passage, "## 1. İki bölümü kaldırmak\n\n1. Birinci madde\n2. İkinci madde\n3. Üçüncü madde")
        self.assertEqual(self.o["typed"]["count"], "1 answer")
        self.assertEqual(self.o["left"], [True, [[1, 2, 3, 4]], 1])  # Enter leaves it; the frame stays

    def test_an_items_own_answer_inside_is_kept_beside_it(self):
        b = self.o["both"]
        self.assertEqual(b["rows"], ["m1#4", "m1#1-4"])
        self.assertEqual((b["count"], b["frame"]), ("2 answers", [[1, 2, 3, 4]]))
        self.assertEqual([(a["key"], a["line"], a["text"]) for a in self.o["sent"]],
                         [("m1#4", 6, "Only this one needs a test."), ("m1#1-4", 6, "Onaylıyorum")])

    def test_it_comes_back_after_a_redraw(self):
        r = self.o["redrawn"]
        self.assertEqual((r["frame"], r["area"], r["own"], r["count"]),
                         ([[1, 2, 3, 4]], "Onaylıyorum", "Only this one needs a test.", "2 answers · sent"))  # what was sent, unchanged

    def test_a_drag_inside_reopens_it_and_one_past_it_extends_it(self):
        self.assertEqual(self.o["inside"], ["m1#1-4", "Onaylıyorum", [[1, 2, 3, 4]]])
        key, text, frame, drafts = self.o["extended"]
        self.assertEqual((key, text, frame), ("m1#1-5", "Onaylıyorum", [[1, 2, 3, 4, 5]]))
        self.assertEqual(drafts, [["m1#1-4", ""], ["m1#1-5", "Onaylıyorum"]])  # the old key's draft goes, the new one holds it

    def test_emptied_and_left_it_goes_with_its_frame(self):
        self.assertEqual(self.o["emptied"], [[], 0, "1 answer"])

    def test_send_to_session_quotes_the_area_as_one_with_the_answer_under_it(self):
        P = panel_methods("seen", "reply_text")
        p = P()
        p.reply_mod, p.reply_seen = ar, {}
        p.seen("s1", "m1", "Intro.\n\n## 1. İki bölümü kaldırmak\n\n1. Birinci madde\n2. İkinci madde\n3. Üçüncü madde\n\nAfter.", None)
        self.assertEqual(p.reply_text("s1", self.o["sent"]),
                         'My answers inside your reply (your reply is quoted with "> ", my answers start with "My answer"):\n\n'
                         "> Intro.\n>\n> ## 1. İki bölümü kaldırmak\n>\n> 1. Birinci madde\n> 2. İkinci madde\n> 3. Üçüncü madde\n\n"
                         'My answer to "3. Üçüncü madde": Only this one needs a test.\n\n'
                         'My answer to all 4 blocks from "## 1. İki bölümü kaldırmak" down to here, as a whole: Onaylıyorum\n\n'
                         "> After.")


# A second drag while the first area's empty box has the keyboard (headless Edge): the press on blank space leaves
# that box, so it goes with its frame without an error (Chromium fires focusout while a box is being removed), the new
# area gets its own box with the caret, frames never nest, and each release copies once.
AREA_REDRAG_TEST = r"""<pre id="tout"></pre><script>
const later = ms => new Promise(r => setTimeout(r, ms));
const errs = [];
addEventListener("error", e => errs.push(e.message));
(async () => {
  const R = "Intro.\n\n## Plan\n\n1. One\n2. Two\n3. Three\n\nAfter.";
  reply.show({sid: "s1", name: "x", replies: [{id: "m1", when: "10:00", text: R}], answers: {}, selections: [], note: "", pins: []});
  await later(50);
  const ev = (el, type, x, y) => el.dispatchEvent(new (type.startsWith("pointer") ? PointerEvent : MouseEvent)(type,
    {bubbles: true, cancelable: true, composed: true, button: 0, buttons: type === "pointerup" ? 0 : 1, pointerId: 1,
     pointerType: "mouse", isPrimary: true, clientX: x, clientY: y}));
  const drag = async (el, x0, y0, x1, y1) => {
    ev(el, "pointerdown", x0, y0); ev(el, "mousedown", x0, y0);
    for (let k = 1; k <= 5; k++) ev(el, "pointermove", x0 + (x1 - x0) * k / 5, y0 + (y1 - y0) * k / 5);
    ev(el, "pointerup", x1, y1); ev(el, "mouseup", x1, y1); ev(el, "click", x1, y1);
    await later(30);
  };
  const B = n => document.querySelector(`#replies [data-n="${n}"]`).getBoundingClientRect(), left = document.getElementById("body").getBoundingClientRect().left + 4;
  const frame = () => [...document.querySelectorAll(".agroup")].map(g => [...g.querySelectorAll("[data-n]")].map(e => +e.dataset.n));
  const body = document.getElementById("body"), o = {};
  await drag(body, left, B(1).top + 4, 400, B(3).top + 6);
  o.first = [document.activeElement.closest(".ansrow").dataset.for, frame(), document.getElementById("area").hidden];
  // the press leaves the empty box, which goes and lifts what stood under it: the end is measured after the press
  const y0 = B(1).top + 4;
  ev(body, "pointerdown", left, y0); ev(body, "mousedown", left, y0);
  await later(10);
  const y1 = B(4).top + 8;
  for (let k = 1; k <= 5; k++) ev(body, "pointermove", left + (400 - left) * k / 5, y0 + (y1 - y0) * k / 5);
  ev(body, "pointerup", 400, y1); ev(body, "mouseup", 400, y1); ev(body, "click", 400, y1);
  await later(30);
  const t = document.activeElement;
  o.second = [t.closest(".ansrow") && t.closest(".ansrow").dataset.for, frame(), document.querySelectorAll(".ansrow").length, t.selectionStart,
    document.querySelectorAll(".agroup .agroup").length];
  o.copied = T.area;
  o.errs = errs;
  document.getElementById("tout").textContent = JSON.stringify(o);
})().catch(e => { document.getElementById("tout").textContent = JSON.stringify({error: String(e && e.stack || e)}); });
</script>
"""


@unittest.skipUnless(EDGE, "Microsoft Edge is not installed")
class AreaRedragPage(unittest.TestCase):
    o = None

    @classmethod
    def setUpClass(cls):
        cls.o = page_run(AREA_REDRAG_TEST)

    def test_a_new_drag_with_the_empty_box_focused_replaces_the_area_and_copies_again(self):
        self.assertNotIn("error", self.o)
        self.assertEqual(self.o["errs"], [])
        self.assertEqual(self.o["first"], ["m1#1-3", [[1, 2, 3]], True])
        self.assertEqual(self.o["second"], ["m1#1-4", [[1, 2, 3, 4]], 1, 0, 0])
        self.assertEqual(self.o["copied"], ["## Plan\n\n1. One\n2. Two", "## Plan\n\n1. One\n2. Two\n3. Three"])


# The area starts anywhere (headless Edge, events dispatched to the page): a press on text takes no text selection
# (its mousedown is prevented) unless it is a double or triple click's second or third; a drag from the middle of a
# sentence across three blocks frames them; a click on a paragraph under that frame opens its box although leaving the
# empty area box moves the page under the pointer; a link opens on a click and a drag from it draws; a drag in an
# answer box or on a code block's own scroll bar draws nothing; the rubber band and the frame have rounded corners.
AREA_ANYWHERE_TEST = r"""<pre id="tout"></pre><script>
const later = ms => new Promise(r => setTimeout(r, ms));
const errs = [];
addEventListener("error", e => errs.push(e.message));
(async () => {
  const R = "The spend per job this week came out higher than planned, mostly from the scoring run.\n\n1. Cut the trend job\n2. Move scoring\n3. Keep the report\n\n" +
    "```powershell\nGet-ChildItem -Recurse -Filter *.json | Where-Object { $_.Length -gt 100000 } | Sort-Object Length -Descending | Select-Object -First 20 FullName, Length\n```\n\nSee [the report](https://ex.com/r) now.";
  reply.show({sid: "s1", name: "x", replies: [{id: "m1", when: "10:00", text: R}], answers: {}, selections: [], note: "", pins: []});
  await later(50);
  const mk = (type, x, y, o) => new (type.startsWith("pointer") ? PointerEvent : MouseEvent)(type, Object.assign({bubbles: true,
    cancelable: true, composed: true, button: 0, buttons: type === "pointerup" ? 0 : 1, pointerId: 1, pointerType: "mouse",
    isPrimary: true, clientX: x, clientY: y, detail: type.startsWith("pointer") ? 0 : 1}, o));
  const ev = (el, type, x, y, o) => { const e = mk(type, x, y, o); el.dispatchEvent(e); return e; };
  const moves = (el, x0, y0, x1, y1) => { for (let k = 1; k <= 5; k++) ev(el, "pointermove", x0 + (x1 - x0) * k / 5, y0 + (y1 - y0) * k / 5); };
  const B = n => document.querySelector(`#replies [data-n="${n}"]`).getBoundingClientRect();
  const area = document.getElementById("area"), o = {};
  const frame = () => [...document.querySelectorAll(".agroup")].map(g => [...g.querySelectorAll("[data-n]")].map(e => +e.dataset.n));
  const rows = () => [...document.querySelectorAll(".ansrow")].map(r => r.dataset.for);
  // a press on text: its mousedown is prevented (no text selection), the second press of a double click is not
  const p0 = document.querySelector('#replies [data-n="0"]'), q0 = B(0), x = q0.left + 200, y = q0.top + q0.height / 2;
  ev(p0, "pointerdown", x, y);
  o.single = ev(p0, "mousedown", x, y).defaultPrevented;
  ev(p0, "pointerup", x, y); ev(p0, "mouseup", x, y);
  ev(p0, "pointerdown", x, y);
  o.double = ev(p0, "mousedown", x, y, {detail: 2}).defaultPrevented;
  moves(p0, x, y, x + 60, y + 40);
  o.doubleDraws = !area.hidden;
  ev(p0, "pointerup", x + 60, y + 40);
  // a drag from the middle of the sentence down to the second item: three blocks framed, the rubber band rounded
  ev(p0, "pointerdown", x, y); ev(p0, "mousedown", x, y);
  moves(p0, x, y, x + 120, B(2).top + 8);
  o.band = [!area.hidden, getComputedStyle(area).borderRadius, getSelection().toString()];
  ev(p0, "pointerup", x + 120, B(2).top + 8); ev(p0, "mouseup", x + 120, B(2).top + 8); ev(p0, "click", x + 120, B(2).top + 8);
  await later(30);
  const g = document.querySelector(".agroup");
  o.framed = [frame(), g && getComputedStyle(g, "::before").borderRadius, rows(), T.area.length];
  // a drag inside the area's open box draws nothing
  const t = document.activeElement, tq = t.getBoundingClientRect();
  ev(t, "pointerdown", tq.left + 10, tq.top + 10); ev(t, "mousedown", tq.left + 10, tq.top + 10);
  moves(t, tq.left + 10, tq.top + 10, tq.left + 200, tq.top + 60);
  o.boxDrag = [!area.hidden, document.activeElement === t];
  ev(t, "pointerup", tq.left + 200, tq.top + 60);
  // a click on the third item, under the frame: leaving the empty area box lifts it, the click lands on what holds
  // both, and still opens the item's own box
  const it = document.querySelector('#replies [data-n="3"]'), iq = B(3), ix = iq.left + 60, iy = iq.top + iq.height / 2;
  ev(it, "pointerdown", ix, iy); ev(it, "mousedown", ix, iy);
  await later(10);
  o.lifted = B(3).top < iy - iq.height / 2 - 1;
  const under = document.elementFromPoint(ix, iy), common = under && under.contains(it) ? under : document.getElementById("replies");
  ev(under || document.body, "pointerup", ix, iy); ev(common, "click", ix, iy);
  await later(20);
  o.shiftClick = [rows(), frame()];
  document.activeElement.blur();
  // a link: a click opens it; a drag from it draws the area and opens nothing more
  const a = document.querySelector("#replies a.lnk"), aq = a.getBoundingClientRect(), ax = aq.left + 5, ay = aq.top + aq.height / 2;
  ev(a, "pointerdown", ax, ay); ev(a, "mousedown", ax, ay); ev(a, "pointerup", ax, ay); ev(a, "mouseup", ax, ay); ev(a, "click", ax, ay);
  await later(20);
  o.linkClick = T.links.slice();
  ev(a, "pointerdown", ax, ay); ev(a, "mousedown", ax, ay);
  moves(a, ax, ay, ax + 80, ay - 60);
  o.linkDrag = [!area.hidden];
  ev(a, "pointerup", ax + 80, ay - 60); ev(a, "mouseup", ax + 80, ay - 60); ev(a, "click", ax + 80, ay - 60);
  await later(20);
  o.linkDrag.push(T.links.length);
  document.activeElement.blur();
  // the code block's own sideways scroll bar: a press there draws nothing; on its code it draws
  const pre = document.querySelector("#replies .code pre"), pq = pre.getBoundingClientRect();
  o.bar = pre.offsetHeight - pre.clientHeight;
  const by = pq.top + pre.clientTop + pre.clientHeight + 2;
  ev(pre, "pointerdown", pq.left + 40, by); ev(pre, "mousedown", pq.left + 40, by);
  moves(pre, pq.left + 40, by, pq.left + 240, by);
  o.barDraws = !area.hidden;
  ev(pre, "pointerup", pq.left + 240, by);
  ev(pre, "pointerdown", pq.left + 40, pq.top + 8); ev(pre, "mousedown", pq.left + 40, pq.top + 8);
  moves(pre, pq.left + 40, pq.top + 8, pq.left + 240, pq.top + 20);
  o.codeDraws = !area.hidden;
  ev(pre, "pointerup", pq.left + 240, pq.top + 20);
  o.errs = errs;
  document.getElementById("tout").textContent = JSON.stringify(o);
})().catch(e => { document.getElementById("tout").textContent = JSON.stringify({error: String(e && e.stack || e)}); });
</script>
"""


@unittest.skipUnless(EDGE, "Microsoft Edge is not installed")
class AreaAnywherePage(unittest.TestCase):
    o = None

    @classmethod
    def setUpClass(cls):
        cls.o = page_run(AREA_ANYWHERE_TEST, budget=5000)

    def test_no_error(self):
        self.assertNotIn("error", self.o)
        self.assertEqual(self.o["errs"], [])

    def test_a_press_on_text_takes_no_selection_but_a_double_click_does(self):
        self.assertEqual((self.o["single"], self.o["double"], self.o["doubleDraws"]), (True, False, False))

    def test_a_drag_from_the_middle_of_a_sentence_frames_three_blocks_with_round_corners(self):
        self.assertEqual(self.o["band"], [True, "10px", ""])  # the rubber band: --r-ctl, no text selected
        self.assertEqual(self.o["framed"], [[[0, 1, 2]], "10px", ["m1#0-2"], 1])

    def test_a_drag_in_an_answer_box_draws_nothing(self):
        self.assertEqual(self.o["boxDrag"], [False, True])

    def test_a_click_under_the_frame_opens_its_box_although_the_page_moved(self):
        self.assertTrue(self.o["lifted"])
        self.assertEqual(self.o["shiftClick"], [["m1#3"], []])

    def test_a_link_opens_on_a_click_and_a_drag_from_it_draws(self):
        self.assertEqual(self.o["linkClick"], ["https://ex.com/r"])
        self.assertEqual(self.o["linkDrag"], [True, 1])

    def test_a_code_blocks_scroll_bar_scrolls_and_its_code_draws(self):
        self.assertGreater(self.o["bar"], 0)  # the long line gives it a scroll bar
        self.assertEqual((self.o["barDraws"], self.o["codeDraws"]), (False, True))


class AreaAnswerText(unittest.TestCase):
    """How an area's answer reads inside the quoted reply (agent-reply.py lead(), answer_block())."""

    def test_the_size_comes_from_the_key(self):
        self.assertEqual([ar.area_size(b) for b in ("1-4", "3-3", "3", "", "4-1", "x-2")], [4, 1, 0, 0, 0, 0])

    def test_the_lead_names_the_blocks_and_the_first_line(self):
        self.assertEqual(ar.lead("x", 0), 'My answer to "x":')
        self.assertEqual(ar.lead("\n  \n", 0), "My answer:")  # nothing to name
        self.assertEqual(ar.lead("```js\nx\n```", 1), 'My answer to "```js":')  # one block: as a block's own answer
        self.assertEqual(ar.lead("\n## Plan\n\n- a", 2), 'My answer to all 2 blocks from "## Plan" down to here, as a whole:')

    def test_a_blocks_own_lead_names_its_first_line(self):
        item = "- İkinci seçenek: **✨ Find its place**. Daha düz, ama yerini bulmak biraz zaman alır.\n  İkinci satır."
        self.assertEqual(ar.lead(item, 0), 'My answer to "- İkinci seçenek: **✨ Find its place**. Daha düz, ama yerini…":')
        self.assertEqual(len(ar.lead(item, 0)), len('My answer to "":') + ar.NAME_CUT + 1)  # cut at NAME_CUT, then "…"
        self.assertEqual(ar.lead('Name it "Plus" or "Max"?', 0), "My answer to \"Name it 'Plus' or 'Max'?\":")
        self.assertEqual(ar.lead("Reply with :3 to keep it.", 0), 'My answer to "Reply with `:3` to keep it.":')

    def test_the_hook_takes_no_marker_from_a_blocks_own_answer(self):
        text = ar.answer_block("Pick :3 here.\n\nNext.", "", [(0, "Pick :3 here.", "ok")])
        self.assertIn('> Pick :3 here.\n\nMy answer to "Pick `:3` here.": ok\n\n> Next.', text)
        self.assertEqual(load_quote_reply().parse(text, {1, 2, 3}), ([], [], False))
        long = ar.lead('Reply with :1 yes, "quoted" ' + "x" * 80, 3)
        self.assertIn("from \"Reply with `:1` yes, 'quoted' xxx", long)  # no marker for quote-reply.py, no stray quote
        self.assertIn('…" down to here', long)

    def test_an_area_not_found_in_the_reply_goes_last_quoted_whole(self):
        text = ar.answer_block("A\n\nB", "", [(9, "Gone one\n\nGone two", "yes", 2)])
        self.assertEqual(text.split("\n")[2:], ["> A", ">", "> B", "", "> Gone one", ">", "> Gone two", "",
                                                'My answer to all 2 blocks from "Gone one" down to here, as a whole: yes'])

    def test_the_hook_takes_no_marker_from_an_area_answer(self):
        text = ar.answer_block("Reply :1 or :2\n\nSecond.", "", [(2, "Reply :1 or :2\n\nSecond.", "fine :2", 2)])
        self.assertEqual(load_quote_reply().parse(text, {1, 2}), ([], [], False))


class AreaCopy(unittest.TestCase):
    """The area's Copy on the panel's side: the page's text alone on the clipboard (the clipboard is a stand-in)."""

    def api(self):
        with open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ReplyApi")
        keep = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in ("__init__", "copy_area")]
        ns = {}
        exec(compile(ast.Module(body=keep, type_ignores=[]), "agent-panel.pyw", "exec"), ns)
        got = []
        mod = type("M", (), {"set_clipboard_text": staticmethod(lambda t, e=None: got.append(t) or True),
                             "area_copy": staticmethod(ar.area_copy), "area_size": staticmethod(ar.area_size)})
        panel = type("P", (), {"reply_mod": mod})()
        return type("A", (), {"__init__": ns["__init__"], "copy_area": ns["copy_area"]})(panel), got, mod

    def test_the_text_goes_on_the_clipboard_as_it_is(self):
        a, got, _ = self.api()
        self.assertTrue(a.copy_area("| a |\n|---|\n\n```js\nx\n```"))
        self.assertEqual(got, ["| a |\n|---|\n\n```js\nx\n```"])

    def test_nothing_to_copy_or_a_busy_clipboard_is_false(self):
        a, got, mod = self.api()
        self.assertFalse(a.copy_area(""))
        self.assertEqual(got, [])
        mod.set_clipboard_text = staticmethod(lambda t, e=None: False)
        self.assertFalse(a.copy_area("x"))


# The folder in the head's line: a link drawn as the replies' links are; a press released in place opens it (the head
# takes the press for its drag, so the click itself opens nothing more), shows no tab, folds nothing, opens no answer
# box and leaves the keyboard in a box being typed in; a folder that is gone gets the short note at the link.
HEAD_LINK_TEST = r"""<pre id="tout"></pre><script>
const later = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const head = where => ({tabs: [{sid: "s1", name: "gameclub", dot: "open", when: "10:01", on: true, full: "gameclub"},
    {sid: "s2", name: "other", dot: "open", when: "", on: false, full: "other"}],
    meta: {name: "gameclub", where, line: "last reply 10:01, after your prompt of 10:00"}, open: true});
  reply.show({sid: "s1", name: "gameclub", replies: [{id: "m1", when: "10:01", text: "A paragraph."}], answers: {},
    selections: [], note: "", pins: []});
  reply.head(head("C:\\Users\\x\\game club"));
  await later(50);
  const a = () => document.querySelector("#meta a.lnk");
  const press = async el => {
    const r = el.getBoundingClientRect(), at = {bubbles: true, cancelable: true, button: 0, pointerId: 1,
      clientX: r.left + 3, clientY: r.top + 3, screenX: r.left + 3, screenY: r.top + 3};
    el.dispatchEvent(new PointerEvent("pointerdown", at));
    el.dispatchEvent(new PointerEvent("pointerup", at));
    el.dispatchEvent(new MouseEvent("click", at));
    await later(50);
  };
  const o = {link: [a().textContent, a().dataset.url, a().dataset.tip, getComputedStyle(a()).color,
    getComputedStyle(a()).textDecorationLine, getComputedStyle(a()).cursor], meta: document.getElementById("meta").textContent};
  document.querySelector("#replies .para").click();
  await later(20);
  const box = document.querySelector("#replies .ans");
  box.focus();
  await press(a());
  o.opened = T.links.slice();
  o.tabs = T.tabs.slice();
  o.folds = T.folds;
  o.boxes = document.querySelectorAll("#replies .ansrow").length;
  o.focusKept = document.activeElement === box;
  o.shown = document.querySelector(".tab.on").dataset.sid;
  box.blur();
  reply.head(head("C:\\Users\\x\\gone"));
  await later(20);
  await press(a());
  o.note = [...document.querySelectorAll(".lnote")].map(n => [n.textContent, n.previousElementSibling.textContent, n.parentElement.id]);
  o.opened2 = T.links.slice();
  document.getElementById("tout").textContent = JSON.stringify(o);
})().catch(e => { document.getElementById("tout").textContent = JSON.stringify({error: String(e && e.stack || e)}); });
</script>
"""


# Copy and Send to Session: never off; a send keeps the answers in their boxes, a second press pastes the same again;
# the bar says "sent" while the answers are what was pasted; with no answer the replies shown go as they are.
SEND_TEST = r"""<pre id="tout"></pre><script>
const later = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  reply.show({sid: "s1", name: "gameclub", replies: [{id: "m1", when: "10:01", text: "First.\n\nSecond?"}], answers: {},
    selections: [], note: "", pins: []});
  await later(50);
  const st = () => ({count: document.getElementById("count").textContent, copy: document.getElementById("copy").disabled,
    send: document.getElementById("send").disabled, boxes: [...document.querySelectorAll("#replies .ans")].map(b => b.value)});
  const o = {empty: st()};
  document.getElementById("copy").click();
  document.getElementById("send").click();
  await later(50);
  o.emptySent = st();
  o.plain = [T.copies[0][1], T.sends[0][1]];
  const type = (n, text) => {
    document.querySelectorAll("#replies .para")[n].click();
    const b = document.querySelectorAll("#replies .ans")[n] || document.querySelector(`#replies .ansrow[data-for="m1#${n}"] .ans`);
    b.value = text;
    b.dispatchEvent(new Event("input", {bubbles: true}));
    b.blur();
  };
  type(0, "yes");
  type(1, "no");
  await later(50);
  o.typed = st();
  document.getElementById("send").click();
  await later(50);
  o.sent1 = st();
  document.getElementById("send").click();
  await later(50);
  o.sent2 = st();
  o.sends = T.sends.slice(1).map(s => s[1].map(a => [a.key, a.text]));
  const b = document.querySelector("#replies .ans");
  b.value = "yes, and more";
  b.dispatchEvent(new Event("input", {bubbles: true}));
  b.blur();
  await later(50);
  o.edited = st();
  document.getElementById("tout").textContent = JSON.stringify(o);
})().catch(e => { document.getElementById("tout").textContent = JSON.stringify({error: String(e && e.stack || e)}); });
</script>
"""


@unittest.skipUnless(EDGE, "Microsoft Edge is not installed")
class SendKeeps(unittest.TestCase):
    """Send to Session keeps the answers and can be pressed again, in the real page (the panel's send is a stub)."""
    o = None

    @classmethod
    def setUpClass(cls):
        cls.o = page_run(SEND_TEST)

    def test_no_error(self):
        self.assertNotIn("error", self.o)

    def test_never_off_and_with_no_answer_the_reply_goes_as_it_is(self):
        self.assertEqual(self.o["empty"], {"count": "No answer yet", "copy": False, "send": False, "boxes": []})
        self.assertEqual(self.o["emptySent"]["count"], "No answer yet · sent")
        plain = [{"key": "m1#", "passage": "", "text": "", "line": -1}]
        self.assertEqual(self.o["plain"], [plain, plain])

    def test_a_send_keeps_the_answers_and_a_second_press_pastes_them_again(self):
        self.assertEqual(self.o["typed"], {"count": "2 answers", "copy": False, "send": False, "boxes": ["yes", "no"]})
        self.assertEqual(self.o["sent1"], {"count": "2 answers · sent", "copy": False, "send": False, "boxes": ["yes", "no"]})
        self.assertEqual(self.o["sent2"], self.o["sent1"])
        self.assertEqual(self.o["sends"], [[["m1#0", "yes"], ["m1#1", "no"]]] * 2)

    def test_an_answer_changed_after_a_send_is_no_longer_sent(self):
        self.assertEqual(self.o["edited"]["count"], "2 answers")


@unittest.skipUnless(EDGE, "Microsoft Edge is not installed")
class HeadLink(unittest.TestCase):
    """The session's folder in the head's line is a live link, in the real page."""
    o = None

    @classmethod
    def setUpClass(cls):
        cls.o = page_run(HEAD_LINK_TEST)

    def test_no_error(self):
        self.assertNotIn("error", self.o)

    def test_drawn_as_a_link_with_its_path_whole(self):
        text, url, tip, color, line, cursor = self.o["link"]
        self.assertEqual((text, url), ("C:\\Users\\x\\game club",) * 2)
        self.assertEqual(tip, "Opens this folder in File Explorer")
        self.assertEqual((color, line, cursor), ("rgb(27, 77, 245)", "underline", "pointer"))
        self.assertEqual(self.o["meta"], "gameclub · C:\\Users\\x\\game club · last reply 10:01, after your prompt of 10:00")

    def test_a_click_opens_it_once_and_changes_nothing_else(self):
        self.assertEqual(self.o["opened"], ["C:\\Users\\x\\game club"])
        self.assertEqual((self.o["tabs"], self.o["folds"], self.o["shown"]), ([], 0, "s1"))
        self.assertEqual(self.o["boxes"], 1)  # the box opened before stays, no other opens
        self.assertTrue(self.o["focusKept"])

    def test_a_folder_that_is_gone_gets_the_note_at_the_link(self):
        self.assertEqual(self.o["opened2"], ["C:\\Users\\x\\game club", "C:\\Users\\x\\gone"])
        self.assertEqual(self.o["note"], [["File not found", "C:\\Users\\x\\gone", "meta"]])


# The head's tabs at the reply window's width on a 1920 px screen (808 px): nine sessions, long names among them.
TABS_TEST = r"""<pre id="tout"></pre><script>
(async () => {
  const names = ["order_processing", "audio_capture", "claude-plus", "warehouse-portal", "mindmap", "photoframe",
    "game club", "falcon", "beta"];
  const head = n => ({tabs: names.concat(["sales-forecasts", "one two three four five six seven eight"]).slice(0, n).map((x, i) => ({sid: "s" + i, name: x,
    dot: i === 1 ? "work" : "open", when: "22:0" + i, on: i === 0, full: x})), meta: null, open: true});
  const look = () => [...document.querySelectorAll(".tab")].map(t => {
    const nm = t.querySelector(".nm"), r = t.getBoundingClientRect();
    return {name: nm.textContent, cut: nm.classList.contains("cut") || nm.scrollWidth > nm.clientWidth,
      size: parseFloat(getComputedStyle(nm).fontSize), h: r.height, top: Math.round(r.top), time: t.querySelector(".at").textContent};
  });
  reply.head(head(9));
  await new Promise(r => setTimeout(r, 50));
  const o = {nine: look(), fold: document.getElementById("fold").getBoundingClientRect().height};
  reply.head(head(11));
  await new Promise(r => setTimeout(r, 50));
  o.eleven = look();
  document.getElementById("tout").textContent = JSON.stringify(o);
})().catch(e => { document.getElementById("tout").textContent = JSON.stringify({error: String(e && e.stack || e)}); });
</script>
"""


@unittest.skipUnless(EDGE, "Microsoft Edge is not installed")
class Tabs(unittest.TestCase):
    """The session tabs in the real page: whole names, one height, more rows rather than tabs out of sight."""
    o = None

    @classmethod
    def setUpClass(cls):
        cls.o = page_run(TABS_TEST, "808,300")

    def test_nine_tabs_share_one_row_with_every_name_whole_and_readable(self):
        self.assertNotIn("error", self.o)
        tabs = self.o["nine"]
        self.assertEqual([t["name"] for t in tabs][:2], ["order_processing", "audio_capture"])
        self.assertEqual(len({t["top"] for t in tabs}), 1)
        self.assertFalse([t["name"] for t in tabs if t["cut"]])
        self.assertTrue(all(t["size"] >= 10 for t in tabs))
        self.assertEqual(tabs[0]["time"], "22:00")

    def test_tabs_and_the_fold_button_are_one_height(self):
        self.assertEqual({t["h"] for t in self.o["nine"] + self.o["eleven"]}, {self.o["fold"]})

    def test_more_tabs_wrap_into_a_second_row_and_only_a_name_too_long_for_two_lines_is_cut(self):
        tabs = self.o["eleven"]
        self.assertEqual(len({t["top"] for t in tabs}), 2)
        self.assertEqual([t["name"] for t in tabs if t["cut"]], ["one two three four five six seven eight"])


# Space held in an answer box through the hidden Claude Code (agent-voice-host.py): the page asks "hold" at "Keep
# holding…", reads the prompt line while the take lasts (dim words at the caret), says "release" on key-up and puts the
# final text in; "No speech detected." is a short note; an error or a "whisper" answer goes to Voice to Text (the
# dictation API). The stand-in voice() plays one script per take; headless Edge has no microphone, which the Claude
# path does not need.
VOICE_TEST = r"""<pre id="tout"></pre><script>
const later = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  let script = [], reads = 0;
  // a PC without a microphone (headless Edge never answers getUserMedia): the Claude path does not need the page's
  navigator.mediaDevices.getUserMedia = () => Promise.reject(Object.assign(new Error("none"), {name: "NotFoundError"}));
  T.voice = [];
  T.dict = [];
  T_API.voice = (step, why) => {
    T.voice.push(why ? step + ":" + why : step);
    if (step === "hold") return Promise.resolve(script.hold || {ok: true});
    if (step === "read") { const r = script.reads[Math.min(reads, script.reads.length - 1)]; reads++;
      return Promise.resolve(script.released && r.phase !== "error" ? script.after.shift() || {phase: "done", text: ""} : r); }
    if (step === "release") { script.released = true; return Promise.resolve({ok: true}); }
    return Promise.resolve({ok: true});
  };
  T_API.dictation = step => { T.dict.push(step); return Promise.resolve({up: true}); };
  T_API.dictate = () => { T.dict.push("dictate"); return Promise.resolve({text: "whisper words"}); };
  T_API.draft = () => Promise.resolve(null);
  reply.show({sid: "s1", name: "x", replies: [{id: "m1", when: "10:00", text: "First paragraph.\n\nSecond paragraph."}],
    answers: {}, selections: [], note: "", pins: []});
  await later(50);
  const paras = document.querySelectorAll("#replies .para");
  const key = (t, type, repeat) => t.dispatchEvent(new KeyboardEvent(type, {code: "Space", key: " ", repeat, bubbles: true, cancelable: true}));
  const line = t => { const l = t.parentElement.querySelector(".vline"); return l ? l.textContent : ""; };
  const ghostText = t => { const g = t.parentElement.querySelector(".ghost .tail"); return g ? g.textContent : ""; };
  async function hold(t, steps) {  // a press, its repeats, a look while held, the release, a look after
    t.focus();
    key(t, "keydown", false);
    const o = {afterPress: t.value};
    await later(20);
    key(t, "keydown", true);
    await later(20);
    o.warm = line(t);
    for (let i = 0; i < 4; i++) { key(t, "keydown", true); await later(30); }
    await later(steps || 400);
    o.held = line(t);
    o.ghost = ghostText(t);
    o.valueHeld = t.value;
    key(t, "keyup", false);
    await later(30);
    o.released = line(t);
    await later(900);
    o.value = t.value;
    o.after = line(t);
    o.ghostAfter = ghostText(t);
    return o;
  }
  const out = {};
  paras[0].click();
  await later(20);
  let t = document.querySelector("#replies .ans");
  // 1. an ordinary take
  script = {reads: [{phase: "warm", text: ""}, {phase: "listening", text: "merhaba"}, {phase: "listening", text: "merhaba dünya"}],
    after: [{phase: "transcribing", text: "merhaba dünya"}, {phase: "done", text: "Merhaba dünya.", note: ""}]};
  reads = 0;
  out.ok = await hold(t);
  out.okVoice = T.voice.slice(); T.voice.length = 0;
  out.okDict = T.dict.slice(); T.dict.length = 0;
  // 2. no speech: a short note, nothing typed
  t.value = ""; t.dispatchEvent(new Event("input", {bubbles: true}));
  script = {reads: [{phase: "listening", text: ""}], after: [{phase: "done", text: "", note: "No speech detected."}]};
  reads = 0;
  out.quiet = await hold(t);
  T.voice.length = 0;
  // 3. an error while held: the take goes to Voice to Text
  key(t, "keydown", false); key(t, "keyup", false);  // a key clears the note; then empty the box
  t.value = ""; t.dispatchEvent(new Event("input", {bubbles: true}));
  script = {reads: [{phase: "listening", text: "yarım"}, {phase: "error", why: "Claude Code says: voice failed"}], after: []};
  reads = 0;
  out.err = await hold(t);
  out.errVoice = T.voice.slice(); T.voice.length = 0;
  out.errDict = T.dict.slice(); T.dict.length = 0;
  // 4. the panel answers whisper (not ready, or "dictation": "whisper")
  key(t, "keydown", false); key(t, "keyup", false);
  t.value = ""; t.dispatchEvent(new Event("input", {bubbles: true}));
  script = {hold: {whisper: "the hidden Claude Code is starting"}, reads: [{phase: "idle"}], after: []};
  out.wh = await hold(t);
  out.whVoice = T.voice.slice(); T.voice.length = 0;
  out.whDict = T.dict.slice(); T.dict.length = 0;
  // 5. let go at "Keep holding…": one space stays, the take is dropped
  key(t, "keydown", false); key(t, "keyup", false);
  t.value = ""; t.dispatchEvent(new Event("input", {bubbles: true}));
  script = {reads: [{phase: "warm", text: ""}], after: []};
  t.focus();
  key(t, "keydown", false);
  await later(20);
  key(t, "keydown", true);
  await later(30);
  key(t, "keyup", false);
  await later(100);
  out.cold = {value: t.value, line: line(t), voice: T.voice.slice()};
  // 6. an error after the release (Claude Code could not transcribe): Voice to Text gets the page's recording; with no
  // microphone there is none, so the microphone note
  key(t, "keydown", false); key(t, "keyup", false);
  t.value = ""; t.dispatchEvent(new Event("input", {bubbles: true}));
  T.voice.length = 0;
  script = {reads: [{phase: "listening", text: "bir"}], after: [{phase: "transcribing", text: "bir"},
    {phase: "error", text: "bir", why: "no final text within 15 s of the release"}]};
  reads = 0;
  out.late = await hold(t);
  document.getElementById("tout").textContent = JSON.stringify(out);
})().catch(e => { document.getElementById("tout").textContent = JSON.stringify({error: String(e && e.stack || e)}); });
</script>
"""


@unittest.skipUnless(EDGE, "Microsoft Edge is not installed")
class VoiceTake(unittest.TestCase):
    o = None

    @classmethod
    def setUpClass(cls):
        cls.o = page_run(VOICE_TEST, budget=20000)

    def test_no_error(self):
        self.assertNotIn("error", self.o)

    def test_an_ordinary_take(self):
        o = self.o["ok"]
        self.assertEqual(o["afterPress"], " ")  # a first Space types a space, as before
        self.assertEqual(o["warm"], "Keep holding…")
        self.assertEqual(o["held"], "Listening… release Space to stop.")
        self.assertEqual(o["ghost"], "merhaba dünya")  # the words so far, dim at the caret
        self.assertEqual(o["valueHeld"], "")  # the typed space taken out; the dim words are not in the box
        self.assertEqual(o["released"], "Transcribing…")
        self.assertEqual(o["value"], "Merhaba dünya.")
        self.assertEqual(o["after"], "")
        self.assertEqual(o["ghostAfter"], "")

    def test_the_take_talks_to_the_panel_only(self):
        v = self.o["okVoice"]
        self.assertEqual(v[0], "hold")
        self.assertIn("release", v)
        self.assertGreater(v.count("read"), 3)
        self.assertEqual(self.o["okDict"], [])  # Voice to Text never asked

    def test_no_speech_is_a_short_note(self):
        o = self.o["quiet"]
        self.assertEqual(o["value"], "")
        self.assertEqual(o["after"], "No speech detected.")

    def test_an_error_goes_to_the_fallback(self):
        o = self.o["err"]
        self.assertIn("ready", self.o["errDict"])  # Voice to Text asked for, in the same hold
        self.assertEqual(o["ghost"], "")  # Claude Code's half words gone
        # no microphone here: the Voice to Text take ends on the microphone note, as it always did
        self.assertTrue(o["held"].startswith("No microphone found."), o["held"])

    def test_a_whisper_answer_is_a_fallback_take(self):
        self.assertEqual(self.o["whVoice"], ["hold"])
        self.assertIn("ready", self.o["whDict"])

    def test_an_error_after_the_release_goes_to_the_fallback(self):
        o = self.o["late"]
        self.assertEqual(o["released"], "Transcribing…")
        self.assertEqual(o["value"], "")  # Claude Code's half words are not put in
        self.assertTrue(o["after"].startswith("No microphone found."), o["after"])

    def test_let_go_at_keep_holding_keeps_the_space_and_drops_the_take(self):
        c = self.o["cold"]
        self.assertEqual(c["value"], " ")
        self.assertEqual(c["line"], "")
        self.assertEqual(c["voice"], ["hold", "drop:let go before listening"])


class QuickAnswers(unittest.TestCase):
    """The quick answers the panel hands the page: the user's "quick_answers" from claude-plus.local.json, else
    "I approve"; quick_answers() taken out of agent-panel.pyw by ast."""

    def run_with(self, content):
        with open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        keep = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("local", "quick_answers")]
        keep += [n for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "QUICK_ANSWERS" for t in n.targets)]
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "claude-plus.local.json")
            if content is not None:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
            ns = {"json": json, "LOCAL": path}
            exec(compile(ast.Module(body=keep, type_ignores=[]), "agent-panel.pyw", "exec"), ns)
            return ns["quick_answers"]()
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_the_default_is_english(self):
        self.assertEqual(self.run_with(None), ["I approve"])  # no file
        self.assertEqual(self.run_with('{"reply_open": true}'), ["I approve"])  # no key
        self.assertEqual(self.run_with("not json"), ["I approve"])
        self.assertEqual(self.run_with('{"quick_answers": "Onaylıyorum"}'), ["I approve"])  # not a list

    def test_the_users_own_list_in_order(self):
        self.assertEqual(self.run_with('{"quick_answers": ["Onaylıyorum"]}'), ["Onaylıyorum"])
        self.assertEqual(self.run_with('{"quick_answers": [" Evet ", "", 5, null, "Hayır"]}'), ["Evet", "Hayır"])
        self.assertEqual(self.run_with('{"quick_answers": []}'), [])  # none: no chip


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class QuickInsert(unittest.TestCase):
    def test_the_text_goes_in_at_the_caret(self):
        self.assertEqual(node_markdown()["quick"], [{"value": "Onaylıyorum", "caret": 11}, {"value": "aXb", "caret": 2},
                                                    {"value": "aXd", "caret": 2}, {"value": "abX", "caret": 3}])


QUICK_TEST = r"""<pre id="tout"></pre><script>
const later = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const R = "Intro paragraph.\n\n1. Birinci madde\n2. İkinci madde\n\nAfter.";
  const st = (quick, answers, sid) => ({sid: sid || "s1", name: "x", replies: [{id: "m1", when: "10:00", text: R}], answers: answers || {},
    selections: [], note: "", pins: [], quick});
  reply.show(st(["Onaylıyorum", "Later"]));
  await later(50);
  const ev = (el, type, x, y) => el.dispatchEvent(new (type.startsWith("pointer") ? PointerEvent : MouseEvent)(type,
    {bubbles: true, cancelable: true, composed: true, button: 0, buttons: type === "pointerup" ? 0 : 1, pointerId: 1,
     pointerType: "mouse", isPrimary: true, clientX: x, clientY: y, detail: 1}));
  const press = el => { const r = el.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
    ev(el, "pointerdown", x, y); const md = new MouseEvent("mousedown", {bubbles: true, cancelable: true, button: 0, detail: 1, clientX: x, clientY: y});
    el.dispatchEvent(md); ev(el, "pointerup", x, y); ev(el, "mouseup", x, y); ev(el, "click", x, y); return md.defaultPrevented; };
  const shown = el => !!el && getComputedStyle(el).display !== "none" && el.offsetWidth > 0;
  const o = {};
  press(document.querySelector('#replies .para[data-key="m1#0"]'));
  await later(30);
  const t = document.activeElement, row = t.closest(".ansrow"), q = row.querySelector(".quick");
  const tr = t.getBoundingClientRect(), qr = q.getBoundingClientRect(), pr = parseFloat(getComputedStyle(t).paddingRight);
  o.open = {chips: [...q.querySelectorAll("button")].map(b => [b.textContent, b.tabIndex, b.dataset.tip]), shown: shown(q),
    inside: qr.right <= tr.right && qr.left > tr.left && qr.top >= tr.top && qr.bottom <= tr.bottom,
    room: pr >= qr.width + 6, same: [...q.querySelectorAll("button")].map(b => b.offsetHeight)};
  o.kept = press(q.querySelector("button"));
  await later(400);
  o.after = {value: t.value, focus: document.activeElement === t, caret: [t.selectionStart, t.selectionEnd],
    chips: shown(row.querySelector(".quick")), boxes: document.querySelectorAll("#replies .ansrow").length,
    draft: (T.drafts || []).slice(-1)[0], count: document.getElementById("count").textContent,
    pad: parseFloat(getComputedStyle(t).paddingRight)};
  // emptied again: the chips come back
  t.value = ""; t.dispatchEvent(new Event("input", {bubbles: true}));
  o.back = shown(row.querySelector(".quick"));
  // Tab from the box: the next block's box, never a chip
  t.dispatchEvent(new KeyboardEvent("keydown", {key: "Tab", bubbles: true, cancelable: true}));
  await later(30);
  o.tab = document.activeElement.closest(".ansrow") && document.activeElement.closest(".ansrow").dataset.for;
  // the panel's list changed (the same replies, no redraw): held while the box has the keyboard, then the open box
  // takes the new chips
  reply.show(st(["Yes"]));
  await later(30);
  o.held = [...document.querySelectorAll("#replies .ansrow .quick")].map(x => [...x.children].map(b => b.textContent));
  document.activeElement.blur();
  await later(30);
  o.synced = [...document.querySelectorAll("#replies .ansrow .quick")].map(x => [...x.children].map(b => b.textContent));
  // a box with a kept answer while the list changes (its chips hidden, so not measured), then emptied: room for the
  // new chips
  reply.show(st(["Yes"], {"m1#3": {text: "kept", passage: "After."}}, "s2"));  // another session: drawn afresh
  await later(30);
  reply.show(st(["Yes, go ahead with all of it"], {"m1#3": {text: "kept", passage: "After."}}, "s2"));
  await later(30);
  const kt = document.querySelector('.ansrow[data-for="m1#3"] .ans');
  kt.focus(); kt.value = ""; kt.dispatchEvent(new Event("input", {bubbles: true}));
  const kq = kt.parentElement.querySelector(".quick");
  o.emptiedRoom = parseFloat(getComputedStyle(kt).paddingRight) >= kq.getBoundingClientRect().width + 6;
  kt.blur();
  await later(30);
  // an area's box: a drag over the two items opens one box under them, with the chip
  const B = n => document.querySelector(`#replies [data-n="${n}"]`).getBoundingClientRect(), left = document.getElementById("body").getBoundingClientRect().left + 4;
  const el = document.getElementById("body"), x0 = left, y0 = B(1).top + 4, x1 = 400, y1 = B(2).top + 6;
  ev(el, "pointerdown", x0, y0); ev(el, "mousedown", x0, y0);
  for (let k = 1; k <= 5; k++) ev(el, "pointermove", x0 + (x1 - x0) * k / 5, y0 + (y1 - y0) * k / 5);
  ev(el, "pointerup", x1, y1); ev(el, "mouseup", x1, y1); ev(el, "click", x1, y1);
  await later(30);
  const at = document.activeElement, arow = at.closest(".ansrow.area");
  o.area = {area: !!arow, chips: shown(arow && arow.querySelector(".quick"))};
  press(arow.querySelector(".quick button"));
  await later(400);
  o.areaAfter = {value: at.value, focus: document.activeElement === at, frame: document.querySelectorAll(".agroup").length,
    key: (T.drafts || []).slice(-1)[0]};
  // none in the list: no chip, the box's usual padding
  at.blur();
  reply.show(st([], {}));
  await later(30);
  press(document.querySelector('#replies .para[data-key="m1#0"]'));
  await later(30);
  o.none = [document.activeElement.closest(".ansrow").querySelectorAll(".quick button").length, getComputedStyle(document.activeElement).paddingRight];
  document.getElementById("tout").textContent = JSON.stringify(o);
})().catch(e => { document.getElementById("tout").textContent = JSON.stringify({error: String(e && e.stack || e)}); });
</script>
"""


@unittest.skipUnless(EDGE, "Microsoft Edge is not installed")
class QuickPage(unittest.TestCase):
    """The quick answers in the real page: a chip in an empty box puts its text in, the box keeps the keyboard."""
    o = None

    @classmethod
    def setUpClass(cls):
        cls.o = page_run(QUICK_TEST, budget=6000)

    def test_no_error(self):
        self.assertNotIn("error", self.o)

    def test_an_empty_box_shows_the_chips_inside_its_right_end(self):
        o = self.o["open"]
        tip = "Puts this text in the box"
        self.assertEqual(o["chips"], [["Onaylıyorum", -1, tip], ["Later", -1, tip]])  # in order, no Tab stop
        self.assertTrue(o["shown"] and o["inside"], o)
        self.assertTrue(o["room"], o)  # the placeholder and the caret keep clear of them
        self.assertEqual(len(set(o["same"])), 1)  # side by side, one height

    def test_a_click_puts_the_text_in_and_keeps_the_box(self):
        self.assertTrue(self.o["kept"])  # the press leaves the keyboard in the box (no focusout closing it)
        a = self.o["after"]
        self.assertEqual(a["value"], "Onaylıyorum")
        self.assertTrue(a["focus"])
        self.assertEqual(a["caret"], [11, 11])
        self.assertFalse(a["chips"])  # text in the box: the chips go
        self.assertEqual(a["boxes"], 1)  # no other box opened, this one not closed
        self.assertEqual(a["draft"], ["s1", "m1#0", "Intro paragraph.", "Onaylıyorum"])  # saved as typing is
        self.assertEqual(a["count"], "1 answer")
        self.assertEqual(a["pad"], 10)
        self.assertTrue(self.o["back"])  # emptied: back

    def test_tab_never_lands_on_a_chip(self):
        self.assertEqual(self.o["tab"], "m1#1")

    def test_a_new_list_reaches_the_open_boxes(self):
        self.assertEqual(self.o["held"], [["Onaylıyorum", "Later"]])
        self.assertEqual(self.o["synced"], [["Yes"]])

    def test_a_box_emptied_after_a_redraw_makes_room_for_them(self):
        self.assertTrue(self.o["emptiedRoom"])

    def test_an_areas_box_has_them_too(self):
        self.assertEqual(self.o["area"], {"area": True, "chips": True})
        a = self.o["areaAfter"]
        long = "Yes, go ahead with all of it"
        self.assertEqual((a["value"], a["focus"], a["frame"]), (long, True, 1))
        self.assertEqual((a["key"][1], a["key"][3]), ("m1#1-2", long))

    def test_an_empty_list_shows_no_chip(self):
        self.assertEqual(self.o["none"], [0, "10px"])

class AreaClipboard(unittest.TestCase):
    """The clipboard follows the area (asked 2026-11-25): its blocks, and once its box has an answer, the blocks quoted
    with the answer after them as Send to Session words it; written only while the clipboard still holds the copy
    this page made last. Windows' clipboard is never touched: user32 and kernel32 are stand-ins."""

    MD = "1. Birinci madde\n2. İkinci madde"

    def test_with_no_answer_the_blocks_go_as_they_are(self):
        self.assertEqual(ar.area_copy(self.MD, []), self.MD)
        self.assertEqual(ar.area_copy(self.MD, [(1, self.MD, "  \n", 2)]), self.MD)

    def test_an_areas_answer_follows_its_quoted_blocks(self):
        self.assertEqual(ar.area_copy(self.MD, [(1, self.MD, "Onaylıyorum", 2)]),
                         '> 1. Birinci madde\n> 2. İkinci madde\n\n'
                         'My answer to all 2 blocks from "1. Birinci madde" down to here, as a whole: Onaylıyorum')

    def test_one_block_and_a_paragraph_read_as_that_blocks_own_answer(self):
        self.assertEqual(ar.area_copy("After.", [(0, "After.", "ok", 1)]), '> After.\n\nMy answer to "After.": ok')
        self.assertEqual(ar.area_copy("After.", [(0, "After.", "ok", 0)]), '> After.\n\nMy answer to "After.": ok')

    def test_a_blocks_own_answer_inside_the_area_stays_at_its_block_before_the_areas(self):
        md = "Intro.\n\n1. one\n2. two"
        out = ar.area_copy(md, [(0, "Intro.", "first", 0), (3, "2. two", "own", 0), (3, md, "all", 3)])
        self.assertEqual(out, '> Intro.\n\nMy answer to "Intro.": first\n\n> 1. one\n> 2. two\n\n'
                              'My answer to "2. two": own\n\nMy answer to all 3 blocks from "Intro." down to here, as a whole: all')
        self.assertFalse(out.startswith("My answers inside"))  # no heading line

    def fake(self, seq):
        import ctypes, types
        calls, store, bufs = [], {}, []

        def put(fmt, h):
            store["text"] = ctypes.wstring_at(h)
            self.seq += 1
            return 1

        def alloc(flags, n):
            b = ctypes.create_string_buffer(n)
            bufs.append(b)
            return ctypes.addressof(b)

        self.seq = seq
        noop = lambda *a: 1
        u = types.SimpleNamespace(OpenClipboard=noop, CloseClipboard=lambda: calls.append("close") or 1,
                                  EmptyClipboard=lambda: calls.append("empty") or 1, SetClipboardData=put,
                                  GetClipboardSequenceNumber=lambda: self.seq)
        k = types.SimpleNamespace(GlobalAlloc=alloc, GlobalLock=lambda h: h, GlobalUnlock=noop, GlobalFree=noop)
        return u, k, calls, store, bufs

    def test_written_only_while_the_clipboard_holds_the_last_copy(self):
        u, k, calls, store, bufs = self.fake(7)
        self.assertEqual(ar.set_clipboard_text("blocks", None, u, k), 8)  # the release: written, its number back
        self.assertEqual(ar.set_clipboard_text("blocks + answer", 8, u, k), 9)  # still ours: followed
        self.assertEqual(store["text"], "blocks + answer")
        self.seq = 20  # the user copied something else
        n = len(calls)
        self.assertIsNone(ar.set_clipboard_text("blocks + answer!", 9, u, k))
        self.assertEqual(calls[n:], ["close"])  # opened, compared, closed: nothing emptied or written
        self.assertEqual(store["text"], "blocks + answer")

    def test_the_panel_hands_the_answers_and_the_number_through(self):
        import types
        with open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        api = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ReplyApi")
        fn = next(n for n in api.body if isinstance(n, ast.FunctionDef) and n.name == "copy_area")
        ns = {}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "agent-panel.pyw", "exec"), ns)
        got = []
        mod = types.SimpleNamespace(area_copy=ar.area_copy, area_size=ar.area_size,
                                    set_clipboard_text=lambda t, e=None: got.append((t, e)) or 5)
        me = types.SimpleNamespace(_p=types.SimpleNamespace(reply_mod=mod))
        self.assertEqual(ns["copy_area"](me, self.MD), 5)
        self.assertEqual(ns["copy_area"](me, self.MD, [{"key": "m1#1-2", "passage": self.MD, "text": "Onaylıyorum", "line": 1}], 5), 5)
        self.assertEqual(got[0], (self.MD, None))
        self.assertEqual(got[1], ('> 1. Birinci madde\n> 2. İkinci madde\n\nMy answer to all 2 blocks from '
                                  '"1. Birinci madde" down to here, as a whole: Onaylıyorum', 5))
        self.assertFalse(ns["copy_area"](me, ""))


# The clipboard follows the area in the real page (headless Edge, the panel's copy_area a stand-in holding a pretend
# clipboard's sequence number): the release copies the blocks; typing in the area's box copies them again with the
# answer CLIP_MS after typing stops, each copy handed the number the last one left; once something else was copied,
# the next copy is refused and the area stops following; a quick answer and Enter copy at once; another box's typing
# never copies; no "Copied" note for these.
CLIP_TEST = r"""<pre id="tout"></pre><script>
const later = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  T.seq = 10; T.calls = [];
  T_API.copy_area = (text, answers, expect) => {
    T.calls.push({text, answers: (answers || []).map(a => [a.key, a.text, a.line]), expect});
    if (expect !== null && expect !== undefined && expect !== T.seq) return Promise.resolve(null);
    T.seq++;
    return Promise.resolve(T.seq);
  };
  const R = "Intro paragraph.\n\n1. Birinci madde\n2. İkinci madde\n\nAfter.";
  reply.show({sid: "s1", name: "x", replies: [{id: "m1", when: "10:00", text: R}], answers: {}, selections: [], note: "", pins: [],
    quick: ["Onaylıyorum"]});
  await later(50);
  const ev = (el, type, x, y) => el.dispatchEvent(new (type.startsWith("pointer") ? PointerEvent : MouseEvent)(type,
    {bubbles: true, cancelable: true, composed: true, button: 0, buttons: type === "pointerup" ? 0 : 1, pointerId: 1,
     pointerType: "mouse", isPrimary: true, clientX: x, clientY: y, detail: 1}));
  const press = el => { const r = el.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
    ev(el, "pointerdown", x, y); el.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, cancelable: true, button: 0, detail: 1, clientX: x, clientY: y}));
    ev(el, "pointerup", x, y); ev(el, "mouseup", x, y); ev(el, "click", x, y); };
  const B = n => document.querySelector(`#replies [data-n="${n}"]`).getBoundingClientRect();
  const body = document.getElementById("body"), left = body.getBoundingClientRect().left + 4, nt = document.getElementById("areaNote");
  const drag = async (a, b) => {
    const x0 = left, y0 = B(a).top + 4, x1 = 400, y1 = B(b).top + 6;
    ev(body, "pointerdown", x0, y0); ev(body, "mousedown", x0, y0);
    for (let k = 1; k <= 5; k++) ev(body, "pointermove", x0 + (x1 - x0) * k / 5, y0 + (y1 - y0) * k / 5);
    ev(body, "pointerup", x1, y1); ev(body, "mouseup", x1, y1); ev(body, "click", x1, y1);
    await later(30);
  };
  const type = s => { if (!document.execCommand("insertText", false, s)) { const t = document.activeElement; t.value += s; t.dispatchEvent(new Event("input", {bubbles: true})); } };
  const o = {};
  await drag(1, 2);  // the two items: one frame, its box, the blocks copied
  o.release = T.calls.slice();
  const box = document.activeElement;
  await later(1100);  // the release's "Copied" is gone
  await later(800);  // and its tint
  o.tintGone = document.querySelectorAll(".copied, .fading").length;
  type("Onay");
  await later(100);
  o.early = T.calls.length;  // typing still going on: nothing yet
  type("lıyorum");
  await later(400);
  o.typed = T.calls.slice(1);
  o.note = nt.hidden;
  o.retint = document.querySelectorAll(".copied, .fading").length;
  T.seq = 50;  // the user copied something else
  type("!");
  await later(400);
  type("?");
  await later(400);
  o.moved = T.calls.slice(1 + o.typed.length);
  // a new area (the paragraph after): a quick answer copies at once
  box.blur();
  await later(30);
  await drag(3, 3);
  const n0 = T.calls.length, row = document.activeElement.closest(".ansrow");
  o.lone = [row && row.dataset.for, T.calls[n0 - 1]];
  press(row.querySelector(".quick button"));
  await later(20);
  o.quick = T.calls.slice(n0);
  // typed, then Enter at once: the copy goes as the box is left
  type(" now");
  document.activeElement.dispatchEvent(new KeyboardEvent("keydown", {key: "Enter", bubbles: true, cancelable: true}));
  await later(20);
  o.enter = T.calls.slice(n0 + o.quick.length);
  await later(400);
  o.enterOnce = T.calls.length - n0 - o.quick.length;
  // another box (the intro's): its typing never copies
  const n1 = T.calls.length;
  press(document.querySelector('#replies .para[data-key="m1#0"]'));
  await later(30);
  type("other");
  await later(400);
  o.other = T.calls.length - n1;
  document.getElementById("tout").textContent = JSON.stringify(o);
})().catch(e => { document.getElementById("tout").textContent = JSON.stringify({error: String(e && e.stack || e)}); });
</script>
"""


@unittest.skipUnless(EDGE, "Microsoft Edge is not installed")
class ClipPage(unittest.TestCase):
    o = None
    MD = "1. Birinci madde\n2. İkinci madde"

    @classmethod
    def setUpClass(cls):
        cls.o = page_run(CLIP_TEST, budget=9000)

    def test_no_error(self):
        self.assertNotIn("error", self.o)

    def test_the_release_copies_the_blocks_alone(self):
        self.assertEqual(self.o["release"], [{"text": self.MD, "answers": [], "expect": None}])

    def test_typing_copies_the_blocks_again_with_the_answer_once_it_stops(self):
        self.assertEqual(self.o["early"], 1)
        self.assertEqual(self.o["typed"], [{"text": self.MD, "answers": [["m1#1-2", "Onaylıyorum", 1]], "expect": 11}])
        self.assertTrue(self.o["note"])  # no "Copied" for it
        self.assertEqual((self.o["tintGone"], self.o["retint"]), (0, 0))  # and no tint again

    def test_once_something_else_was_copied_the_area_stops_following(self):
        self.assertEqual(self.o["moved"], [{"text": self.MD, "answers": [["m1#1-2", "Onaylıyorum!", 1]], "expect": 12}])

    def test_a_quick_answer_copies_at_once(self):
        key, release = self.o["lone"]
        self.assertEqual((key, release["text"], release["answers"]), ("m1#3", "After.", []))
        self.assertEqual([c["answers"] for c in self.o["quick"]], [[["m1#3", "Onaylıyorum", 0]]])

    def test_enter_copies_at_once_and_only_once(self):
        self.assertEqual([c["answers"] for c in self.o["enter"]], [[["m1#3", "Onaylıyorum now", 0]]])
        self.assertEqual(self.o["enterOnce"], 1)

    def test_another_boxs_typing_never_copies(self):
        self.assertEqual(self.o["other"], 0)


# Live dictation in the real page (headless Edge, the panel's voice() a stand-in, keys dispatched to the page, never to
# a window): the dim words stand where the final text goes, the placeholder is not drawn under them, in an empty box, a
# box with text before the caret, a long take that wraps, an area's box; Esc cancels a take (held or transcribing), and
# outside a take asks once, clears on a second press within ESC_MS (Ctrl+Z brings it back), leaves an empty box.
ESC_TEST = r"""<pre id="tout"></pre><script>
const later = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  navigator.mediaDevices.getUserMedia = () => Promise.reject(Object.assign(new Error("none"), {name: "NotFoundError"}));
  let script = null;
  T.voice = []; T.drafts = [];
  T_API.voice = (step, why) => {
    T.voice.push(why ? step + ":" + why : step);
    if (step === "hold") return Promise.resolve({ok: true});
    if (step === "read") return Promise.resolve(script.released ? {phase: "transcribing", text: script.text}
      : {phase: "listening", text: script.text});
    if (step === "release") { script.released = true; return Promise.resolve({ok: true}); }
    return Promise.resolve({ok: true});
  };
  T_API.dictation = () => Promise.resolve({up: true});
  T_API.draft = (sid, key, passage, text) => { T.drafts.push([key, text]); return Promise.resolve(null); };
  reply.show({sid: "s1", name: "x", replies: [{id: "m1", when: "10:00", text: "First paragraph.\n\nSecond paragraph.\n\nThird."}],
    answers: {}, selections: [], note: "", pins: [], quick: ["I approve"]});
  await later(50);
  const sp = (t, type, repeat) => t.dispatchEvent(new KeyboardEvent(type, {code: "Space", key: " ", repeat, bubbles: true, cancelable: true}));
  const esc = t => t.dispatchEvent(new KeyboardEvent("keydown", {code: "Escape", key: "Escape", bubbles: true, cancelable: true}));
  const key = (t, k) => t.dispatchEvent(new KeyboardEvent("keydown", {code: "Key" + k.toUpperCase(), key: k, bubbles: true, cancelable: true}));
  const line = t => { const l = t.parentElement && t.parentElement.querySelector(".vline"); return l ? l.textContent : ""; };
  const look = t => {  // what the box draws: the placeholder's colour, the dim copy over it, both in one place
    const g = t.parentElement.querySelector(".ghost"), tr = t.getBoundingClientRect(), q = t.parentElement.querySelector(".quick");
    const gr = g && g.getBoundingClientRect(), px = (el, k) => parseFloat(getComputedStyle(el)[k]);
    return {ph: getComputedStyle(t, "::placeholder").color, text: getComputedStyle(t).color,
      ghost: g ? g.textContent.replace("​", "") : null, tail: g ? g.querySelector(".tail").textContent : null,
      same: !!g && Math.abs(gr.left - tr.left) < 0.5 && Math.abs(gr.top - tr.top) < 0.5 && Math.abs(gr.width - tr.width) < 0.5
        && px(g, "paddingLeft") === px(t, "paddingLeft") && px(g, "paddingRight") === px(t, "paddingRight"),
      fits: !g || tr.height >= gr.height - 0.5, chips: !!q && getComputedStyle(q).display !== "none"};
  };
  async function hold(t, text) {  // Space pressed and held until listening; not let go
    script = {text, released: false};
    t.focus();
    sp(t, "keydown", false);
    await later(20);
    for (let i = 0; i < 5; i++) { sp(t, "keydown", true); await later(30); }
    await later(400);
  }
  const box = n => { document.querySelectorAll("#replies .para")[n].click(); return document.activeElement; };
  const voice = () => T.voice.filter(v => v !== "read");
  const o = {};
  // 1. an empty box: the placeholder goes, the words stand at its start
  let t = box(0);
  o.emptyBefore = look(t);
  await hold(t, "de göremedim hiçbir yerde");
  o.empty = Object.assign(look(t), {line: line(t), value: t.value});
  // Esc while listening: the take goes, the hidden prompt is dropped, nothing goes in
  T.voice.length = 0;
  esc(t);
  await later(300);
  o.cancel = {value: t.value, line: line(t), ghost: !!t.parentElement.querySelector(".ghost"), voice: voice(),
    focus: document.activeElement === t, ph: getComputedStyle(t, "::placeholder").color, rec: !!rec, busy: busy.size};
  sp(t, "keyup", false);  // the Space let go after Esc: nothing happens
  await later(300);
  o.afterKeyup = {value: t.value, line: line(t), voice: voice()};
  // 2. a box with text before the caret
  t.value = "Önce yazı"; t.dispatchEvent(new Event("input", {bubbles: true}));
  t.setSelectionRange(t.value.length, t.value.length);
  await hold(t, "sonra dikte");
  o.text = look(t);
  // Esc while transcribing (Space let go): the take goes, the box keeps what was there
  sp(t, "keyup", false);
  await later(200);
  o.transcribing = line(t);
  T.voice.length = 0;
  esc(t);
  await later(300);
  o.cancelBusy = {value: t.value, line: line(t), voice: voice(), busy: busy.size};
  // 3. a long take wraps like the box's own text and grows the box
  t.value = ""; t.dispatchEvent(new Event("input", {bubbles: true}));
  const h0 = t.getBoundingClientRect().height;
  await hold(t, Array.from({length: 40}, (_, i) => "kelime" + i).join(" "));
  o.long = Object.assign(look(t), {grew: t.getBoundingClientRect().height > h0 * 2});
  // words committed meanwhile (Voice to Text commits while held) go too: the box is as the take found it
  rec.said = "committed words";
  putDictation(t, "committed words");
  o.committed = t.value;
  esc(t);
  await later(300);
  o.cancelCommitted = {value: t.value, h: Math.round(t.getBoundingClientRect().height), h0: Math.round(h0)};
  sp(t, "keyup", false);
  await later(100);
  // 4. Esc at "Keep holding…": the press's space goes, the hold is dropped
  t.value = "abc"; t.dispatchEvent(new Event("input", {bubbles: true}));
  t.setSelectionRange(3, 3);
  T.voice.length = 0;
  script = {text: ""};
  sp(t, "keydown", false);
  await later(20);
  sp(t, "keydown", true);
  await later(50);
  o.warmLine = line(t);
  esc(t);
  await later(100);
  sp(t, "keyup", false);
  await later(100);
  o.warm = {value: t.value, line: line(t), voice: T.voice.slice()};
  // 5. not dictating, text in the box: the first Esc asks, the second clears; the box keeps the keyboard
  T.drafts.length = 0;
  esc(t);
  await later(50);
  o.first = {value: t.value, line: line(t), focus: document.activeElement === t};
  esc(t);
  await later(400);
  o.second = {value: t.value, line: line(t), focus: document.activeElement === t, drafts: T.drafts.slice(),
    chips: getComputedStyle(t.parentElement.querySelector(".quick")).display !== "none"};
  document.execCommand("undo");
  await later(50);
  o.undo = t.value;
  // the hint goes after ESC_MS, and the Esc after it asks again
  esc(t);
  await later(ESC_MS + 200);
  o.expired = line(t);
  esc(t);
  await later(50);
  o.again = {value: t.value, line: line(t)};
  // another key between two Escs: the second asks again
  key(t, "x");
  esc(t);
  await later(50);
  o.between = {value: t.value, line: line(t)};
  // Enter still keeps the text and leaves the box
  t.dispatchEvent(new KeyboardEvent("keydown", {key: "Enter", code: "Enter", bubbles: true, cancelable: true}));
  await later(50);
  o.enter = {value: t.value, focus: document.activeElement === t, open: t.isConnected};
  // 6. an empty box: Esc leaves it and it closes
  const t2 = box(1);
  esc(t2);
  await later(50);
  o.leave = {open: t2.isConnected, focus: document.activeElement === document.body};
  // 7. an area's box: the same, the placeholder gone under the words
  const ta = openAreaBox(areaKey("m1", 1, 2), "", true);
  ta.focus();
  await hold(ta, "alanın cevabı");
  o.area = look(ta);
  esc(ta);
  await later(300);
  o.areaCancel = {value: ta.value, line: line(ta)};
  sp(ta, "keyup", false);
  document.getElementById("tout").textContent = JSON.stringify(o);
})().catch(e => { document.getElementById("tout").textContent = JSON.stringify({error: String(e && e.stack || e)}); });
</script>
"""


@unittest.skipUnless(EDGE, "Microsoft Edge is not installed")
class EscAndLiveWords(unittest.TestCase):
    o = None
    CLEAR = "rgba(0, 0, 0, 0)"

    @classmethod
    def setUpClass(cls):
        cls.o = page_run(ESC_TEST, budget=30000)

    def test_no_error(self):
        self.assertNotIn("error", self.o)

    def test_the_placeholder_shows_in_an_empty_box_and_goes_while_words_arrive(self):
        self.assertNotEqual(self.o["emptyBefore"]["ph"], self.CLEAR)
        e = self.o["empty"]
        self.assertEqual(e["ph"], self.CLEAR)
        self.assertEqual(e["text"], self.CLEAR)  # the box's own text is clear; the copy draws it
        self.assertEqual((e["ghost"], e["tail"]), ("de göremedim hiçbir yerde", "de göremedim hiçbir yerde"))
        self.assertTrue(e["same"])
        self.assertFalse(e["chips"])  # the quick answer hides while dictating
        self.assertEqual(e["value"], "")
        self.assertEqual(e["line"], "Listening… release Space to stop.")

    def test_words_follow_the_text_before_the_caret(self):
        t = self.o["text"]
        self.assertEqual((t["ghost"], t["tail"]), ("Önce yazı sonra dikte", " sonra dikte"))
        self.assertTrue(t["same"])

    def test_a_long_take_wraps_and_grows_the_box(self):
        l = self.o["long"]
        self.assertTrue(l["same"])
        self.assertTrue(l["fits"])
        self.assertTrue(l["grew"])

    def test_an_area_box_draws_the_words_the_same_way(self):
        a = self.o["area"]
        self.assertEqual(a["ph"], self.CLEAR)
        self.assertEqual(a["tail"], "alanın cevabı")
        self.assertTrue(a["same"])
        self.assertEqual(self.o["areaCancel"], {"value": "", "line": ""})

    def test_esc_while_listening_cancels_the_take(self):
        c = self.o["cancel"]
        self.assertEqual((c["value"], c["line"], c["ghost"], c["rec"], c["busy"]), ("", "", False, False, 0))
        self.assertEqual(c["voice"], ["drop:cancelled with Esc"])
        self.assertTrue(c["focus"])
        self.assertNotEqual(c["ph"], self.CLEAR)  # the placeholder is back
        self.assertEqual(self.o["afterKeyup"], {"value": "", "line": "", "voice": ["drop:cancelled with Esc"]})

    def test_esc_while_transcribing_cancels_and_keeps_the_text_before(self):
        self.assertEqual(self.o["transcribing"], "Transcribing…")
        c = self.o["cancelBusy"]
        self.assertEqual((c["value"], c["line"], c["busy"]), ("Önce yazı", "", 0))
        self.assertEqual(c["voice"], ["drop:cancelled with Esc"])

    def test_esc_takes_committed_words_out_again(self):
        self.assertEqual(self.o["committed"], "committed words")
        c = self.o["cancelCommitted"]
        self.assertEqual(c["value"], "")
        self.assertEqual(c["h"], c["h0"])

    def test_esc_at_keep_holding_takes_the_space_out_and_drops_the_hold(self):
        self.assertEqual(self.o["warmLine"], "Keep holding…")
        w = self.o["warm"]
        self.assertEqual((w["value"], w["line"]), ("abc", ""))
        self.assertEqual(w["voice"], ["hold", "drop:let go before listening"])

    def test_first_esc_asks_second_clears_and_ctrl_z_brings_it_back(self):
        self.assertEqual(self.o["first"], {"value": "abc", "line": "Press Esc again to clear", "focus": True})
        s = self.o["second"]
        self.assertEqual((s["value"], s["line"], s["focus"], s["chips"]), ("", "", True, True))
        self.assertEqual(s["drafts"], [["m1#0", ""]])  # the draft goes, as an emptied answer's
        self.assertEqual(self.o["undo"], "abc")

    def test_the_hint_lasts_esc_ms_and_another_key_starts_over(self):
        self.assertEqual(self.o["expired"], "")
        self.assertEqual(self.o["again"], {"value": "abc", "line": "Press Esc again to clear"})
        self.assertEqual(self.o["between"], {"value": "abc", "line": "Press Esc again to clear"})

    def test_enter_keeps_the_text_and_leaves(self):
        self.assertEqual(self.o["enter"], {"value": "abc", "focus": False, "open": True})

    def test_esc_in_an_empty_box_leaves_and_closes_it(self):
        self.assertEqual(self.o["leave"], {"open": False, "focus": True})


if __name__ == "__main__":
    unittest.main(verbosity=1)
