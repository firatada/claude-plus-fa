"""Konuşma denemesi: the voice host experiment with real speech (phase 1, not part of the panel).

Starts a hidden Claude Code (tools/voice_host.py), forwards Space held in the hold area to it as
key repeats, shows what its prompt line holds while you speak, and the timings. Each hold is
appended to %LOCALAPPDATA%\\claude-plus\\voice-host\\trial.log. Closing the window ends the hidden
Claude Code. Enter is never sent to it; its prompt is cleared after every hold.
"""
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from voice_host import HOST_DIR, VoiceHost  # noqa: E402

LOG = os.path.join(HOST_DIR, "trial.log")
GUARD_S = 0.6      # no Space goes to Claude Code this long after a release (its double tap submits)
SETTLE_S = 0.35    # the text must stay the same this long after Transcribing… ends to be final
FINAL_WAIT_S = 15  # give up waiting for the final text after this long

BG, INK, MUTED, BLUE, RED, LINE = "#ffffff", "#0a1a40", "#5b6478", "#2563eb", "#e11d48", "#d9deea"


def log_line(s):
    try:
        os.makedirs(HOST_DIR, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + s + "\n")
    except OSError:
        pass


class Trial:
    def __init__(self, root):
        self.root = root
        self.host = VoiceHost(log=lambda *a: log_line("host: " + " ".join(map(str, a))))
        self.ready = False
        self.down = False          # Space is held (as far as Tk tells)
        self.release_at = None
        self.blocked_until = 0.0
        self.hold = None           # the current hold's record
        self.history = []
        self.release_job = None

        root.title("Konuşma denemesi")
        root.configure(bg=BG)
        root.geometry("640x520")
        root.minsize(480, 400)
        base = tkfont.Font(family="Segoe UI", size=10)
        big = tkfont.Font(family="Segoe UI", size=12)
        small = tkfont.Font(family="Segoe UI", size=9)

        tk.Label(root, text="Click the box below, hold Space and speak; let go to finish.",
                 bg=BG, fg=INK, font=base, anchor="w").pack(fill="x", padx=16, pady=(14, 8))

        self.area = tk.Label(root, text="Starting the hidden Claude Code…", bg="#f3f5fa", fg=MUTED,
                             font=big, height=3, relief="flat", highlightthickness=2,
                             highlightbackground=LINE, highlightcolor=BLUE, takefocus=1)
        self.area.pack(fill="x", padx=16)
        self.area.bind("<Button-1>", lambda e: self.area.focus_set())
        self.area.bind("<KeyPress-space>", self.on_press)
        self.area.bind("<KeyRelease-space>", self.on_release)
        self.area.bind("<FocusIn>", lambda e: self.paint())
        self.area.bind("<FocusOut>", self.on_blur)

        tk.Label(root, text="Live text", bg=BG, fg=MUTED, font=small, anchor="w").pack(fill="x", padx=16, pady=(12, 2))
        self.live = tk.Text(root, height=6, wrap="word", font=big, bg=BG, fg=INK, relief="flat",
                            highlightthickness=1, highlightbackground=LINE, padx=8, pady=6)
        self.live.pack(fill="both", expand=True, padx=16)
        self.live.tag_configure("old", foreground=MUTED)
        self.live.tag_configure("now", foreground=INK)
        self.live.configure(state="disabled")

        tk.Label(root, text="Timings", bg=BG, fg=MUTED, font=small, anchor="w").pack(fill="x", padx=16, pady=(12, 2))
        self.times = tk.Label(root, text="", bg=BG, fg=INK, font=base, anchor="nw", justify="left", height=4)
        self.times.pack(fill="x", padx=16, pady=(0, 14))

        root.protocol("WM_DELETE_WINDOW", self.close)
        threading.Thread(target=self.start_host, daemon=True).start()
        self.tick()

    # ---- hidden Claude Code ----
    def start_host(self):
        t0 = time.monotonic()
        try:
            self.host.start()
            ok = self.host.wait_for(lambda: self.host.prompt_cells() is not None, 30)
        except Exception as e:  # noqa: BLE001
            self.fail = f"Could not start Claude Code: {e}"
            log_line("start failed: " + repr(e))
            return
        if not ok:
            self.fail = "Claude Code did not show its prompt within 30 s."
            log_line("start: no prompt in 30 s; screen:\n" + self.host.text())
            return
        time.sleep(1.0)  # let it finish drawing
        self.host.clear_line()
        self.ready = True
        log_line(f"ready in {time.monotonic() - t0:.2f} s")

    fail = None

    # ---- keys ----
    def on_press(self, e):
        now = time.monotonic()
        if self.release_job:   # Tk on Windows may send release+press pairs while repeating
            self.root.after_cancel(self.release_job)
            self.release_job = None
        waiting = self.hold and self.hold.get("released") and not self.hold.get("final")
        if not self.ready or now < self.blocked_until or waiting:
            return "break"
        if not self.down:
            self.down = True
            self.hold = {"press": now, "keys": 0, "updates": [], "first": None, "text": "",
                         "states": [], "released": None, "final": None}
            self.paint()
        self.hold["keys"] += 1
        try:
            self.host.key(" ")
        except Exception as e:  # noqa: BLE001
            self.fail = f"Claude Code stopped: {e}"
        return "break"

    def on_release(self, e):
        if self.down:
            self.release_job = self.root.after(40, self.released)
        return "break"

    def on_blur(self, e):
        if self.down:
            self.released()
        self.paint()

    def released(self):
        self.release_job = None
        if not self.down:
            return
        self.down = False
        now = time.monotonic()
        self.hold["released"] = now
        self.blocked_until = now + GUARD_S
        self.paint()

    # ---- the loop: read the hidden screen ----
    def tick(self):
        try:
            self.step()
        finally:
            self.root.after(40, self.tick)

    def step(self):
        if self.fail:
            self.area.configure(text=self.fail, fg=RED)
            return
        if not self.ready:
            return
        h = self.hold
        if not h or h.get("final"):
            self.paint()
            return
        now = time.monotonic()
        text = self.host.typed() or ""
        status = self.host.status()
        if not h["states"] or h["states"][-1][1] != status:
            h["states"].append((now - h["press"], status))
        if text != h["text"]:
            h["text"] = text
            if text.strip():
                h["updates"].append((now - h["press"], text))
                if h["first"] is None:
                    h["first"] = now - h["press"]
            h["changed"] = now
            self.show_live()
        if h["released"] and not h["final"]:
            busy = any(w in status.lower() for w in ("listening", "transcribing", "keep holding"))
            quiet = now - h.get("changed", h["released"]) >= SETTLE_S
            if (not busy and quiet and now - h["released"] >= 0.3) or now - h["released"] > FINAL_WAIT_S:
                h["final"] = now
                h["note"] = status if not text.strip() else ""
                self.finish(h)
        self.paint()

    def finish(self, h):
        text = h["text"].strip()
        self.history.append(text or "(nothing)")
        r = h["released"] - h["press"]
        first = f"{h['first']:.2f} s" if h["first"] is not None else "none"
        upd_during = [t for t, _ in h["updates"] if t < r]
        log_line(f"hold {r:.2f} s, {h['keys']} keys; first words at {first}; "
                 f"{len(h['updates'])} text changes ({len(upd_during)} while held); "
                 f"release to final {h['final'] - h['released']:.2f} s; states {h['states']}; "
                 f"note {h.get('note')!r}; text {text!r}")
        # empty the hidden prompt for the next hold; Enter is never sent
        self.blocked_until = time.monotonic() + 0.3
        threading.Thread(target=self.host.clear_line, daemon=True).start()
        self.show_live()

    # ---- drawing ----
    def show_live(self):
        self.live.configure(state="normal")
        self.live.delete("1.0", "end")
        h = self.hold
        olds = self.history[:-1] if h and h.get("final") else self.history
        for s in olds[-6:]:
            self.live.insert("end", s + "\n", "old")
        if h:
            self.live.insert("end", h["text"] or ("" if h.get("final") else "…"), "now")
        self.live.see("end")
        self.live.configure(state="disabled")

    def paint(self):
        h = self.hold
        if not self.ready:
            return
        focused = self.root.focus_get() is self.area
        status = self.host.status()
        if self.down:
            msg, fg, bg = f"Holding… {status or ''}".strip(), RED, "#fff1f3"
        elif h and h.get("released") and not h.get("final"):
            msg, fg, bg = status or "Waiting for the final text…", MUTED, "#f3f5fa"
        elif focused:
            msg, fg, bg = "Hold Space and speak", BLUE, "#f5f8ff"
        else:
            msg, fg, bg = "Click here first", MUTED, "#f3f5fa"
        if h and h.get("final") and h.get("note") and not self.down:
            msg = f"{msg}  ·  {h['note']}"
        self.area.configure(text=msg, fg=fg, bg=bg)
        if h:
            lines = [f"Press → first words: {h['first']:.2f} s" if h["first"] is not None else "Press → first words: none yet"]
            r = (h["released"] - h["press"]) if h["released"] else None
            ups = h["updates"]
            during = [f"{t:.1f}" for t, _ in ups if r is None or t < r]
            lines.append(f"Word updates while held: {len(during)}" + (f" (at {', '.join(during[-8:])} s)" if during else ""))
            if r is not None:
                lines.append(f"Held {r:.2f} s, {h['keys']} Space events")
            if h.get("final"):
                lines.append(f"Release → final text: {h['final'] - h['released']:.2f} s")
            self.times.configure(text="\n".join(lines))

    def close(self):
        log_line("closing")
        try:
            self.host.stop()
        finally:
            self.root.destroy()


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        pass
    root = tk.Tk()
    Trial(root)
    root.mainloop()


if __name__ == "__main__":
    main()
