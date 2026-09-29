"""Resumable tiled download of BD TOPO vectors + OSM POIs for the 32 x 32 km area (10 x 10 sectors of 3.2 km).

  data/big/vec/{layer}_{si}_{sj}.json   GeoJSON in Lambert-93, si east / sj north from the south-west corner
Features straddling sectors are duplicated across files; build_world.py de-duplicates on `cleabs`.
"""
import json, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import requests
from fetch import CX, CY

HALF, SECTOR = 16000, 3200
X0, Y0 = CX - HALF, CY - HALF
OUT = Path("data/big/vec"); OUT.mkdir(parents=True, exist_ok=True)
LAYERS = {"roads": "BDTOPO_V3:troncon_de_route", "buildings": "BDTOPO_V3:batiment",
          "hydro_lines": "BDTOPO_V3:troncon_hydrographique", "hydro_areas": "BDTOPO_V3:surface_hydrographique"}

def wfs(layer, bbox):
    feats, start = [], 0
    while True:
        for k in range(6):
            try:
                r = requests.get("https://data.geopf.fr/wfs/ows", params=dict(SERVICE="WFS", VERSION="2.0.0", REQUEST="GetFeature", TYPENAMES=layer,
                    OUTPUTFORMAT="application/json", SRSNAME="EPSG:2154", COUNT=5000, STARTINDEX=start, BBOX=bbox + ",EPSG:2154"), timeout=(10, 120))
                r.raise_for_status(); page = r.json()["features"]; break
            except Exception as e:
                err = repr(e)[:100]; time.sleep(2 + 3 * k)
        else: raise RuntimeError(err)
        feats += page
        if len(page) < 5000: return feats
        start += 5000

def job(a):
    name, si, sj = a
    f = OUT / f"{name}_{si}_{sj}.json"
    if f.exists(): return
    x, y = X0 + si * SECTOR, Y0 + sj * SECTOR
    feats = wfs(LAYERS[name], f"{x},{y},{x+SECTOR},{y+SECTOR}")
    f.with_suffix(".tmp").write_text(json.dumps(feats)); f.with_suffix(".tmp").rename(f)

def osm():
    f = OUT / "osm_poi.json"
    if f.exists(): return
    lat0, lon0, lat1, lon1 = 43.23, 0.93, 43.53, 1.42                          # generous cover of the 32 km box
    q = f"""[out:json][timeout:180];(nwr["amenity"~"place_of_worship|pharmacy|townhall|school|post_office|restaurant|cafe|bar|bakery|fuel|doctors|community_centre|fire_station|police|library|kindergarten|bank|marketplace"]({lat0},{lon0},{lat1},{lon1});nwr["shop"]({lat0},{lon0},{lat1},{lon1}););out center tags;"""
    for k in range(5):
        r = requests.post("https://overpass-api.de/api/interpreter", data={"data": q}, timeout=240, headers={"User-Agent": "berat-racer/1.0 (hobby game, non-commercial)"})
        if r.ok and r.text.lstrip().startswith("{"): f.write_text(r.text); print("osm", len(r.json()["elements"]), flush=True); return
        time.sleep(10)
    print("osm FAILED", flush=True)

if __name__ == "__main__":
    jobs = [(n, i, j) for n in LAYERS for j in range(10) for i in range(10)]
    osm()
    with ThreadPoolExecutor(3) as ex:
        for n, _ in enumerate(ex.map(job, jobs), 1):
            if n % 20 == 0: print(f"{n}/{len(jobs)}", flush=True)
    print("DONE", flush=True)
