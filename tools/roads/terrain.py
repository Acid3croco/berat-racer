"""Shaping a terrain height grid around the road surface, and the embankment ribbons that hide the grid along the roads.

Rules applied to a grid of vertices (row = north index, south first; vertex (r, c) at (x0 + c * cell, z0 + r * cell)):

  blend   under the road and on its shoulder the ground sits just below the road; from there it eases back to the natural ground
          (cut or embankment) over `EMBANKMENT` metres.
  bench   no terrain triangle may stand above the road. A triangle lies below the road wherever all its corners lie below the
          road's tangent planes, so: every corner of a grid cell touched by the road is lowered to the lowest tangent plane of the
          road points that can share a cell with it, less cell^2 / 2R over a crest (the road falls away from its tangent plane). This
          keeps the coarse 16 m terrain under the roads too.
  drape   (with ribbons) every vertex under a ribbon is lowered just below it, to the lowest ribbon over it.

Ribbons: along every road edge and junction kerb, a strip sampled with the road that blends from the road edge to the ground in one
smoothstep (level with the road where it leaves it, tangent to the ground where it meets it), over a width that keeps it no steeper
than BLEND_SLOPE, never into another road nor folded on the inside of a bend. The 4 m cells it covers are cut out of the terrain
and the gap is filled by tools/stitch.py, so the ribbon meets the terrain without a step.
"""
import numpy as np
import shapely
from rasterio import features
from rasterio.transform import from_origin
from scipy.ndimage import distance_transform_edt
from scipy.spatial import cKDTree

from . import config


class Footprint:
    """The road surface of a rectangle as a 1 m raster: distance from any point to the nearest road surface."""

    def __init__(self, polygons, x0, z0, width, height):
        self.x0, self.z0, self.cols, self.rows = x0, z0, int(width) + 1, int(height) + 1
        self.polygons = polygons
        self._grown = {}
        if polygons:                                       # pixel centres on whole metres, row 0 = north
            transform = from_origin(x0 - 0.5, z0 + height + 0.5, 1.0, 1.0)
            mask = features.rasterize([(p, 1) for p in polygons], out_shape=(self.rows, self.cols), transform=transform, dtype=np.uint8).astype(bool)
            self.outside = distance_transform_edt(~mask[::-1]).astype(np.float32)          # row 0 = south
        else:
            self.outside = np.full((self.rows, self.cols), 1e6, np.float32)

    def grown(self, by):
        """The polygons widened by `by` metres (kept: the 4 m and the 16 m terrain ask for the same ones)."""
        if by not in self._grown:
            self._grown[by] = list(shapely.buffer(np.array(self.polygons, dtype=object), by, quad_segs=16))
        return self._grown[by]

    def distance(self, x, z):
        """Metres from (x, z) to the road surface, 0 on it (nearest whole metre)."""
        c = np.clip(np.rint(np.asarray(x) - self.x0).astype(int), 0, self.cols - 1)
        r = np.clip(np.rint(np.asarray(z) - self.z0).astype(int), 0, self.rows - 1)
        return self.outside[r, c]


def _smoothstep(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def blend(h, x0, z0, cell, footprint, cloud, sink=config.ROAD_SINK):
    """Ground just below the road under it and on the shoulder, easing back to the natural ground beyond. Returns the new heights."""
    if not len(cloud["z"]):
        return h
    rows, cols = h.shape
    gx, gz = np.meshgrid(x0 + cell * np.arange(cols), z0 + cell * np.arange(rows))
    outside = footprint.distance(gx, gz)
    near = outside < config.SHOULDER + config.EMBANKMENT
    v = np.c_[gx[near], gz[near]]
    d, i = cKDTree(cloud["xy"]).query(v)
    offset = v - cloud["xy"][i]
    reach = cloud["hw"][i] + config.SHOULDER                                # the road plane is followed this far from its centreline, flat beyond
    offset *= np.minimum(1.0, reach / np.maximum(d, 1e-6))[:, None]
    road = cloud["z"][i] + (cloud["grad"][i] * offset).sum(axis=1)
    weight = 1.0 - _smoothstep((outside[near] - config.SHOULDER) / config.EMBANKMENT)
    out = h.copy()
    out[near] = h[near] * (1.0 - weight) + (road - sink) * weight
    return out


def bench(h, x0, z0, cell, footprint, cloud, sink):
    """Lower the corners of every grid cell the road touches below the road's tangent planes. Returns the new heights."""
    if not footprint.polygons or not len(cloud["z"]):
        return h
    rows, cols = h.shape
    transform = from_origin(x0, z0 + (rows - 1) * cell, cell, cell)
    touched = features.rasterize([(p, 1) for p in footprint.grown(0.5)], out_shape=(rows - 1, cols - 1), transform=transform,
                                 dtype=np.uint8, all_touched=True).astype(bool)[::-1]
    corner = np.zeros((rows, cols), bool)
    for dr in (0, 1):
        for dc in (0, 1):
            corner[dr:rows - 1 + dr, dc:cols - 1 + dc] |= touched
    r, c = np.nonzero(corner)
    if not len(r):
        return h
    v = np.c_[x0 + cell * c, z0 + cell * r]
    # a cloud point stands for the cross-section of the road through it. It concerns a corner only if the two can lie in one cell:
    # the point of the cross-section nearest to the corner is within one cell of it, east-west and north-south (`keep` below).
    # Such a corner is within (cell + 0.5) * sqrt(2) + half width of the cloud point: candidates are searched that far, by class of width
    tree, width = cKDTree(v), np.ceil(cloud["hw"])
    i, q = [], []
    for w in np.unique(width):
        members = np.flatnonzero(width == w)
        pairs = tree.sparse_distance_matrix(cKDTree(cloud["xy"][members]), (cell + 0.5) * np.sqrt(2.0) + w + 0.01, output_type="coo_matrix")
        i.append(pairs.row)
        q.append(members[pairs.col])
    i, q = np.concatenate(i), np.concatenate(q)
    offset = v[i] - cloud["xy"][q]
    tan = cloud["tan"][q]
    lateral = offset[:, 1] * tan[:, 0] - offset[:, 0] * tan[:, 1]
    sideways = np.clip(lateral, -cloud["hw"][q], cloud["hw"][q])[:, None] * np.c_[-tan[:, 1], tan[:, 0]]
    keep = np.abs(offset - sideways).max(axis=1) <= cell + 0.5
    i, q = i[keep], q[keep]
    plane = cloud["z"][q] + (cloud["grad"][q] * (v[i] - cloud["xy"][q])).sum(axis=1)
    # over a crest the road falls away from its tangent plane by d^2 / 2R a distance d along it (d up to a cell): sink that much more
    plane -= 0.5 * cell * cell * cloud.get("crest", np.zeros(len(cloud["z"])))[q]
    lowest = np.full(len(v), np.inf)
    np.minimum.at(lowest, i, plane)
    out = h.copy()
    out[r, c] = np.minimum(h[r, c], lowest - sink)
    return out


# ---------------------------------------------------------------- ribbons

def inner_reach(edge, out):
    """How far a ribbon may reach from each edge point before it folds over itself on the inside of a bend: FOLD_FRACTION of the
    edge's radius where the ribbon's side is the inside (inf on straights and outsides). The bend is measured over KERB_CHORD either
    side along the edge, as for the kerbs: neighbouring samples alone are too noisy, and read no bend at a piece's ends."""
    n = len(edge)
    if n < 2:
        return np.full(n, np.inf)
    return kerb_reach(edge, [(i, i + 1) for i in range(n - 1)], out)


def kerb_reach(points, segments, out):
    """How far a ribbon may reach from each point of a kerb or road edge before it folds: `segments` [(a, b)] are its edges (vertex indices into `points`, in outline order).
    The bend at a vertex is the circle through the kerb points KERB_CHORD before and after it along the kerb (its vertices are too
    dense and uneven for neighbour angles). A roundabout island's kerb turns all the way round: its ribbon reaches at most
    FOLD_FRACTION of the island's radius."""
    n = len(points)
    reach = np.full(n, np.inf)
    nxt, prv = np.full(n, -1), np.full(n, -1)
    for a, b in segments:
        nxt[a], prv[b] = b, a
    def walk(i, step):                                                                           # near the kerb's end the chord may shorten, to 1 m
        j, run = i, 0.0
        while run < config.KERB_CHORD:
            k = step[j]
            if k < 0 or k == i:
                break
            run += np.hypot(*(points[k, :2] - points[j, :2])); j = k
        return j if run >= 1.0 else None
    for i in np.flatnonzero((nxt >= 0) | (prv >= 0)):
        p, q = walk(i, prv), walk(i, nxt)
        if p is None or q is None:
            continue
        d1, d2 = points[i, :2] - points[p, :2], points[q, :2] - points[i, :2]
        cross = d1[0] * d2[1] - d1[1] * d2[0]
        k = 2.0 * cross / max(np.hypot(*d1) * np.hypot(*d2) * np.hypot(*(d1 + d2)), 1e-9)          # signed curvature of the circle, left turn positive
        left = np.array([-(d1 + d2)[1], (d1 + d2)[0]])
        if np.sign(left @ out[i]) * k > 1e-4:                                                    # the ribbon lies on the side the kerb turns to
            reach[i] = config.FOLD_FRACTION / abs(k)
    for _ in range(3):                                                                           # over the neighbouring vertices too
        reach = np.minimum(reach, np.where(prv >= 0, reach[np.maximum(prv, 0)], np.inf))
        reach = np.minimum(reach, np.where(nxt >= 0, reach[np.maximum(nxt, 0)], np.inf))
    return reach


BLEND_FRACTIONS = np.array([0.08, 0.18, 0.32, 0.5, 0.68, 0.84, 1.0])     # of the ribbon's width: where its seven cross-section points are


def smoothstep(t):
    return t * t * (3.0 - 2.0 * t)


def ribbon_profile(edge, out, natural, footprint, cap=None):
    """Embankment beside a road edge: one smooth blend from the road edge to the ground. `edge` (n, 3): x, north, height of the edge
    points; `out` (n, 2): unit outward directions; `natural(xy)`: the ground. Returns (n, 8): the width W, then the heights at
    BLEND_FRACTIONS of W: edge + s(t) (ground - edge), s the smoothstep, so the ribbon leaves the road level and meets the ground
    tangent to it (no crease at either end). W is the narrowest of RIBBON_MIN .. RIBBON_REACH keeping the steepest part (1.5 x the
    height difference / W) at BLEND_SLOPE or less, and stops a metre short of another road and where the bend would fold it
    (`cap`, default `inner_reach`)."""
    n = len(edge)
    if not n:
        return np.zeros((0, 8))
    d = np.arange(0.5, config.RIBBON_REACH + 0.01, 0.5)
    p = edge[:, None, :2] + out[:, None, :] * d[None, :, None]
    g = natural(p.reshape(-1, 2)).reshape(n, len(d))
    others = footprint.distance(p[..., 0], p[..., 1]) < d[None, :] - 1.0              # another road is nearer than ours
    room = np.where(others.any(axis=1), d[np.argmax(others, axis=1)] - 1.0, config.RIBBON_REACH)
    room = np.minimum(room, inner_reach(edge, out) if cap is None else cap)
    room = np.clip(room, 0.5, config.RIBBON_REACH)
    gentle = 1.5 * np.abs(g - edge[:, 2:3]) <= config.BLEND_SLOPE * d[None, :]
    ok = gentle & (d[None, :] >= config.RIBBON_MIN) & (d[None, :] <= room[:, None])
    width = np.where(ok.any(axis=1), d[np.argmax(ok, axis=1)], room)
    width = np.minimum(width, room)
    at = edge[:, None, :2] + out[:, None, :] * (width[:, None, None] * BLEND_FRACTIONS[None, :, None])
    ground = natural(at.reshape(-1, 2)).reshape(n, len(BLEND_FRACTIONS))
    heights = edge[:, 2:3] + smoothstep(BLEND_FRACTIONS)[None, :] * (ground - edge[:, 2:3])
    return np.c_[width, heights]


def ribbon_points(edge, out, profile):
    """(n, 8, 3) points across the ribbon: the edge, then the seven blend points (x, north, height)."""
    at = edge[:, None, :2] + out[:, None, :] * (profile[:, :1, None] * BLEND_FRACTIONS[None, :, None])
    return np.concatenate([edge[:, None, :], np.concatenate([at, profile[:, 1:, None]], axis=2)], axis=1)


def ribbon_triangles(points, segments):
    """Triangles of a ribbon between consecutive cross-sections (the game splits each quad the same way): (m, 3, 3). A triangle folded
    over (its plan winding opposite to the ribbon's: a sharp bend) is left out, here and in the game."""
    out = []
    for i, j in segments:
        a, b = points[i], points[j]
        for k in range(points.shape[1] - 1):
            out += [(a[k], b[k], b[k + 1]), (a[k], b[k + 1], a[k + 1])]
    tris = np.array(out, float).reshape(-1, 3, 3)
    if not len(tris):
        return tris
    area = ((tris[:, 1, 0] - tris[:, 0, 0]) * (tris[:, 2, 1] - tris[:, 0, 1]) - (tris[:, 1, 1] - tris[:, 0, 1]) * (tris[:, 2, 0] - tris[:, 0, 0]))
    expected = np.sign(np.median(area[np.abs(area) > 1e-6])) if (np.abs(area) > 1e-6).any() else 1.0
    return tris[area * expected > 1e-6]


def drape(h, x0, z0, cell, triangles, sink):
    """Lower every vertex under one of `triangles` (plan x, north; height) to `sink` below the lowest of them there."""
    if not len(triangles):
        return h
    rows, cols = h.shape
    lowest = np.full(h.shape, np.inf)
    xy, z = triangles[:, :, :2], triangles[:, :, 2]
    lo = np.floor((xy.min(axis=1) - [x0, z0]) / cell).astype(int)
    hi = np.ceil((xy.max(axis=1) - [x0, z0]) / cell).astype(int)
    span = (hi - lo).max(axis=0) if len(lo) else (0, 0)
    for dc in range(int(span[0]) + 1):
        for dr in range(int(span[1]) + 1):
            c, r = lo[:, 0] + dc, lo[:, 1] + dr
            ok = (c <= hi[:, 0]) & (r <= hi[:, 1]) & (c >= 0) & (r >= 0) & (c < cols) & (r < rows)
            if not ok.any():
                continue
            k = np.flatnonzero(ok)
            p = np.c_[x0 + c[k] * cell, z0 + r[k] * cell]
            a, b, e = xy[k, 0], xy[k, 1], xy[k, 2]
            area = (b[:, 0] - a[:, 0]) * (e[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (e[:, 0] - a[:, 0])
            good = np.abs(area) > 1e-9
            wb = ((p[:, 0] - a[:, 0]) * (e[:, 1] - a[:, 1]) - (p[:, 1] - a[:, 1]) * (e[:, 0] - a[:, 0])) / np.where(good, area, 1.0)
            we = ((b[:, 0] - a[:, 0]) * (p[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (p[:, 0] - a[:, 0])) / np.where(good, area, 1.0)
            wa = 1.0 - wb - we
            inside = good & (wa >= -1e-6) & (wb >= -1e-6) & (we >= -1e-6)
            height = wa * z[k, 0] + wb * z[k, 1] + we * z[k, 2]
            np.minimum.at(lowest, (r[k][inside], c[k][inside]), height[inside])
    return np.where(np.isfinite(lowest), np.minimum(h, lowest - sink), h)


def _mesh_corners(rows, cols, x0, z0, cell, p):
    """Corners (r, c) of the game's terrain triangle holding each plan point (two triangles per cell, the diagonal alternating like a
    checkerboard, as ChunkMeshes builds them), and the point's barycentric weights on them: (3, n) rows, (3, n) cols, (3, n) weights."""
    fx = np.clip((p[:, 0] - x0) / cell, 0, cols - 1 - 1e-9)
    fz = np.clip((p[:, 1] - z0) / cell, 0, rows - 1 - 1e-9)
    ix, iz = fx.astype(int), fz.astype(int)
    tx, tz = fx - ix, fz - iz
    even = (ix + iz) % 2 == 0
    # corners a (x, z), b (x, z + 1), c (x + 1, z), e (x + 1, z + 1)
    a, b, c, e = (iz, ix), (iz + 1, ix), (iz, ix + 1), (iz + 1, ix + 1)
    upper_even = tz >= tx                                  # even cells: triangles (a, b, e) and (a, e, c)
    lower_odd = tx + tz <= 1                               # odd cells: triangles (a, b, c) and (c, b, e)
    rr = np.empty((3, len(p)), int)
    cc = np.empty((3, len(p)), int)
    ww = np.empty((3, len(p)))

    def put(mask, corners, weights):
        for k, (corner, w) in enumerate(zip(corners, weights)):
            rr[k, mask], cc[k, mask], ww[k, mask] = corner[0][mask], corner[1][mask], w[mask]

    put(even & upper_even, (a, b, e), (1 - tz, tz - tx, tx))
    put(even & ~upper_even, (a, c, e), (1 - tx, tx - tz, tz))
    put(~even & lower_odd, (a, c, b), (1 - tx - tz, tx, tz))
    put(~even & ~lower_odd, (e, b, c), (tx + tz - 1, 1 - tx, 1 - tz))
    return rr, cc, ww


def tuck(h, x0, z0, cell, triangles, sink, rounds=4, movable=None, deepest=2.0):
    """Where a terrain triangle straddling a ribbon's edge still rises above the ribbon (its corners outside were not lowered), lower
    its corners that may move (`movable`, default all) enough to take the excess away, at most `deepest` metres per round: sampled at
    seven points of every ribbon triangle. Returns the new heights."""
    if not len(triangles):
        return h
    weights = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1], [.5, .5, 0], [0, .5, .5], [.5, 0, .5], [1 / 3, 1 / 3, 1 / 3]])
    pts = np.einsum("wk,tkc->twc", weights, triangles).reshape(-1, 3)
    rows, cols = h.shape
    inside = (pts[:, 0] >= x0) & (pts[:, 0] <= x0 + (cols - 1) * cell) & (pts[:, 1] >= z0) & (pts[:, 1] <= z0 + (rows - 1) * cell)
    pts = pts[inside]
    rr, cc, ww = _mesh_corners(rows, cols, x0, z0, cell, pts[:, :2])
    out = h.copy()
    free = np.ones(h.shape, bool) if movable is None else movable
    for _ in range(rounds):
        mesh = (out[rr, cc] * ww).sum(axis=0)
        over = mesh - (pts[:, 2] - sink)
        moving = free[rr, cc]
        share = (ww * moving).sum(axis=0)                                        # how much of the point's height the movable corners carry
        bad = (over > 1e-4) & (share > 1e-3)
        if not bad.any():
            break
        drop = np.minimum(over[bad] / share[bad], deepest)
        for k in range(3):
            m = moving[k, bad]
            np.minimum.at(out, (rr[k, bad][m], cc[k, bad][m]), out[rr[k, bad][m], cc[k, bad][m]] - drop[m])
    return out
