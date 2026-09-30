"""Links as geometry: a dense centreline with half widths, then the final samples every ~2 m.

A link runs from node to node. Inside a junction it is a "stub": kept for traffic and for the heights, but not drawn (the junction surface
covers it). `trim[end]` is the length of the stub at each end; the drawn road runs between the two trims, and a sample sits exactly on each.
"""
from dataclasses import dataclass, field

import numpy as np
from scipy.ndimage import uniform_filter1d

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

    @property
    def length(self):
        return float(self.dense_s[-1])

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
