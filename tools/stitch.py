"""The seam between the roads and the 4 m terrain, made watertight.

Along every road the ground is drawn by the road band: carriageways, junctions, car parks and the embankment ribbons out to their
outer edge (roads/terrain.py). The 4 m terrain cells that band touches are cut out of the grid (BM07 hole list), and the gap
between the band's outline and the remaining cells is filled with triangles whose vertices are exactly the band's outline points
(at the band's height) and the grid corners (at the terrain's height). Nothing is lowered, lifted or overlapped: the terrain keeps
its own heights everywhere it is drawn, and the band meets it without a step.

  band_polygons   plan outline of the band
  band_surface    its triangles (x, north, height), for the heights of the outline points
  cut_cells       the 4 m cells to drop
  fill            per chunk, the triangles between the band and the kept cells, wound clockwise seen from above
"""
import numpy as np
import shapely
from rasterio import features
from rasterio.transform import from_origin
from scipy.spatial import cKDTree
from shapely.geometry import Polygon, box

SNAP = 0.01            # m: an outline point this close to a band vertex takes its height


def band_polygons(footprints, ribbons, park_polygons):
    """Plan polygons of the band: road and junction footprints, car parks, and each ribbon side from its edge to its outer end."""
    out = list(footprints) + list(park_polygons)
    for _, side, edge, o, prof, segs in ribbons:
        outer = edge[:, :2] + o * prof[:, 3:4]
        for a, b in segs:
            quad = Polygon([edge[a, :2], edge[b, :2], outer[b], outer[a]])
            if quad.area > 1e-4:
                out.append(quad if quad.is_valid else quad.buffer(0))
    return shapely.union_all([p for p in out if not p.is_empty]).buffer(0)


def band_surface(pieces, meshes, ribbons, park_meshes, ribbon_triangles, junction_triangles):
    """Triangles (t, 3, 3) (x, north, height) of everything the band draws."""
    tris = []
    for p in pieces:
        if p.bridge or p.tunnel:
            continue
        for k in np.flatnonzero(p.drawn):
            l0, r0, l1, r1 = p.left[k], p.right[k], p.left[k + 1], p.right[k + 1]
            tris += [(l0, r0, r1), (l0, r1, l1)]
    for j, v in meshes:
        tris += list(v[junction_triangles(j, v)])
    for t in ribbon_triangles:
        tris += list(t)
    for v, t in park_meshes:
        tris += list(v[t])
    return np.asarray(tris, float).reshape(-1, 3, 3)


class BandHeight:
    """Height of the band's surface at plan points on or near it: a band vertex within SNAP, else the triangle holding the point (the
    highest where several do, the one drawn on top), else the nearest triangle's plane clamped to it."""

    def __init__(self, tris):
        self.tris = tris
        self.vertex_tree = cKDTree(tris.reshape(-1, 3)[:, :2]) if len(tris) else None
        self.vertex_y = tris.reshape(-1, 3)[:, 2]
        self.centre_tree = cKDTree(tris[:, :, :2].mean(axis=1)) if len(tris) else None
        self.reach = float(np.max(np.hypot(*(tris[:, :, :2] - tris[:, :, :2].mean(axis=1)[:, None, :]).transpose(2, 0, 1)))) if len(tris) else 0.0

    def __call__(self, xy):
        out = np.full(len(xy), np.nan)
        if self.vertex_tree is None:
            return out
        hits = self.vertex_tree.query_ball_point(xy, SNAP)
        for i, h in enumerate(hits):
            if h:
                out[i] = self.vertex_y[h].max()
                continue
            best, best_d = np.nan, np.inf
            for t in self.centre_tree.query_ball_point(xy[i], self.reach + SNAP):
                a, b, c = self.tris[t]
                det = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
                if abs(det) < 1e-9:
                    continue
                wb = ((xy[i, 0] - a[0]) * (c[1] - a[1]) - (xy[i, 1] - a[1]) * (c[0] - a[0])) / det
                wc = ((b[0] - a[0]) * (xy[i, 1] - a[1]) - (b[1] - a[1]) * (xy[i, 0] - a[0])) / det
                wa = 1.0 - wb - wc
                off = -min(wa, wb, wc, 0.0)                                              # 0 inside, grows outside
                y = a[2] * wa + b[2] * wb + c[2] * wc
                if off < best_d - 1e-9 or (abs(off - best_d) <= 1e-9 and y > best):
                    w = np.clip([wa, wb, wc], 0.0, None); w /= w.sum()
                    best, best_d = (y if off == 0 else a[2] * w[0] + b[2] * w[1] + c[2] * w[2]), off
            out[i] = best
        return out


def cut_cells(band, x0, z0, cell, rows, cols):
    """(rows - 1, cols - 1) bool, row 0 south: the 4 m cells the band touches."""
    transform = from_origin(x0, z0 + (rows - 1) * cell, cell, cell)
    shapes = [band] if band.geom_type == "Polygon" else list(band.geoms)
    return features.rasterize([(g, 1) for g in shapes], out_shape=(rows - 1, cols - 1), transform=transform, dtype=np.uint8,
                              all_touched=True).astype(bool)[::-1]


def fill(band, cut, H, x0, z0, cell, chunk_box, band_height):
    """Triangles (t, 3, 3) (x, north, height) filling the cut cells of one chunk around the band, wound clockwise seen from above.
    Heights: band outline points from `band_height`, grid corners from `H`."""
    bx0, bz0, bx1, bz1 = chunk_box
    c0, c1 = int(round((bx0 - x0) / cell)), int(round((bx1 - x0) / cell))
    r0, r1 = int(round((bz0 - z0) / cell)), int(round((bz1 - z0) / cell))
    rr, cc = np.nonzero(cut[r0:r1, c0:c1])
    if not len(rr):
        return np.zeros((0, 3, 3))
    cells = shapely.union_all([box(x0 + (c0 + c) * cell, z0 + (r0 + r) * cell, x0 + (c0 + c + 1) * cell, z0 + (r0 + r + 1) * cell) for r, c in zip(rr, cc)])
    gap = cells.difference(band.intersection(box(bx0 - 1, bz0 - 1, bx1 + 1, bz1 + 1)))
    parts = [g for g in getattr(gap, "geoms", [gap]) if g.geom_type == "Polygon" and g.area > 1e-4]
    if not parts:
        return np.zeros((0, 3, 3))
    tri = shapely.constrained_delaunay_triangles(shapely.MultiPolygon(parts) if len(parts) > 1 else parts[0])
    pts = np.array([np.array(t.exterior.coords)[:3] for t in tri.geoms])
    if not len(pts):
        return np.zeros((0, 3, 3))
    xy = pts.reshape(-1, 2)
    gx, gz = (xy[:, 0] - x0) / cell, (xy[:, 1] - z0) / cell
    on_grid = (np.abs(gx - np.round(gx)) < 1e-6) & (np.abs(gz - np.round(gz)) < 1e-6)
    near_band = shapely.dwithin(band, shapely.points(xy), SNAP)
    y = np.empty(len(xy))
    grid = on_grid & ~near_band
    y[grid] = H[np.round(gz[grid]).astype(int), np.round(gx[grid]).astype(int)]
    rest = ~grid
    if rest.any():
        y[rest] = band_height(xy[rest])
        bad = rest & np.isnan(y)
        if bad.any():                                                                     # neither on the band nor on the grid: the terrain under it
            from roads.terrain import _mesh_corners
            r_, c_, w_ = _mesh_corners(H.shape[0], H.shape[1], x0, z0, cell, xy[bad])
            y[bad] = (H[r_, c_] * w_).sum(axis=0)
    out = np.dstack([pts, y.reshape(-1, 3)])
    a, b, c = out[:, 0, :2], out[:, 1, :2], out[:, 2, :2]
    ccw = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]) > 0
    out[ccw] = out[ccw][:, [0, 2, 1]]
    return out
