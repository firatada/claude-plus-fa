# Unit tests for quote-reply.py (reply to one passage or several): the ":N" marker parser, the hook's answer for
# a live list, an empty one and none, and the list's rules. Run: python -I tools/test-quote-reply.py
# Uses a temporary folder for the list and USED files and a mutex of its own; never touches the clipboard
# (clip() is replaced where the no-list path needs it) or the real ~/.claude.
import ctypes, importlib.util, json, os, shutil, sys, tempfile, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("quote_reply", os.path.join(HERE, "..", "quote-reply.py"))
qr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qr)

MUTEX = "Local\\claude-plus-fa-test-%d" % os.getpid()
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


class Hook(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.saved = qr.LIST, qr.USED, qr.clip
        qr.LIST, qr.USED = os.path.join(self.dir, "list.json"), os.path.join(self.dir, "used.json")
        qr.clip = lambda tries=20: (_ for _ in ()).throw(AssertionError("clipboard read with a live list"))
        self.mutex = kernel32.CreateMutexW(None, False, MUTEX)
        self.now = time.time()

    def tearDown(self):
        qr.LIST, qr.USED, qr.clip = self.saved
        kernel32.CloseHandle(ctypes.c_void_p(self.mutex))
        shutil.rmtree(self.dir)

    def write(self, texts, on=True, mutex=MUTEX, gen=7, ages=None):
        items = [{"n": n, "seq": 100 + n, "at": self.now - (ages or {}).get(n, 5), "text": t, "more": 0}
                 for n, t in texts.items()]
        qr.write_json(qr.LIST, {"on": on, "mutex": mutex, "gen": gen, "next": max(texts or {0: 0}) + 1,
                                "items": items})

    def used(self):
        with open(qr.USED, encoding="utf-8") as f:
            return json.load(f)

    def ctx(self, out):
        return out["hookSpecificOutput"]["additionalContext"]

    def test_no_colon_no_output(self):
        self.write({1: "Q1"})
        self.assertIsNone(qr.reply("just a prompt"))
        self.assertIsNone(qr.reply("at 10:20 it broke"))
        self.assertFalse(os.path.exists(qr.USED))

    def test_numbered(self):
        self.write({1: "Question one?", 2: "Question two?", 3: "Question three?", 4: "Question four?"})
        out = qr.reply(":1 yes, fix it :3 no :4 your call", self.now)
        self.assertEqual(out["systemMessage"], "Quoted: :1 :3 :4")
        c = self.ctx(out)
        self.assertIn('<quote n="1">\n<passage>\nQuestion one?\n</passage>\n<reply>\nyes, fix it\n</reply>\n</quote>', c)
        self.assertIn('<quote n="3">', c)
        self.assertIn("<reply>\nyour call\n</reply>", c)
        self.assertNotIn("Question two?", c)  # unanswered: not sent
        self.assertEqual({k: v for k, v in self.used().items() if k != "at"}, {"seq": 104, "gen": 7, "upto": 4})

    def test_numbered_with_unknown(self):
        self.write({1: "Q1", 2: "Q2"})
        out = qr.reply(":1 yes :5 no", self.now)
        self.assertEqual(out["systemMessage"], "Quoted: :1 (:5 matched nothing)")
        self.assertIn("<reply>\nyes :5 no\n</reply>", self.ctx(out))

    def test_unknown_only(self):
        self.write({1: "Q1"})
        out = qr.reply(":5 hello", self.now)
        self.assertEqual(out, {"systemMessage": "Reply: :5 matched nothing"})
        self.assertFalse(os.path.exists(qr.USED))

    def test_empty_list_marker_quotes_nothing(self):
        self.write({})
        out = qr.reply(":1 yes", self.now)
        self.assertEqual(out, {"systemMessage": "Reply: :1 matched nothing (no selection is listed)"})
        self.assertFalse(os.path.exists(qr.USED))

    def test_empty_list_bare(self):
        self.write({})
        self.assertEqual(qr.reply(": hello", self.now), {"systemMessage": "Reply: nothing to quote (no selection is listed)"})

    def test_bare_one_item_as_before(self):
        self.write({3: "The one passage"})
        out = qr.reply(": agreed", self.now)
        self.assertEqual(out["systemMessage"], "Quoted: The one passage")
        self.assertTrue(self.ctx(out).startswith("The user is replying to this passage"))
        self.assertIn("<quote>\nThe one passage\n</quote>", self.ctx(out))
        self.assertEqual(self.used()["upto"], 3)

    def test_bare_several_sends_all(self):
        self.write({1: "Q1", 2: "Q2", 4: "Q4"})  # 3 was dropped: numbers are not shifted
        out = qr.reply(": yes", self.now)
        self.assertEqual(out["systemMessage"],
                         "Quoted all 3 (:1 :2 :4): the reply was not numbered, so Claude may ask which one is meant")
        c = self.ctx(out)
        self.assertIn("ask the user which item they mean", c)
        for n in (1, 2, 4):
            self.assertIn('<quote n="%d">\n<passage>\nQ%d\n</passage>\n</quote>' % (n, n), c)
        self.assertNotIn("<reply>", c)

    def test_bare_plus_marker_is_numbered(self):
        self.write({1: "Q1", 2: "Q2"})
        out = qr.reply(": thanks :2 no", self.now)
        self.assertEqual(out["systemMessage"], "Quoted: :2")

    def test_cut_passage_noted(self):
        self.write({1: "x" * 4000})
        qr.write_json(qr.LIST, dict(qr.load(qr.LIST), items=[dict(qr.load(qr.LIST)["items"][0], more=2000)]))
        self.assertIn("cut to its first 4000 of 6000 characters", self.ctx(qr.reply(":1 ok", self.now)))

    def test_panel_gone_falls_back_to_clipboard(self):
        self.write({1: "Q1", 2: "Q2"}, mutex="Local\\claude-plus-fa-test-nobody")
        qr.clip = lambda tries=20: (555, "From the clipboard", False)
        out = qr.reply(": yes", self.now)
        self.assertEqual(out["systemMessage"], "Quoted: From the clipboard")
        self.assertEqual(self.used()["seq"], 555)
        self.assertNotIn("gen", self.used())
        self.assertEqual(qr.reply(": again", self.now)["systemMessage"].split(" (")[0], "Reply: nothing to quote")

    def test_strip_off_falls_back(self):
        self.write({1: "Q1"}, on=False)
        qr.clip = lambda tries=20: (556, "Clip text", False)
        self.assertEqual(qr.reply(": yes", self.now)["systemMessage"], "Quoted: Clip text")

    def test_no_list_marker_quotes_nothing(self):
        self.assertEqual(qr.reply(":1 yes", self.now), {"systemMessage": "Reply: :1 matched nothing (no selection is listed)"})

    def test_old_items_drop(self):
        self.write({1: "old", 2: "new"}, ages={1: qr.AGE + 1})
        out = qr.reply(":1 a :2 b", self.now)
        self.assertEqual(out["systemMessage"], "Quoted: :2 (:1 matched nothing)")

    def test_ascii_json(self):
        self.write({1: "Çalışma öğün?"})
        out = qr.reply(":1 evet, şöyle", self.now)
        json.dumps(out, ensure_ascii=True).encode("ascii")
        self.assertIn("Çalışma öğün?", self.ctx(out))


class Rules(unittest.TestCase):
    def test_fresh_cap_keeps_numbers(self):
        now = time.time()
        items = [{"n": n, "at": now} for n in range(1, 13)]
        self.assertEqual([i["n"] for i in qr.fresh(items, now)], list(range(4, 13)))

    def test_alive(self):
        h = kernel32.CreateMutexW(None, False, MUTEX + "-alive")
        self.assertTrue(qr.alive(MUTEX + "-alive"))
        kernel32.CloseHandle(ctypes.c_void_p(h))
        self.assertFalse(qr.alive(MUTEX + "-alive"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
