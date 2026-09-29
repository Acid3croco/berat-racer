"""Resumable download of the NEW sectors of the Haute-Garonne extension (sector list: data/big/hg_sectors.json, see hg_sectors.py).

Per new sector (si, sj) (grid of fetch_vectors.py, indices may be negative), written under data/big/hg/ :
  mnt_{i}_{j}.npz   1.6 km tile, 800 x 800 px (2 m), key "a" uint16 = round((z + 100) * 20)  (5 cm steps, z in -100..1200 m), 65535 = NaN.
  mnh_{i}_{j}.npz   1.6 km tile, key "a" uint16 = round(clip(h, 0, 60) * 10) (0.1 m). Missing LiDAR HD -> 0.
  ortho_{si}_{sj}.jpg  3.2 km, 800 x 800 px (4 m), the server JPEG as received.
  tile (i, j) = floor((x - X0) / 1600) / floor((y - Y0) / 1600) with X0, Y0 of the existing grid, i east, j north; tiles 2si..2si+1, 2sj..2sj+1 belong to sector (si, sj).
  coverage.jsonl    one JSON line per finished tile (append-only): kind, i, j, status, nan_frac, fallback_frac.
Vectors: data/big/vec/{layer}_{si}_{sj}.json.gz (gzip level 6; older ones .json, read with vec_io.read_vec) (as fetch_vectors.py) and data/big/vec/osm_poi_hg.json.
Usage: uv run python fetch_hg.py [data/big/hg_sectors{_09_12}.json]   (default: Haute-Garonne; other departements: run hg_sectors.py 09 12 first).
Existing files are skipped: re-running resumes. Stops if free disk < 15 GB.
"""
import gzip, io, json, os, shutil, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import numpy as np, rasterio, requests
from fetch import CX, CY
from vec_io import exists_vec
from fetch_vectors import LAYERS as VLAYERS, wfs, SECTOR

X0, Y0 = CX - 16000, CY - 16000
WMS = "https://data.geopf.fr/wms-r/wms"
OUT = Path("data/big/hg"); OUT.mkdir(parents=True, exist_ok=True)
VEC = Path("data/big/vec"); VEC.mkdir(parents=True, exist_ok=True)
LID = {"mnt": "IGNF_LIDAR-HD_MNT_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93", "mnh": "IGNF_LIDAR-HD_MNH_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93"}
RGE = "ELEVATION.ELEVATIONGRIDCOVERAGE.HIGHRES"
MIN_FREE = int(float(os.environ.get("HG_MIN_FREE_GB", 13)) * 2**30)                                                     # hard floor; the region driver gates whole departements at 14 GB
COV = "coverage_region.jsonl"                                                # region runs must not touch the old coverage.jsonl
lock, stop = threading.Lock(), threading.Event()

def get(params, tries=6):
    for k in range(tries):
        try:
            r = requests.get(WMS, params=params, timeout=(10, 90))
            if r.ok and r.headers.get("content-type", "").startswith("image/"): return r.content
            err = f"HTTP {r.status_code} {r.text[:80]!r}"
            if r.status_code == 400 and "ServiceException" in r.text and "Layer" not in r.text[:400]: pass
        except Exception as e:
            err = repr(e)[:100]
        time.sleep(2 + 4 * k)
    raise RuntimeError(err)

def params(layer, x, y, size, fmt):
    return dict(SERVICE="WMS", VERSION="1.3.0", REQUEST="GetMap", STYLES="", CRS="EPSG:2154", LAYERS=layer,
                BBOX=f"{x},{y},{x+size},{y+size}", WIDTH=800, HEIGHT=800, FORMAT=fmt)

def tif(layer, x, y):
    with rasterio.open(io.BytesIO(get(params(layer, x, y, 1600, "image/geotiff")))) as ds: return ds.read(1).astype(np.float32)

def save_npz(f, a):
    tmp = f.with_name(f.stem + ".tmp.npz"); np.savez_compressed(tmp, a=a); tmp.rename(f)

def log_cov(rec):
    with lock, open(OUT / COV, "a") as fh: fh.write(json.dumps(rec) + "\n")

def raster(kind, i, j):
    if kind == "ortho":
        f = OUT / f"ortho_{i}_{j}.jpg"
        if f.exists(): return
        data = get(params("ORTHOIMAGERY.ORTHOPHOTOS", X0 + i * 3200, Y0 + j * 3200, 3200, "image/jpeg"))
        f.with_suffix(".tmp").write_bytes(data); f.with_suffix(".tmp").rename(f)
        log_cov(dict(kind=kind, i=i, j=j, status="ok")); return
    f = OUT / f"{kind}_{i}_{j}.npz"
    if f.exists(): return
    x, y = X0 + i * 1600, Y0 + j * 1600
    try: a = tif(LID[kind], x, y)
    except RuntimeError as e:
        if "HTTP 400" not in str(e) and "HTTP 404" not in str(e): raise
        a = np.full((800, 800), -9999, np.float32)                        # layer refuses the tile: treat as no LiDAR
    if kind == "mnh":
        bad = ~np.isfinite(a) | (a < -50) | (a > 200)
        a = np.where(bad, 0, np.clip(a, 0, 60))
        save_npz(f, np.round(a * 10).astype(np.uint16))
        log_cov(dict(kind=kind, i=i, j=j, status="missing" if bad.all() else "ok", nan_frac=round(float(bad.mean()), 4))); return
    bad = ~np.isfinite(a) | (a < -100) | (a > 3000)
    a[bad] = np.nan
    frac, status = float(bad.mean()), "ok"
    if bad.any():                                                          # fill holes / missing tile from plain RGE ALTI
        try:
            r = tif(RGE, x, y); r[~np.isfinite(r) | (r < -100) | (r > 3000)] = np.nan
            a = np.where(bad, r, a); status = "rge_alti_full" if bad.all() else "rge_alti_partial"
        except RuntimeError: status = "missing" if bad.all() else "holes"
    left = float(np.isnan(a).mean())
    q = np.where(np.isfinite(a), np.clip(np.round((a + 100) * 20), 0, 65534), 65535).astype(np.uint16)
    save_npz(f, q)
    log_cov(dict(kind=kind, i=i, j=j, status=status, nan_frac=round(left, 4), fallback_frac=round(frac, 4)))

def du(d):
    n = 0
    for p in list(d.glob("*")):
        try: n += p.stat().st_size
        except FileNotFoundError: pass                                     # tmp file renamed meanwhile
    return n

def disk_ok():
    if shutil.disk_usage(".").free < MIN_FREE:
        if not stop.is_set(): print("LOW DISK: stopping", flush=True)
        stop.set()
    return not stop.is_set()

def rjob(a):
    if not disk_ok(): return "skipped"
    raster(*a); return "ok"

def vjob(a):
    name, si, sj = a
    f = VEC / f"{name}_{si}_{sj}.json.gz"
    if exists_vec(VEC / f"{name}_{si}_{sj}") or not disk_ok(): return
    x, y = X0 + si * SECTOR, Y0 + sj * SECTOR
    feats = wfs(VLAYERS[name], f"{x},{y},{x+SECTOR},{y+SECTOR}")
    tmp = f.with_name(f.name + ".tmp"); tmp.write_bytes(gzip.compress(json.dumps(feats).encode(), 6)); tmp.rename(f)

TAG = ""
def osm(bbox):
    """OSM POIs for the whole bbox, in a 4 x 4 grid of Overpass queries, merged (same filter as fetch_vectors.py)."""
    f = VEC / f"osm_poi_hg{TAG}.json.gz"
    if f.exists() or (VEC / f"osm_poi_hg{TAG}.json").exists(): return
    from pyproj import Transformer
    t = Transformer.from_crs(2154, 4326, always_xy=True)
    (lo0, la0), (lo1, la1) = t.transform(bbox[0], bbox[1]), t.transform(bbox[2], bbox[3])
    lo0, la0, lo1, la1 = min(lo0, lo1) - .01, min(la0, la1) - .01, max(lo0, lo1) + .01, max(la0, la1) + .01
    els = {}
    for a in range(4):
        for b in range(4):
            s, w = la0 + (la1 - la0) * a / 4, lo0 + (lo1 - lo0) * b / 4
            n, e = la0 + (la1 - la0) * (a + 1) / 4, lo0 + (lo1 - lo0) * (b + 1) / 4
            bb = f"({s:.4f},{w:.4f},{n:.4f},{e:.4f})"
            q = f'[out:json][timeout:180];(nwr["amenity"~"place_of_worship|pharmacy|townhall|school|post_office|restaurant|cafe|bar|bakery|fuel|doctors|community_centre|fire_station|police|library|kindergarten|bank|marketplace"]{bb};nwr["shop"]{bb};);out center tags;'
            for k in range(6):
                try:
                    r = requests.post("https://overpass-api.de/api/interpreter", data={"data": q}, timeout=240, headers={"User-Agent": "berat-racer/1.0 (hobby game, non-commercial)"})
                    if r.ok and r.text.lstrip().startswith("{"):
                        for el in r.json()["elements"]: els[(el["type"], el["id"])] = el
                        break
                except Exception as ex: print("osm", repr(ex)[:80], flush=True)
                time.sleep(15 * (k + 1))
            else: raise RuntimeError(f"osm cell {a},{b} failed")
            print(f"osm cell {a},{b}: total {len(els)}", flush=True)
            time.sleep(3)
    tmp = f.with_name(f.name + ".tmp"); tmp.write_bytes(gzip.compress(json.dumps({"elements": list(els.values())}).encode(), 6)); tmp.rename(f)

if __name__ == "__main__":
    sf = sys.argv[1] if len(sys.argv) > 1 else "data/big/hg_sectors.json"          # sector list from hg_sectors.py [codes...]
    S = json.load(open(sf))
    TAG = S.get("tag", ""); new = [tuple(s) for s in S["new_sectors"]]
    rj = [("ortho", si, sj) for si, sj in new]
    for si, sj in new:
        for kind in ("mnt", "mnh"):
            rj += [(kind, 2 * si + a, 2 * sj + b) for b in (0, 1) for a in (0, 1)]
    vj = [(n, si, sj) for si, sj in new for n in VLAYERS]
    print(f"{len(new)} new sectors, {len(rj)} raster tiles, {len(vj)} vector files", flush=True)
    t0, fails = time.time(), []
    def run(pool_n, fn, jobs, label):
        done = 0
        with ThreadPoolExecutor(pool_n) as ex:
            futs = {ex.submit(fn, j): j for j in jobs}
            for f in as_completed(futs):
                try: f.result()
                except Exception as e: fails.append((label, futs[f], str(e)[:120]))
                done += 1
                if done % 25 == 0 or done == len(jobs):
                    gb = du(OUT) / 2**20
                    print(f"{label} {done}/{len(jobs)}  {time.time()-t0:.0f}s  failures {len(fails)}  hg MB {gb:.0f}  free GB {shutil.disk_usage('.').free/2**30:.1f}", flush=True)
    th = threading.Thread(target=run, args=(2, vjob, vj, "vec")); th.start()
    osm_err = None
    def osm_t():
        global osm_err
        try: osm(S["bbox_l93"])
        except Exception as e: osm_err = repr(e); print("OSM FAILED", e, flush=True)
    th2 = threading.Thread(target=osm_t); th2.start()
    run(4, rjob, rj, "raster")
    th.join(); th2.join()
    json.dump(dict(failures=fails, osm_error=osm_err), open(OUT / f"failures{TAG}.json", "w"), indent=1)
    print("DONE failures:", len(fails), fails[:10], flush=True)
