"""Stage 7: the finished road surface, in the shapes its consumers need.

  pieces      one per surveyed section of a link (two or three where a bridge span starts or ends in it): centreline, both edges (with heights), what is drawn. The game's road records.
  junctions   vertices with heights + triangles + boundary edges. The game's junction meshes.
  footprints  plan polygons of everything that lies on the ground (bridges and tunnels excluded): the terrain is shaped around them.
  cloud       points of the ground-level surface with its local gradient (its tangent plane): the height of the road at any place nearby.

Heights are `z`; plan coordinates are (x east, north).
"""
from dataclasses import dataclass

import numpy as np
from shapely.geometry import Polygon

from .geometry import arc_lengths

CLOUD_STEP = 0.5                  # spacing of the cloud points along a centreline


@dataclass
class Piece:
    link: int
    edge: object                  # source.Edge
    oneway: int                   # relative to the piece's own direction
    limits: tuple                 # (along, against) the piece's own direction, km/h
    s: np.ndarray                 # (n,) distance along the link
    xy: np.ndarray                # (n, 2) centreline
    z: np.ndarray                 # (n,)
    tan: np.ndarray               # (n, 2)
    hw: np.ndarray                # (n,)
    tilt: np.ndarray              # (n,)
    left: np.ndarray              # (n, 3): x, north, z
    right: np.ndarray
    drawn: np.ndarray             # (n - 1,) segments that are rendered (the others lie inside a junction)
    give_way: list                # [(point index, drawn part lies after it)]: a give-way line is painted across the lane arriving there
    bridge: bool                  # the piece is carried over what lies below (a span of a section may be, see crossing.py)
    tunnel: bool = False          # the piece runs under the ground: not part of the terrain's inputs
    lanes: np.ndarray = None      # (n, 2) lanes along and against the piece (lanes.py)
    line_offsets: np.ndarray = None   # (n, K) lane lines, metres left of the centreline, nan where not painted
    line_kinds: np.ndarray = None     # (K,) lanes.CENTRE / DIVIDER
    marked: np.ndarray = None         # (n - 1,) lines are painted
    no_overtaking: np.ndarray = None  # (n - 1, 2) per segment, along and against
    edge_style: np.ndarray = None     # (n - 1,) lanes.EDGE_*
    arrows: list = None               # [(x, north, z, dx, dnorth, bits)]

    def polygon(self):
        """Plan outline of the drawn part (None when nothing is drawn). Drawn segments of a piece are always contiguous."""
        k = np.flatnonzero(self.drawn)
        if not len(k):
            return None
        span = slice(k[0], k[-1] + 2)
        return Polygon(np.vstack([self.left[span, :2], self.right[span, :2][::-1]]))


def _give_way_points(network, link):
    """Sample indices of a link where it meets a junction as a minor road: [(index, drawn part lies after it)].
    Minor = paved and of lower rank than the best arm of a junction of three arms or more (equal ranks: priority to the right, no line)."""
    out = []
    if link.internal:
        return out
    for end, index in ((0, link.i0), (1, link.i1)):
        if link.junction[end] < 0:
            continue
        junction = network.junctions[link.junction[end]]
        arms = junction.arms
        edge = network.edges[link.chain[0 if end == 0 else -1][0]]
        best = max(network.edges[network.links[a.link].chain[0 if a.end == 0 else -1][0]].road_class.rank for a in arms)
        if len(arms) >= 3 and junction.paved and not edge.dirt and edge.road_class.rank < best:
            out.append((index, end == 0))
    return out


def pieces(network):
    out = []
    for k, link in enumerate(network.links):
        left, right = link.left(), link.right()
        drawn = (np.arange(len(link.s) - 1) >= link.i0) & (np.arange(len(link.s) - 1) < link.i1)
        lines = _give_way_points(network, link)
        for p, (e, rev) in enumerate(link.chain):
            seg = np.flatnonzero(link.part == p)
            if not len(seg):
                continue
            edge = network.edges[e]
            limits = (edge.limit, edge.limit_back or edge.limit)
            level = link.bridge[seg].astype(int) + 2 * link.tunnel[seg].astype(int)
            for run in np.split(seg, np.flatnonzero(np.diff(level)) + 1):                      # one piece per section, split where a bridge or tunnel starts or ends
                first, last = run[0], run[-1] + 1                  # first and last sample of the piece
                span = slice(first, last + 1)
                # a line belongs to the piece that holds the drawn segment next to it
                give_way = [(i - first, after) for i, after in lines if (first <= i < last if after else first < i <= last)]
                out.append(Piece(link=k, edge=edge, oneway={1: 2, 2: 1}.get(edge.oneway, 0) if rev else edge.oneway,
                                 limits=limits[::-1] if rev else limits,
                                 s=link.s[span], xy=link.xy[span], z=link.z[span], tan=link.tan[span], hw=link.hw[span], tilt=link.tilt[span],
                                 left=left[span], right=right[span], drawn=drawn[first:last], give_way=give_way, bridge=bool(link.bridge[first]), tunnel=bool(link.tunnel[first]),
                                 lanes=link.lanes[span], line_offsets=link.line_offsets[span], line_kinds=link.line_kinds,
                                 marked=link.marked[first:last], no_overtaking=link.no_overtaking[first:last], edge_style=link.edge_style[first:last],
                                 arrows=[a[1:] for a in link.arrows if link.s[first] <= a[0] < link.s[last]]))
    return out


def junction_meshes(network):
    """[(junction, vertices (n, 3))]: plan vertices with plane heights. A mouth vertex takes the exact height of the link edge it coincides with."""
    exact = {}
    for link in network.links:
        if link.internal:
            continue
        left, right = link.left(), link.right()
        for i in (link.i0, link.i1):
            for p in (left[i], right[i]):
                exact[(p[0], p[1])] = p[2]
    out = []
    for junction in network.junctions:
        if junction.vertices is None or junction.plane is None:
            continue
        z = junction.height(junction.vertices)
        for i, v in enumerate(junction.vertices):
            z[i] = exact.get((v[0], v[1]), z[i])
        out.append((junction, np.c_[junction.vertices, z]))
    return out


def cloud(piece_list, meshes):
    """Points of the ground-level road surface: dict of xy (n, 2), z, grad (n, 2), tan (n, 2: direction of the road),
    hw (half width of the road across the point, 0 in junctions)."""
    xy, z, grad, tan_, hw = [], [], [], [], []
    for piece in piece_list:
        if piece.bridge or piece.tunnel:
            continue
        s = arc_lengths(piece.xy)
        t = np.arange(0.0, s[-1] + CLOUD_STEP, CLOUD_STEP).clip(max=s[-1])

        def lerp(values):
            return np.interp(t, s, values)

        tan = np.c_[lerp(piece.tan[:, 0]), lerp(piece.tan[:, 1])]
        grade = lerp(np.gradient(piece.z, s)) if len(s) > 2 else np.full(len(t), (piece.z[-1] - piece.z[0]) / max(s[-1], 1e-6))
        xy.append(np.c_[lerp(piece.xy[:, 0]), lerp(piece.xy[:, 1])])
        z.append(lerp(piece.z))
        grad.append(tan * grade[:, None] + np.c_[-tan[:, 1], tan[:, 0]] * lerp(piece.tilt)[:, None])
        tan_.append(tan)
        hw.append(lerp(piece.hw))
    for junction, vertices in meshes:
        points = np.vstack([vertices[:, :2], vertices[junction.triangles][:, :, :2].mean(axis=1)])
        xy.append(points)
        z.append(junction.height(points))
        grad.append(np.tile(junction.plane[1:], (len(points), 1)))
        tan_.append(np.tile([1.0, 0.0], (len(points), 1)))
        hw.append(np.zeros(len(points)))
    if not xy:
        return dict(xy=np.zeros((0, 2)), z=np.zeros(0), grad=np.zeros((0, 2)), tan=np.zeros((0, 2)), hw=np.zeros(0))
    return dict(xy=np.vstack(xy), z=np.concatenate(z), grad=np.vstack(grad), tan=np.vstack(tan_), hw=np.concatenate(hw))


def thin(points, every):
    """Every `every`-th point of a cloud (the coarse terrain needs fewer)."""
    return {key: values[::every] for key, values in points.items()}


def footprints(piece_list, meshes):
    """Plan polygons of the ground-level road surface."""
    out = []
    for piece in piece_list:
        polygon = None if piece.bridge or piece.tunnel else piece.polygon()
        if polygon is not None:
            out.append(polygon if polygon.is_valid else polygon.buffer(0))
    out += [junction.polygon for junction, _ in meshes]
    return out


def junction_triangles(junction, vertices):
    """Triangles of a junction mesh wound clockwise seen from above (the game's front face)."""
    a, b, c = (vertices[junction.triangles[:, k], :2] for k in range(3))
    ccw = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]) > 0
    out = junction.triangles.copy()
    out[ccw] = out[ccw][:, [0, 2, 1]]
    return out


def touches(lo, hi, box):
    """Does the rectangle lo .. hi touch `box` = (x0, z0, x1, z1)?"""
    return hi[0] >= box[0] and lo[0] <= box[2] and hi[1] >= box[1] and lo[1] <= box[3]
