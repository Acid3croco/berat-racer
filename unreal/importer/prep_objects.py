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
FRAME_DEPTH = 0.08    # m: window and door frames stand this far out of the wall (the wall is not cut)


def openings(sector: Path) -> gltf.Mesh:
    """Windows, doors, garages, shopfronts and shutters from the facade layout of buildings.geojson, with depth (the wall is
    not cut): every opening gets a frame proud of the wall (FRAME_DEPTH) with the glass or door leaf set back inside it, a
    stone sill under windows, and on shuttered buildings a pair of open shutters flat on the wall. Materials: glass, frame,
    sill, door, garage, shopfront, shutter. Vertex colour: glass R = random per opening (which windows light up at night),
    G = 1 on blocks of flats; frames and shutters carry their paint colour."""
    g = json.loads((sector / "buildings.geojson").read_text(encoding="utf-8"))
    mesh = gltf.Mesh()
    parts = {}

    def box(material, a, d, n, width, z0, z1, o0, o1, colour):
        """A box on the wall: from a along d for width, z0..z1, from o0 to o1 out of the wall (n outward); 5 faces."""
        a = np.asarray(a, np.float64)
        b = a + d * width
        p0, p1 = a + n * o0, b + n * o0
        q0, q1 = a + n * o1, b + n * o1
        v = lambda xy, z: [xy[0], xy[1], z]
        faces = [
            (v(q0, z0), v(q1, z0), v(q1, z1), v(q0, z1)),          # front
            (v(p0, z1), v(q0, z1), v(q1, z1), v(p1, z1)),          # top
            (v(p1, z0), v(q1, z0), v(q0, z0), v(p0, z0)),          # bottom
            (v(p0, z0), v(q0, z0), v(q0, z1), v(p0, z1)),          # side at a
            (v(q1, z0), v(p1, z0), v(p1, z1), v(q1, z1)),          # side at b
        ]
        q = parts.setdefault(material, dict(p=[], c=[], uv=[]))
        for f in faces:
            q["p"].append(np.asarray(f, np.float64))
            q["c"].append(colour)
            q["uv"].append(np.array([[0, 1], [1, 1], [1, 0], [0, 0]], np.float64))

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
        frame = (0.92, 0.91, 0.87) if rng.random() < 0.6 else ((0.45, 0.32, 0.22) if old else (0.35, 0.35, 0.36))
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
                a = c - d * width / 2
                kind = o["type"]
                fr = 0.07                                             # frame width (m)
                if kind in ("window", "balcony"):
                    leaf, leaf_mat = (float(rng.random()), flats, 0.0), "glass"
                elif kind == "shopfront":
                    leaf, leaf_mat = (float(rng.random()), 1.0, 0.0), "shopfront"
                else:
                    leaf, leaf_mat = frame, ("garage" if kind == "garage" else "door")
                # frame: four bars proud of the wall; the leaf set back inside (looks recessed)
                box("frame", a, d, nrm, fr, z0, z1, 0.0, FRAME_DEPTH, frame)
                box("frame", a + d * (width - fr), d, nrm, fr, z0, z1, 0.0, FRAME_DEPTH, frame)
                box("frame", a + d * fr, d, nrm, width - 2 * fr, z1 - fr, z1, 0.0, FRAME_DEPTH, frame)
                if kind in ("window", "balcony", "shopfront"):
                    box("frame", a + d * fr, d, nrm, width - 2 * fr, z0, z0 + fr, 0.0, FRAME_DEPTH, frame)
                box(leaf_mat, a + d * fr, d, nrm, width - 2 * fr, z0 + (fr if kind != "door" else 0.0), z1 - fr, 0.0,
                    FRAME_DEPTH * 0.35, leaf)
                if kind == "window":
                    # stone sill: sticks out under the window, a little wider
                    box("sill", a - d * 0.05, d, nrm, width + 0.1, z0 - 0.06, z0, 0.0, 0.14, (0.75, 0.72, 0.66))
                    if has_shutters and height < 2.4:
                        sw = width / 2
                        box("shutter", a - d * sw, d, nrm, sw, z0, z1, 0.0, 0.035, shutter)
                        box("shutter", a + d * width, d, nrm, sw, z0, z1, 0.0, 0.035, shutter)

    for material, q in parts.items():
        pos = np.concatenate(q["p"])
        k = len(q["p"])
        tris = (np.array([[0, 1, 2], [0, 2, 3]])[None, :, :] + 4 * np.arange(k)[:, None, None]).reshape(-1, 3)
        mesh.add(material, pos, tris, uv0=np.concatenate(q["uv"]),
                 colour=np.repeat(np.asarray(q["c"], np.float64), 4, axis=0))
    return mesh


CONTROLS = ["priority", "give_way", "stop", "signals", "right"]


def write_lanes_bin(path: Path, lg: dict) -> int:
    """The lane graph of a sector, binary (little-endian), for the C++ importer (JSON does not scale to 921k lanes):
    int32 count; per lane: int64 id; uint8 kind, control, importance, dirt; int32 junction; float32 turn, limit;
    uint16 n; n x float32 (x, y, z, speed) in Unreal cm and m/s; uint8 k; k x int64 next; int64 left, right;
    uint8 m; m x int64 yields."""
    controls = lg["controls"]
    out = [struct.pack("<i", len(lg["elements"]))]
    for e in lg["elements"]:
        ctl = controls[e["control"]] if 0 <= e.get("control", -1) < len(controls) else "priority"
        road = e.get("road") or {}
        out.append(struct.pack("<qBBBBiff", e["id"], e["kind"], CONTROLS.index(ctl) if ctl in CONTROLS else 0,
                               int(road.get("importance", 5) or 5), 1 if road.get("dirt") else 0,
                               int(e.get("junction", -1)), float(e.get("turn", 0.0)), float(e.get("limit", 50) or 50)))
        pts = np.asarray(e["points"], np.float64).reshape(-1, 3)
        spd = np.asarray(e.get("speed", []), np.float64)
        if len(spd) != len(pts):
            spd = np.full(len(pts), float(e.get("limit", 50) or 50) / 3.6)
        rec = np.column_stack([pts[:, 0] * 100, -pts[:, 1] * 100, pts[:, 2] * 100, spd]).astype("<f4")
        out.append(struct.pack("<H", len(pts)) + rec.tobytes())
        nxt = e.get("next", [])
        out.append(struct.pack("<B", len(nxt)) + struct.pack(f"<{len(nxt)}q", *nxt))
        out.append(struct.pack("<qq", e.get("left", -1), e.get("right", -1)))
        y = e.get("yields", [])
        out.append(struct.pack("<B", len(y)) + struct.pack(f"<{len(y)}q", *y))
    path.write_bytes(b"".join(out))
    return len(lg["elements"])


def read_glb_mesh(path: Path):
    """[(material, positions in package axes (absolute), triangles)] of a package glb."""
    d = path.read_bytes()
    n = struct.unpack("<I", d[12:16])[0]
    doc = json.loads(d[20:20 + n])
    blob = d[20 + n + 8:]
    tx, ty, tz = doc["nodes"][0].get("translation", [0.0, 0.0, 0.0])
    out = []
    for p in doc["meshes"][0]["primitives"]:
        a = doc["accessors"][p["attributes"]["POSITION"]]
        v = doc["bufferViews"][a["bufferView"]]
        g = np.frombuffer(blob, "<f4", a["count"] * 3, v["byteOffset"] + a.get("byteOffset", 0)).reshape(-1, 3).astype(np.float64)
        pos = np.column_stack([g[:, 0] + tx, -(g[:, 2] + tz), g[:, 1] + ty])
        ia = doc["accessors"][p["indices"]]
        iv = doc["bufferViews"][ia["bufferView"]]
        tri = np.frombuffer(blob, "<u4", ia["count"], iv["byteOffset"] + ia.get("byteOffset", 0)).reshape(-1, 3)
        out.append((doc["materials"][p["material"]]["name"], pos, tri))
    return out


def road_collision(sector: Path) -> gltf.Mesh:
    """The drivable surfaces of roads.glb: carriageways, junctions, car parks and bridge decks, without the skirts and deck
    sides (vertical) nor the paint (2 cm over the surface). The skirts hide gaps where the terrain dips under a road edge; as
    collision they made a 5-10 cm wall at every road edge that stopped a wheel dead. Normal z > 0.5: up to 60 degrees.
    One part per road material (asphalt, dirt, deck): the importer gives each its physical material (grip per surface)."""
    mesh = gltf.Mesh()
    for material, pos, tri in read_glb_mesh(sector / "roads.glb"):
        if material == "paint":
            continue
        a, b, c = pos[tri[:, 0]], pos[tri[:, 1]], pos[tri[:, 2]]
        nrm = np.cross(b - a, c - a)
        nz = nrm[:, 2] / np.maximum(np.linalg.norm(nrm, axis=1), 1e-12)
        keep = tri[nz > 0.5]
        if len(keep):
            used, inv = np.unique(keep, return_inverse=True)
            mesh.add(material, pos[used], inv.reshape(-1, 3))
    return mesh


# Garden walls: the package has no property lines, so plots meet the street where the land beside a road is garden or yard
# with a house near. Low rendered walls with a coping (the nearest house's finish and colour), some with a railing on
# top, some as hedges (plants). Gaps for gates and drives; none where a building or a hedge already lines the street.
WALL_SET = 1.0          # m from the drawn road edge (verge)
WALL_STEP = 1.0         # m between samples along the edge
GARDEN = (8, 9)         # classes: garden, yard
RESIDENTIAL = ("house", "shop", "restaurant", "library", "church")


def _wall_finish(p) -> str:
    """The wall finish a house's garden wall takes: render mostly; brick and stone on old ones and where the data says."""
    wm = (p.get("wall_material") or "").lower()
    old = p.get("era") == "before_1950"
    rnd = ((p.get("seed") or 0) * 2654435761 % 2**32) / 2**32
    if "brique" in wm or (old and rnd < 0.35):
        return "wall_brick"
    if "pierre" in wm or (old and rnd < 0.6):
        return "wall_stone"
    return "wall_render"


def garden_walls(sector: Path, rng):
    """(wall mesh: materials wall_render / wall_brick / wall_stone, coping, rail; extra hedge plants (x, y, z, h, yaw, kind))."""
    import tifffile
    from PIL import Image
    si, sj = (int(v) for v in sector.name.split("_"))
    x0, ytop = 3200 * si - 16000, 3200 * (sj + 1) - 16000
    cls = np.asarray(Image.open(sector / "classes.png"))
    hgt = tifffile.imread(sector / "height.tif").astype(np.float64)
    n = cls.shape[0]

    def cell(a, x, y):
        c = np.clip(np.rint(np.asarray(x) - x0).astype(int), 0, n - 1)
        r = np.clip(np.rint(ytop - np.asarray(y)).astype(int), 0, n - 1)
        return a[r, c]

    b = json.loads((sector / "buildings.geojson").read_text(encoding="utf-8"))
    polys, props = [], []
    for f in b["features"]:
        polys.append(shapely.Polygon(np.asarray(f["geometry"]["coordinates"][0], np.float64)[:, :2]))
        props.append(f["properties"])
    mesh = gltf.Mesh()
    if not polys:
        return mesh, np.zeros((0, 6))
    btree = shapely.STRtree(polys)
    homes = [i for i, p in enumerate(props) if p.get("use") in RESIDENTIAL]
    htree = shapely.STRtree([polys[i] for i in homes]) if homes else None
    v = json.load(gzip.open(sector / "vegetation.json.gz"))
    hedge_lines = [shapely.LineString(np.asarray(h["points"], np.float64)[:, :2]) for h in v["hedges"] if len(h["points"]) > 1]
    hedges = shapely.STRtree(hedge_lines) if hedge_lines else None

    g = json.loads((sector / "roads.geojson").read_text(encoding="utf-8"))
    runs = []                       # (points (k, 2), outward normals (k, 2), house index)
    for feat in g["features"]:
        p = feat["properties"]
        if p["tunnel"] or p["bridge"] or p["class"] in ("track", "motorway", "ramp", "path", "footway", "cycleway"):
            continue
        a, bb = p["drawn_from"], p["drawn_to"]
        if a is None or bb is None:
            continue
        xyz = np.asarray(feat["geometry"]["coordinates"], np.float64)[a:bb + 1]
        hw = np.asarray(p["half_width"], np.float64)[a:bb + 1]
        if len(xyz) < 2:
            continue
        seg = np.diff(xyz[:, :2], axis=0)
        cum = np.concatenate([[0], np.cumsum(np.hypot(seg[:, 0], seg[:, 1]))])
        if cum[-1] < 8:
            continue
        d = np.arange(3.0, cum[-1] - 3.0, WALL_STEP)              # not into the junction mouths
        i = np.clip(np.searchsorted(cum, d, side="right") - 1, 0, len(seg) - 1)
        f = (d - cum[i]) / np.maximum(cum[i + 1] - cum[i], 1e-6)
        c = xyz[i, :2] + f[:, None] * (xyz[i + 1, :2] - xyz[i, :2])
        t = seg[i] / np.maximum(np.hypot(seg[i, 0], seg[i, 1]), 1e-6)[:, None]
        off = hw[i] + f * (hw[i + 1] - hw[i]) + WALL_SET
        for side in (1, -1):
            nrm = np.column_stack([-t[:, 1], t[:, 0]]) * side
            pt = c + nrm * off[:, None]
            probe = pt + nrm * 2.0
            ok = np.isin(cell(cls, probe[:, 0], probe[:, 1]), GARDEN)
            pts = shapely.points(pt[:, 0], pt[:, 1])
            near_b = btree.query_nearest(pts, max_distance=2.5, return_distance=False, all_matches=False)[0]
            ok[near_b] = False
            if hedges is not None:
                near_h = hedges.query_nearest(pts, max_distance=2.0, return_distance=False, all_matches=False)[0]
                ok[near_h] = False
            home = np.full(len(pt), -1)
            if htree is not None:
                q, hidx = htree.query_nearest(pts, max_distance=35.0, return_distance=False, all_matches=False)
                home[q] = np.asarray(homes)[hidx]
            ok &= home >= 0
            # contiguous runs, cut into plots with a gate gap between them
            k = 0
            while k < len(ok):
                if not ok[k]:
                    k += 1
                    continue
                e = k
                while e + 1 < len(ok) and ok[e + 1]:
                    e += 1
                start = k
                while start <= e:
                    stop = min(start + int(rng.uniform(10, 28) / WALL_STEP), e)
                    if stop - start >= 4:
                        hs = home[start:stop + 1]
                        runs.append((pt[start:stop + 1], nrm[start:stop + 1], int(np.bincount(hs[hs >= 0]).argmax())))
                    start = stop + int(rng.uniform(3, 5) / WALL_STEP)      # gate / drive
                k = e + 1

    extra = []
    uv1 = lambda k, r: np.column_stack([np.full(k, r), np.zeros(k)])
    for pts, nrm, h in runs:
        p = props[h]
        rnd = ((p.get("seed") or 0) * 2654435761 % 2**32) / 2**32
        style = rng.uniform()
        z = cell(hgt, pts[:, 0], pts[:, 1])
        if style < 0.25:                                            # hedge along the plot
            for (x, y), zz in zip(pts[::2], z[::2]):
                extra.append((x, y, zz, rng.uniform(1.4, 2.0), rng.uniform(0, 360), 6))
            continue
        finish = _wall_finish(p)
        colour = np.asarray(p.get("wall_colour") or (200, 190, 175), np.float64) / 255.0
        railing = style >= 0.65
        height = rng.uniform(0.5, 0.8) if railing else rng.uniform(0.8, 1.3)
        th, ch = 0.2, 0.06                                          # wall thickness, coping height
        out_l, in_l = pts, pts + nrm * th                           # road face, garden face
        base = z - 0.3                                              # into the ground (slopes)
        top = z + height
        s_al = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])

        def strip(material, line, z0, z1, flip, u, v0, v1, col):
            k = len(line)
            pos = np.concatenate([np.column_stack([line, z0]), np.column_stack([line, z1])])
            uv = np.concatenate([np.column_stack([u, v0]), np.column_stack([u, v1])])
            tri = []
            for q in range(k - 1):
                a_, b_, c_, d_ = q, q + 1, k + q + 1, k + q
                tri += [[a_, c_, b_], [a_, d_, c_]] if flip else [[a_, b_, c_], [a_, c_, d_]]
            mesh.add(material, pos, np.asarray(tri), uv0=uv, uv1=uv1(2 * k, rnd), colour=np.tile(col, (2 * k, 1)))

        strip(finish, out_l, base, top, True, s_al, base - z, top - z, colour)
        strip(finish, in_l, base, top, False, s_al, base - z, top - z, colour)
        for q, sgn in ((0, 1), (-1, -1)):
            quad = np.asarray([[*out_l[q], base[q]], [*in_l[q], base[q]], [*in_l[q], top[q]], [*out_l[q], top[q]]])
            tri = [[0, 1, 2], [0, 2, 3]] if sgn > 0 else [[0, 2, 1], [0, 3, 2]]
            mesh.add(finish, quad, np.asarray(tri), uv0=np.asarray([[0, -0.3], [th, -0.3], [th, height], [0, height]]),
                     uv1=uv1(4, rnd), colour=np.tile(colour, (4, 1)))
        # coping: a slab over the top, 5 cm proud on both faces
        co, ci = pts - nrm * 0.05, pts + nrm * (th + 0.05)
        grey = np.asarray([0.72, 0.7, 0.66])
        strip("coping", co, top, top + ch, True, s_al, np.zeros_like(top), np.full_like(top, ch), grey)
        strip("coping", ci, top, top + ch, False, s_al, np.zeros_like(top), np.full_like(top, ch), grey)
        k = len(pts)
        cp = np.concatenate([np.column_stack([co, top + ch]), np.column_stack([ci, top + ch])])
        tri = [[q, k + q + 1, k + q] for q in range(k - 1)] + [[q, q + 1, k + q + 1] for q in range(k - 1)]
        mesh.add("coping", cp, np.asarray(tri), uv0=cp[:, :2], uv1=uv1(2 * k, rnd), colour=np.tile(grey, (2 * k, 1)))
        if railing:
            # one strip along the wall's middle; the material cuts bars (12 cm), posts (2 m) and the top rail out of it
            # (UV0 = metres along, 0..1 up): one quad per metre instead of a box per bar
            paint = np.asarray([(0.08, 0.12, 0.09), (0.05, 0.05, 0.05), (0.3, 0.32, 0.33), (0.85, 0.85, 0.82)][int(rnd * 4) % 4])
            t0 = top + ch
            strip("rail", pts + nrm * (th / 2), t0, t0 + rng.uniform(0.8, 1.1), False, s_al, np.zeros_like(t0),
                  np.ones_like(t0), paint)
    return mesh, np.asarray(extra, np.float64).reshape(-1, 6)


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
    ap.add_argument("--si", type=int, nargs=2)
    ap.add_argument("--sj", type=int, nargs=2)
    ap.add_argument("--all", action="store_true", help="every sector of the package (no merged lanes.json: lanes.bin per sector)")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    t0 = time.time()
    man = json.loads((args.package / "manifest.json").read_text())
    size = man["sector_size"]
    rng = np.random.default_rng(11)
    lanes, controls = [], None
    summary = {}
    walls_total = [0, 0]           # garden wall triangles, garden hedge plants
    if args.all:
        todo = sorted(tuple(s) for s in man["sectors"])
    else:
        todo = [(si, sj) for sj in range(args.sj[0], args.sj[1] + 1) for si in range(args.si[0], args.si[1] + 1)]
    for si, sj in todo:
        if True:
            src = args.package / "sectors" / f"{si}_{sj}"
            dst = args.out / "sectors" / f"{si}_{sj}"
            dst.mkdir(parents=True, exist_ok=True)
            pl = plants(src, rng)
            gw, gwh = garden_walls(src, np.random.default_rng(si * 1000 + sj))
            if len(gwh):
                gwh[:, 0], gwh[:, 1], gwh[:, 2], gwh[:, 3] = gwh[:, 0] * 100, -gwh[:, 1] * 100, gwh[:, 2] * 100, gwh[:, 3] * 100
                pl = np.concatenate([pl, gwh])
            write_instances(dst / "plants.bin", pl, True)
            if len(gw):
                gltf.write_glb(dst / "walls.glb", gw, (size * si - 16000, size * sj - 16000),
                               {m: {"colour": (0.7, 0.7, 0.7)} for m in gw.parts}, f"walls_{si}_{sj}")
            walls_total[0] += len(gw)
            walls_total[1] += len(gwh)
            lp = lamps(src)
            write_instances(dst / "lamps.bin", lp, False)
            corner = (size * si - 16000, size * sj - 16000)
            wm = water(src)
            if len(wm):
                gltf.write_glb(dst / "water.glb", wm, corner, {"water": {"colour": (0.1, 0.2, 0.25), "roughness": 0.05}},
                               f"water_{si}_{sj}")
            rc = road_collision(src)
            gltf.write_glb(dst / "roads_collision.glb", rc, corner, {m: {"colour": (1.0, 0.0, 1.0)} for m in rc.parts},
                           f"roads_collision_{si}_{sj}")
            om = openings(src)
            if len(om):
                gltf.write_glb(dst / "openings.glb", om, corner,
                               {m: {"colour": (0.5, 0.5, 0.5)} for m in om.parts},
                               f"openings_{si}_{sj}")
            lg = json.load(gzip.open(src / "lanes.json.gz"))
            write_lanes_bin(dst / "lanes.bin", lg)
            controls = controls or lg["controls"]
            if not args.all:
                for e in lg["elements"]:
                    e["points"] = [ue(*q) for q in e["points"]]
                    lanes.append(e)
            summary[f"{si}_{sj}"] = {"plants": len(pl), "lamps": len(lp), "water_tris": len(wm), "opening_tris": len(om),
                                     "by_kind": np.bincount(pl[:, 5].astype(int), minlength=len(KINDS)).tolist()}
    if not args.all:
        (args.out / "lanes.json").write_text(json.dumps({"controls": controls, "kinds": KINDS, "elements": lanes}))
    (args.out / "objects.json").write_text(json.dumps({"kinds": KINDS, "sectors": summary,
                                                       "seconds": round(time.time() - t0, 1)}, indent=1))
    print(f"garden walls: {walls_total[0]} triangles, {walls_total[1]} hedge plants")
    tot = np.sum([s["by_kind"] for s in summary.values()], axis=0)
    print(f"{len(lanes)} lanes, {sum(s['lamps'] for s in summary.values())} lamps, "
          f"{sum(s['water_tris'] for s in summary.values())} water triangles, plants:",
          dict(zip(KINDS, tot.tolist())), f"{time.time() - t0:.1f} s")


if __name__ == "__main__":
    main()
