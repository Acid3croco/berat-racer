"""See-through gaps in the ground of a built world: places where the player could see the sky through the near ground.

The near ground is rebuilt as the game draws it (ChunkMeshes): the kept 4 m terrain cells (two triangles each, the diagonal
alternating), the ground around the paved surfaces (BM07 seam), the roads and junctions (lifted by RoadLift) and the car parks. Every
open edge of the ground (an edge of the terrain or the seam used by one triangle only) is looked across, 2 cm outward, at three
points along it:

  hole     nothing is drawn there
  crack    the ground there, extrapolated back to the edge, is at another height (more than TOLERANCE)
  step     a paved surface is there, but below the ground's edge (more than TOLERANCE), or above it by more than the 45-degree
           skirt hanging from paved edges reaches (SKIRT): either way a slot opens between them

Chunk borders are looked across into the neighbour; a kept terrain cell's border edge hangs a TERRAIN_SKIRT, which covers a
difference smaller than that (an old mismatch between sectors' grids: up to 0.42 m, as on master). The world's own border (WORLD_EDGE) is left out: nothing is drawn past it.

Usage: uv run python check_gaps.py ../world_small [every]       exit status 1 when any gap is found
"""
import gzip
import json
import struct
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from check_roads import parse_mid

CELL, CV, CHUNK = 4.0, 101, 400.0
ROAD_LIFT = 0.012            # ChunkMeshes.RoadLift
SKIRT = 0.34                 # ChunkMeshes: SkirtLength x sin(SkirtAngle), how far the skirt reaches down
TOLERANCE = 0.01             # m
OFFSET = 0.02                # m across an open edge
WORLD_EDGE = 8.0             # m: the band along the world's border that is not checked
TERRAIN_SKIRT = 2.0          # m: the skirt a terrain cell hangs along a chunk border (ChunkMeshes, BuildTerrainFull)


def terrain_triangles(d, x0, z0):
    """(t, 3, 3) x, y, z of the kept 4 m cells, split as ChunkMeshes splits them."""
    H = d["H"]
    holes = np.zeros((CV - 1) * (CV - 1), bool)
    holes[d["holes"]] = True
    zz, xx = np.mgrid[0:CV - 1, 0:CV - 1]
    keep = ~holes.reshape(CV - 1, CV - 1)
    xx, zz = xx[keep], zz[keep]
    def v(cx, cz):
        return np.stack([x0 + cx * CELL, H[cz, cx], z0 + cz * CELL], axis=-1)
    a, b, c, e = v(xx, zz), v(xx, zz + 1), v(xx + 1, zz), v(xx + 1, zz + 1)
    even = ((xx + zz) & 1) == 0
    t1 = np.where(even[:, None, None], np.stack([a, b, e], 1), np.stack([a, b, c], 1))
    t2 = np.where(even[:, None, None], np.stack([a, e, c], 1), np.stack([c, b, e], 1))
    return np.concatenate([t1, t2])


def paved_triangles(d, parks):
    tris = []
    for r in d["road_list"]:
        if r["bridge"] or r["tunnel"]:
            continue
        L, R = r["left"] + [0, ROAD_LIFT, 0], r["right"] + [0, ROAD_LIFT, 0]
        for k in np.flatnonzero(r["drawn"]):
            tris += [(L[k], R[k], R[k + 1]), (L[k], R[k + 1], L[k + 1])]
    for j in d["junction_list"]:
        v = j["v"] + [0, ROAD_LIFT, 0]
        tris += list(v[j["tri"]])
    tris += parks
    return np.asarray(tris, float).reshape(-1, 3, 3)


def park_triangles(path):
    """Car-park surfaces (x, y, z) of a BN02 near file."""
    if not path.exists():
        return []
    raw = gzip.open(path).read()
    if raw[:4] != b"BN02":
        return []
    o = 4
    nt = struct.unpack_from("<i", raw, o)[0]; o += 4 + 16 * nt
    ns = struct.unpack_from("<i", raw, o)[0]; o += 4 + 16 * ns
    o += nt + 2 * CV * CV
    for _ in range(2):
        k = struct.unpack_from("<i", raw, o)[0]; o += 4 + 16 * k
    nh = struct.unpack_from("<i", raw, o)[0]; o += 4
    for _ in range(nh):
        k = struct.unpack_from("<i", raw, o + 4)[0]; o += 8 + 8 * k
    out = []
    np_ = struct.unpack_from("<i", raw, o)[0]; o += 4
    for _ in range(np_):
        k = struct.unpack_from("<i", raw, o)[0]; v = np.frombuffer(raw, "<f4", 3 * k, o + 4).reshape(-1, 3); o += 4 + 12 * k
        k = struct.unpack_from("<i", raw, o)[0]; t = np.frombuffer(raw, "<i4", 3 * k, o + 4).reshape(-1, 3); o += 4 + 12 * k
        out += list(v[t])
    return out


class Surface:
    """Height of a set of triangles at plan points (NaN where none holds the point; the highest where several do)."""

    def __init__(self, tris):
        self.tris = tris
        self.tree = cKDTree(tris[:, :, [0, 2]].mean(axis=1)) if len(tris) else None
        self.reach = float(np.hypot(*(tris[:, :, [0, 2]] - tris[:, :, [0, 2]].mean(axis=1)[:, None, :]).transpose(2, 0, 1)).max()) if len(tris) else 0.0

    def __call__(self, p):
        out = np.full(len(p), np.nan)
        if self.tree is None or not len(p):
            return out
        for i, cand in enumerate(self.tree.query_ball_point(p, self.reach + 1e-3)):
            if not cand:
                continue
            t = self.tris[cand]
            a, b, c = t[:, 0], t[:, 1], t[:, 2]
            det = (b[:, 0] - a[:, 0]) * (c[:, 2] - a[:, 2]) - (b[:, 2] - a[:, 2]) * (c[:, 0] - a[:, 0])
            ok = np.abs(det) > 1e-10
            det = np.where(ok, det, 1.0)
            wb = ((p[i, 0] - a[:, 0]) * (c[:, 2] - a[:, 2]) - (p[i, 1] - a[:, 2]) * (c[:, 0] - a[:, 0])) / det
            wc = ((b[:, 0] - a[:, 0]) * (p[i, 1] - a[:, 2]) - (b[:, 2] - a[:, 2]) * (p[i, 0] - a[:, 0])) / det
            inside = ok & (wb >= -1e-6) & (wc >= -1e-6) & (wb + wc <= 1 + 1e-6)
            if inside.any():
                y = a[:, 1] + wb * (b[:, 1] - a[:, 1]) + wc * (c[:, 1] - a[:, 1])
                out[i] = y[inside].max()
        return out


def open_edges(tris):
    """(edges (m, 2, 3), third vertex of their triangle (m, 3), that triangle's index (m,)) used by one triangle only (vertices
    matched to the millimetre)."""
    v = tris.reshape(-1, 3)
    key = np.round(v * 1000).astype(np.int64)
    _, vid = np.unique(key, axis=0, return_inverse=True)
    vid = vid.reshape(-1, 3)
    e = np.concatenate([vid[:, [0, 1]], vid[:, [1, 2]], vid[:, [2, 0]]])
    third = np.concatenate([tris[:, 2], tris[:, 0], tris[:, 1]])
    ends = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    s = np.sort(e, axis=1)
    _, inv, count = np.unique(s, axis=0, return_inverse=True, return_counts=True)
    single = count[inv] == 1
    owner = np.concatenate([np.arange(len(tris))] * 3)
    return ends[single], third[single], owner[single]


def check(world, every=1):
    world = Path(world)
    info = json.loads((world / "world.json").read_text())
    files = sorted((world / "chunks").glob("m_*"), key=lambda f: tuple(map(int, f.name[2:-7].split("_"))))[::every]     # column by column
    from collections import OrderedDict
    cache = OrderedDict()                                               # the chunks used last (a chunk is ~15 MB parsed): bounded on any map

    def load(ci, cj):
        if (ci, cj) in cache:
            cache.move_to_end((ci, cj))
        else:
            while len(cache) >= 64:
                cache.popitem(last=False)
            p = world / "chunks" / f"m_{ci}_{cj}.bin.gz"
            if not p.exists():
                cache[(ci, cj)] = None
            else:
                d = parse_mid(gzip.open(p).read())
                x0, z0 = info["x0"] + ci * CHUNK, info["z0"] + cj * CHUNK
                terrain = terrain_triangles(d, x0, z0)
                ground = np.concatenate([terrain, d["seam"]]) if len(d["seam"]) else terrain
                cache[(ci, cj)] = (ground, paved_triangles(d, park_triangles(world / "chunks" / f"n_{ci}_{cj}.bin.gz")), len(terrain))
        return cache[(ci, cj)]

    totals = dict(chunks=0, open_edges=0, hole=0, crack=0, step=0)
    spots = []
    for path in files:
        ci, cj = map(int, path.name[2:-7].split("_"))
        here = load(ci, cj)
        if here is None:
            continue
        totals["chunks"] += 1
        ground, paved, n_terrain = here
        near = [load(ci + di, cj + dj) for di in (-1, 0, 1) for dj in (-1, 0, 1)]
        g_all = np.concatenate([n[0] for n in near if n is not None])
        p_all = np.concatenate([n[1] for n in near if n is not None and len(n[1])]) if any(n is not None and len(n[1]) for n in near) else np.zeros((0, 3, 3))
        ends, third, owner = open_edges(ground)
        x0, z0 = info["x0"] + ci * CHUNK, info["z0"] + cj * CHUNK
        wx1, wz1 = info["x0"] + info["ncx"] * CHUNK, info["z0"] + info["ncz"] * CHUNK
        totals["open_edges"] += len(ends)
        if not len(ends):
            continue
        t = np.array([0.25, 0.5, 0.75])
        pts = ends[:, :1] + (ends[:, 1:2] - ends[:, :1]) * t[None, :, None]                     # (m, 3, 3) points along each edge
        d = ends[:, 1, [0, 2]] - ends[:, 0, [0, 2]]
        n = np.stack([d[:, 1], -d[:, 0]], 1) / np.maximum(np.hypot(d[:, 0], d[:, 1]), 1e-9)[:, None]
        away = np.sign(((pts[:, 1, [0, 2]] - third[:, [0, 2]]) * n).sum(1))[:, None]
        n = n * np.where(away == 0, 1, away)
        q = pts[:, :, [0, 2]] + n[:, None, :] * OFFSET
        flat = q.reshape(-1, 2)
        far = (pts[:, :, [0, 2]] + n[:, None, :] * 2 * OFFSET).reshape(-1, 2)
        edge_y = pts[:, :, 1].reshape(-1)
        on_world_edge = (flat[:, 0] <= info["x0"] + WORLD_EDGE) | (flat[:, 0] >= wx1 - WORLD_EDGE) | (flat[:, 1] <= info["z0"] + WORLD_EDGE) | (flat[:, 1] >= wz1 - WORLD_EDGE)
        G, P = Surface(g_all), Surface(p_all)
        gy, py = G(flat), P(flat)
        gy2, py2 = G(far), P(far)                                                                  # the surfaces across, extrapolated back to the edge:
        gy = np.where(np.isnan(gy2), gy, 2 * gy - gy2)                                             # a steep slope must not read as a crack
        py = np.where(np.isnan(py2), py, 2 * py - py2)
        hole = np.isnan(gy) & np.isnan(py) & ~on_world_edge
        mx, mz = (ends[:, 0, 0] + ends[:, 1, 0]) / 2, (ends[:, 0, 2] + ends[:, 1, 2]) / 2
        on_chunk_border = (np.abs(ends[:, 0, 0] - ends[:, 1, 0]) < 1e-6) & (np.minimum(np.abs(mx - x0), np.abs(mx - x0 - CHUNK)) < 1e-3) | \
                          (np.abs(ends[:, 0, 2] - ends[:, 1, 2]) < 1e-6) & (np.minimum(np.abs(mz - z0), np.abs(mz - z0 - CHUNK)) < 1e-3)
        skirted = np.repeat(on_chunk_border & (owner < n_terrain), 3)                             # a terrain cell's chunk-border edge hangs a skirt (TERRAIN_SKIRT)
        crack = ~np.isnan(gy) & np.isnan(py) & (np.abs(gy - edge_y) > TOLERANCE) & ~on_world_edge & ~(skirted & (np.abs(gy - edge_y) < TERRAIN_SKIRT))
        rise = py - edge_y
        step = ~np.isnan(py) & ((rise < -TOLERANCE) | (rise > SKIRT)) & ~on_world_edge
        for kind, mask in (("hole", hole), ("crack", crack), ("step", step)):
            per_edge = mask.reshape(-1, 3).any(axis=1)
            totals[kind] += int(per_edge.sum())
            for k in np.flatnonzero(per_edge)[:20]:
                at = pts[k, 1]
                spots.append((kind, round(float(at[0]), 1), round(float(at[2]), 1), round(float(np.nanmax(np.abs(np.r_[gy.reshape(-1, 3)[k] - edge_y.reshape(-1, 3)[k], rise.reshape(-1, 3)[k]]))), 3) if kind != "hole" else None))
    return totals, spots


if __name__ == "__main__":
    totals, spots = check(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 1)
    print(json.dumps(totals))
    for s in spots[:40]:
        print(s)
    bad = totals["hole"] + totals["crack"] + totals["step"]
    print("OK: no see-through gap" if not bad else f"FAIL: {bad} see-through gaps")
    sys.exit(1 if bad else 0)
