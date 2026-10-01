"""What the ground data covers over a sector list: per source and class, the features and the share of the map's area (fetch_ground.py).

Usage: uv run python check_ground.py data/big/small_sectors.json      (the fetched data)
       uv run python check_ground.py --world ../world_small               (what a built world carries: BN02 near files)
"""
import gzip
import json
import sys
from collections import Counter
from pathlib import Path

import shapely
from shapely.geometry import box, shape

from build_world import load_vectors
from fetch_ground import LAYERS, osm_path
from fetch_hg import X0, Y0
from fetch_vectors import SECTOR
from roads import build as road_build

CLASS_FIELD = {"vegetation": "nature", "transport": "nature", "structures": "nature", "rpg": "code_cultu", "hedges": None}


def osm_class(tags):
    for key in ("amenity", "landuse", "natural", "leisure", "service", "barrier"):
        if key in tags:
            return f"{key}={tags[key]}"
    return "other"


def measure(list_path):
    sectors = [tuple(s) for s in json.loads(Path(list_path).read_text())["sectors"]]
    area = shapely.union_all([box(X0 + si * SECTOR, Y0 + sj * SECTOR, X0 + (si + 1) * SECTOR, Y0 + (sj + 1) * SECTOR) for si, sj in sectors])
    report = {}
    for name in LAYERS:
        seen, per_class, length = {}, Counter(), Counter()
        for si, sj in sectors:
            for f in load_vectors(name, si, sj):
                key = f["properties"].get("cleabs") or f.get("id")
                if key not in seen:
                    seen[key] = f
        for f in seen.values():
            g = shape(f["geometry"]).intersection(area)
            if g.is_empty:
                continue
            field = CLASS_FIELD[name]
            label = str(f["properties"].get(field)) if field else "hedge"
            per_class[label] += g.area
            length[label] += g.length if g.geom_type in ("LineString", "MultiLineString") else 0
        report[name] = {k: dict(share=round(v / area.area, 4), km=round(length[k] / 1000, 1)) for k, v in per_class.most_common()}
        report[name]["_covered_share"] = round(sum(per_class.values()) / area.area, 4)
    feats = json.loads(gzip.open(osm_path(road_build.tag_of(list_path))).read())
    per_class, length = Counter(), Counter()
    for f in feats:
        g = shape(f["geometry"])
        g = (g if g.is_valid else g.buffer(0)).intersection(area)
        if not g.is_empty:
            per_class[osm_class(f["properties"])] += g.area
            length[osm_class(f["properties"])] += g.length if f["properties"]["osm_kind"] == "line" else 0
    report["osm"] = {k: dict(share=round(v / area.area, 4), km=round(length[k] / 1000, 1)) for k, v in per_class.most_common()}
    return report


def measure_world(world_dir):
    """Share of the vertices per ground class, rows with a direction, vine rows, bays, parked cars, hedges and trees per kind."""
    import struct

    import numpy as np

    import ground
    cv, classes, kinds = 101, Counter(), Counter()
    rowed = vines_km = bays = parked = hedges_km = parks = park_m2 = 0.0
    for path in sorted(Path(world_dir, "chunks").glob("n_*")):
        raw = gzip.open(path).read()
        if raw[:4] != b"BN02":
            continue
        o = 4
        nt = struct.unpack_from("<i", raw, o)[0]; o += 4 + 16 * nt
        ns = struct.unpack_from("<i", raw, o)[0]; o += 4 + 16 * ns
        kinds.update(np.frombuffer(raw, np.uint8, nt, o).tolist()); o += nt
        g = np.frombuffer(raw, np.uint8, cv * cv, o)[:cv * cv].reshape(cv, cv)[:-1, :-1]; o += cv * cv     # each chunk owns its south-west vertices
        a = np.frombuffer(raw, np.uint8, cv * cv, o).reshape(cv, cv)[:-1, :-1]; o += cv * cv
        classes.update(Counter(g.ravel().tolist())); rowed += int((a > 0).sum())
        n = struct.unpack_from("<i", raw, o)[0]; v = np.frombuffer(raw, "<f4", 4 * n, o + 4).reshape(-1, 4); o += 4 + 16 * n
        vines_km += float(np.hypot(v[:, 2] - v[:, 0], v[:, 3] - v[:, 1]).sum()) / 1000
        n = struct.unpack_from("<i", raw, o)[0]; b = np.frombuffer(raw, "<f4", 4 * n, o + 4).reshape(-1, 4); o += 4 + 16 * n
        bays += n; parked += float(b[:, 3].sum())
        n = struct.unpack_from("<i", raw, o)[0]; o += 4
        for _ in range(n):                                                               # height, point count, points
            k = struct.unpack_from("<i", raw, o + 4)[0]; xy = np.frombuffer(raw, "<f4", 2 * k, o + 8).reshape(-1, 2); o += 8 + 8 * k
            hedges_km += float(np.hypot(*np.diff(xy, axis=0).T).sum()) / 1000
        n = struct.unpack_from("<i", raw, o)[0]; o += 4
        for _ in range(n):                                                               # car-park surfaces: vertices (x, y, z), triangles
            k = struct.unpack_from("<i", raw, o)[0]; v = np.frombuffer(raw, "<f4", 3 * k, o + 4).reshape(-1, 3); o += 4 + 12 * k
            k = struct.unpack_from("<i", raw, o)[0]; t = np.frombuffer(raw, "<i4", 3 * k, o + 4).reshape(-1, 3); o += 4 + 12 * k
            parks += 1; park_m2 += ground.plan_area(v[:, [0, 2]][t])
        assert o == len(raw), path
    total = sum(classes.values())
    if not total:
        return dict(classes={}, note="no BN02 near files: no ground classes")
    return dict(classes={ground.NAMES[k]: round(v / total, 4) for k, v in classes.most_common()}, rowed_share=round(rowed / total, 4),
                vine_rows_km=round(vines_km, 1), bays=int(bays), parked=int(parked), hedges_km=round(hedges_km, 1), car_parks=int(parks), car_park_m2=round(park_m2),
                trees_by_kind={["unknown", "broadleaf", "conifer", "poplar", "fruit"][k]: v for k, v in sorted(kinds.items())})


if __name__ == "__main__":
    if sys.argv[1] == "--world":
        print(json.dumps(measure_world(sys.argv[2]), ensure_ascii=False))
    else:
        for name, classes in measure(sys.argv[1]).items():
            print(name, json.dumps(classes, ensure_ascii=False))
