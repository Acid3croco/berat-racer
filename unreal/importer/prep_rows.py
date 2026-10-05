"""Crop-row directions of the block as a texture for the terrain material's furrows. The package's rows.png (per sector,
1 m, 1 + 254 * angle / pi from east counter-clockwise, 0 none) becomes one 2 m texture: R, G = cos, sin of twice the angle
(halved again in the material: filtering a doubled angle does not jump at 0 / pi), B = 255 where there are rows.
Writes <out>/rows_dir.png and rows_dir.json.

    uv run python prep_rows.py C:/Users/jack/berat70scale-1m --si 4 6 --sj 4 6 --out C:/Users/jack/berat-cache/b3x3
"""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

CELL = 2.0          # m per texel


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("package", type=Path)
    ap.add_argument("--si", type=int, nargs=2, required=True)
    ap.add_argument("--sj", type=int, nargs=2, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    size = 3200
    nsi, nsj = a.si[1] - a.si[0] + 1, a.sj[1] - a.sj[0] + 1
    per = int(size / CELL)
    rgb = np.zeros((nsj * per, nsi * per, 3), np.uint8)
    for sj in range(a.sj[0], a.sj[1] + 1):
        for si in range(a.si[0], a.si[1] + 1):
            v = np.asarray(Image.open(a.package / "sectors" / f"{si}_{sj}" / "rows.png"))[:-1, :-1]
            v = v[::int(CELL), ::int(CELL)].astype(np.float32)                    # rows are constant per parcel
            ang = (v - 1.0) / 254.0 * np.pi * 2.0
            tile = np.stack([np.cos(ang) * 127.5 + 127.5, np.sin(ang) * 127.5 + 127.5, np.where(v > 0, 255.0, 0.0)], -1)
            tile[v == 0, :2] = 127.5
            r0 = (a.sj[1] - sj) * per                                             # north row first
            c0 = (si - a.si[0]) * per
            rgb[r0:r0 + per, c0:c0 + per] = np.clip(tile, 0, 255).astype(np.uint8)
    Image.fromarray(rgb).save(a.out / "rows_dir.png")
    info = {"x0": size * a.si[0] - 16000, "ytop": size * (a.sj[1] + 1) - 16000, "cell": CELL, "pixels": [rgb.shape[1], rgb.shape[0]]}
    (a.out / "rows_dir.json").write_text(json.dumps(info, indent=1))
    print(info, f"{(rgb[..., 2] > 0).mean() * 100:.1f}% rowed")


if __name__ == "__main__":
    main()
