"""Raster tiles (LiDAR ground `mnt`, canopy / surface height `mnh`, orthophoto `ortho`) assembled into one window of local coordinates.

Local coordinates: x = east, z = north, metres from the Berat centre. Tile (i, j) of size `tile_m` covers x in [-HALF + i * tile_m, +tile_m).
The original 10 x 10 sector block lives in float32 .npy files, every other sector in the compressed files of data/big/hg.
"""
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import distance_transform_edt, map_coordinates, median_filter

HALF = 16000
SECTOR = 3200
NS = 2 * HALF // SECTOR           # 10 sectors per side of the original block (stored as .npy tiles)
SPIKE = 100.0                     # m: a 2 m ground pixel this far from its 5 x 5 median is a data error
MIN_GROUND = -20.0                # m: nothing in the region lies below this, lower samples are data errors
BIG = Path("data/big")

TILE_M = {"mnt": 1600, "mnh": 1600, "ortho": 3200}
RES = {"mnt": 2, "mnh": 2, "ortho": 4}


def read_tile(kind, i, j, tile_m):
    """One raster tile (i, j) of `kind`, north row first, or None if the file is missing."""
    si, sj = i * tile_m // SECTOR, j * tile_m // SECTOR                               # sector owning the tile
    if 0 <= si < NS and 0 <= sj < NS:
        f = BIG / f"{kind}_{i}_{j}.npy"
        return np.load(f) if f.exists() else None
    if kind == "ortho":
        f = BIG / "hg" / f"ortho_{i}_{j}.jpg"
        return np.asarray(Image.open(f).convert("RGB")) if f.exists() else None
    f = BIG / "hg" / f"{kind}_{i}_{j}.npz"
    if not f.exists():
        return None
    a = np.load(f)["a"]
    if kind == "mnt":
        return np.where(a == 65535, np.nan, a / 20.0 - 100).astype(np.float32)
    return (a / 10.0).astype(np.float32)


class Mosaic:
    """Rasters over the rectangle x in [x0, x0 + width), z in [z0, z0 + height): mnt / mnh at 2 m, ortho at 4 m. Row 0 is the north edge.

    `x0`, `z0`, `width`, `height` must be multiples of 4 m. Missing ground is filled from the nearest valid pixel and spikes are removed.
    """

    def __init__(self, x0, z0, width, height, kinds=("mnt", "mnh", "ortho")):
        self.x0, self.z0, self.width, self.height = int(x0), int(z0), int(width), int(height)
        self.z1 = self.z0 + self.height
        for kind in kinds:
            setattr(self, kind, self._paste(kind))
        if "mnt" in kinds:
            self._clean_ground()

    def _paste(self, kind):
        tile_m, res = TILE_M[kind], RES[kind]
        rows, cols = self.height // res, self.width // res
        if kind == "ortho":
            out = np.zeros((rows, cols, 3), np.uint8)
        else:
            out = np.full((rows, cols), np.nan if kind == "mnt" else 0.0, np.float32)
        px = tile_m // res
        for j in range((self.z0 + HALF) // tile_m, (self.z1 + HALF - 1) // tile_m + 1):
            for i in range((self.x0 + HALF) // tile_m, (self.x0 + self.width + HALF - 1) // tile_m + 1):
                a = read_tile(kind, i, j, tile_m)
                if a is None:
                    continue
                tx0, tz1 = -HALF + i * tile_m, -HALF + (j + 1) * tile_m                    # tile north-west corner
                c0, r0 = (tx0 - self.x0) // res, (self.z1 - tz1) // res                    # position of the tile in the window (may be negative)
                sc0, sr0 = max(-c0, 0), max(-r0, 0)
                dc0, dr0 = max(c0, 0), max(r0, 0)
                w, h = min(px - sc0, cols - dc0), min(px - sr0, rows - dr0)
                if w > 0 and h > 0:
                    out[dr0:dr0 + h, dc0:dc0 + w] = a[sr0:sr0 + h, sc0:sc0 + w]
        return out

    def _clean_ground(self):
        self.mnt[self.mnt < MIN_GROUND] = np.nan                       # LiDAR / RGE ALTI garbage (a few pits of -20..-100 m in some Pyrenees tiles)
        bad = ~np.isfinite(self.mnt)
        if bad.all():
            self.mnt[:] = 0.0
        elif bad.any():
            iy, ix = distance_transform_edt(bad, return_distances=False, return_indices=True)
            self.mnt = self.mnt[iy, ix]
        med = median_filter(self.mnt, size=5, mode="nearest")            # single-pixel spikes / pits of hundreds of metres (bad LiDAR returns)
        spike = np.abs(self.mnt - med) > SPIKE
        if spike.any():
            self.mnt[spike] = med[spike]

    def sample(self, arr, x, z, res):
        """Bilinear sample of `arr` (pixel size `res`) at local coordinates."""
        return map_coordinates(arr, [(self.z1 - np.asarray(z)) / res - 0.5, (np.asarray(x) - self.x0) / res - 0.5], order=1, mode="nearest")
