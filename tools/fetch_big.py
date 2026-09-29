"""Resumable tiled download of the 32 x 32 km (~1000 km2) area around Berat.

  MNT   LiDAR HD bare-earth model, 2 m/px   20 x 20 tiles of 1.6 km  -> data/big/mnt_{i}_{j}.npy   (float32, north-up)
  MNH   LiDAR HD canopy / structure height  20 x 20 tiles of 1.6 km  -> data/big/mnh_{i}_{j}.npy
  ORTHO IGN orthophoto, 4 m/px              10 x 10 tiles of 3.2 km  -> data/big/ortho_{i}_{j}.npy (uint8 RGB)

Tile (i, j): i counts east from the west edge, j counts north from the south edge. Existing files are skipped, so re-running resumes.
"""
import io, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import numpy as np, rasterio, requests
from PIL import Image
from fetch import CX, CY

HALF = 16000
X0, Y0 = CX - HALF, CY - HALF
WMS = "https://data.geopf.fr/wms-r/wms"
OUT = Path("data/big"); OUT.mkdir(parents=True, exist_ok=True)
LAYERS = {"mnt": "IGNF_LIDAR-HD_MNT_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93", "mnh": "IGNF_LIDAR-HD_MNH_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93"}

def get(params, tries=6):
    for k in range(tries):
        try:
            r = requests.get(WMS, params=params, timeout=(10, 75))
            if r.ok and r.headers.get("content-type", "").startswith("image/"): return r.content
            err = f"HTTP {r.status_code} {r.text[:80]!r}"
        except Exception as e:
            err = repr(e)[:100]
        time.sleep(2 + 3 * k)
    raise RuntimeError(err)

def job(kind, i, j):
    f = OUT / f"{kind}_{i}_{j}.npy"
    if f.exists(): return kind, i, j, "cached"
    size = 3200 if kind == "ortho" else 1600
    x, y = X0 + i * size, Y0 + j * size
    common = dict(SERVICE="WMS", VERSION="1.3.0", REQUEST="GetMap", STYLES="", CRS="EPSG:2154", BBOX=f"{x},{y},{x+size},{y+size}", WIDTH=800, HEIGHT=800)
    if kind == "ortho":
        img = np.asarray(Image.open(io.BytesIO(get(dict(common, LAYERS="ORTHOIMAGERY.ORTHOPHOTOS", FORMAT="image/jpeg")))).convert("RGB"))
    else:
        with rasterio.open(io.BytesIO(get(dict(common, LAYERS=LAYERS[kind], FORMAT="image/geotiff")))) as ds: img = ds.read(1).astype(np.float32)
        if kind == "mnt":
            bad = ~np.isfinite(img) | (img < -100) | (img > 3000)
            if bad.any(): img[bad] = np.nan                                       # filled later from neighbours
        else: img = np.where(np.isfinite(img) & (img > -50) & (img < 200), img, 0.0).astype(np.float32)
    np.save(f.with_suffix(".tmp.npy"), img); f.with_suffix(".tmp.npy").rename(f)
    return kind, i, j, "ok"

if __name__ == "__main__":
    jobs = [("mnt", i, j) for j in range(20) for i in range(20)] + [("ortho", i, j) for j in range(10) for i in range(10)] + [("mnh", i, j) for j in range(20) for i in range(20)]
    t0, done, fail = time.time(), 0, []
    print(f"{len(jobs)} tiles", flush=True)
    with ThreadPoolExecutor(4) as ex:
        futs = {ex.submit(job, *j): j for j in jobs}
        for f in as_completed(futs):
            try: f.result()
            except Exception as e: fail.append((futs[f], str(e)))
            done += 1
            if done % 25 == 0 or done == len(jobs): print(f"{done}/{len(jobs)}  {time.time()-t0:.0f}s  failures {len(fail)}", flush=True)
    print("DONE", "failures:", fail, flush=True)
