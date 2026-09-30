"""Shaping a terrain height grid around the road surface.

Two rules, applied to a grid of vertices (row = north index, south first; vertex (r, c) at (x0 + c * cell, z0 + r * cell)):

  blend   under the road and on its shoulder the ground sits just below the road; from there it eases back to the natural ground
          (cut or embankment) over `EMBANKMENT` metres.
  bench   no terrain triangle may stand above the road. A triangle lies below the road wherever all its corners lie below the
          road's tangent planes, so: every corner of a grid cell touched by the road is lowered to the lowest tangent plane of the
          road points that can share a cell with it. This is exact for any cell size, which is what keeps the coarse 16 m terrain under the roads too.
"""
import numpy as np
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
        if polygons:                                       # pixel centres on whole metres, row 0 = north
            transform = from_origin(x0 - 0.5, z0 + height + 0.5, 1.0, 1.0)
            mask = features.rasterize([(p, 1) for p in polygons], out_shape=(self.rows, self.cols), transform=transform, dtype=np.uint8).astype(bool)
            self.outside = distance_transform_edt(~mask[::-1]).astype(np.float32)          # row 0 = south
        else:
            self.outside = np.full((self.rows, self.cols), 1e6, np.float32)

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
    touched = features.rasterize([(p.buffer(0.5), 1) for p in footprint.polygons], out_shape=(rows - 1, cols - 1), transform=transform,
                                 dtype=np.uint8, all_touched=True).astype(bool)[::-1]
    corner = np.zeros((rows, cols), bool)
    for dr in (0, 1):
        for dc in (0, 1):
            corner[dr:rows - 1 + dr, dc:cols - 1 + dc] |= touched
    r, c = np.nonzero(corner)
    if not len(r):
        return h
    v = np.c_[x0 + cell * c, z0 + cell * r]
    pairs = cKDTree(v).sparse_distance_matrix(cKDTree(cloud["xy"]), cell * np.sqrt(2.0) + cloud["hw"].max() + 1.0, output_type="coo_matrix")
    i, q = pairs.row, pairs.col
    # a cloud point stands for the cross-section of the road through it. It concerns a corner only if the two can lie in one cell:
    # the point of the cross-section nearest to the corner is within one cell of it, east-west and north-south
    offset = v[i] - cloud["xy"][q]
    tan = cloud["tan"][q]
    lateral = offset[:, 1] * tan[:, 0] - offset[:, 0] * tan[:, 1]
    sideways = np.clip(lateral, -cloud["hw"][q], cloud["hw"][q])[:, None] * np.c_[-tan[:, 1], tan[:, 0]]
    keep = np.abs(offset - sideways).max(axis=1) <= cell + 0.5
    i, q = i[keep], q[keep]
    plane = cloud["z"][q] + (cloud["grad"][q] * (v[i] - cloud["xy"][q])).sum(axis=1)
    lowest = np.full(len(v), np.inf)
    np.minimum.at(lowest, i, plane)
    out = h.copy()
    out[r, c] = np.minimum(h[r, c], lowest - sink)
    return out
