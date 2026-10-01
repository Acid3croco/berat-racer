"""Roofs on the real footprint: a straight skeleton (weighted: an edge may stand still, which makes it a gable) of the outline and its
courtyards, lifted into planes.

The skeleton is simulated as a moving wavefront. Every edge moves inwards at its speed (1 for a roof slope, 0 for a gable end); every
vertex moves so that it stays on both its edges. Two kinds of event change the wavefront:

  edge event    an edge shrinks to nothing: its two vertices meet and become one;
  split event   a reflex vertex runs into an edge of the wavefront (of its own ring or of another, a courtyard's): the ring is split
                in two (or two rings become one).

Each event leaves a skeleton node, at the height (time x pitch) where it happened, and the node belongs to the faces of the edges that
meet there. A face is the edge's own roof plane: the edge and its nodes, which are monotone along the edge, so they are ordered by
their position along it. `roof()` returns the faces as triangles; a footprint the simulation cannot settle returns None (the caller
then lays a flat roof).
"""
import numpy as np

EPS = 1e-7
OSM_SHAPES = {"flat": "flat", "gabled": "gabled", "hipped": "hipped", "pyramidal": "hipped", "half-hipped": "hipped",
              "skillion": "flat", "dome": "hipped", "round": "hipped", "mansard": "hipped", "gambrel": "gabled", "saltbox": "gabled"}


class _Vertex:
    __slots__ = ("p", "w", "a", "b", "ring")

    def __init__(self, p, a, b, ring):
        self.p, self.a, self.b, self.ring = np.asarray(p, float), a, b, ring       # a: edge arriving, b: edge leaving
        self.w = None


class Skeleton:
    def __init__(self, rings, speeds=None):
        """`rings`: the outline (counter-clockwise) and its holes (clockwise), each (n, 2) without repeating the first point.
        `speeds`: per edge (ring by ring, edge i from point i to i + 1), 1 or 0 (a gable). Default all 1."""
        self.p0, self.p1, self.n, self.d = [], [], [], []
        self.rings = []
        for ring in rings:
            ring = np.asarray(ring, float)
            first = len(self.p0)
            for i in range(len(ring)):
                a, b = ring[i], ring[(i + 1) % len(ring)]
                d = (b - a) / max(np.hypot(*(b - a)), 1e-12)
                self.p0.append(a); self.p1.append(b); self.d.append(d); self.n.append(np.array([-d[1], d[0]]))     # inward: left of the edge
            self.rings.append((first, len(ring)))
        self.p0, self.p1, self.n, self.d = map(np.array, (self.p0, self.p1, self.n, self.d))
        self.speed = np.ones(len(self.p0)) if speeds is None else np.asarray(speeds, float)
        self.nodes = []                                  # [(point, time)]
        self.faces = [[] for _ in range(len(self.p0))]   # per edge: node indices
        self.time = 0.0

    # ---------------------------------------------------------------- kinematics
    def _velocity(self, a, b):
        """Velocity of a vertex between edges a and b: it keeps on both (n_a . w = s_a, n_b . w = s_b)."""
        na, nb, sa, sb = self.n[a], self.n[b], self.speed[a], self.speed[b]
        det = na[0] * nb[1] - na[1] * nb[0]
        if abs(det) < 1e-9:                                                      # parallel: straight on
            if np.dot(na, nb) > 0:
                return na * max(sa, sb)
            return None                                                          # opposite: a spike
        return np.array([sa * nb[1] - sb * na[1], -sa * nb[0] + sb * na[0]]) / det

    def _line_point(self, e):
        """A point of edge e's line at the current time."""
        return self.p0[e] + self.n[e] * self.speed[e] * self.time

    def _node(self, p, edges):
        self.nodes.append((np.asarray(p, float), self.time))
        k = len(self.nodes) - 1
        for e in set(edges):
            self.faces[e].append(k)
        return k

    # ---------------------------------------------------------------- simulation
    def run(self, max_rounds=None):
        lavs = []
        for first, count in self.rings:
            lav = []
            for i in range(count):
                e_in, e_out = first + (i - 1) % count, first + i
                v = _Vertex(self.p0[e_out], e_in, e_out, len(lavs))
                lav.append(v)
                self._node(v.p, (e_in, e_out))
            lavs.append(lav)
        rounds = 0
        limit = max_rounds or 20 * len(self.p0) + 50
        while any(len(l) for l in lavs):
            rounds += 1
            if rounds > limit:
                raise RuntimeError("too many events")
            lavs = [self._tidy(l) for l in lavs]
            lavs = [l for l in lavs if l]
            if not lavs:
                break
            lavs = [self._unspike(l) for l in lavs]
            lavs = [l for l in lavs if l]
            if not lavs:
                break
            for lav in lavs:
                for v in lav:
                    v.w = self._velocity(v.a, v.b)
                    if v.w is None:
                        raise RuntimeError("spike")
            dt, events = self._next_events(lavs)
            if dt is None:
                raise RuntimeError("no event: the wavefront does not close")
            for lav in lavs:
                for v in lav:
                    v.p = v.p + v.w * dt
            self.time += dt
            lavs = self._apply(lavs, events)
        return self

    def _tidy(self, lav):
        """Merge neighbours that coincide (their edge collapsed) into one vertex, leaving a node; close rings of two or fewer."""
        changed = True
        while changed and len(lav) > 1:
            changed = False
            for i in range(len(lav)):
                u, v = lav[i], lav[(i + 1) % len(lav)]
                if u is v:
                    continue
                if np.hypot(*(u.p - v.p)) < 1e-6:
                    self._node(u.p, (u.a, u.b, v.a, v.b))
                    merged = _Vertex(u.p, u.a, v.b, u.ring)
                    lav = [x for x in lav if x is not u and x is not v]
                    lav.insert(min(i, len(lav)), merged)
                    changed = True
                    break
        pts = np.array([v.p for v in lav]) if lav else np.zeros((0, 2))
        area = 0.5 * abs(np.sum(pts[:, 0] * np.roll(pts[:, 1], -1) - np.roll(pts[:, 0], -1) * pts[:, 1])) if len(lav) > 2 else 0.0
        if len(lav) <= 2 or area < 1e-9 or any(v.a == v.b for v in lav):
            for v in lav:                                                        # the wavefront has closed (to a point or a ridge line)
                self._node(v.p, (v.a, v.b))
            return []
        return lav

    def _unspike(self, lav):
        """A vertex between two edges facing each other lies where they have met (a ridge): it leaves the wavefront with a node, and its
        neighbours are joined along the edge before it (the two edges coincide there)."""
        while len(lav) > 2:
            k = next((i for i, v in enumerate(lav) if self._velocity(v.a, v.b) is None), None)
            if k is None:
                return lav
            v, nxt = lav[k], lav[(k + 1) % len(lav)]
            self._node(v.p, (v.a, v.b))
            nxt.a = v.a
            lav = lav[:k] + lav[k + 1:]
            lav = self._tidy(lav)
        return self._tidy(lav)

    def _next_events(self, lavs):
        best, events = None, []

        def offer(dt, ev):
            nonlocal best, events
            if dt is None or dt < -1e-9:
                return
            dt = max(dt, 0.0)
            if best is None or dt < best - 1e-7:
                best, events = dt, [ev]
            elif abs(dt - best) <= 1e-7:
                events.append(ev)

        active = [(v, lav) for lav in lavs for v in lav]
        for lav in lavs:
            m = len(lav)
            for i in range(m):                                                   # edge events
                u, v = lav[i], lav[(i + 1) % m]
                d = v.p - u.p
                length = np.hypot(*d)
                rate = np.dot(v.w - u.w, d / max(length, 1e-12)) if length > 1e-12 else -1.0
                if rate < -1e-9:
                    offer(-length / rate, ("edge", u, v))
        for v, lav in active:                                                    # split events: reflex vertices
            na, nb = self.n[v.a], self.n[v.b]
            if self.d[v.a][0] * self.d[v.b][1] - self.d[v.a][1] * self.d[v.b][0] >= -1e-9:
                continue                                                         # convex or straight
            for lav2 in lavs:
                m = len(lav2)
                for i in range(m):
                    l, r = lav2[i], lav2[(i + 1) % m]
                    f = l.b
                    if f != r.a or f in (v.a, v.b) or l is v or r is v:
                        continue
                    nf, sf = self.n[f], self.speed[f]
                    denom = sf - np.dot(nf, v.w)
                    if denom <= 1e-9:
                        continue
                    dt = (np.dot(nf, v.p - self._line_point(f))) / denom
                    if dt <= 1e-9:
                        continue
                    hit = v.p + v.w * dt
                    lt, rt = l.p + l.w * dt, r.p + r.w * dt
                    along = self.d[f]
                    if np.dot(hit - lt, along) < -1e-6 or np.dot(rt - hit, along) < -1e-6:
                        continue
                    offer(dt, ("split", v, l, r))
        return best, events

    def _apply(self, lavs, events):
        done = set()
        for ev in events:
            if ev[0] == "edge":
                _, u, v = ev
                if id(u) in done or id(v) in done:
                    continue
                lav = next((l for l in lavs if any(x is u for x in l) and any(x is v for x in l)), None)
                if lav is None:
                    continue
                self._node(u.p, (u.a, u.b, v.b))
                x = _Vertex(u.p, u.a, v.b, u.ring)
                i = next(k for k, y in enumerate(lav) if y is u)
                lav[i] = x
                lav.remove(v)
                done |= {id(u), id(v)}
            else:
                _, v, l, r = ev
                if id(v) in done or id(l) in done or id(r) in done:
                    continue
                lv = next((L for L in lavs if any(x is v for x in L)), None)
                lf = next((L for L in lavs if any(x is l for x in L) and any(x is r for x in L)), None)
                if lv is None or lf is None:
                    continue
                f = l.b
                self._node(v.p, (v.a, v.b, f))
                v1, v2 = _Vertex(v.p, v.a, f, v.ring), _Vertex(v.p, f, v.b, v.ring)
                iv = next(k for k, y in enumerate(lv) if y is v)
                if lv is lf:
                    ir = next(k for k, y in enumerate(lv) if y is r)
                    # ring A: v1, r, ... up to the vertex before v; ring B: v2, after v, ... up to l
                    seq = lv[iv + 1:] + lv[:iv]                                  # after v, around, to before v
                    jr, jl = next(k for k, y in enumerate(seq) if y is r), next(k for k, y in enumerate(seq) if y is l)
                    ring_b = [v2] + seq[:jl + 1]                                  # v2 -> after v ... -> l
                    ring_a = [v1] + seq[jr:]                                      # v1 -> r ... -> before v
                    lavs = [L for L in lavs if L is not lv] + [ring_a, ring_b]
                else:
                    seq_v = lv[iv + 1:] + lv[:iv]                                # after v ... before v
                    il = next(k for k, y in enumerate(lf) if y is l)
                    seq_f = lf[il + 1:] + lf[:il + 1]                            # r ... l
                    merged = [v1] + seq_f + [v2] + seq_v
                    lavs = [L for L in lavs if L is not lv and L is not lf] + [merged]
                done |= {id(v), id(l), id(r)}
        return lavs

    # ---------------------------------------------------------------- faces
    def faces_3d(self, pitch):
        """Per edge: its face as a polygon (m, 3) in order (the edge, then its nodes back along it), heights time x pitch / speed."""
        out = []
        for e in range(len(self.p0)):
            pts = np.array([self.nodes[k][0] for k in self.faces[e]])
            hs = np.array([self.nodes[k][1] for k in self.faces[e]]) * pitch
            if len(pts) < 3:
                out.append(None)
                continue
            # unique nodes, ordered along the edge from its end back to its start (the face is monotone along its edge)
            key = np.round(np.c_[pts, hs], 6)
            _, keep = np.unique(key, axis=0, return_index=True)
            pts, hs = pts[np.sort(keep)], hs[np.sort(keep)]
            t = (pts - self.p0[e]) @ self.d[e]
            on_edge = hs < 1e-9
            top = np.flatnonzero(~on_edge)
            order = top[np.lexsort((-hs[top], -t[top]))]
            ring = [np.r_[self.p0[e], 0.0], np.r_[self.p1[e], 0.0]] + [np.r_[pts[k], hs[k]] for k in order]
            out.append(np.array(ring))
        return out


def _triangulate(poly2d):
    """Ear clipping of a simple polygon (counter-clockwise or not): index triples."""
    pts = np.asarray(poly2d, float)
    n = len(pts)
    if n < 3:
        return []
    area = 0.5 * np.sum(pts[:, 0] * np.roll(pts[:, 1], -1) - np.roll(pts[:, 0], -1) * pts[:, 1])
    idx = list(range(n)) if area > 0 else list(range(n))[::-1]
    tris, guard = [], 4 * n * n

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    while len(idx) > 3 and guard > 0:
        guard -= 1
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = pts[i0], pts[i1], pts[i2]
            if cross(a, b, c) <= 1e-12:
                continue
            if any(cross(a, b, pts[j]) > 1e-12 and cross(b, c, pts[j]) > 1e-12 and cross(c, a, pts[j]) > 1e-12 for j in idx if j not in (i0, i1, i2)):
                continue
            tris.append((i0, i1, i2))
            idx.pop(k)
            break
        else:
            return None
    if len(idx) == 3:
        tris.append(tuple(idx))
    return tris


def roof(rings, pitch, gables=None):
    """Triangles of a roof over `rings` (outline counter-clockwise, holes clockwise; local metres): (t, 3, 3) points (x, north, height
    above the eaves) and per triangle True where it is a gable wall (vertical face of an edge with speed 0). None if it fails."""
    def signed(r):
        r = np.asarray(r, float)
        return 0.5 * np.sum(r[:, 0] * np.roll(r[:, 1], -1) - np.roll(r[:, 0], -1) * r[:, 1])
    rings = [np.asarray(r, float) for r in rings]
    flips = [(signed(r) < 0) if i == 0 else (signed(r) > 0) for i, r in enumerate(rings)]     # outline counter-clockwise, holes clockwise
    rings = [r[::-1] if f else r for r, f in zip(rings, flips)]
    speeds = None
    if gables is not None:
        speeds, at = [], 0
        for r, f in zip(rings, flips):
            g = np.asarray(gables[at:at + len(r)], bool)
            speeds.append(np.where((np.roll(g[::-1], -1) if f else g), 0.0, 1.0))          # edge i of a reversed ring is edge n - 2 - i
            at += len(r)
        speeds = np.concatenate(speeds)
    sk = Skeleton(rings, speeds)
    try:
        sk.run()
    except (RuntimeError, StopIteration, ValueError):
        return None
    tris, wall = [], []
    for e, face in enumerate(sk.faces_3d(pitch)):
        if face is None:
            continue
        gable = sk.speed[e] == 0.0
        # triangulate in the face's own frame: along the edge, and up its slope (or up the wall for a gable)
        u = (face[:, :2] - sk.p0[e]) @ sk.d[e]
        v = face[:, 2] if gable else (face[:, :2] - sk.p0[e]) @ sk.n[e]
        t = _triangulate(np.c_[u, v])
        if t is None:
            return None
        for i, j, k in t:
            tris.append(face[[i, j, k]])
            wall.append(gable)
    return np.array(tris), np.array(wall, bool)


# ---------------------------------------------------------------- one building

FLAT_RISE = 0.5                   # m: a roof rising less than this from eave to ridge is flat
PITCH_RANGE = (0.12, 1.5)         # rise / run of the slopes: below, the roof is flat; above, the ridge is lowered (data or skeleton disagree)
GABLE_LENGTH = 14.0               # an end wall longer than this stays hipped
GABLE_TEST_INSET = 0.8            # the surface model is read this far inside an end wall ...
GABLE_SHARE = 0.6                 # ... where it stands this share of the rise above the eave, the end is a gable
TILE_FIT = 0.02                   # the roof's faces must cover the footprint within this share of its area


def _area(tris):
    t = np.asarray(tris)
    return float(np.abs(0.5 * ((t[:, 1, 0] - t[:, 0, 0]) * (t[:, 2, 1] - t[:, 0, 1]) - (t[:, 1, 1] - t[:, 0, 1]) * (t[:, 2, 0] - t[:, 0, 0]))).sum()) if len(t) else 0.0


def flat_cap(poly):
    """Triangles (t, 3, 2) of a flat roof over a polygon with courtyards (constrained Delaunay: no holes left, none filled)."""
    import shapely
    tris = shapely.constrained_delaunay_triangles(poly)
    return np.array([np.array(t.exterior.coords)[:3] for t in tris.geoms]) if not tris.is_empty else np.zeros((0, 3, 2))


def building_roof(poly, eave, rise, dsm, osm_shape=None, party=None):
    """The roof of one building: dict(shape: "flat" / "hipped" / "gabled" / "flat fallback", tris (t, 3, 3) absolute (x, north,
    height), wall (t,) gable faces, pitch). `poly`: footprint (shapely, courtyards kept); `eave`, `rise`: metres; `dsm(xy)`: the
    LiDAR surface (ground + height above ground) at plan points; `osm_shape`: OSM roof:shape, which wins over the LiDAR; `party`: per
    edge (outline, then courtyards), shared with a neighbour: an end there is a gable, so a terrace keeps one ridge."""
    shape = OSM_SHAPES.get(osm_shape) if osm_shape else None
    rings = [np.array(poly.exterior.coords)[:-1]] + [np.array(h.coords)[:-1] for h in poly.interiors]

    def flat(label):
        cap = flat_cap(poly)
        return dict(shape=label, tris=np.concatenate([cap, np.full(cap.shape[:2] + (1,), eave)], axis=2), wall=np.zeros(len(cap), bool), pitch=0.0)

    if shape == "flat" or (shape is None and rise < FLAT_RISE):
        return flat("flat")
    hip = roof(rings, 1.0)                                                    # unit pitch: heights are the wavefront's times
    if hip is None or abs(_area(hip[0]) - poly.area) > TILE_FIT * poly.area + 0.5:
        return flat("flat fallback")
    depth = float(hip[0][:, :, 2].max())
    if depth < 0.5:
        return flat("flat")
    pitch = rise / depth
    if pitch < PITCH_RANGE[0] and shape is None:
        return flat("flat")
    pitch = float(np.clip(pitch, *PITCH_RANGE))
    # ends: edges whose hip face is a triangle; a gable where the surface model stands high just inside them (or OSM says gabled)
    edges = [(r[i], r[(i + 1) % len(r)]) for r in rings for i in range(len(r))]
    gables = np.zeros(len(edges), bool)
    if shape != "hipped":
        sk = Skeleton([r if k == 0 else r for k, r in enumerate(rings)])
        try:
            sk.run()
            faces = sk.faces_3d(1.0)
        except (RuntimeError, StopIteration, ValueError):
            faces = [None] * len(edges)
        for e, (a, b) in enumerate(edges):
            face = faces[e] if e < len(faces) else None
            length = float(np.hypot(*(b - a)))
            if face is None or len(face) != 3 or length > GABLE_LENGTH:
                continue
            d = (b - a) / max(length, 1e-9)
            inside = 0.5 * (a + b) + np.array([-d[1], d[0]]) * GABLE_TEST_INSET
            if shape == "gabled" or (party is not None and party[e]) or dsm(inside[None])[0] - eave >= GABLE_SHARE * rise:
                gables[e] = True
    shaped = roof(rings, pitch, gables) if gables.any() else (hip[0] * [1.0, 1.0, pitch], hip[1])
    if shaped is None or abs(_area(shaped[0][~shaped[1]]) - poly.area) > TILE_FIT * poly.area + 0.5:
        shaped, gables = (hip[0] * [1.0, 1.0, pitch], hip[1]), np.zeros(len(edges), bool)
    tris = shaped[0].copy()
    tris[:, :, 2] += eave
    return dict(shape="gabled" if gables.any() else "hipped", tris=tris, wall=shaped[1], pitch=pitch)


# ---------------------------------------------------------------- OSM roof tags (sources.py: Overpass, the OSM database on mace has no buildings)

def osm_buildings(tiles):
    """[(shapely polygon, tags)] of the OSM buildings of these tiles that tag their roof or levels, by id."""
    from shapely.geometry import Polygon
    import sources
    return [(Polygon(b["xy"]), b["tags"]) for b in sorted(sources.read_tiles("osm_roofs", tiles), key=lambda b: b["id"]) if len(b["xy"]) >= 4]
