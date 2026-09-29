"""Turn the downloaded LiDAR HD tiles + BD TOPO vectors (tools/fetch_big.py, tools/fetch_vectors.py) into streamable chunk files.

The 32 x 32 km area is cut into 10 x 10 sectors of 3.2 km. Each sector is processed independently (in parallel) together with a
240 m margin, so everything computed near a sector border sees the same neighbourhood from both sides and the seams match.
Every sector writes its 8 x 8 chunks of 400 m:

  m_{ci}_{cj}.bin.gz   "mid" data: terrain (4 m grid), roads, water, buildings          (loaded out to ~5 km)
  n_{ci}_{cj}.bin.gz   "near" data: trees + shrubs                                     (loaded out to ~1.6 km)

and returns a 64 m far-terrain patch; build_world assembles all of them into far.bin (the whole map, always resident),
plus world.json (grid geometry, spawn).

Local coordinates: x = east, z = north (metres from the Berat centre), y = elevation (m NGF). Chunk (ci, cj) covers
x in [-16000 + 400 ci, +400), z in [-16000 + 400 cj, +400).

Haute-Garonne extension: the sector list is data/big/hg_sectors.json (indices may be negative; sector (si, sj) covers x in
[-16000 + 3200 si, +3200), z likewise). The world origin (x0, z0) is the south-west corner of the sector list; chunk files are named with
indices RELATIVE to it (ci = floor((x - x0) / 400)). Sectors 0..9 x 0..9 read the float32 .npy tiles, all others the compressed files of
data/big/hg (see HG_README.txt). Terrain heights of a chunk are base + uint16 * step (step >= 5 mm, larger for mountain chunks).

Usage: uv run python build_world.py [--sectors 4:4,4:5,5:4,5:5] [--jobs 8] [--out DIR] [--list data/big/hg_sectors.json] [--far-cache F]
       (the world geometry always comes from --list; --sectors only selects which of them to (re)build)
"""
import argparse, functools, shutil, gzip, math, hashlib, json, struct, sys, time, collections, zlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np
from PIL import Image
import shapely
from pyproj import Transformer
from rasterio import features
from rasterio.transform import from_origin
from scipy.ndimage import (distance_transform_edt, gaussian_filter, map_coordinates, maximum_filter, uniform_filter, uniform_filter1d)
from scipy.spatial import cKDTree
from shapely.geometry import LineString, Point, Polygon, box, shape
from shapely.strtree import STRtree
from fetch import CX, CY
from road_smooth import round_corners

HALF = 16000
SECTOR = 3200                    # m
NS = 2 * HALF // SECTOR          # 10 sectors per side of the original block (stored as .npy tiles)
CHUNK = 400                      # m
CPS = SECTOR // CHUNK            # 8 chunks per sector side
CELL = 4                         # terrain vertex spacing (m)
CV = CHUNK // CELL + 1           # 101 vertices per chunk side
FAR_CELL = 64
MARGIN = 240
BIG = Path("data/big")
DEFAULT_OUT = Path("../world_hg")
DEFAULT_SPAWN = Path("../world/spawn.json")
DEFAULT_LIST = BIG / "hg_sectors.json"
DEFAULT_FAR_CACHE = BIG / "far_hg.npz"
FAR_NEUTRAL = (96, 104, 88)       # far colour outside the covered sectors

# ------------------------------------------------------------------ rasters

def read_tile(kind, i, j, tile_m):
    """One raster tile (i, j) of `kind` (tile index = floor((x + HALF) / tile_m), may be negative), north row first, or None if the file is missing.
    The original 10 x 10 sector block lives in float32 .npy files, every other sector in the compressed files of data/big/hg."""
    si, sj = i * tile_m // SECTOR, j * tile_m // SECTOR                               # sector owning the tile
    if 0 <= si < NS and 0 <= sj < NS:
        f = BIG / f"{kind}_{i}_{j}.npy"
        return np.load(f) if f.exists() else None
    if kind == "ortho":
        f = BIG / "hg" / f"ortho_{i}_{j}.jpg"
        return np.asarray(Image.open(f).convert("RGB")) if f.exists() else None
    f = BIG / "hg" / f"{kind}_{i}_{j}.npz"
    if not f.exists(): return None
    a = np.load(f)["a"]
    if kind == "mnt": return np.where(a == 65535, np.nan, a / 20.0 - 100).astype(np.float32)
    return (a / 10.0).astype(np.float32)

class Window:
    """Raster window around one sector: mnt / mnh at 2 m and ortho at 4 m, assembled from the tile files. Origin = north-west corner."""
    def __init__(self, si, sj):
        self.x0 = -HALF + si * SECTOR - MARGIN
        self.z0 = -HALF + sj * SECTOR - MARGIN
        self.size = SECTOR + 2 * MARGIN
        self.z1 = self.z0 + self.size
        n2, n4 = self.size // 2, self.size // 4
        self.mnt = self._paste("mnt", 1600, 2, n2, np.nan, np.float32)
        self.mnh = self._paste("mnh", 1600, 2, n2, 0.0, np.float32)
        self.ortho = self._paste("ortho", 3200, 4, n4, 0, np.uint8, channels=3)
        bad = ~np.isfinite(self.mnt)
        if bad.all(): self.mnt[:] = 0.0
        elif bad.any():
            iy, ix = distance_transform_edt(bad, return_distances=False, return_indices=True)
            self.mnt = self.mnt[iy, ix]

    def _paste(self, kind, tile_m, res, n, fill, dtype, channels=None):
        shape_ = (n, n) if channels is None else (n, n, channels)
        out = np.full(shape_, fill, dtype)
        px = tile_m // res
        for j in range((self.z0 + HALF) // tile_m, (self.z1 + HALF - 1) // tile_m + 1):
            for i in range((self.x0 + HALF) // tile_m, (self.x0 + self.size + HALF - 1) // tile_m + 1):
                a = read_tile(kind, i, j, tile_m)
                if a is None: continue
                tx0, tz1 = -HALF + i * tile_m, -HALF + (j + 1) * tile_m                    # tile north-west corner
                c0, r0 = (tx0 - self.x0) // res, (self.z1 - tz1) // res                    # position of the tile in the window (may be negative)
                sc0, sr0 = max(-c0, 0), max(-r0, 0)
                dc0, dr0 = max(c0, 0), max(r0, 0)
                w, h = min(px - sc0, n - dc0), min(px - sr0, n - dr0)
                if w > 0 and h > 0: out[dr0:dr0 + h, dc0:dc0 + w] = a[sr0:sr0 + h, sc0:sc0 + w]
        return out

    def sample(self, arr, x, z, res):
        return map_coordinates(arr, [(self.z1 - np.asarray(z)) / res - 0.5, (np.asarray(x) - self.x0) / res - 0.5], order=1, mode="nearest")

# ------------------------------------------------------------------ vectors

def load_vectors(name, si, sj):
    """Features of every vector file touching the sector's window, de-duplicated on cleabs."""
    seen, out = set(), []
    for dj in (-1, 0, 1):
        for di in (-1, 0, 1):
            i, j = si + di, sj + dj
            f = BIG / "vec" / f"{name}_{i}_{j}.json"
            if not f.exists(): continue
            for feat in json.loads(f.read_text()):
                key = feat["properties"].get("cleabs") or feat.get("id")
                if key in seen: continue
                seen.add(key); out.append(feat)
    return out

@functools.lru_cache(maxsize=1)
def load_pois():
    """OSM points of interest of both downloads (original block and Haute-Garonne bbox), de-duplicated on (type, id)."""
    seen, out = set(), []
    for name in ("osm_poi.json", "osm_poi_hg.json"):
        f = BIG / "vec" / name
        if not f.exists(): continue
        for e in json.loads(f.read_text())["elements"]:
            if (e["type"], e["id"]) in seen: continue
            seen.add((e["type"], e["id"])); out.append(e)
    return out

def local(coords):
    a = np.asarray(coords, float)[:, :2]
    return np.c_[a[:, 0] - CX, a[:, 1] - CY]

def geom_local(f):
    """GeoJSON feature geometry as a 2D shapely geometry in local coordinates."""
    return shapely.transform(shapely.force_2d(shape(f["geometry"])), lambda c: c - np.array([CX, CY]))

def dense_runs(geom, wbox, step=2.0, rounded=False):
    """Densify each line of the FULL feature from its own start (so every sector gets the same vertices), then keep the runs of points inside wbox."""
    out = []
    for line in lines_of(geom):
        src = np.asarray(line.coords)[:, :2]
        if rounded: src = round_corners(src)                                       # bends become curves (bounded fillets), then the usual stable densification
        xy = densify(src, step)
        if xy is None: continue
        inside = shapely.contains_xy(wbox, xy[:, 0], xy[:, 1])
        idx = np.where(inside)[0]
        if not len(idx): continue
        for seg in np.split(idx, np.where(np.diff(idx) != 1)[0] + 1):
            if len(seg) >= 2: out.append(xy[seg])
    return out

def lines_of(geom):
    if geom.geom_type == "LineString": return [geom]
    return [g for g in getattr(geom, "geoms", []) if g.geom_type == "LineString"]

# ------------------------------------------------------------------ roads (ported from export_world.py)

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
    t = np.arange(0, d[-1], step)
    if len(t) > 1 and d[-1] - t[-1] < 0.6 * step: t = t[:-1]                           # no sliver at the end: it would give the ribbon a noisy end tangent
    t = np.r_[t, d[-1]]
    return np.c_[np.interp(t, d, xy[:, 0]), np.interp(t, d, xy[:, 1])]

def smooth_free(y, win=17):
    """Two box passes (~triangular, ~68 m support at 2 m spacing) with replicated edges."""
    if len(y) < 3: return y.copy()
    w = min(win, len(y) | 1); pad = w // 2
    for _ in range(2):
        ext = np.r_[np.full(pad, y[0]), y, np.full(pad, y[-1])]
        y = uniform_filter1d(ext, w, mode="nearest")[pad:pad + len(y)]
    return y

def densify_pts(pts, step=0.5):
    out = []
    for a, b in zip(pts[:-1], pts[1:]):
        L = np.hypot(b[0] - a[0], b[1] - a[1]); n = max(int(L / step), 1)
        t = (np.arange(n) / n)[:, None]; out.append(a[None, :] + (b - a)[None, :] * t)
    return np.vstack(out) if out else pts

def process_roads(win, wbox, feats):
    """Drivable polylines of the window with network-consistent heights. Returns a list of dicts (xy, y, hw, dirt, bridge, name, imp)."""
    mns = win.mnt + np.clip(win.mnh, -1.0, 7.0)                                     # surface model: bridge decks, capped so tall trees cannot pass for a deck
    raw = []
    for f in feats:
        p = f["properties"]
        if p["nature"] == "Sentier": continue
        g = geom_local(f)
        for xy in dense_runs(g, wbox, rounded=True):
            hw0 = road_halfwidth(p)
            tang = np.gradient(xy, axis=0); tang /= np.maximum(np.hypot(tang[:, 0], tang[:, 1]), 1e-6)[:, None]
            nrm = np.c_[-tang[:, 1], tang[:, 0]]
            y = np.mean([win.sample(win.mnt, xy[:, 0] + nrm[:, 0] * o * hw0, xy[:, 1] + nrm[:, 1] * o * hw0, 2) for o in (-0.7, -0.35, 0.0, 0.35, 0.7)], axis=0)
            pos = str(p.get("position_par_rapport_au_sol") or "0")
            bridge = pos not in ("0", "") and not pos.startswith("-")
            if bridge:
                L = float(np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1])).sum())
                ground_ends = float(y[0]), float(y[-1])
                deck = win.sample(mns, xy[:, 0], xy[:, 1], 2); h, k = len(deck), max(len(deck) // 4, 1)
                dh = float(np.median(deck[k:max(h - k, k + 1)]))
                if L < 40.0 and -0.5 <= dh - max(ground_ends) <= 4.5: y0 = y1 = dh
                else: y0, y1 = ground_ends
                y = np.linspace(y0, y1, len(y))
            else: y = smooth_free(y)
            raw.append(dict(xy=xy, y=y, bridge=bridge, hw=hw0, dirt=p["nature"] in ("Chemin", "Route empierrée"),
                            name=next((p[k] for k in ("nom_1_gauche", "nom_1_droite") if p.get(k)), ""), imp=str(p.get("importance")),
                            fid=zlib.crc32(p["cleabs"].encode()) & 0x7FFFFFFF, t0=(0.0, 0.0), t1=(0.0, 0.0),
                            pri=hw0 + (0.0 if p["nature"] in ("Chemin", "Route empierrée") else 1.0) + min(g.length, 20000.0) / 1e5))      # asphalt beats dirt, then wider, then longer
    if not raw: return raw
    ends = []
    for i, r in enumerate(raw):
        ends.append((i, 0, r["xy"][0, 0], r["xy"][0, 1])); ends.append((i, 1, r["xy"][-1, 0], r["xy"][-1, 1]))
    parent = list(range(len(ends)))
    def find(a):
        while parent[a] != a: parent[a] = parent[parent[a]]; a = parent[a]
        return a
    for a, b in cKDTree(np.array([[e[2], e[3]] for e in ends])).query_pairs(1.5): parent[find(a)] = find(b)
    groups = {}
    for k in range(len(ends)): groups.setdefault(find(k), []).append(k)
    for members in groups.values():
        if len(members) < 2: continue
        vals = [raw[ends[k][0]]["y"][0 if ends[k][1] == 0 else -1] for k in members]
        bridges = [v for v, k in zip(vals, members) if raw[ends[k][0]]["bridge"]]
        node = float(np.mean(bridges)) if bridges else float(np.mean(vals))
        for k in members:
            r = raw[ends[k][0]]
            if r["bridge"]: continue
            y = r["y"]; d = node - y[0 if ends[k][1] == 0 else -1]
            s_ = np.r_[0, np.cumsum(np.hypot(np.diff(r["xy"][:, 0]), np.diff(r["xy"][:, 1])))]
            span = max(min(60.0 if bridges else 30.0, 0.5 * s_[-1]), 2.0)                     # a bridge deck is level: ease the approaches onto it over a longer stretch
            y += d * np.clip(1.0 - (s_ if ends[k][1] == 0 else s_[-1] - s_) / span, 0.0, 1.0)
    # ---- joins: every end of a cluster snaps to the cluster centre (fading out over 8 m), and where exactly two roads meet along a gentle bend
    #      both get the SAME tangent at the joint, so their ribbons meet edge to edge instead of leaving a wedge
    for members in groups.values():
        if len(members) < 2: continue
        cen = np.mean([[ends[k][2], ends[k][3]] for k in members], axis=0)
        inward = []
        for k in members:
            r = raw[ends[k][0]]; xy = r["xy"]; first = ends[k][1] == 0
            u = xy[1] - xy[0] if first else xy[-2] - xy[-1]
            inward.append(u / max(float(np.hypot(*u)), 1e-6))
            s_ = np.r_[0, np.cumsum(np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1])))]
            dist = s_ if first else s_[-1] - s_
            xy += (cen - (xy[0] if first else xy[-1]))[None, :] * np.clip(1.0 - dist / 8.0, 0.0, 1.0)[:, None]
        # the two arms that run most nearly straight through the node (a fork or T: the trunk and its better-aligned branch) share a tangent
        best = None
        for ia in range(len(members)):
            for ib in range(ia + 1, len(members)):
                if ends[members[ia]][0] == ends[members[ib]][0]: continue        # two ends of the same road
                T = inward[ib] - inward[ia]; n = float(np.hypot(*T))
                if n > 1.6 and (best is None or n > best[0]): best = (n, ia, ib, T)     # arms more than ~106 degrees apart: a gentle bend, not a corner
        if best is not None:
            n, ia, ib, T = best; T = (float(T[0] / n), float(T[1] / n))
            for i in (ia, ib):
                k = members[i]; raw[ends[k][0]]["t0" if ends[k][1] == 0 else "t1"] = T
    # ---- vertical curves: smooth every road's profile again (bridge decks stay level), then pull the ends of joining roads back to one shared height,
    #      fading the correction out over 12 m; a few rounds turn "flat -> ramp -> flat" into a smooth vertical curve without opening steps at the joints
    multi = [m for m in groups.values() if len(m) >= 2]
    for _ in range(3):
        for r in raw:
            if not r["bridge"] and len(r["y"]) >= 5: r["y"] = smooth_free(r["y"], 15)
        for members in multi:
            vals = [raw[ends[k][0]]["y"][0 if ends[k][1] == 0 else -1] for k in members]
            bridges = [v for v, k in zip(vals, members) if raw[ends[k][0]]["bridge"]]
            node = float(np.mean(bridges)) if bridges else float(np.mean(vals))
            for k in members:
                r = raw[ends[k][0]]
                if r["bridge"]: continue
                y = r["y"]; d = node - y[0 if ends[k][1] == 0 else -1]
                s_ = np.r_[0, np.cumsum(np.hypot(np.diff(r["xy"][:, 0]), np.diff(r["xy"][:, 1])))]
                y += d * np.clip(1.0 - (s_ if ends[k][1] == 0 else s_[-1] - s_) / 12.0, 0.0, 1.0)
    return raw

# ------------------------------------------------------------------ colour (ported)

def ground_colour(raw_rgb, covered):
    """Ortho colour on the terrain vertex grid: cover cells take the nearest bare-ground colour, then warm / olive grading."""
    col = raw_rgb.astype(np.float32)
    iy, ix = distance_transform_edt(covered, return_distances=False, return_indices=True) if covered.any() and not covered.all() else (None, None)
    if iy is not None: col = col[iy, ix]
    col = gaussian_filter(col, sigma=(1.2, 1.2, 0))
    return grade(col)

def grade(col):
    grey = col.mean(axis=2, keepdims=True)
    col = (grey + (col - grey) * 1.5) * np.array([1.10, 1.16, 0.90])
    lum = col.mean(axis=2, keepdims=True)
    dark = np.clip((135 - lum) / 65, 0, 1)
    olive = np.concatenate([lum * 1.16 + 14, lum * 1.12 + 17, lum * 0.78 + 6], axis=2)
    col = (col * (1 - dark) + olive * dark) * 1.12
    l2 = col.mean(axis=2, keepdims=True)
    return np.clip(col * np.maximum(1.0, 96.0 / np.maximum(l2, 1.0)), 0, 255)

def soften(col):
    """Compress highlights: pale stubble fields at grazing sun bloomed into glowing discs on the horizon."""
    lum = col.mean(axis=2, keepdims=True)
    target = np.where(lum > 150, 150 + (lum - 150) * 0.35, lum)
    return np.clip(col * target / np.maximum(lum, 1.0), 0, 255)

# ------------------------------------------------------------------ buildings (ported)

WALLS = [(232, 222, 200), (222, 190, 170), (238, 236, 230), (214, 190, 140), (200, 198, 192), (205, 145, 122)]

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

# ------------------------------------------------------------------ water

LINE_WIDTH = {"Entre 0 et 5 m": 2.6, "Entre 5 et 15 m": 8.0, "Entre 15 et 50 m": 24.0}      # full width of BD TOPO width classes (m)
WATER_DEPTH = 0.45

def water_features(win, wbox, area_feats, line_feats):
    """Standing water polygons (ponds, reservoirs, river surfaces; levels are added later from a smooth field) and streams / canals with a level per vertex."""
    areas, lines = [], []
    for f in area_feats:
        p = f["properties"]
        if p.get("etat_de_l_objet") == "Disparu" or str(p.get("position_par_rapport_au_sol") or "0") != "0": continue
        g = geom_local(f)
        for poly in ([g] if g.geom_type == "Polygon" else list(g.geoms)):
            poly = poly.simplify(0.6)                                                # simplify the whole polygon first, so every sector sees the same outline
            if poly.is_empty or poly.geom_type != "Polygon" or poly.area < 12: continue
            if not poly.intersects(wbox): continue
            areas.append(dict(poly=poly))
    for f in line_feats:
        p = f["properties"]
        if p.get("fictif") or p.get("etat_de_l_objet") == "Disparu" or str(p.get("position_par_rapport_au_sol") or "0") != "0": continue
        if p.get("nature") in ("Conduit buse", "Retenue"): continue
        hw = LINE_WIDTH.get(p.get("classe_de_largeur"), 3.0) / 2
        g = geom_local(f)
        for xy in dense_runs(g, wbox):
            if len(xy) < 3: continue
            tang = np.gradient(xy, axis=0); tang /= np.maximum(np.hypot(tang[:, 0], tang[:, 1]), 1e-6)[:, None]
            nrm = np.c_[-tang[:, 1], tang[:, 0]]
            y = np.min([win.sample(win.mnt, xy[:, 0] + nrm[:, 0] * o * hw, xy[:, 1] + nrm[:, 1] * o * hw, 2) for o in (-1.0, 0.0, 1.0)], axis=0)
            y = smooth_free(y) - 0.1
            pad = np.r_[np.full(30, y[0]), y]                                        # never uphill along the digitised (= flow) direction, judged over the last 60 m only
            y = np.lib.stride_tricks.sliding_window_view(pad, 31).min(axis=1)       # (a global running minimum would make a window's edge change the whole river)
            lines.append(dict(xy=xy, y=y, hw=hw))
    return areas, lines

def water_level_field(areas, h0, vx0, vz0, nv):
    """Smooth water-surface height on the vertex grid (row = z south->north): the mean LiDAR ground over ~120 m of water, held below the local bank height.
    A function of position only, so adjacent sectors and chunks agree, and a long river follows its own slope."""
    mask = np.zeros((nv, nv), bool)
    if areas:
        tr = from_origin(vx0 - CELL / 2, vz0 + (nv - 0.5) * CELL, CELL, CELL)               # north-up raster of the same cells
        mask = features.rasterize([(a["poly"], 1) for a in areas], out_shape=(nv, nv), transform=tr, dtype=np.uint8, all_touched=True).astype(bool)[::-1]
    from scipy.ndimage import binary_dilation
    box_ = 31
    w = mask.astype(np.float32); den = uniform_filter(w, box_, mode="constant")
    inside = uniform_filter(h0 * w, box_, mode="constant") / np.maximum(den, 1e-6)
    bank = binary_dilation(mask, iterations=3) & ~binary_dilation(mask, iterations=1)
    wb = bank.astype(np.float32); denb = uniform_filter(wb, box_, mode="constant")
    bankh = uniform_filter(h0 * wb, box_, mode="constant") / np.maximum(denb, 1e-6)
    level = np.where(denb > 2e-3, np.minimum(inside, bankh - 0.15), inside)
    return mask, level.astype(np.float32)

# ------------------------------------------------------------------ binary writers

def wstr(buf, s):
    b = s.encode("utf-8"); n = len(b)
    while n >= 0x80: buf.append((n & 0x7F) | 0x80); n >>= 7
    buf.append(n); buf += b

def wi(buf, v): buf += struct.pack("<i", v)
def wf(buf, *v): buf += struct.pack(f"<{len(v)}f", *v)
def wfa(buf, a): buf += np.asarray(a, "<f4").tobytes()

def write_gz(path, buf):
    with gzip.open(path, "wb", compresslevel=6) as fo: fo.write(bytes(buf))

# ------------------------------------------------------------------ sector

def runs_by_chunk(pts_xz, values=None):
    """Split a polyline into per-chunk runs. Segment (i, i+1) belongs to the chunk holding point i; each run carries the next point too."""
    ci = np.floor((pts_xz[:, 0] + HALF) / CHUNK).astype(int); cj = np.floor((pts_xz[:, 1] + HALF) / CHUNK).astype(int)
    out, start = [], 0
    for i in range(1, len(pts_xz) + 1):
        if i == len(pts_xz) or ci[i] != ci[start] or cj[i] != cj[start]:
            out.append(((ci[start], cj[start]), start, min(i + 1, len(pts_xz))))
            start = i
    return [o for o in out if o[2] - o[1] >= 2]

def process_sector(args):
    si, sj, out_dir, ci0, cj0 = args                                                 # (ci0, cj0): chunk index of the world origin
    t0 = time.time()
    out_dir = Path(out_dir)
    ox, oz = -HALF + si * SECTOR, -HALF + sj * SECTOR
    win = Window(si, sj)
    wbox = box(win.x0 + 10, win.z0 + 10, win.x0 + win.size - 10, win.z0 + win.size - 10)

    # ---- roads
    raw = process_roads(win, wbox, load_vectors("roads", si, sj))
    if raw:
        road_pts = np.vstack([np.c_[r["xy"][:, 0], r["xy"][:, 1], r["y"], np.full(len(r["y"]), r["hw"])] for r in raw if not r["bridge"]] or [np.zeros((0, 4))])
    else: road_pts = np.zeros((0, 4))
    brk = np.where(np.hypot(np.diff(road_pts[:, 0]), np.diff(road_pts[:, 1])) > 6.0)[0] + 1 if len(road_pts) else []
    carve_pts = np.vstack([densify_pts(s) for s in np.split(road_pts, brk) if len(s) > 1]) if len(road_pts) > 1 else np.zeros((0, 4))
    corr = [LineString(np.c_[r["xy"][:, 0], r["xy"][:, 1]]).buffer(r["hw"] + 0.35) for r in raw]
    corr_tree = shapely.STRtree(corr) if corr else None

    # ---- water lines (areas need the terrain grid, see below)
    areas, wlines = water_features(win, wbox, load_vectors("hydro_areas", si, sj), load_vectors("hydro_lines", si, sj))
    # keep streams off the roads: cut a line wherever it enters a road corridor (bridges / culverts carry the road over it)
    if wlines and len(road_pts):
        rtree = cKDTree(road_pts[:, :2]); cut = []
        for w in wlines:
            d, i = rtree.query(w["xy"], distance_upper_bound=12.0)
            near = np.isfinite(d) & (d < road_pts[np.minimum(i, len(road_pts) - 1), 3] + w["hw"] + 1.5)
            idx = np.where(~near)[0]
            for seg in np.split(idx, np.where(np.diff(idx) != 1)[0] + 1) if len(idx) else []:
                if len(seg) >= 3: cut.append(dict(xy=w["xy"][seg], y=w["y"][seg], hw=w["hw"]))
        wlines = cut

    # ---- terrain on the window's vertex grid (aligned to 4 m)
    nv = win.size // CELL + 1
    vx = win.x0 + CELL * np.arange(nv); vz = win.z0 + CELL * np.arange(nv)
    gx, gz = np.meshgrid(vx, vz)                                                     # row = z index (south -> north)
    mnt_s = uniform_filter(win.mnt, 2, mode="nearest")
    h = win.sample(mnt_s, gx.ravel(), gz.ravel(), 2)
    pts = np.c_[gx.ravel(), gz.ravel()]
    wmask, wlevel = water_level_field(areas, h.reshape(nv, nv), win.x0, win.z0, nv)
    if wmask.any():                                                                  # scoop the bed below the surface, deeper away from the shore
        shore = distance_transform_edt(wmask) * CELL
        bed = wlevel - WATER_DEPTH * np.clip(0.35 + shore / 4.0, 0.35, 1.0)
        hh_ = h.reshape(nv, nv); hh_[wmask] = np.minimum(hh_[wmask], bed[wmask]); h = hh_.ravel()
    for w in wlines:
        seg = densify_pts(np.c_[w["xy"], w["y"]], 1.0) if len(w["xy"]) > 1 else w["xy"]
        dd, ii = cKDTree(seg[:, :2]).query(pts, distance_upper_bound=w["hw"] + 3.0)
        m = np.isfinite(dd)
        if not m.any(): continue
        ly = seg[np.minimum(ii[m], len(seg) - 1), 2]
        bed = ly - WATER_DEPTH + np.maximum(dd[m] - w["hw"], 0) * 0.35
        h[m] = np.minimum(h[m], bed)
    for r in raw:                                                                    # under a bridge deck the ground falls away, so the terrain mesh can never poke through the deck
        if not r["bridge"]: continue
        seg = densify_pts(np.c_[r["xy"], r["y"]], 1.0) if len(r["xy"]) > 1 else np.c_[r["xy"], r["y"]]
        dd, ii = cKDTree(seg[:, :2]).query(pts, distance_upper_bound=r["hw"] + 6.0)
        m = np.isfinite(dd)
        if not m.any(): continue
        drop = 1.4 * (1.0 - np.clip((dd[m] - (r["hw"] + 2.5)) / 3.5, 0.0, 1.0))
        h[m] = np.minimum(h[m], seg[np.minimum(ii[m], len(seg) - 1), 2] - drop)
    if len(carve_pts):
        d, idx = cKDTree(carve_pts[:, :2]).query(pts, distance_upper_bound=16.0)
        hit = np.isfinite(d); ii = np.minimum(idx, len(carve_pts) - 1)
        hwv = np.where(hit, carve_pts[ii, 3], 0); yv = np.where(hit, carve_pts[ii, 2], 0)
        t = np.clip((d - (hwv + 3.0)) / 6.0, 0, 1); wgt = np.where(hit, 1 - t * t * (3 - 2 * t), 0)
        # the road always wins: sink the ground a little below the drawn surface under and beside the ribbon, so a coarse 4 m terrain facet can never
        # stand above the road where its profile bends sharply (bridge exits, crests)
        sink = 0.05 * (1.0 - np.clip((d - (hwv + 0.5)) / 3.0, 0.0, 1.0))
        h = h * (1 - wgt) + (yv - np.where(hit, sink, 0.0)) * wgt
    H = h.reshape(nv, nv)

    # ---- ground colour
    rgb = np.stack([win.sample(win.ortho[..., k].astype(np.float32), gx.ravel(), gz.ravel(), 4) for k in range(3)], axis=1).reshape(nv, nv, 3)
    cov = maximum_filter(win.mnh, 3) > 1.3
    covered = win.sample(cov.astype(np.float32), gx.ravel(), gz.ravel(), 2).reshape(nv, nv) > 0.4
    C = ground_colour(rgb, covered)
    far_raw = soften(grade(gaussian_filter(rgb, sigma=(2, 2, 0))))                  # far view keeps forests and villages (no cover removal)
    low_col = uniform_filter(far_raw, size=(4, 4, 1), mode="nearest")               # 16 m LOD colours

    # ---- buildings
    pois = load_pois()
    road_tree = cKDTree(road_pts[:, :2]) if len(road_pts) else None
    owned = box(ox, oz, ox + SECTOR, oz + SECTOR); owned_wide = owned.buffer(6)
    buildings, bpolys = [], []
    for f in load_vectors("buildings", si, sj):
        p = f["properties"]
        g = geom_local(f)
        for poly in ([g] if g.geom_type == "Polygon" else list(g.geoms)):
            poly = shapely.force_2d(poly).simplify(0.3)
            if poly.is_empty or poly.area < 6: continue
            if poly.geom_type != "Polygon": poly = max(poly.geoms, key=lambda q: q.area)
            if not owned_wide.contains(poly.centroid): continue
            cutp = poly
            near = corr_tree.query(poly.buffer(0.5)) if corr_tree is not None else []
            if len(near):
                cutp = poly.difference(shapely.union_all([corr[j] for j in near]))
                if cutp.is_empty: continue
                if cutp.geom_type != "Polygon": cutp = max(cutp.geoms, key=lambda q: q.area) if hasattr(cutp, "geoms") else cutp
                if cutp.geom_type != "Polygon" or cutp.area < 6 or cutp.area < 0.35 * poly.area: continue
                cutp = cutp.simplify(0.2)
                if cutp.geom_type != "Polygon": continue
            poly = shapely.geometry.polygon.orient(cutp, 1.0)
            ring = np.array(poly.exterior.coords)[:-1]
            if not (ox <= ring[:, 0].mean() < ox + SECTOR and oz <= ring[:, 1].mean() < oz + SECTOR): continue      # the chunk is chosen from this same mean below
            gx0, gz0, gx1, gz1 = poly.buffer(1.0).bounds
            gxs, gzs = np.meshgrid(np.arange(gx0, gx1 + 1e-6, 0.75), np.arange(gz0, gz1 + 1e-6, 0.75))
            inside = shapely.contains_xy(poly.buffer(1.0), gxs.ravel(), gzs.ravel())
            gpts = np.c_[gxs.ravel()[inside], gzs.ravel()[inside]]
            ground = float(min(win.sample(win.mnt, ring[:, 0], ring[:, 1], 2).min(), win.sample(win.mnt, gpts[:, 0], gpts[:, 1], 2).min() if len(gpts) else 1e9))
            c = poly.centroid
            mnh_c = float(win.sample(win.mnh, [c.x], [c.y], 2)[0])
            inner = poly.buffer(-1.2)
            ipts = np.array(inner.exterior.coords) if (not inner.is_empty and inner.geom_type == "Polygon") else ring
            mnh_edge = float(win.sample(win.mnh, ipts[:, 0], ipts[:, 1], 2).mean())
            hh = p.get("hauteur")
            wall = float(hh) if isinstance(hh, (int, float)) and hh > 0 else (
                3.0 * p["nombre_d_etages"] if isinstance(p.get("nombre_d_etages"), (int, float)) else max(3.0, min(mnh_edge, 12.0)))
            kind = bd_kind(p, poly.area)
            tower = []
            if kind in ("church", "chapel"):
                wall = float(np.clip(mnh_edge, 5.0, 10.0))
                ins = shapely.contains_xy(poly, gxs.ravel(), gzs.ravel())
                if ins.any():
                    hx, hz = gxs.ravel()[ins], gzs.ravel()[ins]
                    hv = win.sample(win.mnh, hx, hz, 2); k = int(hv.argmax())
                    if hv[k] > wall + 2.5: tower = [round(float(hx[k]), 2), round(float(hz[k]), 2), round(float(hv[k]), 2)]
            mrr = poly.minimum_rotated_rectangle
            rect = np.array(mrr.exterior.coords)[:4]
            e1, e2 = rect[1] - rect[0], rect[2] - rect[1]
            L, Wd = np.linalg.norm(e1), np.linalg.norm(e2)
            axis, long_, short = (e1 / L, L, Wd) if L >= Wd else (e2 / Wd, Wd, L)
            fit = 1.0 - mrr.symmetric_difference(poly).area / poly.area
            pitched = (mnh_c - mnh_edge) > 0.8 and fit > 0.86 and poly.area < 900 and short > 3
            rise = float(np.clip(mnh_c - wall, 0.8, 5.0)) if pitched else 0.0
            if kind in ("church", "chapel") and fit > 0.6 and short > 4: pitched, rise = True, max(rise, float(short) * 0.32)
            seed = int(hashlib.md5(p["cleabs"].encode()).hexdigest()[:8], 16)
            mids = (ring + np.roll(ring, -1, axis=0)) / 2
            fe = int(road_tree.query(mids)[0].argmin()) if road_tree is not None else 0
            # collision rings: footprint minus road corridors (already cut above, so the ring is the visual footprint)
            cp = np.round(ring, 2).ravel().tolist()
            # roof colour from the orthophoto
            minx, minz, maxx, maxz = poly.bounds
            rxs, rzs = np.meshgrid(np.arange(minx, maxx, 2.0), np.arange(minz, maxz, 2.0))
            mk = shapely.contains_xy(poly, rxs.ravel(), rzs.ravel())
            px, pz = (rxs.ravel()[mk], rzs.ravel()[mk]) if mk.any() else ([poly.centroid.x], [poly.centroid.y])
            roof = np.clip(np.array([win.sample(win.ortho[..., k].astype(np.float32), px, pz, 4) for k in range(3)]).mean(axis=1) * 1.1, 0, 255).astype(int).tolist()
            buildings.append(dict(k=kind, n="", fe=fe, tw=tower, cp=cp, cn=[len(ring)], p=np.round(ring, 2).ravel().tolist(), b=round(ground - 0.8, 2), h=round(wall + 0.8, 2),
                                  r=round(rise, 2), rc=[round(float(v), 2) for v in (*mrr.centroid.coords[0], *axis, long_, short)] if pitched else [], c=roof, w=list(WALLS[seed % len(WALLS)])))
            bpolys.append(poly)

    # ---- collision rings proper (a building crossing a road keeps the road corridor free, computed against the uncut original in export_world; here the visual is already cut)
    # ---- POIs -> kind + name
    if bpolys:
        strtree = STRtree(bpolys); tr = Transformer.from_crs(4326, 2154, always_xy=True)
        for e in pois:
            lat, lon = (e.get("lat"), e.get("lon")) if e["type"] == "node" else (e["center"]["lat"], e["center"]["lon"])
            kind = poi_kind(e["tags"])
            if not kind: continue
            x, y = tr.transform(lon, lat); pt = Point(x - CX, y - CY)
            if not owned.buffer(20).contains(pt): continue
            hit = [i for i in strtree.query(pt) if bpolys[i].contains(pt)]
            if not hit:
                j = int(strtree.nearest(pt))
                if bpolys[j].distance(pt) > 15: continue
                hit = [j]
            b = buildings[hit[0]]
            if kind == "church" and b["k"] == "chapel": kind = "chapel"
            b["k"] = kind; b["n"] = e["tags"].get("name", "")
        towers = [(b["tw"][2], i) for i, b in enumerate(buildings) if b.get("tw")]
        for h_i, i in sorted(towers, reverse=True):
            if not buildings[i]["tw"]: continue
            for h_j, j in towers:
                if j != i and buildings[j]["tw"] and h_j < h_i and np.hypot(buildings[i]["tw"][0] - buildings[j]["tw"][0], buildings[i]["tw"][1] - buildings[j]["tw"][1]) < 15:
                    buildings[j]["tw"] = []

    # ---- trees and shrubs (LiDAR canopy height maxima, 2 m raster)
    tr_ = from_origin(win.x0, win.z1, 2.0, 2.0)
    bmask = features.rasterize([(q.buffer(2.0), 1) for q in bpolys], out_shape=win.mnh.shape, transform=tr_, dtype=np.uint8).astype(bool) if bpolys else np.zeros(win.mnh.shape, bool)
    sm = gaussian_filter(win.mnh, 0.6)
    peak = (sm == maximum_filter(sm, size=3)) & (sm >= 2.5) & (sm <= 32) & ~bmask
    rr, cc = np.nonzero(peak)
    tx_, tz_ = win.x0 + (cc + 0.5) * 2.0, win.z1 - (rr + 0.5) * 2.0
    m = (tx_ >= ox) & (tx_ < ox + SECTOR) & (tz_ >= oz) & (tz_ < oz + SECTOR)
    rr, cc, tx_, tz_ = rr[m], cc[m], tx_[m], tz_[m]
    if road_tree is not None and len(tx_):
        dd, ii = road_tree.query(np.c_[tx_, tz_], distance_upper_bound=10.0)
        keep = ~(np.isfinite(dd) & (dd < road_pts[np.minimum(ii, len(road_pts) - 1), 3] + 1.8))
    else: keep = np.ones(len(tx_), bool)
    trees = np.c_[tx_[keep], win.sample(win.mnt, tx_[keep], tz_[keep], 2), tz_[keep], sm[rr, cc][keep]].astype("<f4")

    low = (sm >= 0.9) & (sm < 2.5) & ~bmask
    lpeak = (sm == maximum_filter(sm, size=3)) & low
    lr, lc = np.nonzero(lpeak)
    sx_, sz_ = win.x0 + (lc + 0.5) * 2.0, win.z1 - (lr + 0.5) * 2.0
    m = (sx_ >= ox) & (sx_ < ox + SECTOR) & (sz_ >= oz) & (sz_ < oz + SECTOR)
    lr, lc, sx_, sz_ = lr[m], lc[m], sx_[m], sz_[m]
    if road_tree is not None and len(sx_):
        sd_, si_ = road_tree.query(np.c_[sx_, sz_], distance_upper_bound=8.0)
        sk = ~(np.isfinite(sd_) & (sd_ < road_pts[np.minimum(si_, len(road_pts) - 1), 3] + 1.2))
    else: sk = np.ones(len(sx_), bool)
    rs = np.random.default_rng(3 + si * 10 + sj if si >= 0 and sj >= 0 else [3, si + 1000, sj + 1000])   # seeds must not be negative
    sk &= rs.random(len(sx_)) < min(1.0, 17000 / max(int(sk.sum()), 1))
    shrubs = np.c_[sx_[sk], win.sample(win.mnt, sx_[sk], sz_[sk], 2), sz_[sk], sm[lr, lc][sk]].astype("<f4")

    # ---- bucket everything by chunk and write
    def bucket_xy(arr, xcol=0, zcol=2):
        d = collections.defaultdict(list)
        if len(arr):
            ci = np.floor((arr[:, xcol] + HALF) / CHUNK).astype(int); cj = np.floor((arr[:, zcol] + HALF) / CHUNK).astype(int)
            for k in range(len(arr)): d[(ci[k], cj[k])].append(k)
        return d
    tree_b, shrub_b = bucket_xy(trees), bucket_xy(shrubs)
    bld_b = collections.defaultdict(list)
    for k, b in enumerate(buildings):
        cx_, cz_ = np.mean(b["p"][0::2]), np.mean(b["p"][1::2])
        bld_b[(int((cx_ + HALF) // CHUNK), int((cz_ + HALF) // CHUNK))].append(k)
    road_b, ctx_b = collections.defaultdict(list), collections.defaultdict(list)
    for r in raw:
        pxz = r["xy"]; a = np.c_[pxz[:, 0], r["y"], pxz[:, 1]]
        for key, s, e in runs_by_chunk(pxz):                                        # one extra point on each side keeps the ribbon tangent identical across the join
            lo, hi = max(s - 1, 0), min(e + 1, len(pxz))
            road_b[key].append((r, a[lo:hi], s - lo, hi - e))
        # context: pieces inside each neighbouring chunk's 25 m apron that belong to another chunk (obstacle clearance next to roads)
        ci = np.floor((pxz[:, 0] + HALF) / CHUNK).astype(int); cj = np.floor((pxz[:, 1] + HALF) / CHUNK).astype(int)
        cand = {(k[0] + dx, k[1] + dz) for k in set(zip(ci.tolist(), cj.tolist())) for dx in (-1, 0, 1) for dz in (-1, 0, 1)}
        for nk in cand:
            if not (sj * CPS <= nk[1] < (sj + 1) * CPS and si * CPS <= nk[0] < (si + 1) * CPS): continue      # only chunks this sector writes
            x0_, z0_ = -HALF + nk[0] * CHUNK - 25, -HALF + nk[1] * CHUNK - 25
            inb = (pxz[:, 0] >= x0_) & (pxz[:, 0] < x0_ + CHUNK + 50) & (pxz[:, 1] >= z0_) & (pxz[:, 1] < z0_ + CHUNK + 50) & ~((ci == nk[0]) & (cj == nk[1]))
            idx = np.where(inb)[0]
            for seg in (np.split(idx, np.where(np.diff(idx) != 1)[0] + 1) if len(idx) else []):
                lo, hi = max(seg[0] - 1, 0), min(seg[-1] + 2, len(pxz))
                if hi - lo >= 2: ctx_b[nk].append((r, a[lo:hi], 0, 0))
    area_b, wline_b = collections.defaultdict(list), collections.defaultdict(list)
    for a in areas:
        minx, minz, maxx, maxz = a["poly"].bounds
        for cj in range(int((minz + HALF) // CHUNK), int((maxz + HALF) // CHUNK) + 1):
            for ci in range(int((minx + HALF) // CHUNK), int((maxx + HALF) // CHUNK) + 1):
                if not (ox <= -HALF + ci * CHUNK < ox + SECTOR and oz <= -HALF + cj * CHUNK < oz + SECTOR): continue
                piece = a["poly"].intersection(box(-HALF + ci * CHUNK - 1, -HALF + cj * CHUNK - 1, -HALF + (ci + 1) * CHUNK + 1, -HALF + (cj + 1) * CHUNK + 1))
                for q in ([piece] if piece.geom_type == "Polygon" else [x for x in getattr(piece, "geoms", []) if x.geom_type == "Polygon"]):
                    if q.area <= 4: continue
                    ring = np.asarray(shapely.geometry.polygon.orient(q, 1.0).exterior.coords)[:-1]
                    ly = map_coordinates(wlevel, [(ring[:, 1] - win.z0) / CELL, (ring[:, 0] - win.x0) / CELL], order=1, mode="nearest")
                    area_b[(ci, cj)].append(np.c_[ring[:, 0], ly, ring[:, 1]])
    for w in wlines:
        a = np.c_[w["xy"][:, 0], w["y"], w["xy"][:, 1]]
        for key, s0, e0 in runs_by_chunk(w["xy"]):
            lo, hi = max(s0 - 1, 0), min(e0 + 1, len(a))
            wline_b[key].append((w["hw"], a[lo:hi], s0 - lo, hi - e0))

    n_chunks = 0
    for cj in range(sj * CPS, (sj + 1) * CPS):
        for ci in range(si * CPS, (si + 1) * CPS):
            key = (ci, cj)
            r0, c0 = (cj - sj * CPS) * (CHUNK // CELL) + MARGIN // CELL, (ci - si * CPS) * (CHUNK // CELL) + MARGIN // CELL
            th = H[r0:r0 + CV, c0:c0 + CV]; tc = C[r0:r0 + CV, c0:c0 + CV]
            base = float(th.min()); step = max(0.005, math.ceil((float(th.max()) - base) / 65535 * 1000 - 1e-9) / 1000)
            q = np.clip(np.round((th - base) / step), 0, 65535).astype("<u2")
            buf = bytearray(b"BM02"); wi(buf, ci - ci0); wi(buf, cj - cj0); wi(buf, CV); wf(buf, base, step)
            buf += q.tobytes(); buf += tc.astype(np.uint8).tobytes()
            buf += np.clip(low_col[r0:r0 + CV:4, c0:c0 + CV:4], 0, 255).astype(np.uint8).tobytes()
            def put_roads(items, ctx=False):
                wi(buf, len(items))
                for it in items:
                    r, seg = it[0], it[1]
                    wf(buf, r["hw"]); buf.append((1 if r["dirt"] else 0) | (2 if r["bridge"] else 0)); buf.append(int(r["imp"]) if r["imp"].isdigit() else 0)
                    buf.append(it[2]); buf.append(it[3]); wi(buf, r["fid"]); wf(buf, r["pri"])
                    wf(buf, *(r["t0"] if it[2] == 0 else (0.0, 0.0)), *(r["t1"] if it[3] == 0 else (0.0, 0.0)))
                    wstr(buf, "" if ctx else r["name"]); wi(buf, len(seg)); wfa(buf, np.c_[seg[:, 0], np.round(seg[:, 1], 4), seg[:, 2]].ravel())
            put_roads(road_b.get(key, [])); put_roads(ctx_b.get(key, []), True)
            wi(buf, len(area_b.get(key, [])))
            for ring in area_b.get(key, []): wi(buf, len(ring)); wfa(buf, ring.ravel())
            wi(buf, len(wline_b.get(key, [])))
            for hw, seg, lead, trail in wline_b.get(key, []): wf(buf, hw); buf.append(lead); buf.append(trail); wi(buf, len(seg)); wfa(buf, seg.ravel())
            wi(buf, len(bld_b.get(key, [])))
            for k in bld_b.get(key, []):
                b = buildings[k]
                wi(buf, len(b["p"]) // 2); wfa(buf, b["p"]); wf(buf, b["b"], b["h"], b["r"]); wi(buf, len(b["rc"])); wfa(buf, b["rc"])
                buf += bytes(b["c"]) + bytes(b["w"]); wstr(buf, b["k"]); wstr(buf, b["n"]); wi(buf, b["fe"]); wi(buf, len(b["tw"])); wfa(buf, b["tw"])
                wi(buf, len(b["cp"]) // 2); wfa(buf, b["cp"]); wi(buf, len(b["cn"])); buf += np.asarray(b["cn"], "<i4").tobytes()
            write_gz(out_dir / f"m_{ci - ci0}_{cj - cj0}.bin.gz", buf)
            nb = bytearray(b"BN01"); ti, sh = tree_b.get(key, []), shrub_b.get(key, [])
            wi(nb, len(ti)); wfa(nb, trees[ti].ravel()); wi(nb, len(sh)); wfa(nb, shrubs[sh].ravel())
            write_gz(out_dir / f"n_{ci - ci0}_{cj - cj0}.bin.gz", nb)
            n_chunks += 1

    # ---- far terrain patch: 64 m vertices over the owned square (51 x 51, borders shared with the neighbours)
    fv = np.arange(SECTOR // FAR_CELL + 1) * FAR_CELL
    fgx, fgz = np.meshgrid(ox + fv, oz + fv)
    coarse = uniform_filter(win.mnt, 16, mode="nearest")                              # 32 m box before sampling every 64 m
    fh = win.sample(coarse, fgx.ravel(), fgz.ravel(), 2).reshape(fgx.shape)
    fc = np.stack([map_coordinates(far_raw[..., k], [(fgz.ravel() - win.z0) / CELL, (fgx.ravel() - win.x0) / CELL], order=1, mode="nearest") for k in range(3)], axis=1).reshape(fgx.shape + (3,))
    stats = dict(sector=(si, sj), chunks=n_chunks, roads=len(raw), buildings=len(buildings), trees=len(trees), shrubs=len(shrubs), water_areas=len(areas), water_lines=len(wlines), secs=round(time.time() - t0, 1))
    return si, sj, fh.astype(np.float32), np.clip(fc, 0, 255).astype(np.uint8), stats

# ------------------------------------------------------------------ assemble

def process_safe(args):
    """process_sector that never raises: a failed sector is reported and the run goes on."""
    try: return process_sector(args)
    except Exception as e:
        import traceback
        return args[0], args[1], None, None, dict(error=repr(e), trace=traceback.format_exc())

def fill_far(far_h, far_c, covered):
    """Outside the covered vertices: nearest covered height (no cliffs) and a neutral colour."""
    h, c = far_h.copy(), far_c.copy()
    if covered.all() or not covered.any(): return h, c
    iy, ix = distance_transform_edt(~covered, return_distances=False, return_indices=True)
    h = h[iy, ix]
    c[~covered] = FAR_NEUTRAL
    return h, c

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sectors", default="", help="comma list si:sj to (re)build (default: every sector of --list)")
    ap.add_argument("--list", default=str(DEFAULT_LIST), help="sector list json (defines the world geometry)")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--far-cache", default=str(DEFAULT_FAR_CACHE), help="npz keeping the far arrays between partial rebuilds")
    a = ap.parse_args()
    out = Path(a.out); (out / "chunks").mkdir(parents=True, exist_ok=True)
    world = [tuple(s) for s in json.loads(Path(a.list).read_text())["sectors"]]
    si_min, si_max = min(s[0] for s in world), max(s[0] for s in world)
    sj_min, sj_max = min(s[1] for s in world), max(s[1] for s in world)
    x0, z0 = -HALF + SECTOR * si_min, -HALF + SECTOR * sj_min
    ncx, ncz = CPS * (si_max - si_min + 1), CPS * (sj_max - sj_min + 1)
    per = SECTOR // FAR_CELL
    far_nx, far_nz = ncx * CHUNK // FAR_CELL + 1, ncz * CHUNK // FAR_CELL + 1
    secs = [tuple(map(int, s.split(":"))) for s in a.sectors.split(",")] if a.sectors else world
    for s in secs: assert s in world, f"sector {s} is not in {a.list}"
    jobs = [(i, j, str(out / "chunks"), CPS * si_min, CPS * sj_min) for i, j in secs]

    far_path = Path(a.far_cache)
    far_h = np.zeros((far_nz, far_nx), np.float32); far_c = np.zeros((far_nz, far_nx, 3), np.uint8); covered = np.zeros((far_nz, far_nx), bool)
    if far_path.exists():
        z = np.load(far_path)
        if z["h"].shape == far_h.shape: far_h, far_c, covered = z["h"], z["c"], z["covered"]
        else: print("far cache has another shape, ignored")
    def save_cache(): np.savez(far_path, h=far_h, c=far_c, covered=covered)

    failed, t0 = [], time.time()
    with ProcessPoolExecutor(a.jobs) as ex:
        futs = [ex.submit(process_safe, j) for j in jobs]
        for n, fu in enumerate(futs, 1):
            si, sj, fh, fc, st = fu.result()
            if fh is None:
                failed.append((si, sj)); print(f"[{n}/{len(jobs)}] FAILED {si}:{sj} {st['error']}\n{st['trace']}", flush=True); continue
            r, c = (sj - sj_min) * per, (si - si_min) * per
            far_h[r:r + per + 1, c:c + per + 1] = fh; far_c[r:r + per + 1, c:c + per + 1] = fc; covered[r:r + per + 1, c:c + per + 1] = True
            print(f"[{n}/{len(jobs)}] {st}  elapsed {time.time() - t0:.0f}s", flush=True)
            if n % 100 == 0: save_cache()
    save_cache()
    fh_, fc_ = fill_far(far_h, far_c, covered)
    with open(out / "far.bin", "wb") as fo:
        fo.write(struct.pack("<iiffff", far_nx, far_nz, FAR_CELL, x0, z0, 0.0))
        fo.write(fh_.astype("<f4").tobytes()); fo.write(fc_.tobytes())
    (out / "world.json").write_text(json.dumps(dict(x0=x0, z0=z0, ncx=ncx, ncz=ncz, chunk=CHUNK, cell=CELL, cv=CV, farCell=FAR_CELL, farNx=far_nx, farNz=far_nz, lambertE=CX, lambertN=CY)))
    shutil.copy(DEFAULT_SPAWN, out / "spawn.json")
    print("done, failed sectors:", failed)

if __name__ == "__main__":
    main()
