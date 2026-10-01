"""What covers the ground, per 4 m terrain vertex, and the dressing that goes with it (sources.py gets the data).

  class       per vertex, from the most specific source that says something: OSM / BD TOPO areas that name a use (car park, vineyard,
              orchard, cemetery, pitch, farmyard, wood, scrub) > the RPG crop of the declared parcel > OSM generic land use (residential,
              farmland, meadow) > nothing (the orthophoto colour alone). OCS GE is not published for the Haute-Garonne and OSO is not
              fetched: what is left unclassed keeps the photo's colour, as before
  rows        per vertex of a crop, vine or orchard parcel, the row direction: measured on the 20 cm orthophoto (structure tensor over a
              50 m crop of the parcel) where the texture is clear, else the parcel's long axis. Measured on a sample: vines follow the long
              axis 7 / 7, orchards 12 / 14, cereals 9 / 11, but row crops only 8 / 14 (4 run across), hence the measurement
  vines       rows 2.2 m apart along the row direction, clipped 1 m inside the parcel and clear of roads; the LiDAR shrubs in a
              vineyard (the vines themselves) give way to the rows
  car parks   paved like the roads: their own surface (PARK_STEP triangles inside the outline, cut back to the road edges, with an
              entrance where the outline does not reach a road), laid
              PARK_LIFT over the terrain and smoothed, the terrain lowered under it; the road-edge ribbons stop at it
  parking     bays of 2.5 x 5 m in double rows along the car park's long axis, 6 m aisles; a car on some of them (a stable hash:
              occupancy is not in any data, PARKED_SHARE is a look choice)
  hedges      BD Haie lines where the LiDAR still sees vegetation; their height from it (HEDGE_HEIGHT), shrubs on them dropped
  tree kind   per LiDAR tree, from the vegetation zone it stands in: broadleaf, conifer, poplar, fruit (orchards), else unknown (hash)
"""
import io
import zlib

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
ACCESS_TOUCH = 3.0            # m of outline along a road under which a car park gets an entrance
ACCESS_WIDTH = 5.0            # m, the entrance's width
ACCESS_MAX = 30.0             # m: a car park further from any road is left as it is (counted)
ACCESS_OTHER = 1.0            # m: a road piece this close to another car park is its aisle, not a road to connect to
PARK_MIN_AREA = 40.0          # m²: smaller car-park pieces (slivers left between roads) are not paved
PARK_STEP = 3.0               # m between the vertices of a car park's surface
PARK_LIFT = 0.06              # m over the terrain it covers
PARK_SMOOTH = 4               # rounds of neighbour averaging of its heights
HEDGE_STEP = 4.0
BROADLEAF, CONIFER, POPLAR, FRUIT = 1, 2, 3, 4
TREE_KINDS = {"Forêt fermée de feuillus": BROADLEAF, "Forêt ouverte": BROADLEAF, "Bois": BROADLEAF, "Forêt fermée de conifères": CONIFER,
              "Peupleraie": POPLAR, "Verger": FRUIT}


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


def measure_rows(cx, cy, get):
    """(direction of the rows in radians 0 .. pi, coherence) on the 20 cm orthophoto around a Lambert-93 point. `get(params)`: the
    WMS image bytes (sources.py fetches politely)."""
    from PIL import Image
    from scipy.ndimage import gaussian_filter, sobel
    h = ROW_CROP_HALF
    params = dict(SERVICE="WMS", VERSION="1.3.0", REQUEST="GetMap", STYLES="", CRS="EPSG:2154", LAYERS="ORTHOIMAGERY.ORTHOPHOTOS",
                  BBOX=f"{cx - h},{cy - h},{cx + h},{cy + h}", WIDTH=250, HEIGHT=250, FORMAT="image/jpeg")
    img = np.asarray(Image.open(io.BytesIO(get(params))).convert("L"), float)
    gx, gy = sobel(img, 1), -sobel(img, 0)                                              # image rows run south
    jxx, jyy, jxy = (float(gaussian_filter(a, 4).mean()) for a in (gx * gx, gy * gy, gx * gy))
    gradient = 0.5 * np.arctan2(2 * jxy, jxx - jyy)
    return float((gradient + np.pi / 2) % np.pi), float(np.hypot(jxx - jyy, 2 * jxy) / (jxx + jyy + 1e-9))


def row_angle(pid, poly, measured):
    m = measured.get(pid)
    if m is not None and m[1] >= ROW_COHERENCE:
        return m[0], True
    return long_axis(poly), False


def areas(load, osm_feats, local, codes, measured):
    """[(polygon, class, rank, row angle or None, id, measured)] of every source, in local metres. `load(name)`: the sector's features
    of a ground layer (sources.py); `local(geometry)`: to local coordinates."""
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
    order = [a for a in order if a[1] != PARKING]                                       # car parks are paved surfaces of their own (parking_mesh)
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


def parking_surfaces(area_list, road_polygons, stats=None):
    """The car parks to pave: union of the PARKING areas of every source, less the road surface (`roads`: shapely), in pieces of
    PARK_MIN_AREA or more. A car park that meets a road over less than ACCESS_TOUCH gets an entrance: a strip ACCESS_WIDTH wide along
    the shortest line to a road that is not another car park's aisle (up to ACCESS_MAX away; the outlines rarely draw the way in).
    `stats`: dict counting them."""
    from shapely.geometry import LineString
    from shapely.ops import nearest_points
    parks = [a[0] for a in area_list if a[1] == PARKING]
    if not parks:
        return []
    merged = shapely.union_all(parks).buffer(0)
    road_tree = shapely.STRtree(road_polygons) if road_polygons else None

    def roads_near(geometry, reach=0.0, skip=()):
        """Union of the road polygons within `reach` of a geometry (the rest cannot change the answer there)."""
        if road_tree is None:
            return Polygon()
        near = road_tree.query(geometry, predicate="dwithin", distance=reach) if reach > 0 else road_tree.query(geometry, predicate="intersects")
        return shapely.union_all([road_polygons[k] for k in sorted(near) if k not in skip])
    roads = roads_near(merged)
    if not roads.is_empty:
        merged = merged.difference(roads)
    pieces = [g for g in getattr(merged, "geoms", [merged]) if g.geom_type == "Polygon" and g.area >= PARK_MIN_AREA]
    out = []
    for i, q in enumerate(pieces):
        nearby = roads_near(q, ACCESS_MAX + 1.0)
        if road_tree is not None and q.exterior.intersection(nearby.buffer(0.5)).length < ACCESS_TOUCH:
            others = shapely.union_all([p for j, p in enumerate(pieces) if j != i]).buffer(ACCESS_OTHER)      # a road piece touching another
            aisles = set(road_tree.query(others, predicate="intersects").tolist()) if not others.is_empty else set()   # car park is its aisle
            public = roads_near(q, ACCESS_MAX + 1.0, aisles)
            gap = q.distance(public) if not public.is_empty else np.inf
            if gap > ACCESS_MAX:                                                       # only aisles nearby: a shared aisle is the way in
                public, gap = nearby, (q.distance(nearby) if not nearby.is_empty else np.inf)
            if gap <= ACCESS_MAX:
                a, b = nearest_points(q, public)
                if stats is not None:
                    stats.setdefault("entrance_m", []).append(round(float(gap), 1))
                d = np.array(b.coords[0]) - a.coords[0]
                d = d / max(np.hypot(*d), 1e-9)
                way = LineString([np.array(a.coords[0]) - d * 2.0, np.array(b.coords[0]) + d * 1.0]).buffer(ACCESS_WIDTH / 2, cap_style="flat")
                joined = q.union(way).difference(roads_near(q.union(way)))
                q = max(getattr(joined, "geoms", [joined]), key=lambda g: g.area)
                if stats is not None:
                    stats["entrances"] = stats.get("entrances", 0) + 1
            elif stats is not None:
                stats["unreached"] = stats.get("unreached", 0) + 1
        out.append(q.simplify(0.2))
    return out


def parking_mesh(poly, height):
    """Surface of a car park: vertices (n, 3) (x, north, height) every PARK_STEP on its outline and inside, triangles (t, 3) wound
    clockwise seen from above. `height(xy)`: the terrain it is laid over."""
    from scipy.spatial import Delaunay
    rings = [np.array(poly.exterior.coords)] + [np.array(h.coords) for h in poly.interiors]
    edge = []
    for r in rings:
        for a, b in zip(r[:-1], r[1:]):
            n = max(int(np.ceil(np.hypot(*(b - a)) / PARK_STEP)), 1)
            edge.append(a + (b - a) * (np.arange(n) / n)[:, None])
    edge = np.vstack(edge)
    x0, z0, x1, z1 = poly.bounds
    gx, gz = np.meshgrid(np.arange(x0 + PARK_STEP / 2, x1, PARK_STEP), np.arange(z0 + PARK_STEP / 2, z1, PARK_STEP))
    inner = np.c_[gx.ravel(), gz.ravel()]
    inner = inner[shapely.contains_xy(poly.buffer(-PARK_STEP * 0.4), inner[:, 0], inner[:, 1])] if len(inner) else inner
    xy = np.vstack([edge, inner])
    if len(xy) < 3:
        return np.zeros((0, 3)), np.zeros((0, 3), int)
    tri = Delaunay(xy).simplices
    mid = xy[tri].mean(axis=1)
    keep = shapely.contains_xy(poly.buffer(0.05), mid[:, 0], mid[:, 1])
    for k in range(3):                                                          # an edge midpoint outside: the triangle spans a notch
        m = 0.5 * (xy[tri[:, k]] + xy[tri[:, (k + 1) % 3]])
        keep &= shapely.contains_xy(poly.buffer(0.05), m[:, 0], m[:, 1])
    tri = tri[keep]
    y = height(xy) + PARK_LIFT
    nbr = [[] for _ in range(len(xy))]
    for a, b, c in tri:
        nbr[a] += [b, c]; nbr[b] += [a, c]; nbr[c] += [a, b]
    for _ in range(PARK_SMOOTH):                                                # a paved surface is smooth; it never sinks under the ground
        y = np.maximum(np.array([y[i] if not n else 0.5 * y[i] + 0.5 * y[n].mean() for i, n in enumerate(nbr)]), height(xy) + PARK_LIFT * 0.5)
    a, b, c = xy[tri[:, 0]], xy[tri[:, 1]], xy[tri[:, 2]]
    ccw = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]) > 0
    tri[ccw] = tri[ccw][:, [0, 2, 1]]
    return np.c_[xy, y], tri


def plan_area(tris):
    """Plan area of triangles (t, 3, >= 2): x and north first."""
    a, b, c = tris[:, 0, :2], tris[:, 1, :2], tris[:, 2, :2]
    return float(np.abs((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])).sum()) / 2
