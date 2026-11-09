"""Writes mascot/knock.wav, the Agent Panel's alert sound: three full, deep knocks.

Usage: python -I tools/knock-sound.py

Synthesized, no recording (so the public repository carries no licensed sound). Each knock is a
deep thump (a decaying 95 Hz sine with 190 Hz under it, 60 ms decay), a short body resonance
(a decaying 420 Hz) and a soft 4 ms noise for the knuckle's hit; three knocks half a second apart
(the maintainer, 2026-11-09: "fuller, three times, half a second apart", after three quick glassy
taps). 44.1 kHz mono 16 bit, peak at about -3 dBFS, about 1.3 s.
"""
import math, os, random, struct, wave

RATE = 44100
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mascot", "knock.wav")


def tap(n):
    random.seed(7)
    out = []
    for i in range(n):
        t = i / RATE
        thump = math.exp(-t / 0.06) * (math.sin(2 * math.pi * 95 * t) + 0.5 * math.sin(2 * math.pi * 190 * t))
        ring = math.exp(-t / 0.018) * 0.3 * math.sin(2 * math.pi * 420 * t)
        click = (random.random() * 2 - 1) * math.exp(-t / 0.0015) * 0.25
        out.append(thump + ring + click)
    return out


one = tap(int(0.3 * RATE))
total = [0.0] * int(1.3 * RATE)
for k, (start, gain) in enumerate(((0.0, 1.0), (0.5, 1.0), (1.0, 1.0))):
    s = int(start * RATE)
    for i, v in enumerate(one):
        if s + i < len(total):
            total[s + i] += v * gain
peak = max(abs(v) for v in total)
scale = 0.7 * 32767 / peak
with wave.open(OUT, "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(RATE)
    w.writeframes(b"".join(struct.pack("<h", int(v * scale)) for v in total))
print("wrote", OUT)
