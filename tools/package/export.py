"""Write the map package of a sector list (docs/map-package.md): one folder per sector, then manifest.json.

Each sector is computed by build_world.compute_sector (the same terrain, water, buildings, plants and ground classes as the world
build) on the road pipeline's finished roads, then written in the package's formats. Heights are first written as float32 GeoTIFF;
once every sector is done their common range is known and the 16-bit PNGs are written from them.
"""
import machine                    # first: half the machine, single-threaded maths
import datetime, gzip, json, os, shutil, subprocess, time, traceback
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

import numpy as np
import rasterio
import shapely
from PIL import Image
from pyproj import Transformer
from rasterio import features as rfeatures
from rasterio.transform import from_origin
from scipy.ndimage import map_coordinates
from shapely.geometry import box

import build_world
import ground as land
import sources
from fetch import CX, CY
from rasters import HALF, SECTOR
from roads import build as road_build

from . import buildings as pbuildings
from . import gltf
from . import hires
from . import roads as proads
from . import terrain

FORMAT, VERSION = "berat-map-package", 1
TREE_KINDS = ["unknown", "broadleaf", "conifer", "poplar", "fruit"]
SOURCES = [
    dict(name="LiDAR HD (MNT, MNH)", by="IGN", licence="Licence Ouverte 2.0", use="ground heights, canopy, roofs, levels"),
    dict(name="RGE ALTI", by="IGN", licence="Licence Ouverte 2.0", use="ground where LiDAR HD is missing"),
    dict(name="BD ORTHO", by="IGN", licence="Licence Ouverte 2.0", use="ground and roof colours, crop row directions"),
    dict(name="BD TOPO", by="IGN", licence="Licence Ouverte 2.0", use="roads, buildings, water, vegetation, transport areas"),
    dict(name="BD Haie", by="IGN", licence="Licence Ouverte 2.0", use="hedges"),
    dict(name="RPG (Registre Parcellaire Graphique)", by="IGN / ASP", licence="Licence Ouverte 2.0", use="crop per field"),
    dict(name="OpenStreetMap", by="OpenStreetMap contributors", licence="ODbL 1.0", use="road attributes, speed limits, junction controls, land use, points of interest, place names"),
]


def sector_dir(out, si, sj):
    return Path(out) / "sectors" / f"{si}_{sj}"


def north_up(a):
    """Rows south-first (the computation's) to north-first (the images')."""
    return np.ascontiguousarray(a[::-1])


def write_png(path, a):
    Image.fromarray(np.ascontiguousarray(a)).save(path, optimize=False, compress_level=6)


def write_json_gz(path, obj):
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=6) as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def write_geojson(path, feats):
    Path(path).write_text(json.dumps(dict(type="FeatureCollection", features=feats), ensure_ascii=False, separators=(",", ":")))


def sample(h, origin, cell, x, y):
    """The carved terrain (row 0 south, `cell` m) at plan points."""
    return map_coordinates(h, [(np.asarray(y) - origin[1]) / cell, (np.asarray(x) - origin[0]) / cell], order=1, mode="nearest")


# ------------------------------------------------------------------ layers

def land_cover(w, origin, cell):
    """(classes, row directions) per terrain vertex of the sector (row 0 south); car parks get the parking class."""
    n = terrain.samples(cell)
    cls, rows = land.rasterize(w.garea, origin[0], origin[1], n, cell)
    parks = [shapely.union_all(shapely.polygons(v[t][:, :, :2])) for v, t in w.park_meshes]
    if parks:
        transform = from_origin(origin[0] - cell / 2, origin[1] + (n - 0.5) * cell, cell, cell)
        mask = rfeatures.rasterize([(p, 1) for p in parks], out_shape=cls.shape, transform=transform, dtype=np.uint8)[::-1].astype(bool)
        cls[mask] = land.PARKING
    return cls, rows


def colour(w, origin):
    """The bare-ground colour (orthophoto with canopy and roads replaced, graded) on the 4 m vertices of the sector, row 0 south."""
    r0 = int(round((origin[1] - w.win.z0) / build_world.CELL)); c0 = int(round((origin[0] - w.win.x0) / build_world.CELL))
    n = SECTOR // build_world.CELL + 1
    return np.clip(w.C[r0:r0 + n, c0:c0 + n], 0, 255).astype(np.uint8)


def vegetation(w, h, origin, cell):
    sample_ = lambda x, y: sample(h, origin, cell, x, y)

    def rows(a, kinds=None):
        z = sample_(a[:, 0], a[:, 2]) if len(a) else np.zeros(0)
        out = np.c_[a[:, 0], a[:, 2], z, a[:, 3]] if len(a) else np.zeros((0, 4))
        out = np.round(out, 2)
        if kinds is not None:
            return [[*r, int(k)] for r, k in zip(out.tolist(), kinds)]
        return out.tolist()
    hedges = [dict(height=round(float(hh), 2), points=np.round(np.c_[xy, sample_(xy[:, 0], xy[:, 1])], 2).tolist()) for hh, xy in w.hedge_list]
    vines = np.asarray(w.vines, float).reshape(-1, 4)
    vine_rows = [[round(a, 2) for a in (x0, y0, float(sample_([x0], [y0])[0]), x1, y1, float(sample_([x1], [y1])[0]))] for x0, y0, x1, y1 in vines.tolist()]
    return dict(trees=dict(columns=["x", "y", "z", "height", "kind"], kinds=TREE_KINDS, rows=rows(w.trees, w.tree_kind)),
                shrubs=dict(columns=["x", "y", "z", "height"], rows=rows(w.shrubs)),
                hedges=hedges, vines=dict(columns=["x0", "y0", "z0", "x1", "y1", "z1"], rows=vine_rows))


def water(w, origin):
    """Water areas cut to the sector (with the level at every outline point), streams owned by the sector, aqueduct troughs."""
    square = box(origin[0], origin[1], origin[0] + SECTOR, origin[1] + SECTOR)
    win = w.win

    def level(xy):
        return map_coordinates(w.wlevel, [(xy[:, 1] - win.z0) / build_world.CELL, (xy[:, 0] - win.x0) / build_world.CELL], order=1, mode="nearest")

    def polygons(polys, kind):
        out = []
        for poly in polys:
            part = poly.intersection(square)
            for q in getattr(part, "geoms", [part]):
                if q.geom_type != "Polygon" or q.area <= 1.0:
                    continue
                q = shapely.geometry.polygon.orient(q, 1.0)
                rings = [np.asarray(q.exterior.coords)] + [np.asarray(r.coords) for r in q.interiors]
                coords = [np.round(np.c_[r[:, :2], level(r[:, :2])], 3).tolist() for r in rings]
                out.append(dict(type="Feature", geometry=dict(type="Polygon", coordinates=coords),
                                properties=dict(kind=kind, level=round(float(level(rings[0][:, :2]).mean()), 3), area=round(q.area, 1))))
        return out

    feats = polygons([a["poly"] for a in w.areas], "water") + polygons(w.troughs, "carried")
    for line in w.wlines:
        xy, y = line["xy"], line["y"]
        mine = proads.owned(xy[:-1, 0], xy[:-1, 1], origin, SECTOR) if len(xy) > 1 else np.zeros(0, bool)
        idx = np.flatnonzero(mine)
        for run in (np.split(idx, np.flatnonzero(np.diff(idx) != 1) + 1) if len(idx) else []):
            k = np.r_[run, run[-1] + 1]
            feats.append(dict(type="Feature", geometry=dict(type="LineString", coordinates=np.round(np.c_[xy[k], y[k]], 3).tolist()),
                              properties=dict(kind="stream", width=round(2 * float(line["hw"]), 2))))
    return feats


def places(si, sj, w, origin):
    to_l93 = Transformer.from_crs(4326, 2154, always_xy=True)
    pois = []
    for e in sources.read_tile("osm_pois", si, sj):
        where = e if e["type"] == "node" else e.get("center", {})
        if "lon" not in where:
            continue
        x, y = to_l93.transform(where["lon"], where["lat"])
        t = e["tags"]
        pois.append(dict(osm=f"{e['type'][0]}{e['id']}", amenity=t.get("amenity"), shop=t.get("shop"), name=t.get("name"),
                         kind=build_world.poi_kind(t), x=round(x - CX, 2), y=round(y - CY, 2)))
    names = [dict(name=p["n"], kind=p["k"], rank=p["r"], population=p["pop"], x=p["x"], y=p["z"]) for p in sources.read_tile("osm_places", si, sj)]
    bays = np.asarray(w.bays, float).reshape(-1, 4)
    return dict(places=names, pois=pois, parking_bays=dict(columns=["x", "y", "angle", "occupied"], rows=np.round(bays, 3).tolist()))


# ------------------------------------------------------------------ one sector

def export_sector(args):
    si, sj, out, tag, cell = args
    t0 = time.time()
    lap = build_world.Laps()
    origin = (-HALF + si * SECTOR, -HALF + sj * SECTOR)
    d = sector_dir(out, si, sj)
    d.mkdir(parents=True, exist_ok=True)
    pieces, meshes, lanes = road_build.load_sector(tag, si, sj)
    w = build_world.compute_sector(si, sj, pieces, meshes, lanes, lap)
    stats = dict(sector=[si, sj])

    h, holes, stats["terrain"] = terrain.carved(w, cell)
    stats["road_on_terrain"] = terrain.surface_gap(h, origin, cell, pieces, meshes)
    n = terrain.samples(cell)
    transform = from_origin(CX + origin[0] - cell / 2, CY + origin[1] + SECTOR + cell / 2, cell, cell)       # pixel centres on the vertices
    with rasterio.open(d / "height.tif", "w", driver="GTiff", width=n, height=n, count=1, dtype="float32",
                       crs="EPSG:2154", transform=transform, compress="deflate", predictor=3) as f:
        f.write(north_up(h).astype(np.float32), 1)
    write_png(d / "holes.png", north_up(holes.astype(np.uint8) * 255))
    lap("package_terrain")

    cls, rows = land_cover(w, origin, cell)
    write_png(d / "classes.png", north_up(cls))
    write_png(d / "rows.png", north_up(rows))
    Image.fromarray(north_up(colour(w, origin))).save(d / "colour.jpg", quality=92)
    ortho = sources.raster_path("ortho", si, sj)                                     # the orthophoto as served (JPEG), the original block's
    if ortho is not None and Path(ortho).suffix == ".jpg":                             # lossless PNG re-encoded, so every sector has ortho.jpg
        shutil.copy(ortho, d / "ortho.jpg")
    elif ortho is not None:
        Image.open(ortho).convert("RGB").save(d / "ortho.jpg", quality=95)
    fine = [(a, b) for a in range(hires.ORTHO_SPLIT) for b in range(hires.ORTHO_SPLIT)]
    if all(hires.ortho_path(si, sj, a, b).exists() for a, b in fine):                 # the 0.2 m orthophoto, linked from the cache
        (d / "ortho20").mkdir(exist_ok=True)
        for a, b in fine:
            target = d / "ortho20" / f"{a}_{b}.jpg"
            target.unlink(missing_ok=True)
            try:
                os.link(hires.ortho_path(si, sj, a, b), target)
            except OSError:
                shutil.copy(hires.ortho_path(si, sj, a, b), target)
        stats["ortho20"] = True
    stats["classes"] = {land.NAMES[k]: int(v) for k, v in enumerate(np.bincount(cls.ravel(), minlength=len(land.NAMES))) if v}
    lap("package_ground")

    mesh = gltf.Mesh()
    ground = lambda xy: sample(h, origin, cell, xy[:, 0], xy[:, 1])
    stats["roads"] = proads.surfaces(mesh, pieces, meshes, w.park_meshes, origin, SECTOR, build_world.road_surface.junction_triangles, ground)
    paint = proads.Paint()
    for p in pieces:
        proads.paint_piece(paint, p, proads.piece_segments(p, origin, SECTOR), origin, SECTOR)
    paint.into(mesh)
    stats["roads"]["triangles"] = len(mesh)
    gltf.write_glb(d / "roads.glb", mesh, origin, proads.MATERIALS, f"roads_{si}_{sj}")
    road_feats = proads.features(pieces, origin, SECTOR)
    write_geojson(d / "roads.geojson", road_feats)
    lane_list = proads.lane_records(lanes, origin, SECTOR)
    write_json_gz(d / "lanes.json.gz", dict(controls=["priority", "give_way", "stop", "signals", "right"], elements=lane_list))
    stats["roads"].update(features=len(road_feats), lanes=len(lane_list))
    lap("package_roads")

    mesh = gltf.Mesh()
    feats = []
    for b in w.buildings:
        pbuildings.walls_mesh(mesh, b)
        pbuildings.roof_mesh(mesh, b)
        feats.append(pbuildings.feature(b))
    gltf.write_glb(d / "buildings.glb", mesh, origin, pbuildings.MATERIALS, f"buildings_{si}_{sj}")
    write_geojson(d / "buildings.geojson", feats)
    stats["buildings"] = dict(count=len(feats), triangles=len(mesh))
    lap("package_buildings")

    veg = vegetation(w, h, origin, cell)
    write_json_gz(d / "vegetation.json.gz", veg)
    write_geojson(d / "water.geojson", water(w, origin))
    (d / "places.json").write_text(json.dumps(places(si, sj, w, origin), ensure_ascii=False, separators=(",", ":")))
    stats["vegetation"] = dict(trees=len(veg["trees"]["rows"]), shrubs=len(veg["shrubs"]["rows"]), hedges=len(veg["hedges"]), vines=len(veg["vines"]["rows"]))
    lap("package_rest")

    stats.update(lap.stats(), secs=round(time.time() - t0, 1), height=[float(h.min()), float(h.max())])
    (d / "stats.json").write_text(json.dumps(stats, indent=1))
    return stats


def finished(out, si, sj, cell):
    """The stats of a sector already exported at `cell` (None when it is not complete)."""
    path = sector_dir(out, si, sj) / "stats.json"
    if not path.exists():
        return None
    st = json.loads(path.read_text())
    return st if st.get("terrain", {}).get("cell") == cell else None


def export_safe(args):
    try:
        return export_sector(args)
    except Exception as e:
        return dict(sector=list(args[:2]), error=repr(e), trace=traceback.format_exc())


# ------------------------------------------------------------------ the whole package

def spawn(out, sectors):
    """Where a car starts: on the main road nearest the origin (Bérat), heading along it."""
    best = None
    for si, sj in sorted(sectors, key=lambda s: abs(-HALF + (s[0] + 0.5) * SECTOR) + abs(-HALF + (s[1] + 0.5) * SECTOR))[:4]:
        path = sector_dir(out, si, sj) / "roads.geojson"
        for f in json.loads(path.read_text())["features"]:
            p = f["properties"]
            if p["class"] not in ("main", "collector") or p["bridge"] or p["tunnel"]:
                continue
            c = np.asarray(f["geometry"]["coordinates"])
            k = int(np.argmin(np.hypot(c[:, 0], c[:, 1])))
            dist = float(np.hypot(*c[k, :2]))
            if best is None or dist < best[0]:
                j = min(k + 1, len(c) - 1); i = j - 1
                heading = float(np.degrees(np.arctan2(c[j, 0] - c[i, 0], c[j, 1] - c[i, 1])))
                best = (dist, dict(x=round(float(c[k, 0]), 2), y=round(float(c[k, 1]), 2), z=round(float(c[k, 2]) + 0.5, 2), heading=round(heading, 1), road=p["name"] or p["number"]))
    return best[1] if best else None


def unify_seams(out, sectors, log=print):
    """Make the shared edge rows / columns of neighbouring sectors identical: both take the lower of the two (so the terrain stays
    under the roads of both), repeated until the corners shared by four sectors agree too. The sectors are computed apart and
    agree almost everywhere; where a car park near a border is cut a little differently by the roads each sector loads, they
    differ by a few centimetres (berat70scale at 2 m: 2 borders of ~900 over 5 cm, 16 cm at most). Returns (vertices changed, max change)."""
    have = set(sectors)
    edges = {}
    for s in sectors:
        with rasterio.open(sector_dir(out, *s) / "height.tif") as f:
            a = f.read(1)                                                             # row 0 north
        edges[s] = dict(n=a[0].copy(), s=a[-1].copy(), w=a[:, 0].copy(), e=a[:, -1].copy())
    original = {s: {k: v.copy() for k, v in e.items()} for s, e in edges.items()}

    def corners(e):                                                                   # the corner vertices live in two edges each
        e["n"][0] = e["w"][0] = min(e["n"][0], e["w"][0]); e["n"][-1] = e["e"][0] = min(e["n"][-1], e["e"][0])
        e["s"][0] = e["w"][-1] = min(e["s"][0], e["w"][-1]); e["s"][-1] = e["e"][-1] = min(e["s"][-1], e["e"][-1])

    for _ in range(4):
        changed = False
        for si, sj in sectors:
            for other, mine, theirs in (((si + 1, sj), "e", "w"), ((si, sj + 1), "n", "s")):
                if other not in have:
                    continue
                low = np.minimum(edges[(si, sj)][mine], edges[other][theirs])
                if (low != edges[(si, sj)][mine]).any() or (low != edges[other][theirs]).any():
                    changed = True
                    edges[(si, sj)][mine], edges[other][theirs] = low.copy(), low.copy()
        for e in edges.values():
            corners(e)
        if not changed:
            break
    count, worst = 0, 0.0
    for s in sectors:
        diff = {k: original[s][k] - edges[s][k] for k in edges[s]}
        if not any(d.any() for d in diff.values()):
            continue
        count += sum(int((d != 0).sum()) for d in diff.values())
        worst = max(worst, max(float(d.max()) for d in diff.values()))
        path = sector_dir(out, *s) / "height.tif"
        with rasterio.open(path) as f:
            a, profile = f.read(1), f.profile
        a[0], a[-1], a[:, 0], a[:, -1] = edges[s]["n"], edges[s]["s"], edges[s]["w"], edges[s]["e"]
        with rasterio.open(path, "w", **profile) as f:
            f.write(a, 1)
    log(f"seams: {count} edge vertices lowered to their neighbour's, {worst:.3f} m at most")
    return count, round(worst, 3)


def finish_heights(out, sectors):
    """The common height range, then every sector's height.png from its height.tif."""
    lo, hi = np.inf, -np.inf
    for si, sj in sectors:
        with rasterio.open(sector_dir(out, si, sj) / "height.tif") as f:
            a = f.read(1)
        lo, hi = min(lo, float(a.min())), max(hi, float(a.max()))
    lo, hi = np.floor(lo) - 1.0, np.ceil(hi) + 1.0
    for si, sj in sectors:
        d = sector_dir(out, si, sj)
        with rasterio.open(d / "height.tif") as f:
            a = f.read(1).astype(np.float64)
        write_png(d / "height.png", np.round((a - lo) / (hi - lo) * 65535).astype(np.uint16))
    return lo, hi


def git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def build(list_path, out, jobs=0, log=print, cell=2.0, fresh=False):
    """Export every sector of `list_path` into `out`, then the manifest. Returns the manifest."""
    sectors = [tuple(s) for s in json.loads(Path(list_path).read_text())["sectors"]]
    tag = road_build.tag_of(list_path)
    if not road_build.sector_path(tag, *sectors[0]).exists():
        raise SystemExit(f"no roads for {list_path}: run `uv run python -m roads --list {list_path} build` first")
    fetched = sources.ensure(sectors, sources.BUILD_KINDS, log=log)
    if fetched["failures"]:
        raise SystemExit(f"source tiles could not be fetched: {fetched['failures'][:3]}")
    out = Path(out)
    (out / "sectors").mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    jobs = machine.jobs(jobs)
    results, failed, todo = [], [], []
    for si, sj in sectors:                                                            # resume: stats.json is written last, so a sector
        done = finished(out, si, sj, cell) if not fresh else None                     # that has it at this cell is complete
        if done:
            results.append(done)
        else:
            todo.append((si, sj))
    if results:
        log(f"{len(results)} sectors already exported at {cell} m (--fresh to redo them)")
    # submitted a few at a time, never all at once: a pool given every task and recycling its workers (max_tasks_per_child) stalled
    # for good when the first workers retired (berat70scale, 100 of 484 sectors)
    pending, queue, k = set(), iter(todo), 0
    with ProcessPoolExecutor(jobs, max_tasks_per_child=20) as ex:
        while True:
            while len(pending) < jobs + 1:
                s = next(queue, None)
                if s is None:
                    break
                pending.add(ex.submit(export_safe, (*s, str(out), tag, cell)))
            if not pending:
                break
            fut = next(iter(wait(pending, return_when=FIRST_COMPLETED)[0]))
            pending.discard(fut)
            st, k = fut.result(), k + 1
            if "error" in st:
                failed.append(st["sector"]); log(f"[{k}/{len(todo)}] FAILED {st['sector']} {st['error']}\n{st['trace']}")
                continue
            results.append(st)
            log(f"[{k}/{len(todo)}] {st['sector']} {st['secs']} s  road on terrain {st['road_on_terrain']}  elapsed {time.time() - t0:.0f} s")
    if failed:
        raise SystemExit(f"failed sectors: {failed}")
    seams = unify_seams(out, sectors, log)
    lo, hi = finish_heights(out, sectors)
    files = sorted(p.name for p in sector_dir(out, *sectors[0]).iterdir() if p.is_file())
    manifest = dict(format=FORMAT, version=VERSION, tag=tag, crs="EPSG:2154", origin=[CX, CY], z_datum="NGF-IGN69",
                    axes="x east, y north, z up, metres from origin", sector_size=SECTOR, sector_origin="x = 3200 si - 16000, y = 3200 sj - 16000",
                    cell=cell, samples=terrain.samples(cell), sectors=[list(s) for s in sorted(sectors)], files=files,
                    ortho20=dict(cell=hires.ORTHO_RES, tiles=f"{hires.ORTHO_SPLIT} x {hires.ORTHO_SPLIT} per sector, ortho20/<a>_<b>.jpg, a east, b north, {hires.ORTHO_SIDE} px",
                                 sectors=sum(1 for r in results if r.get("ortho20"))),
                    height=dict(min=lo, max=hi, png="z = min + v / 65535 * (max - min)"),
                    classes=land.NAMES, tree_kinds=TREE_KINDS, openings=pbuildings.OPENINGS,
                    materials=dict(roads=sorted(proads.MATERIALS), buildings=sorted(pbuildings.MATERIALS)),
                    road_width_scale=build_world.road_config.WIDTH_SCALE, spawn=spawn(out, sectors), sources=SOURCES,
                    built=dict(commit=git_commit(), date=datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                               seconds=round(time.time() - t0), jobs=jobs))
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False))
    docs = Path(__file__).resolve().parents[2] / "docs"                               # the format and the plan travel with the data
    shutil.copy(docs / "map-package.md", out / "README.md")
    shutil.copy(docs / "unreal-roadmap.md", out / "ROADMAP.md")
    shutil.copy(Path(__file__).with_name("verify_on_pc.ps1"), out / "verify_on_pc.ps1")
    totals = dict(sectors=len(results), seam_vertices_lowered=seams[0], seam_lowered_max_m=seams[1], buildings=sum(r["buildings"]["count"] for r in results), trees=sum(r["vegetation"]["trees"] for r in results),
                  road_triangles=sum(r["roads"]["triangles"] for r in results),
                  terrain_above_road_max=max(r["road_on_terrain"].get("terrain_above_road_max", 0) for r in results))
    (out / "report.json").write_text(json.dumps(dict(totals=totals, sectors=sorted(results, key=lambda r: r["sector"])), indent=1))
    log(f"done: {totals}, {round(time.time() - t0)} s -> {out}")
    return manifest
