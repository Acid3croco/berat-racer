"""Stage 9: the lane graph traffic drives on, built offline.

Elements are polylines a car follows from start to end, then onto one of its successors:

  lane        one lane of a link in one direction between two breakpoints (link ends, the start and end of a transition zone where
              the lane count changes). It runs at the lane's local centre, offset from the smoothed centreline (`lanes.py`).
  change      where a lane is dropped (it merges into the next lane in) or added (it leaves the outer lane), across the zone.
  connector   through a junction, from a lane arriving at an arm's mouth to a lane leaving another arm's mouth: a cubic Bézier whose
              handles make it a circular arc between the two tangents (the kerb corners of `junction.py` are arcs of the same kind).
  u-turn      at a dead end of a two-way road, from the arriving lane to the leaving one.

Which connectors exist: no U-turn on the same arm; from a lane `turn:lanes` allows (else the leftmost lane turns left, the rightmost
turns right, every lane goes straight on); OSM restriction relations (`no_*`, `only_*`) and BD TOPO `non_communication` remove more.
Each connector carries the control of its arm (a stop or give-way sign or traffic lights from OSM nodes, else the rank of the arms:
the lower-ranked arm of a junction gives way; equal ranks: priority to the right; entering a roundabout gives way), and the list of
connectors it must yield to: those that cross or merge with it and have the priority over it. Every point carries the speed its
curvature allows (LATERAL_ACCEL) under the limit.
"""
from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely.geometry import LineString, Point

from . import config, lanes as lanes_stage

LANE, CHANGE, CONNECTOR, UTURN = 0, 1, 2, 3
PRIORITY, GIVE_WAY, STOP, SIGNALS, RIGHT = 0, 1, 2, 3, 4
CONTROL_NAMES = ("priority", "give_way", "stop", "signals", "right")
LEFT_BIT, THROUGH_BIT, RIGHT_BIT = 1, 2, 4


@dataclass
class Element:
    id: int
    kind: int
    xyz: np.ndarray                         # (n, 3) x, north, z
    limit: int                              # km/h
    speed: np.ndarray = None                # (n,) m/s allowed by the curvature and the limit
    succ: list = field(default_factory=list)
    left: int = -1                          # the lane beside it on the left (same direction), for lane changes
    right: int = -1
    control: int = PRIORITY                 # connectors
    yields: list = field(default_factory=list)   # connectors this one must give way to
    road: tuple = (0, 0, 0, 0.0, False)     # (kind, importance, rank, half width, dirt) of the road it belongs to
    junction: int = -1
    turn: float = 0.0                       # degrees, left positive (connectors)
    arm_in: int = -1                        # index into the junction's arms (connectors)
    owner: tuple = None                     # ("link", index) or ("junction", index): what it was made for (tiled builds number it from there)


def _frame(link, s):
    """Centreline, normal, height, cross slope, half width and eased lanes of a link at distances `s`."""
    def lerp(values):
        return np.interp(s, link.s, values)
    xy = np.c_[lerp(link.xy[:, 0]), lerp(link.xy[:, 1])]
    nrm = np.c_[lerp(link.nrm[:, 0]), lerp(link.nrm[:, 1])]
    nrm /= np.maximum(np.hypot(nrm[:, 0], nrm[:, 1]), 1e-9)[:, None]
    return xy, nrm, lerp(link.z), lerp(link.tilt), lerp(link.hw), np.c_[lerp(link.lanes[:, 0]), lerp(link.lanes[:, 1])]


def lane_offsets(hw, eased, direction, j):
    """Offset (metres left of the centreline) of the centre of lane `j` of `direction` (0 along the link, 1 against it), numbered from
    the centre line outward."""
    total = np.maximum(eased.sum(axis=1), 1e-6)
    unit = 2.0 * hw / total
    centre = hw - eased[:, 1] * unit
    n = eased[:, direction]
    before = sum(np.clip(n - i, 0.0, 1.0) for i in range(j)) if j else 0.0
    width = np.clip(n - j, 0.0, 1.0)
    inward = unit * (before + 0.5 * width)
    return centre - inward if direction == 0 else centre + inward


def _lane_line(link, a, b, direction, j):
    """(n, 3) points of lane j of `direction` between distances a and b of the link, in the direction of travel."""
    inner = link.s[(link.s > min(a, b) + 0.5) & (link.s < max(a, b) - 0.5)]
    s = np.r_[min(a, b), inner, max(a, b)]
    xy, nrm, z, tilt, hw, eased = _frame(link, s)
    offset = lane_offsets(hw, eased, direction, j)
    pts = np.c_[xy + nrm * offset[:, None], z + tilt * offset]
    return pts if direction == 0 else pts[::-1]


def _bezier(p0, d0, p3, d3, step=0.5):
    """Points of the cubic from p0 leaving along d0 to p3 arriving along d3 (unit plan directions), handles sized so it follows a
    circular arc when the ends allow one."""
    chord = float(np.hypot(*(p3[:2] - p0[:2])))
    turn = abs(np.arctan2(d0[0] * d3[1] - d0[1] * d3[0], d0 @ d3))
    if turn < 1e-3:
        h = chord / 3.0
    else:
        radius = chord / max(2.0 * np.sin(turn / 2.0), 1e-6)
        h = min(4.0 / 3.0 * np.tan(turn / 4.0) * radius, 0.9 * chord)
    c1 = p0[:2] + d0 * h
    c2 = p3[:2] - d3 * h
    n = max(int(np.ceil(chord * 1.3 / step)), 4)
    t = np.linspace(0.0, 1.0, n + 1)[:, None]
    xy = (1 - t) ** 3 * p0[:2] + 3 * (1 - t) ** 2 * t * c1 + 3 * (1 - t) * t ** 2 * c2 + t ** 3 * p3[:2]
    z = p0[2] + (p3[2] - p0[2]) * (3 * t[:, 0] ** 2 - 2 * t[:, 0] ** 3)
    return np.c_[xy, z]


def _around(p0, d0, p3, d3, island, step=0.5):
    """A path round a roundabout's island, counter-clockwise: in from p0, along a circle halfway between the island and the
    nearer mouth, out to p3."""
    c = np.array(island.centroid.coords[0])
    r_island = float(np.sqrt(island.area / np.pi))
    radius = 0.5 * (r_island + min(np.hypot(*(p0[:2] - c)), np.hypot(*(p3[:2] - c))))
    a0 = np.arctan2(*(p0[:2] - c)[::-1]) + 0.35
    a1 = np.arctan2(*(p3[:2] - c)[::-1]) - 0.35
    while a1 <= a0:
        a1 += 2 * np.pi
    angles = np.linspace(a0, a1, max(int((a1 - a0) * radius / step), 2))
    ring = np.c_[c[0] + radius * np.cos(angles), c[1] + radius * np.sin(angles)]
    tangent = lambda a: np.array([-np.sin(a), np.cos(a)])
    z = lambda t: p0[2] + (p3[2] - p0[2]) * t
    head = _bezier(p0, d0, np.r_[ring[0], z(0.2)], tangent(a0), step)
    tail = _bezier(np.r_[ring[-1], z(0.8)], tangent(a1), p3, d3, step)
    middle = np.c_[ring, z(np.linspace(0.2, 0.8, len(ring)))]
    return np.vstack([head[:-1], middle, tail[1:]])


def _direction(pts, at_end, reach=1.0):
    """Unit plan direction of travel at the end (or start) of a polyline, over the last (first) `reach` metres."""
    xy = pts[::-1, :2] if at_end else pts[:, :2]
    dist = np.hypot(*(xy - xy[0]).T)
    k = int(np.argmax(dist > max(reach, 1e-3))) if (dist > max(reach, 1e-3)).any() else len(xy) - 1
    d = (xy[0] - xy[k]) if at_end else (xy[k] - xy[0])
    return d / max(np.hypot(*d), 1e-9)


def curvature(pts):
    """Plan curvature (1/m) at every point (Menger, over neighbours at least 2 m away)."""
    xy = pts[:, :2]
    n = len(xy)
    if n < 3:
        return np.zeros(n)
    s = np.r_[0.0, np.cumsum(np.hypot(*np.diff(xy, axis=0).T))]
    i = np.arange(1, n - 1)
    a = np.clip(np.minimum(np.searchsorted(s, s[i] - 2.0, side="right") - 1, i - 1), 0, None)
    b = np.clip(np.maximum(np.searchsorted(s, s[i] + 2.0), i + 1), None, n - 1)
    p, q, r = xy[a], xy[i], xy[b]
    area2 = np.abs((q[:, 0] - p[:, 0]) * (r[:, 1] - p[:, 1]) - (q[:, 1] - p[:, 1]) * (r[:, 0] - p[:, 0]))
    sides = np.hypot(*(q - p).T) * np.hypot(*(r - q).T) * np.hypot(*(r - p).T)
    k = np.r_[0.0, 2.0 * area2 / np.maximum(sides, 1e-9), 0.0]
    k[0], k[-1] = k[1], k[-2]
    return k


class Builder:
    def __init__(self, network, controls):
        self.network, self.controls = network, controls or dict(nodes=[], restrictions=[], no_turn=[])
        self.elements = []
        self.ends = {}               # (link, direction, at the link end it arrives at? -> list per lane): elements arriving / leaving at a link end
        self.stats = dict(lanes=0, changes=0, connectors=0, uturns=0, restricted=0, by_turn_lanes=0, dead_ends=0, around_islands=0)
        self.applied = set()          # restrictions found on the network (their ways at their junction)
        self.node_xy = np.array([(n["x"], n["z"]) for n in self.controls["nodes"]]).reshape(-1, 2)
        self.owner = None                                                   # what the elements being made belong to

    def add(self, kind, pts, limit, road, **kw):
        e = Element(id=len(self.elements), kind=kind, xyz=pts, limit=int(limit), road=road, owner=self.owner, **kw)
        self.elements.append(e)
        return e

    # ------------------------------------------------------------------ lanes along links
    def link_lanes(self, k, link):
        edges = self.network.edges
        per_part = lanes_stage.chain_lanes(link.chain, edges)
        a0, a1 = link.s[link.i0], link.s[link.i1]
        for direction in (0, 1):
            counts = per_part[:, direction].astype(int)
            if counts.max() == 0:
                continue
            breaks = []                                                         # (za, zb, n before, m after) along the link
            for za, zb, part in link.zones or []:
                n, m = counts[part], counts[part + 1]
                if n != m:
                    breaks.append((max(za, a0), min(zb, a1), n, m))
            # pieces in increasing s: (start, end, lane count, kind) where kind 0 plain, 1 inside a zone
            pieces, s0, n_now = [], a0, counts[link.part[link.i0]] if link.i1 > link.i0 else counts[0]
            for za, zb, n, m in breaks:
                za = max(za, s0)
                if za > s0 + 0.05:
                    pieces.append((s0, za, n, 0))
                if zb > za + 0.05:
                    pieces.append((za, zb, min(n, m), 1))
                s0, n_now = zb, m
            if a1 > s0 + 0.05 or not pieces:
                pieces.append((s0, max(a1, s0 + 0.05), n_now, 0))
            if direction == 1:
                pieces = pieces[::-1]
            chain_edge = lambda s: edges[link.chain[link.part[int(np.clip(np.searchsorted(link.s, s) - 1, 0, len(link.part) - 1))]][0]]
            built = []
            for a, b, n, _ in pieces:
                if n == 0:
                    built.append([])
                    continue
                edge = chain_edge(0.5 * (a + b))
                rev = link.chain[link.part[int(np.clip(np.searchsorted(link.s, 0.5 * (a + b)) - 1, 0, len(link.part) - 1))]][1]
                limit = edge.limit if (direction == 0) != rev else (edge.limit_back or edge.limit)
                road = (edge.kind, int(edge.importance) if edge.importance.isdigit() else 0, edge.road_class.rank, float(np.interp(0.5 * (a + b), link.s, link.hw)), edge.dirt)
                row = [self.add(LANE, _lane_line(link, a, b, direction, j), limit, road) for j in range(n)]
                for j, e in enumerate(row):
                    e.left = row[j - 1].id if j > 0 else -1
                    e.right = row[j + 1].id if j + 1 < n else -1
                self.stats["lanes"] += int(n)
                built.append(row)
            # successors along the link, in the direction of travel. A lane dropped where a zone starts merges across the zone into
            # the inner lane after it; a lane added where a zone ends branches off the outer lane where the zone starts.
            for p in range(len(built) - 1):
                here, there = built[p], built[p + 1]
                for j, e in enumerate(here):
                    if j < len(there):
                        e.succ.append(there[j].id)
                    elif there:
                        after = built[p + 2] if p + 2 < len(built) and len(built[p + 2]) >= len(there) else None
                        self._change(e, there[-1], after[len(there) - 1] if after else None)
                if len(there) > len(here) and here:
                    zone = pieces[p][3] == 1 and p > 0 and len(built[p - 1]) >= len(here)
                    outer = built[p - 1][len(here) - 1] if zone else here[-1]
                    for j in range(len(here), len(there)):
                        self._branch(outer, there[j])
            self.ends[(k, direction, "first")] = built[0] if built else []
            self.ends[(k, direction, "last")] = built[-1] if built else []

    def _change(self, dropped, inner_zone, inner_after):
        """A lane that ends where the zone starts: it merges, across the zone, into the inner lane's continuation after the zone."""
        if inner_after is None:
            dropped.succ.append(inner_zone.id)
            return
        p0, p3 = dropped.xyz[-1], inner_after.xyz[0]
        pts = _bezier(p0, _direction(dropped.xyz, True), p3, _direction(inner_after.xyz, False))
        e = self.add(CHANGE, pts, dropped.limit, dropped.road)
        e.succ.append(inner_after.id)
        dropped.succ.append(e.id)
        self.stats["changes"] += 1

    def _branch(self, outer, new):
        """A lane that starts where a zone ends: cars leave the outer lane at the start of the zone."""
        p0, p3 = outer.xyz[-1], new.xyz[0]
        pts = _bezier(p0, _direction(outer.xyz, True), p3, _direction(new.xyz, False))
        e = self.add(CHANGE, pts, new.limit, new.road)
        e.succ.append(new.id)
        outer.succ.append(e.id)
        self.stats["changes"] += 1

    # ------------------------------------------------------------------ junctions
    def arm_lanes(self, arm):
        """(arriving lanes, leaving lanes) at a junction arm, each numbered from the centre line outward."""
        k, end = arm.link, arm.end
        arriving = self.ends.get((k, 0, "last") if end == 1 else (k, 1, "last"), [])
        leaving = self.ends.get((k, 1, "first") if end == 1 else (k, 0, "first"), [])
        return arriving, leaving

    def arm_edge(self, arm):
        link = self.network.links[arm.link]
        e, rev = link.chain[-1] if arm.end == 1 else link.chain[0]
        return self.network.edges[e], rev

    def _turn_lanes(self, arm, n):
        """Allowed movements (bits) per arriving lane from `turn:lanes`, or None."""
        edge, rev = self.arm_edge(arm)
        arriving_along_edge = (arm.end == 1) != rev
        if edge.oneway:
            key = "turn:lanes"
        else:
            key = "turn:lanes:forward" if arriving_along_edge == edge.osm_same else "turn:lanes:backward"
        text = (edge.tags or {}).get(key)
        if not text:
            return None
        bits = [sum(lanes_stage.ARROW_BITS.get(v.strip(), 0) for v in set(part.split(";"))) or 7 for part in text.split("|")]
        return bits if len(bits) == n else None

    def _restricted(self, junction, arms):
        """Forbidden (arm in, arm out) pairs at a junction from OSM relations and BD TOPO non_communication."""
        nodes = self.network.nodes[junction.nodes]
        forbidden = set()
        edge_of = [self.arm_edge(a)[0] for a in arms]

        def near(x, z):
            return np.hypot(nodes[:, 0] - x, nodes[:, 1] - z).min() < 4.0

        for r in self.controls["restrictions"]:
            if not near(r["x"], r["z"]):
                continue
            ins = [i for i, e in enumerate(edge_of) if e.osm_id == r["from_way"]]
            outs = [i for i, e in enumerate(edge_of) if e.osm_id == r["to_way"]]
            self.applied.add(("osm", r["id"])) if ins and outs else None
            for i in ins:
                if r["restriction"].startswith("no_"):
                    forbidden |= {(i, o) for o in outs}
                elif r["restriction"].startswith("only_"):
                    forbidden |= {(i, o) for o in range(len(arms)) if o not in outs}
        for r in self.controls["no_turn"]:
            if not near(r["x"], r["z"]):
                continue
            ins = [i for i, e in enumerate(edge_of) if e.cleabs == r["entry"]]
            outs = [i for i, e in enumerate(edge_of) if e.cleabs in r["exits"]]
            self.applied.add(("bdtopo", r["entry"], r["x"])) if ins and outs else None
            forbidden |= {(i, o) for i in ins for o in outs}
        return forbidden

    def _controls(self, junction, arms):
        """Control of every arm. Where some arms carry a sign (stop, give way) the others have the priority."""
        signs = [self._sign(junction, arm) for arm in arms]
        if SIGNALS in signs:
            return [SIGNALS] * len(arms)
        if any(c is not None for c in signs):
            return [PRIORITY if c is None else c for c in signs]
        if junction.polygon is not None and any(shapely.geometry.Polygon(h).area >= 12.0 for h in junction.polygon.interiors):
            return [GIVE_WAY] * len(arms)                                      # a roundabout swallowed into one junction: entering gives way
        return [self._by_rank(junction, arms, i) for i in range(len(arms))]

    def _sign(self, junction, arm):
        """OSM lights, stop or give-way sign controlling an arm, or None."""
        link = self.network.links[arm.link]
        mouth = link.xy[link.i1 if arm.end == 1 else link.i0]
        out_dir = -link.tan[link.i1] if arm.end == 1 else link.tan[link.i0]          # from the mouth away from the junction
        best_sign = None
        near = np.flatnonzero(np.hypot(*(self.node_xy - mouth).T) < config.CONTROL_REACH + 40.0) if len(self.node_xy) else []
        grown = junction.polygon.buffer(2.0) if junction.polygon is not None and len(near) else None
        for node in (self.controls["nodes"][i] for i in near):
            p = np.array([node["x"], node["z"]])
            d = p - mouth
            along, across = d @ out_dir, abs(d[0] * out_dir[1] - d[1] * out_dir[0])
            inside = grown is not None and grown.contains(Point(*p))
            on_arm = -3.0 <= along <= config.CONTROL_REACH and across <= link.hw[link.i1 if arm.end == 1 else link.i0] + 3.0
            if node["highway"] == "traffic_signals" and (inside or on_arm):
                return SIGNALS
            if on_arm and node["highway"] in ("stop", "give_way"):
                best_sign = STOP if node["highway"] == "stop" else GIVE_WAY
        return best_sign

    def _by_rank(self, junction, arms, arm_index):
        """Control of an arm without a sign: the ranks of the roads, roundabouts."""
        arm = arms[arm_index]
        ranks = [self.arm_edge(a)[0].road_class.rank for a in arms]
        rings = [self.arm_edge(a)[0].kind == 1 for a in arms]
        mine = self.arm_edge(arm)[0]
        if any(rings) and not rings[arm_index]:
            return GIVE_WAY                                                    # entering a roundabout
        if any(rings):
            return PRIORITY
        minis = [n for n in self.controls["nodes"] if n["highway"] == "mini_roundabout"]
        if minis and junction.polygon is not None and any(junction.polygon.distance(Point(n["x"], n["z"])) < 3.0 for n in minis):
            return GIVE_WAY
        if len(arms) < 3:
            return PRIORITY
        best = max(ranks)
        if ranks[arm_index] < best and junction.paved and not mine.dirt:
            return GIVE_WAY
        return PRIORITY if sum(r == best for r in ranks) == 2 and ranks[arm_index] == best else RIGHT

    def junction(self, ji, junction):
        arms = junction.arms
        if not arms:
            return
        lanes_at = [self.arm_lanes(a) for a in arms]
        holes = [shapely.geometry.Polygon(h) for h in junction.polygon.interiors] if junction.polygon is not None else []
        island = max(holes, key=lambda h: h.area) if holes else None             # a roundabout swallowed into one junction
        forbidden = self._restricted(junction, arms)
        self.stats["restricted"] += len(forbidden)
        controls = self._controls(junction, arms)
        made = []
        for i, arm in enumerate(arms):
            arriving = lanes_at[i][0]
            if not arriving:
                continue
            allowed = self._turn_lanes(arm, len(arriving))
            if allowed is not None:
                self.stats["by_turn_lanes"] += 1
            for o, other in enumerate(arms):
                leaving = lanes_at[o][1]
                if o == i or not leaving or (i, o) in forbidden:
                    continue
                d0 = _direction(arriving[0].xyz, True)
                d1 = _direction(leaving[0].xyz, False)
                turn = float(np.degrees(np.arctan2(d0[0] * d1[1] - d0[1] * d1[0], d0 @ d1)))
                if abs(turn) > config.TURN_MAX and not self._no_other_exit(i, o, arms, lanes_at, forbidden):
                    continue
                movement = LEFT_BIT if turn > config.TURN_STRAIGHT else RIGHT_BIT if turn < -config.TURN_STRAIGHT else THROUGH_BIT
                n, m = len(arriving), len(leaving)
                if allowed is not None:
                    from_lanes = [j for j in range(n) if allowed[j] & movement]
                elif n == 1:
                    from_lanes = [0]
                else:
                    from_lanes = [0] if movement == LEFT_BIT else [n - 1] if movement == RIGHT_BIT else list(range(n))
                if self._only_way_out(i, o, arms, lanes_at, forbidden):
                    from_lanes = list(range(n))                                 # the one way out of this arm: every lane takes it
                for rank_j, j in enumerate(from_lanes):
                    if movement == LEFT_BIT:
                        out = min(rank_j, m - 1)
                    elif movement == RIGHT_BIT:
                        out = max(m - len(from_lanes) + rank_j, 0)
                    else:
                        out = min(j, m - 1)
                    src, dst = arriving[j], leaving[out]
                    pts = _bezier(src.xyz[-1], _direction(src.xyz, True), dst.xyz[0], _direction(dst.xyz, False))
                    if island is not None and LineString(pts[:, :2]).crosses(island.exterior):
                        pts = _around(src.xyz[-1], _direction(src.xyz, True), dst.xyz[0], _direction(dst.xyz, False), island)
                        self.stats["around_islands"] += 1
                    if junction.plane is not None:
                        pts[1:-1, 2] = junction.height(pts[1:-1, :2])
                    e = self.add(CONNECTOR, pts, dst.limit, dst.road, control=controls[i], junction=ji, turn=turn, arm_in=i)
                    e.succ.append(dst.id)
                    src.succ.append(e.id)
                    made.append(e)
                    self.stats["connectors"] += 1
        self._yields(made, arms)

    @staticmethod
    def _only_way_out(i, o, arms, lanes_at, forbidden):
        return all(k in (i, o) or not lanes_at[k][1] or (i, k) in forbidden for k in range(len(arms)))

    @staticmethod
    def _no_other_exit(i, o, arms, lanes_at, forbidden):
        """Is every other way out of arm i sharper than TURN_MAX (or forbidden)? Then the sharp one is offered: a lane must lead on."""
        d0 = _direction(lanes_at[i][0][0].xyz, True)
        for k in range(len(arms)):
            if k in (i, o) or not lanes_at[k][1] or (i, k) in forbidden:
                continue
            d1 = _direction(lanes_at[k][1][0].xyz, False)
            if abs(np.degrees(np.arctan2(d0[0] * d1[1] - d0[1] * d1[0], d0 @ d1))) <= config.TURN_MAX:
                return False
        return True

    def _yields(self, made, arms):
        """Who gives way to whom among the connectors of one junction: those whose paths cross or merge."""
        if len(made) < 2:
            return
        lines = [LineString(e.xyz[:, :2]) for e in made]
        mouth = [e.xyz[0, :2] for e in made]
        dirs = [_direction(e.xyz, False) for e in made]
        tree = shapely.STRtree(lines)
        for a, b in zip(*tree.query(lines, predicate="intersects")):
            if a == b:
                continue
            A, B = made[a], made[b]
            if A.arm_in == B.arm_in:
                continue                                                       # same arm: they queue, they do not cross
            if self._gives_way(A, B, mouth[a], dirs[a], mouth[b]):
                A.yields.append(B.id)

    @staticmethod
    def _gives_way(A, B, pa, da, pb):
        rank = {PRIORITY: 0, SIGNALS: 0, RIGHT: 1, GIVE_WAY: 2, STOP: 2}
        if rank[A.control] != rank[B.control]:
            return rank[A.control] > rank[B.control]
        if A.control == RIGHT:
            return da[0] * (pb - pa)[1] - da[1] * (pb - pa)[0] < 0.0            # B arrives from my right
        return A.turn > config.TURN_STRAIGHT and B.turn <= config.TURN_STRAIGHT  # turning left across oncoming traffic

    def _turn_round(self, src, candidates):
        """U-turn from the end of `src` onto the start of the nearest of `candidates` (lanes of the other direction)."""
        dst = min(candidates, key=lambda e: float(np.hypot(*(e.xyz[0, :2] - src.xyz[-1, :2]))))
        pts = _bezier(src.xyz[-1], _direction(src.xyz, True), dst.xyz[0], _direction(dst.xyz, False))
        e = self.add(UTURN, pts, 20, src.road)
        e.succ.append(dst.id)
        src.succ.append(e.id)
        self.stats["uturns"] += 1

    def lane_ends(self):
        """A lane that stops inside its link (a two-way road becoming one-way against it): turn round onto the other direction."""
        starts = {}
        for e in self.elements:
            if e.kind == LANE:
                starts.setdefault((round(float(e.xyz[0, 0]), 0), round(float(e.xyz[0, 1]), 0)), []).append(e)
        lanes_ = [e for e in self.elements if e.kind == LANE]
        tree = shapely.STRtree([Point(*e.xyz[0, :2]) for e in lanes_])
        for e in list(self.elements):
            if e.kind != LANE or e.succ:
                continue
            near = [lanes_[i] for i in tree.query(Point(*e.xyz[-1, :2]).buffer(12.0))
                    if lanes_[i] is not e and _direction(lanes_[i].xyz, False) @ _direction(e.xyz, True) < -0.7]
            if near:
                self.owner = e.owner
                self._turn_round(e, near)

    # ------------------------------------------------------------------ dead ends
    def dead_ends(self):
        for k, link in enumerate(self.network.links):
            if link.internal:
                continue
            self.owner = ("link", k)
            for end in (0, 1):
                if link.junction[end] >= 0:
                    continue
                arriving = self.ends.get((k, 0, "last") if end == 1 else (k, 1, "last"), [])
                leaving = self.ends.get((k, 1, "first") if end == 1 else (k, 0, "first"), [])
                if not arriving:
                    continue
                self.stats["dead_ends"] += 1
                if not leaving:
                    continue
                src, dst = arriving[-1], leaving[-1]
                pts = _bezier(src.xyz[-1], _direction(src.xyz, True), dst.xyz[0], _direction(dst.xyz, False))
                e = self.add(UTURN, pts, 20, src.road)
                e.succ.append(dst.id)
                src.succ.append(e.id)
                self.stats["uturns"] += 1

    # ------------------------------------------------------------------ speeds and report
    def speeds(self):
        for e in self.elements:
            cap = e.limit / 3.6
            e.speed = np.minimum(cap, np.sqrt(config.LATERAL_ACCEL / np.maximum(curvature(e.xyz), 1e-6))).astype(np.float32)

    def report(self):
        by_id = self.elements
        kinks, worst = 0, 0.0
        for e in by_id:
            if len(e.xyz) < 2:
                continue
            d0 = _direction(e.xyz, True, reach=0.0)                            # the segments that meet at the join
            for t in e.succ:
                f = by_id[t]
                if UTURN in (e.kind, f.kind) or len(f.xyz) < 2 or np.hypot(*(f.xyz[0, :2] - e.xyz[-1, :2])) > 0.5:
                    continue                                                   # a turn round is one tight curve, not a kink
                angle = float(np.degrees(np.arccos(np.clip(d0 @ _direction(f.xyz, False, reach=0.0), -1.0, 1.0))))
                worst = max(worst, angle)
                kinks += angle > config.KINK_ANGLE
        gaps = sum(1 for e in by_id for t in e.succ if np.hypot(*(by_id[t].xyz[0, :2] - e.xyz[-1, :2])) > 0.5)
        radius = [1.0 / max(curvature(e.xyz).max(), 1e-6) for e in by_id if e.kind == CONNECTOR and abs(e.turn) > 45.0]
        controls = {name: sum(1 for e in by_id if e.kind == CONNECTOR and e.control == c) for c, name in enumerate(CONTROL_NAMES)}
        no_exit = sum(1 for e in by_id if not e.succ)
        restrictions = f"{len(self.applied)} of {len(self.controls['restrictions']) + len(self.controls['no_turn'])}"
        return dict(self.stats, restrictions_matched=restrictions, elements=len(by_id), kinks=kinks, worst_join_deg=round(worst, 1), gaps=gaps, no_exit=no_exit,
                    turn_radius_p10=round(float(np.percentile(radius, 10)), 1) if radius else None,
                    yields=sum(len(e.yields) for e in by_id), controls=controls)


def build(network, controls, junctions=None):
    """The lane graph of a network: [Element], report numbers. `junctions`: the indices of the junctions to connect (None: all)."""
    b = Builder(network, controls)
    for k, link in enumerate(network.links):
        if not link.internal and link.i1 >= link.i0 and link.lanes is not None:
            b.owner = ("link", k)
            b.link_lanes(k, link)
    for ji, junction in enumerate(network.junctions):
        if junctions is None or ji in junctions:
            b.owner = ("junction", ji)
            b.junction(ji, junction)
    b.dead_ends()
    b.lane_ends()
    b.speeds()
    return b.elements, b.report()
