"""Prepare a block of map-package sectors for the Unreal importer.

Reads the package (never writes to it) and writes, into a cache folder outside the repo:

  block.json        what the block is: sectors, landscape size, transform (cm), Z mapping, layers, checkpoints
  height.r16        uint16 little-endian, rows north to south: the package's height.png values, unchanged
  layer_<name>.r8   uint8 weight per land-cover class (soft edges, summing to 255), only classes present
  holes.r8          uint8, 255 where the terrain is cut away (tunnel portals)

The landscape grid is the package's grid (1 vertex per package vertex). Its size is rounded up to a whole number of
components; the extra rows and columns (east and south) are filled from the neighbouring sectors when the package has
them, else by repeating the edge.

    uv run python prep.py C:/Users/jack/berat70scale-1m --si 4 6 --sj 4 6 --out C:/Users/jack/berat-cache/b3x3
"""

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image


def read_png(path: Path) -> np.ndarray:
    im = Image.open(path)
    return np.asarray(im, dtype=np.uint16 if im.mode.startswith("I;16") else np.uint8)


def layer_name(cls: str) -> str:
    """Unreal layer name of a package class. "none" would be Unreal's NAME_None (FName is case-insensitive): it is "bare"."""
    return "bare" if cls == "none" else cls.replace(" ", "_")


def box_blur(a: np.ndarray, r: int) -> np.ndarray:
    """Mean over a (2r+1)^2 window, edges clamped. float32 in, float32 out."""
    if r <= 0:
        return a
    for axis in (0, 1):
        p = np.pad(a, [(r + 1, r) if ax == axis else (0, 0) for ax in (0, 1)], mode="edge")
        c = np.cumsum(p, axis=axis, dtype=np.float64)
        hi = np.take(c, np.arange(2 * r + 1, c.shape[axis]), axis=axis)
        lo = np.take(c, np.arange(0, c.shape[axis] - 2 * r - 1), axis=axis)
        a = ((hi - lo) / (2 * r + 1)).astype(np.float32)
    return a


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("package", type=Path)
    ap.add_argument("--si", type=int, nargs=2, required=True, help="first and last sector index east")
    ap.add_argument("--sj", type=int, nargs=2, required=True, help="first and last sector index north")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--quads", type=int, default=127, help="quads per landscape section (7, 15, 31, 63, 127, 255)")
    ap.add_argument("--sections", type=int, default=1, help="sections per component (1 or 2)")
    ap.add_argument("--blur", type=int, default=1, help="weightmap blur radius in vertices (soft class edges)")
    ap.add_argument("--checkpoints", type=int, default=24)
    args = ap.parse_args()
    t0 = time.time()

    man = json.loads((args.package / "manifest.json").read_text())
    size, cell, n = man["sector_size"], man["cell"], man["samples"]
    per = n - 1                                   # vertices per sector without the shared edge
    have = {tuple(s) for s in man["sectors"]}
    si0, si1 = args.si
    sj0, sj1 = args.sj
    block = [(i, j) for j in range(sj0, sj1 + 1) for i in range(si0, si1 + 1)]
    missing = [s for s in block if s not in have]
    if missing:
        raise SystemExit(f"sectors not in the package: {missing}")

    comp = args.quads * args.sections
    real = (si1 - si0 + 1) * per + 1, (sj1 - sj0 + 1) * per + 1          # vertices east, north
    ncomp = math.ceil((real[0] - 1) / comp), math.ceil((real[1] - 1) / comp)
    w, h = ncomp[0] * comp + 1, ncomp[1] * comp + 1

    # Grid placement: column c is x = x0 + c * cell; row r is y = ytop - r * cell (row 0 north).
    x0 = size * si0 - 16000
    ytop = size * (sj1 + 1) - 16000
    # The padding runs east (more columns) and south (more rows), so the block's north-west corner stays put.
    # Sectors that can feed the padded window:
    jmin = sj0 - math.ceil((h - real[1]) / per)
    imax = si1 + math.ceil((w - real[0]) / per)

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    height = np.zeros((h, w), np.uint16)
    classes = np.zeros((h, w), np.uint8)
    holes = np.zeros((h, w), np.uint8)
    filled = np.zeros((h, w), bool)
    for j in range(jmin, sj1 + 1):
        for i in range(si0, imax + 1):
            if (i, j) not in have:
                continue
            c0 = (i - si0) * per
            r0 = (sj1 - j) * per
            if c0 >= w or r0 >= h:
                continue
            d = args.package / "sectors" / f"{i}_{j}"
            cw, rh = min(n, w - c0), min(n, h - r0)
            height[r0:r0 + rh, c0:c0 + cw] = read_png(d / "height.png")[:rh, :cw]
            classes[r0:r0 + rh, c0:c0 + cw] = read_png(d / "classes.png")[:rh, :cw]
            holes[r0:r0 + rh, c0:c0 + cw] = read_png(d / "holes.png")[:rh, :cw]
            filled[r0:r0 + rh, c0:c0 + cw] = True
    if not filled.all():                         # beyond the package's border: repeat the last filled row / column
        cols = np.where(filled.all(axis=0))[0]
        rows = np.where(filled.all(axis=1))[0]
        lc, lr = cols.max(), rows.max()
        for a in (height, classes, holes):
            a[:, lc + 1:] = a[:, lc:lc + 1]
            a[lr + 1:, :] = a[lr:lr + 1, :]
    t_read = time.time() - t0

    height.astype("<u2").tofile(out / "height.r16")
    holes.tofile(out / "holes.r8")

    names = man["classes"]
    present = [k for k in range(len(names)) if (classes == k).any()]
    soft = {}
    total = np.zeros((h, w), np.float32)
    for k in present:
        soft[k] = box_blur((classes == k).astype(np.float32), args.blur)
        total += soft[k]
    layers = []
    for k in present:
        wgt = np.round(soft[k] / total * 255).astype(np.uint8)
        name = layer_name(names[k])
        wgt.tofile(out / f"layer_{name}.r8")
        layers.append({"id": k, "name": name, "share": float((classes == k).mean())})
    del soft, total
    t_layers = time.time() - t0 - t_read

    # Height mapping. Package: z = min + v / 65535 * (max - min) (m). Unreal landscape:
    # Z = loc_z + (v - 32768) * scale_z / 128 (cm). Matching both for every v:
    zmin, zmax = man["height"]["min"], man["height"]["max"]
    scale_z = 128 * 100 * (zmax - zmin) / 65535
    loc_z = 100 * zmin + 32768 * 100 * (zmax - zmin) / 65535

    # Checkpoints: float heights from height.tif at vertices of the real block (not the padding), plus the spawn.
    rng = np.random.default_rng(7)
    checks = []
    tifs = {}
    sp = man["spawn"]
    pts = [(sp["x"], sp["y"])] + [(x0 + rng.uniform(0, real[0] - 1) * cell, ytop - rng.uniform(0, real[1] - 1) * cell)
                                   for _ in range(args.checkpoints)]
    for x, y in pts:
        c, r = round((x - x0) / cell), round((ytop - y) / cell)
        if not (0 <= c < real[0] and 0 <= r < real[1]):
            continue
        i, j = si0 + min(c // per, si1 - si0), sj1 - min(r // per, sj1 - sj0)
        if (i, j) not in tifs:
            tifs[(i, j)] = tifffile.imread(args.package / "sectors" / f"{i}_{j}" / "height.tif")
        z_tif = float(tifs[(i, j)][r - (sj1 - j) * per, c - (i - si0) * per])
        z_png = zmin + float(height[r, c]) / 65535 * (zmax - zmin)
        checks.append({"x": x0 + c * cell, "y": ytop - r * cell, "z_tif": round(z_tif, 4), "z_png": round(z_png, 4)})

    info = {
        "package": str(args.package),
        "tag": man["tag"],
        "sectors": block,
        "origin_l93": man["origin"],
        "cell": cell,
        "real_size": real,
        "size": [w, h],
        "components": ncomp,
        "quads_per_section": args.quads,
        "sections_per_component": args.sections,
        "x_west": x0, "y_north": ytop,
        "location_cm": [x0 * 100, -ytop * 100, loc_z],
        "scale_cm": [cell * 100, cell * 100, scale_z],
        "height": {"min": zmin, "max": zmax},
        "layers": layers,
        "holes": int((holes > 0).sum()),
        "spawn": sp,
        "checkpoints": checks,
        "seconds": {"read": round(t_read, 1), "layers": round(t_layers, 1), "total": round(time.time() - t0, 1)},
    }
    (out / "block.json").write_text(json.dumps(info, indent=1))
    print(json.dumps({k: info[k] for k in ("size", "components", "location_cm", "scale_cm", "holes", "seconds")}))
    print("layers:", ", ".join(f"{l['name']} {l['share']:.1%}" for l in layers))
    err = max(abs(c["z_tif"] - c["z_png"]) for c in checks)
    print(f"checkpoints: {len(checks)}, png vs tif max {err * 100:.2f} cm")


if __name__ == "__main__":
    main()
