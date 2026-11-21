"""Builds the Agent Panel's hero strips from the 512 px source frames.

Usage: python -I tools/hero-export.py

Source: tools/mascot-source/hero/, transparent 512 px frames on one shared cell (feet on row 463):
run-0..7.png, the running cycle (drawn at 11 fps), and sit-0..3.png, the sitting idle (rest, half
breath, full breath, blink), both at the same scale, so the head is the same size in either.
Every frame is cut with one square box, the union of all twelve drawings, padded and standing on
their shared baseline: the hero is taller than wide, so the box's height decides the scale and the
figure is never squashed; switching between running and sitting keeps the size and place.
Frames are scaled to OUT_PX (shown at 38 px on a card, 28 px in the top strip's tabs, 36 px in the
header; 96 px keeps them sharp up to 2.5x screens).
Output: mascot/hero-run.webp (8 frames) and mascot/hero-sit.webp (4 frames), one horizontal strip each.
The panel greys the sitting hero of a closed card itself (CSS), so there is no grey strip.
"""
import os
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "mascot-source", "hero")
OUT = os.path.join(os.path.dirname(HERE), "mascot")
OUT_PX = 96
PAD = 6  # source px of air round the union box
STRIPS = {"hero-run": ["run-%d.png" % i for i in range(8)], "hero-sit": ["sit-%d.png" % i for i in range(4)]}

frames = {name: [Image.open(os.path.join(SRC, f)).convert("RGBA") for f in files] for name, files in STRIPS.items()}
union = np.zeros((512, 512), bool)
for strip in frames.values():
    for f in strip:
        union |= np.array(f)[:, :, 3] > 8
ys, xs = np.where(union.any(1))[0], np.where(union.any(0))[0]
bottom = ys[-1] + 1 + PAD
side = max(bottom - (ys[0] - PAD), xs[-1] - xs[0] + 1 + 2 * PAD)
left = (xs[0] + xs[-1] + 1) // 2 - side // 2
box = (left, bottom - side, left + side, bottom)
print("box %s, %d px square" % (box, side))

os.makedirs(OUT, exist_ok=True)
for name, strip in frames.items():
    out = Image.new("RGBA", (OUT_PX * len(strip), OUT_PX))
    for i, f in enumerate(strip):
        out.paste(f.crop(box).resize((OUT_PX, OUT_PX), Image.LANCZOS), (i * OUT_PX, 0))
    path = os.path.join(OUT, name + ".webp")
    out.save(path, "WEBP", quality=88, method=6)
    print("wrote %s (%d frames, %d bytes)" % (name, len(strip), os.path.getsize(path)))
