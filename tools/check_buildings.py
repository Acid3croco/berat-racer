"""Measure the buildings of a built world, the way the game draws them.

  roofs        how many are pitched, hipped (no gable wall), gabled, flat; for BM07 worlds the exported roof meshes
  outside      roof area hanging outside the footprint (plan): the old gable over the oriented bounding rectangle, or the mesh
  courtyards   buildings with a courtyard kept

Usage: uv run python check_buildings.py DIR [every]   (every n-th chunk; default 1)
"""
import gzip
import json
import sys
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import Polygon

from check_roads import parse_mid


def plan_area(tris):
    t = np.asarray(tris)
    if not len(t):
        return 0.0
    return float(np.abs(0.5 * ((t[:, 1, 0] - t[:, 0, 0]) * (t[:, 2, 1] - t[:, 0, 1]) - (t[:, 1, 1] - t[:, 0, 1]) * (t[:, 2, 0] - t[:, 0, 0]))).sum())


def check(world_dir, every=1):
    world_dir = Path(world_dir)
    totals = dict(buildings=0, pitched=0, gabled=0, hipped=0, flat=0, courtyards=0, roof_m2=0.0, outside_m2=0.0, outside_buildings=0)
    for path in sorted((world_dir / "chunks").glob("m_*"))[::every]:
        d = parse_mid(gzip.open(path).read())
        for b in d["bld_list"]:
            totals["buildings"] += 1
            foot = Polygon(b["rings"][0], b["rings"][1:]) if b.get("rings") else Polygon(b["outline"])
            if not foot.is_valid:
                foot = foot.buffer(0)
            totals["courtyards"] += len(b.get("rings", [])) > 1
            if "roof_t" in b and len(b["roof_t"]):
                v = b["roof_v"][:, [0, 2, 1]]                       # x, north, height
                tris = v[b["roof_t"]]
                slopes = tris[~b["roof_gable"]]
                rise = tris[:, :, 2].max() - tris[:, :, 2].min()
                pitched = rise > 0.3
                totals["pitched"] += pitched
                totals["gabled"] += bool(b["roof_gable"].any())
                totals["hipped"] += pitched and not b["roof_gable"].any()
                totals["flat"] += not pitched
                roof = shapely.union_all([Polygon(t[:, :2]) for t in slopes if Polygon(t[:, :2]).area > 1e-6])
            elif b["rise"] > 0:
                totals["pitched"] += 1
                roof = foot.minimum_rotated_rectangle                    # old: one gable over the bounding rectangle
            else:
                totals["flat"] += 1
                roof = foot
            outside = roof.difference(foot.buffer(0.05)).area
            totals["roof_m2"] += roof.area
            totals["outside_m2"] += outside
            totals["outside_buildings"] += outside > 0.5
    totals["outside_share"] = round(totals["outside_m2"] / max(totals["roof_m2"], 1e-9), 4)
    totals["roof_m2"], totals["outside_m2"] = round(totals["roof_m2"]), round(totals["outside_m2"])
    return totals


if __name__ == "__main__":
    print(json.dumps(check(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 1), default=float))
