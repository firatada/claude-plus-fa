# Unit tests for the move to Codex and back (glm-fallback.py): the session's name, Codex's first prompt, the
# incremental carry of Codex's turns, the first-turn watch ending at Codex's first answer, background commands.
# Run: python -I tools/test-codex-move.py
# glm-fallback.py is loaded as a module with RETURN_DIR and HOME on a temp folder: nothing real is read or written,
# no window opens, nothing is sent.
import importlib.util, json, os, shutil, sys, tempfile, threading, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="claude-plus-test-move-")


def load():
    spec = importlib.util.spec_from_file_location("glm_fallback", os.path.join(HERE, "..", "glm-fallback.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.HOME = TMP
    m.RETURN_DIR = os.path.join(TMP, "glm-return")
    return m


fb = load()


def ev(kind, **p):
    return json.dumps({"type": "event_msg", "payload": dict(type=kind, **p)}) + "\n"


def turn(user, reply):
    return ev("task_started") + ev("user_message", message=user) + ev("agent_message", message=reply) + \
        ev("task_complete")


class Thread:
    """A made-up Codex thread file and its record."""
    def __init__(self, name):
        self.path = os.path.join(TMP, name + ".jsonl")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "session_meta", "payload": {}}) + "\n")
        self.rec = {"session": "S-" + name, "via": "codex", "codex_thread": "T-" + name, "codex_file": self.path,
                    "codex_from": os.path.getsize(self.path), "cwd": TMP, "folder": "tmp", "at": time.time() - 5,
                    "console": None}

    def add(self, text):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(text)


class Carry(unittest.TestCase):
    def test_each_carry_takes_only_newer_turns(self):
        t = Thread("inc")
        self.assertFalse(fb.pending(t.rec))
        self.assertIsNone(fb.take_carry(t.rec, "test"))  # nothing yet
        t.add(turn("first question", "first answer"))
        span = fb.take_carry(t.rec, "hook")
        body = fb.codex_record(t.rec, span)
        self.assertIn("first question", body)
        self.assertFalse(fb.pending(t.rec))
        t.add(turn("second question", "second answer") + turn("option two is fine", "Option two noted"))
        self.assertTrue(fb.pending(t.rec))
        span2 = fb.take_carry(t.rec, "panel")
        body2 = fb.codex_record(t.rec, span2)
        self.assertNotIn("first question", body2)
        self.assertIn("second question", body2)
        self.assertIn("Option two noted", body2)
        prompt, _ = fb.codex_back_prompt(t.rec, span=span2)
        self.assertTrue(prompt.startswith("This session went on in Codex after the last record"))
        first, _ = fb.codex_back_prompt(t.rec, span=span)
        self.assertTrue(first.startswith("While Claude's limit was out"))
        self.assertIsNone(fb.take_carry(t.rec, "panel"))

    def test_half_written_line_waits(self):
        t = Thread("half")
        t.add(turn("q", "a") + '{"type":"event_msg","payload":{"type":"user_mess')
        span = fb.take_carry(t.rec, "hook")
        t.add('age","message":"late one"}}\n' + ev("agent_message", message="late reply"))
        span2 = fb.take_carry(t.rec, "hook")
        self.assertIn("late one", fb.codex_record(t.rec, span2))
        self.assertEqual(span2["start"], span["end"])

    def test_give_back(self):
        t = Thread("back")
        t.add(turn("q1", "a1"))
        span = fb.take_carry(t.rec, "panel")
        fb.give_back(t.rec, span)
        self.assertTrue(fb.pending(t.rec))
        again = fb.take_carry(t.rec, "panel")
        self.assertEqual((again["start"], again["end"]), (span["start"], span["end"]))
        # a later carry moved the mark on: giving the first back does nothing
        t.add(turn("q2", "a2"))
        later = fb.take_carry(t.rec, "hook")
        fb.give_back(t.rec, again)
        self.assertEqual(fb.carried_to(t.rec), later["end"])

    def test_old_claim_counts_as_all_carried(self):
        t = Thread("legacy")
        t.add(turn("q", "a"))
        os.makedirs(fb.RETURN_DIR, exist_ok=True)
        with open(fb.claim_path(t.rec), "w") as f:
            json.dump({"by": "panel", "at": 1}, f)
        self.assertFalse(fb.pending(t.rec))

    def test_race_one_winner(self):
        t = Thread("race")
        t.add(turn("q", "a"))
        got = []
        ths = [threading.Thread(target=lambda: got.append(fb.take_carry(t.rec, "x"))) for _ in range(12)]
        [h.start() for h in ths]
        [h.join() for h in ths]
        self.assertEqual(len([g for g in got if g]), 1)

    def test_hook_waits_while_codex_open(self):
        t = Thread("hook")
        t.add(turn("q", "a"))
        os.makedirs(fb.RETURN_DIR, exist_ok=True)
        t.rec["console"], t.rec["console_born"] = os.getpid(), fb.born(os.getpid())  # alive: the window is open
        with open(os.path.join(fb.RETURN_DIR, t.rec["session"] + ".json"), "w") as f:
            json.dump(t.rec, f)

        def run(event, source=None):
            import io, contextlib
            data = {"session_id": t.rec["session"], "hook_event_name": event}
            if source:
                data["source"] = source
            sys.stdin, out = io.StringIO(json.dumps(data)), io.StringIO()
            with contextlib.redirect_stdout(out):
                fb.carry_hook()
            sys.stdin = sys.__stdin__
            return out.getvalue()
        self.assertEqual(run("UserPromptSubmit"), "")  # a prompt typed while Codex works: nothing taken
        self.assertTrue(fb.pending(t.rec))
        out = json.loads(run("SessionStart", "resume"))  # a resume by hand: what exists so far
        self.assertIn("q", out["hookSpecificOutput"]["additionalContext"])
        t.add(turn("after the resume", "more"))
        self.assertEqual(run("UserPromptSubmit"), "")
        t.rec["console"] = 999999  # the window closed
        with open(os.path.join(fb.RETURN_DIR, t.rec["session"] + ".json"), "w") as f:
            json.dump(t.rec, f)
        out = json.loads(run("UserPromptSubmit"))
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("after the resume", ctx)
        self.assertNotIn("User: q\n", ctx)
        self.assertTrue(os.path.exists(os.path.join(fb.RETURN_DIR, t.rec["session"] + ".json")))  # kept for the panel


class Names(unittest.TestCase):
    def transcript(self, lines):
        p = os.path.join(TMP, "t%d.jsonl" % len(os.listdir(TMP)))
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(l, separators=(",", ":")) for l in lines) + "\n")
        return p

    def test_order(self):
        u = {"type": "user", "message": {"role": "user", "content": "  make the   news video  "}}
        self.assertEqual(fb.session_name(self.transcript([u])), "make the news video")
        ai = {"type": "ai-title", "aiTitle": "News video"}
        self.assertEqual(fb.session_name(self.transcript([u, ai])), "News video")
        cu = {"type": "custom-title", "customTitle": "demo project"}
        self.assertEqual(fb.session_name(self.transcript([u, cu, {"type": "ai-title", "aiTitle": "later"}])),
                         "demo project")
        meta = {"type": "user", "isMeta": True, "message": {"content": "meta"}}
        self.assertEqual(fb.session_name(self.transcript([meta, u])), "make the news video")
        self.assertEqual(fb.session_name(os.path.join(TMP, "none.jsonl")), "")

    def test_a_session_that_starts_with_a_paste(self):
        u = {"type": "user", "message": {"role": "user", "content":
             '\n\n<pasted_content id="89a2">\nC:\\work\\loan\n</pasted_content id="89a2">\n\n bu klasorde cmd acamiyorum'}}
        self.assertEqual(fb.session_name(self.transcript([u])), "C:\\work\\loan bu klasorde cmd acamiyorum")
        cmd = {"type": "user", "message": {"role": "user", "content": "<command-name>/model</command-name>"}}
        self.assertEqual(fb.session_name(self.transcript([cmd, u])), "C:\\work\\loan bu klasorde cmd acamiyorum")

    def test_window_name(self):
        self.assertEqual(fb.window_name('a "b" & c | 50% <x>'), "a b  c  50 x")


class Prompt(unittest.TestCase):
    def test_reads_both_claude_mds(self):
        proj = os.path.join(TMP, "proj")
        os.makedirs(os.path.join(TMP, ".claude"), exist_ok=True)
        os.makedirs(proj, exist_ok=True)
        open(os.path.join(TMP, ".claude", "CLAUDE.md"), "w").close()
        open(os.path.join(proj, "CLAUDE.md"), "w").close()
        p = fb.codex_prompt(None, proj, ["a subagent (review)"])
        self.assertIn(os.path.join(TMP, ".claude", "CLAUDE.md"), p)
        self.assertIn(os.path.join(proj, "CLAUDE.md"), p)
        self.assertIn("documentation rules", p)
        self.assertIn("stays open because it still runs a subagent (review)", p)
        self.assertNotIn('"', fb.codex_prompt('C:\\x "y" 100%.jsonl', proj))
        self.assertNotIn("%", fb.codex_prompt('C:\\x "y" 100%.jsonl', proj))


class Watch(unittest.TestCase):
    class Open:
        pid = None

        def poll(self):
            return None

    def test_first_answer_ends_the_watch(self):
        t = Thread("watch")
        t.add(ev("task_started") + json.dumps({"type": "response_item", "payload": {
            "type": "message", "role": "assistant", "content": []}}) + "\n")
        started = time.time()
        self.assertIsNone(fb.first_turn_error(t.path, t.rec["codex_from"], self.Open(), wait=5))
        self.assertLess(time.time() - started, 1)

    def test_error_before_any_answer(self):
        t = Thread("err")
        t.add(ev("task_started") + ev("error", message="ran out of room"))
        self.assertEqual(fb.first_turn_error(t.path, t.rec["codex_from"], self.Open(), wait=5), "ran out of room")


class Background(unittest.TestCase):
    def test_started_and_ended(self):
        ts = lambda s: "2026-11-21T12:00:%02d.000Z" % s
        started = __import__("datetime").datetime.fromisoformat("2026-11-21T12:00:10+00:00").timestamp()
        lines = [
            {"timestamp": ts(5), "c": "Command running in background with ID: old111. Output"},  # before the process
            {"timestamp": ts(20), "c": "Command running in background with ID: aaa111. Output"},
            {"timestamp": ts(21), "c": "Command running in background with ID: bbb222. Output"},
            {"timestamp": ts(22), "c": "Command running in background with ID: ccc333. Output"},
            {"timestamp": ts(30), "c": "<task-notification>\n<task-id>aaa111</task-id>"},
            {"timestamp": ts(31), "name": "TaskStop", "input": {"task_id": "ccc333"}},
        ]
        p = os.path.join(TMP, "bg.jsonl")
        with open(p, "w") as f:
            f.write("\n".join(json.dumps(l, separators=(",", ":")) for l in lines) + "\n")
        self.assertEqual(fb.background_ids(p, started), ["bbb222"])


if __name__ == "__main__":
    try:
        unittest.main(verbosity=1)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
