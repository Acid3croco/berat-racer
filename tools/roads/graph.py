"""Stage 2: the road network as a graph.

  node     a point where road ends meet (degree 1 = dead end, 2 = a road continues, 3+ = junction)
  edge     one surveyed section between two nodes (`source.Edge`)
  stroke   a through road: a chain of edges that continue each other, straight across junctions. The unit of horizontal smoothing.
  link     the part of a stroke between two junctions (or dead ends). The unit of geometry: it gets samples, edges, a height profile.

An "arm" is one end of an edge, written (edge index, end) with end 0 at `xy[0]` and 1 at `xy[-1]`.
"""
from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import cKDTree

from . import config


@dataclass
class Graph:
    edges: list
    nodes: np.ndarray                             # (m, 2)
    arms: list                                    # per node: [(edge, end), ...]
    through: dict = field(default_factory=dict)   # arm -> the arm that continues it across its node

    def degree(self, node):
        return len(self.arms[node])

    def node_of(self, arm):
        e = self.edges[arm[0]]
        return e.a if arm[1] == 0 else e.b

    def is_junction(self, node):
        """Where links end and a junction surface is built: three arms or more, or two arms that meet at a sharp corner."""
        arms = self.arms[node]
        return len(arms) >= 3 or (len(arms) == 2 and arms[0] not in self.through)


def polyline_length(xy):
    return float(np.hypot(*np.diff(xy, axis=0).T).sum())


def point_along(xy, distance):
    """Point at `distance` metres from xy[0] along the polyline (clamped to its end)."""
    s = np.r_[0.0, np.cumsum(np.hypot(*np.diff(xy, axis=0).T))]
    d = min(max(distance, 0.0), s[-1])
    return np.array([np.interp(d, s, xy[:, 0]), np.interp(d, s, xy[:, 1])])


def _cluster_ends(edges):
    """Union road ends closer than NODE_SNAP into nodes. Returns (node xy, node index of each edge start, of each edge end)."""
    ends = np.array([p for e in edges for p in (e.xy[0], e.xy[-1])])
    parent = np.arange(len(ends))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for a, b in cKDTree(ends).query_pairs(config.NODE_SNAP):
        parent[find(a)] = find(b)
    roots = np.array([find(k) for k in range(len(ends))])
    _, index = np.unique(roots, return_inverse=True)
    nodes = np.zeros((index.max() + 1, 2))
    np.add.at(nodes, index, ends)
    nodes /= np.bincount(index)[:, None]
    return nodes, index[0::2], index[1::2]


def _drop_spurs(edges):
    """Remove dead-end stubs shorter than SPUR_LENGTH, repeatedly (removing one can expose another)."""
    while True:
        _, a, b = _cluster_ends(edges)
        degree = np.bincount(np.r_[a, b])
        keep = [not ((degree[a[k]] == 1) != (degree[b[k]] == 1) and e.length < config.SPUR_LENGTH) for k, e in enumerate(edges)]
        if all(keep):
            return edges
        edges = [e for e, k in zip(edges, keep) if k]


def arm_direction(graph, arm):
    """Unit vector pointing away from the node along the arm, measured ARM_DIRECTION_REACH out."""
    e = graph.edges[arm[0]]
    xy = e.xy if arm[1] == 0 else e.xy[::-1]
    d = point_along(xy, config.ARM_DIRECTION_REACH) - xy[0]
    return d / max(float(np.hypot(*d)), 1e-9)


def _pair_through(graph):
    """At every node decide which arms continue each other. A road never continues from a roundabout ring into one of its arms."""
    through = {}
    for node, arms in enumerate(graph.arms):
        if len(arms) < 2:
            continue
        directions = [arm_direction(graph, arm) for arm in arms]
        candidates = []
        for i in range(len(arms)):
            for j in range(i + 1, len(arms)):
                ei, ej = graph.edges[arms[i][0]], graph.edges[arms[j][0]]
                if arms[i][0] == arms[j][0] or (ei.kind == 1) != (ej.kind == 1):
                    continue
                deflection = 180.0 - np.degrees(np.arccos(np.clip(directions[i] @ directions[j], -1.0, 1.0)))
                limit = config.THROUGH_MAX_DEFLECTION if len(arms) > 2 else config.JOINT_MAX_DEFLECTION
                if deflection > limit and ei.kind != 1:
                    continue
                cost = deflection + 15.0 * abs(ei.road_class.rank - ej.road_class.rank) + (0.0 if ei.number and ei.number == ej.number else 8.0)
                candidates.append((cost, i, j))
        used = set()
        for _, i, j in sorted(candidates):
            if i in used or j in used:
                continue
            used.update((i, j))
            through[arms[i]] = arms[j]
            through[arms[j]] = arms[i]
    return through


def build(edges):
    """Nodes, arms and through pairs of the edges (their end points are snapped onto the nodes)."""
    edges = _drop_spurs(list(edges))
    nodes, a, b = _cluster_ends(edges)
    arms = [[] for _ in range(len(nodes))]
    for k, e in enumerate(edges):
        e.a, e.b = int(a[k]), int(b[k])
        e.xy = e.xy.copy()
        e.xy[0], e.xy[-1] = nodes[e.a], nodes[e.b]
        arms[e.a].append((k, 0))
        arms[e.b].append((k, 1))
    graph = Graph(edges=edges, nodes=nodes, arms=arms)
    graph.through = _pair_through(graph)
    return graph


def _walk(graph, arm, seen, continues):
    """Follow a chain from `arm` (leaving its node) while `continues(far arm)` gives the next arm. Returns [(edge, reversed), ...]."""
    chain = []
    while arm is not None and arm[0] not in seen:
        seen.add(arm[0])
        chain.append((arm[0], arm[1] == 1))
        arm = continues((arm[0], 1 - arm[1]))
    return chain


def _chains(graph, continues):
    seen, out = set(), []
    for k in range(len(graph.edges)):                     # open chains start at an arm nothing continues into
        for end in (0, 1):
            if k not in seen and continues((k, end)) is None:
                out.append(_walk(graph, (k, end), seen, continues))
    for k in range(len(graph.edges)):                     # what is left are closed loops
        if k not in seen:
            out.append(_walk(graph, (k, 0), seen, continues))
    return out


def strokes(graph):
    """Through roads: chains of edges joined by through pairs."""
    return _chains(graph, graph.through.get)


def links(graph):
    """Chains of edges between junctions and dead ends (they run through the degree-2 nodes only)."""
    def continues(arm):
        return None if graph.is_junction(graph.node_of(arm)) else graph.through.get(arm)
    return _chains(graph, continues)


def chain_nodes(graph, chain):
    """Nodes visited by a chain, in order (one more than its edges)."""
    first, rev = chain[0]
    out = [graph.edges[first].b if rev else graph.edges[first].a]
    for k, rev in chain:
        out.append(graph.edges[k].a if rev else graph.edges[k].b)
    return out
