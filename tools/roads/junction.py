"""Stage 4: junction geometry.

Around a node the arms are sorted counter-clockwise. Two neighbouring arms overlap until the facing kerbs cross (the left kerb of
one, the right kerb of the next); each arm is cut back ("trimmed") to where it is clear of both neighbours, plus the length of the
corner curve.

The junction surface is the union of simple pieces: the stub of every arm (from its node to its trim; its far end is the "mouth",
shared vertex for vertex with the end of the drawn road), a rounded wedge in every corner where two kerbs cross, and, where two
kerbs never cross (the far side of a T, the outside of a bend), the area under one smooth curve from mouth to mouth.

When the trims of the two ends of a link leave nothing to draw, the link is swallowed: it becomes part of the surface and its two
nodes share one junction (staggered crossroads, tiny roundabouts, slip-road triangles).
"""
from dataclasses import dataclass, field

import numpy as np
import shapely
import shapely.geometry.polygon
from shapely.geometry import LineString, Polygon

from . import config
from .geometry import left_normals, tangents

ISLAND_MIN = 12.0                 # m2: a hole in a junction smaller than this is paved over


@dataclass
class Arm:
    """One link end seen from its node: arrays run from the node outwards; left / right are the kerbs as seen looking outwards."""
    link: int
    end: int
    node: int
    u: np.ndarray
    xy: np.ndarray
    tan: np.ndarray
    left: np.ndarray
    right: np.ndarray
    corner_radius: float
    swallowed: bool = False                        # the whole link lies inside the junction
    trim: float = config.TRIM_MIN

    def at(self, array, u):
        return np.array([np.interp(u, self.u, array[:, 0]), np.interp(u, self.u, array[:, 1])])


@dataclass
class Junction:
    nodes: list
    fans: dict = field(default_factory=dict)       # node -> its arms, counter-clockwise (swallowed links included)
    corners: dict = field(default_factory=dict)    # node -> per arm i: corner with the next arm: (crossing point, u on i, u on next, tangent length) or None
    internal: list = field(default_factory=list)   # links swallowed by the junction
    centre: np.ndarray = None
    polygon: Polygon = None                        # the junction surface in plan
    vertices: np.ndarray = None                    # (n, 2) corners of the surface
    triangles: np.ndarray = None                   # (m, 3) indices into vertices
    boundary: np.ndarray = None                    # (k, 3): vertex i -> vertex j with the surface on the left; third column 1 on an arm mouth
    valid: bool = True
    problem: str = ""                              # why it is not valid
    paved: bool = True
    plane: np.ndarray = None                       # (z at centre, dz/dx, dz/dz), filled by the profile stage

    @property
    def arms(self):
        """The arms that leave the junction (their links are drawn)."""
        return [arm for fan in self.fans.values() for arm in fan if not arm.swallowed]

    def height(self, xy):
        return self.plane[0] + (np.atleast_2d(xy) - self.centre) @ self.plane[1:]

    def slim(self):
        """Drop what only the construction needed (the dense arrays of every arm): a finished junction is its surface, plane and arm list."""
        self.corners = {}
        for fan in self.fans.values():
            for arm in fan:
                arm.u, arm.xy, arm.tan, arm.left, arm.right = arm.u[:1], arm.xy[:1], arm.tan[:1], arm.left[:1], arm.right[:1]


def make_arm(graph, links, k, end, reach):
    link = links[k]
    inside = link.dense_s <= reach if end == 0 else link.dense_s >= link.length - reach
    xy, hw = link.dense_xy[inside], link.dense_hw[inside]
    u = link.dense_s[inside] if end == 0 else link.length - link.dense_s[inside]
    if end == 1:
        xy, hw, u = xy[::-1], hw[::-1], u[::-1]
    tan = tangents(xy)
    nrm = left_normals(tan)
    edge = graph.edges[link.chain[0 if end == 0 else -1][0]]
    return Arm(link=k, end=end, node=link.nodes[end], u=u, xy=xy, tan=tan, left=xy + nrm * hw[:, None], right=xy - nrm * hw[:, None],
               corner_radius=edge.road_class.corner_radius, swallowed=link.internal)


# ---------------------------------------------------------------- trims

def _points_of(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Point":
        return [np.array(geometry.coords[0])]
    if geometry.geom_type == "LineString":
        return [np.array(geometry.coords[0]), np.array(geometry.coords[-1])]
    return [p for g in geometry.geoms for p in _points_of(g)]


def facing_corner(a, b):
    """Where the left kerb of arm `a` crosses the right kerb of its counter-clockwise neighbour `b`, or None when they never meet."""
    if len(a.u) < 2 or len(b.u) < 2:
        return None
    crossings = _points_of(LineString(a.left).intersection(LineString(b.right)))
    if not crossings:
        return None
    centre_a, centre_b = LineString(a.xy), LineString(b.xy)
    x = min(crossings, key=lambda p: centre_a.project(shapely.Point(p)))
    ua, ub = centre_a.project(shapely.Point(x)), centre_b.project(shapely.Point(x))
    between = np.arccos(np.clip(a.at(a.tan, ua) @ b.at(b.tan, ub), -1.0, 1.0))
    radius = min(a.corner_radius, b.corner_radius)
    tangent = float(np.clip(radius / max(np.tan(between / 2.0), 1e-3), config.CORNER_TANGENT_MIN, config.CORNER_TANGENT_MAX))
    return x, float(ua), float(ub), tangent


def trim_fan(fan):
    """Corners and trims of the arms around one node."""
    n = len(fan)
    corners = [facing_corner(fan[i], fan[(i + 1) % n]) if n > 1 else None for i in range(n)]
    for arm in fan:
        arm.trim = config.TRIM_MIN
    for i, corner in enumerate(corners):
        if corner is None:
            continue
        _, ua, ub, tangent = corner
        a, b = fan[i], fan[(i + 1) % n]
        a.trim, b.trim = max(a.trim, ua + tangent), max(b.trim, ub + tangent)
    for arm in fan:
        if arm.swallowed:
            arm.trim = min(arm.trim, arm.u[-1])
    return corners


def _mouth_line(arm):
    return LineString([arm.at(arm.right, arm.trim), arm.at(arm.left, arm.trim)])


def _dense_mouth(arm):
    return arm.at(arm.right, arm.trim), arm.at(arm.left, arm.trim)


def _reach(arm):
    """The whole arm as far as it was examined (not only its stub)."""
    return Polygon(np.vstack([arm.right, arm.left[::-1]])) if len(arm.u) > 1 else Polygon()


def clear_mouths(junction, graph, links):
    """Push every trim outwards until the mouth of the arm is clear of every other piece of the junction surface and of the
    other arms themselves (two arms leaving side by side overlap far beyond the point where their kerbs first cross)."""
    arms = junction.arms
    reach = {id(a): _polygons(shapely.make_valid(_reach(a))) for a in arms}
    for _ in range(4):
        pieces = _pieces(junction, graph, links, lambda arm: _dense_mouth(arm))
        moved = False
        for a in arms:
            others = shapely.unary_union([part for owner, p in pieces if owner is not a for part in _polygons(shapely.make_valid(p))]
                                         + [part for b in arms if b is not a and b.link != a.link for part in reach[id(b)]])
            while a.trim < a.u[-1] + 1.0 and _mouth_line(a).intersection(others).length > 0.01:       # touching at the corners is fine
                a.trim += 1.0
                moved = True
        if not moved:
            break


def _make_junction(graph, links, nodes, touching, internal):
    """The junction of a group of nodes: its arms sorted and trimmed. Arms are examined TRIM_REACH out; if one is still entangled
    with a neighbour there (a slip road leaving a motorway at a few degrees), the junction is built again looking TRIM_REACH_LONG out."""
    for reach in (config.TRIM_REACH, config.TRIM_REACH_LONG):
        junction = Junction(nodes=nodes, fans={n: [] for n in nodes})
        for node in nodes:
            for k, end in touching[node]:
                junction.fans[node].append(make_arm(graph, links, k, end, reach))
                if k in internal and k not in junction.internal:
                    junction.internal.append(k)
        junction.centre = graph.nodes[nodes].mean(axis=0)
        for node, fan in junction.fans.items():
            fan.sort(key=lambda arm: np.arctan2(*arm.at(arm.tan, min(6.0, arm.u[-1]))[::-1]))
            junction.corners[node] = trim_fan(fan)
        clear_mouths(junction, graph, links)
        if not any(arm.trim >= reach - 1.0 and links[arm.link].length > reach for arm in junction.arms):
            break
    return junction


def build_junctions(graph, links):
    """Group junction nodes into junctions and trim every arm. Sets `junction`, `trim`, `internal` on the links. Returns the junction list.

    Every junction node starts as its own junction. A link left with nothing to draw between its two trims is swallowed and its
    junctions merge; only the junctions that changed are built again, until nothing changes."""
    parent = {n: n for n in range(len(graph.nodes)) if graph.is_junction(n)}
    members = {n: [n] for n in parent}
    touching = {n: [] for n in parent}
    for k, link in enumerate(links):
        link.internal, link.junction, link.trim = False, [-1, -1], [0.0, 0.0]
        for end in (0, 1):
            if link.nodes[end] in parent:
                touching[link.nodes[end]].append((k, end))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    internal, built, dirty = set(), {}, set(parent)
    while dirty:
        affected = set()
        for root in sorted(dirty):
            junction = built[root] = _make_junction(graph, links, sorted(members[root]), touching, internal)
            for arm in junction.arms:
                links[arm.link].trim[arm.end] = arm.trim
                affected.add(arm.link)
        dirty = set()
        for k in sorted(affected):
            link = links[k]
            loop = link.nodes[0] in parent and link.nodes[1] in parent and find(link.nodes[0]) == find(link.nodes[1])
            least = config.LOOP_MIN_DRAWN if loop else config.LINK_MIN_DRAWN        # a short link from a junction back to itself is part of it
            if link.internal or sum(link.trim) <= link.length - least:
                continue
            internal.add(k)
            link.internal = True
            roots = sorted({find(n) for n in link.nodes if n in parent})
            for other in roots[1:]:
                parent[other] = roots[0]
                members[roots[0]] += members.pop(other)
                built.pop(other, None)
            dirty = {find(r) for r in dirty} | {roots[0]}
    roots = sorted(built)
    index = {root: i for i, root in enumerate(roots)}
    junctions = [built[root] for root in roots]
    for link in links:
        link.junction = [index[find(n)] if n in parent else -1 for n in link.nodes]
    for junction in junctions:
        paved = [not graph.edges[links[arm.link].chain[0][0]].dirt for arm in junction.arms]
        junction.paved = sum(paved) >= min(2, len(paved))
    return junctions


# ---------------------------------------------------------------- surface

def _bezier(p0, control, p1, n):
    t = np.linspace(0.0, 1.0, n + 2)[1:-1, None]
    return (1 - t) ** 2 * p0 + 2 * t * (1 - t) * control + t ** 2 * p1


def _hermite(p0, t0, p1, t1, n):
    """Cubic from p0 (leaving along t0) to p1 (arriving along t1), interior points only."""
    handle = float(np.hypot(*(p1 - p0))) / 3.0
    c0, c1 = p0 + t0 * handle, p1 - t1 * handle
    t = np.linspace(0.0, 1.0, n + 2)[1:-1, None]
    return (1 - t) ** 3 * p0 + 3 * t * (1 - t) ** 2 * c0 + 3 * t ** 2 * (1 - t) * c1 + t ** 3 * p1


def mouth_corners(link, arm):
    """(right corner, left corner) of the mouth of an arm, as seen looking away from the node.
    For a drawn link these are its own sample points, so the junction and the road share them exactly."""
    if arm.swallowed:
        return arm.at(arm.right, arm.trim), arm.at(arm.left, arm.trim)
    i = link.i0 if arm.end == 0 else link.i1
    left, right = link.xy[i] + link.nrm[i] * link.hw[i], link.xy[i] - link.nrm[i] * link.hw[i]
    return (right, left) if arm.end == 0 else (left, right)


def _stub(arm, right, left):
    """Surface of an arm between its node and its mouth (`right`, `left`: the mouth corners)."""
    inside = arm.u < arm.trim - 0.05
    return Polygon(np.vstack([arm.right[inside], right, left, arm.left[inside][::-1]]))


def _corner(a, b, corner, start, stop, node):
    """Surface between arm `a` and its counter-clockwise neighbour `b`, outside their stubs. `start`: left mouth corner of a, `stop`: right mouth corner of b."""
    if corner is None:                                           # the kerbs never cross: everything under one curve from mouth to mouth
        n = int(np.clip(np.hypot(*(stop - start)) / 1.5, 1, 10))
        curve = _hermite(start, -a.at(a.tan, a.trim), stop, b.at(b.tan, b.trim), n)
        inside_a, inside_b = a.u < a.trim - 0.05, b.u < b.trim - 0.05
        return Polygon(np.vstack([a.left[inside_a], start, curve, stop, b.right[inside_b][::-1], node]))
    x, ua, ub, tangent = corner
    fa, fb = min(a.trim, ua + tangent), min(b.trim, ub + tangent)
    p0 = start if fa >= a.trim - 1e-6 else a.at(a.left, fa)
    p1 = stop if fb >= b.trim - 1e-6 else b.at(b.right, fb)
    # the wedge reaches in to both centrelines, so it overlaps the two stubs instead of merely touching their kerbs
    return Polygon(np.vstack([p0, _bezier(p0, x, p1, 5), p1, b.at(b.xy, fb - 0.3), b.at(b.xy, ub), a.at(a.xy, ua), a.at(a.xy, fa - 0.3)]))


def _polygons(geometry):
    """The polygons of any geometry (make_valid and union may also return stray lines and points)."""
    if geometry.geom_type == "Polygon":
        return [geometry] if geometry.area > 1e-6 else []
    return [p for g in getattr(geometry, "geoms", []) for p in _polygons(g)]


def _ribbon(link):
    nrm = left_normals(tangents(link.dense_xy))
    return Polygon(np.vstack([link.dense_xy + nrm * link.dense_hw[:, None], (link.dense_xy - nrm * link.dense_hw[:, None])[::-1]]))


def _pieces(junction, graph, links, mouth_of):
    """The simple shapes whose union is the junction surface: [(arm that owns the piece or None, polygon)]. `mouth_of(arm)`: its mouth corners."""
    pieces = [(None, _ribbon(links[k])) for k in junction.internal]
    for node, fan in junction.fans.items():
        ends = [mouth_of(arm) for arm in fan]
        for i, a in enumerate(fan):
            if not a.swallowed:
                pieces.append((a, _stub(a, *ends[i])))
            if len(fan) > 1:
                j = (i + 1) % len(fan)
                pieces.append((None, _corner(a, fan[j], junction.corners[node][i], ends[i][1], ends[j][0], graph.nodes[node])))
    return pieces


def outline(junction, graph, links):
    """Build the junction surface from the sampled links: polygon, triangles and boundary edges."""
    if not junction.arms:
        junction.valid, junction.problem = False, "no arm"
        return
    pieces = _pieces(junction, graph, links, lambda arm: mouth_corners(links[arm.link], arm))
    mouths = [mouth_corners(links[arm.link], arm) for arm in junction.arms]
    surface = shapely.unary_union([part for _, p in pieces for part in _polygons(shapely.make_valid(p))])
    parts = sorted(_polygons(surface), key=lambda g: g.area, reverse=True)
    if not parts:
        junction.valid, junction.problem = False, "no surface"
        return
    if len(parts) > 1 and parts[1].area >= 0.5:
        junction.valid, junction.problem = False, f"{len(parts)} separate parts"
    surface = parts[0]
    surface = shapely.geometry.polygon.orient(Polygon(surface.exterior, [h for h in surface.interiors if Polygon(h).area >= ISLAND_MIN]), 1.0)
    junction.polygon = surface

    index, vertices, boundary = {}, [], []
    for ring in (surface.exterior, *surface.interiors):
        ids = []
        for p in ring.coords[:-1]:
            if p not in index:
                index[p] = len(vertices)
                vertices.append(p)
            ids.append(index[p])
        boundary += [(ids[k], ids[(k + 1) % len(ids)], 0) for k in range(len(ids))]
    boundary = np.array(boundary)
    for right, left in mouths:                                   # the mouth of every arm must be one edge of the boundary
        edge = (index.get(tuple(right)), index.get(tuple(left)))
        hit = np.nonzero((boundary[:, 0] == edge[0]) & (boundary[:, 1] == edge[1]))[0] if None not in edge else []
        if len(hit) == 1:
            boundary[hit[0], 2] = 1
        else:
            junction.valid, junction.problem = False, "a mouth is not one edge of the outline"
    triangles = []
    for tri in shapely.constrained_delaunay_triangles(surface).geoms:
        triangles.append([index[p] for p in tri.exterior.coords[:3]])
    junction.vertices, junction.boundary, junction.triangles = np.array(vertices), boundary, np.array(triangles)
