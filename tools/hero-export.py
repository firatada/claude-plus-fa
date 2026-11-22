"""Builds the Field Agents mascot strips (the big-headed chibi FA hero) from the high-resolution source frames.

Usage: python -I tools/hero-export.py

Source: tools/mascot-source/hero/, transparent 512 px frames as cut from one Codex sheet per strip (how they were
made: trials.md, 2026-11-22), every strip but the sleep already in the run's pixels (cap width matched to the
run's, feet on row 463):
  run-0..7      the running cycle facing right (both boots off the ground in 3 and 7);
  run-8..11     its glance: frames 0, 2, 4 and 6 with the head turned to the viewer
  conduct-0..5  directing background work: beating time with a baton; conduct-6..8 its glance (beats 0, 2, 4)
  ask-0..3      asking: waving, the red "!" shaking, mouth calling (no glance: it already faces the viewer)
  idle-0..3     open: rest, breath in, blink, look aside; idle-4..5 its glance (plain, then happier)
  sleep-0..3    asleep sitting (rest, half, full and half breath, a "z" rising), drawn larger than the run
The sleeping frames are scaled so the cap covers as much area as in the run (measured on the violet). The box is
the union of the run's cycle and the sleep plus PAD, a square standing on the shared baseline and centred on the
figure; every other strip must fit inside it (checked, the export stops otherwise), so every state keeps the
head's size and the feet's place. Each frame goes straight from its source to the output size with one Lanczos
pass (measured sharper than going through an intermediate size).
Output, one horizontal strip per animation and size, frames side by side (the play order lives in agent-panel.html):
  mascot/hero-<run|conduct|ask|idle|sleep>.webp      56 px frames (the card, 1x)
  mascot/hero-<run|conduct|ask|idle|sleep>@2x.webp   112 px frames (the card at 2x; the header, 36 px, and the top
                                                     strip's 28 px tabs scale these down)
The panel greys the sleeping hero of a closed card itself (CSS), so there is no grey strip.
"""
import os
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "mascot-source", "hero")
OUT = os.path.join(os.path.dirname(HERE), "mascot")
SIZES = {"": 56, "@2x": 112}
PAD = 6         # source px of air round the union box
GROUND = 463    # feet row in every source frame
Z_RGB = (92, 59, 205)  # the flat violet of the sleeping "z"
STRIPS = {"hero-run": ["run-%d.png" % i for i in range(12)],
          "hero-conduct": ["conduct-%d.png" % i for i in range(9)],
          "hero-ask": ["ask-%d.png" % i for i in range(4)],
          "hero-idle": ["idle-%d.png" % i for i in range(6)],
          "hero-sleep": ["sleep-%d.png" % i for i in range(4)]}
CYCLE = 8       # the run's own frames (the rest are its glance): they and the sleep set the box


def load(name):
    return np.array(Image.open(os.path.join(SRC, name)).convert("RGBA"))


def cap_size(a):
    """The cap's size as the square root of its violet area: the run shows the cap from the side and
    the sleeping hero more from the front, so widths differ while the head should look as big. The "z"
    beside a sleeping cap is one flat colour (Z_RGB) and is left out."""
    r, g, b, al = (a[..., i].astype(int) for i in range(4))
    cap = (al > 128) & (b > r + 40) & (b > g + 90)
    cap &= ~((r == Z_RGB[0]) & (g == Z_RGB[1]) & (b == Z_RGB[2]))
    return float(np.sqrt(cap.sum()))


frames = {k: [load(f) for f in v] for k, v in STRIPS.items()}
run, sleep = frames["hero-run"][:CYCLE], frames["hero-sleep"]
# the sleeping figure in run pixels: scale k, feet on the same row, centred where the run is centred
k = float(np.median([cap_size(a) for a in run])) / cap_size(sleep[0])
ink = lambda a: a[..., 3] > 8
ru = np.logical_or.reduce([ink(a) for a in run])
su = np.logical_or.reduce([ink(a) for a in sleep])
rys, rxs = np.where(ru.any(1))[0], np.where(ru.any(0))[0]
sys_, sxs = np.where(su.any(1))[0], np.where(su.any(0))[0]
r_cx = (rxs[0] + rxs[-1] + 1) / 2
s_cx = (sxs[0] + sxs[-1] + 1) / 2
place = {name: (1.0, 0.0, 0.0) for name in STRIPS}  # scale, dx, dy
place["hero-sleep"] = (k, r_cx - s_cx * k, GROUND - GROUND * k)
top = min(rys[0], sys_[0] * k + place["hero-sleep"][2]) - PAD
bottom = GROUND + PAD
left = min(rxs[0], sxs[0] * k + place["hero-sleep"][1]) - PAD
right = max(rxs[-1] + 1, (sxs[-1] + 1) * k + place["hero-sleep"][1]) + PAD
side = max(bottom - top, right - left)
cx = (left + right) / 2
box = (cx - side / 2, bottom - side, cx + side / 2, bottom)
print("scale of the sleeping frames %.4f; box %s, %.1f px square" % (k, tuple(round(v, 1) for v in box), side))
for name, strip in frames.items():
    if name == "hero-sleep":
        continue
    u = np.logical_or.reduce([ink(a) for a in strip])
    ys, xs = np.where(u.any(1))[0], np.where(u.any(0))[0]
    room = (ys[0] - box[1], xs[0] - box[0], box[2] - xs[-1] - 1, box[3] - ys[-1] - 1)
    print("%s: room to the box top %.1f, left %.1f, right %.1f, bottom %.1f source px" % ((name,) + room))
    if min(room) < 0:
        raise SystemExit("%s does not fit the box" % name)

M = 600  # transparent margin, so a box reaching past the source frame still resamples cleanly
os.makedirs(OUT, exist_ok=True)
for name, strip in frames.items():
    s, dx, dy = place[name]
    own = tuple((v - d) / s + M for v, d in zip(box, (dx, dy, dx, dy)))  # the box in this frame's pixels
    for suffix, px in SIZES.items():
        out = Image.new("RGBA", (px * len(strip), px))
        for i, a in enumerate(strip):
            big = Image.new("RGBA", (a.shape[1] + 2 * M, a.shape[0] + 2 * M))
            big.paste(Image.fromarray(a), (M, M))
            out.paste(big.resize((px, px), Image.LANCZOS, box=own), (i * px, 0))
        path = os.path.join(OUT, name + suffix + ".webp")
        out.save(path, "WEBP", quality=90, method=6)
        print("wrote %s (%d frames of %d px, %d bytes)" % (os.path.basename(path), len(strip), px, os.path.getsize(path)))
