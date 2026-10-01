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


def _curvature(z, s):
    """Vertical curvature z'' at the interior samples."""
    h = np.diff(s)
    return 2.0 * (np.diff(z[1:]) / h[1:] - np.diff(z[:-1]) / h[:-1]) / (h[:-1] + h[1:])


# ---------------------------------------------------------------- per tile (roads/tiled.py): parts that add up to the same report

OFF_BINS = np.linspace(0.0, 20.0, 20001)                  # |road - ground|, 1 mm
RAW_BINS = np.linspace(-0.2, 0.0, 20001)                  # vertical curvature of the ground under a road (1/m), crests only
SLOPE_BINS = np.linspace(0.0, 1.0, 1001)                  # junction plane slope, 0.1 %


def surface_parts(network, own_links, own_junctions):
    """surface_report of the owned links and junctions; an overlap is counted by the tile owning the first of its two surfaces (by key)."""
    items, mine = [], []
    for k, link in enumerate(network.links):
        polygon = ribbon_polygon(link)
        if polygon is not None:
            items.append((f"L{link.key}", polygon, bool(link.bridge.any())))
            mine.append(k in own_links)
    for i, junction in enumerate(network.junctions):
        if junction.polygon is not None:
            items.append((f"J{junction.key}", junction.polygon, False))
            mine.append(i in own_junctions)
    if not items:
        return dict(surface_m2=0.0, overlaps=[], folded=[], invalid=[])
    valid = shapely.is_valid(np.array([p for _, p, _ in items], dtype=object))
    owned = {label for (label, _, _), m in zip(items, mine) if m}
    found = [f for f in overlaps(items, valid) if min(f[1], f[2]) in owned]
    return dict(surface_m2=float(sum(p.area for (_, p, _), m in zip(items, mine) if m)), overlaps=found,
                folded=[items[n][0] for n in np.flatnonzero(~valid) if mine[n]],
                invalid=[f"J{network.junctions[i].key}: {network.junctions[i].problem}" for i in own_junctions if not network.junctions[i].valid])


def merge_surface(parts):
    found = sorted([f for p in parts for f in p["overlaps"]], reverse=True)
    return dict(surface_m2=round(sum(p["surface_m2"] for p in parts)), overlap_pairs=len(found), overlap_m2=round(sum(f[0] for f in found), 1),
                worst_overlaps=found[:8], folded_ribbons=sum(len(p["folded"]) for p in parts), invalid_junctions=[x for p in parts for x in p["invalid"]])


def profile_parts(network, own_links, own_junctions):
    """profile_report's numbers of the owned links and junctions as maxima and histograms (they add up across tiles)."""
    rows = {}
    for k in own_links:
        link = network.links[k]
        if link.internal or link.i1 - link.i0 < 2:
            continue
        body = slice(link.i0, link.i1 + 1)
        s, z, ground = link.s[body], link.z[body], link.ground[body]
        names = np.array([network.edges[link.chain[p][0]].klass for p in link.part[link.i0:link.i1]])
        bridge = (link.bridge | link.tunnel)[link.i0:link.i1]
        grade, curve, raw, off = np.abs(np.diff(z) / np.diff(s)), _curvature(z, s), _curvature(ground, s), (z - ground)[:-1]
        for name in set(names):
            seg = names == name
            row = rows.setdefault(name, dict(metres=0.0, grade=0.0, crest=0.0, sag=0.0, raw=np.zeros(len(RAW_BINS) - 1, np.int64),
                                             off=np.zeros(len(OFF_BINS) - 1, np.int64)))
            c, r = curve[seg[1:]], raw[seg[1:]]
            row["metres"] += float(np.diff(s)[seg].sum())
            row["grade"] = max(row["grade"], float(grade[seg].max(initial=0.0)))
            row["crest"] = max(row["crest"], float(-c.min(initial=0.0)))
            row["sag"] = max(row["sag"], float(c.max(initial=0.0)))
            row["raw"] += np.histogram(np.clip(r, RAW_BINS[0], RAW_BINS[-1]), RAW_BINS)[0]
            row["off"] += np.histogram(np.clip(np.abs(off[seg & ~bridge]), 0.0, OFF_BINS[-1]), OFF_BINS)[0]
    slopes = [np.hypot(*network.junctions[i].plane[1:]) for i in own_junctions if network.junctions[i].plane is not None]
    tilt = max((float(np.abs(network.links[k].tilt).max(initial=0.0)) for k in own_links), default=0.0)
    return dict(rows=rows, slopes=np.histogram(np.clip(slopes, 0.0, 1.0), SLOPE_BINS)[0], max_slope=max(slopes, default=0.0), max_tilt=tilt)


def _quantile(hist, bins, q):
    c = np.cumsum(hist)
    if not c[-1]:
        return 0.0
    return float(bins[min(np.searchsorted(c, q * c[-1]) + 1, len(bins) - 1)])


def merge_profile(parts):
    rows = {}
    for p in parts:
        for name, r in p["rows"].items():
            row = rows.setdefault(name, dict(metres=0.0, grade=0.0, crest=0.0, sag=0.0, raw=0, off=0))
            row["metres"] += r["metres"]
            for key in ("grade", "crest", "sag"):
                row[key] = max(row[key], r[key])
            row["raw"] = row["raw"] + r["raw"]
            row["off"] = row["off"] + r["off"]
    out = {}
    for name, row in rows.items():
        crest = max(row["crest"], 1e-9)
        raw_crest = max(-_quantile(row["raw"], RAW_BINS, 0.001), 1e-9)
        out[name] = dict(km=round(row["metres"] / 1000.0, 1), max_grade_pct=round(100 * row["grade"], 1), min_crest_radius=round(1.0 / crest),
                         min_sag_radius=round(1.0 / max(row["sag"], 1e-9)), takeoff_kmh=round(3.6 * float(np.sqrt(G / crest))),
                         before_takeoff_kmh=round(3.6 * float(np.sqrt(G / raw_crest))),
                         cut_fill_p90_m=round(_quantile(row["off"], OFF_BINS, 0.9), 2), cut_fill_p99_m=round(_quantile(row["off"], OFF_BINS, 0.99), 2),
                         cut_fill_max_m=round(float(OFF_BINS[np.flatnonzero(row["off"])[-1] + 1]) if np.any(row["off"]) else 0.0, 2))
    slopes = sum(p["slopes"] for p in parts)
    out["junctions"] = dict(max_plane_slope_pct=round(100 * max(p["max_slope"] for p in parts), 1),
                            p99_plane_slope_pct=round(100 * _quantile(slopes, SLOPE_BINS, 0.99), 1), max_tilt_pct=round(100 * max(p["max_tilt"] for p in parts), 1))
    return out
