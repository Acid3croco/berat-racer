"""Stage 5: vertical alignment of the whole network.

Unknowns: the height of every link sample outside the junctions, and a plane (height at the centre, two slopes) per junction.
Link samples inside a junction lie on its plane by construction, so every road through a junction shares its height and its slope there.

    minimise    sum w_i (z_i - ground_i)^2                 stay on the LiDAR ground (robust: outliers are down-weighted and the solve repeated)
              + lambda * sum (third derivative of z)^2     grade changes as parabolas (zero third derivative), the curve of road design
              + tilt penalty                               a junction plane should not tilt across its important arms
    subject to  |grade| <= max_grade                       per road class
                -1 / crest_radius <= z'' <= 1 / sag_radius

Bridges aim at their deck (surface model) instead of the ground below; tunnels have no target and are carried by their two ends.

Tiles. The area is cut into square tiles (PROFILE_TILE). A sample belongs to the tile it lies in, a junction (with the road stubs on
its plane) to the tile of its centre. Tiles are solved in four rounds, like the four colours of a 2 x 2 checkerboard, so the tiles of
one round never touch and run in parallel. A tile is solved together with a halo (PROFILE_HALO) of its neighbours:

  - what a neighbour has already solved is fixed: the tile's roads continue it smoothly (the smoothness and curvature terms reach
    across the border, the fixed heights are their boundary condition);
  - what no neighbour has solved yet is solved along and thrown away: it only makes the tile aware of what lies beyond its border.

Only the tile's own samples and junctions are kept. A road crossing a border is therefore one continuous profile: each side was solved
with the other side either fixed or present. The influence of a height fades within a few smoothing wavelengths (tens of metres),
far less than the halo, so the result is close to what one solve of the whole area would give.

Rebuilds. The result of every tile is kept with a hash of what it was solved from (`block_key`). A tile whose samples, fixed
neighbours, terrain files, tuning and solver code are all unchanged is not solved again. A change therefore costs the tiles it
touches, and those around them that were solved after them.
"""
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed

import clarabel
import numpy as np
from scipy import sparse
from scipy.ndimage import median_filter, uniform_filter1d

import rasters
from rasters import BIG, Mosaic

from . import config
from .digest import code_stamp, digest

ACROSS = (-0.7, -0.35, 0.0, 0.35, 0.7)            # ground is sampled at these fractions of the half width, the median is kept
SOLVER = dict(verbose=False, max_threads=1)      # tiles already run side by side, one core each
SOLVED = {"Solved": "solved", "AlmostSolved": "solved inaccurate"}           # Clarabel statuses whose iterate is a usable profile


# ---------------------------------------------------------------- the network as flat arrays

class Samples:
    """Every link sample of the network in flat arrays (links one after the other), and what the solver needs to know about each."""

    def __init__(self, network):
        links, edges = network.links, network.edges
        self.offsets = np.r_[0, np.cumsum([len(link.s) for link in links])]
        n = self.offsets[-1]
        self.link = np.repeat(np.arange(len(links)), np.diff(self.offsets))
        self.s = np.concatenate([link.s for link in links])
        self.xy = np.vstack([link.xy for link in links])
        self.nrm = np.vstack([link.nrm for link in links])
        self.hw = np.concatenate([link.hw for link in links])
        self.plane = np.full(n, -1)                       # junction whose plane the sample lies on
        self.curve_ok = np.zeros(n, bool)                 # a curvature limit applies at the sample (from mouth to mouth)
        self.seg = {key: np.zeros(n) for key in ("max_grade", "crest", "sag", "wavelength")}       # of the segment after each sample
        self.seg_bridge, self.seg_tunnel = np.zeros(n, bool), np.zeros(n, bool)
        for k, link in enumerate(links):
            o, m = self.offsets[k], len(link.s)
            if link.internal:
                self.plane[o:o + m] = max(link.junction)
            else:
                if link.junction[0] >= 0:
                    self.plane[o:o + link.i0 + 1] = link.junction[0]
                if link.junction[1] >= 0:
                    self.plane[o + link.i1:o + m] = link.junction[1]
                self.curve_ok[o + max(link.i0, 1):o + min(link.i1, m - 2) + 1] = True
            for p in np.unique(link.part):
                edge = edges[link.chain[p][0]]
                c = edge.road_class
                at = o + np.flatnonzero(link.part == p)
                self.seg["max_grade"][at], self.seg["crest"][at], self.seg["sag"][at] = c.max_grade, c.crest_radius, c.sag_radius
                self.seg["wavelength"][at] = c.profile_wavelength
                self.seg_bridge[at], self.seg_tunnel[at] = edge.bridge, edge.tunnel
        self.centres = np.array([j.centre for j in network.junctions]).reshape(-1, 2)
        self.tilt_rows = [_tilt_rows(network, j) for j in network.junctions]
        # ownership: a sample belongs to the tile it lies in, a junction and the samples on its plane to the tile of its centre
        self.junction_tile = np.floor(self.centres / config.PROFILE_TILE).astype(int)
        self.tile = np.floor(self.xy / config.PROFILE_TILE).astype(int)
        planar = self.plane >= 0
        self.tile[planar] = self.junction_tile[self.plane[planar]]


def _tilt_rows(network, junction):
    """[(unit vector across the arm, weight)]: the junction plane resists tilting across its arms, most of all across the most important one."""
    arms = junction.arms
    if not arms:
        return []
    ranks = np.array([network.edges[network.links[a.link].chain[0 if a.end == 0 else -1][0]].road_class.rank for a in arms], float)
    return [(np.array([-arm.tan[0][1], arm.tan[0][0]]), np.sqrt(config.TILT_STIFFNESS) * (rank / ranks.max()) ** 2) for arm, rank in zip(arms, ranks)]


def make_block(samples, tile, known_z, known_plane):
    """Everything one tile solve needs, as plain arrays: the samples of the tile and of its halo, and what is already fixed among them."""
    lo = np.array(tile) * config.PROFILE_TILE - config.PROFILE_HALO
    hi = (np.array(tile) + 1) * config.PROFILE_TILE + config.PROFILE_HALO
    junctions = np.flatnonzero(((samples.centres >= lo) & (samples.centres < hi)).all(axis=1)) if len(samples.centres) else np.zeros(0, int)
    local = np.full(len(samples.centres), -1)
    local[junctions] = np.arange(len(junctions))
    inside = ((samples.xy >= lo) & (samples.xy < hi)).all(axis=1)
    planar = samples.plane >= 0
    take = inside & ~planar
    take[planar] = local[samples.plane[planar]] >= 0                                  # a stub goes with its junction
    ids = np.flatnonzero(take)
    breaks = np.flatnonzero((np.diff(ids) != 1) | (np.diff(samples.link[ids]) != 0)) + 1      # runs: consecutive samples of one link
    plane = samples.plane[ids]
    return dict(
        tile=tuple(int(v) for v in tile), ids=ids, runs=np.r_[0, breaks, len(ids)],
        s=samples.s[ids], xy=samples.xy[ids], nrm=samples.nrm[ids], hw=samples.hw[ids],
        plane=np.where(plane >= 0, local[np.maximum(plane, 0)], -1),
        curve_ok=samples.curve_ok[ids], seg={key: values[ids] for key, values in samples.seg.items()},
        seg_bridge=samples.seg_bridge[ids], seg_tunnel=samples.seg_tunnel[ids],
        known_z=known_z[ids], own=(samples.tile[ids] == np.array(tile)).all(axis=1),
        junctions=junctions, centres=samples.centres[junctions], known_plane=known_plane[junctions],
        tilt_rows=[samples.tilt_rows[j] for j in junctions])


# ---------------------------------------------------------------- one tile

def _stretches(mask, run_of):
    """[(first, last)] of every stretch of True that lies within one run."""
    label = np.where(mask, run_of, -1)
    edges = np.r_[0, np.flatnonzero(np.diff(label)) + 1, len(label)]
    return [(a, b - 1) for a, b in zip(edges[:-1], edges[1:]) if label[a] >= 0]


def targets(block, terrain):
    """Ground, target height and weight per sample: the ground; on bridges the deck; nothing in tunnels."""
    s, xy, runs = block["s"], block["xy"], block["runs"]
    across = [terrain.sample(terrain.mnt, *(xy + block["nrm"] * (o * block["hw"])[:, None]).T, 2) for o in ACROSS]
    ground = np.median(across, axis=0)
    target, weight = ground.copy(), np.ones(len(ground))
    last = np.zeros(len(s), bool)
    last[runs[1:] - 1] = True                                       # the last sample of a run has no segment after it
    run_of = np.repeat(np.arange(len(runs) - 1), np.diff(runs))

    def on(seg):                                                    # a sample is on a bridge when a segment next to it is
        after = seg & ~last
        return after | np.r_[False, after[:-1]]

    weight[on(block["seg_tunnel"])] = 0.0
    for a, b in _stretches(on(block["seg_bridge"]), run_of):
        at = xy[a:b + 1]
        surface = terrain.sample(terrain.mnt, *at.T, 2) + np.clip(terrain.sample(terrain.mnh, *at.T, 2), 0.0, config.BRIDGE_DECK_MAX_ABOVE)
        lo, hi = max(a - 1, runs[run_of[a]]), min(b + 1, runs[run_of[a] + 1] - 1)
        chord = np.interp(s[a:b + 1], [s[lo], s[hi]], [ground[lo], ground[hi]])           # abutment to abutment
        deck = median_filter(surface, size=min(7, b - a + 1), mode="nearest")
        plausible = (deck - chord >= -1.0) & (deck - chord <= 4.5)
        target[a:b + 1] = np.where(plausible, deck, chord)
    return ground.astype(float), target.astype(float), weight


def operators(s, runs):
    """Grade, curvature and third derivative of heights sampled at arc lengths `s`, for every run at once: rows never reach across two runs.

    Returns ((matrix, first) for each of the three): sparse rows x samples, and per row the index of its first sample (a grade row
    spans samples first, first + 1; a curvature row is centred on first + 1; a third-derivative row spans first .. first + 3)."""
    n = len(s)
    run_of = np.repeat(np.arange(len(runs) - 1), np.diff(runs))
    first = [np.flatnonzero(run_of[:n - k] == run_of[k:]) for k in (1, 2, 3)]
    inv = np.zeros(max(n - 1, 0))
    step = np.diff(s)
    inv[first[0]] = 1.0 / step[first[0]]

    def matrix(i, columns):
        rows = np.repeat(np.arange(len(i)), len(columns))
        cols = (i[:, None] + np.arange(len(columns))).ravel()
        return sparse.csr_matrix((np.column_stack(columns).ravel(), (rows, cols)), shape=(len(i), n))

    i = first[0]
    d1 = matrix(i, [-inv[i], inv[i]])
    c = np.zeros((3, max(n - 2, 0)))                                                     # curvature coefficients, by first sample
    i = first[1]
    scale = 1.0 / (0.5 * (step[i] + step[i + 1]))
    c[:, i] = scale * inv[i], scale * (-inv[i] - inv[i + 1]), scale * inv[i + 1]
    d2 = matrix(i, list(c[:, i]))
    i = first[2]
    d3 = matrix(i, [inv[i + 1] * v for v in (-c[0, i], c[0, i + 1] - c[1, i], c[1, i + 1] - c[2, i], c[2, i + 1])])
    return (d1, first[0]), (d2, first[1]), (d3, first[2])


def solve_qp(p, q, a, lower, upper):
    """Minimise 0.5 x'Px + q'x subject to lower <= Ax <= upper (`p`: upper triangle). Returns (x, status); x is None without a usable solution."""
    settings = clarabel.DefaultSettings()
    for name, value in SOLVER.items():
        setattr(settings, name, value)
    both_sides = sparse.vstack([a, -a], format="csc")                  # Clarabel's form: [A; -A] x + s = [u; -l], s >= 0
    result = clarabel.DefaultSolver(p, q, both_sides, np.r_[upper, -lower], [clarabel.NonnegativeConeT(both_sides.shape[0])], settings).solve()
    x, status = np.asarray(result.x), SOLVED.get(str(result.status))
    if status is None or not np.isfinite(x).all():
        return None, str(result.status)
    return x, status


def _window(xy):
    """(x0, z0, width, height) of the terrain a block needs: its samples and 40 m around, on the 4 m grid."""
    x0, z0 = np.floor((xy.min(axis=0) - 40.0) / 4.0) * 4.0
    x1, z1 = np.ceil((xy.max(axis=0) + 40.0) / 4.0) * 4.0
    return x0, z0, x1 - x0, z1 - z0


def block_key(block):
    """Hash of everything the solve of a block depends on. The positions of its samples in the network (`ids`, `junctions`) are
    left out: the same roads solve to the same heights wherever they sit in the arrays."""
    data = {name: value for name, value in block.items() if name not in ("ids", "junctions")}
    terrain = rasters.stamp(*_window(block["xy"]), kinds=("mnt", "mnh")) if len(block["ids"]) else []
    return digest(data, terrain, code_stamp(sys.modules[__name__], rasters), SOLVER, ACROSS)


def cache_file(tag, tile):
    return BIG / "roads" / f"{tag}.cache" / "profile" / f"tile_{tile[0]}_{tile[1]}.npz"


def solve_block_cached(block, path):
    """`solve_block`, or its stored result when the block hashes as it did when `path` was written."""
    key = block_key(block)
    if path.exists():
        with np.load(path) as stored:
            if str(stored["key"]) == key:
                return dict(tile=block["tile"], z=stored["z"], planes=stored["planes"], ground=stored["ground"], status=str(stored["status"]), reused=True)
    result = solve_block(block)
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(f"{path.stem}.{os.getpid()}.tmp.npz")
    np.savez(scratch, key=key, z=result["z"], planes=result["planes"], ground=result["ground"], status=result["status"])
    os.replace(scratch, path)
    return result


def solve_block(block):
    """Solve one tile with its halo. Returns dict(z per block sample, plane per block junction, ground, status)."""
    n, xy = len(block["ids"]), block["xy"]
    if n == 0:
        return dict(tile=block["tile"], z=np.zeros(0), planes=block["known_plane"], ground=np.zeros(0), status="empty")
    terrain = Mosaic(*_window(xy), kinds=("mnt", "mnh"))

    # ---- targets, smoothness, limits
    s, runs, seg = block["s"], block["runs"], block["seg"]
    ground, target, weight = targets(block, terrain)
    (grade, seg_at), (curvature, mid_at), (third, jerk_at) = operators(s, runs)
    step, levelled = np.full(n, config.SAMPLE_STEP), target.copy()
    window = max(int(config.GRADE_GROUND_WINDOW / config.SAMPLE_STEP), 3)                   # the road may be as steep as the ground it lies on
    for a, b in zip(runs[:-1], runs[1:]):
        if b - a >= 2:
            step[a:b] = np.gradient(s[a:b])
            levelled[a:b] = uniform_filter1d(target[a:b], window, mode="nearest")
    grade_max = np.clip(config.GRADE_FOLLOWS_GROUND * np.abs(grade @ levelled), seg["max_grade"][seg_at], config.GRADE_ABSOLUTE_MAX)
    limited = block["curve_ok"][mid_at + 1]                                                 # curvature needs a sample on each side
    curve, curve_lo, curve_hi = curvature[limited], -1.0 / seg["crest"][mid_at[limited]], 1.0 / seg["sag"][mid_at[limited]]
    stiffness = (seg["wavelength"][jerk_at + 1] / (2.0 * np.pi)) ** 6 * np.diff(s)[jerk_at + 1]
    smooth_z = third.multiply(np.sqrt(stiffness)[:, None]).tocsr()

    # ---- unknowns: free samples off the junction planes, and the planes of the junctions not fixed yet
    plane, known_z = block["plane"], block["known_z"]
    datum = float(np.median(ground))
    fixed = ~np.isnan(known_z)
    free = np.flatnonzero(~fixed & (plane < 0))
    open_junction = np.isnan(block["known_plane"][:, 0])
    column = np.full(len(open_junction), -1)
    column[open_junction] = len(free) + 3 * np.arange(open_junction.sum())
    n_x = len(free) + 3 * int(open_junction.sum())
    planar = np.flatnonzero(~fixed & (plane >= 0))
    base = np.where(fixed, known_z - datum, 0.0)                                       # z - datum = expand @ x + base
    if n_x == 0:
        return dict(tile=block["tile"], z=base + datum, planes=block["known_plane"], ground=ground, status="fixed")
    d = xy[planar] - block["centres"][plane[planar]]
    col = column[plane[planar]]
    expand = sparse.csr_matrix(
        (np.r_[np.ones(len(free)), np.ones(len(planar)), d[:, 0], d[:, 1]], (np.r_[free, planar, planar, planar], np.r_[np.arange(len(free)), col, col + 1, col + 2])),
        shape=(n, n_x))

    tilts = [(column[j], scale * across) for j, rows in enumerate(block["tilt_rows"]) if column[j] >= 0 for across, scale in rows]
    tilt_col = np.array([c for c, _ in tilts], int)
    tilt = sparse.csr_matrix((np.array([v for _, v in tilts]).ravel(), (np.repeat(np.arange(len(tilts)), 2), np.column_stack([tilt_col + 1, tilt_col + 2]).ravel())),
                             shape=(len(tilts), n_x))
    smooth_x, smooth_base = smooth_z @ expand, smooth_z @ base
    limits_z = sparse.vstack([grade, curve]).tocsr()
    limits_x = (limits_z @ expand).tocsr()
    limits_x.eliminate_zeros()
    shift = limits_z @ base
    lower = np.r_[-grade_max, curve_lo] - shift
    upper = np.r_[grade_max, curve_hi] - shift
    live = np.diff(limits_x.indptr) > 0                                                # limits between fixed heights only are not ours to enforce
    limits_x, lower, upper = limits_x[live].tocsc(), lower[live], upper[live]
    regular = (smooth_x.T @ smooth_x + tilt.T @ tilt).tocsc()

    at_fixed = np.asarray((limits_z[live][:, np.flatnonzero(fixed)] != 0).sum(axis=1)).ravel() > 0      # limits that involve a height fixed by a neighbour

    def run(slack):
        """Robust rounds; `slack`: per limit, the fraction it is widened by (None: as configured)."""
        widen = 0.0 if slack is None else slack
        x, status, fit = None, "", weight * step
        for _ in range(config.PROFILE_ROBUST_ROUNDS + 1):
            p = sparse.triu(regular + expand.T @ sparse.diags(fit) @ expand, format="csc")
            q = smooth_x.T @ smooth_base + expand.T @ (fit * (base - (target - datum)))
            x, status = solve_qp(p, q, limits_x, lower - widen * (np.abs(lower) + 1e-3), upper + widen * (np.abs(upper) + 1e-3))
            if x is None:
                return None, status
            residual = expand @ x + base + datum - target
            fit = weight * step / (1.0 + (residual / config.PROFILE_ROBUST_SCALE) ** 2)
        return x, status

    # a neighbour's fixed heights may be impossible to meet within the limits: give way there first, everywhere only as a last resort
    x, status = run(None)
    for label, slack in (("limits eased at the tile border", 1.0 * at_fixed), ("limits eased a lot at the tile border", 8.0 * at_fixed), ("limits eased everywhere", np.full(len(lower), 2.0))):
        if x is not None:
            break
        x, status = run(slack)
        status = label if x is not None else f"FAILED ({status})"
    if x is None:
        x = np.zeros(n_x)
    z = expand @ x + base + datum
    planes = block["known_plane"].copy()
    for j in np.flatnonzero(open_junction):
        planes[j] = x[column[j]:column[j] + 3] + np.array([datum, 0.0, 0.0])
    return dict(tile=block["tile"], z=z, planes=planes, ground=ground, status=status)


# ---------------------------------------------------------------- the whole area

def solve(network, log=print, jobs=6, reuse=True):
    """Fill `z`, `tilt`, `ground` of every link and `plane` of every junction. Returns stats for the build report.
    `reuse`: tiles whose inputs did not change since they were last solved take their stored result."""
    links, junctions = network.links, network.junctions
    samples = Samples(network)
    n = samples.offsets[-1]
    z, ground = np.full(n, np.nan), np.zeros(n)
    planes = np.full((len(junctions), 3), np.nan)
    tiles = sorted({tuple(t) for t in samples.tile.tolist()} | {tuple(t) for t in samples.junction_tile.tolist()})
    statuses, reused = {}, 0

    def submit(pool, block):
        return pool.submit(solve_block_cached, block, cache_file(network.tag, block["tile"])) if reuse else pool.submit(solve_block, block)

    def keep(block, result):
        nonlocal reused
        reused += result.get("reused", False)
        own = block["own"]
        z[block["ids"][own]], ground[block["ids"][own]] = result["z"][own], result["ground"][own]
        mine = (samples.junction_tile[block["junctions"]] == np.array(block["tile"])).all(axis=1)
        planes[block["junctions"][mine]] = result["planes"][mine]
        statuses[result["status"]] = statuses.get(result["status"], 0) + 1

    with ProcessPoolExecutor(jobs) as pool:
        for colour in range(4):                                     # tiles of one colour never touch: they are solved side by side
            batch = [t for t in tiles if (t[0] & 1) + 2 * (t[1] & 1) == colour]
            pending = {submit(pool, block): block for block in (make_block(samples, t, z, planes) for t in batch)}
            for count, future in enumerate(as_completed(pending), 1):
                keep(pending[future], future.result())
                if count % 20 == 0 or count == len(pending):
                    log(f"    profile round {colour + 1}/4: {count}/{len(pending)} tiles  {statuses}  ({reused} not solved again)")

    for i, junction in enumerate(junctions):
        junction.plane = planes[i]
    for k, link in enumerate(links):
        span = slice(samples.offsets[k], samples.offsets[k + 1])
        link.z, link.ground = z[span].copy(), ground[span].copy()
        link.tilt = _tilt(link, junctions)
    return dict(tiles=len(tiles), samples=int(n), statuses=statuses, unsolved_samples=int(np.isnan(z).sum()), tiles_reused=reused)


def _fade(x):
    """1 at x <= 0 falling smoothly to 0 at x >= 1."""
    t = np.clip(x, 0.0, 1.0)
    return 1.0 - t * t * (3.0 - 2.0 * t)


def _tilt(link, junctions):
    """Cross slope per sample: that of the junction plane inside a junction, unwound over TILT_FADE beyond each mouth."""
    def plane_tilt(junction, i):
        return link.nrm[i] @ junctions[junction].plane[1:]

    n = len(link.s)
    if link.internal:
        return np.array([plane_tilt(max(link.junction), i) for i in range(n)])
    tilt = np.zeros(n)
    if link.junction[0] >= 0:
        tilt += plane_tilt(link.junction[0], link.i0) * _fade((link.s - link.s[link.i0]) / config.TILT_FADE)
    if link.junction[1] >= 0:
        tilt += plane_tilt(link.junction[1], link.i1) * _fade((link.s[link.i1] - link.s) / config.TILT_FADE)
    if link.junction[0] >= 0:
        tilt[:link.i0 + 1] = [plane_tilt(link.junction[0], i) for i in range(link.i0 + 1)]
    if link.junction[1] >= 0:
        tilt[link.i1:] = [plane_tilt(link.junction[1], i) for i in range(link.i1, n)]
    return tilt
