"""Real ground colour for the terrain material: the sectors' colour.jpg (4 m a pixel, the orthophoto with canopy and roads
replaced, graded) stitched over the block and a ring of neighbour sectors, into <out>/colour.png, with its placement in
<out>/colour.json (x0 west, ytop north, size in metres, pixels).

    uv run python prep_colour.py C:/Users/jack/berat70scale-1m --si 4 6 --sj 4 6 --out C:/Users/jack/berat-cache/b3x3
"""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("package", type=Path)
    ap.add_argument("--si", type=int, nargs=2, required=True)
    ap.add_argument("--sj", type=int, nargs=2, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--ring", type=int, default=1, help="neighbour sectors added around the block")
    a = ap.parse_args()
    man = json.loads((a.package / "manifest.json").read_text())
    size = man["sector_size"]
    have = {tuple(s) for s in man["sectors"]}
    i0, i1 = a.si[0] - a.ring, a.si[1] + a.ring
    j0, j1 = a.sj[0] - a.ring, a.sj[1] + a.ring
    per = 800                                     # 801 pixels a sector, the last shared with the next
    nx, ny = (i1 - i0 + 1) * per + 1, (j1 - j0 + 1) * per + 1
    img = np.zeros((ny, nx, 3), np.uint8)
    filled = np.zeros((ny, nx), bool)
    for j in range(j0, j1 + 1):
        for i in range(i0, i1 + 1):
            if (i, j) not in have:
                continue
            c = np.asarray(Image.open(a.package / "sectors" / f"{i}_{j}" / "colour.jpg").convert("RGB"))
            r0, c0 = (j1 - j) * per, (i - i0) * per
            img[r0:r0 + 801, c0:c0 + 801] = c[:801, :801]
            filled[r0:r0 + 801, c0:c0 + 801] = True
    if not filled.all():                          # outside the package: the mean colour
        img[~filled] = img[filled].mean(axis=0).astype(np.uint8)
    a.out.mkdir(parents=True, exist_ok=True)
    Image.fromarray(img).save(a.out / "colour.png")
    info = {"x0": size * i0 - 16000, "ytop": size * (j1 + 1) - 16000, "size_m": float((nx - 1) * size / per),
            "pixels": [nx, ny]}
    (a.out / "colour.json").write_text(json.dumps(info, indent=1))
    print(info)


if __name__ == "__main__":
    main()
