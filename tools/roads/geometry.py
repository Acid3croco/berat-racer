"""Links as geometry: a dense centreline with half widths, then the final samples every ~2 m.

A link runs from node to node. Inside a junction it is a "stub": kept for traffic and for the heights, but not drawn (the junction surface
covers it). `trim[end]` is the length of the stub at each end; the drawn road runs between the two trims, and a sample sits exactly on each.
"""
from dataclasses import dataclass, field

import numpy as np
from scipy.ndimage import minimum_filter1d, uniform_filter1d
from scipy.spatial import cKDTree

from . import config


def arc_lengths(xy):
    return np.r_[0.0, np.cumsum(np.hypot(*np.diff(xy, axis=0).T))]


def resample(xy, step):
    """Points every ~`step` metres along a polyline, both ends kept. Returns (arc lengths, points)."""
    s = arc_lengths(xy)
    n = max(int(round(s[-1] / step)), 1)
    t = np.linspace(0.0, s[-1], n + 1)
    return t, np.c_[np.interp(t, s, xy[:, 0]), np.interp(t, s, xy[:, 1])]


def tangents(xy):
    """Unit tangents of a polyline (central differences)."""
    d = np.gradient(xy, axis=0)
    return d / np.maximum(np.hypot(d[:, 0], d[:, 1]), 1e-9)[:, None]


def left_normals(tan):
    return np.c_[-tan[:, 1], tan[:, 0]]


@dataclass
class Link:
    chain: list                           # [(edge index, reversed), ...] from node `nodes[0]` to node `nodes[1]`
    nodes: tuple
    # dense centreline (ALIGN_STEP)
    dense_s: np.ndarray
    dense_xy: np.ndarray
    dense_hw: np.ndarray
    dense_part: np.ndarray                # index into `chain` of the edge each dense point lies on
    # junction at each end (-1: none) and the stub length there
    junction: list = field(default_factory=lambda: [-1, -1])
    trim: list = field(default_factory=lambda: [0.0, 0.0])
    internal: bool = False                # wholly inside one junction
    # final samples (filled by `sample`)
    s: np.ndarray = None
    xy: np.ndarray = None
    tan: np.ndarray = None
    nrm: np.ndarray = None                # left normal
    hw: np.ndarray = None
    part: np.ndarray = None
    i0: int = 0                           # segments i0 <= k < i1 are drawn
    i1: int = 0
    z: np.ndarray = None                  # centreline height
    ground: np.ndarray = None             # LiDAR ground under the centreline (debug)
    tilt: np.ndarray = None               # cross slope: the left edge is tilt * hw above the centre
    bridge: np.ndarray = None             # per segment: carried over what lies below (see crossing.py)
    tunnel: np.ndarray = None             # per segment

    @property
    def length(self):
        return float(self.dense_s[-1])

    def slim(self):
        """Drop the dense working centreline (kept: its two ends, for the length): a finished link is its samples."""
        ends = [0, len(self.dense_s) - 1]
        self.dense_s, self.dense_xy, self.dense_hw, self.dense_part = self.dense_s[ends], self.dense_xy[ends], self.dense_hw[ends], self.dense_part[ends]

    def left(self):
        return np.c_[self.xy + self.nrm * self.hw[:, None], self.z + self.tilt * self.hw]

    def right(self):
        return np.c_[self.xy - self.nrm * self.hw[:, None], self.z - self.tilt * self.hw]


def make_link(graph, chain, nodes):
    pieces = [graph.edges[k].xy[::-1] if rev else graph.edges[k].xy for k, rev in chain]
    raw = np.vstack([pieces[0]] + [piece[1:] for piece in pieces[1:]])
    bounds = np.cumsum([arc_lengths(piece)[-1] for piece in pieces])
    s, xy = resample(raw, config.ALIGN_STEP)
    part = np.clip(np.searchsorted(bounds, s, side="left"), 0, len(chain) - 1)
    hw = np.array([graph.edges[k].width / 2.0 for k, _ in chain])[part]
    if len(chain) > 1:                                              # spread width changes between sections
        size = max(int(config.WIDTH_TAPER / 2.0 / config.ALIGN_STEP), 1) | 1
        for _ in range(2):
            hw = uniform_filter1d(hw, size, mode="nearest")
    return Link(chain=chain, nodes=nodes, dense_s=s, dense_xy=xy, dense_hw=hw, dense_part=part)


def clamp_widths(links):
    """Narrow roads that run side by side closer than their widths allow (the two carriageways of a motorway, a slip road along it,
    a street and its service lane): each keeps its share of the space between the two centrelines. Roads that meet at a node are
    left alone near it: there the junction stage decides. Returns the number of links narrowed."""
    every = config.CLAMP_EVERY
    points, owner, index, hw, tan, arc = [], [], [], [], [], []
    for k, link in enumerate(links):
        at = np.arange(0, len(link.dense_s), every)
        points.append(link.dense_xy[at])
        owner.append(np.full(len(at), k))
        index.append(at)
        hw.append(link.dense_hw[at])
        tan.append(tangents(link.dense_xy)[at])
        arc.append(np.c_[link.dense_s[at], link.length - link.dense_s[at]])          # distance to each end of the link
    points, owner, index, hw, tan, arc = (np.concatenate(a) for a in (points, owner, index, hw, tan, arc))
    nodes = np.array([link.nodes for link in links])
    tree = cKDTree(points)
    reach = config.CLAMP_NEAR_NODE
    limit = np.full(len(points), np.inf)
    for start in range(0, len(points), 200000):
        i = np.arange(start, min(start + 200000, len(points)))
        dist, j = tree.query(points[i], k=16, distance_upper_bound=2.0 * hw.max() + config.CLAMP_GAP)
        i = np.repeat(i, 16)
        dist, j = dist.ravel(), j.ravel()
        ok = np.isfinite(dist)
        i, j, dist = i[ok], j[ok], dist[ok]
        ok = (owner[i] != owner[j]) & (dist < hw[i] + hw[j] + config.CLAMP_GAP) & (np.abs((tan[i] * tan[j]).sum(axis=1)) > config.CLAMP_PARALLEL)
        i, j, dist = i[ok], j[ok], dist[ok]
        near_shared_node = np.zeros(len(i), bool)
        for ea in (0, 1):
            for eb in (0, 1):
                near_shared_node |= (nodes[owner[i], ea] == nodes[owner[j], eb]) & (arc[i, ea] < reach) & (arc[j, eb] < reach)
        i, j, dist = i[~near_shared_node], j[~near_shared_node], dist[~near_shared_node]
        share = dist * hw[i] / (hw[i] + hw[j]) - 0.5 * config.CLAMP_GAP
        np.minimum.at(limit, i, np.maximum(share, config.CLAMP_MIN_HALF_WIDTH))
    narrowed = 0
    start = np.searchsorted(owner, np.arange(len(links) + 1))                     # the points of a link are consecutive (appended link by link)
    for k in np.unique(owner[np.isfinite(limit)]):
        link, mine = links[k], slice(start[k], start[k + 1])
        cap = np.interp(np.arange(len(link.dense_s)), index[mine], np.minimum(limit[mine], 1e3))
        size = max(int(config.WIDTH_TAPER / config.ALIGN_STEP), 1) | 1
        cap = uniform_filter1d(minimum_filter1d(cap, size, mode="nearest"), size, mode="nearest")     # never wider than the tightest spot nearby, eased in and out
        link.dense_hw = np.minimum(link.dense_hw, cap)
        narrowed += 1
    return narrowed


def sample(link):
    """Final samples: one on each trim, at most SAMPLE_STEP apart in between and inside the stubs."""
    total, (t0, t1) = link.length, link.trim

    dense_tan = tangents(link.dense_xy)
    cross = dense_tan[:-1, 0] * dense_tan[1:, 1] - dense_tan[:-1, 1] * dense_tan[1:, 0]
    turn = np.abs(np.arcsin(np.clip(cross, -1.0, 1.0))) / np.maximum(np.diff(link.dense_s), 1e-6)       # radians per metre
    step = float(np.clip(config.SAMPLE_TURN / max(turn.max(initial=0.0), 1e-6), config.SAMPLE_STEP_MIN, config.SAMPLE_STEP))      # tight curves get more samples

    def span(a, b):
        n = max(int(round((b - a) / step)), 1)
        return np.linspace(a, b, n + 1)

    if link.internal:
        s = span(0.0, total)
        link.i0 = link.i1 = 0
    else:
        head = span(0.0, t0)[:-1] if t0 > 0 else np.zeros(0)
        body = span(t0, total - t1)
        tail = span(total - t1, total)[1:] if t1 > 0 else np.zeros(0)
        s = np.r_[head, body, tail]
        link.i0, link.i1 = len(head), len(head) + len(body) - 1
    link.s = s
    link.xy = np.c_[np.interp(s, link.dense_s, link.dense_xy[:, 0]), np.interp(s, link.dense_s, link.dense_xy[:, 1])]
    tan = np.c_[np.interp(s, link.dense_s, dense_tan[:, 0]), np.interp(s, link.dense_s, dense_tan[:, 1])]
    link.tan = tan / np.maximum(np.hypot(tan[:, 0], tan[:, 1]), 1e-9)[:, None]
    link.nrm = left_normals(link.tan)
    link.hw = np.interp(s, link.dense_s, link.dense_hw)
    link.part = link.dense_part[np.clip(np.searchsorted(link.dense_s, 0.5 * (s[:-1] + s[1:])), 0, len(link.dense_s) - 1)]      # per segment
    return link
