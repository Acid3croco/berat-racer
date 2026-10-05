"""Building finishes from the package's data: each wall and roof triangle of buildings.glb is matched to its building
(buildings.geojson footprint) and given a material by the building's use, wall material, era, roof shape and roof code;
the geometry, UVs and vertex colours stay the package's. Writes <out>/sectors/<s>/buildings_styled.glb.

    uv run python prep_buildings.py C:/Users/jack/berat70scale-1m --si 4 6 --sj 4 6 --out C:/Users/jack/berat-cache/b3x3

Materials (the Unreal importer maps them by name): wall_render, wall_brick, wall_stone, wall_concrete, wall_metal, wall_wood;
roof_canal, roof_canal_b, roof_canal_c, roof_slate, roof_metal, roof_fibre, roof_flat. UV1.x carries a per-building random
in 0..1 (shade variations in the materials).
"""

import argparse
import json
import struct
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np
import shapely

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "package"))
import gltf  # noqa: E402


def read_glb(path: Path):
    """[(material, positions (package axes, absolute), uv0, colour, triangles)] of a package glb."""
    d = path.read_bytes()
    n = struct.unpack("<I", d[12:16])[0]
    doc = json.loads(d[20:20 + n])
    blob = d[20 + n + 8:]
    tx, ty, tz = doc["nodes"][0].get("translation", [0.0, 0.0, 0.0])

    def acc(i, comps, dtype="<f4"):
        a = doc["accessors"][i]
        v = doc["bufferViews"][a["bufferView"]]
        return np.frombuffer(blob, dtype, a["count"] * comps, v["byteOffset"] + a.get("byteOffset", 0)).reshape(-1, comps)

    out = []
    for p in doc["meshes"][0]["primitives"]:
        at = p["attributes"]
        g = acc(at["POSITION"], 3).astype(np.float64)
        pos = np.column_stack([g[:, 0] + tx, -(g[:, 2] + tz), g[:, 1] + ty])
        uv = acc(at["TEXCOORD_0"], 2).astype(np.float64) if "TEXCOORD_0" in at else np.zeros((len(pos), 2))
        col = acc(at["COLOR_0"], 3).astype(np.float64) if "COLOR_0" in at else np.ones((len(pos), 3))
        tri = acc(p["indices"], 1, "<u4").reshape(-1, 3)
        out.append((doc["materials"][p["material"]]["name"], pos, uv, col, tri))
    return out


def finish(p: dict, area: float, rnd: float) -> tuple[str, str]:
    """(wall material, roof material) of one building, from its data; rnd in 0..1, stable per building."""
    use, wm, era = p.get("use") or "house", p.get("wall_material"), p.get("era") or "unknown"
    roof, code = p.get("roof") or "gabled", p.get("roof_material_code")
    old = era == "before_1950"
    big = area > 180
    # walls
    if use in ("industrial", "silo") or (use == "shed" and area > 250):
        wall = "wall_concrete" if use == "silo" or rnd < 0.25 else "wall_metal"
    elif use == "greenhouse":
        wall = "wall_metal"
    elif use == "barn":
        wall = "wall_brick" if old or rnd < 0.3 else ("wall_wood" if rnd < 0.55 else "wall_metal")
    elif use in ("church", "chapel"):
        wall = "wall_stone" if wm in ("stone", "millstone") else "wall_brick"
    elif wm in ("stone", "millstone"):
        wall = "wall_stone"
    elif wm == "wood":
        wall = "wall_wood"
    elif wm == "brick":
        # Toulouse brick: left bare on old buildings (and some outbuildings), rendered (crepi) on most later houses
        wall = "wall_brick" if old or (use == "shed" and rnd < 0.5) or rnd < 0.15 else "wall_render"
    elif wm in ("concrete", "breeze_block"):
        wall = "wall_concrete" if use == "shed" and rnd < 0.5 else "wall_render"
    elif use == "shed":
        wall = "wall_render" if rnd < 0.45 else ("wall_brick" if rnd < 0.7 else ("wall_wood" if rnd < 0.85 else "wall_metal"))
    else:
        wall = "wall_brick" if old and rnd < 0.35 else "wall_render"
    # roofs
    if roof == "flat":
        r = "roof_flat"
    elif code == 2:
        r = "roof_slate"
    elif code == 3:
        r = "roof_metal"
    elif use in ("industrial",) or (use == "shed" and big):
        r = "roof_fibre" if rnd < 0.6 else "roof_metal"
    elif use == "barn":
        r = "roof_canal" if old or rnd < 0.45 else "roof_fibre"
    elif use == "greenhouse":
        r = "roof_metal"
    else:
        r = ("roof_canal", "roof_canal_b", "roof_canal_c")[int(rnd * 3) % 3]
    return wall, r


def style_sector(src: Path, dst: Path, corner) -> Counter:
    g = json.loads((src / "buildings.geojson").read_text(encoding="utf-8"))
    polys, props = [], []
    for f in g["features"]:
        ring = np.asarray(f["geometry"]["coordinates"][0], np.float64)[:, :2]
        holes = [np.asarray(h, np.float64)[:, :2] for h in f["geometry"]["coordinates"][1:]]
        polys.append(shapely.Polygon(ring, holes))
        props.append(f["properties"])
    tree = shapely.STRtree(polys)
    areas = shapely.area(np.asarray(polys))
    looks = []
    for p, a in zip(props, areas):
        rnd = ((p.get("seed") or 0) * 2654435761 % 2**32) / 2**32
        looks.append((finish(p, a, rnd), rnd))
    mesh = gltf.Mesh()
    counts = Counter()
    for material, pos, uv, col, tri in read_glb(src / "buildings.glb"):
        cen = pos[tri].mean(axis=1)
        pts = shapely.points(cen[:, 0], cen[:, 1])
        idx = tree.query_nearest(pts, return_distance=False, all_matches=False)[1]
        for b in np.unique(idx):
            (wall, roof), rnd = looks[b]
            name = wall if material == "wall" else roof
            t = tri[idx == b]
            used, inv = np.unique(t, return_inverse=True)
            uv1 = np.column_stack([np.full(len(used), rnd), np.zeros(len(used))])
            mesh.add(name, pos[used], inv.reshape(-1, 3), uv0=uv[used], uv1=uv1, colour=col[used])
            counts[name] += len(t)
    mats = {m: {"colour": (0.7, 0.7, 0.7)} for m in mesh.parts}
    gltf.write_glb(dst / "buildings_styled.glb", mesh, corner, mats, f"buildings_{dst.name}")
    return counts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("package", type=Path)
    ap.add_argument("--si", type=int, nargs=2, required=True)
    ap.add_argument("--sj", type=int, nargs=2, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    size = json.loads((a.package / "manifest.json").read_text())["sector_size"]
    t0 = time.time()
    total = Counter()
    for sj in range(a.sj[0], a.sj[1] + 1):
        for si in range(a.si[0], a.si[1] + 1):
            dst = a.out / "sectors" / f"{si}_{sj}"
            dst.mkdir(parents=True, exist_ok=True)
            total += style_sector(a.package / "sectors" / f"{si}_{sj}", dst, (size * si - 16000, size * sj - 16000))
    print(f"{time.time() - t0:.1f} s; triangles by finish:", dict(total.most_common()))


if __name__ == "__main__":
    main()
