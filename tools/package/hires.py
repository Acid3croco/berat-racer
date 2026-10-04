"""High resolution sources for the map package, fetched per tile from the IGN Géoplateforme WMS and kept under data/big/src:

  mnt05/{i}_{j}.f32.zst       LiDAR HD ground at 0.5 m: 1.6 km tiles (the mnt grid), 3200 x 3200, float32 lossless, north row first;
                              NaN where the LiDAR has no ground (filled from the 2 m tile, itself filled from RGE ALTI)
  ortho20/{si}_{sj}_{a}_{b}.jpg  BD ORTHO at 0.2 m: each sector cut in 4 x 4 tiles of 800 m (a east, b north), 4000 x 4000, the
                              server's JPEG (the WMS serves at most 4000 px a side)

Measured at the Bérat centre (400 m): the 1 m ground holds 8 cm rms (34 cm p99) the 2 m one cannot, 0.5 m another 5.7 cm (25 cm
p99): ditches, banks, kerbs. Fetch: uv run python -m package --list L fetch-hires [--no-ortho].
"""
import io
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
from scipy.ndimage import map_coordinates

import sources
from rasters import HALF, SECTOR

RASTER = 1600                     # m: the mnt tile
MNT_RES = 0.5
MNT_SIDE = int(RASTER / MNT_RES)  # 3200
ORTHO_RES = 0.2
ORTHO_SPLIT = 4                   # 800 m tiles
ORTHO_SIDE = int(SECTOR / ORTHO_SPLIT / ORTHO_RES)   # 4000
MNT_LAYER = "IGNF_LIDAR-HD_MNT_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93"
THREADS = 4                       # the WMS pool sources.py uses


NO_DATA = np.iinfo(np.int32).min
_zc = threading.local()


def mnt_path(i, j):
    return sources.SRC / "mnt05" / f"{i}_{j}.cm.zst"


def ortho_path(si, sj, a, b):
    return sources.SRC / "ortho20" / f"{si}_{sj}_{a}_{b}.jpg"


def encode(a):
    """Heights (m, NaN = no data) as whole centimetres (the LiDAR is good to ~10 cm), each row as differences, zstd: ~4 x smaller
    than float32 and within 5 mm."""
    import zstandard
    if not hasattr(_zc, "c"):                                                         # one compressor per thread: they are not
        _zc.c = zstandard.ZstdCompressor(level=12)                                     # thread-safe (a shared one crashed the fetch)
    q = np.where(np.isfinite(a), np.round(np.nan_to_num(a) * 100), NO_DATA).astype(np.int64)
    d = np.diff(q, axis=1, prepend=0).astype("<i4")                                  # int32 wraps cleanly round NO_DATA
    return _zc.c.compress(d.tobytes())


def decode(data):
    d = np.frombuffer(sources._zd.decompress(data), "<i4").reshape(MNT_SIDE, MNT_SIDE)
    q = np.cumsum(d, axis=1, dtype=np.int32)                                          # wrapping int32 sums undo the wrapped differences
    return np.where(q == NO_DATA, np.nan, q / 100.0).astype(np.float32)


def wms(layer, x0, y0, size, px, fmt):
    return dict(SERVICE="WMS", VERSION="1.3.0", REQUEST="GetMap", STYLES="", CRS="EPSG:2154", LAYERS=layer,
                BBOX=f"{x0},{y0},{x0 + size},{y0 + size}", WIDTH=px, HEIGHT=px, FORMAT=fmt)


def fetch_mnt(i, j):
    import rasterio
    x0, y0, _, _ = sources.tile_rect(i, j, RASTER)
    r = sources._get(sources.WMS, wms(MNT_LAYER, x0, y0, RASTER, MNT_SIDE, "image/geotiff"), "wms", expect="image")
    if r.headers.get("content-type", "").startswith("image/"):
        with rasterio.open(io.BytesIO(r.content)) as ds:
            a = ds.read(1).astype(np.float32)
    else:
        a = np.full((MNT_SIDE, MNT_SIDE), np.nan, np.float32)                       # no LiDAR HD there
    a[~np.isfinite(a) | (a < -100) | (a > 3000)] = np.nan
    sources._atomic(mnt_path(i, j), encode(a))


def fetch_ortho(si, sj, a, b):
    x0, y0, _, _ = sources.tile_rect(si, sj)
    step = SECTOR / ORTHO_SPLIT
    data = sources._image(sources.WMS, wms("ORTHOIMAGERY.ORTHOPHOTOS", x0 + a * step, y0 + b * step, step, ORTHO_SIDE, "image/jpeg"), "wms")
    sources._atomic(ortho_path(si, sj, a, b), data)


def mnt_tiles(sectors, reach=64.0):
    """The 0.5 m tiles a sector list needs: under each sector and `reach` metres around it."""
    out = set()
    for si, sj in sectors:
        x0, y0 = -HALF + si * SECTOR - reach, -HALF + sj * SECTOR - reach
        for i in range(int(np.floor((x0 + HALF) / RASTER)), int(np.floor((x0 + SECTOR + 2 * reach + HALF) / RASTER)) + 1):
            for j in range(int(np.floor((y0 + HALF) / RASTER)), int(np.floor((y0 + SECTOR + 2 * reach + HALF) / RASTER)) + 1):
                out.add((i, j))
    return sorted(out)


def fetch(sectors, ortho=True, log=print):
    """Fetch what is missing; returns counts. Re-running resumes."""
    jobs = [(fetch_mnt, t) for t in mnt_tiles(sectors) if not mnt_path(*t).exists()]
    if ortho:
        jobs += [(fetch_ortho, (si, sj, a, b)) for si, sj in sectors for a in range(ORTHO_SPLIT) for b in range(ORTHO_SPLIT)
                 if not ortho_path(si, sj, a, b).exists()]
    log(f"hires: {len(jobs)} tiles to fetch ({sum(1 for f, _ in jobs if f is fetch_mnt)} ground, {sum(1 for f, _ in jobs if f is fetch_ortho)} orthophoto)")
    failures, done = [], 0
    with ThreadPoolExecutor(THREADS) as ex:
        futures = {ex.submit(f, *args): (f.__name__, args) for f, args in jobs}
        for fut in as_completed(futures):
            done += 1
            try:
                fut.result()
            except Exception as e:
                failures.append((futures[fut], repr(e)[:120]))
            if done % 100 == 0 or done == len(jobs):
                log(f"hires: {done}/{len(jobs)} fetched, {len(failures)} failed")
    return dict(fetched=len(jobs) - len(failures), failures=failures)


class Ground:
    """The 0.5 m LiDAR ground over a box (Lambert-93 local metres), sampled bilinearly; where the LiDAR has no ground, `fallback`."""

    def __init__(self, x0, y0, x1, y1, fallback):
        # the pixels of the box and one more around it, north row first; pixel (r, c) is centred at (ax0 + (c + 0.5) res, ay1 - (r + 0.5) res)
        self.ax0 = -HALF + np.floor((x0 + HALF) / MNT_RES - 1) * MNT_RES
        self.ay1 = -HALF + np.ceil((y1 + HALF) / MNT_RES + 1) * MNT_RES
        cols = int(round((np.ceil((x1 + HALF) / MNT_RES + 1) * MNT_RES - HALF - self.ax0) / MNT_RES))
        rows = int(round((self.ay1 - (-HALF + np.floor((y0 + HALF) / MNT_RES - 1) * MNT_RES)) / MNT_RES))
        a = np.full((rows, cols), np.nan, np.float32)
        ay0 = self.ay1 - rows * MNT_RES
        for i in range(int(np.floor((self.ax0 + HALF) / RASTER)), int(np.floor((self.ax0 + cols * MNT_RES - 1e-6 + HALF) / RASTER)) + 1):
            for j in range(int(np.floor((ay0 + HALF) / RASTER)), int(np.floor((self.ay1 - 1e-6 + HALF) / RASTER)) + 1):
                path = mnt_path(i, j)
                if not path.exists():
                    raise FileNotFoundError(f"{path}: run `python -m package --list <list> fetch-hires` first")
                tile = decode(path.read_bytes())
                tx0, ty1 = -HALF + i * RASTER, -HALF + (j + 1) * RASTER                # the tile's west edge, north edge
                c0 = int(round((tx0 - self.ax0) / MNT_RES)); r0 = int(round((self.ay1 - ty1) / MNT_RES))
                rs, cs = max(r0, 0), max(c0, 0)
                re, ce = min(r0 + MNT_SIDE, rows), min(c0 + MNT_SIDE, cols)
                a[rs:re, cs:ce] = tile[rs - r0:re - r0, cs - c0:ce - c0]
        self.a = a
        self.fallback = fallback

    def __call__(self, x, y):
        x, y = np.asarray(x, float), np.asarray(y, float)
        v = map_coordinates(self.a, [(self.ay1 - y) / MNT_RES - 0.5, (x - self.ax0) / MNT_RES - 0.5], order=1, mode="nearest")
        bad = ~np.isfinite(v)
        if bad.any():
            v[bad] = self.fallback(x[bad], y[bad])
        return v
