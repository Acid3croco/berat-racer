"""Stage 3: horizontal alignment.

Surveyed centrelines are polylines with a corner at every vertex. Here every through road (stroke) becomes one smooth curve:

    minimise   sum w_i |q_i - p_i|^2  +  lambda * sum |third difference of q|^2

over the points q_i of the curve (p_i: the surveyed line resampled every metre). Penalising the third difference penalises the *change*
of curvature, so the result runs in straights, arcs and spiral-like transitions, which is how roads are laid out, and a car can follow it
with smooth steering. The weights w_i are raised wherever the curve strays further than the class's `max_deviation` from the surveyed
line, until it is back inside the bound: sharp real corners stay corners (rounded only as far as the bound allows).

Strokes are solved from the most important road down. A node first reached by a stroke moves with it; later strokes must pass through it.
Roundabouts are not smoothed: their ring becomes a true circle.

Rebuilds. Every smoothed stroke is kept with a hash of what it was smoothed from (`Kept`); a stroke whose surveyed line, pins, class
limits and end weights are unchanged takes its curve from there instead of being solved again.
"""
import os
import pickle
import sys

import numpy as np
import shapely
from scipy import sparse
from scipy.sparse.linalg import splu

from . import config
from .digest import code_stamp, digest
from .graph import chain_nodes

PIN = 1e7                         # weight of "pass exactly through this point"
FREE_END = 10.0                   # a dead end may move a little with the smoothing


class Kept:
    """Results of the previous build by hash of their inputs, in one file. Only what this build asked for is written back."""

    def __init__(self, path, fresh=False):
        self.path, self.old, self.new, self.reused = path, {}, {}, 0
        if path is not None and path.exists() and not fresh:
            with open(path, "rb") as fh:
                self.old = pickle.load(fh)

    def get(self, key, compute):
        if self.path is None:
            return compute()
        if key in self.old:
            self.reused += 1
            self.new[key] = self.old[key]
        else:
            self.new[key] = compute()
        return self.new[key]

    def save(self):
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        scratch = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        with open(scratch, "wb") as fh:
            pickle.dump(self.new, fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(scratch, self.path)


def fit_circle(xy):
    """Least-squares circle through points: centre, radius, rms radial error."""
    a = np.c_[2.0 * xy, np.ones(len(xy))]
    (cx, cy, c), *_ = np.linalg.lstsq(a, (xy ** 2).sum(axis=1), rcond=None)
    centre = np.array([cx, cy])
    radius = float(np.sqrt(max(c + cx * cx + cy * cy, 0.0)))
    rms = float(np.sqrt(np.mean((np.hypot(*(xy - centre).T) - radius) ** 2)))
    return centre, radius, rms


def round_roundabouts(graph):
    """Turn every closed ring of roundabout edges into a circle. Returns (frozen edge indices, moved node indices)."""
    ring = [k for k, e in enumerate(graph.edges) if e.kind == 1]
    parent = {k: k for k in ring}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    at_node = {}
    for k in ring:
        for node in (graph.edges[k].a, graph.edges[k].b):
            if node in at_node:
                parent[find(k)] = find(at_node[node])
            at_node[node] = k
    groups = {}
    for k in ring:
        groups.setdefault(find(k), []).append(k)

    frozen, moved = set(), set()
    for members in groups.values():
        points = np.vstack([graph.edges[k].xy for k in members])
        centre, radius, rms = fit_circle(points)
        nodes = {n for k in members for n in (graph.edges[k].a, graph.edges[k].b)}
        closed = all(sum(n in (graph.edges[k].a, graph.edges[k].b) for k in members) == 2 for n in nodes)
        if not closed or rms > 1.0 or not 3.0 <= radius <= 80.0:
            continue
        for n in nodes:
            d = graph.nodes[n] - centre
            graph.nodes[n] = centre + d / np.hypot(*d) * radius
        for k in members:
            e = graph.edges[k]
            turn = np.unwrap(np.arctan2(e.xy[:, 1] - centre[1], e.xy[:, 0] - centre[0]))
            n_seg = max(int(np.ceil(abs(turn[-1] - turn[0]) * radius / config.ALIGN_STEP)), 2)
            angles = np.linspace(turn[0], turn[-1], n_seg + 1)
            e.xy = centre + radius * np.c_[np.cos(angles), np.sin(angles)]
            e.xy[0], e.xy[-1] = graph.nodes[e.a], graph.nodes[e.b]
        frozen.update(members)
        moved.update(nodes)
    return frozen, moved


def _third_difference(n):
    return sparse.diags([-1.0, 3.0, -3.0, 1.0], [0, 1, 2, 3], shape=(n - 3, n), format="csr")


def smooth_polyline(raw, step_eps, step_wavelength, pins, end_weights):
    """Smooth curve close to the polyline `raw`.

    step_eps, step_wavelength: functions of arc length along `raw` giving the deviation bound and the smoothing wavelength there.
    pins: [(arc length, xy)] points the curve must pass through. end_weights: fit weight of the first and the last point.
    Returns (arc lengths s of the curve points along `raw`, their positions q, the worst deviation as a fraction of its bound).
    """
    seg = np.hypot(*np.diff(raw, axis=0).T)
    s_raw = np.r_[0.0, np.cumsum(seg)]
    total = s_raw[-1]
    n = max(int(round(total / config.ALIGN_STEP)), 1) + 1
    s = np.linspace(0.0, total, n)
    p = np.c_[np.interp(s, s_raw, raw[:, 0]), np.interp(s, s_raw, raw[:, 1])]
    if n < 5:
        return s, p, 0.0
    h = total / (n - 1)
    eps = step_eps(s)
    stiffness = (step_wavelength(s) / (2.0 * np.pi)) ** 6 / h ** 6
    d3 = _third_difference(n)
    penalty = d3.T @ sparse.diags(0.5 * (stiffness[1:-2] + stiffness[2:-1])) @ d3

    rows, cols, vals, targets = [], [], [], []
    for r, (at, xy) in enumerate(pins):                       # a pin constrains the curve between two samples
        i = min(int(at / h), n - 2)
        t = at / h - i
        rows += [r, r]
        cols += [i, i + 1]
        vals += [1.0 - t, t]
        targets.append(xy)
    pin = sparse.csr_matrix((vals, (rows, cols)), shape=(len(pins), n))
    pin_lhs = PIN * (pin.T @ pin)
    pin_rhs = PIN * (pin.T @ np.array(targets).reshape(-1, 2)) if pins else 0.0

    line = shapely.LineString(raw)
    w = np.ones(n)
    w[0], w[-1] = end_weights
    q = p
    for _ in range(config.ALIGN_ITERATIONS):
        lu = splu((sparse.diags(w) + penalty + pin_lhs).tocsc())
        rhs = w[:, None] * p + pin_rhs
        q = np.c_[lu.solve(rhs[:, 0]), lu.solve(rhs[:, 1])]
        ratio = shapely.distance(shapely.points(q), line) / eps
        if ratio.max() <= 1.02:
            break
        w[1:-1] *= np.clip(ratio[1:-1], 1.0, 3.0) ** 2
    return s, q, float(ratio.max())


PRIORITY_LENGTH = 1600.0          # m: strokes are smoothed by class, then by length up to this (the tiles' halo, roads/tiled.py)
CONTEXT = 150.0                   # m of a neighbour's finished curve a stroke continuing it is pinned to (tiled builds)


def _runs(chain, fixed):
    """[(first, end)] index ranges of the chain's edges that are not fixed."""
    out, start = [], None
    for i, (k, _) in enumerate(chain):
        if k in fixed:
            if start is not None:
                out.append((start, i))
                start = None
        elif start is None:
            start = i
    if start is not None:
        out.append((start, len(chain)))
    return out


def _context(graph, chain, fixed, indices, at_start):
    """The finished curve of the fixed edges next to a run (`indices`, walking away from it), oriented along the chain: up to CONTEXT m."""
    pieces, length = [], 0.0
    for i in indices:
        k, rev = chain[i]
        if k not in fixed:
            break
        xy = fixed[k][::-1] if rev else fixed[k]
        pieces.append(xy)
        length += float(np.hypot(*np.diff(xy, axis=0).T).sum())
        if length >= CONTEXT:
            break
    if not pieces:
        return np.zeros((0, 2))
    if at_start:                                       # walked backwards: put the pieces back in chain order
        xy = np.vstack([pieces[-1]] + [p[1:] for p in pieces[-2::-1]])
        s = np.r_[0.0, np.cumsum(np.hypot(*np.diff(xy, axis=0).T))]
        return xy[s >= s[-1] - CONTEXT]
    xy = np.vstack([pieces[0]] + [p[1:] for p in pieces[1:]])
    s = np.r_[0.0, np.cumsum(np.hypot(*np.diff(xy, axis=0).T))]
    return xy[s <= CONTEXT]


def smooth_strokes(graph, stroke_list, frozen, placed, kept, fixed=None):
    """Smooth every stroke in place (edge polylines and node positions). `placed`: nodes whose position is already final.
    `kept`: the strokes of the previous build (`Kept`). `fixed`: {edge: finished curve} of edges a neighbouring tile already smoothed;
    a stroke is solved in the runs between them, each continuing the fixed curve on either side (pinned over CONTEXT metres)."""
    code = code_stamp(sys.modules[__name__])
    fixed = fixed or {}
    def priority(chain):
        """Class first, then length up to PRIORITY_LENGTH (a tile sees that much of any stroke through it), then the stroke's first
        section id: an order a tile and a whole-area build agree on."""
        return (max(graph.edges[k].road_class.rank for k, _ in chain), min(sum(graph.edges[k].length for k, _ in chain), PRIORITY_LENGTH),
                min(graph.edges[k].cleabs for k, _ in chain))

    def oriented(chain):
        """The stroke walked along its first section (by id): the smoothing is not symmetric, so a tile and a whole-area build must walk it alike."""
        k, rev = min(chain, key=lambda kr: graph.edges[kr[0]].cleabs)
        return [(e, not r) for e, r in reversed(chain)] if rev else chain

    worst = 0.0
    for whole in sorted((oriented(c) for c in stroke_list), key=priority, reverse=True):
        if all(k in frozen or k in fixed for k, _ in whole):
            continue
        for first, end in _runs(whole, fixed):
            chain = whole[first:end]
            before = _context(graph, whole, fixed, range(first - 1, -1, -1), True)
            after = _context(graph, whole, fixed, range(end, len(whole)), False)
            pieces = [graph.edges[k].xy[::-1] if rev else graph.edges[k].xy for k, rev in chain]
            nodes = chain_nodes(graph, chain)
            body = np.vstack([pieces[0]] + [piece[1:] for piece in pieces[1:]])
            body[0], body[-1] = graph.nodes[nodes[0]], graph.nodes[nodes[-1]]
            raw = np.vstack(([before[:-1]] if len(before) else []) + [body] + ([after[1:]] if len(after) else []))
            lead = float(np.hypot(*np.diff(before, axis=0).T).sum()) if len(before) else 0.0
            bounds = lead + np.r_[0.0, np.cumsum([np.hypot(*np.diff(piece, axis=0).T).sum() for piece in pieces])]      # arc length of every node on the run
            classes = [graph.edges[k].road_class for k, _ in chain]

            def per_edge(values, bounds=bounds, n=len(chain)):
                return lambda s: np.asarray(values)[np.clip(np.searchsorted(bounds, s, side="right") - 1, 0, n - 1)]

            pins = [(bounds[i], graph.nodes[nodes[i]].copy()) for i in range(1, len(nodes) - 1) if nodes[i] in placed]
            if len(before):
                arc = np.r_[0.0, np.cumsum(np.hypot(*np.diff(before, axis=0).T))]
                pins = [(float(a), p.copy()) for a, p in zip(arc, before)] + pins
            if len(after):
                arc = bounds[-1] + np.r_[0.0, np.cumsum(np.hypot(*np.diff(after, axis=0).T))]
                pins += [(float(a), p.copy()) for a, p in zip(arc, after)]
            end_weights = [PIN if (side or (graph.degree(node) != 1 or node in placed)) else FREE_END
                           for node, side in ((nodes[0], len(before)), (nodes[-1], len(after)))]
            bound, wavelength = [c.max_deviation for c in classes], [c.align_wavelength for c in classes]
            s, q, ratio = kept.get(digest(raw, bounds, bound, wavelength, pins, end_weights, code),
                                   lambda: smooth_polyline(raw, per_edge(bound), per_edge(wavelength), pins, end_weights))
            worst = max(worst, ratio)

            for i, node in enumerate(nodes):
                if node in placed:
                    continue
                graph.nodes[node] = np.array([np.interp(bounds[i], s, q[:, 0]), np.interp(bounds[i], s, q[:, 1])])
                placed.add(node)
            for i, (k, rev) in enumerate(chain):
                inside = (s > bounds[i] + 0.3) & (s < bounds[i + 1] - 0.3)
                xy = np.vstack([graph.nodes[nodes[i]], q[inside], graph.nodes[nodes[i + 1]]])
                graph.edges[k].xy = xy[::-1].copy() if rev else xy
    return worst


def reattach(graph):
    """After nodes moved: make every edge end sit exactly on its node."""
    for e in graph.edges:
        e.xy[0], e.xy[-1] = graph.nodes[e.a], graph.nodes[e.b]
