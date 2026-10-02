"""The ground along the roads: one smooth height field from the paved surfaces to the terrain, cut into the 4 m grid without a step.

The paved surfaces (carriageways, junctions, car parks) are drawn by themselves. Around them the ground is a height field:

  y(p) = g(p) + F (e(p) - g(p)),   F = max_i f_i,   e(p) = sum_i f_i^4 e_i / sum_i f_i^4,   f_i = 1 - smoothstep(d_i / W_i)

over the paved edge samples i near p (edge height e_i, distance d_i, blend width W_i), g the terrain surface itself (the 4 m mesh).
On a paved edge the field is the edge's height, it leaves it level and meets the terrain tangent to it at W, and where several edges
are near (junction corners, roads side by side) it blends them instead of overlapping: no crease, crack or fold. W is the narrowest
of RIBBON_MIN .. RIBBON_REACH keeping the steepest part (1.5 x the height difference / W) at BLEND_SLOPE or less.

The 4 m cells the field or the paved surfaces touch are cut out of the grid (BM07 hole list) and filled with triangles: the paved
outline's points (at the paved height), a FILL_STEP grid inside, and the kept cells' corners, where the field equals the terrain.

  EdgeField      the field, from the paved edges
  surfaces       plan outline of the paved surfaces; PavedHeight, their height at outline points
  cut_cells      the 4 m cells to drop
  fill           per chunk, the triangles (and per vertex the field's weight f, for the verge colour), clockwise seen from above
"""
import numpy as np
import shapely
from rasterio import features
from rasterio.transform import from_origin
from scipy.spatial import cKDTree
from shapely.geometry import box

from roads import config
from roads.terrain import smoothstep

SNAP = 0.01            # m: a fill point this close to a paved outline takes the paved height
FILL_STEP = 4.0        # m between the fill's inner points: the 4 m cell itself (a finer grid only adds slivers where it meets a kerb)
NEIGHBOURS = 16        # edge samples blended at each point
SAMPLE_STEP = 1.0      # m between edge samples
BLOCK = 32.0           # m: the fill's boxes are cut by the paved surface of their block of the grid


def _densify_all(polylines, step):
    """Every polyline with points added so that none are more than `step` apart (each segment cut into equal parts), one after the other."""
    if not polylines:
        return np.zeros((0, 5))
    firsts = np.array([p[0] for p in polylines])
    a = np.vstack([p[:-1] for p in polylines])
    b = np.vstack([p[1:] for p in polylines])
    seg_counts = np.array([len(p) - 1 for p in polylines])
    n = np.maximum(np.ceil(np.hypot(b[:, 0] - a[:, 0], b[:, 1] - a[:, 1]) / step).astype(int), 1)
    seg = np.repeat(np.arange(len(a)), n)
    k = np.arange(len(seg)) - np.repeat(np.cumsum(n) - n, n) + 1                # 1 .. n within each segment
    pts = a[seg] + (b[seg] - a[seg]) * (k / n[seg])[:, None]
    # each polyline: its first point, then the points of its segments
    per_line = np.add.reduceat(n, np.r_[0, np.cumsum(seg_counts)[:-1]])
    starts = np.r_[0, np.cumsum(per_line + 1)[:-1]]
    out = np.empty((len(pts) + len(polylines), polylines[0].shape[1]))
    rest = np.ones(len(out), bool)
    rest[starts] = False
    out[starts], out[rest] = firsts, pts
    return out


def blend_width(xy, y, out, ground):
    """W per edge sample: the narrowest of RIBBON_MIN .. RIBBON_REACH (0.5 m steps) where the terrain lies within BLEND_SLOPE x W / 1.5
    of the edge's height, measured along `out`; RIBBON_REACH where none does."""
    d = np.arange(config.RIBBON_MIN, config.RIBBON_REACH + 0.01, 0.5)
    p = xy[:, None, :] + out[:, None, :] * d[None, :, None]
    g = ground(p.reshape(-1, 2)).reshape(len(xy), len(d))
    ok = 1.5 * np.abs(g - y[:, None]) <= config.BLEND_SLOPE * d[None, :]
    return np.where(ok.any(axis=1), d[np.argmax(ok, axis=1)], config.RIBBON_REACH)


class EdgeField:
    """The height field around the paved edges. `edges`: [(points (n, 3) x, north, height along the edge, outward (n, 2))]."""

    def __init__(self, edges, ground, base=None):
        dense = _densify_all([np.c_[pts, o] for pts, o in edges if len(pts) >= 2], SAMPLE_STEP)
        n = dense[:, 3:5]
        out = n / np.maximum(np.hypot(n[:, 0], n[:, 1]), 1e-9)[:, None]
        self.ground = ground
        xy, y = dense[:, :2], dense[:, 2]
        width = blend_width(xy, y, out, ground) if len(xy) else np.zeros(0)
        if base is not None:                                                   # `base`'s samples first, as if built from its edges + these
            xy, y, width = np.vstack([base.xy, xy]), np.r_[base.y, y], np.r_[base.width, width]
        self.xy, self.y, self.width = xy, y, width
        self.tree = cKDTree(self.xy) if len(self.xy) else None

    def extended(self, edges):
        """The field of this one's edges and more (each sample is computed alone, so this is the field of all the edges)."""
        return EdgeField(edges, self.ground, base=self)

    def influence(self, p):
        """(heights of the near samples (m, k), their f (m, k)) at plan points p (m, 2)."""
        k = min(NEIGHBOURS, len(self.xy))
        d, i = self.tree.query(p, k=k, distance_upper_bound=config.RIBBON_REACH)
        d, i = d.reshape(len(p), k), i.reshape(len(p), k)
        real = i < len(self.xy)
        ii = np.where(real, i, 0)
        f = np.where(real, 1.0 - smoothstep(np.clip(d / self.width[ii], 0.0, 1.0)), 0.0)
        return self.y[ii], f

    def __call__(self, p, ground=None):
        """(height, f = the strongest influence, 0 where only the terrain counts) at plan points p (m, 2); `ground`: the heights the
        field blends to there instead of the terrain's (a car park's own)."""
        g = self.ground(p) if ground is None else np.asarray(ground, float)
        if self.tree is None or not len(p):
            return g, np.zeros(len(p))
        e, f = self.influence(p)
        w = f ** 4
        total = w.sum(axis=1)
        edge = np.where(total > 0, (w * e).sum(axis=1) / np.maximum(total, 1e-12), g)          # the near edges' heights, the nearest weighing most
        strength = f.max(axis=1)                                                                 # the pull of the strongest edge: 1 on it
        return g + strength * (edge - g), strength


def paved_edges(pieces, meshes, park_meshes):
    """Edges of the ground-level paved surfaces with their outward directions: both sides of every drawn road run, the kerbs of every
    junction (mouths excluded: a road continues there), the outline of every car park."""
    out = []
    for p in pieces:
        if p.bridge or p.tunnel or not p.drawn.any():
            continue
        across = p.left[:, :2] - p.right[:, :2]
        o = across / np.maximum(np.hypot(across[:, 0], across[:, 1]), 1e-6)[:, None]
        runs = np.split(np.flatnonzero(p.drawn), np.flatnonzero(np.diff(np.flatnonzero(p.drawn)) != 1) + 1)
        for run in runs:
            idx = np.r_[run, run[-1] + 1]
            out += [(p.left[idx], o[idx]), (p.right[idx], -o[idx])]
    for j, v in meshes:
        kerb = j.boundary[j.boundary[:, 2] == 0][:, :2]
        for a, b in kerb:
            d = v[b, :2] - v[a, :2]
            n = np.array([d[1], -d[0]]) / max(np.hypot(*d), 1e-9)                       # the surface lies on the left: outward is to the right
            out.append((v[[a, b]], np.vstack([n, n])))
    for v, t in park_meshes:
        e = np.sort(np.c_[t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]].reshape(-1, 2), axis=1)
        uniq, count = np.unique(e, axis=0, return_counts=True)
        for a, b in uniq[count == 1]:                                                    # outline edges: used by one triangle
            d = v[b, :2] - v[a, :2]
            n = np.array([d[1], -d[0]]) / max(np.hypot(*d), 1e-9)
            centre = v[t].reshape(-1, 3)[:, :2].mean(axis=0)
            if n @ (0.5 * (v[a, :2] + v[b, :2]) - centre) < 0:
                n = -n
            out.append((v[[a, b]], np.vstack([n, n])))
    return out


def paved_triangles(pieces, meshes, park_meshes, junction_triangles):
    """Triangles (t, 3, 3) (x, north, height) of the ground-level paved surfaces."""
    tris = []
    for p in pieces:
        if p.bridge or p.tunnel:
            continue
        for k in np.flatnonzero(p.drawn):
            l0, r0, l1, r1 = p.left[k], p.right[k], p.left[k + 1], p.right[k + 1]
            tris += [(l0, r0, r1), (l0, r1, l1)]
    for j, v in meshes:
        tris += list(v[junction_triangles(j, v)])
    for v, t in park_meshes:
        tris += list(v[t])
    return np.asarray(tris, float).reshape(-1, 3, 3)


class PavedHeight:
    """Height of the paved surfaces at plan points on their outline: a vertex within SNAP, else the triangle edge through the point
    (the lowest where several meet: the ground then never stands above a paved edge, and the higher one's skirt closes the gap)."""

    def __init__(self, tris):
        self.tris = tris
        flat = tris.reshape(-1, 3)
        self.vtree = cKDTree(flat[:, :2]) if len(flat) else None
        self.vy = flat[:, 2]
        if len(tris):                                                                    # each triangle's box, grown by what the barycentric
            lo, hi = tris[:, :, :2].min(axis=1), tris[:, :, :2].max(axis=1)                # tolerance below can reach beyond it
            grow = SNAP + 2e-3 * (hi - lo).max(axis=1)
            self.boxes = shapely.STRtree(shapely.box(lo[:, 0] - grow, lo[:, 1] - grow, hi[:, 0] + grow, hi[:, 1] + grow))

    def __call__(self, xy):
        out = np.full(len(xy), np.nan)
        if self.vtree is None or not len(xy):
            return out
        best = np.full(len(xy), np.inf)
        hits = self.vtree.query_ball_point(xy, SNAP)                                     # every surface meeting there counts, vertex or not
        count = np.fromiter((len(h) for h in hits), int, len(hits))
        if count.any():
            np.minimum.at(best, np.repeat(np.arange(len(xy)), count), self.vy[np.concatenate([h for h in hits if h])])
        i, t = self.boxes.query(shapely.points(xy))
        a, b, c = self.tris[t, 0], self.tris[t, 1], self.tris[t, 2]
        p = xy[i]
        det = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
        ok = np.abs(det) >= 1e-9
        a, b, c, p, i, det = a[ok], b[ok], c[ok], p[ok], i[ok], det[ok]
        wb = ((p[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (p[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])) / det
        wc = ((b[:, 0] - a[:, 0]) * (p[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (p[:, 0] - a[:, 0])) / det
        inside = np.minimum(np.minimum(wb, wc), 1 - wb - wc) >= -1e-3
        z = a[:, 2] + wb * (b[:, 2] - a[:, 2]) + wc * (c[:, 2] - a[:, 2])
        np.minimum.at(best, i[inside], z[inside])
        return np.where(np.isfinite(best), best, np.nan)


def cut_cells(surfaces, field, x0, z0, cell, rows, cols):
    """(rows - 1, cols - 1) bool, row 0 south: the 4 m cells the paved surfaces touch or with a corner the field reaches."""
    transform = from_origin(x0, z0 + (rows - 1) * cell, cell, cell)
    shapes = [surfaces] if surfaces.geom_type == "Polygon" else list(getattr(surfaces, "geoms", []))
    cut = features.rasterize([(g, 1) for g in shapes], out_shape=(rows - 1, cols - 1), transform=transform, dtype=np.uint8,
                             all_touched=True).astype(bool)[::-1] if shapes else np.zeros((rows - 1, cols - 1), bool)
    if field.tree is not None:
        gz, gx = np.mgrid[0:rows, 0:cols]
        corners = np.c_[x0 + gx.ravel() * cell, z0 + gz.ravel() * cell]
        near = np.zeros(len(corners), bool)
        close = field.tree.query(corners, k=1, distance_upper_bound=config.RIBBON_REACH)[0] < np.inf
        if close.any():
            _, f = field.influence(corners[close])
            near[close] = (f > 0).any(axis=1)
        near = near.reshape(rows, cols)
        cut |= near[:-1, :-1] | near[1:, :-1] | near[:-1, 1:] | near[1:, 1:]
    return cut


def fill(surfaces, cut, H, x0, z0, cell, chunk_box, field, paved_height):
    """The ground of one chunk's cut cells around the paved surfaces: (vertices (n, 3) x, north, height; their field weight f (n,);
    triangles (t, 3) of vertex indices, wound clockwise seen from above)."""
    bx0, bz0, bx1, bz1 = chunk_box
    c0, c1 = int(round((bx0 - x0) / cell)), int(round((bx1 - x0) / cell))
    r0, r1 = int(round((bz0 - z0) / cell)), int(round((bz1 - z0) / cell))
    rr, cc = np.nonzero(cut[r0:r1, c0:c1])
    if not len(rr):
        return np.zeros((0, 3)), np.zeros(0), np.zeros((0, 3), int)
    k = int(round(cell / FILL_STEP))
    sx, sz = np.meshgrid(np.arange(k), np.arange(k))
    ox = x0 + (c0 + cc[:, None]) * cell + sx.ravel()[None, :] * FILL_STEP
    oz = z0 + (r0 + rr[:, None]) * cell + sz.ravel()[None, :] * FILL_STEP
    boxes = shapely.box(ox.ravel(), oz.ravel(), ox.ravel() + FILL_STEP, oz.ravel() + FILL_STEP)
    paved = surfaces.intersection(box(bx0 - 1, bz0 - 1, bx1 + 1, bz1 + 1))
    shapely.prepare(paved)
    pieces = shapely.box(ox.ravel(), oz.ravel(), ox.ravel() + FILL_STEP, oz.ravel() + FILL_STEP, ccw=False)     # a box the paved surfaces miss stays
    covered = shapely.contains(paved, boxes)                                               # whole (wound as the difference would give it);
    touched = shapely.intersects(paved, boxes) & ~covered                                  # one wholly on a paved surface leaves nothing
    pieces[covered] = None
    # each box less the paved surface of its block only (BLOCK on the grid: what lies outside the block cannot reach the box, and
    # an overlay with the chunk's whole surface costs what the whole surface has)
    bi = np.floor((ox.ravel()[touched] - x0) / BLOCK).astype(np.int64)
    bj = np.floor((oz.ravel()[touched] - z0) / BLOCK).astype(np.int64)
    blocks, which = np.unique(np.c_[bi, bj], axis=0, return_inverse=True)
    block_paved = shapely.intersection(paved, shapely.box(x0 + blocks[:, 0] * BLOCK, z0 + blocks[:, 1] * BLOCK,
                                                          x0 + (blocks[:, 0] + 1) * BLOCK, z0 + (blocks[:, 1] + 1) * BLOCK))
    pieces[touched] = shapely.difference(boxes[touched], block_paved[which.ravel()])
    parts = shapely.get_parts(pieces)
    parts = parts[(shapely.get_type_id(parts) == shapely.GeometryType.POLYGON) & (shapely.area(parts) > 1e-9)]
    if not len(parts):
        return np.zeros((0, 3)), np.zeros(0), np.zeros((0, 3), int)
    triangles = shapely.get_parts(shapely.constrained_delaunay_triangles(parts))
    pts = shapely.get_coordinates(triangles).reshape(len(triangles), 4, 2)[:, :3]
    a, b, c = (pts[:, k].astype(np.float32) for k in range(3))                        # a triangle with two corners at one point as written (float32:
    pts = pts[~((a == b).all(1) | (b == c).all(1) | (c == a).all(1))]                 # a road edge through a fill point) covers nothing; its zero-length edge reads as open
    if not len(pts):
        return np.zeros((0, 3)), np.zeros(0), np.zeros((0, 3), int)
    xy, back = np.unique(np.ascontiguousarray(pts.reshape(-1, 2)).view(np.complex128).ravel(), return_inverse=True)     # a fill point is a corner of
    xy = np.c_[xy.real, xy.imag]                                                       # ~6 triangles: each is worked out once (as complex: sorted by x then y)
    y, f = field(xy)
    on_paved = shapely.dwithin(paved, shapely.points(xy), SNAP) if not paved.is_empty else np.zeros(len(xy), bool)
    if on_paved.any():
        py = paved_height(xy[on_paved])
        y[on_paved] = np.where(np.isnan(py), y[on_paved], py)
        f[on_paved] = 1.0
    gx, gz = (xy[:, 0] - x0) / cell, (xy[:, 1] - z0) / cell
    corner = (np.abs(gx - np.round(gx)) < 1e-6) & (np.abs(gz - np.round(gz)) < 1e-6) & (f <= 0) & ~on_paved
    y[corner] = H[np.round(gz[corner]).astype(int), np.round(gx[corner]).astype(int)]         # where the field is the terrain: the grid's own height
    # a point on an edge shared with a kept cell takes that cell's edge height (the straight line between its corners), or the two
    # would leave a hairline crack (a T-junction)
    rows, cols = cut.shape
    def kept(r, c):
        inside = (r >= 0) & (r < rows) & (c >= 0) & (c < cols)
        return inside & ~cut[np.clip(r, 0, rows - 1), np.clip(c, 0, cols - 1)]
    on_x = (np.abs(gx - np.round(gx)) < 1e-6) & ~on_paved                                     # on a vertical grid line (x constant)
    cx, rz = np.round(gx).astype(int), np.floor(gz).astype(int)
    vx = on_x & (kept(rz, cx - 1) | kept(rz, cx))
    if vx.any():
        t = gz[vx] - rz[vx]
        y[vx] = H[rz[vx], cx[vx]] * (1 - t) + H[np.minimum(rz[vx] + 1, H.shape[0] - 1), cx[vx]] * t
    on_z = (np.abs(gz - np.round(gz)) < 1e-6) & ~on_paved & ~vx                               # on a horizontal grid line (z constant)
    rz2, cx2 = np.round(gz).astype(int), np.floor(gx).astype(int)
    vz = on_z & (kept(rz2 - 1, cx2) | kept(rz2, cx2))
    if vz.any():
        t = gx[vz] - cx2[vz]
        y[vz] = H[rz2[vz], cx2[vz]] * (1 - t) + H[rz2[vz], np.minimum(cx2[vz] + 1, H.shape[1] - 1)] * t
    tri = back.reshape(-1, 3)
    a, b, c = xy[tri[:, 0]], xy[tri[:, 1]], xy[tri[:, 2]]
    ccw = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]) > 0
    tri[ccw] = tri[ccw][:, [0, 2, 1]]
    return np.c_[xy, y], f, tri
