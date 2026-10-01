"""Ground data of a sector list: what covers the ground between the roads and buildings, for the ground classes and their dressing.

  data/big/vec/{layer}_{si}_{sj}.json.gz   per sector, GeoJSON in Lambert-93, read with build_world.load_vectors (same grid as fetch_hg.py)
      vegetation   BD TOPO zone_de_vegetation: woods by leaf type, hedges, vines, orchards, heath, poplar lines
      transport    BD TOPO equipement_de_transport: car parks, service stations, rest areas, stations
      structures   BD TOPO construction_lineaire: walls, retaining walls, bridges, fences, dams
      rpg          RPG (latest year): every declared farm parcel with its crop code
      hedges       BD Haie: hedgerow lines
  data/big/vec/rows_{tag}.json.gz           row direction of the crop, vine and orchard parcels measured on the 20 cm orthophoto (ground.py)
  data/big/osm/ground_{tag}.json.gz         OSM areas and lines of the list's window: landuse / natural / leisure polygons, car parks
                                            (amenity=parking with parking=*), parking aisles, barriers. From Overpass: the database on
                                            mace is a filtered import without landuse.

Usage: uv run python fetch_ground.py data/big/small_sectors.json     (sectors of the list and their neighbours; files already there are kept)
"""
import gzip
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fetch import CX, CY
from fetch_hg import X0, Y0, VEC
from fetch_vectors import SECTOR, wfs
from rasters import BIG
from roads import build as road_build
from vec_io import exists_vec

LAYERS = {"vegetation": "BDTOPO_V3:zone_de_vegetation", "transport": "BDTOPO_V3:equipement_de_transport",
          "structures": "BDTOPO_V3:construction_lineaire", "rpg": "RPG.LATEST:parcelles_graphiques", "hedges": "HAIES.BOCAGES:haie"}
OVERPASS = "https://overpass-api.de/api/interpreter"
USER_AGENT = "berat-racer/1.0 (hobby game, non-commercial)"
OSM_FILTERS = ('nwr["landuse"]', 'nwr["natural"]', 'nwr["leisure"]', 'nwr["amenity"="parking"]', 'way["service"="parking_aisle"]', 'way["barrier"]',
               'way["highway"="service"]["service"="parking_aisle"]')


def window(sectors):
    return sorted({(si + di, sj + dj) for si, sj in sectors for di in (-1, 0, 1) for dj in (-1, 0, 1)})


def sector_job(job):
    name, si, sj = job
    path = VEC / f"{name}_{si}_{sj}.json.gz"
    if exists_vec(VEC / f"{name}_{si}_{sj}"):
        return name, si, sj, None
    x, y = X0 + si * SECTOR, Y0 + sj * SECTOR
    feats = wfs(LAYERS[name], f"{x},{y},{x + SECTOR},{y + SECTOR}")
    for k, f in enumerate(feats):                                               # build_world de-duplicates on cleabs, else the feature id
        f.setdefault("id", f"{name}.{si}.{sj}.{k}")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(gzip.compress(json.dumps(feats).encode(), 6))
    tmp.rename(path)
    return name, si, sj, len(feats)


def osm_path(tag):
    return BIG / "osm" / f"ground_{tag}.json.gz"


def _feature(e, to_l93):
    """A GeoJSON feature (Lambert-93) of an Overpass element with its geometry: a closed way is an area, a multipolygon relation is
    rebuilt from its member ways, any other way is a line."""
    from shapely.geometry import LineString, Polygon, mapping
    from shapely.ops import polygonize, unary_union

    def xy(points):
        return [to_l93.transform(p["lon"], p["lat"]) for p in points]
    tags = e.get("tags", {})
    if e["type"] == "way":
        pts = xy(e.get("geometry", []))
        if len(pts) < 2:
            return None
        closed = len(pts) >= 4 and pts[0] == pts[-1]
        area = closed and not ("barrier" in tags or tags.get("service") == "parking_aisle")
        geom, kind = (Polygon(pts), "area") if area else (LineString(pts), "line")
    elif e["type"] == "relation" and tags.get("type") == "multipolygon":
        def part(role):
            rings = [LineString(xy(m["geometry"])) for m in e.get("members", [])
                     if m.get("type") == "way" and m.get("role", "outer") == role and len(m.get("geometry", [])) >= 2]
            return unary_union(list(polygonize(unary_union(rings)))) if rings else Polygon()
        outer = part("outer")
        if outer.is_empty:
            return None
        geom, kind = outer.difference(part("inner")), "area"
    else:
        return None
    if not geom.is_valid:
        geom = geom.buffer(0)
    return dict(type="Feature", id=f"osm{e['type'][0]}{e['id']}", properties=dict(tags, osm_kind=kind), geometry=mapping(geom))


def fetch_osm(sectors, tag):
    """Landuse, natural, leisure, car parks, parking aisles and barriers of the window, from Overpass (the OSM database on mace is a
    filtered import without landuse: 170 polygons over 16 km, none tagged landuse)."""
    import time

    import requests
    from pyproj import Transformer
    path = osm_path(tag)
    if path.exists():
        return path, None
    cells = window(sectors)
    x0, y0 = X0 + min(c[0] for c in cells) * SECTOR, Y0 + min(c[1] for c in cells) * SECTOR
    x1, y1 = X0 + (max(c[0] for c in cells) + 1) * SECTOR, Y0 + (max(c[1] for c in cells) + 1) * SECTOR
    to_wgs = Transformer.from_crs(2154, 4326, always_xy=True)
    to_l93 = Transformer.from_crs(4326, 2154, always_xy=True)
    lon0, lat0 = to_wgs.transform(x0, y0)
    lon1, lat1 = to_wgs.transform(x1, y1)
    bbox = f"({min(lat0, lat1)},{min(lon0, lon1)},{max(lat0, lat1)},{max(lon0, lon1)})"
    query = "[out:json][timeout:600];(" + "".join(f"{f}{bbox};" for f in OSM_FILTERS) + ");out tags geom;"
    for attempt in range(6):
        reply = requests.post(OVERPASS, data={"data": query}, timeout=900, headers={"User-Agent": USER_AGENT})
        if reply.ok and reply.text.lstrip().startswith("{"):
            break
        time.sleep(15 + 15 * attempt)
    else:
        raise RuntimeError(f"Overpass ground fetch failed: HTTP {reply.status_code}")
    out = [f for f in (_feature(e, to_l93) for e in reply.json()["elements"]) if f is not None]
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(out, fh)
    return path, len(out)


def main(list_path):
    sectors = [tuple(s) for s in json.loads(Path(list_path).read_text())["sectors"]]
    jobs = [(name, si, sj) for name in LAYERS for si, sj in window(sectors)]
    with ThreadPoolExecutor(3) as pool:
        for name, si, sj, n in pool.map(sector_job, jobs):
            if n is not None:
                print(f"{name} {si} {sj}: {n}", flush=True)
    tag = road_build.tag_of(list_path)
    path, n = fetch_osm(sectors, tag)
    print(f"osm ground: {path} ({'cached' if n is None else n})")
    measure_rows(sectors, tag)


def measure_rows(sectors, tag):
    """Row direction of every rowed RPG parcel of the list's sectors, on the orthophoto (ground.py), cached."""
    import shapely
    from shapely.geometry import shape

    import ground
    from build_world import load_vectors
    codes = ground.rpg_codes(VEC)
    parcels, seen = [], set()
    for si, sj in sectors:
        for f in load_vectors("rpg", si, sj):
            if f["id"] in seen or ground.rpg_class(f["properties"], codes) not in ground.ROWED:
                continue
            seen.add(f["id"])
            g = shapely.transform(shape(f["geometry"]), lambda xy: xy - [CX, CY])
            parcels += [(f["id"], q) for q in ([g] if g.geom_type == "Polygon" else list(g.geoms))]
    done = ground.measure_all(parcels, ground.rows_path(BIG, tag))
    clear = sum(1 for pid, _ in parcels if pid in done and done[pid][1] >= ground.ROW_COHERENCE)
    print(f"rows: {len(parcels)} rowed parcels, {sum(1 for pid, _ in parcels if pid in done)} measured, {clear} clear on the orthophoto")


if __name__ == "__main__":
    main(sys.argv[1])
