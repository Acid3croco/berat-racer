"""What the ground data covers over a sector list: per source and class, the features and the share of the map's area (fetch_ground.py).

Usage: uv run python check_ground.py data/big/small_sectors.json
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


if __name__ == "__main__":
    for name, classes in measure(sys.argv[1]).items():
        print(name, json.dumps(classes, ensure_ascii=False))
