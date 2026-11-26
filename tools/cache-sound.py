"""Writes mascot/cache-soon.wav and mascot/cache-last.wav, Field Agents' cache warning sounds.

Usage: python -I tools/cache-sound.py

Synthesized, no recording (so the public repository carries no licensed sound), and unlike the alert's knock
(mascot/knock.wav, "a session asks you"), so the ear tells the two apart.
- cache-soon.wav, 15 minutes before an open session's prompt cache expires: a soft falling two-note chime
  (E6 then C6, bell-like: a decaying sine with a quiet octave over it), about 0.7 s.
- cache-last.wav, 5 minutes before: three quick, brighter beeps (A5 with a fifth over it), one every 0.2 s, about 0.6 s.
44.1 kHz mono 16 bit, peak at about -4 dBFS (soon) and -2 dBFS (last).
"""
import math, os, struct, wave

RATE = 44100
MASCOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mascot")


def bell(freq, seconds, decay):
    out = []
    for i in range(int(seconds * RATE)):
        t = i / RATE
        attack = min(1.0, t / 0.004)  # a 4 ms rise, so the note starts without a click
        out.append(attack * math.exp(-t / decay) * (math.sin(2 * math.pi * freq * t) +
                                                     0.25 * math.sin(2 * math.pi * 2 * freq * t)))
    return out


def beep(freq, seconds):
    n = int(seconds * RATE)
    out = []
    for i in range(n):
        t = i / RATE
        env = min(1.0, t / 0.004, (n - i) / RATE / 0.012)  # 4 ms in, 12 ms out
        out.append(env * (math.sin(2 * math.pi * freq * t) + 0.35 * math.sin(2 * math.pi * 1.5 * freq * t)))
    return out


def mix(notes, seconds):
    total = [0.0] * int(seconds * RATE)
    for start, samples in notes:
        s = int(start * RATE)
        for i, v in enumerate(samples):
            if s + i < len(total):
                total[s + i] += v
    return total


def write(name, total, peak_of_full):
    peak = max(abs(v) for v in total)
    scale = peak_of_full * 32767 / peak
    path = os.path.join(MASCOT, name)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(v * scale)) for v in total))
    print("wrote", path)


write("cache-soon.wav", mix([(0.0, bell(1318.5, 0.5, 0.12)), (0.18, bell(1046.5, 0.5, 0.16))], 0.7), 0.63)
write("cache-last.wav", mix([(k * 0.2, beep(880.0, 0.11)) for k in range(3)], 0.62), 0.8)
