"""The terrain of a sector, carved under the roads: what the engine's landscape is made from. Vertex spacing `cell`: 2 m from the
2 m LiDAR tiles, 1 m or 0.5 m from the 0.5 m ones (package/hires.py).

  ground    the LiDAR HD ground sampled at the vertices, plus what the world build does to its 4 m grid (water beds scooped below
            the level, stream beds cut, the ground dropped under bridge decks), carried over as a difference
  roads     under every ground-level paved surface (carriageways, junctions, car parks) the ground is the surface ROAD_SINK below
            it; around them the edge field of stitch.py blends from the paved edges back to the ground, as the 4 m world does
  shoulder  a vertex of a cell the paved surface touches, outside it, takes the nearest paved edge's height (less ROAD_SINK): the
            cells along a road edge neither lift the ground over it (a cutting) nor drop it away (an embankment)
  cap       wherever the bilinear terrain still stands within EDGE_CLEARANCE of a paved edge (two roads at different levels closer
            than a cell: a retaining wall), the corners of that cell are lowered by the excess
  holes     vertices of cells cut away over the tunnel portals (the 4 m world's tunnel holes)

The work is done on the sector and APRON metres around it, so both sectors of a border see the same roads and agree on its
vertices exactly. Rows south first here (flipped to north-up when written).
"""
import numpy as np
import shapely
from rasterio.features import rasterize
from rasterio.transform import from_origin
from scipy.ndimage import map_coordinates

import build_world
import stitch
from roads import config as road_config

from . import hires

CELLS = (2.0, 1.0, 0.5)
REACH = 64.0                                     # m of ground computed around the sector, for the field near its border
APRON = 32.0                                     # m of the work grid past each side of the sector
CAP_ROUNDS = 12
EDGE_CLEARANCE = 0.02                            # m: the terrain stays at least this far under every paved edge
BLOCK = 400_000                                  # vertices evaluated at a time (the edge field holds 16 neighbours each)


def samples(cell):
    return int(round(build_world.SECTOR / cell)) + 1


def grid(origin, size, cell):
    """Plan coordinates (n, n) of a vertex grid from `origin`, `size` metres wide."""
    n = int(round(size / cell)) + 1
    v = np.arange(n) * cell
    return np.meshgrid(origin[0] + v, origin[1] + v)


def touched_vertices(tris, origin, n, cell):
    """(n, n) row 0 south: vertices of the cells of an n x n vertex grid from `origin` that any paved triangle (t, 3, 3) touches."""
    cells = np.zeros((n - 1, n - 1), bool)
    if len(tris):
        transform = from_origin(origin[0], origin[1] + (n - 1) * cell, cell, cell)
        shapes = ((shapely.Polygon(t[:, :2]), 1) for t in tris)
        cells = rasterize(shapes, out_shape=cells.shape, transform=transform, dtype=np.uint8, all_touched=True)[::-1].astype(bool)
    out = np.zeros((n, n), bool)
    out[:-1, :-1] |= cells; out[1:, :-1] |= cells; out[:-1, 1:] |= cells; out[1:, 1:] |= cells
    return out


def cap_under_edges(h, origin, cell, xy, ceiling):
    """Lower the terrain (n x n grid from `origin`) wherever it stands over a paved edge sample's `ceiling`: the corners of its cell
    that stand above the ceiling, by the excess but never below the ceiling, round after round (a corner shared with a
    neighbouring cell may lift that one's edge again). Where two roads at different levels share a cell (a retaining wall) the
    terrain follows the lower one and the upper road's skirt reaches down to it. In place; returns how many vertices were lowered."""
    n = h.shape[0]
    fx, fy = (xy[:, 0] - origin[0]) / cell, (xy[:, 1] - origin[1]) / cell
    c, r = np.floor(fx).astype(int), np.floor(fy).astype(int)
    ok = (c >= 0) & (c < n - 1) & (r >= 0) & (r < n - 1)
    c, r, fx, fy, ceiling = c[ok], r[ok], fx[ok] - c[ok], fy[ok] - r[ok], ceiling[ok]
    flat = h.ravel()
    before = flat.copy()
    for round_ in range(CAP_ROUNDS + 1):
        at = lambda dr, dc: flat[(r + dr) * n + c + dc]
        t = at(0, 0) * (1 - fx) * (1 - fy) + at(0, 1) * fx * (1 - fy) + at(1, 0) * (1 - fx) * fy + at(1, 1) * fx * fy
        over = t > ceiling + 1e-4
        if not over.any():
            break
        excess, cap = t[over] - ceiling[over], ceiling[over]
        if round_ == CAP_ROUNDS:                                                      # the last round: straight to the ceiling,
            excess = np.full(len(cap), np.inf)                                        # so the terrain is under every edge for sure
        for dr in (0, 1):
            for dc in (0, 1):                                                         # a corner above the edge comes down by the
                idx = (r[over] + dr) * n + c[over] + dc                               # excess, never below the edge: one under it
                z = flat[idx]                                                         # stays (a cascade between two roads at different
                np.minimum.at(flat, idx, np.where(z > cap, np.maximum(z - excess, cap), z))   # levels would dig a pit otherwise)
    return int((flat < before - 1e-6).sum())


def edge_samples(pieces, meshes, park_meshes, step):
    """Points (x, y, z) along every ground-level paved edge, at most `step` apart."""
    lines = [pts for pts, _ in stitch.paved_edges(pieces, meshes, park_meshes) if len(pts) >= 2]
    return stitch._densify_all(lines, step) if lines else np.zeros((0, 3))


def ground_sampler(w, cell):
    """The ground (x, y) -> z the terrain starts from: the 2 m tiles for a 2 m cell; finer, the 0.5 m tiles (the 2 m where they have
    no data)."""
    win = w.win
    coarse = lambda x, y: win.sample(win.mnt, x, y, 2)
    if cell >= 2.0:
        return coarse
    return hires.Ground(w.ox - REACH, w.oz - REACH, w.ox + build_world.SECTOR + REACH, w.oz + build_world.SECTOR + REACH, coarse)


def carved(w, cell=2.0):
    """(heights (n, n) row 0 south, hole mask (n, n), stats) of the sector computed in `w` (a SectorWorld), n = samples(cell)."""
    assert cell in CELLS, f"cell {cell}: one of {CELLS}"
    n = samples(cell)
    win = w.win
    sample = ground_sampler(w, cell)
    # ground over the sector and REACH around it
    x0, y0 = w.ox - REACH, w.oz - REACH
    gx, gy = grid((x0, y0), build_world.SECTOR + 2 * REACH, cell)
    size = gx.shape[0]
    shaped = map_coordinates(w.H - w.h0, [(gy.ravel() - win.z0) / build_world.CELL, (gx.ravel() - win.x0) / build_world.CELL],
                             order=1, mode="nearest").reshape(size, size)
    ground = sample(gx.ravel(), gy.ravel()).reshape(size, size) + np.minimum(shaped, 0.0)    # the world build only ever lowers it
    lowered = round(float(-np.minimum(shaped, 0).min()), 2)
    del gx, gy, shaped, sample

    def ground_at(xy):
        return map_coordinates(ground, [(xy[:, 1] - y0) / cell, (xy[:, 0] - x0) / cell], order=1, mode="nearest")

    field = stitch.EdgeField(stitch.paved_edges(w.pieces, w.meshes, []), ground_at)
    if w.park_meshes:
        field = field.extended(stitch.paved_edges([], [], w.park_meshes))
    tris = stitch.paved_triangles(w.pieces, w.meshes, w.park_meshes, build_world.road_surface.junction_triangles)
    paved = stitch.PavedHeight(tris)

    # the work grid: the sector and APRON around it
    apron = int(round(APRON / cell))
    k0 = int(round(REACH / cell)) - apron
    m = n + 2 * apron
    origin = (w.ox - apron * cell, w.oz - apron * cell)
    flat = ground[k0:k0 + m, k0:k0 + m].copy().ravel()
    wx, wy = grid(origin, (m - 1) * cell, cell)
    pts = np.c_[wx.ravel(), wy.ravel()]
    del wx, wy
    n_paved = n_shoulder = n_capped = n_near = 0
    if field.tree is not None:
        inside = np.zeros(len(pts), bool)
        for start in range(0, len(pts), BLOCK):                                     # the edge field, block by block
            p = pts[start:start + BLOCK]
            near = np.isfinite(field.tree.query(p, k=1, distance_upper_bound=road_config.RIBBON_REACH)[0])
            if not near.any():
                continue
            idx = np.flatnonzero(near) + start
            n_near += len(idx)
            flat[idx] = field(pts[idx])[0]
            pz = paved(pts[idx])
            on = np.isfinite(pz)
            flat[idx[on]] = pz[on] - road_config.ROAD_SINK
            inside[idx[on]] = True
        n_paved = int(inside.sum())
        shoulder = np.flatnonzero(touched_vertices(tris, origin, m, cell).ravel() & ~inside)
        if len(shoulder):
            d, i = field.tree.query(pts[shoulder], k=1, distance_upper_bound=cell * np.sqrt(2.0) + 0.1)
            ok = np.isfinite(d)
            flat[shoulder[ok]] = field.y[i[ok]] - road_config.ROAD_SINK
            n_shoulder = int(ok.sum())
        e = edge_samples(w.pieces, w.meshes, w.park_meshes, min(stitch.SAMPLE_STEP, cell / 2))
        n_capped = cap_under_edges(flat.reshape(m, m), origin, cell, e[:, :2], e[:, 2] - EDGE_CLEARANCE)
    h = flat.reshape(m, m)[apron:apron + n, apron:apron + n].copy()

    # tunnel portals: each 4 m hole cell covers (4 / cell)^2 cells; a vertex is a hole if a cell around it is
    r0 = int(round((w.oz - win.z0) / build_world.CELL)); c0 = int(round((w.ox - win.x0) / build_world.CELL))
    span = int(build_world.SECTOR / build_world.CELL)
    k = int(round(build_world.CELL / cell))
    cells = np.kron(w.tunnel[r0:r0 + span, c0:c0 + span], np.ones((k, k), bool))
    holes = np.zeros((n, n), bool)
    holes[:-1, :-1] |= cells; holes[1:, :-1] |= cells; holes[:-1, 1:] |= cells; holes[1:, 1:] |= cells

    stats = dict(cell=cell, paved_vertices=n_paved, field_vertices=n_near, shoulder_vertices=n_shoulder, capped_vertices=n_capped,
                 hole_vertices=int(holes.sum()), lowered_by_water_m=lowered)
    return h, holes, stats


def surface_gap(h, origin, cell, pieces, meshes):
    """How the carved terrain meets the drawn road surface, over the edge vertices of the ground-level carriageways and the junction
    vertices inside the sector (the terrain read as the engine does, bilinear between vertices): terrain above the road (max, count
    over 2 cm), terrain below it (p99, max), in metres."""
    pts = []
    for p in pieces:
        if p.bridge or p.tunnel or not p.drawn.any():
            continue
        k = np.flatnonzero(p.drawn)
        idx = np.unique(np.r_[k, k + 1])
        pts += [p.left[idx], p.right[idx]]                                           # the drawn surface is made of the edges
    for j, v in meshes:
        pts.append(v)
    if not pts:
        return dict(points=0)
    pts = np.vstack(pts)
    x0, y0 = origin
    inside = (pts[:, 0] >= x0) & (pts[:, 0] < x0 + build_world.SECTOR) & (pts[:, 1] >= y0) & (pts[:, 1] < y0 + build_world.SECTOR)
    pts = pts[inside]
    if not len(pts):
        return dict(points=0)
    t = map_coordinates(h, [(pts[:, 1] - y0) / cell, (pts[:, 0] - x0) / cell], order=1, mode="nearest")
    d = t - pts[:, 2]
    return dict(points=int(len(pts)), terrain_above_road_max=round(float(d.max()), 3),
                terrain_above_road_over_2cm=int((d > 0.02).sum()),
                terrain_below_road_p99=round(float(-np.percentile(d, 1)), 3), terrain_below_road_max=round(float(-d.min()), 3))
