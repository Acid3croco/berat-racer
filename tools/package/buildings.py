"""Buildings of a sector for the map package: walls and roofs (glTF) and every measure and layout behind them (GeoJSON).

The buildings come from build_world.compute_sector (BD TOPO footprints cut out of the road corridors, heights from BD TOPO checked
against the LiDAR surface model, roofs from the straight skeleton or OSM `roof:shape`, kind from BD TOPO and the points of interest,
facades from tools/facades.py). A building belongs to the sector holding the mean of its outline points.

Walls run from `base` (the ground, sunk WALL_SINK so a slope never shows a gap under them) to the eave; the roof sits on the eave.
Openings (windows, doors, garages, shop fronts, balconies) are data, not geometry: the engine cuts or decals them.
"""
import numpy as np
import shapely

WALL_SINK = 0.8                    # m below the lowest ground the walls start (compute_sector's base)
OPENINGS = ["window", "door", "garage", "shopfront", "balcony"]
WALL_MATERIALS = {1: "stone", 2: "millstone", 3: "concrete", 4: "brick", 5: "breeze_block", 6: "wood", 9: "other"}
ERAS = ["unknown", "before_1950", "1950_1970", "after_1970"]
MATERIALS = dict(wall=dict(colour=(0.85, 0.8, 0.72)), roof=dict(colour=(0.55, 0.3, 0.22)))


def rings_of(b):
    """Outline and courtyards: [(n, 2)], the outline counter-clockwise, courtyards clockwise (as shapely's orient gives them)."""
    cp = np.asarray(b["cp"], float).reshape(-1, 2)
    out, at = [], 0
    for n in b["cn"]:
        out.append(cp[at:at + n]); at += n
    return out


def walls_mesh(mesh, b):
    """Wall quads of every ring edge, facing out of the building (counter-clockwise seen from outside)."""
    base, top = b["b"], b["b"] + b["h"]
    colour = np.array(b["w"], float) / 255.0
    pos, tri, uv = [], [], []
    for ring in rings_of(b):
        along = 0.0
        for a, c in zip(ring, np.roll(ring, -1, axis=0)):
            length = float(np.hypot(*(c - a)))
            if length < 1e-3:
                continue
            k = len(pos)
            pos += [(a[0], a[1], base), (c[0], c[1], base), (c[0], c[1], top), (a[0], a[1], top)]
            uv += [(along, 0.0), (along + length, 0.0), (along + length, top - base), (along, top - base)]
            tri += [(k, k + 1, k + 2), (k, k + 2, k + 3)]
            along += length
    if tri:
        mesh.add("wall", np.array(pos), np.array(tri), np.array(uv), None, np.tile(colour, (len(pos), 1)))


def roof_mesh(mesh, b):
    """The roof triangles (gable ends with the walls), roof faces up, gable faces out."""
    v = np.asarray(b["rv"], float)
    t = np.asarray(b["rt"], np.int64).reshape(-1, 3)
    if not len(t):
        return
    gable = np.asarray(b["rg"], bool)
    a, c, d = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    normal = np.cross(c - a, d - a)
    flip = normal[:, 2] < 0                                                           # roof faces up
    if gable.any():                                                                   # a gable faces out: a step along its normal leaves
        rings = rings_of(b)                                                           # the footprint (an L-shaped house has no useful centre)
        footprint = shapely.Polygon(rings[0], rings[1:])
        mid = ((a + c + d) / 3)[gable, :2]
        horizontal = normal[gable, :2] / np.maximum(np.hypot(normal[gable, 0], normal[gable, 1]), 1e-12)[:, None]
        probe = mid + horizontal * 0.3
        flip[gable] = shapely.contains_xy(footprint, probe[:, 0], probe[:, 1])
    t[flip] = t[flip][:, [0, 2, 1]]
    roof_colour = np.array(b["c"], float) / 255.0
    wall_colour = np.array(b["w"], float) / 255.0
    for material, keep, colour in (("roof", ~gable, roof_colour), ("wall", gable, wall_colour)):
        if keep.any():
            tt = t[keep]
            used, back = np.unique(tt.ravel(), return_inverse=True)
            pts = v[used]
            uv = pts[:, :2] if material == "roof" else np.c_[pts[:, 0] + pts[:, 1], pts[:, 2] - b["b"]]
            mesh.add(material, pts, back.reshape(-1, 3), uv, None, np.tile(colour, (len(pts), 1)))


def feature(b):
    """GeoJSON feature: footprint (outline and courtyards) and everything measured or laid out for the building."""
    rings = rings_of(b)
    coords = [np.round(np.vstack([r, r[:1]]), 3).tolist() for r in rings]
    ground = b["b"] + WALL_SINK
    eave = b["b"] + b["h"]
    walls = [dict(first=w["first"], count=w["count"], front=bool(w["flags"] & 1), party=bool(w["flags"] & 2), blind=bool(w["flags"] & 4),
                  back=bool(w["flags"] & 8), ground=[round(w["g0"], 2), round(w["g1"], 2)],
                  openings=[dict(at=round(t, 2), floor=int(f), type=OPENINGS[kind], width=round(width, 2), height=round(height, 2), sill=round(sill, 2))
                            for t, f, kind, width, height, sill in w["openings"]])
             for w in b.get("fw", [])]
    rm = b["rm"]
    props = dict(id=b["id"], use=b["k"], name=b["n"] or None, ground=round(ground, 2), eave=round(eave, 2), ridge=round(eave + b["r"], 2),
                 wall_height=round(eave - ground, 2), roof_rise=round(b["r"], 2), roof=b["shape"].replace("flat fallback", "flat"), pitch=b["pitch"], floors=int(b.get("ff", 0)), floor_height=round(float(b.get("fh", 0)), 2),
                 era=ERAS[b.get("fa", 0)], wall_material=WALL_MATERIALS.get(b.get("fm", 255)), roof_material_code=None if rm == 255 else int(rm),
                 roof_colour=list(b["c"]), wall_colour=list(b["w"]), front_wall=int(b["fe"]), seed=int(b.get("fs", 0)),
                 tower=dict(x=b["tw"][0], y=b["tw"][1], height=b["tw"][2]) if b["tw"] else None, walls=walls)
    return dict(type="Feature", geometry=dict(type="Polygon", coordinates=coords), properties=props)
