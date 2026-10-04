"""The terrain of a sector at 2 m, carved under the roads: what the engine's landscape is made from.

  ground   the LiDAR HD ground (mnt, 2 m) sampled at the vertices, plus what the world build does to its 4 m grid (water beds
           scooped below the level, stream beds cut, the ground dropped under bridge decks), carried over as a difference
  roads    under every ground-level paved surface (carriageways, junctions, car parks) the ground is the surface ROAD_SINK below it;
           around them the edge field of stitch.py blends from the paved edges back to the ground, exactly as the 4 m world does
  holes    vertices of cells cut away over the tunnel portals (the 4 m world's tunnel holes, at 2 m)

Vertices: (SAMPLES x SAMPLES) on the sector's square, 2 m apart, row 0 = south here (flipped to north-up when written).
"""
import numpy as np
import shapely
from rasterio.features import rasterize
from rasterio.transform import from_origin
from scipy.ndimage import map_coordinates

import build_world
import stitch
from roads import config as road_config

CELL = 2.0
SAMPLES = int(build_world.SECTOR / CELL) + 1        # 1601
REACH = 64.0                                        # m of ground computed around the sector, for the field near its border


SHOULDER_REACH = CELL * np.sqrt(2.0) + 0.1      # m: the paved edges a shoulder vertex follows
CAP_ROUNDS = 8
EDGE_CLEARANCE = 0.02                            # m: the terrain stays at least this far under every paved edge
APRON = 16                                       # cells worked out past each side of the sector, so both sectors of a border see the same
                                                 # roads around it and agree on its vertices (shoulders, caps reach a few cells)


def touched_vertices(tris, origin, n):
    """(n, n) row 0 south: vertices of the 2 m cells of an n x n vertex grid from `origin` that any paved triangle (t, 3, 3) touches."""
    cells = np.zeros((n - 1, n - 1), bool)
    if len(tris):
        transform = from_origin(origin[0], origin[1] + (n - 1) * CELL, CELL, CELL)
        shapes = ((shapely.Polygon(t[:, :2]), 1) for t in tris)
        cells = rasterize(shapes, out_shape=cells.shape, transform=transform, dtype=np.uint8, all_touched=True)[::-1].astype(bool)
    out = np.zeros((n, n), bool)
    out[:-1, :-1] |= cells; out[1:, :-1] |= cells; out[:-1, 1:] |= cells; out[1:, 1:] |= cells
    return out


def cap_under_edges(h, origin, xy, ceiling):
    """Lower the terrain (n x n grid from `origin`) wherever it stands over a paved edge sample's `ceiling`: the four corners of its
    cell by the excess, round after round (a corner shared with a neighbouring cell may lift that one's edge again). The engine's
    bilinear terrain then never rises over a road edge, even between two roads at different levels closer than a cell (a retaining
    wall; the road's skirt shows there). In place; returns how many vertices were lowered."""
    n = h.shape[0]
    fx, fy = (xy[:, 0] - origin[0]) / CELL, (xy[:, 1] - origin[1]) / CELL
    c, r = np.floor(fx).astype(int), np.floor(fy).astype(int)
    ok = (c >= 0) & (c < n - 1) & (r >= 0) & (r < n - 1)
    c, r, fx, fy, ceiling = c[ok], r[ok], fx[ok] - c[ok], fy[ok] - r[ok], ceiling[ok]
    flat = h.ravel()
    before = flat.copy()
    for _ in range(CAP_ROUNDS):
        at = lambda dr, dc: flat[(r + dr) * n + c + dc]
        t = at(0, 0) * (1 - fx) * (1 - fy) + at(0, 1) * fx * (1 - fy) + at(1, 0) * (1 - fx) * fy + at(1, 1) * fx * fy
        over = t > ceiling + 1e-4
        if not over.any():
            break
        excess = t[over] - ceiling[over]                                              # lowering all four corners by it lowers the
        for dr in (0, 1):                                                             # bilinear terrain there by exactly that much
            for dc in (0, 1):
                idx = (r[over] + dr) * n + c[over] + dc
                np.minimum.at(flat, idx, flat[idx] - excess)
    return int((flat < before - 1e-6).sum())


def grid(origin, size, cell):
    """Plan coordinates (n, n) of a vertex grid from `origin`, `size` metres wide."""
    n = int(round(size / cell)) + 1
    v = np.arange(n) * cell
    return np.meshgrid(origin[0] + v, origin[1] + v)


def carved(w):
    """(heights (SAMPLES, SAMPLES) row 0 south, hole mask (SAMPLES, SAMPLES), stats) of the sector computed in `w` (a SectorWorld)."""
    win = w.win
    # ground at 2 m over the sector and REACH around it
    x0, y0 = w.ox - REACH, w.oz - REACH
    gx, gy = grid((x0, y0), build_world.SECTOR + 2 * REACH, CELL)
    n = gx.shape[0]
    mnt = win.sample(win.mnt, gx.ravel(), gy.ravel(), 2).reshape(n, n)
    shaped = map_coordinates(w.H - w.h0, [(gy.ravel() - win.z0) / build_world.CELL, (gx.ravel() - win.x0) / build_world.CELL],
                             order=1, mode="nearest").reshape(n, n)
    ground = mnt + np.minimum(shaped, 0.0)                                            # the world build only ever lowers the ground

    def ground_at(xy):
        return map_coordinates(ground, [(xy[:, 1] - y0) / CELL, (xy[:, 0] - x0) / CELL], order=1, mode="nearest")

    field = stitch.EdgeField(stitch.paved_edges(w.pieces, w.meshes, []), ground_at)
    if w.park_meshes:
        field = field.extended(stitch.paved_edges([], [], w.park_meshes))
    tris = stitch.paved_triangles(w.pieces, w.meshes, w.park_meshes, build_world.road_surface.junction_triangles)
    paved = stitch.PavedHeight(tris)

    # the work grid: the sector and APRON cells around it
    k0 = int(round(REACH / CELL)) - APRON
    m = SAMPLES + 2 * APRON
    work = (slice(k0, k0 + m), slice(k0, k0 + m))
    origin = (w.ox - APRON * CELL, w.oz - APRON * CELL)
    h = ground[work].copy()
    pts = np.c_[gx[work].ravel(), gy[work].ravel()]
    near = np.zeros(len(pts), bool)
    if field.tree is not None:
        near = np.isfinite(field.tree.query(pts, k=1, distance_upper_bound=road_config.RIBBON_REACH)[0])
    flat = h.ravel()
    n_paved = n_shoulder = n_capped = 0
    if near.any():
        z, _ = field(pts[near])
        flat[near] = z
        pz = paved(pts[near])
        on = np.isfinite(pz)
        idx = np.flatnonzero(near)[on]
        flat[idx] = pz[on] - road_config.ROAD_SINK
        n_paved = len(idx)
        # the shoulder: a vertex of a cell the paved surface touches, outside it, takes the nearest paved edge's height (less
        # ROAD_SINK), so the cells along a road edge neither lift the ground over it (a cutting) nor drop it away (an embankment)
        touched = touched_vertices(tris, origin, m)
        inside = np.zeros(len(pts), bool); inside[idx] = True
        shoulder = np.flatnonzero(touched.ravel() & ~inside)
        if len(shoulder):
            d, i = field.tree.query(pts[shoulder], k=1, distance_upper_bound=SHOULDER_REACH)
            ok = np.isfinite(d)
            flat[shoulder[ok]] = field.y[i[ok]] - road_config.ROAD_SINK
            n_shoulder = int(ok.sum())
        n_capped = cap_under_edges(flat.reshape(m, m), origin, field.xy, field.y - EDGE_CLEARANCE)
    h = flat.reshape(m, m)[APRON:APRON + SAMPLES, APRON:APRON + SAMPLES].copy()

    # tunnel portals: the 4 m hole cells, each a 2 x 2 block of 2 m cells; a vertex is a hole if a cell around it is
    r0 = int(round((w.oz - win.z0) / build_world.CELL)); c0 = int(round((w.ox - win.x0) / build_world.CELL))
    span = (SAMPLES - 1) // 2
    cells = np.kron(w.tunnel[r0:r0 + span, c0:c0 + span], np.ones((2, 2), bool))
    holes = np.zeros((SAMPLES, SAMPLES), bool)
    holes[:-1, :-1] |= cells; holes[1:, :-1] |= cells; holes[:-1, 1:] |= cells; holes[1:, 1:] |= cells

    stats = dict(paved_vertices=n_paved, field_vertices=int(near.sum()), shoulder_vertices=n_shoulder, capped_vertices=n_capped,
                 hole_vertices=int(holes.sum()), lowered_by_water_m=round(float(-np.minimum(shaped, 0).min()), 2))
    return h, holes, stats


def surface_gap(h, origin, pieces, meshes):
    """How the carved terrain meets the road surface: (terrain above the road: max, terrain below: p99, max) in metres, over the
    vertices of the ground-level paved surfaces inside the sector (the terrain read as the engine does, bilinear between vertices)."""
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
    t = map_coordinates(h, [(pts[:, 1] - y0) / CELL, (pts[:, 0] - x0) / CELL], order=1, mode="nearest")
    d = t - pts[:, 2]
    return dict(points=int(len(pts)), terrain_above_road_max=round(float(d.max()), 3),
                terrain_above_road_over_2cm=int((d > 0.02).sum()),
                terrain_below_road_p99=round(float(-np.percentile(d, 1)), 3), terrain_below_road_max=round(float(-d.min()), 3))
