"""Compare two road builds of the same area, sector by sector, on what the world builder reads (road pieces, junction meshes, lanes).

  uv run python compare_roads.py small data/big/baseline_bd66/roads_small     # a tag (tiled build) or a directory of sector_<si>_<sj>.pkl
  uv run python compare_roads.py small small_other

Pieces are matched by their surveyed section (cleabs) and their order along it; junctions by their outline's centre (within 1 m).
"""
import pickle
import sys
from pathlib import Path

import numpy as np
import shapely

from roads import build


def sector(source, si, sj):
    path = Path(source) / f"sector_{si}_{sj}.pkl"
    if path.exists():
        with open(path, "rb") as fh:
            return pickle.load(fh)
    return build.load_sector(source, si, sj)


def owned(items, xy_of, si, sj):
    """The items whose middle lies in the sector (each counted once over the area)."""
    x0, z0 = -16000 + 3200 * si, -16000 + 3200 * sj
    out = []
    for it in items:
        p = xy_of(it)
        if x0 <= p[0] < x0 + 3200 and z0 <= p[1] < z0 + 3200:
            out.append(it)
    return out


def main(a, b, list_path="data/big/small_sectors.json"):
    import json
    sectors = [tuple(s) for s in json.loads(Path(list_path).read_text())["sectors"]]
    plan, height, counts, jdist, jz, lanes = [], [], [0, 0, 0, 0], [], [], [0, 0]
    for si, sj in sectors:
        pa, ma, la = sector(a, si, sj)
        pb, mb, lb = sector(b, si, sj)
        mid = lambda p: p.xy[len(p.xy) // 2]
        pa, pb = owned(pa, mid, si, sj), owned(pb, mid, si, sj)
        def by_section(pieces):
            d = {}
            for p in sorted(pieces, key=lambda p: (p.edge.cleabs, float(p.s[0]))):
                d.setdefault(p.edge.cleabs, []).append(p)
            return d
        da, db = by_section(pa), by_section(pb)
        counts[0] += len(pa)
        counts[1] += len(pb)
        for c in da.keys() & db.keys():
            qs = db[c]
            if np.hypot(*(da[c][0].xy[0] - qs[0].xy[0])) > np.hypot(*(da[c][0].xy[0] - qs[-1].xy[-1])):
                qs = qs[::-1]                                                    # walked the other way: the same section, reversed
            for p, q in zip(da[c], qs):
                if np.hypot(*(p.xy[0] - q.xy[0])) > np.hypot(*(p.xy[0] - q.xy[-1])):
                    q = type(q)(**{**q.__dict__, "xy": q.xy[::-1], "z": q.z[::-1]})
                plan.append(shapely.hausdorff_distance(shapely.LineString(p.xy), shapely.LineString(q.xy)))
                s = np.linspace(0, 1, 20)
                za = np.interp(s, np.linspace(0, 1, len(p.z)), p.z)
                zb = np.interp(s, np.linspace(0, 1, len(q.z)), q.z)
                height.append(float(np.abs(za - zb).max()))
        counts[2] += len(da.keys() ^ db.keys())
        ca = np.array([j.centre for j, _ in owned(ma, lambda m: m[0].centre, si, sj)]).reshape(-1, 2)
        cb = np.array([j.centre for j, _ in owned(mb, lambda m: m[0].centre, si, sj)]).reshape(-1, 2)
        counts[3] += abs(len(ca) - len(cb))
        if len(ca) and len(cb):
            from scipy.spatial import cKDTree
            d, i = cKDTree(cb).query(ca)
            jdist += d.tolist()
            pa_ = [j for j, _ in owned(ma, lambda m: m[0].centre, si, sj)]
            pb_ = [j for j, _ in owned(mb, lambda m: m[0].centre, si, sj)]
            jz += [abs(pa_[k].plane[0] - pb_[i[k]].plane[0]) for k in range(len(pa_)) if d[k] < 1.0 and pa_[k].plane is not None and pb_[i[k]].plane is not None]
        lanes[0] += len(owned(la, lambda e: e.xyz[0], si, sj))
        lanes[1] += len(owned(lb, lambda e: e.xyz[0], si, sj))
    plan, height, jdist, jz = (np.array(v) for v in (plan, height, jdist, jz))
    print(f"pieces {counts[0]} / {counts[1]}, sections in one build only {counts[2]}, junction count difference {counts[3]}, lane elements {lanes[0]} / {lanes[1]}")
    print(f"plan (Hausdorff per piece)   p50 {np.median(plan):.3f} m   p90 {np.percentile(plan, 90):.3f}   p99 {np.percentile(plan, 99):.3f}   max {plan.max():.2f}   "
          f"over 0.1 m {np.mean(plan > 0.1):.1%}")
    print(f"height (max per piece)       p50 {np.median(height):.3f} m   p90 {np.percentile(height, 90):.3f}   p99 {np.percentile(height, 99):.3f}   max {height.max():.2f}")
    print(f"junction centres moved       p50 {np.median(jdist):.3f} m   p99 {np.percentile(jdist, 99):.2f}   over 1 m {np.mean(jdist > 1.0):.1%};  plane height p99 {np.percentile(jz, 99):.3f} m")


if __name__ == "__main__":
    main(*sys.argv[1:])
