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
FILL_STEP = 2.0        # m between the fill's inner points
NEIGHBOURS = 16        # edge samples blended at each point
SAMPLE_STEP = 1.0      # m between edge samples


def _densify(points, step):
    out = [points[:1]]
    for a, b in zip(points[:-1], points[1:]):
        n = max(int(np.ceil(np.hypot(*(b[:2] - a[:2])) / step)), 1)
        out.append(a[None, :] + (b - a)[None, :] * (np.arange(1, n + 1) / n)[:, None])
    return np.vstack(out)


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

    def __init__(self, edges, ground):
        xy, y, out = [], [], []
        for pts, o in edges:
            if len(pts) < 2:
                continue
            dense = _densify(np.c_[pts, o], SAMPLE_STEP)
            xy.append(dense[:, :2]); y.append(dense[:, 2])
            n = dense[:, 3:5]; out.append(n / np.maximum(np.hypot(n[:, 0], n[:, 1]), 1e-9)[:, None])
        self.ground = ground
        self.xy = np.vstack(xy) if xy else np.zeros((0, 2))
        self.y = np.concatenate(y) if y else np.zeros(0)
        self.width = blend_width(self.xy, self.y, np.vstack(out), ground) if xy else np.zeros(0)
        self.tree = cKDTree(self.xy) if len(self.xy) else None

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
        self.ctree = cKDTree(tris[:, :, :2].mean(axis=1)) if len(tris) else None
        self.reach = float(np.hypot(*(tris[:, :, :2] - tris[:, :, :2].mean(axis=1)[:, None, :]).transpose(2, 0, 1)).max()) if len(tris) else 0.0

    def __call__(self, xy):
        out = np.full(len(xy), np.nan)
        if self.vtree is None:
            return out
        for i, hit in enumerate(self.vtree.query_ball_point(xy, SNAP)):
            best = self.vy[hit].min() if hit else np.inf                                    # every surface meeting there counts, vertex or not
            for t in self.ctree.query_ball_point(xy[i], self.reach + SNAP):
                a, b, c = self.tris[t]
                det = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
                if abs(det) < 1e-9:
                    continue
                wb = ((xy[i, 0] - a[0]) * (c[1] - a[1]) - (xy[i, 1] - a[1]) * (c[0] - a[0])) / det
                wc = ((b[0] - a[0]) * (xy[i, 1] - a[1]) - (b[1] - a[1]) * (xy[i, 0] - a[0])) / det
                if min(wb, wc, 1 - wb - wc) >= -1e-3:
                    best = min(best, a[2] + wb * (b[2] - a[2]) + wc * (c[2] - a[2]))
            out[i] = best if np.isfinite(best) else np.nan
        return out


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
    """The ground of one chunk's cut cells around the paved surfaces: (triangles (t, 3, 3) x, north, height wound clockwise seen from
    above, field weight f (t, 3) per vertex)."""
    bx0, bz0, bx1, bz1 = chunk_box
    c0, c1 = int(round((bx0 - x0) / cell)), int(round((bx1 - x0) / cell))
    r0, r1 = int(round((bz0 - z0) / cell)), int(round((bz1 - z0) / cell))
    rr, cc = np.nonzero(cut[r0:r1, c0:c1])
    if not len(rr):
        return np.zeros((0, 3, 3)), np.zeros((0, 3))
    k = int(round(cell / FILL_STEP))
    sx, sz = np.meshgrid(np.arange(k), np.arange(k))
    ox = x0 + (c0 + cc[:, None]) * cell + sx.ravel()[None, :] * FILL_STEP
    oz = z0 + (r0 + rr[:, None]) * cell + sz.ravel()[None, :] * FILL_STEP
    boxes = shapely.box(ox.ravel(), oz.ravel(), ox.ravel() + FILL_STEP, oz.ravel() + FILL_STEP)
    paved = surfaces.intersection(box(bx0 - 1, bz0 - 1, bx1 + 1, bz1 + 1))
    pieces = shapely.difference(boxes, paved)
    tris = []
    for g in pieces:
        for part in getattr(g, "geoms", [g]):
            if part.geom_type == "Polygon" and part.area > 1e-5:
                tris += [np.array(t.exterior.coords)[:3] for t in shapely.constrained_delaunay_triangles(part).geoms]
    if not tris:
        return np.zeros((0, 3, 3)), np.zeros((0, 3))
    pts = np.array(tris)
    xy = pts.reshape(-1, 2)
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
    out = np.dstack([pts, y.reshape(-1, 3)])
    a, b, c = out[:, 0, :2], out[:, 1, :2], out[:, 2, :2]
    ccw = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]) > 0
    out[ccw] = out[ccw][:, [0, 2, 1]]
    fw = f.reshape(-1, 3)
    fw[ccw] = fw[ccw][:, [0, 2, 1]]
    return out, fw
