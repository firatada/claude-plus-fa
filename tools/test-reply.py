# Unit tests for agent-reply.py, the reply window's rules: which transcript lines are a reply (end_turn text of the
# main thread; not tool_use narration, thinking, a subagent, <synthetic> or an API error), every reply since the
# last typed prompt, two text blocks of one message joined, reading on from where the last read stopped; the
# drafts kept per session; the line Send to Session types and the text Copy puts on the clipboard.
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


if __name__ == "__main__":
    unittest.main(verbosity=1)
