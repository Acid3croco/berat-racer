"""Download LiDAR HD MNT/MNH (1 m) and BD TOPO roads/buildings for the Berat area."""
import io, json, sys
from concurrent.futures import ThreadPoolExecutor
import numpy as np, rasterio, requests
from pyproj import Transformer

CX, CY = 551972, 6254819          # Berat centre, Lambert-93
HALF = 3200                       # 6.4 km box ~ 41 km2
TILE = 800                        # metres per request, 1 px = 1 m
X0, Y0 = CX - HALF, CY - HALF
N = (2 * HALF) // TILE
WMS = "https://data.geopf.fr/wms-r/wms"
LAYERS = {"mnt": "IGNF_LIDAR-HD_MNT_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93",
          "mnh": "IGNF_LIDAR-HD_MNH_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93"}

def tile(job):
    name, i, j = job
    x, y = X0 + i * TILE, Y0 + j * TILE
    for attempt in range(4):
        r = requests.get(WMS, params=dict(SERVICE="WMS", VERSION="1.3.0", REQUEST="GetMap",
            LAYERS=LAYERS[name], STYLES="", CRS="EPSG:2154",
            BBOX=f"{x},{y},{x+TILE},{y+TILE}", WIDTH=TILE, HEIGHT=TILE, FORMAT="image/geotiff"), timeout=180)
        if r.ok and r.headers.get("content-type", "").startswith("image/"):
            with rasterio.open(io.BytesIO(r.content)) as ds:
                return name, i, j, ds.read(1)
    raise RuntimeError(f"tile failed {job}: {r.status_code} {r.text[:200]}")

def mosaic():
    size = N * TILE
    out = {k: np.full((size, size), np.nan, np.float32) for k in LAYERS}
    jobs = [(k, i, j) for k in LAYERS for i in range(N) for j in range(N)]
    with ThreadPoolExecutor(4) as ex:
        for n, (name, i, j, a) in enumerate(ex.map(tile, jobs), 1):
            # raster rows run north->south; store with row 0 = north edge
            r0 = size - (j + 1) * TILE
            out[name][r0:r0 + TILE, i * TILE:(i + 1) * TILE] = a
            print(f"\r{n}/{len(jobs)}", end="", file=sys.stderr)
    for k, a in out.items():
        print(f"\n{k}: min={np.nanmin(a):.1f} max={np.nanmax(a):.1f} nan={np.isnan(a).sum()}")
        np.save(f"data/{k}.npy", a)

def wfs(layer, fname):
    """BD TOPO features, paged, reprojected to Lambert-93 via srsName."""
    feats, start = [], 0
    while True:
        r = requests.get("https://data.geopf.fr/wfs/ows", params=dict(
            SERVICE="WFS", VERSION="2.0.0", REQUEST="GetFeature", TYPENAMES=layer,
            OUTPUTFORMAT="application/json", SRSNAME="EPSG:2154", COUNT=5000, STARTINDEX=start,
            BBOX=f"{X0},{Y0},{X0+2*HALF},{Y0+2*HALF},EPSG:2154"), timeout=180)
        r.raise_for_status()
        page = r.json()["features"]
        feats += page
        if len(page) < 5000: break
        start += 5000
    json.dump({"type": "FeatureCollection", "features": feats}, open(f"data/{fname}", "w"))
    print(layer, len(feats))

if __name__ == "__main__":
    what = sys.argv[1:] or ["vectors", "rasters"]
    if "vectors" in what:
        wfs("BDTOPO_V3:troncon_de_route", "roads.geojson")
        wfs("BDTOPO_V3:batiment", "buildings.geojson")
    if "rasters" in what:
        mosaic()
