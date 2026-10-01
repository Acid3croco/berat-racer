"""Lanes along a link: how many run each way at every sample, where the lines between them are painted, where overtaking is
forbidden, the edge lines, and the turn arrows before a junction.

Lane counts come from BD TOPO `nombre_de_voies` (OSM `lanes:forward` / `lanes:backward` split a two-way road where tagged). A
two-way road the survey gives one lane is unmarked unless it is a collector: on the 20 cm orthophoto a centre line shows on most
two-lane roads and one-lane collectors, and on almost no one-lane local road or street (config.MARKED_ONE_LANE_CLASSES).

Where width, lane count or marking change between two sections of a link, the change is spread over a transition zone centred on
their boundary (`zones`), as long as a car needs to shift sideways by the change at TAPER_LATERAL_SPEED when driving at the design
speed (1:25 at 90 km/h, 1:14 at 50), never shorter than WIDTH_TAPER. Width and lane count ease through it with a smooth-step, so
a dropped lane narrows to nothing and the divider beside it runs into the edge line.

Markings follow from the lanes and the road's own profile:
  - a centre line between the two directions of a marked two-way road, dividers between lanes of one direction;
  - no overtaking (solid on that side) where the sight distance is shorter than SIGHT_NO_OVERTAKING_SECONDS at the limit, checked per
    direction: over our own height profile, and round bends as far sideways as the LiDAR surface model shows open ground;
  - edge lines on marked roads, their style from the drawn width (smoothed so a section boundary does not flip it);
  - `turn:lanes` arrows near the end of a lane arriving at a junction.
"""
import numpy as np
from scipy.ndimage import median_filter

from rasters import Mosaic

from . import config

CENTRE, DIVIDER = 0, 1                                  # kinds of lane line
EDGE_NONE, EDGE_DASHED, EDGE_SOLID = 0, 1, 2
ARROW_BITS = {"left": 1, "slight_left": 1, "sharp_left": 1, "through": 2, "right": 4, "slight_right": 4, "sharp_right": 4}


def _count(text):
    try:
        return max(int(str(text).strip()), 0)
    except (TypeError, ValueError):
        return None


def _turn_count(tags, key):
    text = tags.get(key)
    return len(text.split("|")) if text else None


def edge_lanes(edge):
    """(lanes along the digitised direction, lanes against it, marked) of a surveyed section. Two-way roads have at least one lane
    each way (cars share an unmarked one-lane road); `marked` says whether lines are painted. Where OSM tags `turn:lanes`, its count
    wins for that direction (approaches widening into turn lanes, which BD TOPO counts as one: Herbettes sector, 32 of 43 sets
    disagreeing), as long as the lanes fit in the surveyed width at LANE_MIN_WIDTH."""
    tags = edge.tags or {}
    fit = max(int(edge.width_real // config.LANE_MIN_WIDTH), 1)
    if edge.oneway:
        n = _turn_count(tags, "turn:lanes") or edge.lanes or (2 if edge.width_real >= config.LANES_INFERRED_ONEWAY_WIDTH else 1)
        n = min(n, max(fit, edge.lanes, 1))
        along, against = (n, 0) if edge.oneway == 1 else (0, n)
        marked = n >= 2 or edge.kind in (2, 3)
    else:
        forward, backward = _count(tags.get("lanes:forward")), _count(tags.get("lanes:backward"))
        forward = _turn_count(tags, "turn:lanes:forward") or forward
        backward = _turn_count(tags, "turn:lanes:backward") or backward
        if forward and backward and forward + backward <= max(fit, edge.lanes, 2):
            along, against = (forward, backward) if edge.osm_same else (backward, forward)
            marked = True
        else:
            n = edge.lanes or (2 if edge.width_real >= config.LANES_INFERRED_TWO_WAY_WIDTH else 1)
            along, against = max(n - n // 2, 1), max(n // 2, 1)
            marked = n >= 2 or edge.klass in config.MARKED_ONE_LANE_CLASSES
    if edge.dirt or edge.kind == 5:
        marked = False
    if edge.kind == 1:
        marked = max(along, against) >= 2                # a ring is painted only when it has two lanes or more
    return along, against, marked


def chain_lanes(chain, edges):
    """(n sections, 3) of a link's chain: lanes forward, lanes backward (along the link), marked."""
    out = []
    for e, rev in chain:
        along, against, marked = edge_lanes(edges[e])
        out.append((against, along, marked) if rev else (along, against, marked))
    return np.array(out, float)


def zones(chain_edges, bounds, part_hw, lanes):
    """Transition zones of a link: [(start, end, k)] where section k gives way to section k + 1 (bounds[k] is their boundary)."""
    out = []
    for k in range(len(bounds) - 1):
        d_width = abs(part_hw[k + 1] - part_hw[k]) * 2.0
        d_lanes = np.abs(lanes[k + 1, :2] - lanes[k, :2]).sum()
        if d_width < 1e-3 and d_lanes == 0 and lanes[k, 2] == lanes[k + 1, 2]:
            continue
        kmh = max(chain_edges[k].road_class.design_kmh, chain_edges[k + 1].road_class.design_kmh)
        shift = max(d_width, d_lanes * config.LANE_WIDTH)
        length = max(config.WIDTH_TAPER, shift / config.TAPER_LATERAL_SPEED * kmh / 3.6)
        out.append((bounds[k] - 0.5 * length, bounds[k] + 0.5 * length, k))
    return out


def ease(s, values, zone_list):
    """Per-section values eased through the transition zones: the first section's value plus one smooth-step per zone."""
    values = np.asarray(values, float)
    out = np.full((len(s),) + values.shape[1:], values[0])
    for a, b, k in zone_list:
        t = np.clip((s - a) / max(b - a, 1e-6), 0.0, 1.0)
        step = t * t * (3.0 - 2.0 * t)
        out = out + np.multiply.outer(step, values[k + 1] - values[k])
    return out


def _lines(hw, forward, backward):
    """Lane lines of every sample: (offsets (n, K) metres left of the centreline, nan where absent; kinds (K,)). The lanes of each
    direction are numbered from the centre outward, so a lane being dropped is the outer one and its divider meets the edge line."""
    total = np.maximum(forward + backward, 1e-6)
    unit = 2.0 * hw / total
    centre = hw - backward * unit
    n_back, n_fwd = int(np.ceil(backward.max() - 1e-6)), int(np.ceil(forward.max() - 1e-6))
    offsets, kinds = [], []
    for j in range(max(n_back - 1, 0))[::-1]:
        lane_sum = sum(np.clip(backward - i, 0.0, 1.0) for i in range(j + 1))
        offsets.append(np.where(backward > j + 1 + 1e-6, centre + unit * lane_sum, np.nan))
        kinds.append(DIVIDER)
    offsets.append(np.where((forward > 1e-6) & (backward > 1e-6), centre, np.nan))
    kinds.append(CENTRE)
    for j in range(max(n_fwd - 1, 0)):
        lane_sum = sum(np.clip(forward - i, 0.0, 1.0) for i in range(j + 1))
        offsets.append(np.where(forward > j + 1 + 1e-6, centre - unit * lane_sum, np.nan))
        kinds.append(DIVIDER)
    return np.array(offsets).T.reshape(len(hw), -1), np.array(kinds, np.uint8)


def clearances(link, terrain):
    """(left, right) per sample: how far from the centreline the driver can see sideways before something stands above eye level
    (surface model: LiDAR ground + height above ground: a cutting, a hedge, a wood, a house), scanned out to SIGHT_SCAN."""
    out = []
    for side in (1.0, -1.0):
        clear = np.full(len(link.s), config.SIGHT_SCAN)
        for offset in np.arange(0.5, config.SIGHT_SCAN, config.SIGHT_SCAN_STEP):
            at = link.xy + side * link.nrm * (link.hw + offset)[:, None]
            surface = terrain.sample(terrain.mnt, at[:, 0], at[:, 1], 2) + terrain.sample(terrain.mnh, at[:, 0], at[:, 1], 2)
            hit = (surface > link.z + config.SIGHT_EYE) & (clear >= config.SIGHT_SCAN)
            clear[hit] = link.hw[hit] + offset
        out.append(clear)
    return out


def sight_distances(s, xy, tan, z, left, right):
    """Distance ahead (increasing s) from each sample to the first point of the road a driver cannot see: over a crest (eye and
    object SIGHT_EYE above the road) or round a bend (the line of sight passes further from the centreline than the measured
    clearance on that side). inf where the link ends first or the road is clear for SIGHT_MAX."""
    n = len(s)
    out = np.full(n, np.inf)
    lo, hi, high = np.full(n, -np.inf), np.full(n, np.inf), np.full(n, -np.inf)
    i = np.arange(n)
    for k in range(1, n):
        a, b = i[:n - k], i[k:]
        alive = np.isinf(out[a]) & (s[b] - s[a] <= config.SIGHT_MAX)
        if not alive.any():
            break
        a, b = a[alive], b[alive]
        run = s[b] - s[a]
        rise = (z[b] - z[a]) / run                                            # eye and object at the same height above the road
        d = xy[b] - xy[a]
        dist = np.maximum(np.hypot(d[:, 0], d[:, 1]), 1e-6)
        bearing = np.arctan2(tan[a, 0] * d[:, 1] - tan[a, 1] * d[:, 0], (tan[a] * d).sum(axis=1))     # from the eye's heading, left positive
        blocked = (rise < high[a]) | (bearing < lo[a]) | (bearing > hi[a])
        out[a[blocked]] = run[blocked]
        clear = ~blocked
        a, b, run, dist, bearing = a[clear], b[clear], run[clear], dist[clear], bearing[clear]
        high[a] = np.maximum(high[a], (z[b] - config.SIGHT_EYE - z[a]) / run)
        lo[a] = np.maximum(lo[a], bearing - np.arcsin(np.clip(right[b] / dist, 0.0, 1.0)))
        hi[a] = np.minimum(hi[a], bearing + np.arcsin(np.clip(left[b] / dist, 0.0, 1.0)))
    return out


def _close_open(flags, close, open_):
    """Fill gaps shorter than `close` segments, then drop runs shorter than `open_` segments."""
    flags = flags.copy()
    for value, size in ((False, close), (True, open_)):
        k = 0
        while k < len(flags):
            if flags[k] != value:
                k += 1
                continue
            m = k
            while m < len(flags) and flags[m] == value:
                m += 1
            if m - k < size and k > 0 and m < len(flags):
                flags[k:m] = not value
            k = m
    return flags


def no_overtaking(link, limits_fwd, limits_back, terrain):
    """(n - 1, 2) per segment: overtaking forbidden for traffic along the link, against it."""
    s, xy, tan, z = link.s, link.xy, link.tan, link.z
    need_fwd = limits_fwd / 3.6 * config.SIGHT_NO_OVERTAKING_SECONDS
    need_back = limits_back / 3.6 * config.SIGHT_NO_OVERTAKING_SECONDS
    if terrain is None:
        lo, hi = np.floor((link.xy.min(axis=0) - config.SIGHT_SCAN - 8.0) / 4.0) * 4.0, np.ceil((link.xy.max(axis=0) + config.SIGHT_SCAN + 8.0) / 4.0) * 4.0
        terrain = Mosaic(lo[0], lo[1], hi[0] - lo[0], hi[1] - lo[1], kinds=("mnt", "mnh"))
    left, right = clearances(link, terrain)
    ahead = sight_distances(s, xy, tan, z, left, right)
    behind = sight_distances(s[-1] - s[::-1], xy[::-1], -tan[::-1], z[::-1], right[::-1], left[::-1])[::-1]
    fwd = np.minimum(ahead[:-1], ahead[1:]) < need_fwd
    back = np.minimum(behind[:-1], behind[1:]) < need_back
    step = max(float(np.median(np.diff(s))), 0.5) if len(s) > 1 else 1.0
    close, open_ = int(config.NO_OVERTAKING_GAP / step), int(config.NO_OVERTAKING_MIN / step)
    return np.c_[_close_open(fwd, close, open_), _close_open(back, close, open_)]


def edge_styles(link, marked, dual, ring):
    """Edge line style per segment from the drawn width, smoothed over EDGE_STYLE_WINDOW so it does not flip at a section boundary."""
    width = (link.hw[:-1] + link.hw[1:])
    style = np.where(width >= config.EDGE_SOLID_WIDTH, EDGE_SOLID, np.where(width >= config.EDGE_DASHED_WIDTH, EDGE_DASHED, EDGE_NONE))
    if len(style) > 2:
        step = max(float(np.median(np.diff(link.s))), 0.5)
        style = median_filter(style, size=max(int(config.EDGE_STYLE_WINDOW / step), 1) | 1, mode="nearest")
    style = np.where(dual, EDGE_SOLID, style)
    return np.where(marked & ~ring, style, EDGE_NONE).astype(np.uint8)


def arrows(link, edges, forward, backward, offsets_centre, unit):
    """[(s, x, north, z, dx, dnorth, bits)] of `turn:lanes` arrows painted before each junction end of a link: per lane arriving there,
    at ARROW_DISTANCES before the junction mouth while the lane count matches the tag (it may not: lanes that do not fit in the
    surveyed width are not drawn, see edge_lanes; those sets are counted as skipped)."""
    out, skipped = [], 0
    for end in (0, 1):
        if link.junction[end] < 0 or link.internal:
            continue
        e, rev = link.chain[0 if end == 0 else -1]
        edge = edges[e]
        tags = edge.tags or {}
        arriving_along_edge = (end == 1) != rev                                    # traffic arriving at this end runs along the edge
        if edge.oneway and (edge.oneway == 1) != arriving_along_edge:
            continue                                                               # one-way away from this end: nobody arrives
        along_way = arriving_along_edge == edge.osm_same
        key = "turn:lanes" if edge.oneway else "turn:lanes:forward" if along_way else "turn:lanes:backward"
        text = tags.get(key)
        if not text:
            continue
        lanes = [sum(ARROW_BITS.get(v.strip(), 0) for v in set(part.split(";"))) for part in text.split("|")]
        mouth = link.i1 if end == 1 else link.i0
        sign = 1.0 if end == 1 else -1.0                                            # direction of travel along the link
        for distance in config.ARROW_DISTANCES:
            at = link.s[mouth] - sign * distance
            if not link.s[link.i0] <= at <= link.s[link.i1]:
                continue
            i = int(np.clip(np.searchsorted(link.s, at) - 1, 0, len(link.s) - 2))
            t = (at - link.s[i]) / max(link.s[i + 1] - link.s[i], 1e-6)
            count = (forward if end == 1 else backward)[i]
            if abs(count - len(lanes)) > 1e-3:
                skipped += 1
                continue
            xy = link.xy[i] + t * (link.xy[i + 1] - link.xy[i])
            tan = link.tan[i] * sign
            z = link.z[i] + t * (link.z[i + 1] - link.z[i])
            for j, bits in enumerate(lanes):
                if not bits:
                    continue
                offset = offsets_centre[i] - sign * unit[i] * (j + 0.5)            # from the centre line outward, driver's left first
                p = xy + link.nrm[i] * offset
                out.append((at, p[0], p[1], z + link.tilt[i] * offset, tan[0], tan[1], bits))
    return out, skipped


def layout(link, edges, terrain=None):
    """Fill the lane attributes of a finished link (samples, heights): lanes, lane lines, no-overtaking (needs `terrain`, a Mosaic
    with mnt and mnh around the link, where a centre line is painted), edge styles, arrows."""
    per_part = chain_lanes(link.chain, edges)
    zone_list = link.zones or []
    s = link.s
    eased = ease(s, per_part[:, :2], zone_list)
    link.lanes = eased.astype(np.float32)                                          # (n, 2): forward, backward
    mid = 0.5 * (s[:-1] + s[1:])
    marked = ease(mid, per_part[:, 2], zone_list) >= 0.5
    link.marked = marked
    offsets, kinds = _lines(link.hw, eased[:, 0], eased[:, 1])
    link.line_offsets, link.line_kinds = offsets.astype(np.float32), kinds
    seg_edge = [edges[link.chain[p][0]] for p in link.part]
    seg_rev = [link.chain[p][1] for p in link.part]
    along = np.array([(e.limit_back or e.limit) if rev else e.limit for e, rev in zip(seg_edge, seg_rev)], float)
    against = np.array([e.limit if rev else (e.limit_back or e.limit) for e, rev in zip(seg_edge, seg_rev)], float)
    centre_marked = marked & np.isfinite(offsets[:-1, list(kinds).index(CENTRE)]) & np.isfinite(offsets[1:, list(kinds).index(CENTRE)])
    if centre_marked.any() and len(s) > 2:
        link.no_overtaking = no_overtaking(link, along, against, terrain) & centre_marked[:, None]
    else:
        link.no_overtaking = np.zeros((len(s) - 1, 2), bool)
    dual = np.array([e.kind in (2, 3) for e in seg_edge], bool)
    ring = np.array([e.kind == 1 for e in seg_edge], bool)
    link.edge_style = edge_styles(link, marked, dual, ring)
    total = np.maximum(eased.sum(axis=1), 1e-6)
    unit = 2.0 * link.hw / total
    link.arrows, skipped = arrows(link, edges, eased[:, 0], eased[:, 1], link.hw - eased[:, 1] * unit, unit)
    return skipped


def _needs_sight(link, edges):
    return not link.internal and len(link.s) > 2 and any(edge_lanes(edges[e])[2] and not edges[e].oneway for e, _ in link.chain)


def layout_all(links, edges):
    """Lanes of every link (the links that may carry a centre line grouped by PROFILE_TILE, with the rasters of their tile).
    Returns the build report's numbers."""
    skipped = 0
    groups = {}
    for link in links:
        key = tuple(np.floor(link.xy.mean(axis=0) / config.PROFILE_TILE).astype(int)) if _needs_sight(link, edges) else None
        groups.setdefault(key, []).append(link)
    for key, group in groups.items():
        terrain = None
        if key is not None:
            lo = np.min([link.xy.min(axis=0) for link in group], axis=0) - config.SIGHT_SCAN - 8.0
            hi = np.max([link.xy.max(axis=0) for link in group], axis=0) + config.SIGHT_SCAN + 8.0
            lo, hi = np.floor(lo / 4.0) * 4.0, np.ceil(hi / 4.0) * 4.0
            terrain = Mosaic(lo[0], lo[1], hi[0] - lo[0], hi[1] - lo[1], kinds=("mnt", "mnh"))
        for link in group:
            skipped += layout(link, edges, terrain)
    drawn = [(link, slice(link.i0, link.i1)) for link in links if not link.internal and link.i1 > link.i0]
    length = sum(float(np.diff(link.s)[seg].sum()) for link, seg in drawn)

    def km(flags_of):
        return round(sum(float((np.diff(link.s)[seg] * flags_of(link)[seg]).sum()) for link, seg in drawn) / 1000.0, 1)

    styles = [link.edge_style[seg] for link, seg in drawn]
    return dict(
        transition_zones=sum(len(link.zones or []) for link in links),
        lane_change_zones=sum(1 for link in links for per_part in [chain_lanes(link.chain, edges)] for _, _, k in (link.zones or [])
                              if (per_part[k] != per_part[k + 1]).any()),
        marked_km=km(lambda link: link.marked), drawn_km=round(length / 1000.0, 1),
        no_overtaking_km=km(lambda link: link.no_overtaking.any(axis=1)),
        edge_style_changes_per_km=round(sum(int((np.diff(st.astype(int)) != 0).sum()) for st in styles) / max(length / 1000.0, 1e-6), 2),
        arrows=sum(len(link.arrows) for link in links), arrow_sets_skipped=skipped)
