"""Roads of a sector for the map package: the drawn surfaces (glTF), their paint (glTF), the centrelines with their attributes (GeoJSON).

Ownership: a road segment (between two samples of a piece) belongs to the sector holding its midpoint, a junction to the one holding
its centre, a car park to the one holding the mean of its vertices; so neighbouring sectors cover every surface once, without a gap.

Surface UVs: uv0 = (metres from the left edge, metres along the road link), uv1 = (fraction of the width from the left edge, metres
along); junctions and car parks: both = plan metres (x, y). Paint follows the French markings the game drew (Unity ChunkMeshes.cs,
BM07 marks): centre line 3 m dashes every 9 m, solid where overtaking is forbidden (doubled solid / dashed where only one way may),
lane dividers 3 m every 6 m (13 m on dual carriageways), edge lines solid or 3 m every 6.5 m, 0.22 m inside the edge, give-way
blocks, turn arrows.
"""
import numpy as np

from roads import lanes as road_lanes

LIFT = 0.02                     # m: paint above the surface
SKIRT = 1.2                     # m: the side of a ground-level road or junction below its edge at least ...
SKIRT_BURY = 0.3                # ... and down to this far under the terrain there (a retaining wall where two levels meet)
DECK_DEPTH = 1.2                # m: the side of a bridge deck below its surface
MATERIALS = dict(asphalt=dict(colour=(0.18, 0.18, 0.19)), dirt=dict(colour=(0.45, 0.38, 0.28)),
                 deck=dict(colour=(0.6, 0.6, 0.58)), paint=dict(colour=(0.92, 0.92, 0.9), roughness=0.6))


def owned(x, y, origin, size):
    return (x >= origin[0]) & (x < origin[0] + size) & (y >= origin[1]) & (y < origin[1] + size)


def surface_material(edge):
    return "dirt" if edge.dirt else "asphalt"


def piece_segments(p, origin, size):
    """Drawn segments of a piece owned by the sector."""
    mid = 0.5 * (p.xy[:-1] + p.xy[1:])
    return np.flatnonzero(p.drawn & owned(mid[:, 0], mid[:, 1], origin, size))


def runs(segs):
    """Consecutive segment indices grouped: [array of k, ...]."""
    segs = np.asarray(segs)
    return np.split(segs, np.flatnonzero(np.diff(segs) != 1) + 1) if len(segs) else []


def strip(top, bottom, s, facing, both=False):
    """A strip between two polylines (n, 3) sharing their vertices: (positions, triangles, uv). Each segment's two triangles wind so
    their normal points along `facing` (n - 1, 3) (a vector on the side they should face); `both`: both faces."""
    n = len(top)
    pos = np.vstack([top, bottom])
    k = np.arange(n - 1)
    a, b, c, d = k, k + 1, n + k + 1, n + k                                          # top k, top k+1, bottom k+1, bottom k
    tri = np.stack([np.c_[a, b, c], np.c_[a, c, d]], axis=1)                        # (n - 1, 2, 3)
    p0, p1, p2 = pos[a], pos[b], pos[c]
    normal = np.cross(p1 - p0, p2 - p0)
    wrong = (normal * facing).sum(axis=1) < 0
    tri[wrong] = tri[wrong][:, :, [0, 2, 1]]
    tri = tri.reshape(-1, 3)
    if both:
        tri = np.vstack([tri, tri[:, [0, 2, 1]]])
    depth = np.linalg.norm(top - bottom, axis=1)
    uv = np.r_[np.c_[s, np.zeros(n)], np.c_[s, depth]]
    return pos, tri, uv


def skirt(mesh, material, edge, inward, s, ground):
    """A strip hanging from an edge polyline (n, 3), facing away from `inward` (n - 1, 2), down SKIRT or to SKIRT_BURY under the
    terrain (`ground(xy)`), whichever is lower: wherever the terrain dips under a road edge, it shows the road's side (a retaining
    wall between two roads at different levels) instead of a gap."""
    away = np.c_[-np.asarray(inward)[:, :2], np.zeros(len(inward))]
    bottom = edge.copy()
    bottom[:, 2] = np.minimum(edge[:, 2] - SKIRT, ground(edge[:, :2]) - SKIRT_BURY)
    pos, tri, uv = strip(edge, bottom, s, away)
    mesh.add(material, pos, tri, uv)


def add_piece(mesh, p, segs, ground):
    """The carriageway of segments `segs` as strips sharing their vertices, its skirts, and for a bridge deck its two sides."""
    material = surface_material(p.edge)
    across = np.hypot(*(p.left[:, :2] - p.right[:, :2]).T)
    for run in runs(segs):
        idx = np.r_[run, run[-1] + 1]
        left, right, s = p.left[idx], p.right[idx], p.s[idx]
        pos, tri, _ = strip(left, right, s, np.tile((0.0, 0.0, 1.0), (len(run), 1)))  # the surface faces up
        uv0 = np.r_[np.c_[np.zeros(len(idx)), s], np.c_[across[idx], s]]              # metres from the left edge, metres along
        uv1 = np.r_[np.c_[np.zeros(len(idx)), s], np.c_[np.ones(len(idx)), s]]        # fraction of the width, metres along
        mesh.add(material, pos, tri, uv0, uv1)
        into = (right - left)[:-1, :2]
        if p.bridge:
            for edge, inward in ((left, into), (right, -into)):
                pos, tri, uv = strip(edge, edge - (0, 0, DECK_DEPTH), s, np.c_[-inward, np.zeros(len(inward))], both=True)
                mesh.add("deck", pos, tri, uv)
        elif not p.tunnel:
            skirt(mesh, material, left, into, s, ground)
            skirt(mesh, material, right, -into, s, ground)


def ccw_up(v, t):
    """Triangles of `t` wound counter-clockwise seen from above."""
    t = np.asarray(t).copy()
    a, b, c = v[t[:, 0], :2], v[t[:, 1], :2], v[t[:, 2], :2]
    cw = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]) < 0
    t[cw] = t[cw][:, [0, 2, 1]]
    return t


def surfaces(mesh, pieces, meshes, park_meshes, origin, size, junction_triangles, ground):
    """Every surface owned by the sector into `mesh`; `ground(xy)`: the package terrain (for the skirts). Returns counts."""
    n_seg = n_junction = n_park = 0
    for p in pieces:
        segs = piece_segments(p, origin, size)
        add_piece(mesh, p, segs, ground)
        n_seg += len(segs)
    for j, v in meshes:
        if not owned(np.array([j.centre[0]]), np.array([j.centre[1]]), origin, size)[0]:
            continue
        t = ccw_up(v, junction_triangles(j, v))
        material = "asphalt" if j.paved else "dirt"
        mesh.add(material, v, t, v[:, :2], v[:, :2])
        for a, b in j.boundary[j.boundary[:, 2] == 0][:, :2]:                          # outline edges that are not a road's mouth;
            d = v[b, :2] - v[a, :2]                                                    # the surface lies on their left (stitch.py)
            skirt(mesh, material, v[[a, b]], np.array([[-d[1], d[0]]]), np.array([0.0, float(np.hypot(*d))]), ground)
        n_junction += 1
    for v, t in park_meshes:
        c = v[:, :2].mean(axis=0)
        if not owned(np.array([c[0]]), np.array([c[1]]), origin, size)[0]:
            continue
        mesh.add("asphalt", v, ccw_up(v, t), v[:, :2], v[:, :2])
        n_park += 1
    return dict(road_segments=n_seg, junctions=n_junction, car_parks=n_park)


# ------------------------------------------------------------------ paint

class Paint:
    """Flat quads of paint, LIFT over the surface, counter-clockwise from above."""

    def __init__(self):
        self.pos, self.tri = [], []

    def quad(self, a, b, c, d):
        """Corners in order round the quad (either way round)."""
        base = len(self.pos)
        q = np.array([a, b, c, d], float) + (0, 0, LIFT)
        cross = (q[1, 0] - q[0, 0]) * (q[2, 1] - q[0, 1]) - (q[1, 1] - q[0, 1]) * (q[2, 0] - q[0, 0])
        self.pos += list(q)
        self.tri += [(base, base + 1, base + 2), (base, base + 2, base + 3)] if cross > 0 else [(base, base + 2, base + 1), (base, base + 3, base + 2)]

    def tri_flat(self, a, b, c):
        base = len(self.pos)
        q = np.array([a, b, c], float) + (0, 0, LIFT)
        cross = (q[1, 0] - q[0, 0]) * (q[2, 1] - q[0, 1]) - (q[1, 1] - q[0, 1]) * (q[2, 0] - q[0, 0])
        self.pos += list(q)
        self.tri.append((base, base + 1, base + 2) if cross > 0 else (base, base + 2, base + 1))

    def into(self, mesh):
        if self.tri:
            pos = np.array(self.pos)
            mesh.add("paint", pos, np.array(self.tri), pos[:, :2])


def unit(v):
    return v / max(float(np.linalg.norm(v)), 1e-9)


def stripe(paint, p, k, t0, t1, f, shift, half):
    """Paint along segment k from fraction t0 to t1, at fraction f (per point) of the way from the left edge to the right one."""
    la, ra = p.left[k] + (p.left[k + 1] - p.left[k]) * t0, p.right[k] + (p.right[k + 1] - p.right[k]) * t0
    lb, rb = p.left[k] + (p.left[k + 1] - p.left[k]) * t1, p.right[k] + (p.right[k + 1] - p.right[k]) * t1
    sa, sb = unit(ra - la), unit(rb - lb)
    fa, fb = f[k] + (f[k + 1] - f[k]) * t0, f[k] + (f[k + 1] - f[k]) * t1
    a, b = la + (ra - la) * fa + sa * shift, lb + (rb - lb) * fb + sb * shift
    paint.quad(a - sa * half, a + sa * half, b + sb * half, b - sb * half)


def mark_line(paint, p, segs, f, shift, half, on, period, draw):
    """A line at fraction f, `shift` m to the right, on the owned segments `draw` accepts; dashes `on` m every `period` m (0: solid),
    laid out by distance along the whole link so they stay in step across sectors."""
    for k in segs:
        s, s1 = p.s[k], p.s[k + 1]
        length = s1 - s
        if length <= 1e-3 or f[k] < 0 or f[k + 1] < 0 or not draw(k):
            continue
        if period <= 0:
            stripe(paint, p, k, 0.0, 1.0, f, shift, half)
            continue
        start = np.floor(s / period) * period
        while start < s1:
            a, b = max(start, s), min(start + on, s1)
            if b - a > 0.05:
                stripe(paint, p, k, (a - s) / length, (b - s) / length, f, shift, half)
            start += period


def give_way(paint, p, i, after, two_lanes):
    """Give-way blocks across the lane arriving at sample i, half a metre before the junction."""
    if p.oneway == (1 if after else 2):
        return
    j = i + (1 if after else -1)
    if j < 0 or j >= len(p.xy):
        return
    along = np.r_[p.xy[j] - p.xy[i], 0.0]
    length = float(np.hypot(along[0], along[1]))
    if length < 0.2:
        return
    t = min(0.5 / length, 1.0)
    left, right = p.left[i] + (p.left[j] - p.left[i]) * t, p.right[i] + (p.right[j] - p.right[i]) * t
    mid = 0.5 * (left + right)
    whole = p.oneway != 0 or not two_lanes
    a0, b0 = (left, right) if whole else (mid, left if after else right)
    across = b0 - a0
    width = float(np.linalg.norm(across))
    if width < 1.0:
        return
    across /= width
    depth = along / length * 0.25
    u = 0.3
    while u + 0.5 <= width - 0.2:
        a, b = a0 + across * u, a0 + across * (u + 0.5)
        paint.quad(a - depth, a + depth, b + depth, b - depth)
        u += 1.0


def arrow(paint, at, direction, bits):
    """A 5 m turn arrow lying at `at`, pointing along `direction` (plan): a shaft and a head per allowed way."""
    F = np.r_[unit(np.asarray(direction, float)), 0.0]
    L = np.array([-F[1], F[0], 0.0])
    at = np.asarray(at, float)

    def bar(a, b, half):
        side = np.cross((0, 0, 1), unit(b - a)) * half
        paint.quad(a - side, a + side, b + side, b - side)

    def head(tip_base, d, half, length):
        side = np.cross((0, 0, 1), d) * half
        paint.tri_flat(tip_base - side, tip_base + side, tip_base + d * length)

    root, top = at - F * 2.5, at + F * (1.0 if bits & 2 else 0.4)
    bar(root, top, 0.12)
    if bits & 2:
        head(top, F, 0.45, 1.5)
    for bit, side in ((1, L), (4, -L)):
        if bits & bit:
            d = unit(F + side)
            bend = at + F * 0.2
            tip = bend + d * 0.9
            bar(bend, tip, 0.12); head(tip, d, 0.45, 1.2)


def paint_piece(paint, p, segs, origin, size):
    """The markings of a piece on its owned segments (the BM07 rules of the game)."""
    e = p.edge
    if e.dirt or e.kind == 5 or p.line_kinds is None or not len(segs):              # tracks and dirt roads are not painted; bridges are
        return
    marks = p.marked.astype(np.uint8) | (p.no_overtaking[:, 0].astype(np.uint8) << 1) | (p.no_overtaking[:, 1].astype(np.uint8) << 2) | (p.edge_style.astype(np.uint8) << 3)
    painted = lambda k: bool(marks[k] & 1)
    hw = np.maximum(p.hw, 1e-3)
    fracs = [np.nan_to_num((hw - p.line_offsets[:, k]) / (2.0 * hw), nan=-1.0) for k in range(len(p.line_kinds))]
    dual = e.kind in (2, 3)
    own = set(segs.tolist())
    centre = int(np.flatnonzero(p.line_kinds == road_lanes.CENTRE)[0]) if (p.line_kinds == road_lanes.CENTRE).any() else -1
    for i, after in p.give_way:
        k = min(max(i if after else i - 1, 0), len(p.xy) - 2)
        if k not in own:
            continue
        on_centre = centre >= 0 and painted(k) and fracs[centre][k] >= 0 and fracs[centre][k + 1] >= 0
        give_way(paint, p, i, after, on_centre)
    for line, f in zip(p.line_kinds, fracs):
        if line == road_lanes.CENTRE:
            mark_line(paint, p, segs, f, 0.0, 0.06, 3.0, 9.0, lambda k: painted(k) and (marks[k] & 6) == 0)
            mark_line(paint, p, segs, f, 0.0, 0.06, 0.0, 0.0, lambda k: painted(k) and (marks[k] & 6) == 6)
            mark_line(paint, p, segs, f, 0.1, 0.05, 0.0, 0.0, lambda k: painted(k) and (marks[k] & 6) == 2)
            mark_line(paint, p, segs, f, -0.1, 0.05, 3.0, 9.0, lambda k: painted(k) and (marks[k] & 6) == 2)
            mark_line(paint, p, segs, f, -0.1, 0.05, 0.0, 0.0, lambda k: painted(k) and (marks[k] & 6) == 4)
            mark_line(paint, p, segs, f, 0.1, 0.05, 3.0, 9.0, lambda k: painted(k) and (marks[k] & 6) == 4)
        else:
            mark_line(paint, p, segs, f, 0.0, 0.075, 3.0, 13.0 if dual else 6.0, painted)
    width = np.maximum(np.hypot(*(p.left[:, :2] - p.right[:, :2]).T), 0.5)
    for f in (0.22 / width, 1.0 - 0.22 / width):
        mark_line(paint, p, segs, f, 0.0, 0.055, 0.0, 0.0, lambda k: (marks[k] >> 3 & 3) == road_lanes.EDGE_SOLID)
        mark_line(paint, p, segs, f, 0.0, 0.045, 3.0, 6.5, lambda k: (marks[k] >> 3 & 3) == road_lanes.EDGE_DASHED)
    for x, north, z, dx, dn, bits in p.arrows or []:
        if owned(np.array([x]), np.array([north]), origin, size)[0]:
            arrow(paint, (x, north, z), (dx, dn), bits)


# ------------------------------------------------------------------ attributes

def link_id(link):
    """A road link's key (first BD TOPO section, flag) as text: `TRONROUT...+` / `-`. A piece is that and the distance it starts at."""
    if isinstance(link, tuple):
        return f"{link[0]}{'+' if link[1] else '-'}"
    return str(link)


def piece_feature(p):
    """GeoJSON feature of a piece: its centreline (x, y, z) and attributes."""
    e = p.edge
    k = np.flatnonzero(p.drawn)
    lanes = p.lanes if p.lanes is not None else np.zeros((len(p.xy), 2))
    link = link_id(p.link)
    props = dict(id=f"{link}@{p.s[0]:.1f}", link=link, bdtopo=e.cleabs, osm=int(e.osm_id) or None,
                 **{"class": e.klass}, nature=e.nature, importance=e.importance, urban=bool(e.urban),
                 width_real=round(float(e.width_real), 2), width_surveyed=bool(e.width_surveyed), width_drawn=round(float(2 * p.hw.mean()), 2),
                 lanes_forward=int(round(float(np.median(lanes[:, 0])))), lanes_backward=int(round(float(np.median(lanes[:, 1])))),
                 oneway=int(p.oneway), limit_forward=int(p.limits[0]), limit_backward=int(p.limits[1]),
                 surface=e.surface or None, dirt=bool(e.dirt), lit=bool(e.lit), name=e.name or None, number=e.number or None,
                 bridge=bool(p.bridge), tunnel=bool(p.tunnel),
                 s=np.round(p.s, 2).tolist(), half_width=np.round(p.hw, 3).tolist(), tilt=np.round(p.tilt, 4).tolist(),
                 drawn_from=int(k[0]) if len(k) else None, drawn_to=int(k[-1] + 1) if len(k) else None)
    coords = np.round(np.c_[p.xy, p.z], 3).tolist()
    return dict(type="Feature", geometry=dict(type="LineString", coordinates=coords), properties=props)


def features(pieces, origin, size):
    """Features of the pieces whose first drawn segment (else first point) lies in the sector."""
    out = []
    for p in pieces:
        k = np.flatnonzero(p.drawn)
        at = 0.5 * (p.xy[k[0]] + p.xy[k[0] + 1]) if len(k) else p.xy[0]
        if owned(np.array([at[0]]), np.array([at[1]]), origin, size)[0]:
            out.append(piece_feature(p))
    return out


def lane_records(elements, origin, size):
    """Lane graph elements whose first point lies in the sector, as plain records."""
    out = []
    for e in elements:
        if not owned(e.xyz[:1, 0], e.xyz[:1, 1], origin, size)[0]:
            continue
        kind, importance, rank, half_width, dirt = e.road
        out.append(dict(id=int(e.id), kind=int(e.kind), points=np.round(e.xyz, 3).tolist(), speed=np.round(e.speed, 2).tolist(),
                        next=[int(s) for s in e.succ], left=int(e.left), right=int(e.right), control=int(e.control),
                        yields=[int(y) for y in e.yields], turn=round(float(e.turn), 1), junction=int(e.junction), limit=int(e.limit),
                        road=dict(kind=int(kind), importance=int(importance), rank=int(rank), half_width=round(float(half_width), 2), dirt=bool(dirt))))
    return out
