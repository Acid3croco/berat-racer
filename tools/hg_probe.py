"""Sample LiDAR HD MNT coverage per departement with tiny GetMap requests."""
import io, random, sys
import numpy as np, rasterio, requests
from shapely.geometry import Point
from hg_sectors import boundary
random.seed(1)
L = "IGNF_LIDAR-HD_MNT_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93"
for code in ["09", "12", "31", "32", "46", "65", "81", "82"]:
    g = boundary(code); x0, y0, x1, y1 = g.bounds; pts = []
    while len(pts) < 40:
        p = Point(random.uniform(x0, x1), random.uniform(y0, y1))
        if g.contains(p): pts.append(p)
    ok = err = 0
    for p in pts:
        for t in range(3):
            try:
                r = requests.get("https://data.geopf.fr/wms-r/wms", params=dict(SERVICE="WMS", VERSION="1.3.0", REQUEST="GetMap", STYLES="", CRS="EPSG:2154", LAYERS=L,
                    BBOX=f"{p.x},{p.y},{p.x+50},{p.y+50}", WIDTH=10, HEIGHT=10, FORMAT="image/geotiff"), timeout=60)
                a = rasterio.open(io.BytesIO(r.content)).read(1)
                ok += bool(((a > -100) & (a < 4000)).mean() > .5); break
            except Exception: 
                if t == 2: err += 1
    print(code, f"area {g.area/1e6:.0f} km2  LiDAR HD covered {ok}/40 = {ok/.4:.0f}%  errors {err}", flush=True)
