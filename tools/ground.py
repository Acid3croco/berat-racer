"""What covers the ground, per 4 m terrain vertex, and the dressing that goes with it (fetch_ground.py gets the data).

  class       per vertex, from the most specific source that says something: OSM / BD TOPO areas that name a use (car park, vineyard,
              orchard, cemetery, pitch, farmyard, wood, scrub) > the RPG crop of the declared parcel > OSM generic land use (residential,
              farmland, meadow) > nothing (the orthophoto colour alone). OCS GE is not published for the Haute-Garonne and OSO is not
              fetched: what is left unclassed keeps the photo's colour, as before
  rows        per vertex of a crop, vine or orchard parcel, the row direction: measured on the 20 cm orthophoto (structure tensor over a
              50 m crop of the parcel) where the texture is clear, else the parcel's long axis. Measured on a sample: vines follow the long
              axis 7 / 7, orchards 12 / 14, cereals 9 / 11, but row crops only 8 / 14 (4 run across), hence the measurement
  vines       rows 2.2 m apart along the row direction, clipped 1 m inside the parcel and clear of roads; the LiDAR shrubs in a
              vineyard (the vines themselves) give way to the rows
  parking     bays of 2.5 x 5 m in double rows along the car park's long axis, 6 m aisles; a car on some of them (a stable hash:
              occupancy is not in any data, PARKED_SHARE is a look choice)
  hedges      BD Haie lines where the LiDAR still sees vegetation; their height from it (HEDGE_HEIGHT), shrubs on them dropped
  tree kind   per LiDAR tree, from the vegetation zone it stands in: broadleaf, conifer, poplar, fruit (orchards), else unknown (hash)
"""
import gzip
import io
import json
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import shapely
from rasterio import features
from rasterio.transform import from_origin
from shapely.geometry import LineString, Polygon, shape

NONE, MEADOW, CEREAL, ROW_CROP, VINEYARD, ORCHARD, FALLOW, PARKING, GARDEN, YARD, FOREST, CEMETERY, PITCH, SCRUB = range(14)
NAMES = ["none", "meadow", "cereal", "row crop", "vineyard", "orchard", "fallow", "parking", "garden", "yard", "forest", "cemetery", "pitch", "scrub"]
ROWED = {CEREAL, ROW_CROP, VINEYARD, ORCHARD}
GENERIC, RPG, SPECIFIC = 1, 2, 3                       # precedence: the higher overwrites

RPG_GROUPS = {"Maïs grain et ensilage": ROW_CROP, "Tournesol": ROW_CROP, "Autres oléagineux": ROW_CROP, "Légumes ou fleurs": ROW_CROP,
              "Blé tendre": CEREAL, "Orge": CEREAL, "Autres céréales": CEREAL, "Colza": CEREAL, "Protéagineux": CEREAL, "Plantes à fibres": CEREAL,
              "Légumineuses à grains": CEREAL, "Riz": CEREAL,
              "Prairies permanentes": MEADOW, "Prairies temporaires": MEADOW, "Fourrage": MEADOW, "Estives et landes": SCRUB,
              "Gel (surfaces gelées sans production)": FALLOW, "Vignes": VINEYARD, "Vergers": ORCHARD, "Fruits à coque": ORCHARD, "Oliviers": ORCHARD}
RPG_CODES = {"SOG": ROW_CROP}                          # sorghum is sown in rows, its group ("other cereals") is not
BD_VEGETATION = {"Vigne": VINEYARD, "Verger": ORCHARD, "Forêt fermée de feuillus": FOREST, "Forêt fermée de conifères": FOREST,
                 "Forêt fermée mixte": FOREST, "Forêt ouverte": FOREST, "Bois": FOREST, "Peupleraie": FOREST, "Lande ligneuse": SCRUB}
BD_TRANSPORT = {"Parking": PARKING, "Aire de repos ou de service": PARKING, "Service dédié aux véhicules": YARD}
OSM_SPECIFIC = {("amenity", "parking"): PARKING, ("landuse", "vineyard"): VINEYARD, ("landuse", "orchard"): ORCHARD, ("landuse", "cemetery"): CEMETERY,
                ("leisure", "pitch"): PITCH, ("landuse", "farmyard"): YARD, ("landuse", "industrial"): YARD, ("landuse", "retail"): YARD,
                ("landuse", "forest"): FOREST, ("natural", "wood"): FOREST, ("natural", "scrub"): SCRUB, ("natural", "heath"): SCRUB}
OSM_GENERIC = {("landuse", "residential"): GARDEN, ("landuse", "meadow"): MEADOW, ("landuse", "grass"): MEADOW, ("leisure", "park"): MEADOW,
               ("leisure", "garden"): GARDEN}
PARKING_KINDS = {None, "surface"}                       # OSM parking=*: bays are drawn on these, not on street-side, underground or multi-storey

ROW_SAMPLE_AREA = 3000.0     # m²: smaller parcels take the long axis without a measurement
ROW_COHERENCE = 0.3          # structure-tensor coherence over which the orthophoto's direction is used
ROW_CROP_HALF = 25.0         # m: half side of the orthophoto crop
VINE_SPACING = 2.2
VINE_PIECE = 20.0            # m: rows are cut in pieces so each lands in one chunk
VINE_CLEARANCE = 1.5         # m from a road corridor or a building
BAY_WIDTH, BAY_DEPTH, AISLE = 2.5, 5.0, 6.0
PARKED_SHARE = 0.45
HEDGE_MIN, HEDGE_HEIGHT = 0.6, (1.0, 3.0)
HEDGE_STEP = 4.0
WMS = "https://data.geopf.fr/wms-r/wms"
BROADLEAF, CONIFER, POPLAR, FRUIT = 1, 2, 3, 4
TREE_KINDS = {"Forêt fermée de feuillus": BROADLEAF, "Forêt ouverte": BROADLEAF, "Bois": BROADLEAF, "Forêt fermée de conifères": CONIFER,
              "Peupleraie": POPLAR, "Verger": FRUIT}


def rpg_codes(vec_dir):
    return {c["code_culture"]: c["libelle_groupe_culture"] for c in json.loads((Path(vec_dir) / "rpg_codes.json").read_text())}


def rpg_class(props, codes):
    code = props.get("code_cultu")
    return RPG_CODES.get(code) or RPG_GROUPS.get(codes.get(code), NONE)


def osm_class(tags):
    for table, rank in ((OSM_SPECIFIC, SPECIFIC), (OSM_GENERIC, GENERIC)):
        for (key, value), cls in table.items():
            if tags.get(key) == value:
                if cls == PARKING and tags.get("parking") not in PARKING_KINDS:
                    return None
                return cls, rank
    return None


def long_axis(poly):
    """Direction (radians, 0 .. pi) of the long side of the parcel's minimum rectangle."""
    r = np.array(poly.minimum_rotated_rectangle.exterior.coords)[:4]
    e1, e2 = r[1] - r[0], r[2] - r[1]
    e = e1 if np.hypot(*e1) >= np.hypot(*e2) else e2
    return float(np.arctan2(e[1], e[0]) % np.pi)


def measure_rows(cx, cy):
    """(direction of the rows in radians 0 .. pi, coherence) on the 20 cm orthophoto around a Lambert-93 point."""
    import requests
    from PIL import Image
    from scipy.ndimage import gaussian_filter, sobel
    h = ROW_CROP_HALF
    params = dict(SERVICE="WMS", VERSION="1.3.0", REQUEST="GetMap", STYLES="", CRS="EPSG:2154", LAYERS="ORTHOIMAGERY.ORTHOPHOTOS",
                  BBOX=f"{cx - h},{cy - h},{cx + h},{cy + h}", WIDTH=250, HEIGHT=250, FORMAT="image/jpeg")
    for attempt in range(4):
        try:
            reply = requests.get(WMS, params=params, timeout=60)
            reply.raise_for_status()
            img = np.asarray(Image.open(io.BytesIO(reply.content)).convert("L"), float)
            break
        except Exception:
            if attempt == 3:
                raise
    gx, gy = sobel(img, 1), -sobel(img, 0)                                              # image rows run south
    jxx, jyy, jxy = (float(gaussian_filter(a, 4).mean()) for a in (gx * gx, gy * gy, gx * gy))
    gradient = 0.5 * np.arctan2(2 * jxy, jxx - jyy)
    return float((gradient + np.pi / 2) % np.pi), float(np.hypot(jxx - jyy, 2 * jxy) / (jxx + jyy + 1e-9))


def rows_path(big, tag):
    return Path(big) / "vec" / f"rows_{tag}.json.gz"


def measure_all(parcels, path, threads=6):
    """Measure the rows of every rowed parcel of ROW_SAMPLE_AREA or more not measured yet; cache {id: [radians, coherence]}."""
    from fetch import CX, CY
    done = json.loads(gzip.open(path).read()) if path.exists() else {}
    todo = [(pid, poly) for pid, poly in parcels if pid not in done and poly.area >= ROW_SAMPLE_AREA]
    def one(item):
        pid, poly = item
        inner = poly.buffer(-10)
        p = (inner if not inner.is_empty else poly).representative_point()
        return pid, measure_rows(p.x + CX, p.y + CY)
    with ThreadPoolExecutor(threads) as pool:
        for pid, value in pool.map(one, todo):
            done[pid] = list(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as fh:
        json.dump(done, fh)
    return done


def row_angle(pid, poly, measured):
    m = measured.get(pid)
    if m is not None and m[1] >= ROW_COHERENCE:
        return m[0], True
    return long_axis(poly), False


def areas(load, osm_feats, local, codes, measured):
    """[(polygon, class, rank, row angle or None, id, measured)] of every source, in local metres. `load(name)`: the sector's features
    of a fetch_ground layer; `local(geometry)`: to local coordinates."""
    out = []
    def polys(g):
        return [g] if g.geom_type == "Polygon" else [q for q in getattr(g, "geoms", []) if q.geom_type == "Polygon"]
    for f in load("rpg"):
        cls = rpg_class(f["properties"], codes)
        if cls == NONE:
            continue
        for q in polys(local(shape(f["geometry"]))):
            angle, ok = row_angle(f["id"], q, measured) if cls in ROWED else (None, False)
            out.append((q, cls, RPG, angle, f["id"], ok))
    for f in load("vegetation"):
        cls = BD_VEGETATION.get(f["properties"].get("nature"))
        if cls is not None:
            for q in polys(local(shape(f["geometry"]))):
                out.append((q, cls, SPECIFIC, long_axis(q) if cls in ROWED else None, f["properties"]["cleabs"], False))
    for f in load("transport"):
        cls = BD_TRANSPORT.get(f["properties"].get("nature"))
        if cls is not None:
            for q in polys(local(shape(f["geometry"]))):
                out.append((q, cls, SPECIFIC, long_axis(q), f["properties"]["cleabs"], False))
    for f in osm_feats:
        if f["properties"].get("osm_kind") != "area":
            continue
        hit = osm_class(f["properties"])
        if hit is not None:
            for q in polys(local(shape(f["geometry"]))):
                out.append((q, hit[0], hit[1], long_axis(q) if hit[0] in ROWED | {PARKING} else None, f["id"], False))
    return out


def rasterize(area_list, x0, z0, nv, cell):
    """Class and row direction (byte: 1 + 254 * angle / pi, 0 none) per vertex of an nv x nv grid from (x0, z0), row = z (south first)."""
    transform = from_origin(x0 - cell / 2, z0 + (nv - 0.5) * cell, cell, cell)             # one pixel centred on each vertex, north first
    order = sorted(area_list, key=lambda a: (a[2], -a[0].area))                         # higher rank last; smaller areas over larger ones
    cls = features.rasterize([(a[0], a[1]) for a in order], out_shape=(nv, nv), transform=transform, dtype=np.uint8, fill=NONE) if order else np.zeros((nv, nv), np.uint8)
    rows = [(a[0], 0 if a[3] is None else 1 + int(round(254 * a[3] / np.pi))) for a in order]
    ang = features.rasterize(rows, out_shape=(nv, nv), transform=transform, dtype=np.uint8, fill=0) if order else np.zeros((nv, nv), np.uint8)
    return cls[::-1].copy(), ang[::-1].copy()


def vine_rows(area_list, clip, blocked):
    """Vine rows (x0, z0, x1, z1) of the vineyards inside `clip`, in VINE_PIECE pieces, VINE_CLEARANCE clear of `blocked(polygon)`
    (the roads and buildings near it)."""
    out, done = [], Polygon()
    vineyards = sorted((a for a in area_list if a[1] == VINEYARD and a[0].intersects(clip)), key=lambda a: (-a[2], -a[5]))      # where sources overlap, the
    for poly, cls, _, angle, _, _ in vineyards:                                                                               # first (specific, measured) draws
        inner = poly.buffer(-1.0).intersection(clip).difference(done).difference(blocked(poly).buffer(VINE_CLEARANCE))
        done = done.union(poly)
        if inner.is_empty:
            continue
        d, n = np.array([np.cos(angle), np.sin(angle)]), np.array([-np.sin(angle), np.cos(angle)])
        c = np.array(inner.centroid.coords[0]); reach = np.hypot(*(np.array(inner.bounds[2:]) - inner.bounds[:2]))
        for k in np.arange(-reach / 2, reach / 2, VINE_SPACING):
            line = LineString([c + n * k - d * reach, c + n * k + d * reach]).intersection(inner)
            for seg in getattr(line, "geoms", [line]):
                if seg.is_empty or seg.geom_type != "LineString" or seg.length < 2:
                    continue
                pieces = max(1, int(np.ceil(seg.length / VINE_PIECE)))
                for j in range(pieces):
                    a, b = seg.interpolate(j / pieces, normalized=True), seg.interpolate((j + 1) / pieces, normalized=True)
                    out.append((a.x, a.y, b.x, b.y))
    return np.array(out, "<f4").reshape(-1, 4)


def parking_bays(area_list, clip, blocked):
    """Bays (x, z, heading, car) of every car park inside `clip`: double rows along the long axis, AISLE apart; a bay is kept when its
    rectangle lies inside the car park and clear of `blocked(polygon)` (the roads and buildings near it). car: 1 with a parked car
    (stable hash), else 0."""
    out = []
    for poly, cls, _, angle, pid, _ in area_list:
        if cls != PARKING or not clip.contains(poly.representative_point()):
            continue
        d, n = np.array([np.cos(angle), np.sin(angle)]), np.array([-np.sin(angle), np.cos(angle)])
        pts = np.array(poly.exterior.coords)
        along, across = pts @ d, pts @ n
        room = poly.buffer(-0.3).difference(blocked(poly))
        k = 0
        for row in np.arange(across.min(), across.max() - BAY_DEPTH, 2 * BAY_DEPTH + AISLE):
            for side, base in ((0, row), (1, row + BAY_DEPTH)):
                for t in np.arange(along.min(), along.max() - BAY_WIDTH, BAY_WIDTH):
                    c = d * (t + BAY_WIDTH / 2) + n * (base + BAY_DEPTH / 2)
                    rect = Polygon([c + d * sx * BAY_WIDTH / 2 + n * sy * BAY_DEPTH / 2 for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))])
                    if not room.contains(rect):
                        continue
                    heading = angle + (np.pi / 2 if side == 0 else -np.pi / 2)              # cars face the aisle between the two rows
                    car = (zlib.crc32(f"{pid}:{k}".encode()) % 1000) < PARKED_SHARE * 1000
                    out.append((c[0], c[1], heading, float(car)))
                    k += 1
    return np.array(out, "<f4").reshape(-1, 4)


def hedges(lines, clip, canopy):
    """Hedge polylines [(height, xy (n, 2))] of BD Haie inside `clip`, where `canopy(xy)` (LiDAR height above ground) still sees them;
    split where it does not."""
    out = []
    for line in lines:
        g = line.intersection(clip)
        for seg in getattr(g, "geoms", [g]):
            if seg.is_empty or seg.geom_type != "LineString" or seg.length < HEDGE_STEP:
                continue
            n = int(np.ceil(seg.length / HEDGE_STEP)) + 1
            xy = np.array([seg.interpolate(t, normalized=True).coords[0] for t in np.linspace(0, 1, n)])
            h = canopy(xy)
            alive = h >= HEDGE_MIN
            for run in np.split(np.arange(n), np.flatnonzero(np.diff(alive.astype(int))) + 1):
                if alive[run[0]] and len(run) >= 2:
                    out.append((float(np.clip(np.median(h[run]), *HEDGE_HEIGHT)), xy[run]))
    return out


def tree_kinds(xz, vegetation, area_list):
    """Per tree: kind from the BD TOPO vegetation zone it stands in (orchards of any source count as fruit), 0 when none says."""
    kinds = np.zeros(len(xz), np.uint8)
    if not len(xz):
        return kinds
    zones = [(q, TREE_KINDS[nat]) for q, nat in vegetation if nat in TREE_KINDS] + [(a[0], FRUIT) for a in area_list if a[1] == ORCHARD]
    for q, kind in zones:
        inside = shapely.contains_xy(q, xz[:, 0], xz[:, 1])
        kinds[inside] = kind
    return kinds
