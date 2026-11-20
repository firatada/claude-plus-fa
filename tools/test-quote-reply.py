# Unit tests for quote-reply.py (reply to one passage or several): the ":N" marker parser (code excluded), the
# hook's answer for a session's live list, an empty one and none, the per-session lists' rules (reset by any prompt
# in that session, never another's), and the Agent Panel's attribution of a copy to one session (tab_session,
# copied_in, taken from agent-panel.pyw by ast so the panel is never imported). Run: python -I tools/test-quote-reply.py
# Uses a temporary folder for the list, reset and USED files and a mutex of its own; never touches the clipboard
# (clip() is replaced where the no-list path needs it), any window, or the real ~/.claude.
import ast, ctypes, importlib.util, json, os, re, shutil, sys, tempfile, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("quote_reply", os.path.join(HERE, "..", "quote-reply.py"))
qr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qr)

MUTEX = "Local\\claude-plus-fa-test-%d" % os.getpid()
A, B = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa", "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"


def pyw_functions(*names):
    """Module-level functions of agent-panel.pyw, run alone (importing it would trip its one-instance mutex)."""
    with open(os.path.join(HERE, "..", "agent-panel.pyw"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    keep = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    ns = {"re": re}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "agent-panel.pyw", "exec"), ns)
    return ns
kernel32 = ctypes.windll.kernel32
kernel32.CreateMutexW.restype = ctypes.c_void_p


def parse(prompt, known=(1, 2, 3, 4)):
    return qr.parse(prompt, set(known))


class Parser(unittest.TestCase):
    def test_one_line_several(self):
        a, u, bare = parse(":1 yes, fix it :3 no :4 your call")
        self.assertEqual(a, [(1, "yes, fix it"), (3, "no"), (4, "your call")])
        self.assertEqual((u, bare), ([], False))

    def test_inside_words_and_times_never_count(self):
        for p in ("a:1b", "x:1 y", ":1b yes", "at 10:20 we met", "see C:1 now", "ratio 3:2", "(:1)", ":1,"):
            a, u, _ = parse(p)
            self.assertEqual((a, u), ([], []), p)

    def test_time_inside_a_reply_stays_text(self):
        a, _, _ = parse(":2 meet at 10:20 :1 ok")
        self.assertEqual(a, [(2, "meet at 10:20"), (1, "ok")])

    def test_multi_line_replies(self):
        a, _, _ = parse(":1 first line\nsecond line\n:2 other\n\nmore")
        self.assertEqual(a, [(1, "first line\nsecond line"), (2, "other\n\nmore")])

    def test_marker_at_line_start_and_after_tab(self):
        a, _, _ = parse("thanks\n:3 yes\tand\t:4 no")
        self.assertEqual(a, [(3, "yes\tand"), (4, "no")])

    def test_unknown_stays_plain_text(self):
        a, u, _ = parse(":1 yes :7 no", known=(1, 2))
        self.assertEqual((a, u), ([(1, "yes :7 no")], ["7"]))

    def test_unknown_alone(self):
        a, u, bare = parse(":9 hello", known=(1,))
        self.assertEqual((a, u, bare), ([], ["9"], False))

    def test_empty_list(self):
        a, u, bare = parse(":1 yes :2 no", known=())
        self.assertEqual((a, u, bare), ([], ["1", "2"], False))

    def test_marker_with_nothing_shares_the_next_reply(self):
        a, _, _ = parse(":1 :3 no")
        self.assertEqual(a, [(1, "no"), (3, "no")])

    def test_last_marker_empty(self):
        a, _, _ = parse(":1 yes :2")
        self.assertEqual(a, [(1, "yes"), (2, "")])

    def test_number_twice_joins(self):
        a, _, _ = parse(":1 yes :2 no :1 and also this")
        self.assertEqual(a, [(1, "yes\nand also this"), (2, "no")])

    def test_marker_at_end_of_prompt(self):
        a, _, _ = parse("all good :2")
        self.assertEqual(a, [(2, "")])

    def test_bare(self):
        self.assertTrue(parse(": my answer")[2])
        self.assertTrue(parse("  :my answer")[2])
        self.assertTrue(parse(":")[2])
        self.assertFalse(parse(":1 my answer")[2])
        self.assertFalse(parse("my : answer")[2])

    def test_preamble_is_not_a_reply(self):
        a, _, _ = parse("Thanks. :2 yes")
        self.assertEqual(a, [(2, "yes")])

    def test_inline_code_is_not_a_marker(self):
        # the prompt that attached a stray passage as item 3: the doc example quoted in backticks
        p = 'Parser fix: a prompt quoting the doc example "`:1 yes :3 no`" in backticks attached a stray passage.'
        self.assertEqual(parse(p)[:2], ([], []))
        a, u, _ = parse(":2 see `:1 yes :3 no` there")
        self.assertEqual((a, u), ([(2, "see `:1 yes :3 no` there")], []))
        self.assertEqual(parse("``x ` :1 y`` :3 real")[0], [(3, "real")])  # a double-tick span holds a single tick

    def test_fenced_block_is_not_a_marker(self):
        p = ":1 yes\n```\n:2 inside a fence\n```\n~~~text\n:3 in tildes\n~~~\n:4 after"
        a, u, _ = parse(p)
        self.assertEqual([n for n, _ in a], [1, 4])
        self.assertIn(":2 inside a fence", a[0][1])
        self.assertEqual(parse("```\n:1 never closed")[0], [])

    def test_unmatched_backtick_is_plain(self):
        a, _, _ = parse("a ` tick :1 yes")
        self.assertEqual(a, [(1, "yes")])
        a, _, _ = parse("``` not a fence when inline? :2 no")  # a line opening with ``` opens a fence; never closed, it runs to the end
        self.assertEqual(a, [])


class Hook(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.saved = qr.LIST, qr.USED, qr.RESETS, qr.clip
        qr.LIST, qr.USED = os.path.join(self.dir, "list.json"), os.path.join(self.dir, "used.json")
        qr.RESETS = os.path.join(self.dir, "resets")
        qr.clip = lambda tries=20: (_ for _ in ()).throw(AssertionError("clipboard read with a live list"))
        self.mutex = kernel32.CreateMutexW(None, False, MUTEX)
        self.now = time.time()

    def tearDown(self):
        qr.LIST, qr.USED, qr.RESETS, qr.clip = self.saved
        kernel32.CloseHandle(ctypes.c_void_p(self.mutex))
        shutil.rmtree(self.dir)

    def write(self, texts, on=True, mutex=MUTEX, gen=7, sid=A, others=None):
        """The panel's file: texts {n: passage} for session sid, others {sid: {n: passage}} for other sessions."""
        def lst(t, g):
            return {"gen": g, "next": max(t or {0: 0}) + 1, "name": "x",
                    "items": [{"n": n, "seq": 100 + n, "at": self.now - 5, "text": v, "more": 0} for n, v in t.items()]}
        lists = {k: lst(t, 99) for k, t in (others or {}).items()}
        if texts is not None:
            lists[sid] = lst(texts, gen)
        qr.write_json(qr.LIST, {"on": on, "mutex": mutex, "lists": lists})

    def reset_of(self, sid=A):
        with open(qr.reset_path(sid), encoding="utf-8") as f:
            return {k: v for k, v in json.load(f).items() if k != "at"}

    def used(self):
        with open(qr.USED, encoding="utf-8") as f:
            return json.load(f)

    def ctx(self, out):
        return out["hookSpecificOutput"]["additionalContext"]

    def test_no_colon_no_output_but_resets(self):
        self.write({1: "Q1", 2: "Q2"})
        self.assertIsNone(qr.reply("just a prompt", A))
        self.assertEqual(self.reset_of(), {"gen": 7, "upto": 2})  # any prompt resets the session's list
        self.assertFalse(os.path.exists(qr.USED))

    def test_time_in_prompt_no_output(self):
        self.write({1: "Q1"})
        self.assertIsNone(qr.reply("at 10:20 it broke", A))

    def test_empty_list_writes_no_reset(self):
        self.write({})
        self.assertIsNone(qr.reply("hello", A))
        self.assertFalse(os.path.exists(qr.reset_path(A)))

    def test_numbered(self):
        self.write({1: "Question one?", 2: "Question two?", 3: "Question three?", 4: "Question four?"})
        out = qr.reply(":1 yes, fix it :3 no :4 your call", A)
        self.assertEqual(out["systemMessage"], "Quoted: :1 :3 :4")
        c = self.ctx(out)
        self.assertIn('<quote n="1">\n<passage>\nQuestion one?\n</passage>\n<reply>\nyes, fix it\n</reply>\n</quote>', c)
        self.assertIn('<quote n="3">', c)
        self.assertIn("<reply>\nyour call\n</reply>", c)
        self.assertNotIn("Question two?", c)  # unanswered: not sent
        self.assertEqual(self.reset_of(), {"gen": 7, "upto": 4})

    def test_reads_only_its_own_session(self):
        self.write({1: "A one"}, others={B: {1: "B one", 2: "B two"}})
        out = qr.reply(":1 ok :2 no", A)
        self.assertIn("A one", self.ctx(out))
        self.assertNotIn("B one", self.ctx(out))
        self.assertEqual(out["systemMessage"], "Quoted: :1 (:2 matched nothing)")
        self.assertFalse(os.path.exists(qr.reset_path(B)))  # B's list is never reset by a prompt in A
        out = qr.reply(":2 yes", B)
        self.assertIn("B two", self.ctx(out))
        self.assertEqual(self.reset_of(B), {"gen": 99, "upto": 2})

    def test_session_without_a_list(self):
        self.write(None, others={B: {1: "B one"}})
        self.assertEqual(qr.reply(":1 yes", A), {"systemMessage": "Reply: :1 matched nothing (no selection is listed)"})
        self.assertEqual(qr.reply(": hi", A), {"systemMessage": "Reply: nothing to quote (no selection is listed)"})
        self.assertFalse(os.path.exists(qr.reset_path(A)))

    def test_numbered_with_unknown(self):
        self.write({1: "Q1", 2: "Q2"})
        out = qr.reply(":1 yes :5 no", A)
        self.assertEqual(out["systemMessage"], "Quoted: :1 (:5 matched nothing)")
        self.assertIn("<reply>\nyes :5 no\n</reply>", self.ctx(out))

    def test_unknown_only(self):
        self.write({1: "Q1"})
        out = qr.reply(":5 hello", A)
        self.assertEqual(out, {"systemMessage": "Reply: :5 matched nothing"})
        self.assertEqual(self.reset_of(), {"gen": 7, "upto": 1})  # still a prompt in that session: reset

    def test_marker_in_code_resets_but_quotes_nothing(self):
        self.write({1: "Q1", 3: "Q3"})
        self.assertIsNone(qr.reply('the example "`:1 yes :3 no`" in backticks', A))
        self.assertEqual(self.reset_of(), {"gen": 7, "upto": 3})

    def test_empty_list_marker_quotes_nothing(self):
        self.write({})
        out = qr.reply(":1 yes", A)
        self.assertEqual(out, {"systemMessage": "Reply: :1 matched nothing (no selection is listed)"})
        self.assertFalse(os.path.exists(qr.USED))

    def test_empty_list_bare(self):
        self.write({})
        self.assertEqual(qr.reply(": hello", A), {"systemMessage": "Reply: nothing to quote (no selection is listed)"})

    def test_bare_one_item_as_before(self):
        self.write({3: "The one passage"})
        out = qr.reply(": agreed", A)
        self.assertEqual(out["systemMessage"], "Quoted: The one passage")
        self.assertTrue(self.ctx(out).startswith("The user is replying to this passage"))
        self.assertIn("<quote>\nThe one passage\n</quote>", self.ctx(out))
        self.assertEqual(self.reset_of()["upto"], 3)

    def test_bare_several_sends_all(self):
        self.write({1: "Q1", 2: "Q2", 4: "Q4"})  # 3 was dropped: numbers are not shifted
        out = qr.reply(": yes", A)
        self.assertEqual(out["systemMessage"],
                         "Quoted all 3 (:1 :2 :4): the reply was not numbered, so Claude may ask which one is meant")
        c = self.ctx(out)
        self.assertIn("ask the user which item they mean", c)
        for n in (1, 2, 4):
            self.assertIn('<quote n="%d">\n<passage>\nQ%d\n</passage>\n</quote>' % (n, n), c)
        self.assertNotIn("<reply>", c)

    def test_bare_plus_marker_is_numbered(self):
        self.write({1: "Q1", 2: "Q2"})
        out = qr.reply(": thanks :2 no", A)
        self.assertEqual(out["systemMessage"], "Quoted: :2")

    def test_cut_passage_noted(self):
        self.write({1: "x" * 4000})
        d = qr.load(qr.LIST)
        d["lists"][A]["items"][0]["more"] = 2000
        qr.write_json(qr.LIST, d)
        self.assertIn("cut to its first 4000 of 6000 characters", self.ctx(qr.reply(":1 ok", A)))

    def test_panel_gone_falls_back_to_clipboard(self):
        self.write({1: "Q1", 2: "Q2"}, mutex="Local\\claude-plus-fa-test-nobody")
        qr.clip = lambda tries=20: (555, "From the clipboard", False)
        out = qr.reply(": yes", A)
        self.assertEqual(out["systemMessage"], "Quoted: From the clipboard")
        self.assertEqual(self.used()["seq"], 555)
        self.assertFalse(os.path.exists(qr.reset_path(A)))
        self.assertEqual(qr.reply(": again", A)["systemMessage"].split(" (")[0], "Reply: nothing to quote")

    def test_strip_off_falls_back(self):
        self.write({1: "Q1"}, on=False)
        qr.clip = lambda tries=20: (556, "Clip text", False)
        self.assertEqual(qr.reply(": yes", A)["systemMessage"], "Quoted: Clip text")

    def test_no_session_id_falls_back(self):
        self.write({1: "Q1"})
        qr.clip = lambda tries=20: (557, "Clip text", False)
        self.assertEqual(qr.reply(": yes", None)["systemMessage"], "Quoted: Clip text")

    def test_old_machine_wide_file_is_no_list(self):
        qr.write_json(qr.LIST, {"on": True, "mutex": MUTEX, "gen": 1, "next": 2,
                                "items": [{"n": 1, "seq": 1, "at": self.now, "text": "old", "more": 0}]})
        self.assertEqual(qr.reply(":1 x", A), {"systemMessage": "Reply: :1 matched nothing (no selection is listed)"})

    def test_no_list_marker_quotes_nothing(self):
        self.assertEqual(qr.reply(":1 yes", A), {"systemMessage": "Reply: :1 matched nothing (no selection is listed)"})

    def test_old_items_stay(self):
        self.write({1: "old", 2: "new"})
        d = qr.load(qr.LIST)
        d["lists"][A]["items"][0]["at"] = self.now - 30 * 86400  # no age limit any more
        qr.write_json(qr.LIST, d)
        self.assertEqual(qr.reply(":1 a :2 b", A)["systemMessage"], "Quoted: :1 :2")

    def test_ascii_json(self):
        self.write({1: "Çalışma öğün?"})
        out = qr.reply(":1 evet, şöyle", A)
        json.dumps(out, ensure_ascii=True).encode("ascii")
        self.assertIn("Çalışma öğün?", self.ctx(out))


class Rules(unittest.TestCase):
    def test_lists_are_per_session(self):
        lists, now = {}, 1000.0
        self.assertTrue(qr.add(lists, A, "alpha", 1, "a1", 0, now))
        self.assertTrue(qr.add(lists, B, "beta", 2, "b1", 0, now))
        self.assertTrue(qr.add(lists, A, "alpha", 3, "a2", 0, now))
        self.assertEqual([i["n"] for i in lists[A]["items"]], [1, 2])
        self.assertEqual([i["n"] for i in lists[B]["items"]], [1])
        self.assertNotEqual(lists[A]["gen"], lists[B]["gen"])

    def test_same_text_twice_counts_once(self):
        lists = {}
        qr.add(lists, A, "a", 1, "same", 0, 1.0)
        self.assertFalse(qr.add(lists, A, "a", 2, "same", 0, 2.0))
        self.assertTrue(qr.add(lists, B, "b", 2, "same", 0, 2.0))  # another session's list is its own

    def test_reset_empties_only_that_session_and_restarts_at_1(self):
        lists = {}
        for k, t in enumerate(("a1", "a2", "a3")):
            qr.add(lists, A, "a", k, t, 0, 1.0)
        qr.add(lists, B, "b", 9, "b1", 0, 1.0)
        gen_a, b_before = lists[A]["gen"], json.dumps(lists[B])
        self.assertTrue(qr.apply_reset(lists, A, {"gen": gen_a, "upto": 3}))
        self.assertNotIn(A, lists)
        self.assertEqual(json.dumps(lists[B]), b_before)  # B untouched
        qr.add(lists, A, "a", 20, "new", 0, 2.0)
        self.assertEqual(lists[A]["items"][0]["n"], 1)
        self.assertNotEqual(lists[A]["gen"], gen_a)
        self.assertFalse(qr.apply_reset(lists, A, {"gen": gen_a, "upto": 3}))  # an old record: another gen

    def test_selection_after_the_hooks_read_stays(self):
        lists = {}
        qr.add(lists, A, "a", 1, "a1", 0, 1.0)
        qr.add(lists, A, "a", 2, "a2", 0, 1.0)
        rec = {"gen": lists[A]["gen"], "upto": 2}
        qr.add(lists, A, "a", 3, "a3", 0, 1.0)  # made between the hook's read and the panel's turn
        self.assertTrue(qr.apply_reset(lists, A, rec))
        self.assertEqual([i["n"] for i in lists[A]["items"]], [3])

    def test_drop_keeps_numbers(self):
        lists = {}
        for k in range(3):
            qr.add(lists, A, "a", k, "t%d" % k, 0, 1.0)
        self.assertTrue(qr.drop(lists, A, 2))
        self.assertEqual([i["n"] for i in lists[A]["items"]], [1, 3])
        self.assertFalse(qr.drop(lists, B, 1))
        qr.add(lists, A, "a", 9, "t9", 0, 1.0)
        self.assertEqual(lists[A]["items"][-1]["n"], 4)

    def test_safety_caps(self):
        lists = {}
        for k in range(qr.MAX_ITEMS + 5):
            qr.add(lists, A, "a", k, "t%d" % k, 0, 1.0)
        self.assertEqual(len(lists[A]["items"]), qr.MAX_ITEMS)
        self.assertEqual(lists[A]["items"][0]["n"], 6)  # the oldest dropped, numbers kept
        for k in range(qr.MAX_LISTS):
            qr.add(lists, "s%02d" % k, "s", k, "x", 0, 2.0 + k)
        self.assertEqual(len(lists), qr.MAX_LISTS)
        self.assertNotIn(A, lists)  # the list least recently added to went

    def test_reset_path_stays_in_its_folder(self):
        self.assertEqual(os.path.dirname(qr.reset_path("..\\..\\evil")), qr.RESETS)

    def test_alive(self):
        h = kernel32.CreateMutexW(None, False, MUTEX + "-alive")
        self.assertTrue(qr.alive(MUTEX + "-alive"))
        kernel32.CloseHandle(ctypes.c_void_p(h))
        self.assertFalse(qr.alive(MUTEX + "-alive"))


class Attribution(unittest.TestCase):
    """agent-panel.pyw tab_session() and copied_in(): which session a copy was selected in. Window handles and
    titles are made up; Claude Code titles a tab "<status glyph> <session name>"."""

    def setUp(self):
        ns = pyw_functions("shown_name", "tab_session", "copied_in")
        self.tab, self.copied = ns["tab_session"], ns["copied_in"]
        self.sessions = [
            {"id": "A", "pid": 11, "name": "mindcopy", "status": "idle"},
            {"id": "B", "pid": 12, "name": "mindmap", "status": "busy"},
            {"id": "C", "pid": 13, "name": "sistem", "status": "idle"},
            {"id": "D", "pid": 14, "name": "closed-one", "status": None},
            {"id": "H", "pid": 15, "name": "headless", "status": "busy", "headless": True},
        ]
        # A and B are tabs of one Windows Terminal window (100); C has its own window (200)
        self.windows = {11: 100, 12: 100, 13: 200, 14: 100, 15: 100}
        self.win = lambda s: self.windows.get(s["pid"])

    def who(self, owner, title, before=None, sessions=None):
        s = self.copied(owner, title, before, sessions or self.sessions, self.win)
        return s and s["id"]

    def test_two_tabs_in_one_window(self):
        self.assertEqual(self.who(100, "\u2733 mindcopy"), "A")
        self.assertEqual(self.who(100, "\u25d0 mindmap"), "B")  # a busy glyph in front of the name

    def test_two_windows(self):
        self.assertEqual(self.who(200, "\u2733 sistem"), "C")
        self.assertIsNone(self.who(200, "\u2733 mindcopy"))  # the name of a session in another window

    def test_non_session_window_in_front(self):
        self.assertIsNone(self.who(300, "Untitled - Notepad"))
        self.assertIsNone(self.who(None, "\u2733 mindcopy"))  # no clipboard owner: nobody to tie it to
        self.assertIsNone(self.who(100, "Windows PowerShell"))  # a shell tab in a session's window

    def test_ambiguous_title(self):
        twin = self.sessions + [{"id": "E", "pid": 16, "name": "mindcopy", "status": "idle"}]
        self.windows[16] = 100
        self.assertIsNone(self.who(100, "\u2733 mindcopy", sessions=twin))
        self.windows[16] = 200  # the same name in another window is no ambiguity for this one
        self.assertEqual(self.who(100, "\u2733 mindcopy", sessions=twin), "A")

    def test_closed_headless_and_unnamed_never_match(self):
        self.assertIsNone(self.who(100, "\u2733 closed-one"))
        self.assertIsNone(self.who(100, "\u2733 headless"))
        unnamed = [{"id": "N", "pid": 17, "name": None, "status": "idle"}]
        self.windows[17] = 400
        self.assertIsNone(self.who(400, "\u2733 Claude Code", sessions=unnamed))
        self.assertIsNone(self.who(400, "", sessions=unnamed))

    def test_tab_switched_since_the_last_turn(self):
        self.assertIsNone(self.who(100, "\u2733 mindmap", before=(100, "mindcopy")))
        self.assertEqual(self.who(100, "\u25d1 mindmap", before=(100, "mindmap")), "B")  # only its glyph moved
        self.assertEqual(self.who(100, "\u2733 mindmap", before=(200, "sistem")), "B")  # just came to the front

    def test_strip_follows_the_session_in_front(self):
        self.assertEqual(self.tab(100, "\u2733 mindcopy", self.sessions, self.win)["id"], "A")
        self.assertIsNone(self.tab(500, "Agent Panel", self.sessions, self.win))


if __name__ == "__main__":
    unittest.main(verbosity=1)
