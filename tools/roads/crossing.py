"""Stage 5: crossings without a junction: which road passes over, and over what length.

Two drawn roads that cross without meeting at a node are grade separated. The LiDAR decides where it can; where it cannot, the OSM
levels (`bridge`, `tunnel`, `covered`, `layer`, `cutting`) are the next hint, then BD TOPO's bridge flag, which is nearly always right.
Where it is not (the towpath of the Canal du Midi on the Herbettes aqueduct is surveyed as lying on the ground, and the
ring road in the trench under it as the bridge), the LiDAR ground decides: the road passing over is the one whose ground climbs well
above the crossing on both sides within a short distance, while the other road's ground stays down. Its span becomes a bridge, from
the top of the bank on one side to the top of the bank on the other; a surveyed bridge of the road below, lying on the ground there,
is dropped.

The flags therefore live on the link samples (`Link.bridge`, `Link.tunnel`, per segment), not on the surveyed sections: a span is
usually part of a section.
"""
import numpy as np
import shapely
from shapely.geometry import LineString

from rasters import Mosaic

from . import config, osm
from .profile import ACROSS


def flags(links, edges):
    """Per segment bridge / tunnel flags of every link, as surveyed, and the OSM level and bridge tag of the way matched there."""
    for link in links:
        chain = [edges[e] for e, _ in link.chain]
        link.bridge = np.array([chain[p].bridge for p in link.part], bool)
        link.tunnel = np.array([chain[p].tunnel for p in link.part], bool)
        level = [osm.level(edge.tags) for edge in chain]
        link.level = np.array([np.nan if level[p] is None else level[p] for p in link.part], float)
        link.osm_bridge = np.array([chain[p].tags.get("bridge", "no") != "no" for p in link.part], bool)


def osm_upper(a, sa, b, sb):
    """(upper link, segment, lower link, segment) after the OSM levels at a crossing; None when OSM does not tell (a way missing,
    the same level)."""
    la, lb = a.level[sa], b.level[sb]
    if not (np.isfinite(la) and np.isfinite(lb)) or la == lb:
        return None
    return (a, sa, b, sb) if la > lb else (b, sb, a, sa)


def crossings(links):
    """[(link, segment, link, segment, (x, north))] of every place where the drawn parts of two links cross."""
    drawn = [k for k, link in enumerate(links) if not link.internal and link.i1 > link.i0]
    lines = [LineString(links[k].xy[links[k].i0:links[k].i1 + 1]) for k in drawn]
    first, second = shapely.STRtree(lines).query(lines, predicate="crosses")
    out = []
    for a, b in zip(first, second):
        if a >= b:
            continue
        points = lines[a].intersection(lines[b])
        for point in getattr(points, "geoms", [points]):
            if point.geom_type != "Point":
                continue
            ka, kb = drawn[a], drawn[b]
            out.append((ka, _segment(links[ka], lines[a], point), kb, _segment(links[kb], lines[b], point), (point.x, point.y)))
    return out


def _segment(link, line, point):
    s = link.s[link.i0] + line.project(point)
    return int(np.clip(np.searchsorted(link.s, s) - 1, link.i0, link.i1 - 1))


def _ground(link, terrain):
    """LiDAR ground under every sample of a link: the median across its width (nan outside the terrain window)."""
    inside = ((link.xy[:, 0] >= terrain.x0 + 4) & (link.xy[:, 0] < terrain.x0 + terrain.width - 4)
              & (link.xy[:, 1] >= terrain.z0 + 4) & (link.xy[:, 1] < terrain.z1 - 4))
    ground = np.full(len(link.s), np.nan)
    xy, nrm, hw = link.xy[inside], link.nrm[inside], link.hw[inside]
    across = [terrain.sample(terrain.mnt, *(xy + nrm * (o * hw)[:, None]).T, 2) for o in ACROSS]
    ground[inside] = np.median(across, axis=0)
    return ground


def span_over(link, seg, low, ground):
    """(first, last) sample of the stretch of `link` around segment `seg` that passes over a hollow whose floor is at `low`: on each
    side, out to where its ground has climbed CROSSING_RISE above the floor and on to the top of that bank. None unless the ground
    climbs so on both sides within CROSSING_REACH of the crossing, as a wall (from half a metre above the floor to CROSSING_RISE
    within CROSSING_WALL: a trench side or an abutment, not the side of a valley), and before the link reaches a junction."""
    centre = 0.5 * (link.s[seg] + link.s[seg + 1])

    def within(i, reach):
        return link.i0 <= i <= link.i1 and abs(link.s[i] - centre) <= reach and np.isfinite(ground[i])

    ends = []
    for step, i in ((-1, seg), (1, seg + 1)):
        foot = i
        while within(i, config.CROSSING_REACH) and ground[i] < low + config.CROSSING_RISE:
            if ground[i] <= low + 0.5:
                foot = i
            i += step
        if not within(i, config.CROSSING_REACH) or abs(link.s[i] - link.s[foot]) > config.CROSSING_WALL:
            return None
        top = abs(link.s[i] - centre) + config.CROSSING_BANK
        while within(i + step, top) and ground[i + step] > ground[i]:
            i += step
        ends.append(i)
    return ends[0], ends[1]


def _run(flags_, seg):
    """(first, one past the last) segment of the run of True flags holding `seg`."""
    a, b = seg, seg + 1
    while a > 0 and flags_[a - 1]:
        a -= 1
    while b < len(flags_) and flags_[b]:
        b += 1
    return a, b


def separate(links):
    """Decide every crossing without a junction: the LiDAR ground where it tells, else the OSM levels, else the survey. Returns counts for the report."""
    found = crossings(links)
    stats = dict(crossings=len(found), bridges_added=0, bridges_dropped=0, by_lidar=0, by_osm=0, osm_without_span=0, by_survey=0)
    reach = config.CROSSING_REACH + config.CROSSING_BANK + 16.0
    for ka, sa, kb, sb, (x, z) in found:
        a, b = links[ka], links[kb]
        if a.tunnel[sa] or b.tunnel[sb]:
            continue
        x0, z0 = np.floor((x - reach) / 4.0) * 4.0, np.floor((z - reach) / 4.0) * 4.0
        terrain = Mosaic(x0, z0, np.ceil((x + reach - x0) / 4.0) * 4.0, np.ceil((z + reach - z0) / 4.0) * 4.0, kinds=("mnt",))
        low = float(terrain.sample(terrain.mnt, [x], [z], 2)[0])
        ground = {ka: _ground(a, terrain), kb: _ground(b, terrain)}
        over = {k: span_over(links[k], seg, low, ground[k]) for k, seg in ((ka, sa), (kb, sb))}
        if (over[ka] is None) != (over[kb] is None):
            (upper, us, span), (lower, ls, floor) = ((a, sa, over[ka]), (b, sb, ground[kb])) if over[ka] is not None else ((b, sb, over[kb]), (a, sa, ground[ka]))
            stats["by_lidar"] += 1
        elif (hint := osm_upper(a, sa, b, sb)) is not None:     # the ground does not tell: OSM's levels are the next hint
            upper, us, lower, ls = hint
            if not upper.osm_bridge[us]:
                stats["osm_without_span"] += 1                  # e.g. the lower road in a cutting under a way not tagged as a bridge
                continue
            span, floor = _run(upper.osm_bridge, us), ground[ka if lower is a else kb]
            stats["by_osm"] += 1
        else:
            stats["by_survey"] += 1                             # neither tells: keep what the survey says
            continue
        if not upper.bridge[us]:
            upper.bridge[span[0]:span[1]] = True
            stats["bridges_added"] += 1
        # a surveyed bridge of the road below, at the crossing or next to it (the sections are not always cut right under it), lying on the floor
        centre = 0.5 * (lower.s[ls] + lower.s[ls + 1])
        near = np.flatnonzero(np.abs(0.5 * (lower.s[:-1] + lower.s[1:]) - centre) <= config.CROSSING_WALL)
        for first, last in {_run(lower.bridge, k) for k in near if lower.bridge[k]}:
            under = floor[first:last + 1]
            if np.isfinite(under).all() and under.min() >= low - config.CROSSING_FLAT:
                lower.bridge[first:last] = False
                stats["bridges_dropped"] += 1
    return stats
