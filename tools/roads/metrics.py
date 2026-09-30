"""Numbers that say whether a build is good: overlaps, deviations, grades, vertical curvature. `python -m roads build` prints and stores them."""
import numpy as np
import shapely
from shapely.geometry import Polygon

G = 9.81


def ribbon_polygon(link):
    """Plan outline of the drawn part of a link, or None for a swallowed link."""
    if link.i1 <= link.i0:
        return None
    span = slice(link.i0, link.i1 + 1)
    left, right = link.xy[span] + link.nrm[span] * link.hw[span, None], link.xy[span] - link.nrm[span] * link.hw[span, None]
    return Polygon(np.vstack([left, right[::-1]]))


def surfaces(network):
    """[(label, polygon, is bridge)] of every drawn surface: link ribbons and junctions."""
    out = []
    for k, link in enumerate(network.links):
        polygon = ribbon_polygon(link)
        if polygon is not None:
            out.append((f"L{k}", polygon, any(network.edges[e].bridge for e, _ in link.chain)))
    for k, junction in enumerate(network.junctions):
        if junction.polygon is not None:
            out.append((f"J{k}", junction.polygon, False))
    return out


def overlaps(items, valid, tolerance=0.05):
    """Pairs of surfaces that cover the same ground (bridges over roads excepted): [(area, label, label)], largest first.
    `items`: as `surfaces` returns them; `valid`: which of their polygons are valid."""
    polygons = np.array([p for _, p, _ in items], dtype=object)
    polygons[~valid] = shapely.buffer(polygons[~valid], 0, quad_segs=16)
    bridge = np.array([b for _, _, b in items], bool)
    a, b = shapely.STRtree(polygons).query(polygons, predicate="intersects")
    pairs = (a < b) & ~bridge[a] & ~bridge[b]
    a, b = a[pairs], b[pairs]
    area = shapely.area(shapely.intersection(polygons[a], polygons[b]))
    return sorted([(round(float(area[n]), 2), items[a[n]][0], items[b[n]][0]) for n in np.flatnonzero(area > tolerance)], reverse=True)


def surface_report(network):
    items = surfaces(network)
    valid = shapely.is_valid(np.array([p for _, p, _ in items], dtype=object))
    found = overlaps(items, valid)
    folded = [items[n][0] for n in np.flatnonzero(~valid)]
    invalid = [f"J{k}: {j.problem}" for k, j in enumerate(network.junctions) if not j.valid]
    return dict(surface_m2=round(sum(p.area for _, p, _ in items)), overlap_pairs=len(found), overlap_m2=round(sum(f[0] for f in found), 1),
                worst_overlaps=found[:8], folded_ribbons=len(folded), invalid_junctions=invalid)


def _curvature(z, s):
    """Vertical curvature z'' at the interior samples."""
    h = np.diff(s)
    return 2.0 * (np.diff(z[1:]) / h[1:] - np.diff(z[:-1]) / h[:-1]) / (h[:-1] + h[1:])


def profile_report(network):
    """Per road class (of each sample's own section): steepest grade, tightest crest and sag, the speed at which a car leaves the ground
    on the tightest crest (and the same for the raw ground profile: what a road draped on the terrain would do), and how far the road is
    from the LiDAR ground (bridges excluded)."""
    rows = {}
    for link in network.links:
        if link.internal or link.i1 - link.i0 < 2:
            continue
        body = slice(link.i0, link.i1 + 1)
        s, z, ground = link.s[body], link.z[body], link.ground[body]
        edges = [network.edges[link.chain[p][0]] for p in link.part[link.i0:link.i1]]            # per segment
        names = np.array([e.klass for e in edges])
        bridge = np.array([e.bridge or e.tunnel for e in edges])
        grade, curve, raw, off = np.abs(np.diff(z) / np.diff(s)), _curvature(z, s), _curvature(ground, s), (z - ground)[:-1]
        for name in set(names):
            seg = names == name
            row = rows.setdefault(name, dict(grade=[], curve=[], raw_curve=[], off=[], metres=0.0))
            row["grade"].append(grade[seg])
            row["curve"].append(curve[seg[1:]])
            row["raw_curve"].append(raw[seg[1:]])
            row["off"].append(off[seg & ~bridge])
            row["metres"] += float(np.diff(s)[seg].sum())
    out = {}
    for klass, row in rows.items():
        grade, curve, raw, off = (np.concatenate(row[k]) for k in ("grade", "curve", "raw_curve", "off"))
        crest = max(-curve.min(), 1e-9)
        out[klass] = dict(
            km=round(row["metres"] / 1000.0, 1),
            max_grade_pct=round(100 * float(grade.max()), 1),
            min_crest_radius=round(1.0 / crest), min_sag_radius=round(1.0 / max(curve.max(), 1e-9)),
            takeoff_kmh=round(3.6 * float(np.sqrt(G / crest))),
            before_takeoff_kmh=round(3.6 * float(np.sqrt(G / max(-np.percentile(raw, 0.1), 1e-9)))),
            cut_fill_p90_m=round(float(np.percentile(np.abs(off), 90)), 2), cut_fill_p99_m=round(float(np.percentile(np.abs(off), 99)), 2),
            cut_fill_max_m=round(float(np.abs(off).max()), 2))
    slopes = np.array([np.hypot(*j.plane[1:]) for j in network.junctions if j.plane is not None])
    tilts = np.concatenate([np.abs(link.tilt) for link in network.links])
    out["junctions"] = dict(max_plane_slope_pct=round(100 * float(slopes.max()), 1), p99_plane_slope_pct=round(100 * float(np.percentile(slopes, 99)), 1),
                            max_tilt_pct=round(100 * float(tilts.max()), 1))
    return out
