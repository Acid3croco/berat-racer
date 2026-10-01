"""Shaping a terrain height grid around the road surface, and the embankment ribbons that hide the grid along the roads.

Rules applied to a grid of vertices (row = north index, south first; vertex (r, c) at (x0 + c * cell, z0 + r * cell)):

  blend   under the road and on its shoulder the ground sits just below the road; from there it eases back to the natural ground
          (cut or embankment) over `EMBANKMENT` metres.
  bench   no terrain triangle may stand above the road. A triangle lies below the road wherever all its corners lie below the
          road's tangent planes, so: every corner of a grid cell touched by the road is lowered to the lowest tangent plane of the
          road points that can share a cell with it, less cell^2 / 2R over a crest (the road falls away from its tangent plane). This
          keeps the coarse 16 m terrain under the roads too.
  drape   (with ribbons) every vertex under a ribbon is lowered just below it, to the lowest ribbon over it.

Ribbons: along every road edge and junction kerb, a strip sampled with the road: a shoulder, then an embankment slope (fill or
cut, EMBANKMENT_SLOPE) down or up to the LiDAR ground, then the ground itself out to RIBBON_MIN from the edge (past the corners
the bench lowers), never into another road; then an apron, APRON wide, that redraws the terrain as it was before it was lowered
under the ribbon (a 4 m triangle with a lowered corner reaches that far past it). The game draws them with the terrain, so the
4 m grid steps lie hidden under a surface that follows the road.
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
    edge's radius where the ribbon's side is the inside, over the neighbouring points too; inf on straights and outsides."""
    n = len(edge)
    reach = np.full(n, np.inf)
    if n < 3:
        return reach
    d = np.diff(edge[:, :2], axis=0)
    seg = np.maximum(np.hypot(d[:, 0], d[:, 1]), 1e-6)
    turn = np.arctan2(d[:-1, 0] * d[1:, 1] - d[:-1, 1] * d[1:, 0], (d[:-1] * d[1:]).sum(axis=1))
    k = np.r_[0.0, turn / (0.5 * (seg[:-1] + seg[1:])), 0.0]                     # signed curvature, left turn positive
    left = np.r_[d[:1], d][:, [1, 0]] * [-1.0, 1.0]                             # left normal of the edge's direction
    inside = np.sign((left * out).sum(axis=1)) * k > 1e-4                       # the ribbon lies on the side the edge turns to
    reach[inside] = config.FOLD_FRACTION / np.abs(k[inside])
    from scipy.ndimage import minimum_filter1d
    return minimum_filter1d(reach, 7, mode="nearest")


def ribbon_profile(edge, out, natural, footprint):
    """Embankment beside a road edge. `edge` (n, 3): x, north, height of the edge points; `out` (n, 2): unit outward directions;
    `natural(xy)`: LiDAR ground. Returns (n, 5): shoulder width, toe distance and height (where the slope meets the ground), outer
    distance and height (the ribbon's end), all from the edge."""
    n = len(edge)
    if not n:
        return np.zeros((0, 5))
    d = config.SHOULDER + np.arange(0.0, config.RIBBON_REACH + 0.01, 0.5)
    p = edge[:, None, :2] + out[:, None, :] * d[None, :, None]
    g = natural(p.reshape(-1, 2)).reshape(n, len(d))
    top = edge[:, 2] - config.VERGE_DROP
    fill = g[:, 0] < top
    line = top[:, None] + np.where(fill, -1.0, 1.0)[:, None] * config.EMBANKMENT_SLOPE * (d - config.SHOULDER)[None, :]
    meets = np.where(fill[:, None], line <= g, line >= g)
    others = footprint.distance(p[..., 0], p[..., 1]) < d[None, :] - 1.0              # another road is nearer than ours
    last = len(d) - 1
    blocked = np.where(others.any(axis=1), np.argmax(others, axis=1) - 2, last + 1)       # stop a metre short of it
    at = np.where(meets.any(axis=1), np.argmax(meets, axis=1), last)
    rows = np.arange(n)
    toe = np.minimum(at, np.maximum(blocked, 0))
    toe_d, toe_y = d[toe], np.where(toe < at, line[rows, toe], g[rows, toe])
    outer = np.clip(np.maximum(toe, int(np.ceil((config.RIBBON_MIN - config.SHOULDER) / 0.5))), 0, np.maximum(np.minimum(blocked, last), toe))
    outer_d, outer_y = d[outer], np.where(outer == toe, toe_y, g[rows, outer])
    shoulder = np.minimum(config.SHOULDER, np.where(blocked < 0, 0.5, config.SHOULDER))
    cap = inner_reach(edge, out)                                                 # no further than the bend allows: the ribbon would fold
    shoulder = np.minimum(shoulder, cap)
    capped = toe_d > cap
    slope_at = top + np.where(fill, -1.0, 1.0) * config.EMBANKMENT_SLOPE * np.maximum(cap - config.SHOULDER, 0.0)
    toe_y = np.where(capped, np.where(np.isfinite(slope_at), slope_at, toe_y), toe_y)       # the slope stops where the bend allows
    toe_d = np.where(capped, np.maximum(cap, shoulder), toe_d)
    outer_capped = outer_d > np.maximum(cap, shoulder)
    outer_d = np.where(outer_capped, np.maximum(toe_d, np.maximum(cap, shoulder)), outer_d)
    outer_y = np.where(outer_capped, np.where(capped, toe_y, natural(edge[:, :2] + out * outer_d[:, None])), outer_y)
    return np.c_[shoulder, toe_d, toe_y, outer_d, outer_y]


def ribbon_points(edge, out, profile, apron=False):
    """(n, 4, 3) points across the ribbon: edge, shoulder end, toe, outer end (x, north, height); with `apron` (profile of 7
    columns) (n, 6, 3), the apron's middle and outer end after them."""
    shoulder = np.c_[edge[:, :2] + out * profile[:, 0:1], edge[:, 2] - config.VERGE_DROP]
    toe = np.c_[edge[:, :2] + out * profile[:, 1:2], profile[:, 2]]
    outer = np.c_[edge[:, :2] + out * profile[:, 3:4], profile[:, 4]]
    points = [edge, shoulder, toe, outer]
    if apron:
        points += [np.c_[edge[:, :2] + out * (profile[:, 3:4] + f * profile[:, 7:8]), profile[:, 4 + k]] for k, f in ((1, 0.5), (2, 1.0))]
    return np.stack(points, axis=1)


def apron_width(edge, out, profile):
    """The apron's width at each point: APRON, less on the inside of a bend (it must not fold)."""
    return np.clip(inner_reach(edge, out) - profile[:, 3], 0.0, config.APRON)


def apron_points(edge, out, profile, width):
    """(n, 2, 2) plan positions of the apron's middle and outer end."""
    return np.stack([edge[:, :2] + out * (profile[:, 3:4] + f * width[:, None]) for f in (0.5, 1.0)], axis=1)


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
