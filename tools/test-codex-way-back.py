# Scenario tests for the Agent Panel's Codex way back (agent-panel.pyw step_return(), close_old(), launch_back()):
# the old Claude window closing once Codex runs (never while it still runs work), the session coming back to
# Claude by itself when the Codex window closes (reset or not), the incremental carry, a Claude window held by hand.
# Run: python -I tools/test-codex-way-back.py
# The .pyw is never imported (its one-instance mutex would raise the running panel): the methods are taken out by
# ast and run against a fake process table; nothing is started, ended or shown. glm-fallback.py is loaded with its
# state on a temp folder.
import ast, glob, importlib.util, json, os, shutil, tempfile, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
TMP = tempfile.mkdtemp(prefix="cpfa-back-")


def load_fb():
    spec = importlib.util.spec_from_file_location("glm_fallback", os.path.join(ROOT, "glm-fallback.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.HOME = TMP
    m.RETURN_DIR = os.path.join(TMP, "glm-return")
    os.makedirs(m.RETURN_DIR, exist_ok=True)
    return m


FB = load_fb()


def put(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def jload(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def jdump(o, path):
    put(path, json.dumps(o))


def panel_ns():
    with open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    funcs = ("read_record", "save_record", "console_alive", "runs_under", "short", "folder_exists")
    meths = ("step_return", "close_old", "launch_back", "agent_idle", "asker_alive", "end_asker")
    keep = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in funcs]
    for c in tree.body:
        if isinstance(c, ast.ClassDef) and c.name == "Panel":
            keep += [m for m in c.body if isinstance(m, ast.FunctionDef) and m.name in meths]
    ns = {"os": os, "time": time, "json": json, "glob": glob, "sys": __import__("sys"), "subprocess": None,
          "START_WAIT": 90, "IDLE_FOR": 4, "ASK_AGAIN": 1800, "CODEX_EXES": ("codex.exe", "node.exe"),
          "VIA": {"glm": "GLM", "codex": "Codex"}, "FALLBACK": (), "CREATE_NO_WINDOW": 0, "kernel32": None}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "agent-panel.pyw", "exec"), ns)
    return ns


class World:
    """The fake machine: processes {pid: (parent, exe)}, which are alive, session holders, and what was ended."""
    def __init__(self):
        self.table, self.dead, self.holders, self.ended, self.launched = {}, set(), [], [], []
        self.ns = ns = panel_ns()
        ns["alive"] = lambda pid: pid in self.table and pid not in self.dead
        ns["born"] = lambda pid: 1 if pid in self.table else None
        ns["session_procs"] = lambda sid: [h for h in self.holders if h["pid"] not in self.dead]
        ns["procs"] = lambda: {p: v for p, v in self.table.items() if p not in self.dead}
        ns["end_tree"] = lambda root, par: self.ended.append(root) or self.kill_tree(root)
        ns["console_root"] = lambda pid, par: self.table[pid][0]  # the tab's shell
        ns["fallback"] = lambda: FB

        class P:
            pass
        for k in ("step_return", "close_old", "launch_back", "agent_idle", "asker_alive", "end_asker"):
            setattr(P, k, ns[k])
        self.panel = p = P()
        p.idle_since, p.find = {}, lambda sid: {"title": "demo project", "cwd": TMP, "distro": None, "path": None}

        class Proc:
            def __init__(s, pid):
                s.pid = pid

        def resume(rec, title, model, prompt=None):
            self.launched.append({"title": title, "model": model, "prompt": prompt})
            self.table[700] = (1, "claude.exe")
            return Proc(700)
        ns["resume_claude"] = resume

    def kill_tree(self, root):
        todo = [root]
        while todo:
            p = todo.pop()
            self.dead.add(p)
            todo += [k for k, (pp, _) in self.table.items() if pp == p and k not in self.dead]

    def step(self, path):
        rec = self.ns["read_record"](path)
        par = {p: v[0] for p, v in self.ns["procs"]().items()}
        self.panel.step_return(path, rec, par, None)
        return self.ns["read_record"](path)


class Base(unittest.TestCase):
    def setUp(self):
        self.w = w = World()
        # old Claude window: cmd 100 -> claude 101 (session S); Codex window: cmd 200 -> node 201 -> codex 202
        w.table.update({100: (1, "cmd.exe"), 101: (100, "claude.exe"), 200: (1, "cmd.exe"), 201: (200, "node.exe"),
                        202: (201, "codex.exe")})
        w.holders = [{"pid": 101, "status": "idle"}]
        self.sid = "S%d" % (time.time() * 1e6)
        self.transcript = os.path.join(TMP, self.sid + ".jsonl")
        put(self.transcript, '{"type":"assistant","message":{"model":"claude-opus-5-5"}}\n')
        self.thread = os.path.join(TMP, self.sid + "-thread.jsonl")
        put(self.thread, '{"type":"session_meta"}\n')
        self.path = os.path.join(FB.RETURN_DIR, self.sid + ".json")
        rec = {"session": self.sid, "cwd": TMP, "folder": "tmp", "via": "codex", "console": 200, "console_born": 1,
               "orig_pid": 101, "skip": True, "at": time.time() - 60, "transcript": self.transcript,
               "resets_at": time.time() + 600, "reset_from": "five_hour", "codex_thread": "T" + self.sid,
               "codex_file": self.thread, "codex_from": os.path.getsize(self.thread)}
        jdump(rec, self.path)
        FB.busy_work = lambda sid, pid, transcript: []

    def codex_turn(self, user, reply):
        with open(self.thread, "a") as f:
            for t, m in (("task_started", None), ("user_message", user), ("agent_message", reply),
                         ("task_complete", None)):
                f.write(json.dumps({"type": "event_msg", "payload": {"type": t, "message": m}}) + "\n")
        os.utime(self.thread, (time.time() - 10, time.time() - 10))


class OldWindow(Base):
    def test_closes_once_codex_runs_and_the_hook_ended(self):
        rec = self.w.step(self.path)
        self.assertTrue(rec["agent_seen"])
        self.assertEqual(self.w.ended, [])  # the hook is still watching Codex's first turn
        FB.mark_watched(self.sid, "T" + self.sid)
        rec = self.w.step(self.path)
        self.assertEqual(self.w.ended, [100])  # the old tab's shell: its whole tree
        self.assertTrue(rec["old_closed"])
        self.assertNotIn(200, self.w.dead)  # Codex untouched

    def test_kept_while_work_runs(self):
        FB.mark_watched(self.sid, "T" + self.sid)
        FB.busy_work = lambda sid, pid, transcript: ["a subagent (review)"]
        self.w.step(self.path)
        rec = self.w.step(self.path)
        self.assertEqual(self.w.ended, [])
        self.assertEqual(rec["old_work"], ["a subagent (review)"])
        FB.busy_work = lambda sid, pid, transcript: []
        rec = self.w.step(self.path)
        self.assertEqual(self.w.ended, [100])

    def test_not_closed_while_busy(self):
        FB.mark_watched(self.sid, "T" + self.sid)
        self.w.holders = [{"pid": 101, "status": "busy"}]
        self.w.step(self.path)
        self.assertEqual(self.w.ended, [])


    def test_never_closed_once_typed_into(self):
        self.w.step(self.path)  # idle seen
        self.w.holders = [{"pid": 101, "status": "busy"}]  # the user typed into it (after the reset, say)
        self.w.step(self.path)
        self.w.holders = [{"pid": 101, "status": "idle"}]
        FB.mark_watched(self.sid, "T" + self.sid)
        rec = self.w.step(self.path)
        self.assertEqual(self.w.ended, [])
        self.assertTrue(rec["orig_used"])
        self.w.kill_tree(200)  # and Codex closing does not start a second Claude window beside it
        self.w.step(self.path)
        self.assertEqual(self.w.launched, [])


class CodexCloses(Base):
    def test_back_to_claude_by_itself_before_the_reset(self):
        FB.mark_watched(self.sid, "T" + self.sid)
        self.w.step(self.path)  # Codex seen; the old window closes
        self.codex_turn("first", "one")
        self.codex_turn("option two is fine", "Option two noted")
        self.w.kill_tree(200)  # the user closes the Codex window
        rec = self.w.step(self.path)
        self.assertEqual(len(self.w.launched), 1)
        prompt = self.w.launched[0]["prompt"]
        self.assertIn("option two is fine", prompt)
        self.assertIn("first", prompt)
        self.assertEqual(self.w.launched[0]["model"], "claude-opus-5-5")
        self.assertTrue(FB.quiet(self.sid))  # its first prompt meets the limit: no way-out window
        self.assertTrue(rec["auto_back"])
        # Claude holds the session: the record goes
        self.w.holders.append({"pid": 700, "status": "busy"})
        self.w.table[701] = (700, "claude.exe")
        self.w.holders[-1]["pid"] = 700
        self.w.step(self.path)
        self.assertFalse(os.path.exists(self.path))

    def test_no_quiet_after_the_reset(self):
        rec = jload(self.path)
        rec["resets_at"] = time.time() - 5
        jdump(rec, self.path)
        FB.mark_watched(self.sid, "T" + self.sid)
        self.w.step(self.path)
        self.codex_turn("q", "a")
        self.w.kill_tree(200)
        self.w.step(self.path)
        self.assertEqual(len(self.w.launched), 1)
        self.assertFalse(FB.quiet(self.sid))

    def test_old_window_with_work_waits_for_a_prompt_there(self):
        FB.mark_watched(self.sid, "T" + self.sid)
        FB.busy_work = lambda sid, pid, transcript: ["1 background command"]
        self.w.step(self.path)
        self.codex_turn("q", "a")
        self.w.kill_tree(200)
        rec = self.w.step(self.path)
        self.assertEqual(self.w.launched, [])  # the old window holds the session: no second Claude window
        self.assertTrue(os.path.exists(self.path))
        self.assertTrue(FB.pending(rec))  # what Codex did waits for the --carry hook

    def test_once_only(self):
        FB.mark_watched(self.sid, "T" + self.sid)
        self.w.step(self.path)
        self.codex_turn("q", "a")
        self.w.kill_tree(200)
        self.w.step(self.path)
        self.w.dead.add(700)  # that Claude never took the session
        rec = jload(self.path)
        rec["switch_at"] = time.time() - 100
        jdump(rec, self.path)
        rec = self.w.step(self.path)
        self.assertNotIn("claude_cmd", rec)
        self.assertTrue(FB.pending(rec))  # the carry was given back
        self.w.step(self.path)
        self.assertEqual(len(self.w.launched), 1)  # not started again by itself; the card offers Back to Claude


class HeldByHand(Base):
    def test_hand_resume_while_codex_open_then_close(self):
        self.w.step(self.path)
        self.codex_turn("early", "e")
        span = FB.take_carry(jload(self.path), "hook:SessionStart")  # the resume carried this much
        self.w.holders.append({"pid": 300, "status": "idle"})
        self.w.table[300] = (1, "claude.exe")
        self.w.step(self.path)
        self.assertTrue(os.path.exists(self.path))  # Codex still open: kept
        self.codex_turn("late", "l")
        self.w.kill_tree(200)
        rec = self.w.step(self.path)
        self.assertTrue(os.path.exists(self.path))  # the late turn is not carried yet
        self.assertEqual(self.w.launched, [])
        span2 = FB.take_carry(rec, "hook:UserPromptSubmit")
        self.assertIn("late", FB.codex_record(rec, span2))
        self.assertNotIn("early", FB.codex_record(rec, span2))
        self.w.step(self.path)
        self.assertFalse(os.path.exists(self.path))


class Glm(Base):
    """The same rules for GLM: its window is cmd 200 -> glm's cmd 201 -> claude 202, holding the same session."""
    def setUp(self):
        super().setUp()
        self.w.table.update({201: (200, "cmd.exe"), 202: (201, "claude.exe")})
        self.w.holders = [{"pid": 101, "status": "idle"}, {"pid": 202, "status": "busy"}]
        rec = jload(self.path)
        for k in ("codex_thread", "codex_file", "codex_from"):
            rec.pop(k)
        rec["via"] = "glm"
        jdump(rec, self.path)

    def test_old_window_closes_once_glm_holds_it(self):
        rec = self.w.step(self.path)
        self.assertTrue(rec["agent_seen"])
        self.assertEqual(self.w.ended, [])  # the hook has not ended yet
        FB.mark_watched(self.sid, None)
        rec = self.w.step(self.path)
        self.assertEqual(self.w.ended, [100])
        self.assertNotIn(202, self.w.dead)  # GLM untouched
        self.assertTrue(os.path.exists(self.path))

    def test_kept_while_work_runs(self):
        FB.mark_watched(self.sid, None)
        FB.busy_work = lambda sid, pid, transcript: ["a Claude Code run in tools"]
        rec = self.w.step(self.path)
        self.assertEqual(self.w.ended, [])
        self.assertEqual(rec["old_work"], ["a Claude Code run in tools"])

    def test_back_to_claude_when_glm_closes(self):
        FB.mark_watched(self.sid, None)
        self.w.step(self.path)
        self.w.kill_tree(200)  # the user closes the GLM window
        rec = self.w.step(self.path)
        self.assertEqual(len(self.w.launched), 1)
        self.assertIsNone(self.w.launched[0]["prompt"])  # GLM wrote into the transcript itself
        self.assertEqual(self.w.launched[0]["model"], "claude-opus-5-5")  # never a plain resume after GLM
        self.assertFalse(FB.quiet(self.sid))  # no prompt is sent, so no limit stop to keep quiet
        self.assertTrue(rec["auto_back"])
        self.w.holders.append({"pid": 700, "status": "idle"})
        self.w.step(self.path)
        self.assertFalse(os.path.exists(self.path))

    def test_glm_closed_while_old_window_works_waits_then_goes_back(self):
        FB.mark_watched(self.sid, None)
        FB.busy_work = lambda sid, pid, transcript: ["1 background command"]
        self.w.step(self.path)
        self.w.kill_tree(200)
        self.w.step(self.path)
        self.assertEqual(self.w.launched, [])  # the old window still works: no second Claude window yet
        FB.busy_work = lambda sid, pid, transcript: []
        self.w.step(self.path)
        self.assertEqual(self.w.ended[-1], 100)
        self.assertEqual(len(self.w.launched), 1)

    def test_left_open_after_the_reset_is_not_closed_or_resumed(self):
        rec = jload(self.path)
        rec["resets_at"] = time.time() - 5
        jdump(rec, self.path)
        FB.mark_watched(self.sid, None)
        self.w.holders[1]["status"] = "idle"
        for _ in range(3):
            self.w.step(self.path)
        self.assertNotIn(202, self.w.dead)
        self.assertEqual(self.w.launched, [])  # the way-back question's turn, as before


class GlmTitle(unittest.TestCase):
    """GLM's pinned title "✳ <name>" reads as a session terminal to the paste key and to the panel's title checks."""
    def test_title_matches(self):
        spec = importlib.util.spec_from_file_location("pp", os.path.join(ROOT, "agent-panel-paste.py"))
        pp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pp)
        title = FB.GLYPH + FB.window_name("demo project")
        self.assertEqual(title, "✳ demo project")
        self.assertTrue(pp.claude_title(title))
        self.assertTrue(pp.wanted(5, {5}, title))
        with open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        ns = {"re": __import__("re")}
        keep = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("shown_name", "tab_session")]
        exec(compile(ast.Module(body=keep, type_ignores=[]), "agent-panel.pyw", "exec"), ns)
        s = {"status": "idle", "pid": 7, "name": "demo project"}
        self.assertIs(ns["tab_session"](5, title, [s], lambda o: 5), s)  # the copy and the band find the session
        self.assertIn(s["name"], title)  # in_front() and focus_session(): the name is in the title, no Ctrl+Tab


if __name__ == "__main__":
    try:
        unittest.main(verbosity=1)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
