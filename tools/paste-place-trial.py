"""Measures how a throwaway interactive Claude Code session takes pastes: where a picture pasted inside a sentence
lands, whether the model sees it and names the word it was pasted after, and what a pasted text is stored as.

One session runs hidden in a pseudo-console (ConPTY through pywinpty, as the voice host does: no window, nothing
focused) in a folder under %TEMP% named claude-plus-test-paste-* (the panel never lists it). A project hook dumps
what UserPromptSubmit receives. Each case is one prompt:

  text    a paste of three lines, then typed text (what a pasted prompt is stored as)
  inline  typed words, a picture's path pasted alone, more words, a second path, more words (the terminal's own
          [Image #N] at the caret?)
  pieces  the same sentence sent as five pastes (text, path, text, path, text), as Send to Session could send it
  block   the same sentence as one paste with each path on a line of its own (how one paste of everything lands)
  long    a paste of 30 lines (Claude Code folds it into [Pasted text #N]), then typed text
  longpieces  a 30-line paste, a picture's path pasted alone, a short paste, a second path, the question pasted
  named   one paste: the sentence with [Image 1] and [Image 2] in place, then "[Image N: <path>]" naming lines,
          then the paths alone at the end (the reply window's fallback delivery)

  python tools/paste-place-trial.py [case ...]

Not -I: it needs pywinpty and pyte from the user packages. Results: <trial folder>/results.json, also printed.
The throwaway transcript stays under ~/.claude/projects.
"""
import importlib.util
import json
import os
import re
import sys
import tempfile
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("keepalive_trial", os.path.join(HERE, "keepalive-trial.py"))
kt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kt)

ASK = (" For each picture in this prompt say its colour and the word written right before the place it stands in the "
       "sentence, one line each, as '<colour>: <word>'. Do not use tools.")
DUMP = "import sys\nopen(sys.argv[1], 'ab').write(sys.stdin.buffer.read() + b'\\n')\n"


def pictures(d):
    from PIL import Image
    red = os.path.join(d, "red.png")
    Image.new("RGB", (64, 64), (220, 30, 30)).save(red)
    os.makedirs(os.path.join(d, "pic s"))
    blue = os.path.join(d, "pic s", "blue.png")
    Image.new("RGB", (64, 64), (30, 60, 220)).save(blue)
    return red, blue


def quoted(p):
    return '"%s"' % p if " " in p else p


def user_lines(path, after):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                o = json.loads(line)
                if o.get("type") == "user" and not o.get("isMeta") and o.get("toolUseResult") is None \
                        and (o.get("timestamp") or "") > after:
                    c = (o.get("message") or {}).get("content")
                    out.append(c if isinstance(c, str) else [
                        b.get("text") if b.get("type") == "text" else "<%s %s>" % (b.get("type"), (
                            b.get("source") or {}).get("media_type")) for b in c if isinstance(b, dict)])
    except OSError:
        pass
    return out


def last_reply(path):
    text = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            o = json.loads(line)
            m = o.get("message") or {}
            if o.get("type") == "assistant" and isinstance(m.get("content"), list):
                t = "".join(b.get("text") or "" for b in m["content"] if b.get("type") == "text")
                text = t or text
    return text


def typing(h, s):
    for ch in s:
        h.proc.write(ch)
        time.sleep(0.02)
    time.sleep(0.5)


GAP = float(os.environ.get("TRIAL_GAP") or 1.2)  # seconds after each paste (Send to Session waits PIECE_GAP)


def paste(h, s):
    h.proc.write("\x1b[200~" + s + "\x1b[201~")
    time.sleep(GAP)


def screen_prompt(h):
    """The prompt box's lines as the screen shows them before Enter."""
    lines = [l.rstrip() for l in h.text().split("\n")]
    keep = [l for l in lines if l.strip()]
    return keep[-14:]


def main():
    cases = sys.argv[1:] or ["text", "inline", "pieces", "block", "named"]
    root = tempfile.mkdtemp(prefix="claude-plus-test-paste-")
    work = os.path.join(root, "work")
    os.makedirs(os.path.join(work, ".claude"))
    dump = os.path.join(root, "hook.jsonl")
    with open(os.path.join(root, "dump.py"), "w") as f:
        f.write(DUMP)
    hook = '"%s" -I "%s" "%s"' % (sys.executable, os.path.join(root, "dump.py"), dump)
    with open(os.path.join(work, ".claude", "settings.json"), "w") as f:
        json.dump({"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": hook}]}]}}, f)
    red, blue = pictures(root)
    h = kt.Host(work, str(uuid.uuid4()))
    h.start()
    results = {"root": root, "sid": h.sid, "cases": {}}
    try:
        if not h.ready():
            print("prompt never drawn:", h.text()[-1500:])
            return
        path = kt.transcript(work, h.sid)
        for case in cases:
            before = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 1))
            n = len(kt.usages(path)) if os.path.exists(path) else 0
            if case == "text":
                paste(h, "Reply with the single word ready.\nline two of the paste\nline three of the paste")
                typing(h, " typed after the paste")
            elif case == "inline":
                typing(h, "The word alpha ")
                paste(h, quoted(red))
                typing(h, " is followed by the word bravo ")
                paste(h, quoted(blue))
                typing(h, " and that is all." + ASK)
            elif case == "pieces":
                paste(h, "The word charlie ")
                paste(h, quoted(red))
                paste(h, " is followed by the word delta ")
                paste(h, quoted(blue))
                paste(h, " and that is all." + ASK)
            elif case == "long":
                paste(h, "Reply with the single word ready.\n" + "\n".join("filler line %d" % i for i in range(30)))
                typing(h, " typed after the paste")
            elif case == "longpieces":
                paste(h, "My answers inside your reply:\n" + "\n".join("> quoted line %d" % i for i in range(30))
                      + "\nMy answer: the word india ")
                paste(h, quoted(red))
                paste(h, " is followed by the word juliet ")
                paste(h, quoted(blue))
                paste(h, " and that is all." + ASK)
            elif case == "block":
                paste(h, "The word echo\n%s\nis followed by the word foxtrot\n%s\nand that is all.%s"
                      % (quoted(red), quoted(blue), ASK))
            elif case == "named":
                paste(h, "The word golf [Image 1] is followed by the word hotel [Image 2] and that is all.%s\n"
                         "[Image 1: %s]\n[Image 2: %s]\n\n%s\n%s" % (ASK, red, blue, quoted(red), quoted(blue)))
            shown = screen_prompt(h)
            h.proc.write("\r")
            us = kt.wait_reply(path, n + 1, timeout=300)
            results["cases"][case] = {"screen": shown, "stored": user_lines(path, before), "reply": last_reply(path),
                                      "usage": us[-1:] if us else None}
            print(json.dumps({case: results["cases"][case]}, ensure_ascii=False, indent=1), flush=True)
        try:
            with open(dump, encoding="utf-8") as f:
                results["hook"] = [json.loads(l).get("prompt") for l in f if l.strip()]
        except OSError:
            results["hook"] = None
        print(json.dumps({"hook": results["hook"]}, ensure_ascii=False, indent=1))
    finally:
        h.stop()
        with open(os.path.join(root, "results.json"), "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)
        print("results", os.path.join(root, "results.json"))


if __name__ == "__main__":
    main()
