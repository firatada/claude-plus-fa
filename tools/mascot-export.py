"""Builds the Agent Panel's mascot strips from the purple octopus sheet.

Usage: python -I tools/mascot-export.py

Source: tools/mascot-source/purple-octopus.png, a 1536 x 1024 sheet with transparent background:
two columns of six numbered rows, five frames per row, a dark label pill at each row's top left.
Each row used here is cut into its frames (alpha bands between the drawings), every frame placed
on one square canvas per row, standing on the drawing's baseline and centred on it, so the frames
line up when played; then scaled to 96 px frames (shown at 36 to 38 px, sharp on scaled screens).
Output: mascot/<name>.webp next to this folder's parent, one horizontal strip per name, and
mascot/logo.png, the package's logo (256 px).
"""
import os
import numpy as np
from PIL import Image
from scipy import ndimage

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "mascot-source", "purple-octopus.png")
OUT = os.path.join(os.path.dirname(HERE), "mascot")
OUT_PX = 96
# name: (column 0 or 1, row band 0..5, frames to keep in play order)
STRIPS = {
    "calm": (0, 0, [0, 1, 2, 3, 4]),        # 1 Calm: looks around, blinks
    "reading": (1, 0, [0, 1, 2, 3, 4]),     # 2 Watching / reading a tablet
    "sleeping": (0, 1, [0, 1, 2]),          # 3 Asleep (cap and bubble frames left out)
    "asking": (1, 3, [1, 2, 4, 2]),         # 8 Puzzled, question marks (the box frame left out)
}
BANDS = [0, 170, 345, 530, 700, 870, 1024]

sheet = Image.open(SRC).convert("RGBA")
alpha = np.array(sheet)[:, :, 3]


def frames_of(col, band):
    x0, x1 = (0, 768) if col == 0 else (768, 1536)
    y0, y1 = BANDS[band], BANDS[band + 1]
    mask = alpha[y0:y1, x0:x1] > 96
    labels, n = ndimage.label(mask)
    for i, sl in enumerate(ndimage.find_objects(labels), 1):
        h, w = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        if sl[0].start < 50 and sl[1].start < 60 and h < 48 and w > 60:  # the row's label pill
            mask[labels == i] = False
    cols = mask.sum(0) > 0
    runs, s = [], None
    for x, v in enumerate(cols):
        if v and s is None:
            s = x
        if not v and s is not None:
            runs.append([s, x]); s = None
    if s is not None:
        runs.append([s, len(cols)])
    merged = []  # join pieces closer than 10 px (a "?" beside a head), drop specks
    for r in runs:
        if merged and r[0] - merged[-1][1] < 10:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    merged = [r for r in merged if r[1] - r[0] > 30]
    assert len(merged) == 5, (col, band, merged)
    out = []
    for a, b in merged:
        ys = np.where(mask[:, a:b].any(1))[0]
        box = (x0 + a, y0 + ys[0], x0 + b, y0 + ys[-1] + 1)
        out.append(sheet.crop(box))
    return out


os.makedirs(OUT, exist_ok=True)
for name, (col, band, keep) in STRIPS.items():
    frames = frames_of(col, band)
    side = max(max(f.width, f.height) for f in frames) + 6
    strip = Image.new("RGBA", (OUT_PX * len(keep), OUT_PX))
    for i, k in enumerate(keep):
        f = frames[k]
        canvas = Image.new("RGBA", (side, side))
        canvas.paste(f, ((side - f.width) // 2, side - 3 - f.height))
        strip.paste(canvas.resize((OUT_PX, OUT_PX), Image.LANCZOS), (i * OUT_PX, 0))
    strip.save(os.path.join(OUT, name + ".webp"), "WEBP", quality=85, method=6)
    print("wrote %s (%d frames)" % (name, len(keep)))

# the package's logo (README, tray icon): the calm octopus's first pose, cut close to its drawing
f = frames_of(*STRIPS["calm"][:2])[0]
side = max(f.width, f.height)
logo = Image.new("RGBA", (side, side))
logo.paste(f, ((side - f.width) // 2, side - f.height))
logo.resize((256, 256), Image.LANCZOS).save(os.path.join(OUT, "logo.png"), optimize=True)
print("wrote logo.png")
