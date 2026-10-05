"""Prepare the per-sector objects of a map package for the Unreal importer (reads the package, never writes to it).

Into <out>/sectors/<si>_<sj>/:
  water.glb      water areas (flat at each outline point's level, triangulated) and streams (ribbons at their width),
                 one material "water", same glTF conventions as the package (node at the sector's south-west corner)
  plants.bin     instances, little-endian: header int32 count, then count x (float32 x, y, z, height, yaw, int32 kind)
                 in Unreal centimetres (X = x*100, Y = -y*100, Z = z*100), kind: see KINDS
  lamps.bin      street lamps along lit or urban roads: int32 count, then count x float32 (x, y, z, yaw) in Unreal cm
                 (yaw in degrees, Unreal convention, the arm pointing at the road)
and <out>/lanes.json: the lane graph of all the sectors, points converted to Unreal cm.

    uv run python prep_objects.py C:/Users/jack/berat70scale-1m --si 4 6 --sj 4 6 --out C:/Users/jack/berat-cache/b3x3
"""

import argparse
import gzip
import json
import math
import struct
import sys
import time
from pathlib import Path

import numpy as np
import shapely

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "package"))
import gltf  # noqa: E402  the package's own numpy glTF writer

# Instance kinds: 0..4 the package's tree kinds, then:
KINDS = ["tree_unknown", "tree_broadleaf", "tree_conifer", "tree_poplar", "tree_fruit", "shrub", "hedge", "vine"]
HEDGE_STEP = 1.6      # m between hedge plants
VINE_STEP = 1.2       # m between vine stocks
LAMP_STEP = 32.0      # m between lamps on one side
LAMP_SET = 1.2        # m from the drawn edge


def ue(x, y, z):
    return x * 100.0, -y * 100.0, z * 100.0


def plants(sector: Path, rng) -> np.ndarray:
    v = json.load(gzip.open(sector / "vegetation.json.gz"))
    out = []
    t = np.asarray(v["trees"]["rows"], np.float64).reshape(-1, 5)
    if len(t):
        out.append(np.column_stack([t[:, :4], rng.uniform(0, 360, len(t)), t[:, 4]]))
    s = np.asarray(v["shrubs"]["rows"], np.float64).reshape(-1, 4)
    if len(s):
        out.append(np.column_stack([s, rng.uniform(0, 360, len(s)), np.full(len(s), 5)]))
    for hedge in v["hedges"]:
        p = np.asarray(hedge["points"], np.float64)
        seg = np.diff(p, axis=0)
        ln = np.hypot(seg[:, 0], seg[:, 1])
        cum = np.concatenate([[0], np.cumsum(ln)])
        if cum[-1] < 0.5:
            continue
        d = np.arange(HEDGE_STEP / 2, cum[-1], HEDGE_STEP)
        pts = np.column_stack([np.interp(d, cum, p[:, k]) for k in range(3)])
        h = hedge["height"] * rng.uniform(0.8, 1.15, len(d))
        out.append(np.column_stack([pts, h, rng.uniform(0, 360, len(d)), np.full(len(d), 6)]))
    vn = np.asarray(v["vines"]["rows"], np.float64).reshape(-1, 6)
    for x0, y0, z0, x1, y1, z1 in vn:
        ln = math.hypot(x1 - x0, y1 - y0)
        f = np.arange(VINE_STEP / 2, ln, VINE_STEP) / max(ln, 1e-6)
        yaw = -math.degrees(math.atan2(y1 - y0, x1 - x0))
        out.append(np.column_stack([x0 + f * (x1 - x0), y0 + f * (y1 - y0), z0 + f * (z1 - z0),
                                    np.full(len(f), 1.4), np.full(len(f), yaw), np.full(len(f), 7)]))
    a = np.concatenate(out) if out else np.zeros((0, 6))
    a[:, 0], a[:, 1], a[:, 2] = a[:, 0] * 100, -a[:, 1] * 100, a[:, 2] * 100
    a[:, 3] *= 100
    return a


def write_instances(path: Path, a: np.ndarray, kind_col: bool) -> None:
    with open(path, "wb") as f:
        f.write(struct.pack("<i", len(a)))
        if kind_col:
            rec = np.zeros(len(a), dtype=[("f", "<f4", 5), ("k", "<i4")])
            rec["f"] = a[:, :5]
            rec["k"] = a[:, 5]
            f.write(rec.tobytes())
        else:
            f.write(a.astype("<f4").tobytes())


def lamps(sector: Path) -> np.ndarray:
    g = json.loads((sector / "roads.geojson").read_text(encoding="utf-8"))
    out = []
    for feat in g["features"]:
        p = feat["properties"]
        if not (p["lit"] or p["urban"]) or p["tunnel"] or p["dirt"] or p["class"] in ("track", "motorway", "ramp"):
            continue
        xyz = np.asarray(feat["geometry"]["coordinates"], np.float64)
        a, b = p["drawn_from"], p["drawn_to"]
        if a is None or b is None:                      # the whole piece lies in junctions
            continue
        xyz, hw = xyz[a:b + 1], np.asarray(p["half_width"], np.float64)[a:b + 1]
        if len(xyz) < 2:
            continue
        seg = np.diff(xyz[:, :2], axis=0)
        cum = np.concatenate([[0], np.cumsum(np.hypot(seg[:, 0], seg[:, 1]))])
        if cum[-1] < LAMP_STEP * 0.6:
            continue
        # one side, then the other, staggered; the start is fixed by the piece's distance along the link so it is stable
        s0 = p["s"][a] if p.get("s") else 0.0
        first = (-s0) % LAMP_STEP
        for side, shift in ((1, 0.0), (-1, LAMP_STEP / 2)):
            for d in np.arange(first + shift, cum[-1], LAMP_STEP):
                i = min(np.searchsorted(cum, d, side="right") - 1, len(seg) - 1)
                f = (d - cum[i]) / max(cum[i + 1] - cum[i], 1e-6)
                c = xyz[i] + f * (xyz[i + 1] - xyz[i])
                tx, ty = seg[i] / max(np.hypot(*seg[i]), 1e-6)
                nx, ny = -ty * side, tx * side                      # left normal for side 1
                off = hw[i] + f * (hw[i + 1] - hw[i]) + LAMP_SET
                x, y = c[0] + nx * off, c[1] + ny * off
                # arm points back at the road: direction (-nx, -ny) in package axes -> Unreal yaw (Y flipped)
                yaw = math.degrees(math.atan2(ny, -nx))
                out.append((x * 100, -y * 100, (c[2] - 0.05) * 100, yaw))
    return np.asarray(out, np.float64).reshape(-1, 4)


# Shutter colours of the Toulouse countryside (sRGB 0..1): grey-blue, sage, oxblood, white, brown, pastel blue.
SHUTTERS = [(0.42, 0.5, 0.56), (0.5, 0.58, 0.5), (0.45, 0.16, 0.13), (0.88, 0.87, 0.82), (0.36, 0.25, 0.18), (0.55, 0.66, 0.74)]
PROUD = 0.04          # m: openings stand this far out of the wall (the wall is not cut)


def openings(sector: Path) -> gltf.Mesh:
    """Windows, doors, garages, shopfronts and shutters from the facade layout of buildings.geojson: one quad per opening
    (UV 0..1 over the opening), on the wall at its place, sill and size. Vertex colour: R a random per opening (which windows
    light up at night), G 1 on apartment-like buildings, B unused; shutters carry their colour."""
    g = json.loads((sector / "buildings.geojson").read_text(encoding="utf-8"))
    mesh = gltf.Mesh()
    quads = {}

    def quad(material, a, b, z0, z1, n, colour):
        # a, b: bottom corners (x, y) along the wall; n: outward normal; counter-clockwise seen from outside
        a = np.asarray(a) + n * PROUD
        b = np.asarray(b) + n * PROUD
        pos = np.array([[a[0], a[1], z0], [b[0], b[1], z0], [b[0], b[1], z1], [a[0], a[1], z1]])
        q = quads.setdefault(material, dict(p=[], c=[]))
        q["p"].append(pos)
        q["c"].append(colour)

    for feat in g["features"]:
        p = feat["properties"]
        if not p.get("walls"):
            continue
        ring = np.asarray(feat["geometry"]["coordinates"][0], np.float64)[:, :2]
        if np.allclose(ring[0], ring[-1]):
            ring = ring[:-1]
        n_pts = len(ring)
        rng = np.random.default_rng(p["seed"] & 0xFFFFFFFF)
        shutter = SHUTTERS[p["seed"] % len(SHUTTERS)]
        old = p["era"] in ("before_1950", "1950_1970", "unknown") and p["use"] in ("house", "barn", "townhall", "school")
        has_shutters = old and rng.random() < 0.85 or (p["use"] == "house" and rng.random() < 0.45)
        flats = 1.0 if (p.get("floors") or 1) >= 3 else 0.0
        for w in p["walls"]:
            pts = ring[[(w["first"] + j) % n_pts for j in range(w["count"] + 1)]]
            seg = np.diff(pts, axis=0)
            ln = np.hypot(seg[:, 0], seg[:, 1])
            cum = np.concatenate([[0], np.cumsum(ln)])
            total = cum[-1]
            if total < 0.5:
                continue
            g0, g1 = w["ground"]
            for o in w["openings"]:
                t, width, height, sill = o["at"], o["width"], o["height"], o["sill"]
                i = int(min(np.searchsorted(cum, t, side="right") - 1, len(seg) - 1))
                d = seg[i] / max(ln[i], 1e-9)
                nrm = np.array([d[1], -d[0]])                         # outward for a counter-clockwise outline
                c = pts[i] + d * (t - cum[i])
                ground = g0 + (g1 - g0) * t / total
                z0, z1 = ground + sill, ground + sill + height
                a, b = c - d * width / 2, c + d * width / 2
                kind = o["type"]
                material = {"window": "window", "door": "door", "garage": "garage", "shopfront": "shopfront",
                            "balcony": "window"}.get(kind, "window")
                quad(material, a, b, z0, z1, nrm, (float(rng.random()), flats, 0.0))
                if kind == "window" and has_shutters and height < 2.4:
                    sw = width / 2
                    quad("shutter", a - d * sw, a, z0, z1, nrm, shutter)
                    quad("shutter", b, b + d * sw, z0, z1, nrm, shutter)

    uv = np.array([[0, 1], [1, 1], [1, 0], [0, 0]], np.float64)
    for material, q in quads.items():
        pos = np.concatenate(q["p"])
        k = len(q["p"])
        tris = (np.array([[0, 1, 2], [0, 2, 3]])[None, :, :] + 4 * np.arange(k)[:, None, None]).reshape(-1, 3)
        mesh.add(material, pos, tris, uv0=np.tile(uv, (k, 1)), colour=np.repeat(np.asarray(q["c"], np.float64), 4, axis=0))
    return mesh


def water(sector: Path) -> gltf.Mesh:
    g = json.loads((sector / "water.geojson").read_text(encoding="utf-8"))
    mesh = gltf.Mesh()
    for feat in g["features"]:
        geom, p = feat["geometry"], feat["properties"]
        if geom["type"] == "Polygon":
            rings = [np.asarray(r, np.float64) for r in geom["coordinates"]]
            poly = shapely.Polygon(rings[0][:, :2], [r[:, :2] for r in rings[1:]])
            if not poly.is_valid:
                poly = shapely.make_valid(poly)
            pts_all = np.concatenate(rings)
            for tri in shapely.get_parts(shapely.constrained_delaunay_triangles(poly)):
                c = np.asarray(tri.exterior.coords)[:3]
                # level at each corner: the nearest outline point's level (levels vary along a canal)
                d = ((pts_all[None, :, :2] - c[:, None, :]) ** 2).sum(-1)
                z = pts_all[d.argmin(1), 2]
                pos = np.column_stack([c[:, 0], c[:, 1], z])
                # counter-clockwise from above
                if (pos[1, 0] - pos[0, 0]) * (pos[2, 1] - pos[0, 1]) - (pos[1, 1] - pos[0, 1]) * (pos[2, 0] - pos[0, 0]) < 0:
                    pos = pos[::-1]
                mesh.add("water", pos, [[0, 1, 2]], uv0=pos[:, :2])
        elif geom["type"] == "LineString":
            p3 = np.asarray(geom["coordinates"], np.float64)
            if len(p3) < 2:
                continue
            w = max(float(p.get("width") or 1.0), 0.6) / 2
            seg = np.diff(p3[:, :2], axis=0)
            tang = np.vstack([seg[:1], seg[:-1] + seg[1:], seg[-1:]])
            tang /= np.maximum(np.linalg.norm(tang, axis=1, keepdims=True), 1e-9)
            nrm = np.column_stack([-tang[:, 1], tang[:, 0]])
            left = p3[:, :2] + nrm * w
            right = p3[:, :2] - nrm * w
            n = len(p3)
            pos = np.empty((2 * n, 3))
            pos[0::2, :2], pos[1::2, :2] = right, left
            pos[:, 2] = np.repeat(p3[:, 2] + 0.02, 2)
            tris = []
            for i in range(n - 1):
                a, b, c2, d = 2 * i, 2 * i + 1, 2 * i + 2, 2 * i + 3
                tris += [[a, c2, b], [b, c2, d]]
            mesh.add("water", pos, tris, uv0=pos[:, :2])
    return mesh


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("package", type=Path)
    ap.add_argument("--si", type=int, nargs=2, required=True)
    ap.add_argument("--sj", type=int, nargs=2, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    t0 = time.time()
    man = json.loads((args.package / "manifest.json").read_text())
    size = man["sector_size"]
    rng = np.random.default_rng(11)
    lanes, controls = [], None
    summary = {}
    for sj in range(args.sj[0], args.sj[1] + 1):
        for si in range(args.si[0], args.si[1] + 1):
            src = args.package / "sectors" / f"{si}_{sj}"
            dst = args.out / "sectors" / f"{si}_{sj}"
            dst.mkdir(parents=True, exist_ok=True)
            pl = plants(src, rng)
            write_instances(dst / "plants.bin", pl, True)
            lp = lamps(src)
            write_instances(dst / "lamps.bin", lp, False)
            corner = (size * si - 16000, size * sj - 16000)
            wm = water(src)
            if len(wm):
                gltf.write_glb(dst / "water.glb", wm, corner, {"water": {"colour": (0.1, 0.2, 0.25), "roughness": 0.05}},
                               f"water_{si}_{sj}")
            om = openings(src)
            if len(om):
                gltf.write_glb(dst / "openings.glb", om, corner,
                               {m: {"colour": (0.5, 0.5, 0.5)} for m in ("window", "door", "garage", "shopfront", "shutter")},
                               f"openings_{si}_{sj}")
            lg = json.load(gzip.open(src / "lanes.json.gz"))
            controls = controls or lg["controls"]
            for e in lg["elements"]:
                e["points"] = [ue(*q) for q in e["points"]]
                lanes.append(e)
            summary[f"{si}_{sj}"] = {"plants": len(pl), "lamps": len(lp), "water_tris": len(wm), "opening_tris": len(om),
                                     "by_kind": np.bincount(pl[:, 5].astype(int), minlength=len(KINDS)).tolist()}
    (args.out / "lanes.json").write_text(json.dumps({"controls": controls, "kinds": KINDS, "elements": lanes}))
    (args.out / "objects.json").write_text(json.dumps({"kinds": KINDS, "sectors": summary,
                                                       "seconds": round(time.time() - t0, 1)}, indent=1))
    tot = np.sum([s["by_kind"] for s in summary.values()], axis=0)
    print(f"{len(lanes)} lanes, {sum(s['lamps'] for s in summary.values())} lamps, "
          f"{sum(s['water_tris'] for s in summary.values())} water triangles, plants:",
          dict(zip(KINDS, tot.tolist())), f"{time.time() - t0:.1f} s")


if __name__ == "__main__":
    main()
