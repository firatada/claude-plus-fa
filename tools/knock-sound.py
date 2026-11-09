"""Writes mascot/knock.wav, the Agent Panel's alert sound: three knuckle taps on glass.

Usage: python -I tools/knock-sound.py

Synthesized, no recording (so the public repository carries no licensed sound). Each tap is a low
thump (a decaying 170 Hz sine with a little 340 Hz), the glass's ring (a decaying 1.6 kHz and
2.9 kHz pair) and a 3 ms noise click for the knuckle's hit; three taps 150 ms apart, the last a
little softer. 44.1 kHz mono 16 bit, peak at about -3 dBFS, about 0.5 s.
"""
import math, os, random, struct, wave

RATE = 44100
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mascot", "knock.wav")


def tap(n):
    random.seed(7)
    out = []
    for i in range(n):
        t = i / RATE
        thump = math.exp(-t / 0.022) * (math.sin(2 * math.pi * 170 * t) + 0.35 * math.sin(2 * math.pi * 340 * t))
        ring = math.exp(-t / 0.012) * (0.45 * math.sin(2 * math.pi * 1600 * t) + 0.25 * math.sin(2 * math.pi * 2900 * t))
        click = (random.random() * 2 - 1) * math.exp(-t / 0.0012) * 0.6
        out.append(thump + ring + click)
    return out


one = tap(int(0.12 * RATE))
total = [0.0] * int(0.5 * RATE)
for k, (start, gain) in enumerate(((0.0, 1.0), (0.15, 0.95), (0.30, 0.8))):
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
