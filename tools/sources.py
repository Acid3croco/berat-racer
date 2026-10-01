"""Source data, one file per tile and kind, fetched on demand.

A build asks for the tiles it needs (`ensure`); whatever is missing is fetched for those tiles only, each service in parallel within its
limits, written atomically (a tile is there or it is not: re-running resumes) and kept. No whole-area download, no whole-area file.

Tiles. Vector and OSM tiles are the sectors of the world grid: tile (si, sj) covers Lambert-93 x in [X0 + 3200 si, + 3200), y likewise,
X0, Y0 = the Berat centre - 16 km (local x = X - CX). Raster tiles: mnt / mnh 1.6 km (i, j: 2 si .. 2 si + 1), ortho 3.2 km (= sector).
A build of a sector list needs the tiles of its sectors and their eight neighbours (`window`).

Layout (data/big/src/):

  mnt/{i}_{j}.u16.zst     LiDAR HD ground, 800 x 800 px of 2 m, row 0 north: uint16 codes z = a / 20 - 100 (5 cm, 65535 = no data),
                          stored as the difference along each row (uint16, wrapping) compressed with zstd
  mnt/{i}_{j}.f32.zst     the original 10 x 10 sector block: float32 metres, the four bytes of each value stored apart (shuffled), zstd
  mnh/...                 height above ground the same way (a / 10 metres, 0.1 m)
  ortho/{si}_{sj}.jpg     orthophoto 3.2 km, 800 x 800 px (4 m), the server's JPEG; .png for the original block (lossless copy)
  <layer>/{si}_{sj}.json.gz   GeoJSON features (Lambert-93) of an IGN WFS layer whose box meets the tile (BD TOPO: roads, buildings,
                          hydro_lines, hydro_areas, vegetation, transport, structures, non_communication; rpg; hedges)
  osm_roads/...           OSM drivable highways (massif-extractor database on mace): {id, highway, surface, name, ref, tags, xy (L93)}
  osm_controls/...        OSM traffic signals / stop / give way / mini roundabouts (mace): {id, highway, tags, x, z (local)}
  osm_ground/...          Overpass: landuse / natural / leisure areas, car parks, aisles, barriers as GeoJSON features (L93)
  osm_roofs/...           Overpass: buildings tagging their roof or levels {id, tags, xy (local)}
  osm_restrictions/...    Overpass: turn restriction relations {id, restriction, from_way, to_way, x, z (local, the via node)}
  osm_pois/...            Overpass: amenity / shop elements (with their centre)
  osm_places/...          Overpass: place nodes {n, k, r, pop, x, z (local)}
  rows/{si}_{sj}.json.gz  row direction of the rowed RPG parcels of the tile measured on the 20 cm orthophoto {id: [radians, coherence]}
  meta/rpg_codes.json     the RPG crop code table

Lines and areas are kept in every tile their box meets (readers de-duplicate on their id), points in the one tile holding them.

  uv run python sources.py fetch data/big/berat70new_sectors.json    fetch what the list needs (report: data/big/profile/fetch_<tag>.json)
  uv run python sources.py status data/big/berat70new_sectors.json   what is there and what is missing
  uv run python sources.py convert                                    move today's caches into this layout (once; nothing is downloaded)
"""
import argparse
import gzip
import io
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import zstandard

from fetch import CX, CY

BIG = Path("data/big")
SRC = BIG / "src"
HALF, SECTOR, RASTER = 16000, 3200, 1600
X0, Y0 = CX - HALF, CY - HALF
SIDE = 800                                     # pixels per raster tile side

WFS = "https://data.geopf.fr/wfs/ows"
WMS = "https://data.geopf.fr/wms-r/wms"
USER_AGENT = "berat-racer/1.0 (hobby game, non-commercial)"
SSH_HOST = os.environ.get("BERAT_OSM_SSH", "mace")
PSQL = ('PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=300000" '
        "psql -w -h localhost -p 5411 -U osm_user osm_db -At")

WFS_LAYERS = {"roads": "BDTOPO_V3:troncon_de_route", "buildings": "BDTOPO_V3:batiment", "hydro_lines": "BDTOPO_V3:troncon_hydrographique",
              "hydro_areas": "BDTOPO_V3:surface_hydrographique", "vegetation": "BDTOPO_V3:zone_de_vegetation",
              "transport": "BDTOPO_V3:equipement_de_transport", "structures": "BDTOPO_V3:construction_lineaire", "rpg": "RPG.LATEST:parcelles_graphiques",
              "hedges": "HAIES.BOCAGES:haie", "non_communication": "BDTOPO_V3:non_communication"}
MACE_KINDS = ("osm_roads", "osm_controls")
OVERPASS_KINDS = ("osm_ground", "osm_roofs", "osm_restrictions", "osm_pois", "osm_places")
RASTER_KINDS = ("mnt", "mnh", "ortho")
VECTOR_KINDS = tuple(WFS_LAYERS) + MACE_KINDS + OVERPASS_KINDS + ("rows",)
# what a build reads: the rows need the rpg tile first (fetched in an earlier wave)
BUILD_KINDS = RASTER_KINDS + VECTOR_KINDS
SERVICE_THREADS = {"wms": 4, "wfs": 3, "mace": 1, "overpass": 3, "rows": 3}          # overpass: one query per public instance

_zc, _zd = threading.local(), zstandard.ZstdDecompressor()


def _compressor():
    if not hasattr(_zc, "c"):
        _zc.c = zstandard.ZstdCompressor(level=19)
    return _zc.c


# ------------------------------------------------------------------ tiles

def window(sectors):
    """Vector tiles a sector list needs: its sectors and their eight neighbours, sorted."""
    return sorted({(si + di, sj + dj) for si, sj in sectors for di in (-1, 0, 1) for dj in (-1, 0, 1)})


def raster_tiles(kind, tiles):
    """Raster tiles (i, j) of `kind` under vector tiles."""
    if kind == "ortho":
        return list(tiles)
    return sorted({(2 * si + a, 2 * sj + b) for si, sj in tiles for a in (0, 1) for b in (0, 1)})


def tile_rect(si, sj, size=SECTOR):
    """Lambert-93 (x0, y0, x1, y1) of a tile."""
    return X0 + si * size, Y0 + sj * size, X0 + (si + 1) * size, Y0 + (sj + 1) * size


def tiles_of_box(x0, y0, x1, y1):
    """Vector tiles a Lambert-93 box meets."""
    return [(si, sj) for sj in range(int((y0 - Y0) // SECTOR), int((y1 - Y0) // SECTOR) + 1)
            for si in range(int((x0 - X0) // SECTOR), int((x1 - X0) // SECTOR) + 1)]


def tile_of_point(x, y):
    return int((x - X0) // SECTOR), int((y - Y0) // SECTOR)


# ------------------------------------------------------------------ files

def raster_path(kind, i, j):
    """The existing file of a raster tile (None when it is not there)."""
    d = SRC / kind
    for name in ((f"{i}_{j}.jpg", f"{i}_{j}.png") if kind == "ortho" else (f"{i}_{j}.u16.zst", f"{i}_{j}.f32.zst")):
        if (d / name).exists():
            return d / name
    return None


def vector_path(kind, si, sj):
    return SRC / kind / f"{si}_{sj}.json.gz"


def _atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def encode_codes(a):
    """uint16 codes (800 x 800) -> bytes: the difference along each row, zstd."""
    d = np.diff(np.asarray(a, np.uint16), axis=1, prepend=np.uint16(0))
    return _compressor().compress(d.astype("<u2").tobytes())


def decode_codes(data):
    d = np.frombuffer(_zd.decompress(data), "<u2").reshape(SIDE, SIDE)
    return np.cumsum(d, axis=1, dtype=np.uint16)


def encode_float(a):
    """float32 (800 x 800) -> bytes: the four bytes of each value stored apart, zstd (lossless)."""
    raw = np.ascontiguousarray(a, "<f4").view(np.uint8).reshape(-1, 4)
    return _compressor().compress(raw.T.copy().tobytes())


def decode_float(data):
    raw = np.frombuffer(_zd.decompress(data), np.uint8).reshape(4, -1)
    return raw.T.copy().view("<f4").reshape(SIDE, SIDE)


def read_raster(kind, i, j):
    """One raster tile, north row first: mnt / mnh float32 metres (mnt NaN where there is no data), ortho uint8 RGB; None if missing."""
    path = raster_path(kind, i, j)
    if path is None:
        return None
    if kind == "ortho":
        from PIL import Image
        return np.asarray(Image.open(path).convert("RGB"))
    data = path.read_bytes()
    if path.name.endswith(".f32.zst"):
        return decode_float(data)
    a = decode_codes(data)
    if kind == "mnt":
        return np.where(a == 65535, np.nan, a / 20.0 - 100).astype(np.float32)
    return (a / 10.0).astype(np.float32)


def write_raster(kind, i, j, a):
    """Store a raster tile: uint16 codes for mnt / mnh, float32 (original block), or JPEG bytes / an RGB array for the orthophoto."""
    if kind == "ortho":
        if isinstance(a, (bytes, bytearray)):
            _atomic(SRC / "ortho" / f"{i}_{j}.jpg", bytes(a))
        else:
            from PIL import Image
            buf = io.BytesIO()
            Image.fromarray(np.asarray(a, np.uint8)).save(buf, format="PNG", optimize=True)
            _atomic(SRC / "ortho" / f"{i}_{j}.png", buf.getvalue())
        return
    a = np.asarray(a)
    if a.dtype == np.float32:
        _atomic(SRC / kind / f"{i}_{j}.f32.zst", encode_float(a))
    else:
        _atomic(SRC / kind / f"{i}_{j}.u16.zst", encode_codes(a))


def read_tile(kind, si, sj):
    """The records of one vector tile ([] when the tile is not there)."""
    path = vector_path(kind, si, sj)
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def write_tile(kind, si, sj, records):
    _atomic(vector_path(kind, si, sj), gzip.compress(json.dumps(records, separators=(",", ":")).encode(), 6))


def record_key(kind, record):
    """What identifies a record across tiles."""
    if kind == "non_communication":
        return record["x"], record["z"], record["entry"]
    if kind in WFS_LAYERS:
        return record["properties"].get("cleabs") or record.get("id")
    if kind == "osm_pois":
        return record["type"], record["id"]
    if kind == "osm_places":
        return record["n"], record["x"], record["z"]
    return record.get("id")


def read_tiles(kind, tiles):
    """Records of several tiles in tile order, de-duplicated (the first copy is kept)."""
    seen, out = set(), []
    for si, sj in tiles:
        for r in read_tile(kind, si, sj):
            key = record_key(kind, r)
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
    return out


def stamp(kind, si, sj):
    """Content hash of a tile file (None when missing), kept in memory by (path, size, mtime) so a file is read once per process."""
    path = raster_path(kind, si, sj) if kind in RASTER_KINDS else vector_path(kind, si, sj)
    return content_hash(path) if path is not None and path.exists() else None


_hashes = {}


def content_hash(path):
    import hashlib
    st = os.stat(path)
    key = (str(path), st.st_size, st.st_mtime_ns)
    if key not in _hashes:
        _hashes[key] = hashlib.blake2b(Path(path).read_bytes(), digest_size=16).hexdigest()
    return _hashes[key]


# ------------------------------------------------------------------ fetchers (one tile each)

class Counter:
    """Requests, bytes and seconds per service (the fetch report)."""

    def __init__(self):
        self.lock, self.by = threading.Lock(), {}

    def add(self, service, nbytes, secs, requests=1):
        with self.lock:
            s = self.by.setdefault(service, dict(requests=0, mb=0.0, secs=0.0, tiles=0))
            s["requests"] += requests
            s["mb"] += nbytes / 2**20
            s["secs"] += secs


COUNT = Counter()


def _get(url, params, service, tries=8, expect="json"):
    import requests
    err = None
    for k in range(tries):
        t = time.time()
        try:
            r = requests.get(url, params=params, timeout=(10, 120), headers={"User-Agent": USER_AGENT})
            COUNT.add(service, len(r.content), time.time() - t)
            if r.ok and (expect != "image" or r.headers.get("content-type", "").startswith("image/")):
                return r
            err = f"HTTP {r.status_code} {r.text[:120]!r}"
            if r.status_code in (400, 404) and expect == "image":
                return r
            if r.status_code in (429, 503):                                     # asked to slow down: wait as told, or longer each time
                time.sleep(float(r.headers.get("Retry-After", 0) or 0) or 20 * (k + 1))
                continue
        except Exception as e:
            err = repr(e)[:120]
        time.sleep(2 + 3 * k)
    raise RuntimeError(f"{service}: {err}")


def wfs_features(layer, rect):
    x0, y0, x1, y1 = rect
    feats, start = [], 0
    while True:
        page = _get(WFS, dict(SERVICE="WFS", VERSION="2.0.0", REQUEST="GetFeature", TYPENAMES=layer, OUTPUTFORMAT="application/json",
                              SRSNAME="EPSG:2154", COUNT=5000, STARTINDEX=start, BBOX=f"{x0},{y0},{x1},{y1},EPSG:2154"), "wfs").json()["features"]
        feats += page
        if len(page) < 5000:
            return feats
        start += 5000


def fetch_wfs(kind, si, sj):
    feats = wfs_features(WFS_LAYERS[kind], tile_rect(si, sj))
    if kind == "non_communication":
        out = []
        for f in feats:
            p = f["properties"]
            exits = p.get("liens_vers_troncon_sortie") or ""
            x, y = f["geometry"]["coordinates"][:2]
            if tile_of_point(x, y) != (si, sj):
                continue
            out.append(dict(x=x - CX, z=y - CY, entry=p.get("lien_vers_troncon_entree"), exits=[e for e in exits.replace(",", "/").split("/") if e]))
        feats = out
    elif kind in ("vegetation", "transport", "structures", "rpg", "hedges"):
        for k, f in enumerate(feats):                                           # readers de-duplicate on cleabs, else the feature id
            f.setdefault("id", f"{kind}.{si}.{sj}.{k}")
    write_tile(kind, si, sj, feats)


def fetch_raster(kind, i, j):
    import rasterio
    if kind == "ortho":
        x0, y0, x1, y1 = tile_rect(i, j)
        data = _get(WMS, _wms("ORTHOIMAGERY.ORTHOPHOTOS", x0, y0, SECTOR, "image/jpeg"), "wms", expect="image").content
        write_raster("ortho", i, j, data)
        return
    layer = {"mnt": "IGNF_LIDAR-HD_MNT_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93", "mnh": "IGNF_LIDAR-HD_MNH_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93"}[kind]
    x0, y0, _, _ = tile_rect(i, j, RASTER)

    def tif(name):
        r = _get(WMS, _wms(name, x0, y0, RASTER, "image/geotiff"), "wms", expect="image")
        if not r.headers.get("content-type", "").startswith("image/"):
            return np.full((SIDE, SIDE), np.nan, np.float32)                # the layer refuses the tile: no LiDAR there
        with rasterio.open(io.BytesIO(r.content)) as ds:
            return ds.read(1).astype(np.float32)
    a = tif(layer)
    if kind == "mnh":
        bad = ~np.isfinite(a) | (a < -50) | (a > 200)
        write_raster(kind, i, j, np.round(np.where(bad, 0, np.clip(a, 0, 60)) * 10).astype(np.uint16))
        return
    bad = ~np.isfinite(a) | (a < -100) | (a > 3000)
    a[bad] = np.nan
    if bad.any():                                                               # holes and missing tiles from RGE ALTI
        r = tif("ELEVATION.ELEVATIONGRIDCOVERAGE.HIGHRES")
        r[~np.isfinite(r) | (r < -100) | (r > 3000)] = np.nan
        a = np.where(bad, r, a)
    write_raster(kind, i, j, np.where(np.isfinite(a), np.clip(np.round((a + 100) * 20), 0, 65534), 65535).astype(np.uint16))


def _wms(layer, x, y, size, fmt):
    return dict(SERVICE="WMS", VERSION="1.3.0", REQUEST="GetMap", STYLES="", CRS="EPSG:2154", LAYERS=layer,
                BBOX=f"{x},{y},{x + size},{y + size}", WIDTH=SIDE, HEIGHT=SIDE, FORMAT=fmt)


DRIVABLE = ("motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link",
            "unclassified", "residential", "living_street", "service", "track", "road")
CONTROLS = ("traffic_signals", "stop", "give_way", "mini_roundabout")
MACE_SQL = {
    "osm_roads": """copy (select json_build_object('id', osm_id, 'highway', highway, 'surface', surface, 'name', name, 'ref', ref, 'tags', hstore_to_json(tags),
  'xy', ST_AsGeoJSON(ST_Transform(way, 2154), 2)::json->'coordinates') from planet_osm_line
  where highway in ({classes}) and way && ST_Transform(ST_MakeEnvelope({x0}, {y0}, {x1}, {y1}, 2154), 4326)) to stdout;""",
    "osm_controls": """copy (select json_build_object('id', osm_id, 'highway', highway, 'tags', hstore_to_json(tags),
  'xy', ST_AsGeoJSON(ST_Transform(way, 2154), 2)::json->'coordinates') from planet_osm_point
  where highway in ({classes}) and way && ST_Transform(ST_MakeEnvelope({x0}, {y0}, {x1}, {y1}, 2154), 4326)) to stdout;"""}


def line_box(xy):
    a = np.asarray(xy, float).reshape(-1, 2)
    return a[:, 0].min(), a[:, 1].min(), a[:, 0].max(), a[:, 1].max()


def meets(box, rect):
    return box[2] >= rect[0] and box[0] < rect[2] and box[3] >= rect[1] and box[1] < rect[3]


def fetch_mace(kind, tiles):
    """One ssh session for a batch of tiles: a COPY per tile, separated by a marker line."""
    t = time.time()
    script = []
    for si, sj in tiles:
        x0, y0, x1, y1 = tile_rect(si, sj)
        script.append(f"\\echo @@TILE {si} {sj}")
        classes = ", ".join(f"'{c}'" for c in (DRIVABLE if kind == "osm_roads" else CONTROLS))
        script.append(MACE_SQL[kind].format(classes=classes, x0=x0, y0=y0, x1=x1, y1=y1))
    done = subprocess.run(["ssh", "-o", "BatchMode=yes", SSH_HOST, PSQL], input="\n".join(script), capture_output=True, text=True, timeout=1800)
    if done.returncode != 0 or done.stderr.strip():
        raise RuntimeError(f"mace: {done.stderr.strip()[:300]}")
    COUNT.add("mace", len(done.stdout), time.time() - t, requests=len(tiles))
    rows, current = {}, None
    for line in done.stdout.splitlines():
        if line.startswith("@@TILE "):
            current = tuple(int(v) for v in line.split()[1:])
            rows[current] = []
        elif line.strip() and current is not None:
            rows[current].append(json.loads(line.replace("\\\\", "\\")))
    for (si, sj), recs in rows.items():
        rect = tile_rect(si, sj)
        if kind == "osm_roads":
            out = [w for w in recs if meets(line_box(w["xy"]), rect)]
        else:
            out = [dict(id=n["id"], highway=n["highway"], tags=n.get("tags") or {}, x=n["xy"][0] - CX, z=n["xy"][1] - CY)
                   for n in recs if tile_of_point(*n["xy"][:2]) == (si, sj)]
        write_tile(kind, si, sj, out)


POI_AMENITY = "place_of_worship|pharmacy|townhall|school|post_office|restaurant|cafe|bar|bakery|fuel|doctors|community_centre|fire_station|police|library|kindergarten|bank|marketplace"
GROUND_FILTERS = ('wr["landuse"]', 'wr["natural"]', 'wr["leisure"]', 'wr["amenity"="parking"]', 'way["service"="parking_aisle"]', 'way["barrier"]',
                  'way["highway"="service"]["service"="parking_aisle"]')
PLACE_RANK = {"city": 0, "town": 1, "village": 2, "suburb": 2, "hamlet": 3}
OVERPASS_SERVERS = ("https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter",
                    "https://overpass.kumi.systems/api/interpreter")
_servers = [dict(url=u, lock=threading.Lock(), last=0.0, seconds=10.0) for u in OVERPASS_SERVERS]
_pick = threading.Lock()


def overpass(query, service="overpass", gap=2.0):
    """POST a query to a public Overpass instance: at most one query at a time on each and `gap` seconds apart (their courtesy rules),
    the one that has been answering fastest (waiting for it when it is busy, unless another is less than half as slow; a refusal or a
    time-out counts as 300 s), with back-off."""
    import requests
    for attempt in range(12):
        with _pick:
            server = min(_servers, key=lambda s: s["seconds"] * (2 if s["lock"].locked() else 1))     # a busy fast one beats a free slow one
        server["lock"].acquire()
        try:
            wait = server["last"] + gap - time.time()
            if wait > 0:
                time.sleep(wait)
            t = time.time()
            try:
                r = requests.post(server["url"], data={"data": query}, timeout=300, headers={"User-Agent": USER_AGENT})
                COUNT.add(service, len(r.content), time.time() - t)
                ok = r.ok and r.text.lstrip().startswith("{")
            except Exception:
                ok = False
            took = time.time() - t if ok else 300.0
            server["seconds"] = 0.7 * server["seconds"] + 0.3 * took
            server["last"] = time.time() + (0 if ok else 15 * (attempt + 1))
        finally:
            server["lock"].release()
        if ok:
            return r.json()["elements"]
    raise RuntimeError("overpass: no answer")


OVERPASS_PARTS = ("osm_ground", "osm_roofs", "osm_restrictions", "osm_pois", "osm_places")


def fetch_overpass(si, sj, kinds=OVERPASS_PARTS):
    """The Overpass kinds of a tile in one query (`out count` separates the answers). The turn restrictions alone are a light query:
    the road build asks for them first."""
    from pyproj import Transformer
    to_wgs = Transformer.from_crs(2154, 4326, always_xy=True)
    to_l93 = Transformer.from_crs(4326, 2154, always_xy=True)
    rect = tile_rect(si, sj)
    lons, lats = to_wgs.transform([rect[0], rect[2], rect[0], rect[2]], [rect[1], rect[1], rect[3], rect[3]])
    bb = f"({min(lats):.6f},{min(lons):.6f},{max(lats):.6f},{max(lons):.6f})"
    statements = {
        "osm_ground": "(" + "".join(f"{f}{bb};" for f in GROUND_FILTERS) + ");out tags geom;",
        "osm_roofs": f'(way["building"]["roof:shape"]{bb};way["building"]["building:levels"]{bb};way["building"]["roof:orientation"]{bb};);out tags geom;',
        "osm_restrictions": f'rel["type"="restriction"]{bb};out body;node(r:"via");out skel;',
        "osm_pois": f'(nwr["amenity"~"{POI_AMENITY}"]{bb};nwr["shop"]{bb};);out center tags;',
        "osm_places": f'node["place"~"^(city|town|village|hamlet|suburb)$"]["name"]{bb};out;'}
    kinds = [k for k in OVERPASS_PARTS if k in kinds]
    query = "[out:json][timeout:300];" + "".join(statements[k] + "out count;" for k in kinds)
    parts, current = [], []
    for e in overpass(query):
        if e["type"] == "count":
            parts.append(current)
            current = []
        else:
            current.append(e)
    if len(parts) != len(kinds):
        raise RuntimeError(f"overpass: {len(parts)} answers for tile {si} {sj}")
    answer = dict(zip(kinds, parts))
    if "osm_ground" in answer:
        write_tile("osm_ground", si, sj, osm_ground_features(answer["osm_ground"], to_l93, rect))
    if "osm_roofs" in answer:
        write_tile("osm_roofs", si, sj, osm_roof_records(answer["osm_roofs"], to_l93, rect))
    if "osm_restrictions" in answer:
        write_tile("osm_restrictions", si, sj, osm_restriction_records(answer["osm_restrictions"], to_l93, (si, sj)))
    if "osm_pois" in answer:
        write_tile("osm_pois", si, sj, [e for e in answer["osm_pois"] if tile_of_point(*to_l93.transform(*_lonlat(e))) == (si, sj)])
    if "osm_places" in answer:
        write_tile("osm_places", si, sj, [p for p in osm_place_records(answer["osm_places"], to_l93) if tile_of_point(p["x"] + CX, p["z"] + CY) == (si, sj)])


def _lonlat(e):
    where = e if e["type"] == "node" else e.get("center", {})
    return where.get("lon", 0.0), where.get("lat", 0.0)


def osm_ground_features(elements, to_l93, rect):
    """Ground elements -> GeoJSON features whose box meets the tile, in Overpass order."""
    from shapely.geometry import shape
    out = []
    for e in elements:
        f = osm_feature(e, to_l93)
        if f is not None and meets(shape(f["geometry"]).bounds, rect):
            out.append(f)
    return out


def osm_feature(e, to_l93):
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


def osm_roof_records(elements, to_l93, rect):
    out = []
    for way in elements:
        pts = [to_l93.transform(g["lon"], g["lat"]) for g in way.get("geometry", [])]
        if pts and meets(line_box(pts), rect):
            out.append(dict(id=way["id"], tags=way.get("tags", {}), xy=[(x - CX, y - CY) for x, y in pts]))
    return out


def osm_restriction_records(elements, to_l93, tile):
    where = {e["id"]: to_l93.transform(e["lon"], e["lat"]) for e in elements if e["type"] == "node"}
    out = []
    for rel in (e for e in elements if e["type"] == "relation"):
        kind = rel.get("tags", {}).get("restriction")
        role = {r["role"]: r for r in rel["members"]}
        if not kind or "from" not in role or "to" not in role or role.get("via", {}).get("type") != "node":
            continue
        x, y = where.get(role["via"]["ref"], (None, None))
        if x is None or tile_of_point(x, y) != tile:
            continue
        out.append(dict(id=rel["id"], restriction=kind, from_way=role["from"]["ref"], to_way=role["to"]["ref"], x=x - CX, z=y - CY))
    return out


def osm_place_records(elements, to_l93):
    out = []
    for e in elements:
        t = e.get("tags", {})
        k = t.get("place")
        if k not in PLACE_RANK:
            continue
        x, y = to_l93.transform(e["lon"], e["lat"])
        try:
            pop = int(str(t.get("population", "0")).replace(" ", "").split(";")[0])
        except ValueError:
            pop = 0
        out.append(dict(n=t["name"], k=k, r=PLACE_RANK[k], pop=pop, x=round(x - CX, 1), z=round(y - CY, 1)))
    return out


def fetch_rows(si, sj):
    """Row directions of the tile's rowed parcels (ground.py), measured on the orthophoto; the parcels measured in a neighbouring tile
    (a parcel crossing tiles) are not measured twice."""
    import shapely
    from shapely.geometry import shape
    import ground
    codes = rpg_codes()
    done = {}
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            if (di or dj) and vector_path("rows", si + di, sj + dj).exists():
                done.update(read_tile("rows", si + di, sj + dj))
    parcels = []
    for f in read_tile("rpg", si, sj):
        if ground.rpg_class(f["properties"], codes) not in ground.ROWED:
            continue
        g = shapely.transform(shape(f["geometry"]), lambda xy: xy - [CX, CY])
        parcels += [(f["id"], q) for q in ([g] if g.geom_type == "Polygon" else list(g.geoms))]
    eligible = [(pid, q) for pid, q in parcels if q.area >= ground.ROW_SAMPLE_AREA]
    out = {pid: done[pid] for pid, _ in eligible if pid in done}
    todo = [(pid, q) for pid, q in eligible if pid not in out]

    for pid, poly in todo:                                                      # one crop at a time per tile (the WMS pool is shared)
        inner = poly.buffer(-10)
        p = (inner if not inner.is_empty else poly).representative_point()
        out[pid] = list(ground.measure_rows(p.x + CX, p.y + CY, lambda params: _get(WMS, params, "wms_rows", expect="image").content))
    write_tile("rows", si, sj, out)


def rpg_codes():
    path = SRC / "meta" / "rpg_codes.json"
    if not path.exists():
        return {}
    return {c["code_culture"]: c["libelle_groupe_culture"] for c in json.loads(path.read_text())}


# ------------------------------------------------------------------ driver

def missing(sectors, kinds=BUILD_KINDS):
    """{kind: [tiles]} of what a sector list needs and is not on disk."""
    tiles = window(sectors)
    out = {}
    for kind in kinds:
        need = raster_tiles(kind, tiles) if kind in RASTER_KINDS else tiles
        gap = [t for t in need if not (raster_path(kind, *t) if kind in RASTER_KINDS else vector_path(kind, *t).exists())]
        if gap:
            out[kind] = gap
    return out


def ensure(sectors, kinds=BUILD_KINDS, log=print):
    """Fetch every missing tile a sector list needs. Returns the fetch report (per service: requests, MB, seconds; failures)."""
    t0 = time.time()
    gaps = missing(sectors, kinds)
    if not gaps:
        return dict(seconds=0.0, fetched={}, services={}, failures=[])
    log(f"fetching: {', '.join(f'{k} {len(v)}' for k, v in gaps.items())}")
    jobs = []                                                                   # (service, function, args)
    for kind, tiles in gaps.items():
        if kind in RASTER_KINDS:
            jobs += [("wms", fetch_raster, (kind, i, j)) for i, j in tiles]
        elif kind in WFS_LAYERS:
            jobs += [("wfs", fetch_wfs, (kind, si, sj)) for si, sj in tiles]
        elif kind in MACE_KINDS:
            jobs += [("mace", fetch_mace, (kind, tiles[k:k + 40])) for k in range(0, len(tiles), 40)]
    over = {}                                                                   # tile -> the Overpass kinds it lacks
    for kind in OVERPASS_KINDS:
        for t in gaps.get(kind, []):
            over.setdefault(t, set()).add(kind)
    light = [(t, k) for t, k in sorted(over.items()) if k == {"osm_restrictions"}]
    heavy = [(t, k) for t, k in sorted(over.items()) if k != {"osm_restrictions"}]
    if heavy and "osm_restrictions" in gaps:                                   # restrictions first, for the road build, in light queries
        light += [(t, {"osm_restrictions"}) for t, k in heavy if "osm_restrictions" in k]
        heavy = [(t, k - {"osm_restrictions"}) for t, k in heavy if k - {"osm_restrictions"}]
    jobs = [("overpass", fetch_overpass, (*t, tuple(k))) for t, k in light] + jobs + [("overpass", fetch_overpass, (*t, tuple(k))) for t, k in heavy]
    rows_now = [t for t in gaps.get("rows", []) if "rpg" not in gaps or t not in gaps["rpg"]]           # their parcels are there
    rows_later = [t for t in gaps.get("rows", []) if t not in rows_now]
    jobs += [("rows", fetch_rows, t) for t in rows_now]
    failures = []
    pools = {s: ThreadPoolExecutor(n) for s, n in SERVICE_THREADS.items()}

    def run(batch):
        futures = {pools[s].submit(fn, *args): (s, fn.__name__, args) for s, fn, args in batch}
        for k, fut in enumerate(as_completed(futures), 1):
            try:
                fut.result()
            except Exception as e:
                failures.append((futures[fut][1], futures[fut][2], str(e)[:200]))
            if k % 100 == 0 or k == len(futures):
                log(f"  {k}/{len(futures)} jobs, {len(failures)} failures, {time.time() - t0:.0f} s")
    run(jobs)
    run([("rows", fetch_rows, t) for t in rows_later if vector_path("rpg", *t).exists()])                  # after the parcels they read
    for p in pools.values():
        p.shutdown()
    report = dict(seconds=round(time.time() - t0, 1), fetched={k: len(v) for k, v in gaps.items()},
                  services={s: dict(v, mb=round(v["mb"], 1), secs=round(v["secs"], 1)) for s, v in COUNT.by.items()}, failures=failures)
    return report


def status(sectors):
    gaps = missing(sectors)
    tiles = window(sectors)
    for kind in BUILD_KINDS:
        need = raster_tiles(kind, tiles) if kind in RASTER_KINDS else tiles
        print(f"{kind:<18} {len(need) - len(gaps.get(kind, [])):>6} / {len(need)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("fetch", "status"):
        p = sub.add_parser(name)
        p.add_argument("list")
        p.add_argument("--kinds", default="", help="comma list (default: everything a build reads)")
    sub.add_parser("convert")
    a = ap.parse_args()
    if a.cmd == "convert":
        import convert_sources
        convert_sources.main()
        return
    sectors = [tuple(s) for s in json.loads(Path(a.list).read_text())["sectors"]]
    if a.cmd == "status":
        status(sectors)
        return
    kinds = tuple(a.kinds.split(",")) if a.kinds else BUILD_KINDS
    report = ensure(sectors, kinds, log=lambda m: print(m, flush=True))
    tag = Path(a.list).stem.removesuffix("_sectors")
    (BIG / "profile").mkdir(parents=True, exist_ok=True)
    (BIG / "profile" / f"fetch_{tag}.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({k: v for k, v in report.items() if k != "failures"}, indent=1), f"failures: {len(report['failures'])}", report["failures"][:5])


if __name__ == "__main__":
    sys.exit(main())
