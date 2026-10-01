"""Verify the roads of a built world (BM05 / BM06 chunks) against its terrain, the way the game sees them:

  - the 4 m terrain mesh and the 16 m one never stand above a road surface (sampled on every road quad and junction triangle)
  - the 4 m terrain mesh never stands above a bridge deck either (the ground falls away under it)
  - how far below the roads they lie (a road must not float either)
  - every road end at a junction meets a junction vertex exactly

Usage: uv run python check_roads.py DIR
"""
import gzip
import json
import struct
import sys
from pathlib import Path

import numpy as np

from check_hg import R, CV

LV, CELL, LOD_CELL, CHUNK = 26, 4.0, 16.0, 400.0
ROAD_LIFT = 0.012                     # ChunkMeshes.RoadLift: the drawn road sits this far above its data height


def read_roads(r):
    roads = []
    for _ in range(r.i()):
        flags = r.take(8)
        r.i(); r.fl(3); r.st()
        n = r.i()
        centre, left, right = (r.fl(3 * n).reshape(n, 3) for _ in range(3))
        drawn = np.frombuffer(r.take(n - 1), np.uint8).astype(bool)
        give_way = [struct.unpack("<HB", r.take(3)) for _ in range(r.u8())]
        roads.append(dict(bridge=bool(flags[0] & 2), dirt=bool(flags[0] & 1), centre=centre, left=left, right=right, drawn=drawn, give_way=give_way))
    return roads


def read_junctions(r):
    out = []
    for _ in range(r.i()):
        r.u8()
        v = r.fl(3 * r.i()).reshape(-1, 3)
        tri = np.frombuffer(r.take(6 * r.i()), "<u2").reshape(-1, 3)
        ne = r.i()
        edge = np.frombuffer(r.take(4 * ne), "<u2").reshape(-1, 2)
        out.append(dict(v=v, tri=tri, edge=edge, mouth=np.frombuffer(r.take(ne), np.uint8).astype(bool)))
    return out


def parse_mid(raw):
    """One m_ chunk: terrain grids, roads, junctions, and the counts of everything else (parsed to the last byte)."""
    r = R(raw)
    magic = r.take(4)
    assert magic in (b"BM05", b"BM06"), "not a BM05 / BM06 chunk: rebuild the world with tools/build_world.py"
    d = dict(ci=r.i(), cj=r.i())
    assert r.i() == CV
    base, step = r.f(), r.f()
    d["H"] = base + np.frombuffer(r.take(CV * CV * 2), "<u2").reshape(CV, CV).astype(np.float64) * np.float32(step)
    d["step"] = step
    r.take(CV * CV * 3); r.take(LV * LV * 3)
    d["LOW"] = r.fl(LV * LV).reshape(LV, LV).astype(np.float64)
    d["road_list"], d["ctx_list"] = read_roads(r), read_roads(r)
    d["junction_list"], d["ctx_junction_list"] = read_junctions(r), read_junctions(r)
    d["roads"], d["ctx"], d["junctions"] = len(d["road_list"]), len(d["ctx_list"]), len(d["junction_list"])
    d["areas"] = r.i()
    for _ in range(d["areas"]): r.fl(3 * r.i())
    d["lines"] = r.i()
    for _ in range(d["lines"]): r.f(); r.u8(); r.u8(); r.fl(3 * r.i())
    d["trough_list"] = [r.fl(3 * r.i()).reshape(-1, 3) for _ in range(r.i())] if magic == b"BM06" else []     # water carried by a structure
    d["bld"] = r.i()
    for _ in range(d["bld"]):
        r.fl(2 * r.i()); r.fl(3); r.fl(r.i()); r.take(6); r.st(); r.st(); r.i(); r.fl(r.i()); r.fl(2 * r.i()); n = r.i(); r.take(4 * n)
    assert r.o == len(raw), (r.o, len(raw))
    return d


def mesh_height(grid, cell, x, z):
    """Height of the game's terrain mesh (two triangles per cell, the diagonal alternating like a checkerboard) at local chunk coordinates."""
    n = grid.shape[0]
    fx, fz = np.clip(x / cell, 0, n - 1 - 1e-9), np.clip(z / cell, 0, n - 1 - 1e-9)
    ix, iz = fx.astype(int), fz.astype(int)
    tx, tz = fx - ix, fz - iz
    a, b, c, e = grid[iz, ix], grid[iz + 1, ix], grid[iz, ix + 1], grid[iz + 1, ix + 1]           # a (x, z), b (x, z + 1), c (x + 1, z), e (x + 1, z + 1)
    even = a + np.where(tz >= tx, (b - a) * tz + (e - b) * tx, (c - a) * tx + (e - c) * tz)       # diagonal a - e
    odd = np.where(tx + tz <= 1, a + (c - a) * tx + (b - a) * tz, e + (b - e) * (1 - tx) + (c - e) * (1 - tz))      # diagonal b - c
    return np.where((ix + iz) % 2 == 0, even, odd)


def surface_points(d, bridges=False):
    """Points spread over every ground-level road triangle of the chunk (`bridges`: over every bridge deck instead): (n, 3) x, height, z."""
    weights = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1], [.5, .5, 0], [0, .5, .5], [.5, 0, .5], [1 / 3, 1 / 3, 1 / 3]])
    tris = []
    for road in d["road_list"]:
        if road["bridge"] != bridges:
            continue
        for k in np.flatnonzero(road["drawn"]):
            l0, r0, l1, r1 = road["left"][k], road["right"][k], road["left"][k + 1], road["right"][k + 1]
            tris += [(l0, r0, r1), (l0, r1, l1)]
    for j in d["junction_list"] if not bridges else []:
        tris += [tuple(j["v"][t]) for t in j["tri"]]
    if not tris:
        return np.zeros((0, 3))
    return np.einsum("wk,tkc->twc", weights, np.array(tris, float)).reshape(-1, 3)


def loose_ends(d):
    """Road ends at a junction (first / last drawn segment not at the end of the road) that do not coincide with a junction vertex."""
    vertices = {tuple(v) for j in d["junction_list"] + d["ctx_junction_list"] for v in j["v"]}
    loose = total = 0
    for road in d["road_list"]:
        drawn = np.flatnonzero(road["drawn"])
        if not len(drawn):
            continue
        n = len(road["drawn"])
        for k, inside in ((drawn[0], drawn[0] > 0), (drawn[-1] + 1, drawn[-1] < n - 1)):
            if inside:                                    # the neighbouring segment is inside a junction
                total += 2
                loose += (tuple(road["left"][k]) not in vertices) + (tuple(road["right"][k]) not in vertices)
    return loose, total


def check(world_dir):
    world_dir = Path(world_dir)
    w = json.load(open(world_dir / "world.json"))
    files = sorted((world_dir / "chunks").glob("m_*"))
    totals = dict(chunks=len(files), roads=0, junctions=0, points=0, above4=0, above16=0, worst4=0.0, worst16=0.0, loose=0, ends=0,
                  deck_points=0, above_deck=0, worst_deck=0.0)
    gaps, where = [], []
    for path in files:
        d = parse_mid(gzip.open(path).read())
        x0, z0 = w["x0"] + d["ci"] * CHUNK, w["z0"] + d["cj"] * CHUNK
        loose, ends = loose_ends(d)
        totals["roads"] += d["roads"]; totals["junctions"] += d["junctions"]; totals["loose"] += loose; totals["ends"] += ends
        deck = surface_points(d, bridges=True)
        if len(deck):
            lx, lz = deck[:, 0] - x0, deck[:, 2] - z0
            inside = (lx >= 0) & (lx <= CHUNK) & (lz >= 0) & (lz <= CHUNK)
            over = mesh_height(d["H"], CELL, lx[inside], lz[inside]) - (deck[inside, 1] + ROAD_LIFT)
            totals["deck_points"] += len(over); totals["above_deck"] += int((over > 0).sum())
            if len(over) and over.max() > totals["worst_deck"]:
                totals["worst_deck"] = float(over.max()); where.append((round(float(over.max()), 3), "4 m / deck", round(float(x0 + lx[inside][over.argmax()]), 1), round(float(z0 + lz[inside][over.argmax()]), 1)))
        p = surface_points(d)
        if not len(p):
            continue
        lx, lz = p[:, 0] - x0, p[:, 2] - z0
        inside = (lx >= 0) & (lx <= CHUNK) & (lz >= 0) & (lz <= CHUNK)            # a road may run a segment past its chunk: the neighbour's terrain is checked with the neighbour
        lx, lz, road = lx[inside], lz[inside], p[inside, 1] + ROAD_LIFT
        over4, over16 = mesh_height(d["H"], CELL, lx, lz) - road, mesh_height(d["LOW"], LOD_CELL, lx, lz) - road
        totals["points"] += len(road)
        totals["above4"] += int((over4 > 0).sum()); totals["above16"] += int((over16 > 0).sum())
        if over4.max() > totals["worst4"]:
            totals["worst4"] = float(over4.max()); where.append((round(float(over4.max()), 3), "4 m", round(float(x0 + lx[over4.argmax()]), 1), round(float(z0 + lz[over4.argmax()]), 1)))
        if over16.max() > totals["worst16"]:
            totals["worst16"] = float(over16.max()); where.append((round(float(over16.max()), 3), "16 m", round(float(x0 + lx[over16.argmax()]), 1), round(float(z0 + lz[over16.argmax()]), 1)))
        gaps.append(-over4)
    gaps = np.concatenate(gaps) if gaps else np.zeros(1)
    totals["gap_below_road_m"] = dict(median=round(float(np.median(gaps)), 3), p99=round(float(np.percentile(gaps, 99)), 3), max=round(float(gaps.max()), 3))
    totals["worst_spots"] = sorted(where, reverse=True)[:6]
    return totals


if __name__ == "__main__":
    report = check(sys.argv[1])
    for key, value in report.items():
        print(f"{key:<18} {value}")
    ok = report["above4"] == 0 and report["above16"] == 0 and report["above_deck"] == 0 and report["loose"] == 0
    print("OK: no terrain above any road or bridge deck, every road end meets its junction" if ok else "PROBLEMS: see above4 / above16 / above_deck / loose")
    sys.exit(0 if ok else 1)
