"""Stage 5: vertical alignment, one optimisation for the whole network.

Unknowns: the height of every link sample outside the junctions, and a plane (height at the centre, two slopes) per junction.
Link samples inside a junction lie on its plane by construction, so every road through a junction shares its height and its slope there.

    minimise    sum w_i (z_i - ground_i)^2                 stay on the LiDAR ground (robust: outliers are down-weighted and the solve repeated)
              + lambda * sum (third derivative of z)^2     grade changes as parabolas (zero third derivative), the curve of road design
              + tilt penalty                               a junction plane should not tilt across its important arms
    subject to  |grade| <= max_grade                       per road class
                -1 / crest_radius <= z'' <= 1 / sag_radius

A flat network satisfies every constraint, so the problem is always feasible. Bridges aim at their deck (surface model) instead of
the ground below; tunnels have no target and are carried by their two ends.
"""
import numpy as np
import osqp
from scipy import sparse
from scipy.ndimage import median_filter

from rasters import Mosaic

from . import config

ACROSS = (-0.7, -0.35, 0.0, 0.35, 0.7)            # ground is sampled at these fractions of the half width, the median is kept
SOLVER = dict(eps_abs=2e-5, eps_rel=2e-5, max_iter=40000, polish=False, verbose=False)


def terrain_for(links, pad=40):
    xy = np.vstack([link.xy for link in links])
    x0, z0 = np.floor((xy.min(axis=0) - pad) / 4.0) * 4.0
    x1, z1 = np.ceil((xy.max(axis=0) + pad) / 4.0) * 4.0
    return Mosaic(x0, z0, x1 - x0, z1 - z0, kinds=("mnt", "mnh"))


def _runs(mask):
    """[(first, last)] of every run of True in a boolean array."""
    edges = np.flatnonzero(np.diff(np.r_[False, mask, False].astype(np.int8)))
    return list(zip(edges[0::2], edges[1::2] - 1))


def sample_targets(link, edges, terrain):
    """Target height and weight per sample: the ground; on bridges the deck; nothing in tunnels."""
    across = [terrain.sample(terrain.mnt, *(link.xy + link.nrm * (o * link.hw)[:, None]).T, 2) for o in ACROSS]
    ground = np.median(across, axis=0)
    target, weight = ground.copy(), np.ones(len(ground))
    seg_bridge = np.array([edges[link.chain[p][0]].bridge for p in link.part])
    seg_tunnel = np.array([edges[link.chain[p][0]].tunnel for p in link.part])

    def on(seg):                                                    # a sample is on a bridge when a segment next to it is
        return np.r_[seg, False] | np.r_[False, seg]

    weight[on(seg_tunnel)] = 0.0
    if seg_bridge.any():
        surface = terrain.sample(terrain.mnt, *link.xy.T, 2) + np.clip(terrain.sample(terrain.mnh, *link.xy.T, 2), 0.0, config.BRIDGE_DECK_MAX_ABOVE)
        for a, b in _runs(on(seg_bridge)):
            lo, hi = max(a - 1, 0), min(b + 1, len(ground) - 1)
            chord = np.interp(link.s[a:b + 1], [link.s[lo], link.s[hi]], [ground[lo], ground[hi]])           # abutment to abutment
            deck = median_filter(surface[a:b + 1], size=min(7, b - a + 1), mode="nearest")
            plausible = (deck - chord >= -1.0) & (deck - chord <= 4.5)
            target[a:b + 1] = np.where(plausible, deck, chord)
    return ground, target, weight


def _derivatives(s):
    """Sparse operators giving grade (n - 1 rows), curvature (n - 2) and third derivative (n - 3) of heights sampled at arc lengths `s`."""
    n, h = len(s), np.diff(s)
    d1 = sparse.diags([-1.0 / h, 1.0 / h], [0, 1], shape=(n - 1, n), format="csr")
    if n < 3:
        return d1, None, None
    between = 0.5 * (h[:-1] + h[1:])
    d2 = sparse.diags(1.0 / between) @ sparse.diags([-np.ones(n - 2), np.ones(n - 2)], [0, 1], shape=(n - 2, n - 1)) @ d1
    if n < 4:
        return d1, d2, None
    d3 = sparse.diags(1.0 / h[1:-1]) @ sparse.diags([-np.ones(n - 3), np.ones(n - 3)], [0, 1], shape=(n - 3, n - 2)) @ d2
    return d1, d2, d3


def solve(network, log=print):
    """Fill `z`, `tilt`, `ground` of every link and `plane` of every junction. Returns stats for the build report."""
    links, junctions, edges = network.links, network.junctions, network.edges
    terrain = terrain_for(links)
    offsets = np.r_[0, np.cumsum([len(link.s) for link in links])]
    n_z = offsets[-1]

    # ---- which samples are free, which lie on a junction plane
    on_plane = np.full(n_z, -1)
    for k, link in enumerate(links):
        local = np.arange(len(link.s))
        if link.internal:
            on_plane[offsets[k] + local] = max(link.junction)
            continue
        if link.junction[0] >= 0:
            on_plane[offsets[k] + local[:link.i0 + 1]] = link.junction[0]
        if link.junction[1] >= 0:
            on_plane[offsets[k] + local[link.i1:]] = link.junction[1]
    free = np.flatnonzero(on_plane < 0)
    n_free, n_x = len(free), len(free) + 3 * len(junctions)
    xy = np.vstack([link.xy for link in links])
    planar = np.flatnonzero(on_plane >= 0)
    j = on_plane[planar]
    d = xy[planar] - np.array([junctions[i].centre for i in j]).reshape(-1, 2)
    # z = expand @ x: a free sample is its own unknown, a sample on a plane is a combination of the three unknowns of its junction
    expand = sparse.csr_matrix(
        (np.r_[np.ones(n_free), np.ones(len(planar)), d[:, 0], d[:, 1]],
         (np.r_[free, planar, planar, planar], np.r_[np.arange(n_free), n_free + 3 * j, n_free + 3 * j + 1, n_free + 3 * j + 2])), shape=(n_z, n_x))

    # ---- targets, smoothness and limits, link by link
    ground, target, weight, step = (np.zeros(n_z) for _ in range(4))
    smooth_rows, grade_rows, grade_max, curve_rows, curve_lo, curve_hi = [], [], [], [], [], []
    for k, link in enumerate(links):                                                    # every list gets one block per link, n columns wide
        span, n = slice(offsets[k], offsets[k + 1]), len(link.s)
        ground[span], target[span], weight[span] = sample_targets(link, edges, terrain)
        step[span] = np.gradient(link.s)
        classes = [edges[link.chain[p][0]].road_class for p in link.part]             # per segment
        d1, d2, d3 = _derivatives(link.s)
        grade_rows.append(d1)
        grade_max += [c.max_grade for c in classes]
        inner = np.arange(max(link.i0, 1), min(link.i1, n - 2) + 1) if d2 is not None and not link.internal else np.zeros(0, int)      # from mouth to mouth: the road eases onto its junction planes
        curve_rows.append(d2[inner - 1] if len(inner) else sparse.csr_matrix((0, n)))
        curve_lo += [-1.0 / classes[i - 1].crest_radius for i in inner]
        curve_hi += [1.0 / classes[i - 1].sag_radius for i in inner]
        if d3 is None:
            smooth_rows.append(sparse.csr_matrix((0, n)))
            continue
        stiffness = np.array([(c.profile_wavelength / (2.0 * np.pi)) ** 6 for c in classes[1:-1]]) * np.diff(link.s)[1:-1]
        smooth_rows.append(sparse.diags(np.sqrt(stiffness)) @ d3)

    tilt_rows = []                                                                      # junction planes resist tilting across their arms
    for i, junction in enumerate(junctions):
        arms = junction.arms
        if not arms:
            continue
        ranks = np.array([edges[links[a.link].chain[0 if a.end == 0 else -1][0]].road_class.rank for a in arms], float)
        for arm, rank in zip(arms, ranks):
            across = np.array([-arm.tan[0][1], arm.tan[0][0]])
            scale = np.sqrt(config.TILT_STIFFNESS) * (rank / ranks.max()) ** 2
            tilt_rows.append(sparse.csr_matrix((scale * across, ([0, 0], [n_free + 3 * i + 1, n_free + 3 * i + 2])), shape=(1, n_x)))

    datum = float(np.median(ground))
    smooth = sparse.block_diag(smooth_rows, format="csr") @ expand
    tilt = sparse.vstack(tilt_rows) if tilt_rows else sparse.csr_matrix((0, n_x))
    limits = sparse.vstack([sparse.block_diag(grade_rows, format="csr"), sparse.block_diag(curve_rows, format="csr")]) @ expand
    lower, upper = np.r_[-np.array(grade_max), curve_lo], np.r_[np.array(grade_max), curve_hi]
    regular = (smooth.T @ smooth + tilt.T @ tilt).tocsc()

    x, status = None, ""
    fit = weight * step
    for round_ in range(config.PROFILE_ROBUST_ROUNDS + 1):
        data = expand.T @ sparse.diags(fit) @ expand
        p = sparse.triu(regular + data, format="csc")
        q = -(expand.T @ (fit * (target - datum)))
        solver = osqp.OSQP()
        solver.setup(P=p, q=q, A=limits.tocsc(), l=lower, u=upper, **SOLVER)
        if x is not None:
            solver.warm_start(x=x)
        result = solver.solve()
        x, status = result.x, result.info.status
        residual = expand @ x + datum - target
        log(f"    profile round {round_}: {status}, {result.info.iter} iterations, rms to ground {np.sqrt(np.mean(residual[weight > 0] ** 2)):.3f} m")
        fit = weight * step / (1.0 + (residual / config.PROFILE_ROBUST_SCALE) ** 2)

    z = expand @ x + datum
    for i, junction in enumerate(junctions):
        junction.plane = x[n_free + 3 * i:n_free + 3 * i + 3] + np.array([datum, 0.0, 0.0])
    for k, link in enumerate(links):
        span = slice(offsets[k], offsets[k + 1])
        link.z, link.ground = z[span].copy(), ground[span].copy()
        link.tilt = _tilt(link, junctions)
    return dict(status=status, unknowns=int(n_x), samples=int(n_z))


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
