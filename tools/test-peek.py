# Unit tests for the opened card's readers in agent-panel.pyw: the last prompt of a session, the background commands
# still running, and for each piece of running work the prompt the user typed that set it off (up the transcript's
# parentUuid chain from the tool call that started it), grouped newest prompt first.
# Run: python -I tools/test-peek.py
# Every transcript here is made up, in a temporary folder; no real session is read. The .pyw is never imported
# (its one-instance mutex would raise the running panel): the readers and Panel.peek are taken out by ast.
import ast, datetime, json, os, re, shutil, tempfile, time, types, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
FT_EPOCH = 116444736000000000
CONSTS = ("PEEK_TAIL", "PEEK_MORE", "PEEK_CHARS", "BG_TAIL", "LATE", "BG_START", "BG_END", "KIND",
          "CHAIN_STEP", "CHAIN_MAX", "RUN_WAIT", "UUID_OF", "STAMP", "QUIET", "PASTED")
FUNCS = ("text_of", "prompt_words", "flat", "typed_prompt", "last_prompt", "bg_commands", "late_child", "subagents", "stamp", "back",
         "chain_prompt", "blocks", "call_of", "workflow_of", "shell_of", "settled", "wf_run", "agent_meta",
         "agent_trigger", "run_trigger", "flow_name", "arrange")


def load():
    with open(os.path.join(ROOT, "agent-panel.pyw"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    keep, peek = [], None
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name in FUNCS:
            keep.append(n)
        elif isinstance(n, ast.Assign) and any(getattr(t, "id", "") in CONSTS for t in n.targets):
            keep.append(n)
        elif isinstance(n, ast.ClassDef) and n.name == "Panel":
            peek = next(m for m in n.body if isinstance(m, ast.FunctionDef) and m.name == "peek")
    ns = {"os": os, "json": json, "re": re, "time": time, "datetime": datetime, "FT_EPOCH": FT_EPOCH}
    exec(compile(ast.Module(body=keep + [peek], type_ignores=[]), "agent-panel.pyw", "exec"), ns)
    return ns


def iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def write(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for o in lines:
            f.write(json.dumps(o, separators=(",", ":"), ensure_ascii=False) + "\n")


class Log:
    """A made-up transcript, each line chained to the one before by parentUuid, as Claude Code writes them."""

    def __init__(self, t):
        self.lines, self.t, self.n = [], t, 0

    def add(self, o, dt=1):
        self.n += 1
        self.t += dt
        o = dict({"parentUuid": self.lines[-1]["uuid"] if self.lines else None, "isSidechain": False}, **o)
        o.setdefault("uuid", "u%04d" % self.n)
        o.setdefault("timestamp", iso(self.t))
        self.lines.append(o)
        return o

    def user(self, text, **kw):
        return self.add(dict({"type": "user", "message": {"role": "user", "content": text}}, **kw))

    def call(self, tid, name, inp):
        return self.add({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": tid, "name": name, "input": inp}]}})

    def result(self, tid, text, extra=None):
        return self.add({"type": "user", "toolUseResult": extra or {"stdout": ""},
                         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid,
                                                                  "content": text}]}})

    def say(self, text):
        return self.add({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": text}]}})

    def notify(self, tid):  # a task notification: it starts a turn of its own
        return self.user("<task-notification>\n<task-id>x</task-id>\n<tool-use-id>%s</tool-use-id>\n"
                         "<status>completed</status>\n</task-notification>" % tid, origin={"kind": "task-notification"})


class Peek(unittest.TestCase):
    def setUp(self):
        self.ns = load()
        self.tmp = tempfile.mkdtemp(prefix="peek-test-")
        self.ns["CLAUDE"] = self.tmp
        self.now = time.time()
        self.started = self.now - 3600  # the session's process started an hour ago

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, name="S"):
        return os.path.join(self.tmp, "projects", "C--work-app", name + ".jsonl")

    def transcript(self, name, lines):
        write(self.path(name), lines)
        return self.path(name)

    def agent(self, aid, desc, sub="", **meta):
        """An agent's meta and transcript beside session S; returns its work item as subagents() makes it."""
        d = os.path.join(self.tmp, "projects", "C--work-app", "S", "subagents", sub)
        write(os.path.join(d, "agent-%s.jsonl" % aid), [
            {"parentUuid": None, "isSidechain": True, "type": "user", "uuid": aid + "-1", "timestamp": iso(self.now),
             "message": {"role": "user", "content": "the task of " + desc}}])
        with open(os.path.join(d, "agent-%s.meta.json" % aid), "w", encoding="utf-8") as f:
            json.dump(dict({"agentType": "general-purpose", "description": desc}, **meta), f)
        return d

    def panel(self, main, work):
        self.ns["procs"] = lambda: {}
        return types.SimpleNamespace(sessions=[{"id": "S", "path": main, "status": "busy", "bg": work}],
                                     live={"S": {"pid": 100, "started": self.started * 1000}}, peeks={})

    @staticmethod
    def shape(got):
        return [(g["prompt"], g["last"], [(w["kind"], w["what"]) for w in g["bg"]]) for g in got["groups"]]

    # ----- the last prompt -----
    def test_last_prompt_skips_tool_results_notifications_and_meta(self):
        t = self.now - 600
        log = Log(t)
        log.user("first question")
        p_id = log.user("Fix the failing\n\n   login   test")["uuid"]
        log.call("toolu_1", "Bash", {"command": "npm test"})
        log.result("toolu_1", "ok")
        log.notify("toolu_1")
        log.user("Caveat: the messages below", isMeta=True)
        log.user("<command-name>/model</command-name>")
        log.user("a subagent's own line", isSidechain=True)
        log.user("a message another session sent", origin={"kind": "peer"})
        log.user("Continue with the next step.", origin={"kind": "auto-continuation"})
        log.say("done")
        p = self.transcript("S", log.lines)
        cache = {}
        got = self.ns["last_prompt"](p, cache)
        self.assertEqual((got["id"], got["text"]), (p_id, "Fix the failing login test"))
        self.assertIn(p, cache)

    def test_a_prompt_typed_while_claude_works_is_a_prompt(self):
        log = Log(self.now - 60)
        log.user("first")
        log.call("toolu_1", "Bash", {"command": "sleep 60"})
        q = log.add({"type": "attachment", "attachment": {"type": "queued_command", "commandMode": "prompt",
                                                          "origin": {"kind": "human"},
                                                          "prompt": [{"type": "text", "text": "also the footer"}]}})
        log.add({"type": "attachment", "attachment": {"type": "queued_command", "commandMode": "task-notification",
                                                      "origin": {"kind": "task-notification"},
                                                      "prompt": "<task-notification>x</task-notification>"}})
        got = self.ns["last_prompt"](self.transcript("S", log.lines), {})
        self.assertEqual((got["id"], got["text"]), (q["uuid"], "also the footer"))

    def test_last_prompt_cached_until_the_file_changes(self):
        log = Log(self.now - 60)
        log.user("one")
        p = self.transcript("S", log.lines)
        cache = {}
        self.assertEqual(self.ns["last_prompt"](p, cache)["text"], "one")
        key = cache[p][0]
        cache[p] = (key, {"id": "x", "text": "from the cache", "at": 0})  # same size and time: not read again
        self.assertEqual(self.ns["last_prompt"](p, cache)["text"], "from the cache")
        log.user("two")
        write(p, log.lines)
        self.assertEqual(self.ns["last_prompt"](p, cache)["text"], "two")

    def test_prompt_in_text_blocks_bom_and_cut(self):
        log = Log(self.now)
        log.user([{"type": "image", "source": {}}, {"type": "text", "text": "\ufeffword " * 400}])
        got = self.ns["last_prompt"](self.transcript("S", log.lines), {})["text"]
        self.assertTrue(got.startswith("word word"))
        self.assertEqual(len(got), self.ns["PEEK_CHARS"])
        self.assertTrue(got.endswith("…"))

    def test_last_prompt_found_beyond_the_first_tail(self):
        self.ns["PEEK_TAIL"] = 2048
        log = Log(self.now - 5)
        log.user("the real prompt")
        for i in range(40):
            log.result("toolu_%d" % i, "x" * 200)
        self.assertEqual(self.ns["last_prompt"](self.transcript("S", log.lines), {})["text"], "the real prompt")

    # ----- background commands -----
    def test_commands_still_running(self):
        log = Log(self.started - 60)
        log.call("toolu_0", "Bash", {"command": "old", "description": "Old run", "run_in_background": True})
        log.result("toolu_0", "Command running in background with ID: b000. Output is being written")  # before start
        log.add({"type": "system"}, dt=200)
        log.call("toolu_1", "Bash", {"command": "npm run dev", "description": "Start the dev server",
                                     "run_in_background": True})
        log.result("toolu_1", "Command running in background with ID: b111. Output is being written")
        log.call("toolu_2", "Bash", {"command": "pytest -x", "description": "Run the tests", "run_in_background": True})
        log.result("toolu_2", "Command running in background with ID: b222. Output is being written")
        log.user("<task-notification>\n<task-id>b222</task-id>\n<status>completed</status></task-notification>")
        log.call("toolu_3", "PowerShell", {"command": "Get-Content log -Wait", "description": "Follow the log",
                                           "run_in_background": True})
        log.result("toolu_3", "Command running in background with ID: b333. Output is being written")
        log.call("toolu_4", "TaskStop", {"task_id": "b333"})
        log.call("toolu_5", "Agent", {"prompt": "audit", "description": "Audit", "run_in_background": True})
        log.result("toolu_5", "Async agent launched")
        got = self.ns["bg_commands"](self.transcript("S", log.lines), self.started, {})
        self.assertEqual([(w["what"], w["use"], w["kind"]) for w in got], [("Start the dev server", "toolu_1", "command")])

    def test_no_late_child_no_command(self):
        lc = self.ns["late_child"]
        table = {100: (1, "claude.exe"), 200: (100, "node.exe"), 300: (100, "bash.exe")}
        self.ns["born"] = lambda pid: {200: FT_EPOCH + int((self.started + 2) * 1e7),
                                       300: FT_EPOCH + int((self.started + 600) * 1e7)}.get(pid)
        self.assertTrue(lc(100, self.started, table))
        del table[300]  # only the MCP server born at the start is left
        self.assertFalse(lc(100, self.started, table))

    # ----- reading a transcript from its end back -----
    def test_back_gives_every_whole_line_newest_first_across_steps(self):
        self.ns["CHAIN_STEP"] = 64
        lines = [("line %d " % i + "x" * (i * 7 % 150)).encode() for i in range(60)]
        p = os.path.join(self.tmp, "b.jsonl")
        with open(p, "wb") as f:
            f.write(b"\n".join(lines) + b"\nstill being writ")
        offs = []
        with open(p, "rb") as f:
            got = list(self.ns["back"](f, os.path.getsize(p), 0))
            for off, line in got:
                f.seek(off)
                offs.append(f.read(len(line)) == line)
        self.assertEqual([l for _, l in got], lines[::-1])  # the line still being written is left out
        self.assertTrue(all(offs))
        with open(p, "rb") as f:  # from a line's start: that line is in
            start = got[10][0]
            self.assertEqual([l for _, l in self.ns["back"](f, os.path.getsize(p), start)], lines[:-11:-1] + [lines[-11]])

    # ----- which prompt set each piece off -----
    def test_one_prompt_started_three_subagents_and_a_workflow(self):
        ns = self.ns
        log = Log(self.started + 100)
        log.user("earlier prompt")
        log.say("ok")
        p_id = log.user("Audit the docs, in parallel")["uuid"]
        for i in range(3):
            log.call("toolu_a%d" % i, "Agent", {"description": "Audit part %d" % i, "prompt": "long task text",
                                                "run_in_background": True})
            log.result("toolu_a%d" % i, "Async agent launched successfully.")
        log.call("toolu_w", "Workflow", {"script": "export const meta = {}"})
        log.result("toolu_w", "Workflow launched in background. Run ID: wf_abc", {
            "status": "async_launched", "runId": "wf_abc", "taskType": "local_workflow"})
        log.say("Started them.")
        main = self.transcript("S", log.lines)
        for i in range(3):
            self.agent("a%d" % i, "Audit part %d" % i, toolUseId="toolu_a%d" % i)
        self.agent("w1", "audit:A", sub=os.path.join("workflows", "wf_abc"), inWorkflowRun=True)
        self.agent("w2", "audit:B", sub=os.path.join("workflows", "wf_abc"), inWorkflowRun=True)
        work = ns["subagents"]({"path": main, "id": "S"}, self.started - 1, {})
        self.assertEqual(len(work), 5)
        got = ns["peek"](self.panel(main, work), "S")
        self.assertEqual(len(got["groups"]), 1)
        g = got["groups"][0]
        self.assertEqual((g["prompt"], g["last"]), ("Audit the docs, in parallel", True))
        # the direct agents numbered among themselves, the Workflow's agents together under its name, numbered anew
        self.assertEqual([(w["kind"], w["what"], w.get("flow")) for w in g["bg"]], [
            ("Agent 1", "Audit part 0", None), ("Agent 2", "Audit part 1", None), ("Agent 3", "Audit part 2", None),
            ("Agent 1", "A", "audit"), ("Agent 2", "B", "audit")])
        self.assertEqual([w.get("wf") for w in g["bg"]], [None, None, None, "wf_abc", "wf_abc"])
        self.assertTrue(all(set(w) <= {"kind", "what", "since", "wf", "flow"} for w in g["bg"]))  # no task text
        self.assertEqual(ns["last_prompt"](main, {})["id"], p_id)

    def test_workflow_name_and_rows_without_a_shared_label_prefix(self):
        ns, cache = self.ns, {}
        rows = [{"kind": "subagent", "what": "Fix it", "since": 1, "wf": None},
                {"kind": "subagent", "what": "review:bugs", "since": 2, "wf": "wf_1"},
                {"kind": "run", "what": "tools", "since": 3, "wf": None},
                {"kind": "subagent", "what": "verify:x", "since": 4, "wf": "wf_1"},
                {"kind": "subagent", "what": "other:A", "since": 5, "wf": "wf_2"},
                {"kind": "command", "what": "Dev server", "since": 6, "wf": None},
                {"kind": "subagent", "what": "Second", "since": 7, "wf": None}]
        main = os.path.join(self.tmp, "S.jsonl")
        os.makedirs(os.path.join(self.tmp, "S", "workflows", "scripts"))
        open(os.path.join(self.tmp, "S", "workflows", "scripts", "site-pass-wf_1.js"), "w").close()
        got = ns["arrange"](rows, main, "S", cache)
        self.assertEqual([(w["kind"], w["what"], w.get("flow"), w.get("wf")) for w in got], [
            ("Agent 1", "Fix it", None, None),
            ("Agent 1", "review:bugs", "site-pass", "wf_1"), ("Agent 2", "verify:x", "site-pass", "wf_1"),
            ("claude -p", "tools", None, None),
            ("Agent 1", "A", "other", "wf_2"),
            ("Command", "Dev server", None, None),
            ("Agent 2", "Second", None, None)])
        self.assertEqual(cache, {"wf_1": "site-pass"})
        self.assertEqual(ns["flow_name"](main, "S", "wf_9", ["a", "b:c"], cache), ("", False))  # no script, no name

    def test_a_later_prompt_started_a_claude_p_run_and_groups_come_newest_first(self):
        ns = self.ns
        log = Log(self.started + 100)
        log.user("Audit the docs")
        log.call("toolu_a", "Agent", {"description": "Audit", "prompt": "x", "run_in_background": True})
        log.result("toolu_a", "Async agent launched successfully.")
        log.say("Started.")
        log.user("Now delegate the release notes", dt=300)
        log.call("toolu_ls", "Bash", {"command": "ls"})
        log.result("toolu_ls", "a b")
        call = log.call("toolu_r", "Bash", {"command": "cd ../tools && claude -p \"Summarise the release notes\"",
                                            "run_in_background": True})
        run_start = log.t + 1  # the process starts a moment after its call
        log.result("toolu_r", "Command running in background with ID: b777. Output is being written")
        log.say("Running.")
        log.user("What is the weather like?", dt=200)  # the last prompt: it started nothing still running
        log.say("Sunny.")
        main = self.transcript("S", log.lines)
        work = [dict(self.item(self.agent("a1", "Audit", toolUseId="toolu_a"), "a1", "Audit")),
                {"kind": "run", "what": "tools", "since": run_start, "pid": 400, "sid": "R", "cwd": "C:\\work\\tools"}]
        got = ns["peek"](self.panel(main, work), "S")
        self.assertEqual(self.shape(got), [
            ("What is the weather like?", True, []),
            ("Now delegate the release notes", False, [("claude -p", "tools")]),
            ("Audit the docs", False, [("Agent 1", "Audit")])])
        self.assertTrue(call)

    def item(self, d, aid, desc):
        p = os.path.join(d, "agent-%s.meta.json" % aid)
        with open(p, encoding="utf-8") as f:
            m = json.load(f)
        return {"kind": "subagent", "what": desc, "since": self.now, "file": p[:-10] + ".jsonl",
                "use": m.get("toolUseId"), "parent": m.get("parentAgentId"), "wf": self.ns["wf_run"](p)}

    def test_a_run_started_by_a_subagent_takes_that_agents_prompt(self):
        ns = self.ns
        log = Log(self.started + 100)
        log.user("Have an agent delegate it")
        log.call("toolu_a", "Agent", {"description": "Delegator", "prompt": "x", "run_in_background": True})
        log.result("toolu_a", "Async agent launched successfully.")
        log.user("Something else", dt=100)
        main = self.transcript("S", log.lines)
        d = self.agent("a1", "Delegator", toolUseId="toolu_a")
        alog = Log(self.started + 150)
        alog.user("the task", isSidechain=True)
        alog.call("toolu_x", "Bash", {"command": "claude -p 'do it'"})
        write(os.path.join(d, "agent-a1.jsonl"), alog.lines)
        work = [dict(self.item(d, "a1", "Delegator"), since=self.started + 140),
                {"kind": "run", "what": "w", "since": self.started + 153, "pid": 401, "sid": "R", "cwd": "C:\\w"}]
        got = ns["peek"](self.panel(main, work), "S")
        self.assertEqual(self.shape(got), [
            ("Something else", True, []),
            ("Have an agent delegate it", False, [("Agent 1", "Delegator"), ("claude -p", "w")])])

    def test_a_nested_agent_takes_its_root_agents_prompt(self):
        ns = self.ns
        log = Log(self.started + 100)
        log.user("Research it deeply")
        log.call("toolu_top", "Agent", {"description": "Lead researcher", "prompt": "x", "run_in_background": True})
        log.result("toolu_top", "Async agent launched successfully.")
        log.user("Meanwhile, fix the typo", dt=50)
        main = self.transcript("S", log.lines)
        self.agent("top", "Lead researcher", toolUseId="toolu_top")
        d = self.agent("kid", "Read the API docs", toolUseId="toolu_in_top", parentAgentId="top", spawnDepth=2)
        self.agent("grandkid", "One page", toolUseId="toolu_in_kid", parentAgentId="kid", spawnDepth=3)
        work = [self.item(d, "kid", "Read the API docs"), self.item(d, "grandkid", "One page")]
        got = ns["peek"](self.panel(main, work), "S")
        self.assertEqual(self.shape(got), [
            ("Meanwhile, fix the typo", True, []),
            ("Research it deeply", False, [("Agent 1", "Read the API docs"), ("Agent 2", "One page")])])

    def test_a_quote_reply_prompt_shows_what_was_typed_not_the_passages(self):
        ns = self.ns
        log = Log(self.started + 100)
        log.user(":1 yes, deploy it :3 no")
        log.add({"type": "attachment", "attachment": {"type": "hook_system_message", "content": "Quoted: :1 :3",
                                                      "hookEvent": "UserPromptSubmit"}})
        log.add({"type": "attachment", "attachment": {"type": "hook_additional_context", "hookEvent": "UserPromptSubmit",
                                                      "content": ["<quote n=\"1\"><passage>Shall I deploy?</passage>"
                                                                  "<reply>yes, deploy it</reply></quote>"]}})
        log.call("toolu_d", "Bash", {"command": "npm run deploy", "description": "Deploy", "run_in_background": True})
        log.result("toolu_d", "Command running in background with ID: b555. Output is being written")
        main = self.transcript("S", log.lines)
        ns["born"] = lambda pid: FT_EPOCH + int((self.started + 900) * 1e7)
        panel = self.panel(main, [])
        ns["procs"] = lambda: {100: (1, "claude.exe"), 500: (100, "bash.exe")}
        got = ns["peek"](panel, "S")
        self.assertEqual(self.shape(got), [(":1 yes, deploy it :3 no", True, [("Command", "Deploy")])])

    def test_a_task_notification_between_the_prompt_and_the_call(self):
        ns = self.ns
        log = Log(self.started + 100)
        log.user("Run the checks one after another")
        log.call("toolu_1", "Bash", {"command": "pytest", "run_in_background": True, "description": "Tests"})
        log.result("toolu_1", "Command running in background with ID: b1. Output")
        log.say("Waiting.")
        log.notify("toolu_1")  # the notification starts a new turn, which starts the next agent
        log.add({"type": "attachment", "attachment": {"type": "queued_command", "commandMode": "task-notification",
                                                      "origin": {"kind": "task-notification"},
                                                      "prompt": "<task-notification>y</task-notification>"}})
        log.call("toolu_a", "Agent", {"description": "Check the lint", "prompt": "x", "run_in_background": True})
        log.result("toolu_a", "Async agent launched successfully.")
        main = self.transcript("S", log.lines)
        d = self.agent("a1", "Check the lint", toolUseId="toolu_a")
        got = ns["peek"](self.panel(main, [self.item(d, "a1", "Check the lint")]), "S")
        self.assertEqual(self.shape(got), [("Run the checks one after another", True, [("Agent 1", "Check the lint")])])

    def test_a_request_blob_repeating_the_call_is_not_its_start(self):
        log = Log(self.started + 100)
        p = log.user("Start the agent")
        call = log.call("toolu_a", "Agent", {"description": "x", "prompt": "x"})
        log.result("toolu_a", "Async agent launched successfully.")
        log.lines.append({"type": "api-request-blob", "hash": "h", "message": call["message"]})  # no uuid, no parent
        done, got = self.ns["chain_prompt"](self.transcript("S", log.lines), self.ns["call_of"]("toolu_a"))
        self.assertEqual((done, got and got["id"]), (True, p["uuid"]))

    def test_the_chain_crosses_a_compaction(self):
        log = Log(self.started + 100)
        p = log.user("Rebuild the index")
        log.say("working")
        log.add({"type": "system", "subtype": "compact_boundary", "parentUuid": None,
                 "logicalParentUuid": log.lines[-1]["uuid"]})
        log.user("This session is being continued from a previous conversation that ran out of context.",
                 isCompactSummary=True)
        log.call("toolu_a", "Agent", {"description": "x", "prompt": "x"})
        main = self.transcript("S", log.lines)
        done, got = self.ns["chain_prompt"](main, self.ns["call_of"]("toolu_a"))
        self.assertTrue(done)
        self.assertEqual(got["id"], p["uuid"])

    def test_prompt_not_found_comes_last_and_answers_are_kept(self):
        ns = self.ns
        log = Log(self.started + 100)
        log.user("Start one agent")
        log.call("toolu_a", "Agent", {"description": "Known", "prompt": "x", "run_in_background": True})
        log.result("toolu_a", "Async agent launched successfully.")
        main = self.transcript("S", log.lines)
        d = self.agent("a1", "Known", toolUseId="toolu_a")
        self.agent("a2", "Lost", toolUseId="toolu_never_written")
        panel = self.panel(main, [self.item(d, "a1", "Known"), self.item(d, "a2", "Lost")])
        got = ns["peek"](panel, "S")
        self.assertEqual(self.shape(got), [("Start one agent", True, [("Agent 1", "Known")]),
                                           (None, False, [("Agent 1", "Lost")])])
        trig = panel.peeks["trig"]
        self.assertEqual(trig[("use", main, "toolu_a")][0], True)  # settled for good
        lost = trig[("use", main, "toolu_never_written")]
        self.assertEqual(lost, (False, os.path.getsize(main)))  # searched up to the end, on from there next time
        calls = []
        real = ns["chain_prompt"]
        ns["chain_prompt"] = lambda *a: calls.append(a) or real(*a)
        log.call("toolu_never_written", "Agent", {"description": "Lost", "prompt": "x"})  # written at last
        write(main, log.lines)
        got = ns["peek"](panel, "S")
        self.assertEqual(self.shape(got), [("Start one agent", True, [("Agent 1", "Known"), ("Agent 2", "Lost")])])
        self.assertEqual([c[2] for c in calls], [lost[1]])  # only the lost one was read again, from where it stopped

    def test_a_prompt_older_than_the_steps_is_still_found(self):
        self.ns["CHAIN_STEP"] = 512
        log = Log(self.started + 10)
        p = log.user("A long-running job")
        log.call("toolu_a", "Agent", {"description": "Long", "prompt": "x"})
        for i in range(200):
            log.result("toolu_x%d" % i, "y" * 100)
        main = self.transcript("S", log.lines)
        self.assertGreater(os.path.getsize(main), 40 * 512)
        done, got = self.ns["chain_prompt"](main, self.ns["call_of"]("toolu_a"))
        self.assertEqual((done, got["id"]), (True, p["uuid"]))
        self.ns["CHAIN_MAX"] = 4096  # past CHAIN_MAX: settled as not found, never read again
        self.assertEqual(self.ns["chain_prompt"](main, self.ns["call_of"]("toolu_a")), (False, os.path.getsize(main)))

    # ----- when an agent without "finished" has ended -----
    def run_agents(self, run, n, quiet=False):
        """n agents of Workflow run `run`, metas without "finished" as Claude Code leaves them; quiet: their
        transcripts last grew longer than QUIET ago."""
        for i in range(n):
            d = self.agent("%s%d" % (run, i), "audit:%s" % "ABCDE"[i], sub=os.path.join("workflows", run),
                           agentType="workflow-subagent", inWorkflowRun=True)
            if quiet:
                self.quiet(d, "%s%d" % (run, i))

    def quiet(self, d, aid):
        t = self.now - self.ns["QUIET"] - 600
        os.utime(os.path.join(d, "agent-%s.jsonl" % aid), (t, t))

    def running(self):
        main = self.path("S")
        return sorted(w["what"] for w in self.ns["subagents"]({"path": main, "id": "S"}, self.started - 1, {},
                                                                 self.now))

    def test_a_workflow_run_with_its_record_has_ended(self):
        self.run_agents("wf_done", 5)  # killed a moment ago: transcripts fresh, no "finished" in any meta
        os.makedirs(os.path.join(self.tmp, "projects", "C--work-app", "S", "workflows"))
        with open(os.path.join(self.tmp, "projects", "C--work-app", "S", "workflows", "wf_done.json"), "w") as f:
            json.dump({"runId": "wf_done", "status": "killed", "agentCount": 5}, f)
        self.run_agents("wf_live", 2)  # another run of the same session, still going: no record yet
        self.assertEqual(self.running(), ["audit:A", "audit:B"])

    def test_an_interrupted_run_without_a_record_ends_once_quiet(self):
        self.run_agents("wf_cut", 3, quiet=True)
        self.assertEqual(self.running(), [])

    def test_a_plain_agent_without_finished_ends_once_quiet(self):
        d = self.agent("stale", "Killed", toolUseId="toolu_1")
        self.quiet(d, "stale")
        self.agent("done", "Done", toolUseId="toolu_2", finished=True)
        self.agent("busy", "Busy", toolUseId="toolu_3")
        self.assertEqual(self.running(), ["Busy"])

    def test_a_quiet_agent_waiting_on_a_growing_one_runs(self):
        d = self.agent("top", "Lead", toolUseId="toolu_top")
        self.quiet(d, "top")
        self.agent("kid", "Reader", toolUseId="toolu_k", parentAgentId="top")  # grows: its parent waits on it
        d = self.agent("lone", "Alone", toolUseId="toolu_l")
        self.quiet(d, "lone")
        self.assertEqual(self.running(), ["Lead", "Reader"])

    def test_closed_session_shows_only_its_prompt(self):
        log = Log(self.started)
        log.user("Old work")
        main = self.transcript("S", log.lines)
        panel = types.SimpleNamespace(sessions=[{"id": "S", "path": main, "status": None, "bg": []}], live={}, peeks={})
        self.assertEqual(self.ns["peek"](panel, "S"), {"id": "S", "groups": [
            {"prompt": "Old work", "last": True, "bg": []}]})
        self.assertIsNone(self.ns["peek"](panel, "nope"))


# How Claude Code (2.1.278 to 2.1.332 seen) stores a prompt that holds a paste: the shapes below are copied from real
# transcripts with their words replaced.
PASTED_ONLY = '\n\n<pasted_content id="bd64">\nReference notes for later.\n\nsecond paragraph\n</pasted_content id="bd64">\n'
TYPED_THEN_PASTE = 'o zaman sana promptunu veriyorum: \n\n<pasted_content id="aa9a">\nYou are given a task\n</pasted_content id="aa9a">\n'
PASTE_THEN_TYPED = '\n\n<pasted_content id="f0e9">\nMy answers inside your reply of 21:06:\n\n> a quoted line\n</pasted_content id="f0e9">\n\n Buradan hareketle ne yazabiliriz?'
TWO_PASTES = '\n\n<pasted_content id="a1">\nfirst block\n</pasted_content id="a1">\n\n and \n\n<pasted_content id="b2">\n<div>second</div>\n</pasted_content id="b2">\n'
IMAGES_THEN_PASTE = [{"type": "text", "text": '[Image #1] [Image #2]\n\n<pasted_content id="190a">\nMy answers inside your reply of 23:10:\n> - red\n</pasted_content id="190a">\n\n For each answer say the colour.'},
                     {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="}}]


class Pasted(unittest.TestCase):
    """A prompt that starts with a paste is the user's prompt; its words are what the wrapper holds."""

    def setUp(self):
        self.ns = load()
        self.tmp = tempfile.mkdtemp(prefix="claude-plus-test-peek-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def typed(self, content, **kw):
        return self.ns["typed_prompt"](dict({"type": "user", "message": {"role": "user", "content": content}}, **kw))

    def test_the_words_inside_the_wrapper(self):
        self.assertEqual(self.typed(PASTED_ONLY), "Reference notes for later. second paragraph")
        self.assertEqual(self.typed(TYPED_THEN_PASTE), "o zaman sana promptunu veriyorum: You are given a task")
        self.assertEqual(self.typed(PASTE_THEN_TYPED),
                         "My answers inside your reply of 21:06: > a quoted line Buradan hareketle ne yazabiliriz?")
        self.assertEqual(self.typed(TWO_PASTES), "first block and <div>second</div>")
        self.assertEqual(self.typed(IMAGES_THEN_PASTE),
                         "[Image #1] [Image #2] My answers inside your reply of 23:10: > - red For each answer say the colour.")

    def test_system_lines_are_still_no_prompt(self):
        for t in ("<command-name>/model</command-name>\n<command-message>model</command-message>",
                  "<local-command-stdout>Set model to Opus</local-command-stdout>",
                  "<bash-input>dir</bash-input>", "<bash-stdout>x</bash-stdout><bash-stderr></bash-stderr>",
                  "<system-reminder>\nx\n</system-reminder>", "<task-notification>\n<task-id>a</task-id>",
                  '\n\n<pasted_content id="e0">\n\n</pasted_content id="e0">\n', "   "):
            self.assertIsNone(self.typed(t), t)
        self.assertIsNone(self.typed(PASTED_ONLY, isMeta=True))
        self.assertIsNone(self.typed(PASTED_ONLY, origin={"kind": "peer"}))

    def test_last_prompt_is_the_pasted_one(self):
        log = Log(time.time() - 600)
        log.user("the evening before")
        log.say("old reply")
        p_id = log.user(PASTE_THEN_TYPED)["uuid"]
        log.say("new reply")
        path = os.path.join(self.tmp, "S.jsonl")
        write(path, log.lines)
        got = self.ns["last_prompt"](path, {})
        self.assertEqual(got["id"], p_id)
        self.assertTrue(got["text"].startswith("My answers inside your reply of 21:06"))

    def test_a_pasted_prompt_queued_while_claude_works(self):
        log = Log(time.time() - 60)
        log.user("first")
        q = log.add({"type": "attachment", "attachment": {"type": "queued_command", "commandMode": "prompt",
                                                          "origin": {"kind": "human"}, "prompt": PASTED_ONLY}})
        path = os.path.join(self.tmp, "S.jsonl")
        write(path, log.lines)
        self.assertEqual(self.ns["last_prompt"](path, {})["id"], q["uuid"])


class Recall(unittest.TestCase):
    """prompt-recall.py keeps what a pasted prompt holds for the status line, never the tags."""

    def run_hook(self, prompt):
        import subprocess, sys
        home = tempfile.mkdtemp(prefix="claude-plus-test-recall-")
        try:
            env = dict(os.environ, USERPROFILE=home, HOME=home)
            subprocess.run([sys.executable, "-I", os.path.join(ROOT, "prompt-recall.py")], env=env, timeout=30,
                           input=json.dumps({"session_id": "S", "prompt": prompt}).encode("utf-8"), check=True)
            p = os.path.join(home, ".claude", "state", "context", "S.prompt.json")
            if not os.path.exists(p):
                return None
            with open(p, encoding="utf-8") as f:
                return json.load(f)["prompt"]
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_pasted_prompts(self):
        self.assertEqual(self.run_hook(PASTED_ONLY), "Reference notes for later.\n\nsecond paragraph")
        self.assertEqual(self.run_hook(PASTE_THEN_TYPED),
                         "My answers inside your reply of 21:06:\n\n> a quoted line\n\n Buradan hareketle ne yazabiliriz?")
        self.assertIsNone(self.run_hook("<task-notification>\n<task-id>a</task-id>"))
        self.assertIsNone(self.run_hook("Keep-alive from claude-plus: reply only `ok`, do nothing else."))
        self.assertEqual(self.run_hook("plain typed prompt"), "plain typed prompt")


if __name__ == "__main__":
    unittest.main(verbosity=1)
