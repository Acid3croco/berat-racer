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


OVERHANG = 0.35    # m: pitched roofs reach past the walls (the package's roofs stop at the wall line)


GUTTERED = ("house", "shop", "restaurant", "library", "church", "barn")
GUTTER_COLOURS = [(0.55, 0.57, 0.58), (0.62, 0.6, 0.55), (0.85, 0.85, 0.82)]     # zinc, weathered zinc, white PVC


def overhang(v: np.ndarray, poly, p: dict) -> np.ndarray:
    """Roof vertices on the building's outline moved OVERHANG outward (along the outline's outward normal, bisected at
    corners) and down the roof slope (pitch from the data)."""
    ring = np.asarray(poly.exterior.coords)[:-1]
    if len(ring) < 3:
        return v
    if shapely.Polygon(ring).exterior.is_ccw is False:
        ring = ring[::-1]
    a, b = ring, np.roll(ring, -1, axis=0)
    seg = b - a
    ln = np.maximum(np.linalg.norm(seg, axis=1), 1e-9)
    nrm = np.column_stack([seg[:, 1], -seg[:, 0]]) / ln[:, None]          # outward (right) for counter-clockwise
    pitch = float(p.get("pitch") or 0.4)
    slope = np.tan(pitch if pitch < 1.3 else np.radians(pitch))
    out = v.copy()
    for k, q in enumerate(v[:, :2]):
        ap = q - a
        t = np.clip((ap * seg).sum(1) / ln**2, 0, 1)
        dist = np.linalg.norm(ap - seg * t[:, None], axis=1)
        near = np.where(dist < 0.08)[0]
        if len(near) == 0:
            continue
        dirn = nrm[near].sum(0)
        nd = np.linalg.norm(dirn)
        if nd < 1e-6:
            continue
        dirn = dirn / nd
        cosang = max(float((dirn * nrm[near[0]]).sum()), 0.5)
        out[k, :2] = q + dirn * OVERHANG / cosang
        out[k, 2] = v[k, 2] - OVERHANG * slope
    return out


def box_along(a, b, out, z_top, depth, width, offset):
    """A box hanging under the segment a-b (xy), `offset` outward (unit `out`), `width` deep outward, `depth` tall below
    z_top (pair of z for a and b). Returns (positions, triangles) wound counter-clockwise from outside."""
    a0, b0 = a + out * offset, b + out * offset
    a1, b1 = a0 + out * width, b0 + out * width
    za, zb = z_top
    v = [[*a0, za - depth], [*b0, zb - depth], [*b1, zb - depth], [*a1, za - depth],
         [*a0, za], [*b0, zb], [*b1, zb], [*a1, za]]
    quads = [(0, 3, 2, 1), (3, 7, 6, 2), (1, 2, 6, 5), (0, 4, 7, 3), (4, 5, 6, 7), (0, 1, 5, 4)]
    tri = []
    for q in quads:
        tri += [[q[0], q[1], q[2]], [q[0], q[2], q[3]]]
    return np.asarray(v, float), np.asarray(tri)


def gutters(v0: np.ndarray, v1: np.ndarray, tri: np.ndarray, poly, p: dict):
    """Gutters under the eaves and downpipes at their ends, for one building's roof triangles: v0 the roof vertices before
    the overhang, v1 after. Eave edges: boundary edges of the roof whose ends both moved (on the outline) and lie level
    at the eave. Returns [(positions, triangles)]."""
    edges = {}
    for t in tri:
        for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            k = (min(a, b), max(a, b))
            edges[k] = edges.get(k, 0) + 1
    moved = np.linalg.norm(v1[:, :2] - v0[:, :2], axis=1) > 0.1
    zmin = v1[moved, 2].min() if moved.any() else 0
    eave = [(a, b) for (a, b), c in edges.items() if c == 1 and moved[a] and moved[b]
            and abs(v1[a, 2] - v1[b, 2]) < 0.05 and v1[a, 2] < zmin + 0.3
            and np.linalg.norm(v1[a, :2] - v1[b, :2]) > 0.8]
    if not eave:
        return []
    cen = np.asarray(poly.centroid.coords[0])
    ground = float(p.get("ground") or 0)
    out = []
    ends = {}
    for a, b in eave:
        A, B = v1[a, :2], v1[b, :2]
        d = B - A
        L = np.linalg.norm(d)
        nrm = np.asarray([-d[1], d[0]]) / L
        if np.dot((A + B) / 2 - cen, nrm) < 0:
            nrm = -nrm
        out.append(box_along(A, B, nrm, (v1[a, 2] - 0.02, v1[b, 2] - 0.02), 0.11, 0.12, -0.04))
        for e, other in ((a, b), (b, a)):
            ends.setdefault(e, []).append((nrm, v1[other, :2]))
    pipe_at = [(e, lst[0]) for e, lst in ends.items() if len(lst) == 1]   # the ends of open eave runs
    if not pipe_at and ends:
        # eaves all round (hipped roof): pipes at the two eave corners farthest apart
        ks = list(ends)
        P = v1[ks, :2]
        dist = np.linalg.norm(P[:, None] - P[None], axis=2)
        i, j = np.unravel_index(dist.argmax(), dist.shape)
        pipe_at = [(ks[i], ends[ks[i]][0]), (ks[j], ends[ks[j]][0])]
    for e, (nrm, other) in pipe_at:
        P = v1[e, :2]
        along = (other - P) / max(np.linalg.norm(other - P), 1e-6)
        # back to the wall face (the overhang), 25 cm in from the end, 6 cm off the wall
        base = P - nrm * (OVERHANG - 0.06) + along * 0.25
        z_top = v1[e, 2] - 0.1
        if z_top - ground < 1.5:
            continue
        w = 0.045
        cs = [base + (-along - nrm) * w, base + (along - nrm) * w, base + (along + nrm) * w, base + (-along + nrm) * w]
        pos, tr = [], []
        for r in range(4):
            c0, c1 = cs[r], cs[(r + 1) % 4]
            k = len(pos)
            pos += [[*c0, ground - 0.05], [*c1, ground - 0.05], [*c1, z_top], [*c0, z_top]]
            tr += [[k, k + 1, k + 2], [k, k + 2, k + 3]]
        pos, tr = np.asarray(pos, float), np.asarray(tr)
        e1, e2 = cs[1] - cs[0], cs[2] - cs[0]
        ccw = e1[0] * e2[1] - e1[1] * e2[0] > 0
        out.append((pos, tr if ccw else tr[:, ::-1]))
        # the elbow from the gutter to the pipe: a short box from the gutter line back to the pipe
        out.append(box_along(base - along * 0.035, base + along * 0.035, nrm, (z_top + 0.03, z_top + 0.03), 0.07, OVERHANG - 0.06, 0.0))
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


def prism(cx, cy, r, z0, z1, n=8, rot=np.pi / 8):
    """Side faces of a regular n-gon prism (counter-clockwise from outside), UV0 = (metres around, metres up)."""
    ang = rot + np.arange(n) * 2 * np.pi / n
    xs, ys = cx + r * np.cos(ang), cy + r * np.sin(ang)
    side = 2 * r * np.sin(np.pi / n)
    pos, tri, uv = [], [], []
    for k in range(n):
        a, b = k, (k + 1) % n
        base = len(pos)
        pos += [[xs[a], ys[a], z0], [xs[b], ys[b], z0], [xs[b], ys[b], z1], [xs[a], ys[a], z1]]
        uv += [[k * side, 0], [(k + 1) * side, 0], [(k + 1) * side, z1 - z0], [k * side, z1 - z0]]
        tri += [[base, base + 1, base + 2], [base, base + 2, base + 3]]
    return np.asarray(pos, float), np.asarray(tri), np.asarray(uv, float)


def cap(cx, cy, r, z, n=8, rot=np.pi / 8, apex=None):
    """A flat octagon at z (apex None) or a pyramid to apex height; faces up / out."""
    ang = rot + np.arange(n) * 2 * np.pi / n
    ring = [[cx + r * np.cos(t), cy + r * np.sin(t), z] for t in ang]
    top = [cx, cy, z if apex is None else apex]
    pos = np.asarray(ring + [top], float)
    tri = np.asarray([[k, (k + 1) % n, n] for k in range(n)])
    return pos, tri, pos[:, :2].copy()


def bell_tower(mesh, t, ground, rnd, white):
    """A Toulousain octagonal bell tower at the data's tower point and height: three tiers stepping in, brick bands
    between them, belfry openings on the top tier; white render with a flat balustraded top (Bérat's Saint-Pierre) or
    brick with a slate spire."""
    cx, cy, h = float(t["x"]), float(t["y"]), float(t["height"])
    body = h * (0.78 if white else 0.68)                 # the spire takes the rest
    r0 = float(np.clip(h * 0.12, 2.4, 3.6))
    wall = "wall_render" if white else "wall_brick"
    wcol = np.asarray((0.93, 0.9, 0.84) if white else (0.75, 0.42, 0.3))
    uv1 = lambda n: np.column_stack([np.full(n, rnd), np.zeros(n)])
    z = ground - 0.5
    for tier in range(3):
        r = r0 * (1.0 - 0.07 * tier)
        z1 = ground + body * (tier + 1) / 3.0
        p, tr, uv = prism(cx, cy, r, z, z1)
        uv[:, 1] += z - ground
        mesh.add(wall, p, tr, uv0=uv, uv1=uv1(len(p)), colour=np.tile(wcol, (len(p), 1)))
        # brick band at the top of the tier, a little proud
        p, tr, uv = prism(cx, cy, r + 0.12, z1 - 0.45, z1 + 0.05)
        mesh.add("wall_brick", p, tr, uv0=uv, uv1=uv1(len(p)), colour=np.tile((0.78, 0.42, 0.28), (len(p), 1)))
        p, tr, _ = cap(cx, cy, r + 0.12, z1 + 0.05)
        mesh.add("wall_brick", p, tr, uv0=p[:, :2], uv1=uv1(len(p)), colour=np.tile((0.78, 0.42, 0.28), (len(p), 1)))
        if tier == 2:
            # belfry openings: a dark arch-tall panel on every face, set just proud
            ang = np.pi / 8 + np.arange(8) * 2 * np.pi / 8
            mid = ang + np.pi / 8
            zo0, zo1 = z + (z1 - z) * 0.35, z1 - 0.8
            for m in mid:
                nx, ny = np.cos(m), np.sin(m)
                apo = r * np.cos(np.pi / 8) + 0.02
                c0 = np.asarray([cx + nx * apo, cy + ny * apo])
                tx, ty = -ny, nx
                w = 0.55
                q = np.asarray([[c0[0] - tx * w, c0[1] - ty * w, zo0], [c0[0] + tx * w, c0[1] + ty * w, zo0],
                                [c0[0] + tx * w, c0[1] + ty * w, zo1], [c0[0] - tx * w, c0[1] - ty * w, zo1]])
                mesh.add("belfry", q, np.asarray([[0, 1, 2], [0, 2, 3]]), uv0=np.zeros((4, 2)), uv1=uv1(4),
                         colour=np.tile((0.05, 0.05, 0.06), (4, 1)))
        z = z1
    rt = r0 * (1.0 - 0.07 * 2)
    if white:
        # flat top with a balustrade (a low ring wall)
        p, tr, _ = cap(cx, cy, rt, z + 0.06)
        mesh.add("wall_concrete", p, tr, uv0=p[:, :2], uv1=uv1(len(p)), colour=np.tile((0.8, 0.78, 0.74), (len(p), 1)))
        p, tr, uv = prism(cx, cy, rt, z, z + 1.1)
        mesh.add(wall, p, tr, uv0=uv, uv1=uv1(len(p)), colour=np.tile(wcol, (len(p), 1)))
    else:
        p, tr, _ = cap(cx, cy, rt + 0.2, z, apex=ground + h)
        mesh.add("roof_slate", p, tr, uv0=p[:, :2], uv1=uv1(len(p)), colour=np.tile((0.45, 0.47, 0.5), (len(p), 1)))


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
            vp = pos[used]
            if material == "roof" and props[b].get("roof") in ("gabled", "hipped"):
                v_before = vp
                vp = overhang(vp, polys[b], props[b])
                if props[b].get("use") in GUTTERED:
                    for gp, gt in gutters(v_before, vp, inv.reshape(-1, 3), polys[b], props[b]):
                        mesh.add("gutter", gp, gt, uv0=gp[:, :2], uv1=np.zeros((len(gp), 2)),
                                 colour=np.tile(GUTTER_COLOURS[int(rnd * 3) % 3], (len(gp), 1)))
                        counts["gutter_parts"] += 1
            uv1 = np.column_stack([np.full(len(used), rnd), np.zeros(len(used))])
            mesh.add(name, vp, inv.reshape(-1, 3), uv0=uv[used], uv1=uv1, colour=col[used])
            counts[name] += len(t)
    # chimneys: on the ridge of pitched-roof houses (most) and old barns, in the building's wall finish, a concrete cap
    roof_pts = {}
    for material, pos, uv, col, tri in read_glb(src / "buildings.glb"):
        if material != "roof":
            continue
        cen = pos[tri].mean(axis=1)
        idx = tree.query_nearest(shapely.points(cen[:, 0], cen[:, 1]), return_distance=False, all_matches=False)[1]
        for b in np.unique(idx):
            roof_pts.setdefault(b, []).append(pos[tri[idx == b]].reshape(-1, 3))
    for b, chunks in roof_pts.items():
        p = props[b]
        if p.get("roof") not in ("gabled", "hipped") or p.get("use") not in ("house", "barn"):
            continue
        (wall, _), rnd = looks[b]
        if p.get("use") == "barn" and p.get("era") != "before_1950":
            continue
        if rnd > 0.85:
            continue
        v = np.concatenate(chunks)
        top = v[:, 2].max()
        ridge = v[v[:, 2] > top - 0.05]
        if len(ridge) < 2:
            continue
        dist = np.linalg.norm(ridge[:, None, :2] - ridge[None, :, :2], axis=2)
        i, j = np.unravel_index(dist.argmax(), dist.shape)
        e0, e1 = ridge[i, :2], ridge[j, :2]
        along = e1 - e0
        L = np.linalg.norm(along)
        d = along / L if L > 0.5 else np.array([1.0, 0.0])
        nrm = np.array([-d[1], d[0]])
        c = e0 + along * (0.2 + 0.6 * ((rnd * 7.13) % 1.0)) + nrm * (0.6 * ((rnd * 3.7) % 1.0) - 0.3)
        w, dep, z0, z1 = 0.62, 0.48, top - 1.4, top + 0.9
        corners = [c + d * sx * w / 2 + nrm * sy * dep / 2 for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        pos, tris, uvs = [], [], []
        for k in range(4):                                              # four sides
            a0, a1 = corners[k], corners[(k + 1) % 4]
            base = len(pos)
            pos += [[*a0, z0], [*a1, z0], [*a1, z1], [*a0, z1]]
            seg = np.linalg.norm(a1 - a0)
            uvs += [[0, z1 - z0], [seg, z1 - z0], [seg, 0], [0, 0]]
            tris += [[base, base + 1, base + 2], [base, base + 2, base + 3]]   # counter-clockwise from outside
        mesh.add(wall, np.asarray(pos), np.asarray(tris), uv0=np.asarray(uvs, float),
                 uv1=np.column_stack([np.full(len(pos), rnd), np.zeros(len(pos))]), colour=np.full((len(pos), 3), 0.8))
        cap = [[*q, z1 + 0.06] for q in [c + d * sx * (w / 2 + 0.06) + nrm * sy * (dep / 2 + 0.06)
                                         for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]]
        capl = [[*q, z1] for q in [c + d * sx * (w / 2 + 0.06) + nrm * sy * (dep / 2 + 0.06)
                                   for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]]
        cp = np.asarray(cap + capl)
        ct = [[0, 1, 2], [0, 2, 3]]
        for k in range(4):
            ct += [[k + 4, (k + 1) % 4 + 4, (k + 1) % 4], [k + 4, (k + 1) % 4, k]]
        mesh.add("wall_concrete", cp, np.asarray(ct), uv0=cp[:, :2], colour=np.full((8, 3), 0.7))
        counts["chimneys"] += 1
    # bell towers: the data gives their point and height; the package's building stops at its roof
    for p, (looks_, rnd) in zip(props, looks):
        t = p.get("tower")
        if t and t.get("height") and float(t["height"]) > 8.0:
            white = (p.get("name") or "").endswith("Saint-Pierre") or any(
                (q.get("name") or "").endswith("Saint-Pierre") and abs(float(t["x"]) - np.mean(np.asarray(f["geometry"]["coordinates"][0])[:, 0])) < 40
                for q, f in zip(props, g["features"]))
            bell_tower(mesh, t, float(p.get("ground") or 0.0), rnd, white)
            counts["bell_towers"] += 1
    mats = {m: {"colour": (0.7, 0.7, 0.7)} for m in mesh.parts}
    gltf.write_glb(dst / "buildings_styled.glb", mesh, corner, mats, f"buildings_{dst.name}")
    return counts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("package", type=Path)
    ap.add_argument("--si", type=int, nargs=2)
    ap.add_argument("--sj", type=int, nargs=2)
    ap.add_argument("--all", action="store_true", help="every sector of the package")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    size = json.loads((a.package / "manifest.json").read_text())["sector_size"]
    t0 = time.time()
    total = Counter()
    man = json.loads((a.package / "manifest.json").read_text())
    todo = sorted(tuple(s) for s in man["sectors"]) if a.all else         [(si, sj) for sj in range(a.sj[0], a.sj[1] + 1) for si in range(a.si[0], a.si[1] + 1)]
    for si, sj in todo:
        dst = a.out / "sectors" / f"{si}_{sj}"
        dst.mkdir(parents=True, exist_ok=True)
        total += style_sector(a.package / "sectors" / f"{si}_{sj}", dst, (size * si - 16000, size * sj - 16000))
    print(f"{time.time() - t0:.1f} s; triangles by finish:", dict(total.most_common()))


if __name__ == "__main__":
    main()
