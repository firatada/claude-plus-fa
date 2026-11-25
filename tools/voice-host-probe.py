"""Voice host feasibility probe (phase 1): no window, no speech needed.

Starts the hidden Claude Code (voice_host.py), waits for its prompt, checks that the prompt line
can be read, typed into and cleared, then holds Space as this PC's keyboard repeats it and logs
every change of the prompt text and of Claude Code's voice line with its time. Ends the hidden
Claude Code. The microphone is open during the hold: whatever the room says may be transcribed.

    python tools/voice-host-probe.py [seconds held, default 3]   (not -I: pywinpty and pyte are user packages)
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from voice_host_load import VoiceHost  # noqa: E402


def main():
    held = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
    h = VoiceHost(log=lambda *a: print(f"{h.now():7.3f}", *a, flush=True))
    say = lambda *a: print(f"{h.now():7.3f}", *a, flush=True)  # noqa: E731
    h.start()
    pid = h.proc.pid
    try:
        ok = h.wait_for(lambda: h.prompt_cells() is not None, 30)
        say("prompt drawn" if ok else "NO PROMPT in 30 s")
        if not ok:
            print(h.text())
            return 1
        time.sleep(1.5)
        say("placeholder only, typed =", repr(h.typed()))
        h.key("probe text")
        h.wait_for(lambda: h.typed() == "probe text", 3)
        say("after typing, typed =", repr(h.typed()))
        h.clear_line()
        h.wait_for(lambda: h.typed() == "", 3)
        say("after clearing, typed =", repr(h.typed()))

        stop = threading.Event()

        def watch():
            last = None
            while not stop.is_set():
                h.changed.wait(0.03)
                h.changed.clear()
                cur = (h.typed(), h.status())
                if cur != last:
                    say(f"typed={cur[0]!r} voice line={cur[1]!r}")
                    last = cur
        threading.Thread(target=watch, daemon=True).start()
        say("=== Space pressed")
        n = h.hold_space(held)
        say(f"=== Space released ({n} Space characters sent)")
        time.sleep(8)
        stop.set()
        say("after release, typed =", repr(h.typed()), "voice line =", repr(h.status()))
        h.clear_line()
        h.wait_for(lambda: h.typed() == "", 3)
        say("cleared, typed =", repr(h.typed()))
        print("--- screen ---\n" + h.text())
    finally:
        h.stop()
        time.sleep(0.5)
        left = [f for f in os.listdir(os.path.expanduser(r"~\.claude\sessions")) if f.startswith(f"{pid}.")]
        print("session files left:", left)
    return 0


if __name__ == "__main__":
    sys.exit(main())
