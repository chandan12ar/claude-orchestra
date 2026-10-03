"""Turn the frames from capture-pill.mjs into the animated GIFs used by the README.

    node docs/evidence/capture-pill.mjs
    python docs/evidence/make_pill_gif.py

Needs Pillow. Writes docs/assets/pill-light.gif and pill-dark.gif, and removes the frames.
"""

import glob
import os
import shutil

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ASSETS = os.path.join(ROOT, "docs", "assets")
FRAMES = os.path.join(ASSETS, ".frames")
FPS = 12
WIDTH = 540            # the frames are 720 wide; this keeps the file small and still sharp


def build(scheme):
    paths = sorted(glob.glob(os.path.join(FRAMES, scheme, "*.png")))
    if not paths:
        raise SystemExit("no frames for %s: run capture-pill.mjs first" % scheme)
    frames = []
    for path in paths:
        img = Image.open(path).convert("RGB")
        img = img.resize((WIDTH, round(img.height * WIDTH / img.width)), Image.LANCZOS)
        frames.append(img.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE))
    out = os.path.join(ASSETS, "pill-%s.gif" % scheme)
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=round(1000 / FPS),
                   loop=0, optimize=True, disposal=1)
    print("%s: %d frames, %d KB" % (os.path.basename(out), len(frames), os.path.getsize(out) // 1024))


if __name__ == "__main__":
    os.makedirs(ASSETS, exist_ok=True)
    for scheme in ("light", "dark"):
        build(scheme)
    shutil.rmtree(FRAMES, ignore_errors=True)
