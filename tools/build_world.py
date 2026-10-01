"""Turn the downloaded LiDAR HD tiles + BD TOPO vectors (tools/fetch_big.py, tools/fetch_vectors.py) into streamable chunk files.

The 32 x 32 km area is cut into 10 x 10 sectors of 3.2 km. Each sector is processed independently (in parallel) together with a
240 m margin, so everything computed near a sector border sees the same neighbourhood from both sides and the seams match.
Every sector writes its 8 x 8 chunks of 400 m:

  m_{ci}_{cj}.bin.gz   "mid" data: terrain (4 m and 16 m grids), roads, junctions, water, buildings   (loaded out to ~5 km)
  n_{ci}_{cj}.bin.gz   "near" data: trees + shrubs                                     (loaded out to ~1.6 km)

and returns a 64 m far-terrain patch; build_world assembles all of them into far.bin (the whole map, always resident),
plus world.json (grid geometry, spawn).

Roads come finished from the road pipeline (tools/roads: `uv run python -m roads --list L build`, same sector list); here the terrain is
shaped around them, buildings and plants keep clear of them, and they are cut into chunks.

Local coordinates: x = east, z = north (metres from the Berat centre), y = elevation (m NGF). Chunk (ci, cj) covers
x in [-16000 + 400 ci, +400), z in [-16000 + 400 cj, +400).

Haute-Garonne extension: the sector list is data/big/hg_sectors.json (indices may be negative; sector (si, sj) covers x in
[-16000 + 3200 si, +3200), z likewise). The world origin (x0, z0) is the south-west corner of the sector list; chunk files are named with
indices RELATIVE to it (ci = floor((x - x0) / 400)). Sectors 0..9 x 0..9 read the float32 .npy tiles, all others the compressed files of
data/big/hg (see HG_README.txt). Terrain heights of a chunk are base + uint16 * step (step >= 5 mm, larger for mountain chunks).

Rebuilds: a sector leaves a key in <out>/keys, a hash of everything its chunks were made from (its roads, the raster and vector files
under it, this code and its tuning). A sector whose key is unchanged and whose chunks are all there is not built again (--fresh: build
every sector).

Usage: uv run python build_world.py [--sectors 4:4,4:5,5:4,5:5] [--jobs 8] [--out DIR] [--list data/big/hg_sectors.json] [--far-cache F]
       [--far-cell 64] [--fresh] [--skip-existing] [--min-free-gb G] [--stop-file F]   (whole region: --list data/big/region_sectors.json --far-cell 128 --out ../world_region)
       (the world geometry always comes from --list; --sectors only selects which of them to (re)build)
"""
import argparse, functools, os, shutil, gzip, math, hashlib, json, struct, sys, time, collections, zlib
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path
import numpy as np
import shapely
from pyproj import Transformer
from rasterio import features
from rasterio.transform import from_origin
from scipy.ndimage import (correlate, distance_transform_edt, gaussian_filter, label, map_coordinates, maximum_filter, uniform_filter, uniform_filter1d)
from scipy.spatial import cKDTree
from shapely.geometry import Point, box, shape
from shapely.strtree import STRtree
import pyproj
import rasterio
import fetch
import rasters
import vec_io
from fetch import CX, CY
from rasters import BIG, HALF, SECTOR, Mosaic
from vec_io import exists_vec, read_vec
from roads import build as road_build, config as road_config, surface as road_surface, terrain as road_terrain
from roads.digest import code_stamp, digest

CHUNK = 400                      # m
CPS = SECTOR // CHUNK            # 8 chunks per sector side
CELL = 4                         # terrain vertex spacing (m)
CV = CHUNK // CELL + 1           # 101 vertices per chunk side
LOD_CELL = 16                    # vertex spacing of the mid-distance terrain (m)
LV = CHUNK // LOD_CELL + 1       # 26
FAR_CELL = 64
MARGIN = 240
DEFAULT_OUT = Path("../world_hg")
DEFAULT_SPAWN = Path("../world/spawn.json")
PLACES = BIG / "places.json"      # the map's place names (tools/fetch_places.py), copied into every world
DEFAULT_LIST = BIG / "hg_sectors.json"
DEFAULT_FAR_CACHE = BIG / "far_hg.npz"
FAR_NEUTRAL = (96, 104, 88)       # far colour outside the covered sectors

# ------------------------------------------------------------------ rasters

class Window(Mosaic):
    """Raster window around one sector (with its margin): mnt / mnh at 2 m and ortho at 4 m."""
    def __init__(self, si, sj):
        self.size = SECTOR + 2 * MARGIN
        super().__init__(-HALF + si * SECTOR - MARGIN, -HALF + sj * SECTOR - MARGIN, self.size, self.size)

# ------------------------------------------------------------------ vectors

VECTOR_LAYERS = ("hydro_areas", "hydro_lines", "buildings")

def vector_bases(name, si, sj):
    """The vector files (without extension) a sector reads for one layer: its own and its eight neighbours'."""
    return [BIG / "vec" / f"{name}_{si + di}_{sj + dj}" for dj in (-1, 0, 1) for di in (-1, 0, 1)]

def load_vectors(name, si, sj):
    """Features of every vector file touching the sector's window, de-duplicated on cleabs."""
    seen, out = set(), []
    for base in vector_bases(name, si, sj):
        if not exists_vec(base): continue
        for feat in read_vec(base):
            key = feat["properties"].get("cleabs") or feat.get("id")
            if key in seen: continue
            seen.add(key); out.append(feat)
    return out

def poi_files():
    return [f for f in [BIG / "vec" / n for n in ("osm_poi.json", "osm_poi_hg.json")] + sorted((BIG / "vec").glob("osm_poi_hg_*.json.gz")) if f.exists()]

@functools.lru_cache(maxsize=1)
def load_pois():
    """OSM points of interest of every download (original block, Haute-Garonne bbox, one file per other departement), de-duplicated on (type, id)."""
    seen, out = set(), []
    for f in poi_files():
        text = gzip.open(f, "rt", encoding="utf-8").read() if f.suffix == ".gz" else f.read_text()
        for e in json.loads(text)["elements"]:
            if (e["type"], e["id"]) in seen: continue
            seen.add((e["type"], e["id"])); out.append(e)
    return out

@functools.lru_cache(maxsize=1)
def load_poi_points():
    """The points of interest that name a kind of building: (kinds, names, x, y in local coordinates), in file order."""
    kinds, names, lon, lat = [], [], [], []
    for e in load_pois():
        where = e if e["type"] == "node" else e["center"]
        kind = poi_kind(e["tags"])
        if not kind: continue
        kinds.append(kind); names.append(e["tags"].get("name", "")); lon.append(where.get("lon")); lat.append(where.get("lat"))
    x, y = Transformer.from_crs(4326, 2154, always_xy=True).transform(np.array(lon, float), np.array(lat, float))
    return kinds, names, x - CX, y - CY

def feature_bounds(f):
    """(min x, min y, max x, max y) of a GeoJSON polygon / multipolygon feature in its own coordinates, from the outer rings."""
    g = f["geometry"]
    rings = [g["coordinates"][0]] if g["type"] == "Polygon" else [poly[0] for poly in g["coordinates"]]
    xs = [c[0] for ring in rings for c in ring]; ys = [c[1] for ring in rings for c in ring]
    return min(xs), min(ys), max(xs), max(ys)

def local(coords):
    a = np.asarray(coords, float)[:, :2]
    return np.c_[a[:, 0] - CX, a[:, 1] - CY]

def geom_local(f):
    """GeoJSON feature geometry as a 2D shapely geometry in local coordinates."""
    return shapely.transform(shapely.force_2d(shape(f["geometry"])), lambda c: c - np.array([CX, CY]))

def dense_runs(geom, wbox, step=2.0):
    """Densify each line of the FULL feature from its own start (so every sector gets the same vertices), then keep the runs of points inside wbox."""
    out = []
    for line in lines_of(geom):
        xy = densify(np.asarray(line.coords)[:, :2], step)
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

# ------------------------------------------------------------------ polylines

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
CARRIED_WATER = 1.5             # water standing this far above the ground under it, with a road passing under it, is carried by a structure (an aqueduct)
TROUGH_REACH = WATER_DEPTH + 0.15   # ... and its trough goes on as long as the ground under the water lies deeper than this (deeper than a bed)

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
    A function of position only, so adjacent sectors and chunks agree, and a long river follows its own slope.
    Ground lying CARRIED_WATER or more below that level, under the water or on its bank, is a void the water is carried over (a canal on an
    aqueduct over a road trench): it is left out and the level is taken again from the rest."""
    mask = np.zeros((nv, nv), bool)
    if areas:
        tr = from_origin(vx0 - CELL / 2, vz0 + (nv - 0.5) * CELL, CELL, CELL)               # north-up raster of the same cells
        mask = features.rasterize([(a["poly"], 1) for a in areas], out_shape=(nv, nv), transform=tr, dtype=np.uint8, all_touched=True).astype(bool)[::-1]
    from scipy.ndimage import binary_dilation
    box_ = 31
    bank = binary_dilation(mask, iterations=3) & ~binary_dilation(mask, iterations=1)

    def level_of(wet, dry):
        w = wet.astype(np.float32); den = uniform_filter(w, box_, mode="constant")
        inside = uniform_filter(h0 * w, box_, mode="constant") / np.maximum(den, 1e-6)
        wb = dry.astype(np.float32); denb = uniform_filter(wb, box_, mode="constant")
        bankh = uniform_filter(h0 * wb, box_, mode="constant") / np.maximum(denb, 1e-6)
        return np.where(denb > 2e-3, np.minimum(inside, bankh - 0.15), inside), den

    level, _ = level_of(mask, bank)
    void = h0 < level - CARRIED_WATER
    if (mask & void).any():
        again, den = level_of(mask & ~void, bank & ~void)
        level = np.where(den > 1e-3, again, level)                                  # (no water left within the box: keep the first level)
    return mask, level.astype(np.float32)

def carried_water(areas, ground, mask, level, pieces, vx0, vz0, nv):
    """Plan polygons of the water carried by a structure (a canal on an aqueduct over a road trench). Seeds: water over ground lying CARRIED_WATER
    or more below its level, with a road on the ground passing under it at least that far below (water merely levelled too high over a sloping
    shore has no road under it). From there the trough goes on through the water for as long as the ground under it lies deeper than a bed
    (TROUGH_REACH), so it reaches the banks across the slopes of the trench. `ground` is the finished terrain. The game draws the channel and
    keeps the ground under it dry."""
    void = mask & (ground < level - CARRIED_WATER)
    if not void.any(): return []
    labels, _ = label(void)
    keep = set()
    for p in pieces:
        if p.bridge: continue
        c = np.round((p.xy - (vx0, vz0)) / CELL).astype(int)
        inside = ((c >= 0) & (c < nv)).all(axis=1)
        c, z = c[inside], p.z[inside]
        under = labels[c[:, 1], c[:, 0]]
        keep.update(under[(under > 0) & (z < level[c[:, 1], c[:, 0]] - CARRIED_WATER)].tolist())
    if not keep: return []
    seeds = np.isin(labels, sorted(keep))
    deep, _ = label(mask & (ground < level - TROUGH_REACH))                           # every seed lies in one of these
    cells = np.isin(deep, np.unique(deep[seeds]))[::-1]                               # north-up, like the raster the water mask was burnt from
    tr = from_origin(vx0 - CELL / 2, vz0 + (nv - 0.5) * CELL, CELL, CELL)
    region = shapely.union_all([shape(g) for g, v in features.shapes(cells.astype(np.uint8), mask=cells, transform=tr) if v == 1]).buffer(CELL / 2, join_style="mitre")
    out = []
    for a in areas:
        part = a["poly"].intersection(region)
        out += [q for q in getattr(part, "geoms", [part]) if q.geom_type == "Polygon" and q.area > 4]
    return out

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

def chunk_of(x, z):
    return int(np.floor((x + HALF) / CHUNK)), int(np.floor((z + HALF) / CHUNK))

def put_roads(buf, items):
    """Road records of a chunk: [(piece, first sample, one past the last sample)]."""
    wi(buf, len(items))
    for p, a, b in items:
        e = p.edge
        buf += bytes([(1 if e.dirt else 0) | (2 if p.bridge else 0) | (4 if e.lit else 0), int(e.importance) if e.importance.isdigit() else 0,
                      p.limits[0], e.avg, p.oneway, e.lanes, e.kind, e.road_class.rank])
        wi(buf, zlib.crc32(e.cleabs.encode()) & 0x7FFFFFFF); wf(buf, e.width_real, float(p.hw[a:b].mean()), float(p.s[a]))
        wstr(buf, e.name); wi(buf, b - a)
        wfa(buf, np.c_[p.xy[a:b, 0], p.z[a:b], p.xy[a:b, 1]])                       # centreline, then the two edges: x, height, north
        for edge in (p.left, p.right): wfa(buf, edge[a:b][:, [0, 2, 1]])
        buf += p.drawn[a:b - 1].astype(np.uint8).tobytes()
        lines = [(i - a, after) for i, after in p.give_way if (a <= i < b - 1 if after else a < i <= b - 1)]      # those whose drawn segment is in this run
        buf.append(len(lines))
        for i, after in lines: buf += struct.pack("<HB", i, 1 if after else 0)
        wstr(buf, e.surface); buf.append(p.limits[1])                                # BM07: OSM surface, limit against the piece's direction
        put_lane_marks(buf, p, a, b)

def put_lane_marks(buf, p, a, b):
    """BM07 lane markings of a road run (samples a .. b - 1): the lane lines (kind, then per point the fraction of the way from the
    left edge to the right one, -1 where it is not painted), per segment the paint flags, then the turn arrows."""
    buf.append(len(p.line_kinds)); buf += p.line_kinds.astype(np.uint8).tobytes()
    hw = np.maximum(p.hw[a:b], 1e-3)
    for k in range(len(p.line_kinds)):
        wfa(buf, np.nan_to_num((hw - p.line_offsets[a:b, k]) / (2.0 * hw), nan=-1.0))
    seg = slice(a, b - 1)                                                            # bit 0 painted, 1 / 2 no overtaking along / against, 3-4 edge style
    flags = (p.marked[seg].astype(np.uint8) | (p.no_overtaking[seg, 0].astype(np.uint8) << 1) | (p.no_overtaking[seg, 1].astype(np.uint8) << 2)
             | (p.edge_style[seg].astype(np.uint8) << 3))
    buf += flags.tobytes()
    inside = [w for w in p.arrows if run_holds(p, a, b, w)]
    buf.append(len(inside))
    for x, north, z, dx, dn, bits in inside: wf(buf, x, z, north, dx, dn); buf.append(bits)

def run_holds(p, a, b, arrow):
    """Is the arrow nearest to a sample of this run (each arrow is written once, with the run that holds it)?"""
    i = int(np.argmin(np.hypot(p.xy[:, 0] - arrow[0], p.xy[:, 1] - arrow[1])))
    return a <= i < b - 1 or (i == b - 1 == len(p.xy) - 1)

def put_junctions(buf, items):
    """Junction meshes of a chunk: [(junction, vertices)]."""
    wi(buf, len(items))
    for j, v in items:
        buf.append(0 if j.paved else 1)
        wi(buf, len(v)); wfa(buf, v[:, [0, 2, 1]])
        tri = road_surface.junction_triangles(j, v)
        wi(buf, len(tri)); buf += tri.astype("<u2").tobytes()
        wi(buf, len(j.boundary)); buf += j.boundary[:, :2].astype("<u2").tobytes(); buf += j.boundary[:, 2].astype(np.uint8).tobytes()

@functools.lru_cache(maxsize=1)
def code_key():
    """What this process builds sectors with: the code, its tuning, the libraries and the points of interest."""
    modules = [sys.modules[__name__], rasters, vec_io, fetch, road_surface, road_terrain]
    return digest(code_stamp(*modules), [rasterio.__version__, pyproj.__version__], [rasters.file_stamp(f) for f in poi_files()])

def sector_key(si, sj, ci0, cj0, far_cell, pieces, meshes):
    """Hash of everything the chunks of a sector are made from. Of the roads, what the builder reads: not where a link or a node sits in the network's lists."""
    roads = digest(pieces, [(j.paved, j.polygon, j.centre, j.plane, j.triangles, j.boundary, v) for j, v in meshes], skip=("link", "a", "b"))
    size = SECTOR + 2 * MARGIN
    ground = rasters.stamp(-HALF + si * SECTOR - MARGIN, -HALF + sj * SECTOR - MARGIN, size, size, kinds=("mnt", "mnh", "ortho"))
    vectors = [rasters.file_stamp(vec_io.vec_file(base) or base) for name in VECTOR_LAYERS for base in vector_bases(name, si, sj)]
    return digest(roads, ground, vectors, code_key(), [si, sj, ci0, cj0, far_cell])

def key_file(out_dir, si, sj):
    return Path(out_dir).parent / "keys" / f"sector_{si}_{sj}.key"

def chunk_files(out_dir, si, sj, ci0, cj0):
    return [Path(out_dir) / f"{kind}_{ci - ci0}_{cj - cj0}.bin.gz" for cj in range(sj * CPS, (sj + 1) * CPS) for ci in range(si * CPS, (si + 1) * CPS) for kind in "mn"]

def process_sector(args):
    si, sj, out_dir, ci0, cj0, far_cell, road_tag, reuse = args                                # (ci0, cj0): chunk index of the world origin
    t0 = time.time()
    out_dir = Path(out_dir)
    ox, oz = -HALF + si * SECTOR, -HALF + sj * SECTOR

    # ---- roads: the finished surface of the road pipeline, cut to this window (the same geometry in every sector, so borders match)
    pieces, meshes = road_build.load_sector(road_tag, si, sj)
    made_from, stamp = sector_key(si, sj, ci0, cj0, far_cell, pieces, meshes), key_file(out_dir, si, sj)
    if reuse and stamp.exists() and stamp.read_text() == made_from and all(f.exists() for f in chunk_files(out_dir, si, sj, ci0, cj0)):
        return si, sj, None, None, dict(sector=(si, sj), unchanged=True, secs=round(time.time() - t0, 1))
    stamp.unlink(missing_ok=True)                                                    # from here on the chunks on disk are not what that key described
    win = Window(si, sj)
    wbox = box(win.x0 + 10, win.z0 + 10, win.x0 + win.size - 10, win.z0 + win.size - 10)
    road_pts = np.vstack([np.c_[p.xy, p.z, p.hw] for p in pieces if not p.bridge] or [np.zeros((0, 4))])       # centreline samples on the ground: x, north, height, half width
    footprint = road_terrain.Footprint(road_surface.footprints(pieces, meshes), win.x0, win.z0, win.size, win.size)
    cloud = road_surface.cloud(pieces, meshes)
    corr = [g.buffer(0.35) for g in [q for q in (p.polygon() for p in pieces) if q is not None] + [j.polygon for j, _ in meshes]]
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
    def vertices_near(seg, reach):
        """(indices into `pts`, distance to the polyline `seg`, nearest point of it) of the grid vertices within `reach` of it."""
        lo = np.maximum(np.floor((seg[:, :2].min(axis=0) - reach - (win.x0, win.z0)) / CELL).astype(int) - 1, 0)
        hi = np.minimum(np.ceil((seg[:, :2].max(axis=0) + reach - (win.x0, win.z0)) / CELL).astype(int) + 1, nv - 1)
        if (hi < lo).any(): return np.zeros(0, int), np.zeros(0), np.zeros(0, int)
        at = (np.arange(lo[1], hi[1] + 1)[:, None] * nv + np.arange(lo[0], hi[0] + 1)).ravel()      # the vertices of the bounding box: nothing further away can be within reach
        dd, ii = cKDTree(seg[:, :2]).query(pts[at], distance_upper_bound=reach)
        m = np.isfinite(dd)
        return at[m], dd[m], ii[m]
    for w in wlines:
        seg = densify_pts(np.c_[w["xy"], w["y"]], 1.0) if len(w["xy"]) > 1 else w["xy"]
        m, dd, ii = vertices_near(seg, w["hw"] + 3.0)
        if not len(m): continue
        bed = seg[ii, 2] - WATER_DEPTH + np.maximum(dd - w["hw"], 0) * 0.35
        h[m] = np.minimum(h[m], bed)
    for p in pieces:                                                                 # under a bridge deck the ground falls away, so the terrain mesh can never poke through the deck
        if not p.bridge: continue
        seg, hw = densify_pts(np.c_[p.xy, p.z], 1.0), float(p.hw.max())
        m, dd, ii = vertices_near(seg, hw + 6.0)
        if not len(m): continue
        drop = 1.4 * (1.0 - np.clip((dd - (hw + 2.5)) / 3.5, 0.0, 1.0))
        h[m] = np.minimum(h[m], seg[ii, 2] - drop)
    # the road always wins: the ground is shaped around the road surface, and no terrain triangle (4 m or 16 m) may stand above it
    H = road_terrain.blend(h.reshape(nv, nv), win.x0, win.z0, CELL, footprint, cloud)
    H = road_terrain.bench(H, win.x0, win.z0, CELL, footprint, cloud, road_config.ROAD_SINK)
    troughs = carried_water(areas, H, wmask, wlevel, pieces, win.x0, win.z0, nv)
    lod_kernel = np.zeros((5, 5)); lod_kernel[::2, ::2] = 1.0 / 9.0                  # each 16 m vertex averages the 3 x 3 vertices 8 m around it
    LOW = correlate(H, lod_kernel, mode="nearest")[::LOD_CELL // CELL, ::LOD_CELL // CELL]
    LOW = road_terrain.bench(LOW, win.x0, win.z0, LOD_CELL, footprint, road_surface.thin(cloud, 4), road_config.LOD_SINK)

    # ---- ground colour
    ortho = [win.ortho[..., k].astype(np.float32) for k in range(3)]
    rgb = np.stack([win.sample(band, gx.ravel(), gz.ravel(), 4) for band in ortho], axis=1).reshape(nv, nv, 3)
    cov = maximum_filter(win.mnh, 3) > 1.3
    covered = win.sample(cov.astype(np.float32), gx.ravel(), gz.ravel(), 2).reshape(nv, nv) > 0.4
    C = ground_colour(rgb, covered)
    far_raw = soften(grade(gaussian_filter(rgb, sigma=(2, 2, 0))))                  # far view keeps forests and villages (no cover removal)
    low_col = uniform_filter(far_raw, size=(4, 4, 1), mode="nearest")               # 16 m LOD colours

    # ---- buildings
    road_tree = cKDTree(road_pts[:, :2]) if len(road_pts) else None
    owned = box(ox, oz, ox + SECTOR, oz + SECTOR); owned_wide = owned.buffer(6)
    buildings, bpolys = [], []
    reach_lo, reach_hi = (ox + CX - 8, oz + CY - 8), (ox + CX + SECTOR + 8, oz + CY + SECTOR + 8)      # `owned_wide` and a bit, in the features' coordinates
    for f in load_vectors("buildings", si, sj):
        bx0, by0, bx1, by1 = feature_bounds(f)
        if bx1 < reach_lo[0] or by1 < reach_lo[1] or bx0 > reach_hi[0] or by0 > reach_hi[1]: continue      # its centroid cannot lie in the sector
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
            around = poly.buffer(1.0)
            gx0, gz0, gx1, gz1 = around.bounds
            gxs, gzs = np.meshgrid(np.arange(gx0, gx1 + 1e-6, 0.75), np.arange(gz0, gz1 + 1e-6, 0.75))
            inside = shapely.contains_xy(around, gxs.ravel(), gzs.ravel())
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
            roof = np.clip(np.array([win.sample(band, px, pz, 4) for band in ortho]).mean(axis=1) * 1.1, 0, 255).astype(int).tolist()
            buildings.append(dict(k=kind, n="", fe=fe, tw=tower, cp=cp, cn=[len(ring)], p=np.round(ring, 2).ravel().tolist(), b=round(ground - 0.8, 2), h=round(wall + 0.8, 2),
                                  r=round(rise, 2), rc=[round(float(v), 2) for v in (*mrr.centroid.coords[0], *axis, long_, short)] if pitched else [], c=roof, w=list(WALLS[seed % len(WALLS)])))
            bpolys.append(poly)

    # ---- collision rings proper (a building crossing a road keeps the road corridor free, computed against the uncut original in export_world; here the visual is already cut)
    # ---- POIs -> kind + name
    if bpolys:
        strtree = STRtree(bpolys)
        poi_kinds, poi_names, poi_x, poi_y = load_poi_points()
        for e in np.flatnonzero(shapely.contains_xy(owned.buffer(20), poi_x, poi_y)):
            kind, pt = poi_kinds[e], Point(poi_x[e], poi_y[e])
            hit = [i for i in strtree.query(pt) if bpolys[i].contains(pt)]
            if not hit:
                j = int(strtree.nearest(pt))
                if bpolys[j].distance(pt) > 15: continue
                hit = [j]
            b = buildings[hit[0]]
            if kind == "church" and b["k"] == "chapel": kind = "chapel"
            b["k"] = kind; b["n"] = poi_names[e]
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
    keep = footprint.distance(tx_, tz_) >= 1.8                                        # nothing grows on a road or right beside it
    trees = np.c_[tx_[keep], win.sample(win.mnt, tx_[keep], tz_[keep], 2), tz_[keep], sm[rr, cc][keep]].astype("<f4")

    low = (sm >= 0.9) & (sm < 2.5) & ~bmask
    lpeak = (sm == maximum_filter(sm, size=3)) & low
    lr, lc = np.nonzero(lpeak)
    sx_, sz_ = win.x0 + (lc + 0.5) * 2.0, win.z1 - (lr + 0.5) * 2.0
    m = (sx_ >= ox) & (sx_ < ox + SECTOR) & (sz_ >= oz) & (sz_ < oz + SECTOR)
    lr, lc, sx_, sz_ = lr[m], lc[m], sx_[m], sz_[m]
    sk = footprint.distance(sx_, sz_) >= 1.2
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
    own = lambda key: si * CPS <= key[0] < (si + 1) * CPS and sj * CPS <= key[1] < (sj + 1) * CPS          # a chunk this sector writes
    apron = lambda key: (-HALF + key[0] * CHUNK - 25, -HALF + key[1] * CHUNK - 25, -HALF + (key[0] + 1) * CHUNK + 25, -HALF + (key[1] + 1) * CHUNK + 25)
    road_b, ctx_b = collections.defaultdict(list), collections.defaultdict(list)
    for p in pieces:
        for key, s, e in runs_by_chunk(p.xy): road_b[key].append((p, s, e))
        # context: the parts inside a neighbouring chunk's 25 m apron that belong to another chunk (ground height and obstacle clearance next to roads)
        ci = np.floor((p.xy[:, 0] + HALF) / CHUNK).astype(int); cj = np.floor((p.xy[:, 1] + HALF) / CHUNK).astype(int)
        for nk in {(k[0] + dx, k[1] + dz) for k in set(zip(ci.tolist(), cj.tolist())) for dx in (-1, 0, 1) for dz in (-1, 0, 1)}:
            if not own(nk): continue
            x0_, z0_, x1_, z1_ = apron(nk)
            inb = (p.xy[:, 0] >= x0_) & (p.xy[:, 0] < x1_) & (p.xy[:, 1] >= z0_) & (p.xy[:, 1] < z1_) & ~((ci == nk[0]) & (cj == nk[1]))
            idx = np.where(inb)[0]
            for seg in (np.split(idx, np.where(np.diff(idx) != 1)[0] + 1) if len(idx) else []):
                lo, hi = max(seg[0] - 1, 0), min(seg[-1] + 2, len(p.xy))
                if hi - lo >= 2: ctx_b[nk].append((p, lo, hi))
    junc_b, jctx_b = collections.defaultdict(list), collections.defaultdict(list)
    for j, v in meshes:
        home = chunk_of(*j.centre); junc_b[home].append((j, v))
        lo, hi = v[:, :2].min(axis=0), v[:, :2].max(axis=0)
        for nk in {(home[0] + dx, home[1] + dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1)} - {home}:
            if own(nk) and road_surface.touches(lo, hi, apron(nk)): jctx_b[nk].append((j, v))
    def rings_by_chunk(polygons):
        """Each polygon cut to the owned chunks it touches (1 m beyond their border), as rings of (x, water level, z), counter-clockwise."""
        out = collections.defaultdict(list)
        for poly in polygons:
            minx, minz, maxx, maxz = poly.bounds
            for cj in range(int((minz + HALF) // CHUNK), int((maxz + HALF) // CHUNK) + 1):
                for ci in range(int((minx + HALF) // CHUNK), int((maxx + HALF) // CHUNK) + 1):
                    if not (ox <= -HALF + ci * CHUNK < ox + SECTOR and oz <= -HALF + cj * CHUNK < oz + SECTOR): continue
                    piece = poly.intersection(box(-HALF + ci * CHUNK - 1, -HALF + cj * CHUNK - 1, -HALF + (ci + 1) * CHUNK + 1, -HALF + (cj + 1) * CHUNK + 1))
                    for q in ([piece] if piece.geom_type == "Polygon" else [x for x in getattr(piece, "geoms", []) if x.geom_type == "Polygon"]):
                        if q.area <= 4: continue
                        ring = np.asarray(shapely.geometry.polygon.orient(q, 1.0).exterior.coords)[:-1]
                        ly = map_coordinates(wlevel, [(ring[:, 1] - win.z0) / CELL, (ring[:, 0] - win.x0) / CELL], order=1, mode="nearest")
                        out[(ci, cj)].append(np.c_[ring[:, 0], ly, ring[:, 1]])
        return out
    area_b, trough_b, wline_b = rings_by_chunk([a["poly"] for a in areas]), rings_by_chunk(troughs), collections.defaultdict(list)
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
            buf = bytearray(b"BM07"); wi(buf, ci - ci0); wi(buf, cj - cj0); wi(buf, CV); wf(buf, base, step)
            buf += q.tobytes(); buf += tc.astype(np.uint8).tobytes()
            buf += np.clip(low_col[r0:r0 + CV:4, c0:c0 + CV:4], 0, 255).astype(np.uint8).tobytes()
            wfa(buf, LOW[r0 // 4:r0 // 4 + LV, c0 // 4:c0 // 4 + LV])                 # heights of the 16 m terrain (already kept below the roads)
            put_roads(buf, road_b.get(key, [])); put_roads(buf, ctx_b.get(key, []))
            put_junctions(buf, junc_b.get(key, [])); put_junctions(buf, jctx_b.get(key, []))
            wi(buf, len(area_b.get(key, [])))
            for ring in area_b.get(key, []): wi(buf, len(ring)); wfa(buf, ring.ravel())
            wi(buf, len(wline_b.get(key, [])))
            for hw, seg, lead, trail in wline_b.get(key, []): wf(buf, hw); buf.append(lead); buf.append(trail); wi(buf, len(seg)); wfa(buf, seg.ravel())
            wi(buf, len(trough_b.get(key, [])))                                         # BM06: outlines of the water carried by a structure
            for ring in trough_b.get(key, []): wi(buf, len(ring)); wfa(buf, ring.ravel())
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
    fv = np.arange(SECTOR // far_cell + 1) * far_cell
    fgx, fgz = np.meshgrid(ox + fv, oz + fv)
    coarse = uniform_filter(win.mnt, far_cell // 4, mode="nearest")                   # box of a quarter cell (32 m at 64 m) before sampling
    fh = win.sample(coarse, fgx.ravel(), fgz.ravel(), 2).reshape(fgx.shape)
    if far_cell > FAR_CELL: far_raw = uniform_filter(far_raw, size=(far_cell // 16, far_cell // 16, 1), mode="nearest")     # coarser far grid: average the colour instead of aliasing it
    fc = np.stack([map_coordinates(far_raw[..., k], [(fgz.ravel() - win.z0) / CELL, (fgx.ravel() - win.x0) / CELL], order=1, mode="nearest") for k in range(3)], axis=1).reshape(fgx.shape + (3,))
    stamp.parent.mkdir(exist_ok=True)
    stamp.write_text(made_from)
    stats = dict(sector=(si, sj), chunks=n_chunks, roads=len(pieces), junctions=len(meshes), buildings=len(buildings), trees=len(trees), shrubs=len(shrubs), water_areas=len(areas), water_lines=len(wlines), troughs=len(troughs), secs=round(time.time() - t0, 1))
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

def chunks_done(out, si, sj, ci0, cj0, script_mtime):
    """True when all 64 m_ chunk files of the sector exist and are newer than this script."""
    for cj in range(sj * CPS, (sj + 1) * CPS):
        for ci in range(si * CPS, (si + 1) * CPS):
            f = out / "chunks" / f"m_{ci - ci0}_{cj - cj0}.bin.gz"
            if not f.exists() or f.stat().st_mtime <= script_mtime: return False
    return True

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sectors", default="", help="comma list si:sj to (re)build (default: every sector of --list)")
    ap.add_argument("--list", default=str(DEFAULT_LIST), help="sector list json (defines the world geometry)")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--far-cache", default=str(DEFAULT_FAR_CACHE), help="npz keeping the far arrays between partial rebuilds")
    ap.add_argument("--far-cell", type=int, default=FAR_CELL, help="far terrain vertex spacing in metres (divides 3200, multiple of 16)")
    ap.add_argument("--fresh", action="store_true", help="build every sector, even those whose inputs did not change since their chunks were written")
    ap.add_argument("--skip-existing", action="store_true", help="skip a sector whose 64 m_ chunk files exist, are newer than this script and whose far patch is in the far cache")
    ap.add_argument("--min-free-gb", type=float, default=0.0, help="stop submitting new sectors when the free disk space of --out falls below this (0 = no check)")
    ap.add_argument("--stop-file", default="", help="stop submitting new sectors as soon as this file exists")
    a = ap.parse_args()
    far_cell = a.far_cell
    road_tag = road_build.tag_of(a.list)
    world = [tuple(s) for s in json.loads(Path(a.list).read_text())["sectors"]]
    if not road_build.sector_path(road_tag, *world[0]).exists():
        sys.exit(f"no roads for {a.list}: run `uv run python -m roads --list {a.list} build` first")
    out = Path(a.out); (out / "chunks").mkdir(parents=True, exist_ok=True)
    si_min, si_max = min(s[0] for s in world), max(s[0] for s in world)
    sj_min, sj_max = min(s[1] for s in world), max(s[1] for s in world)
    x0, z0 = -HALF + SECTOR * si_min, -HALF + SECTOR * sj_min
    ncx, ncz = CPS * (si_max - si_min + 1), CPS * (sj_max - sj_min + 1)
    per = SECTOR // far_cell
    far_nx, far_nz = ncx * CHUNK // far_cell + 1, ncz * CHUNK // far_cell + 1
    secs = [tuple(map(int, s.split(":"))) for s in a.sectors.split(",")] if a.sectors else world
    for s in secs: assert s in world, f"sector {s} is not in {a.list}"

    far_path = Path(a.far_cache)
    far_h = np.zeros((far_nz, far_nx), np.float32); far_c = np.zeros((far_nz, far_nx, 3), np.uint8); covered = np.zeros((far_nz, far_nx), bool)
    if far_path.exists():
        z = np.load(far_path)
        if z["h"].shape == far_h.shape: far_h, far_c, covered = z["h"], z["c"], z["covered"]
        else: print("far cache has another shape, ignored")
    def save_cache():
        tmp = far_path.with_name(far_path.stem + ".tmp.npz")
        np.savez(tmp, h=far_h, c=far_c, covered=covered); os.replace(tmp, far_path)

    if a.skip_existing:
        mtime = Path(__file__).stat().st_mtime
        todo = [s for s in secs if not (chunks_done(out, *s, CPS * si_min, CPS * sj_min, mtime) and covered[(s[1] - sj_min) * per, (s[0] - si_min) * per])]
        print(f"--skip-existing: {len(secs) - len(todo)} of {len(secs)} sectors already done", flush=True)
        secs = todo
    def far_patch(i, j):
        r, c = (j - sj_min) * per, (i - si_min) * per
        return slice(r, r + per + 1), slice(c, c + per + 1)
    # an unchanged sector may be skipped only if its far patch is still in the cache
    jobs = [(i, j, str(out / "chunks"), CPS * si_min, CPS * sj_min, far_cell, road_tag, not a.fresh and bool(covered[far_patch(i, j)].all())) for i, j in secs]

    def stop_reason():
        if a.stop_file and Path(a.stop_file).exists(): return f"stop file {a.stop_file}"
        free = shutil.disk_usage(out).free / 2**30
        if a.min_free_gb and free < a.min_free_gb: return f"free disk {free:.1f} GB < {a.min_free_gb} GB"
        return None

    failed, unchanged, t0, n_done, stopped = [], 0, time.time(), 0, None
    pending, todo_iter = set(), iter(jobs)
    with ProcessPoolExecutor(a.jobs, max_tasks_per_child=40) as ex:
        exhausted = False
        while True:
            while not exhausted and not stopped and len(pending) < a.jobs + 1:              # keep the pool busy without queuing everything, so a stop takes effect quickly
                stopped = stop_reason()
                if stopped: print(f"STOPPING (no new sectors): {stopped}", flush=True); break
                j = next(todo_iter, None)
                if j is None: exhausted = True; break
                pending.add(ex.submit(process_safe, j))
            if not pending: break
            done = next(iter(wait(pending, return_when=FIRST_COMPLETED)[0]))
            pending.discard(done)
            try: si, sj, fh, fc, st = done.result()
            except Exception as e:                                                             # worker died (e.g. out of memory): the pool is unusable
                print(f"POOL BROKEN {e!r}: saving the far cache, re-run with --skip-existing", flush=True); save_cache(); raise
            n_done += 1
            if st.get("unchanged"):
                unchanged += 1
                if unchanged % 50 == 0: print(f"[{n_done}/{len(jobs)}] {unchanged} sectors unchanged so far  elapsed {time.time() - t0:.0f}s", flush=True)
                continue
            if fh is None:
                failed.append((si, sj)); print(f"[{n_done}/{len(jobs)}] FAILED {si}:{sj} {st['error']}\n{st['trace']}", flush=True); continue
            patch = far_patch(si, sj)
            far_h[patch] = fh; far_c[patch] = fc; covered[patch] = True
            print(f"[{n_done}/{len(jobs)}] {st}  elapsed {time.time() - t0:.0f}s", flush=True)
            if n_done % 25 == 0: save_cache()
    save_cache()
    fh_, fc_ = fill_far(far_h, far_c, covered)
    with open(out / "far.bin", "wb") as fo:
        fo.write(struct.pack("<iiffff", far_nx, far_nz, far_cell, x0, z0, 0.0))
        fo.write(fh_.astype("<f4").tobytes()); fo.write(fc_.tobytes())
    (out / "world.json").write_text(json.dumps(dict(x0=x0, z0=z0, ncx=ncx, ncz=ncz, chunk=CHUNK, cell=CELL, cv=CV, farCell=far_cell, farNx=far_nx, farNz=far_nz, lambertE=CX, lambertN=CY)))
    shutil.copy(DEFAULT_SPAWN, out / "spawn.json")
    if PLACES.exists(): shutil.copy(PLACES, out / "places.json")
    else: print(f"no {PLACES}: the map will have no place names (run fetch_places.py)", flush=True)
    print("stopped early:" if stopped else "done,", stopped or "", f"{n_done - unchanged - len(failed)} sectors built, {unchanged} unchanged, failed sectors:", failed, flush=True)

if __name__ == "__main__":
    main()
