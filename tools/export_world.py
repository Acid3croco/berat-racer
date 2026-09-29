"""Turn IGN LiDAR HD rasters + BD TOPO vectors into compact game data for Unity.

Local coordinates: x = east, z = north (metres from the Berat centre), y = elevation (m, NGF).
Outputs to unity/Assets/StreamingAssets/berat/.
"""
import json, struct, hashlib
from pathlib import Path
import numpy as np
from scipy.ndimage import map_coordinates, uniform_filter1d, gaussian_filter, maximum_filter, uniform_filter
from scipy.spatial import cKDTree
from shapely.geometry import shape, Polygon
import shapely
from rasterio import features
from rasterio.transform import from_origin
from fetch import CX, CY, HALF

OUT = Path("unity/Assets/StreamingAssets/berat"); OUT.mkdir(parents=True, exist_ok=True)
SIZE = 2 * HALF
CELL = 4                       # terrain vertex spacing (m)
NV = SIZE // CELL              # 1600 x 1600 vertices

mnt = np.load("data/mnt.npy"); mnh = np.load("data/mnh.npy"); ortho = np.load("data/ortho.npy")

def to_local(x, y): return x - CX, y - CY

def sample(arr, x, z, scale=1.0):
    """Bilinear sample of a north-up raster at local coords. scale = metres per pixel."""
    col = (x + HALF) / scale - 0.5
    row = (HALF - z) / scale - 0.5
    return map_coordinates(arr, [row, col], order=1, mode="nearest")

# ---------------------------------------------------------------- roads
HW_BY_NATURE = {"Rond-point": 3.0, "Chemin": 1.8, "Route empierrée": 1.9, "Sentier": 0.7}
HW_BY_IMPORTANCE = {"1": 3.6, "2": 3.5, "3": 3.1, "4": 2.9, "5": 2.4, "6": 2.2}

def road_halfwidth(p):
    w = p.get("largeur_de_chaussee")
    if isinstance(w, (int, float)) and w > 0: return max(w, 2.5) / 2
    if p["nature"] in HW_BY_NATURE: return HW_BY_NATURE[p["nature"]]
    return HW_BY_IMPORTANCE.get(str(p.get("importance")), 2.6)

def densify(xy, step=2.0):
    d = np.r_[0, np.cumsum(np.hypot(*np.diff(xy, axis=0).T))]
    if d[-1] < 1e-6: return None
    t = np.arange(0, d[-1], step); t = np.r_[t, d[-1]]
    return np.c_[np.interp(t, d, xy[:, 0]), np.interp(t, d, xy[:, 1])]

def smooth_pinned(y, win=9):
    if len(y) < 3: return y
    w = min(win, len(y) | 1)
    pad = w // 2
    ext = np.r_[np.full(pad, y[0]), y, np.full(pad, y[-1])]
    s = uniform_filter1d(ext, w, mode="nearest")[pad:pad + len(y)]
    s[0], s[-1] = y[0], y[-1]
    return s

roads, road_pts = [], []       # road_pts: x, z, y, halfwidth for terrain carving
for f in json.load(open("data/roads.geojson"))["features"]:
    p = f["properties"]
    if p["nature"] == "Sentier": continue                     # footpaths are not drivable
    geoms = f["geometry"]["coordinates"] if f["geometry"]["type"] == "MultiLineString" else [f["geometry"]["coordinates"]]
    for line in geoms:
        xy = np.array([to_local(c[0], c[1]) for c in line])
        xy = densify(xy)
        if xy is None: continue
        y = sample(mnt, xy[:, 0], xy[:, 1])
        pos = str(p.get("position_par_rapport_au_sol") or "0")
        bridge = pos not in ("0", "") and not pos.startswith("-")   # tunnels are treated as ground roads
        if bridge: y = np.linspace(y[0], y[-1], len(y))            # bridge deck: straight between ends
        else: y = smooth_pinned(y)
        hw = road_halfwidth(p)
        dirt = p["nature"] in ("Chemin", "Route empierrée")
        name = next((p[k] for k in ("nom_1_gauche", "nom_1_droite") if p.get(k)), "")
        roads.append(dict(hw=round(hw, 2), dirt=dirt, bridge=bridge, name=name,
                          imp=str(p.get("importance")),
                          pts=np.round(np.c_[xy[:, 0], y, xy[:, 1]], 2).ravel().tolist()))
        if not bridge: road_pts.append(np.c_[xy[:, 0], xy[:, 1], y, np.full(len(y), hw)])
road_pts = np.vstack(road_pts)
print("roads", len(roads), "points", len(road_pts))

# ---------------------------------------------------------------- terrain
blk = mnt.reshape(NV, CELL, NV, CELL).mean(axis=(1, 3))                # row 0 = north
tx = -HALF + CELL * np.arange(NV) + CELL / 2                           # vertex x
tz = HALF - CELL * np.arange(NV) - CELL / 2                            # vertex z for each row
gx, gz = np.meshgrid(tx, tz)
tree = cKDTree(road_pts[:, :2])
d, idx = tree.query(np.c_[gx.ravel(), gz.ravel()], distance_upper_bound=16.0)
hit = np.isfinite(d)
hwv = np.where(hit, road_pts[np.minimum(idx, len(road_pts) - 1), 3], 0)
yv = np.where(hit, road_pts[np.minimum(idx, len(road_pts) - 1), 2], 0)
flat, fade = hwv + 3.0, 6.0
t = np.clip((d - flat) / fade, 0, 1); w = np.where(hit, 1 - t * t * (3 - 2 * t), 0)   # smoothstep
h = blk.ravel() * (1 - w) + yv * w
height = h.reshape(NV, NV)[::-1]                                        # row 0 = south (z increasing)
with open(OUT / "terrain.bin", "wb") as fo:
    fo.write(struct.pack("<iffff", NV, CELL, -HALF + CELL / 2, -HALF + CELL / 2, 0.0))
    fo.write(height.astype("<f4").tobytes())

# ground colour from orthophoto (2 m/px -> 4 m vertices).
# Under trees / roofs the orthophoto shows canopy, roofs and cast shadows, not ground: replace those cells with the nearest bare-ground colour.
from scipy.ndimage import distance_transform_edt
col = ortho.reshape(NV, 2, NV, 2, 3).mean(axis=(1, 3))
covered = mnh.reshape(NV, CELL, NV, CELL).max(axis=(1, 3)) > 1.3
iy, ix = distance_transform_edt(covered, return_distances=False, return_indices=True)
col = col[iy, ix]
col = gaussian_filter(col, sigma=(1.2, 1.2, 0))
grey = col.mean(axis=2, keepdims=True)
col = (grey + (col - grey) * 1.5) * np.array([1.10, 1.16, 0.90])           # warm + saturate: fields read green / ochre
col = np.clip(np.maximum(col * 1.12, 82), 0, 255)                                   # no black shadow blobs
(OUT / "terrain_colors.bin").write_bytes(col[::-1].astype(np.uint8).tobytes())
print("terrain", height.shape, height.min(), height.max())

# ---------------------------------------------------------------- buildings
WALLS = [(232, 222, 200), (222, 190, 170), (238, 236, 230), (214, 190, 140), (200, 198, 192), (205, 145, 122)]

def roof_colour(poly):
    minx, minz, maxx, maxz = poly.bounds
    xs, zs = np.meshgrid(np.arange(minx, maxx, 2.0), np.arange(minz, maxz, 2.0))
    m = shapely.contains_xy(poly, xs.ravel(), zs.ravel())
    px, pz = (xs.ravel()[m], zs.ravel()[m]) if m.any() else ([poly.centroid.x], [poly.centroid.y])
    c = np.array([sample(ortho[..., k].astype(np.float32), np.asarray(px), np.asarray(pz), 2.0) for k in range(3)])
    return np.clip(c.mean(axis=1) * 1.1, 0, 255).astype(int).tolist()

def bd_kind(p, area):
    nat, u1 = p.get("nature"), p.get("usage_1")
    if nat == "Eglise": return "church"
    if nat == "Chapelle": return "chapel"
    if nat == "Silo": return "silo"
    if nat == "Serre": return "greenhouse"
    if u1 == "Religieux": return "chapel"
    if u1 == "Commercial et services": return "shop"
    if u1 == "Industriel" or nat == "Industriel, agricole ou commercial": return "barn" if u1 == "Agricole" else "industrial"
    if u1 == "Agricole": return "barn"
    if u1 == "Annexe" or area < 35: return "shed"
    return "house"

road_tree = cKDTree(road_pts[:, :2])
buildings, bpolys = [], []
for f in json.load(open("data/buildings.geojson"))["features"]:
    p = f["properties"]
    g = f["geometry"]
    polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
    for rings in polys:
        poly = Polygon([to_local(c[0], c[1]) for c in rings[0]]).simplify(0.3)
        if poly.is_empty or poly.area < 6: continue
        if poly.geom_type != "Polygon": poly = max(poly.geoms, key=lambda q: q.area)
        poly = shapely.geometry.polygon.orient(poly, 1.0)
        ring = np.array(poly.exterior.coords)[:-1]
        gx0, gz0, gx1, gz1 = poly.buffer(1.0).bounds                                   # lowest terrain anywhere under/around the footprint
        gxs, gzs = np.meshgrid(np.arange(gx0, gx1 + 1e-6, 0.75), np.arange(gz0, gz1 + 1e-6, 0.75))
        inside = shapely.contains_xy(poly.buffer(1.0), gxs.ravel(), gzs.ravel())
        gpts = np.c_[gxs.ravel()[inside], gzs.ravel()[inside]]
        ground = float(min(sample(mnt, ring[:, 0], ring[:, 1]).min(), sample(mnt, gpts[:, 0], gpts[:, 1]).min() if len(gpts) else 1e9))
        c = poly.centroid
        mnh_c = float(sample(mnh, np.array([c.x]), np.array([c.y]))[0])
        inner = poly.buffer(-1.2)
        pts = np.array(inner.exterior.coords) if (not inner.is_empty and inner.geom_type == "Polygon") else ring
        mnh_edge = float(sample(mnh, pts[:, 0], pts[:, 1]).mean())
        hh = p.get("hauteur")
        wall = float(hh) if isinstance(hh, (int, float)) and hh > 0 else (
            3.0 * p["nombre_d_etages"] if isinstance(p.get("nombre_d_etages"), (int, float)) else max(3.0, min(mnh_edge, 12.0)))
        tower = []
        if bd_kind(p, poly.area) in ("church", "chapel"):          # eaves from the LiDAR perimeter height, tower = highest LiDAR point inside the footprint
            wall = float(np.clip(mnh_edge, 5.0, 10.0))
            ins = shapely.contains_xy(poly, gxs.ravel(), gzs.ravel())
            if ins.any():
                hx, hz = gxs.ravel()[ins], gzs.ravel()[ins]
                hv = sample(mnh, hx, hz); k = int(hv.argmax())
                if hv[k] > wall + 2.5: tower = [round(float(hx[k]), 2), round(float(hz[k]), 2), round(float(hv[k]), 2)]
        mrr = poly.minimum_rotated_rectangle
        rect = np.array(mrr.exterior.coords)[:4]
        e1, e2 = rect[1] - rect[0], rect[2] - rect[1]
        L, Wd = np.linalg.norm(e1), np.linalg.norm(e2)
        axis, long_, short = (e1 / L, L, Wd) if L >= Wd else (e2 / Wd, Wd, L)
        kind = bd_kind(p, poly.area)
        pitched = (mnh_c - mnh_edge) > 0.8 and poly.area / mrr.area > 0.7 and poly.area < 900 and short > 3
        rise = float(np.clip(mnh_c - wall, 0.8, 5.0)) if pitched else 0.0
        if kind in ("church", "chapel") and poly.area / mrr.area > 0.6 and short > 4:       # church naves are always gabled
            pitched, rise = True, max(rise, float(short) * 0.32)
        seed = int(hashlib.md5(f["properties"]["cleabs"].encode()).hexdigest()[:8], 16)
        # front edge = the footprint edge whose midpoint is nearest a road (door / shopfront goes there)
        mids = (ring + np.roll(ring, -1, axis=0)) / 2
        fe = int(road_tree.query(mids)[0].argmin())
        buildings.append(dict(k=kind, n="", fe=fe, tw=tower, p=np.round(ring, 2).ravel().tolist(), b=round(ground - 0.8, 2), h=round(wall + 0.8, 2), r=round(rise, 2),
                              rc=[round(float(v), 2) for v in (*mrr.centroid.coords[0], *axis, long_, short)] if pitched else [],
                              c=roof_colour(poly), w=list(WALLS[seed % len(WALLS)])))
        bpolys.append(poly)
# ---- OSM points of interest -> building type + name (pharmacy, town hall, church, shops...)
from pyproj import Transformer
from shapely.geometry import Point
from shapely.strtree import STRtree
def poi_kind(t):
    a, sh = t.get("amenity"), t.get("shop")
    if a == "place_of_worship": return "church"
    if a == "pharmacy": return "pharmacy"
    if a == "townhall": return "townhall"
    if a in ("school", "kindergarten"): return "school"
    if a == "post_office": return "post"
    if a in ("restaurant", "fast_food"): return "restaurant"
    if a in ("cafe", "bar"): return "bar"
    if a in ("library",): return "library"
    if a in ("community_centre", "marketplace"): return "hall"
    if a in ("doctors", "dentist", "clinic"): return "clinic"
    if sh == "bakery": return "bakery"
    if sh in ("convenience", "supermarket"): return "grocery"
    if sh: return "shop"
    return None
try:
    pois = json.load(open("data/osm_poi.json"))["elements"]
except Exception:
    pois = []
tr = Transformer.from_crs(4326, 2154, always_xy=True)
strtree = STRtree(bpolys)
assigned = 0
for e in pois:
    lat, lon = (e.get("lat"), e.get("lon")) if e["type"] == "node" else (e["center"]["lat"], e["center"]["lon"])
    kind = poi_kind(e["tags"])
    if not kind: continue
    x, y = tr.transform(lon, lat); pt = Point(x - CX, y - CY)
    hit = [i for i in strtree.query(pt) if bpolys[i].contains(pt)]
    if not hit:
        j = int(strtree.nearest(pt))
        if bpolys[j].distance(pt) > 15: continue
        hit = [j]
    b = buildings[hit[0]]
    if kind == "church" and b["k"] == "chapel": kind = "chapel"     # keep the small one a chapel
    b["k"] = kind; b["n"] = e["tags"].get("name", ""); assigned += 1
# a church can be split into several BD TOPO outlines: keep only the tallest LiDAR tower within 15 m of each other
towers = [(b["tw"][2], i) for i, b in enumerate(buildings) if b.get("tw")]
for h_i, i in sorted(towers, reverse=True):
    if not buildings[i]["tw"]: continue
    for h_j, j in towers:
        if j != i and buildings[j]["tw"] and h_j < h_i and np.hypot(buildings[i]["tw"][0] - buildings[j]["tw"][0], buildings[i]["tw"][1] - buildings[j]["tw"][1]) < 15:
            buildings[j]["tw"] = []
import collections
print("kinds", dict(collections.Counter(b["k"] for b in buildings)), "| OSM POIs matched:", assigned, "of", len(pois))
json.dump(dict(items=buildings), open(OUT / "buildings.json", "w"), separators=(",", ":"))
json.dump(dict(items=roads), open(OUT / "roads.json", "w"), separators=(",", ":"))
print("buildings", len(buildings), "pitched", sum(1 for b in buildings if b["r"] > 0))

# ---------------------------------------------------------------- trees (LiDAR canopy height maxima)
tr = from_origin(-HALF, HALF, 1.0, 1.0)
bmask = features.rasterize([(q.buffer(2.0), 1) for q in bpolys], out_shape=mnh.shape, transform=tr, dtype=np.uint8).astype(bool)
sm = gaussian_filter(mnh, 1.0)
peak = (sm == maximum_filter(sm, size=7)) & (sm >= 2.5) & (sm <= 32) & ~bmask
rr, cc = np.nonzero(peak)
tx_, tz_ = cc + 0.5 - HALF, HALF - rr - 0.5
dd, ii = cKDTree(road_pts[:, :2]).query(np.c_[tx_, tz_], distance_upper_bound=10.0)
keep = ~(np.isfinite(dd) & (dd < road_pts[np.minimum(ii, len(road_pts) - 1), 3] + 1.8))
tx_, tz_, th = tx_[keep], tz_[keep], sm[rr, cc][keep]
ty = sample(mnt, tx_, tz_)
(OUT / "trees.bin").write_bytes(np.c_[tx_, ty, tz_, th].astype("<f4").tobytes())
print("trees", len(th))

# ---------------------------------------------------------------- spawn: main road nearest the village centre
best = None
for r in roads:
    if r["dirt"] or r["bridge"]: continue
    a = np.array(r["pts"]).reshape(-1, 3)
    dist = np.hypot(a[:, 0], a[:, 2]); k = int(dist.argmin())
    score = dist[k] + (0 if r["imp"] in ("1", "2", "3", "4") else 40)
    if k + 1 < len(a) and (best is None or score < best[0]): best = (score, a[k], a[k + 1], r["name"])
_, p0, p1, nm = best
heading = float(np.degrees(np.arctan2(p1[0] - p0[0], p1[2] - p0[2])))
json.dump(dict(x=float(p0[0]), y=float(p0[1]) + 1.0, z=float(p0[2]), heading=heading, road=nm), open(OUT / "spawn.json", "w"))
print("spawn", p0, heading, nm)
