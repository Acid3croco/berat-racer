"""One-off: move today's source caches into the per-tile layout of sources.py, without downloading anything.

  rasters   data/big/hg/{mnt,mnh}_{i}_{j}.npz (uint16)        -> src/{kind}/{i}_{j}.u16.zst   (checked bit for bit, then removed)
            data/big/{mnt,mnh}_{i}_{j}.npy (float32, original) -> src/{kind}/{i}_{j}.f32.zst
            data/big/hg/ortho_{i}_{j}.jpg -> src/ortho/{i}_{j}.jpg (moved); data/big/ortho_{i}_{j}.npy -> src/ortho/{i}_{j}.png
  vectors   data/big/vec/{layer}_{si}_{sj}.json[.gz] -> src/{layer}/{si}_{sj}.json.gz; vec/rpg_codes.json -> src/meta/
  per-area files, split into the tiles they cover completely (a tile they only partly cover is left to be fetched):
            osm/roads_<tag>, osm/controls_<tag> (nodes, restrictions, BD TOPO non_communication), osm/buildings_<tag>, osm/ground_<tag>,
            vec/rows_<tag>; the POI files per departement; places.json
Areas are taken in a fixed order (small first) and a tile already written is never overwritten.
"""
import gzip
import json
import os
import shutil
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

import sources as S
from fetch import CX, CY

BIG = S.BIG
TAG_ORDER = ["small", "b70s20", "herb", "berat70new", "berat70fix", "berat70", "herbm", "smallv2", "testa64", "smallbase", "smallnew", "berat70base"]


def _raster(job):
    kind, old, i, j = job
    old = Path(old)
    if old.suffix == ".npz":
        a = np.load(old)["a"]
        data = S.encode_codes(a)
        assert (S.decode_codes(data) == a).all(), old
        S._atomic(S.SRC / kind / f"{i}_{j}.u16.zst", data)
    elif kind == "ortho":
        a = np.load(old)
        S.write_raster("ortho", i, j, a)
        assert (S.read_raster("ortho", i, j) == a).all(), old
    else:
        a = np.load(old)
        data = S.encode_float(a)
        assert np.array_equal(S.decode_float(data).view(np.uint32), a.view(np.uint32)), old
        S._atomic(S.SRC / kind / f"{i}_{j}.f32.zst", data)
    old.unlink()
    return kind


def index(name):
    """'mnt_-3_10.npz' -> ('mnt', -3, 10)."""
    stem = name.split(".")[0]
    kind, i, j = stem.rsplit("_", 2)
    return kind, int(i), int(j)


def convert_rasters(jobs=9):
    work = []
    for f in sorted((BIG / "hg").iterdir()):
        if f.name.startswith(("mnt_", "mnh_")) and f.suffix == ".npz":
            kind, i, j = index(f.name)
            if not (BIG / f"{kind}_{i}_{j}.npy").exists():
                work.append((kind, str(f), i, j))
        elif f.name.startswith("ortho_") and f.suffix == ".jpg":
            kind, i, j = index(f.name)
            (S.SRC / "ortho").mkdir(parents=True, exist_ok=True)
            if not (BIG / f"ortho_{i}_{j}.npy").exists():
                os.replace(f, S.SRC / "ortho" / f"{i}_{j}.jpg")
    for f in sorted(BIG.glob("*.npy")):
        if f.name.startswith(("mnt_", "mnh_", "ortho_")):
            kind, i, j = index(f.name)
            work.append((kind, str(f), i, j))
    print(f"rasters: {len(work)} tiles to re-encode", flush=True)
    with ProcessPoolExecutor(jobs) as ex:
        for k, _ in enumerate(ex.map(_raster, work, chunksize=16), 1):
            if k % 1000 == 0:
                print(f"  {k}/{len(work)}", flush=True)


def convert_vectors():
    vec = BIG / "vec"
    n = 0
    for f in sorted(vec.iterdir()):
        name = f.name
        if name == "rpg_codes.json":
            (S.SRC / "meta").mkdir(parents=True, exist_ok=True)
            shutil.copy(f, S.SRC / "meta" / name)
            continue
        if name.startswith(("osm_poi", "rows_")):
            continue
        stem = name[:-len(".json.gz")] if name.endswith(".json.gz") else name[:-len(".json")] if name.endswith(".json") else None
        if stem is None:
            continue
        layer, si, sj = stem.rsplit("_", 2)
        if layer not in S.WFS_LAYERS:
            continue
        dest = S.vector_path(layer, int(si), int(sj))
        dest.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith(".gz"):
            os.replace(f, dest)
        else:
            S._atomic(dest, gzip.compress(f.read_bytes(), 6))
            f.unlink()
        n += 1
    print(f"vectors: {n} files", flush=True)


def area_tiles(x0, z0, x1, z1):
    """Tiles lying wholly inside a local-metre box."""
    out = []
    for si, sj in S.tiles_of_box(x0 + CX, z0 + CY, x1 + CX, z1 + CY):
        r = S.tile_rect(si, sj)
        if r[0] >= x0 + CX and r[1] >= z0 + CY and r[2] <= x1 + CX and r[3] <= z1 + CY:
            out.append((si, sj))
    return out


def put(kind, tiles, records_of):
    """Write the tiles not written yet; `records_of(tile)` gives a tile's records."""
    n = 0
    for t in tiles:
        if S.vector_path(kind, *t).exists():
            continue
        S.write_tile(kind, *t, records_of(t))
        n += 1
    return n


def convert_area_files():
    from roads import build as road_build, source as road_source
    from fetch_ground import window as ground_window
    tags = [t for t in TAG_ORDER if (BIG / f"{t}_sectors.json").exists()]
    for tag in tags:
        sectors = [tuple(s) for s in json.loads((BIG / f"{tag}_sectors.json").read_text())["sectors"]]
        x0, z0, x1, z1 = road_source.area_of(sectors, road_build.MARGIN)
        inside = area_tiles(x0, z0, x1, z1)
        done = {}
        path = BIG / "osm" / f"roads_{tag}.json.gz"
        if path.exists():
            ways = json.loads(gzip.open(path).read())
            boxes = [S.line_box(w["xy"]) for w in ways]
            done["osm_roads"] = put("osm_roads", inside, lambda t: [w for w, b in zip(ways, boxes) if S.meets(b, S.tile_rect(*t))])
        path = BIG / "osm" / f"controls_{tag}.json.gz"
        if path.exists():
            c = json.loads(gzip.open(path).read())
            at = lambda r: S.tile_of_point(r["x"] + CX, r["z"] + CY)
            done["osm_controls"] = put("osm_controls", inside, lambda t: [r for r in c["nodes"] if at(r) == t])
            done["osm_restrictions"] = put("osm_restrictions", inside, lambda t: [r for r in c["restrictions"] if at(r) == t])
            done["non_communication"] = put("non_communication", inside, lambda t: [r for r in c["no_turn"] if at(r) == t])
        path = BIG / "osm" / f"buildings_{tag}.json.gz"
        if path.exists():
            b = json.loads(gzip.open(path).read())
            boxes = [S.line_box(np.asarray(r["xy"]) + (CX, CY)) if r["xy"] else (0, 0, -1, -1) for r in b]
            done["osm_roofs"] = put("osm_roofs", inside, lambda t: [r for r, bb in zip(b, boxes) if S.meets(bb, S.tile_rect(*t))])
        path = BIG / "osm" / f"ground_{tag}.json.gz"
        if path.exists():
            from shapely.geometry import shape
            g = json.loads(gzip.open(path).read())
            boxes = [shape(f["geometry"]).bounds for f in g]
            cells = ground_window(sectors)                                       # fetch_ground asked for the box of these tiles
            box_tiles = [(si, sj) for si in range(min(c[0] for c in cells), max(c[0] for c in cells) + 1)
                         for sj in range(min(c[1] for c in cells), max(c[1] for c in cells) + 1)]
            done["osm_ground"] = put("osm_ground", box_tiles, lambda t: [f for f, bb in zip(g, boxes) if S.meets(bb, S.tile_rect(*t))])
        path = BIG / "vec" / f"rows_{tag}.json.gz"
        if path.exists():
            done["rows"] = convert_rows(json.loads(gzip.open(path).read()), sectors)
        print(f"{tag}: {done}", flush=True)


def convert_rows(measured, sectors):
    """A rows tile for every tile whose eligible parcels were all measured."""
    import shapely
    from shapely.geometry import shape
    import ground
    codes = S.rpg_codes()
    n = 0
    for t in S.window(sectors):
        if S.vector_path("rows", *t).exists() or not S.vector_path("rpg", *t).exists():
            continue
        out, complete = {}, True
        for f in S.read_tile("rpg", *t):
            if ground.rpg_class(f["properties"], codes) not in ground.ROWED:
                continue
            g = shapely.transform(shape(f["geometry"]), lambda xy: xy - [CX, CY])
            if any(q.area >= ground.ROW_SAMPLE_AREA for q in ([g] if g.geom_type == "Polygon" else list(g.geoms))):
                if f["id"] in measured:
                    out[f["id"]] = measured[f["id"]]
                else:
                    complete = False
        if complete:
            S.write_tile("rows", *t, out)
            n += 1
    return n


def convert_pois_places():
    from pyproj import Transformer
    from shapely.geometry import box
    from shapely.ops import unary_union
    to_l93 = Transformer.from_crs(4326, 2154, always_xy=True)
    to_wgs = Transformer.from_crs(2154, 4326, always_xy=True)
    files = [(BIG / "vec" / "osm_poi.json", (0.93, 43.23, 1.42, 43.53))]
    for f in sorted((BIG / "vec").glob("osm_poi_hg*.json*")):
        code = f.name.split(".")[0].removeprefix("osm_poi_hg")
        meta = json.loads((BIG / f"hg_sectors{code}.json").read_text())
        bx = meta["bbox_l93"]
        (lo0, la0), (lo1, la1) = to_wgs.transform(bx[0], bx[1]), to_wgs.transform(bx[2], bx[3])
        files.append((f, (min(lo0, lo1) - .01, min(la0, la1) - .01, max(lo0, lo1) + .01, max(la0, la1) + .01)))
    cover = unary_union([box(*b) for _, b in files])
    elements, seen = {}, set()
    for f, _ in files:
        text = gzip.open(f, "rt", encoding="utf-8").read() if f.suffix == ".gz" else f.read_text()
        for e in json.loads(text)["elements"]:
            if (e["type"], e["id"]) in seen:
                continue
            seen.add((e["type"], e["id"]))
            x, y = to_l93.transform(*S._lonlat(e))
            elements.setdefault(S.tile_of_point(x, y), []).append(e)
    lon0, lat0, lon1, lat1 = cover.bounds
    (x0, y0), (x1, y1) = to_l93.transform(lon0, lat0), to_l93.transform(lon1, lat1)
    n = 0
    for t in S.tiles_of_box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)):
        r = S.tile_rect(*t)
        lons, lats = to_wgs.transform([r[0], r[2], r[0], r[2]], [r[1], r[1], r[3], r[3]])
        if not cover.contains(box(min(lons), min(lats), max(lons), max(lats))) or S.vector_path("osm_pois", *t).exists():
            continue
        S.write_tile("osm_pois", *t, sorted(elements.get(t, []), key=lambda e: ("nwr".index(e["type"][0]), e["id"])))
        n += 1
    print(f"pois: {n} tiles", flush=True)
    places = json.loads((BIG / "places.json").read_text())                    # fetch_places.py: box S, W, N, E = 42.35, -0.25, 44.0, 2.35
    pbox = box(-0.25, 42.35, 2.35, 44.0)
    by = {}
    for p in places:
        by.setdefault(S.tile_of_point(p["x"] + CX, p["z"] + CY), []).append(p)
    (x0, y0), (x1, y1) = to_l93.transform(-0.25, 42.35), to_l93.transform(2.35, 44.0)
    n = 0
    for t in S.tiles_of_box(x0, y0, x1, y1):
        r = S.tile_rect(*t)
        lons, lats = to_wgs.transform([r[0], r[2], r[0], r[2]], [r[1], r[1], r[3], r[3]])
        if pbox.contains(box(min(lons), min(lats), max(lons), max(lats))) and not S.vector_path("osm_places", *t).exists():
            S.write_tile("osm_places", *t, by.get(t, []))
            n += 1
    print(f"places: {n} tiles", flush=True)


def main():
    convert_vectors()
    convert_area_files()
    convert_pois_places()
    convert_rasters()


if __name__ == "__main__":
    main()
