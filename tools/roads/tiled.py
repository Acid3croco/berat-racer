"""The road pipeline per tile: no stage holds the whole network.

Tiles are the sectors (3.2 km). The stages of `build.py` run in four passes over the tiles; every pass reads the source tiles and
what the previous passes wrote for the tiles around, and writes, per tile, only what the tile owns. Every object is computed by
one tile, its owner, so where two tiles meet there is one answer, not two to reconcile:

  1. align      horizontal alignment, in four rounds like the colours of a 2 x 2 checkerboard (tiles of one round never touch).
                A tile reads the surveyed sections around it (ALIGN_HALO), cut where they cross a tile border, and smooths its
                strokes; whatever a tile of an earlier round already smoothed is fixed, and a stroke continuing it is pinned to its
                last CONTEXT metres (alignment.py). It owns the section parts lying in it and the nodes no earlier tile owns.
  2. network    the sections joined again (the parts of each one put end to end): graph, links, junctions, crossings
  3. profile    heights, in rounds (profile.py)
  4. surface    lanes, the lane graph, road pieces and junction meshes

Files: data/big/roads/<tag>/<pass>/<si>_<sj>.pkl (+ a key: the hash of what the tile was made from; an unchanged tile is not
made again).
"""
import os
import pickle
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

import numpy as np
import zstandard
from scipy.spatial import cKDTree

import rasters
import sources
from rasters import BIG, HALF, SECTOR

from . import alignment, config, graph as graph_stage, osm, source
from .digest import code_stamp, digest

TILE = SECTOR
WORKER_TASKS = 10                 # tiles per worker before the pool is renewed: its memory is what a tile needs, not what piled up
ALIGN_HALO = 1600.0               # m: how far a tile reads the sections around it for the alignment


def tile_of(x, z):
    return int(np.floor((x + HALF) / TILE)), int(np.floor((z + HALF) / TILE))


def rect(t, grow=0.0):
    """(x0, z0, x1, z1) local metres of a tile, grown by `grow`."""
    x0, z0 = -HALF + t[0] * TILE, -HALF + t[1] * TILE
    return x0 - grow, z0 - grow, x0 + TILE + grow, z0 + TILE + grow


def colour(t):
    """Round (0 .. 3) in which a tile is solved: neighbouring tiles never share one."""
    return (t[0] & 1) + 2 * (t[1] & 1)


def tiles_meeting(box):
    x0, z0, x1, z1 = box
    a, b = tile_of(x0, z0), tile_of(x1, z1)
    return [(i, j) for j in range(a[1], b[1] + 1) for i in range(a[0], b[0] + 1)]


def meets(lo, hi, box):
    return hi[0] >= box[0] and lo[0] <= box[2] and hi[1] >= box[1] and lo[1] <= box[3]


def pass_dir(tag, name):
    return BIG / "roads" / tag / name


def tile_file(tag, name, t):
    return pass_dir(tag, name) / f"{t[0]}_{t[1]}.pkl"


def write_pickle(path, value):
    """A tile file: the pickled value, zstd-compressed (level 3: the passes are not slowed by it), written atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    scratch.write_bytes(zstandard.ZstdCompressor(level=3).compress(pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)))
    os.replace(scratch, path)


def read_pickle(path):
    return pickle.loads(zstandard.ZstdDecompressor().decompress(Path(path).read_bytes()))


# ---------------------------------------------------------------- the area

class Area:
    """What every tile job of a build knows: the tag, the sector list's box (roads are built MARGIN beyond it) and its tiles."""

    def __init__(self, tag, sectors, margin):
        self.tag, self.sectors = tag, sorted(sectors)
        self.box = source.area_of(sectors, margin)
        self.tiles = [t for t in sources.window(sectors) if meets(rect(t)[:2], rect(t)[2:], self.box)]
        self.tile_set = set(self.tiles)


# ---------------------------------------------------------------- pass 1: alignment

def border_cuts(xy):
    """Cut a polyline where it crosses tile borders: [(part points)] in order, each lying in one tile (its points within the closed tile)."""
    out, current = [], [xy[0]]
    for a, b in zip(xy[:-1], xy[1:]):
        ts = []
        for axis in (0, 1):
            lo, hi = sorted((a[axis], b[axis]))
            k0, k1 = int(np.floor((lo + HALF) / TILE)) + 1, int(np.floor((hi + HALF) / TILE))
            for k in range(k0, k1 + 1):
                line = -HALF + k * TILE
                if lo < line < hi:
                    ts.append(((line - a[axis]) / (b[axis] - a[axis]), axis, line))
        for t, axis, line in sorted(ts):
            p = a + (b - a) * t
            p[axis] = line                                               # exactly on the border
            current.append(p)
            out.append(np.array(current))
            current = [p.copy()]
        current.append(b)
    out.append(np.array(current))
    return [part for part in out if len(part) >= 2 and np.hypot(*np.diff(part, axis=0).T).sum() > 1e-6]


def part_tile(xy):
    """The tile a part lies in (its middle vertex or segment)."""
    m = len(xy) // 2
    p = xy[m] if len(xy) % 2 else 0.5 * (xy[m - 1] + xy[m])
    return tile_of(*p)


def split_edges(edges):
    """Every section cut at the tile borders: parts (Edge copies, `part` index in `tags['_part']`), sorted by (cleabs, part)."""
    out = []
    for e in sorted(edges, key=lambda e: e.cleabs):
        cuts = border_cuts(e.xy)
        for i, xy in enumerate(cuts):
            part = replace(e, xy=xy, tags=dict(e.tags))
            part.tags["_part"] = (i, len(cuts))
            out.append(part)
    return out


def part_key(e):
    return e.cleabs, e.tags["_part"][0]


def node_key(xy, seam=None):
    """Identity of a node across tiles: its position to the centimetre (and the section for a border point)."""
    k = (int(round(xy[0] * 100.0)), int(round(xy[1] * 100.0)))
    return k + (seam,) if seam else k


def split_graph(parts):
    """`graph_stage.build` for border-cut sections: road ends closer than NODE_SNAP are one node as before; the two ends of a cut
    (on the border) are one node of their own. Nodes in a fixed order (by key); returns (graph, node keys)."""
    ends, seam = [], []
    for e in parts:
        i, n = e.tags["_part"]
        ends += [e.xy[0], e.xy[-1]]
        seam += [i > 0, i < n - 1]
    ends, seam = np.array(ends), np.array(seam)
    parent = np.arange(len(ends))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    plain = np.flatnonzero(~seam)
    for a, b in sorted(cKDTree(ends[plain]).query_pairs(config.NODE_SNAP)):
        parent[find(plain[a])] = find(plain[b])
    for k in range(len(parts) - 1):                                      # a cut: the end of part i and the start of part i + 1
        if parts[k].cleabs == parts[k + 1].cleabs and seam[2 * k + 1]:
            parent[find(2 * k + 1)] = find(2 * k + 2)
    roots = np.array([find(k) for k in range(len(ends))])
    groups = {}
    for k, r in enumerate(roots):
        groups.setdefault(r, []).append(k)
    keyed = []
    for members in groups.values():
        centre = ends[members].mean(axis=0)
        cut = seam[members[0]]
        keyed.append((node_key(centre, parts[members[0] // 2].cleabs if cut else None), centre, members))
    keyed.sort(key=lambda g: g[0])
    nodes = np.array([c for _, c, _ in keyed]).reshape(-1, 2)
    index = np.zeros(len(ends), int)
    for n, (_, _, members) in enumerate(keyed):
        index[members] = n
    arms = [[] for _ in range(len(nodes))]
    for k, e in enumerate(parts):
        e.a, e.b = int(index[2 * k]), int(index[2 * k + 1])
        e.xy = e.xy.copy()
        e.xy[0], e.xy[-1] = nodes[e.a], nodes[e.b]
        arms[e.a].append((k, 0))
        arms[e.b].append((k, 1))
    graph = graph_stage.Graph(edges=parts, nodes=nodes, arms=arms)
    graph.through = graph_stage._pair_through(graph)
    return graph, [k for k, _, _ in keyed]


def load_edges(area, box):
    """The surveyed sections meeting `box` (and the build area), with their OpenStreetMap knowledge and hand corrections, minus the
    dead-end stubs (graph_stage._drop_spurs), in a fixed order."""
    lo_box = np.array(box[:2]), np.array(box[2:])
    edges = []
    for feature in sources.read_tiles("roads", tiles_meeting(box)):
        p = feature["properties"]
        if p["nature"] in source.SKIP_NATURE or p.get("etat_de_l_objet") not in (None, "En service"):
            continue
        e = source.edge_from_feature(feature)
        lo, hi = e.xy.min(axis=0), e.xy.max(axis=0)
        if not meets(lo, hi, area.box) or not meets(lo, hi, box) or e.length < 1e-3:
            continue
        edges.append(e)
    edges.sort(key=lambda e: e.cleabs)
    if edges:
        reach = np.vstack([e.xy for e in edges])
        ways = osm.load(tiles_meeting((*(reach.min(axis=0) - osm.MATCH_DISTANCE), *(reach.max(axis=0) + osm.MATCH_DISTANCE))))
        osm.enrich(edges, ways)
    edges = source.apply_overrides(edges, source.load_overrides())
    return graph_stage._drop_spurs(edges)


def align_tile(job):
    """Pass 1 for one tile. Returns its stats; writes the parts and nodes it owns."""
    area, t, fresh = job
    clock = time.time()
    box = rect(t, ALIGN_HALO)
    parts = split_edges(load_edges(area, box))
    graph, keys = split_graph(parts)
    strokes = graph_stage.strokes(graph)
    owner_part = [part_tile(e.xy) for e in parts]
    node_owner = []
    for n, arms in enumerate(graph.arms):
        tiles = {owner_part[k] for k, _ in arms}
        node_owner.append(min(tiles, key=colour))
    path = tile_file(area.tag, "align", t)
    # what the tiles of earlier rounds already made
    earlier = sorted(o for o in {owner_part[k] for k in range(len(parts))} | set(node_owner) if colour(o) < colour(t) and o in area.tile_set)
    done = {o: read_pickle(tile_file(area.tag, "align", o)) for o in earlier}
    made_from = digest(parts, [done[o]["content"] for o in earlier], align_code(), [t, colour(t)])
    if not fresh and path.exists() and read_pickle(path)["key"] == made_from:
        return dict(tile=t, reused=True, seconds=round(time.time() - clock, 2))
    frozen, moved = alignment.round_roundabouts(graph)                  # on the surveyed lines: a ring a neighbour rounded comes out the same
    index = {part_key(e): k for k, e in enumerate(parts)}
    node_index = {key: n for n, key in enumerate(keys)}
    fixed, placed = {}, set(moved)
    for o in earlier:
        for key, e in done[o]["edges"].items():
            if key in index:
                fixed[index[key]] = e.xy
        for key, (xy, _) in done[o]["nodes"].items():
            if key in node_index:
                graph.nodes[node_index[key]] = xy
                placed.add(node_index[key])
    alignment.reattach(graph)
    for k, xy in fixed.items():
        parts[k].xy = xy.copy()
    kept = alignment.Kept(pass_dir(area.tag, "align") / f"{t[0]}_{t[1]}.kept", fresh)
    worst = alignment.smooth_strokes(graph, strokes, frozen | set(fixed), placed, kept, fixed)
    kept.save()
    mine = [k for k in range(len(parts)) if owner_part[k] == t]
    out_edges = {part_key(parts[k]): parts[k] for k in mine}
    out_nodes = {keys[n]: (graph.nodes[n].copy(), len(graph.arms[n])) for n in range(len(keys)) if node_owner[n] == t}
    through = [((part_key(parts[a[0]]), a[1]), (part_key(parts[b[0]]), b[1])) for a, b in graph.through.items()
               if node_owner[graph.node_of(a)] == t and a < b]
    ends = {part_key(parts[k]): (keys[parts[k].a], keys[parts[k].b]) for k in mine}
    write_pickle(path, dict(key=made_from, content=digest(out_edges, out_nodes, through, ends), edges=out_edges, nodes=out_nodes, through=through, ends=ends))
    return dict(tile=t, parts=len(mine), nodes=len(out_nodes), strokes_reused=kept.reused, worst_bound_ratio=round(worst, 3),
                seconds=round(time.time() - clock, 2))


def run_batched(function, area, tiles, jobs, fresh):
    """`function((area, tile, fresh))` for the tiles, side by side, in fresh worker processes every jobs x WORKER_TASKS tiles (a long-lived
    worker piles memory up; max_tasks_per_child hangs the pool when many tasks are queued)."""
    stats = []
    step = jobs * WORKER_TASKS
    for start in range(0, len(tiles), step):
        with ProcessPoolExecutor(jobs) as pool:
            for f in as_completed([pool.submit(function, (area, t, fresh)) for t in tiles[start:start + step]]):
                stats.append(f.result())
    return stats


def run_rounds(function, area, jobs, fresh, log, label):
    """`function((area, tile, fresh))` for every tile of the area, in four rounds of non-touching tiles."""
    stats = []
    for c in range(4):
        batch = [t for t in area.tiles if colour(t) == c]
        stats += run_batched(function, area, batch, jobs, fresh)
        log(f"    {label} round {c + 1}/4: {len(batch)} tiles")
    return stats


# ---------------------------------------------------------------- pass 2: network

NETWORK_NEAR = 700.0              # m: every link this close to the tile, or to an end of a link it owns, must be whole in its window
ACTIVE_REACH = 400.0              # m: the junction nodes this close to the tile or to the ends of its links are built (merges included)


def load_aligned(area, tiles):
    """The sections of the aligned tiles put back together. Returns ({cleabs: Edge (a, b = node keys)}, {node key: (xy, degree)},
    through pairs, keys of the nodes that lost an arm (a section not wholly loaded))."""
    parts, nodes, through, ends = {}, {}, [], {}
    for o in tiles:
        if o not in area.tile_set:
            continue
        d = read_pickle(tile_file(area.tag, "align", o))
        for (c, i), e in d["edges"].items():
            parts.setdefault(c, {})[i] = e
        ends.update(d["ends"])
        nodes.update(d["nodes"])
        through += d["through"]
    edges, cut = {}, set()
    for c, ps in parts.items():
        n = next(iter(ps.values())).tags["_part"][1]
        if len(ps) != n:
            cut |= {ends[(c, 0)][0]} if 0 in ps else set()
            cut |= {ends[(c, n - 1)][1]} if n - 1 in ps else set()
            continue
        xy = np.vstack([ps[0].xy] + [ps[i].xy[1:] for i in range(1, n)])
        e = replace(ps[0], xy=xy, tags={k: v for k, v in ps[0].tags.items() if k != "_part"})
        e.tags["_cuts"] = n
        e.a, e.b = ends[(c, 0)][0], ends[(c, n - 1)][1]
        edges[c] = e
    return edges, nodes, through, cut


def network_graph(edges, nodes, through, cut):
    """The graph of whole sections (node keys from the alignment). Returns (graph, node keys, indices of nodes that may miss arms)."""
    order = sorted(edges)
    keys = sorted({edges[c].a for c in order} | {edges[c].b for c in order})
    index = {k: i for i, k in enumerate(keys)}
    pos = np.zeros((len(keys), 2))
    arms = [[] for _ in keys]
    out = []
    for k, c in enumerate(order):
        e = replace(edges[c], xy=edges[c].xy.copy(), tags={key: v for key, v in edges[c].tags.items() if key != "_cuts"})
        e.a, e.b = index[e.a], index[e.b]
        pos[e.a], pos[e.b] = e.xy[0], e.xy[-1]
        arms[e.a].append((k, 0))
        arms[e.b].append((k, 1))
        out.append(e)
    edge_index = {c: k for k, c in enumerate(order)}

    def whole_end(c, i, end):
        """(edge index, end) of a part end that is an end of its whole section; None at a cut."""
        if c not in edge_index:
            return None
        if end == 0 and i == 0:
            return edge_index[c], 0
        if end == 1 and i == edges[c].tags["_cuts"] - 1:
            return edge_index[c], 1
        return None
    graph = graph_stage.Graph(edges=out, nodes=pos, arms=arms)
    graph.through = {}
    for (pa, ea), (pb, eb) in through:
        a, b = whole_end(*pa, ea), whole_end(*pb, eb)
        if a is not None and b is not None:
            graph.through[a], graph.through[b] = b, a
    missing = {index[k] for k in cut if k in index} | {n for n, k in enumerate(keys) if k not in nodes or nodes[k][1] != len(arms[n])}
    return graph, keys, missing


def link_key(graph, link):
    """Identity of a link across tiles: its first section and the way it is walked (the chain starts at the same end everywhere)."""
    k, rev = link.chain[0]
    return graph.edges[k].cleabs, bool(rev)


def link_owner(link):
    """The tile holding the middle of a link (by length)."""
    s = link.dense_s
    m = np.interp(0.5 * s[-1], s, np.arange(len(s)))
    i = int(np.floor(m))
    p = link.dense_xy[i] if i + 1 >= len(s) else link.dense_xy[i] + (m - i) * (link.dense_xy[i + 1] - link.dense_xy[i])
    return tile_of(*p)


def junction_owner(junction):
    return tile_of(*junction.centre)


def junction_key(junction, keys):
    return min(keys[n] for n in junction.nodes)


class Near:
    """Is a polyline within some distance of a tile or of a set of points (the ends of links)?"""

    def __init__(self, box, points):
        self.box = box
        self.tree = cKDTree(np.asarray(points, float).reshape(-1, 2)) if len(points) else None

    def __call__(self, xy, reach):
        x0, z0, x1, z1 = self.box
        dx = np.maximum(np.maximum(x0 - xy[:, 0], xy[:, 0] - x1), 0.0)
        dz = np.maximum(np.maximum(z0 - xy[:, 1], xy[:, 1] - z1), 0.0)
        if np.hypot(dx, dz).min() <= reach:
            return True
        return self.tree is not None and bool(np.isfinite(self.tree.query(xy, distance_upper_bound=reach)[0]).any())


def network_tile(job):
    """Pass 2 for one tile: the whole sections around it, its links and junctions. Writes what it owns, keyed."""
    from . import crossing, geometry, junction as junction_stage
    area, t, fresh = job
    clock = time.time()
    loaded = {(t[0] + di, t[1] + dj) for di in (-1, 0, 1) for dj in (-1, 0, 1)}
    for attempt in range(6):
        edges, nodes, through, cut = load_aligned(area, sorted(loaded))
        graph, keys, missing = network_graph(edges, nodes, through, cut)
        links = []
        for chain in graph_stage.links(graph):
            ends = graph_stage.chain_nodes(graph, chain)
            link = geometry.make_link(graph, chain, (ends[0], ends[-1]))
            link.whole = not any(n in missing for n in ends)
            links.append(link)
        owner = [link_owner(link) for link in links]
        owned = [k for k, link in enumerate(links) if owner[k] == t and link.whole]
        ends = [graph.nodes[n] for k in owned for n in links[k].nodes]
        near_test = Near(rect(t), ends)
        near = [k for k, link in enumerate(links) if near_test(link.dense_xy, NETWORK_NEAR)]
        broken = [k for k in near if not links[k].whole]
        if not broken:
            break
        grow = {tile_of(*graph.nodes[n]) for k in broken for n in links[k].nodes if n in missing}
        grow = {(g[0] + di, g[1] + dj) for g in grow for di in (-1, 0, 1) for dj in (-1, 0, 1)} & area.tile_set
        if grow <= loaded:
            break                                                        # the build area ends there
        loaded |= grow
    inputs = digest([read_pickle(tile_file(area.tag, "align", o))["content"] for o in sorted(loaded & area.tile_set)], network_code(), [t])
    path = tile_file(area.tag, "network", t)
    if not fresh and path.exists() and read_pickle(path)["key"] == inputs:
        return dict(tile=t, reused=True, seconds=round(time.time() - clock, 2))
    before = {k: links[k].dense_hw.copy() for k in owned}
    geometry.clamp_widths(links)
    narrowed = sum(not np.array_equal(before[k], links[k].dense_hw) for k in owned)
    # the junctions to build: around the tile, at the ends of its links, and at the ends of the links crossing them
    import shapely
    lines = [shapely.LineString(link.dense_xy) for link in links]
    partners = set()
    if owned:
        tree = shapely.STRtree(lines)
        for a, b in zip(*tree.query([lines[k] for k in owned], predicate="crosses")):
            partners.add(int(b))
    partners -= set(owned)
    reach = Near(rect(t), ends + [graph.nodes[n] for k in partners for n in links[k].nodes])
    junction_nodes = [n for n in range(len(graph.nodes)) if graph.is_junction(n)]
    active = {n for n in junction_nodes if n not in missing and reach(graph.nodes[[n]], ACTIVE_REACH)}
    junctions = junction_stage.build_junctions(graph, links, 1, active)
    jowner = [junction_owner(j) for j in junctions]
    mine_j = [i for i, j in enumerate(junctions) if jowner[i] == t]
    wanted = set(owned) | partners | {arm.link for i in mine_j for arm in junctions[i].arms} | {k for i in mine_j for k in junctions[i].internal}
    for k in sorted(wanted):
        geometry.sample(links[k])
    junction_stage.outline_all([junctions[i] for i in mine_j], graph, links, 1)
    crossing.flags([links[k] for k in sorted(wanted)], graph.edges)
    decide = [links[k] for k in sorted(set(owned) | partners)]
    tunnels = crossing.verify_tunnels(decide)
    crossed = crossing.separate(decide)
    # keyed records of what the tile owns
    lkey = {k: link_key(graph, links[k]) for k in range(len(links))}
    jkey = {i: junction_key(j, keys) for i, j in enumerate(junctions)}
    out_links, out_junctions, out_edges = {}, {}, {}
    for k in owned:
        link = links[k]
        rec = replace(link, chain=[(graph.edges[e].cleabs, rev) for e, rev in link.chain], nodes=tuple(keys[n] for n in link.nodes),
                      junction=[(jkey[j], jowner[j]) if j >= 0 else None for j in link.junction])
        rec.slim()
        rec.key, rec.owner = lkey[k], t
        out_links[rec.key] = rec
        for e, _ in link.chain:
            out_edges[graph.edges[e].cleabs] = graph.edges[e]
    for i in mine_j:
        j = junctions[i]
        j.slim()
        fans = {keys[n]: [replace(arm, link=(lkey[arm.link], owner[arm.link]), node=keys[n]) for arm in fan] for n, fan in j.fans.items()}
        rec = replace(j, nodes=[keys[n] for n in j.nodes], fans=fans, internal=[(lkey[k], owner[k]) for k in j.internal], corners={})
        rec.key, rec.owner = jkey[i], t
        out_junctions[rec.key] = rec
        for fan in j.fans.values():
            for arm in fan:
                e = links[arm.link].chain[0 if arm.end == 0 else -1][0]
                out_edges[graph.edges[e].cleabs] = graph.edges[e]
    # links of other tiles passing near this one (a long link owned far away), for the passes that look around a tile
    far = {lkey[k]: owner[k] for k, link in enumerate(links) if link.whole and abs(owner[k][0] - t[0]) + abs(owner[k][1] - t[1]) > 0
           and max(abs(owner[k][0] - t[0]), abs(owner[k][1] - t[1])) > 1 and Near(rect(t), [])(link.dense_xy, config.PROFILE_HALO)}
    write_pickle(path, dict(key=inputs, content=digest(out_links, out_junctions, out_edges, far), links=out_links, junctions=out_junctions, edges=out_edges, far=far,
                            nodes={keys[n]: graph.nodes[n] for n in {n for k in owned for n in links[k].nodes} | {n for i in mine_j for n in junctions[i].nodes}}))
    return dict(tile=t, links=len(out_links), junctions=len(out_junctions), invalid=sum(not j.valid for j in out_junctions.values()), narrowed=narrowed,
                swallowed=sum(link.internal for link in out_links.values()), loaded=len(loaded), **{f"crossing_{k}": v for k, v in crossed.items()},
                **tunnels, seconds=round(time.time() - clock, 2))


def run_all(function, area, jobs, fresh, log, label):
    """`function((area, tile, fresh))` for every tile of the area, side by side."""
    stats = run_batched(function, area, area.tiles, jobs, fresh)
    log(f"    {label}: {len(stats)} tiles")
    return stats


# ---------------------------------------------------------------- local networks

class Records:
    """Pass 2 records read from tile files (each file read once), and their references followed one level."""

    def __init__(self, area):
        self.area, self.files = area, {}

    def file(self, t):
        if t not in self.files:
            path = tile_file(self.area.tag, "network", t)
            self.files[t] = read_pickle(path) if t in self.area.tile_set and path.exists() else dict(links={}, junctions={}, edges={}, far={}, nodes={})
        return self.files[t]

    def around(self, t, reach=1):
        """Links and junctions of the tiles within `reach` of t, the far links passing near them, and what their junctions'
        arms and their links' ends refer to."""
        tiles = [(t[0] + di, t[1] + dj) for di in range(-reach, reach + 1) for dj in range(-reach, reach + 1)]
        links, junctions = {}, {}
        for o in tiles:
            f = self.file(o)
            links.update(f["links"])
            junctions.update(f["junctions"])
        for key, o in self.file(t)["far"].items():
            links.setdefault(key, self.file(o)["links"].get(key))
        for j in list(junctions.values()):
            for arm in (a for fan in j.fans.values() for a in fan):
                key, o = arm.link
                if key not in links:
                    links[key] = self.file(o)["links"].get(key)
        for link in list(links.values()):
            for ref in link.junction if link is not None else []:
                if ref is not None and ref[0] not in junctions:
                    junctions[ref[0]] = self.file(ref[1])["junctions"].get(ref[0])
        return {k: v for k, v in links.items() if v is not None}, {k: v for k, v in junctions.items() if v is not None}

    def edges(self, links, junctions):
        out = {}
        for link in links.values():
            f = self.file(link.owner)["edges"]
            for c, _ in link.chain:
                out[c] = f[c]
        for j in junctions.values():
            f = self.file(j.owner)["edges"]
            for arm in (a for fan in j.fans.values() for a in fan):
                ref = links.get(arm.link[0])
                if ref is not None:
                    c = ref.chain[0 if arm.end == 0 else -1][0]
                    out[c] = f.get(c) or self.file(ref.owner)["edges"][c]
        return out


def assemble(area, links, junctions, edges, z=None):
    """A `build.Network` of keyed records: edges, links and junctions in key order, every reference turned into an index (-1 where
    the record it names is not among them). `z`: {link key: (z, tilt, ground)} and {junction key: plane} from the profile pass."""
    from .build import Network
    edge_order = sorted(edges)
    eindex = {c: k for k, c in enumerate(edge_order)}
    lorder, jorder = sorted(links), sorted(junctions)
    lindex, jindex = {k: i for i, k in enumerate(lorder)}, {k: i for i, k in enumerate(jorder)}
    node_keys = sorted({n for link in links.values() for n in link.nodes} | {n for j in junctions.values() for n in j.nodes})
    nindex = {k: i for i, k in enumerate(node_keys)}
    pos = np.zeros((len(node_keys), 2))
    out_edges = [replace(edges[c]) for c in edge_order]
    for link in links.values():
        pos[nindex[link.nodes[0]]], pos[nindex[link.nodes[1]]] = link.xy[0], link.xy[-1]
    for j in junctions.values():
        for n in j.nodes:
            pass
    out_links = []
    for key in lorder:
        rec = links[key]
        link = replace(rec, chain=[(eindex[c], rev) for c, rev in rec.chain], nodes=tuple(nindex[n] for n in rec.nodes),
                       junction=[jindex.get(ref[0], -1) if ref is not None else -1 for ref in rec.junction])
        link.key, link.owner = key, rec.owner
        if z is not None and key in z[0]:
            link.z, link.tilt, link.ground = z[0][key]
        out_links.append(link)
    out_junctions = []
    for key in jorder:
        rec = junctions[key]
        fans = {nindex[n]: [replace(arm, link=lindex.get(arm.link[0], -1), node=nindex[n]) for arm in fan] for n, fan in rec.fans.items()}
        j = replace(rec, nodes=[nindex[n] for n in rec.nodes], fans=fans, internal=[lindex[k] for k, _ in rec.internal if k in lindex])
        j.key, j.owner = key, rec.owner
        for n in rec.nodes:
            if not pos[nindex[n]].any():
                pos[nindex[n]] = rec.centre
        if z is not None and key in z[1]:
            j.plane = z[1][key]
        out_junctions.append(j)
    return Network(tag=area.tag, sectors=area.sectors, edges=out_edges, nodes=pos, links=out_links, junctions=out_junctions)


# ---------------------------------------------------------------- pass 3: heights

def profile_tile(job):
    """Pass 3 for one tile: the height solve of profile.py for the tile and its halo, the neighbours of earlier rounds fixed."""
    from . import profile
    area, t, fresh = job
    clock = time.time()
    records = Records(area)
    links, junctions = records.around(t)
    network = assemble(area, links, junctions, records.edges(links, junctions))
    samples = profile.Samples(network)
    n = samples.offsets[-1]
    known_z, known_t = np.full(n, np.nan), np.zeros(n)
    known_plane = np.full((len(network.junctions), 3), np.nan)
    p = (t[0] - 5, t[1] - 5)                                             # profile.py numbers tiles from the local origin
    lindex = {link.key: k for k, link in enumerate(network.links)}
    jindex = {j.key: i for i, j in enumerate(network.junctions)}
    for o in sorted({(t[0] + di, t[1] + dj) for di in (-1, 0, 1) for dj in (-1, 0, 1)} & area.tile_set):
        if colour(o) >= colour(t):
            continue
        done = read_pickle(tile_file(area.tag, "profile", o))
        for key, (idx, z, tilt, _) in done["links"].items():
            if key in lindex:
                k = lindex[key]
                known_z[samples.offsets[k] + idx], known_t[samples.offsets[k] + idx] = z, tilt
        for key, plane in done["planes"].items():
            if key in jindex:
                known_plane[jindex[key]] = plane
    block = profile.make_block(samples, p, known_z, known_plane, known_t)
    key = digest(profile.block_key(block), code_stamp(Records, assemble, profile_tile))
    path = tile_file(area.tag, "profile", t)
    if not fresh and path.exists():
        done = read_pickle(path)
        if done["key"] == key:
            return dict(tile=t, reused=True, status=done["status"], seconds=round(time.time() - clock, 2))
    result = profile.solve_block(block)
    own = block["own"]
    ids, z, tilt, ground = block["ids"][own], result["z"][own], result["t"][own], result["ground"][own]
    which = np.searchsorted(samples.offsets, ids, side="right") - 1
    out_links = {}
    for k in np.unique(which):
        m = which == k
        out_links[network.links[k].key] = (ids[m] - samples.offsets[k], z[m], tilt[m], ground[m])
    mine = (samples.junction_tile[block["junctions"]] == np.array(p)).all(axis=1) if len(block["junctions"]) else np.zeros(0, bool)
    planes = {network.junctions[i].key: result["planes"][b] for b, i in enumerate(block["junctions"]) if mine[b]}
    write_pickle(path, dict(key=key, content=digest(out_links, planes), links=out_links, planes=planes, status=result["status"]))
    return dict(tile=t, samples=int(own.sum()), status=result["status"], seconds=round(time.time() - clock, 2))


# ---------------------------------------------------------------- pass 4: lanes, lane graph, surface

ID_BITS = 15                      # lane graph elements of a tile are numbered (tile code << ID_BITS) + rank: at most 32,768 per tile


def tile_code(area, t):
    """A tile's number in the build area (row by row from the south-west of its tiles)."""
    x0, z0 = min(a for a, _ in area.tiles), min(b for _, b in area.tiles)
    width = max(a for a, _ in area.tiles) - x0 + 1
    return (t[0] - x0) + width * (t[1] - z0)


def heights(area, links, junctions, files=None):
    """({link key: (z, tilt, ground)} for the links whose every sample has a height, {junction key: plane}). `files`: a dict that
    keeps the profile files read (tile -> content)."""
    files = {} if files is None else files

    def done(o):
        if o not in files:
            path = tile_file(area.tag, "profile", o)
            files[o] = read_pickle(path) if o in area.tile_set and path.exists() else dict(links={}, planes={})
        return files[o]
    z = {}
    for key, link in links.items():
        cells = np.unique(np.floor((link.xy + HALF) / TILE).astype(int), axis=0)
        tiles = {tuple(c) for c in cells.tolist()} | {ref[1] for ref in link.junction if ref is not None}
        out = [np.full(len(link.s), np.nan) for _ in range(3)]
        for o in tiles:
            got = done(o)["links"].get(key)
            if got is not None:
                idx = got[0]
                for a, v in zip(out, got[1:]):
                    a[idx] = v
        if np.isfinite(out[0]).all():
            z[key] = (out[0], out[1], out[2])
    planes = {}
    for key, j in junctions.items():
        p = done(tile_of(*j.centre))["planes"].get(key)
        if p is not None:
            planes[key] = p
    return z, planes


def surface_tile(job):
    """Pass 4 for one tile: lanes of its links (and of the arms of its junctions), the lane graph elements it owns (references to
    another tile's elements by what they belong to, resolved in `number_tile`), its road pieces and junction meshes."""
    from . import controls as controls_stage, lanegraph, lanes, metrics, surface
    area, t, fresh = job
    clock = time.time()
    records = Records(area)
    links, junctions = records.around(t)
    profiles = {}
    z, planes = heights(area, links, junctions, profiles)
    window = sorted({(t[0] + di, t[1] + dj) for di in (-1, 0, 1) for dj in (-1, 0, 1)})
    lo, hi = np.array(rect(t)[:2]) - TILE, np.array(rect(t)[2:]) + TILE               # the rasters the sight distances may read
    terrain = rasters.stamp(lo[0], lo[1], hi[0] - lo[0], hi[1] - lo[1], kinds=("mnt", "mnh"))
    made_from = digest([f.get("content") for _, f in sorted(records.files.items())], [f.get("content") for _, f in sorted(profiles.items())],
                       [(k, w, sources.stamp(k, *w)) for k in ("osm_controls", "osm_restrictions", "non_communication") for w in window],
                       terrain, surface_code(), [t, tile_code(area, t)])
    path = tile_file(area.tag, "surface", t)
    if not fresh and path.exists() and read_pickle(path).get("key") == made_from:
        return dict(tile=t, reused=True, seconds=round(time.time() - clock, 2))
    links = {k: v for k, v in links.items() if k in z}
    network = assemble(area, links, junctions, records.edges(links, junctions), (z, planes))
    own_l = [k for k, link in enumerate(network.links) if link.owner == t]
    own_j = [i for i, j in enumerate(network.junctions) if j.owner == t and j.plane is not None]
    ends_j = {j for k in own_l for j in network.links[k].junction if j >= 0 and network.junctions[j].plane is not None}
    connect = set(own_j) | ends_j                                       # its links' lanes lead into the connectors of the junctions at their ends,
    need = set(own_l) | {a.link for i in connect for a in network.junctions[i].arms if a.link >= 0}      # wherever those are owned
    if own_l:                                                            # lanes ending inside an owned link turn round onto lanes up to 12 m away
        import shapely
        heads = shapely.STRtree([shapely.Point(*link.xy[0]) for link in network.links] + [shapely.Point(*link.xy[-1]) for link in network.links])
        for k in own_l:
            for i in heads.query(shapely.LineString(network.links[k].xy).buffer(15.0)):
                need.add(int(i) % len(network.links))
    for k, link in enumerate(network.links):
        if k not in need:
            link.lanes = None
    stats = lanes.layout_all([network.links[k] for k in sorted(need)], network.edges)
    elements, elements_stats = lanegraph.build(network, controls_stage.load(window), connect)
    # numbering: what each element belongs to, and its rank among the elements made for it
    kinds = {"link": network.links, "junction": network.junctions}
    sig, count = [], {}
    for e in elements:
        obj = kinds[e.owner[0]][e.owner[1]]
        ordinal = count.get(e.owner, 0)
        count[e.owner] = ordinal + 1
        sig.append((e.owner[0], obj.key, ordinal))
    owned = [i for i, e in enumerate(elements) if kinds[e.owner[0]][e.owner[1]].owner == t]
    owned.sort(key=lambda i: sig[i])
    base = tile_code(area, t) << ID_BITS
    assert len(owned) < 1 << ID_BITS, f"tile {t}: {len(owned)} lane elements"
    ids = {sig[i]: base + r for r, i in enumerate(owned)}
    jnumber = {j.key: base + r for r, j in enumerate(sorted((network.junctions[i] for i in own_j), key=lambda j: j.key))}
    out = []
    for i in owned:
        e = replace(elements[i])
        e.id = ids[sig[i]]
        e.succ, e.yields = [sig[s] for s in e.succ], [sig[y] for y in e.yields]
        e.left, e.right = (sig[e.left] if e.left >= 0 else None), (sig[e.right] if e.right >= 0 else None)
        e.junction = jnumber.get(network.junctions[e.junction].key, -1) if e.junction >= 0 else -1
        e.owner = sig[i][:2]
        out.append(e)
    pieces = surface.pieces(network, own_l)
    for p in pieces:
        p.link = network.links[p.link].key
    seal_mouths(network, [i for i, j in enumerate(network.junctions) if j.owner == t])
    meshes = surface.junction_meshes(network, [i for i, j in enumerate(network.junctions) if j.owner == t])
    mine_j = [i for i, j in enumerate(network.junctions) if j.owner == t]
    names = {lanegraph.LANE: "lanes", lanegraph.CHANGE: "changes", lanegraph.CONNECTOR: "connectors", lanegraph.UTURN: "uturns"}
    graph_stats = {name: sum(1 for e in out if e.kind == k) for k, name in names.items()}
    graph_stats.update({k: sum(elements_stats["per_junction"].get(i, {}).get(k, 0) for i in own_j) for k in ("restricted", "by_turn_lanes", "around_islands")},
                       elements=len(out),
                       yields=sum(len(e.yields) for e in out),
                       controls={name: sum(1 for e in out if e.kind == lanegraph.CONNECTOR and e.control == c) for c, name in enumerate(lanegraph.CONTROL_NAMES)})
    report = dict(surface=metrics.surface_parts(network, set(own_l), set(mine_j)), classes=metrics.profile_parts(network, own_l, mine_j),
                  lanes=stats, lanegraph=graph_stats)
    write_pickle(path, dict(key=made_from, pieces=pieces, meshes=meshes, elements=out, report=report))
    write_pickle(tile_file(area.tag, "ids", t), ids)
    return dict(tile=t, pieces=len(pieces), meshes=len(meshes), elements=len(out), seconds=round(time.time() - clock, 2))


MOUTH_SNAP = 1.5                  # m: a junction vertex this close to an arm's mouth corner is that corner


def seal_mouths(network, which):
    """Put the mouth corners of these junctions exactly on the edge points of their arms' links as the links' owners made them. A
    junction's owner outlined it with its own copy of a link owned by another tile, which can differ in the last digits (a few
    millimetres; more where the copy's far end saw another neighbourhood): the road would not end on the junction's vertices."""
    from . import junction as junction_stage
    for i in which:
        j = network.junctions[i]
        if j.vertices is None or j.boundary is None:
            continue
        corners = [junction_stage.mouth_corners(network.links[a.link], a) for a in j.arms if a.link >= 0 and not a.swallowed]
        if not corners:
            continue
        v = j.vertices.copy()
        for corner in (c for pair in corners for c in pair):                    # mouths flagged or not (an invalid junction's are not)
            d = np.hypot(*(v - corner).T)
            k = int(np.argmin(d))
            if d[k] < MOUTH_SNAP:
                v[k] = corner
        j.vertices = v


NUMBER_REACH = 2                  # tiles: an element refers to elements of tiles at most this far (connectors at its links' ends)


def number_tile(job):
    """Pass 4b: the references of a tile's elements to elements of other tiles, from what they belong to to their numbers."""
    area, t, fresh = job
    path = tile_file(area.tag, "surface", t)
    near = sorted({(t[0] + di, t[1] + dj) for di in range(-NUMBER_REACH, NUMBER_REACH + 1) for dj in range(-NUMBER_REACH, NUMBER_REACH + 1)} & area.tile_set)
    made_from = digest(sources.content_hash(path), [sources.content_hash(tile_file(area.tag, "ids", o)) for o in near], code_stamp(number_tile))
    out_path = tile_file(area.tag, "lanes", t)
    if not fresh and out_path.exists() and read_pickle(out_path).get("key") == made_from:
        return dict(tile=t, reused=True, unresolved=read_pickle(out_path)["unresolved"])
    done = read_pickle(path)
    ids, files = dict(read_pickle(tile_file(area.tag, "ids", t))), {}
    missing = 0

    def resolve(s):
        nonlocal missing
        if s is None:
            return -1
        if s not in ids:
            for o in near:
                if o not in files:
                    files[o] = read_pickle(tile_file(area.tag, "ids", o))
                if s in files[o]:
                    ids[s] = files[o][s]
                    break
        if s not in ids:
            missing += 1
            return None
        return ids[s]
    for e in done["elements"]:
        e.succ = [i for i in (resolve(s) for s in e.succ) if i is not None]
        e.yields = [i for i in (resolve(s) for s in e.yields) if i is not None]
        e.left, e.right = (resolve(e.left) if e.left is not None else -1), (resolve(e.right) if e.right is not None else -1)
        e.left, e.right = -1 if e.left is None else e.left, -1 if e.right is None else e.right
    report = done["report"]
    report["lanegraph"]["no_exit"] = sum(1 for e in done["elements"] if not e.succ)
    write_pickle(out_path, dict(key=made_from, unresolved=missing, pieces=done["pieces"], meshes=done["meshes"], elements=done["elements"], report=report))
    return dict(tile=t, unresolved=missing)


def align_code():
    return code_stamp(alignment, graph_stage, source, osm, border_cuts, part_tile, split_edges, part_key, node_key, split_graph, load_edges, align_tile)


def network_code():
    from . import crossing, geometry, junction as junction_stage, lanes
    return code_stamp(junction_stage, geometry, crossing, graph_stage, lanes, load_aligned, network_graph, link_key, link_owner, junction_owner,
                      junction_key, Near, network_tile)


def surface_code():
    from . import controls, lanegraph, lanes, metrics, surface
    return code_stamp(controls, lanegraph, lanes, metrics, surface, Records, assemble, heights, seal_mouths, tile_code, surface_tile)


# ---------------------------------------------------------------- the build

def build(tag, sectors, margin, jobs=6, fresh=False, log=print):
    """Every pass over the area's tiles. Returns the build report (the same sections as build.build's)."""
    from . import metrics
    area = Area(tag, sectors, margin)
    report, clock = {}, time.time()

    def done(stage, **stats):
        nonlocal clock
        report[stage] = dict(stats, seconds=round(time.time() - clock, 1))
        log(f"  {stage:<10} {report[stage]}")
        clock = time.time()

    align = run_rounds(align_tile, area, jobs, fresh, log, "align")
    done("alignment", tiles=len(align), parts=sum(s.get("parts", 0) for s in align), nodes=sum(s.get("nodes", 0) for s in align),
         reused_tiles=sum(bool(s.get("reused")) for s in align), worst_bound_ratio=max((s.get("worst_bound_ratio", 0.0) for s in align), default=0.0))
    net = run_all(network_tile, area, jobs, fresh, log, "network")
    keys = ("links", "junctions", "invalid", "narrowed", "swallowed", "crossing_crossings", "crossing_bridges_added", "crossing_bridges_dropped",
            "crossing_by_lidar", "crossing_by_osm", "crossing_by_survey", "tunnel_runs", "tunnels_kept", "tunnels_dropped", "tunnel_m")
    done("junction", **{k: sum(s.get(k, 0) for s in net) for k in keys}, reused_tiles=sum(bool(s.get("reused")) for s in net),
         most_tiles_loaded=max((s.get("loaded", 0) for s in net), default=0))
    prof = run_rounds(profile_tile, area, jobs, fresh, log, "profile")
    statuses = {}
    for s in prof:
        statuses[s["status"]] = statuses.get(s["status"], 0) + 1
    done("profile", tiles=len(prof), samples=sum(s.get("samples", 0) for s in prof), statuses=statuses, reused_tiles=sum(bool(s.get("reused")) for s in prof))
    surf = run_all(surface_tile, area, jobs, fresh, log, "surface")
    numbered = run_all(number_tile, area, jobs, fresh, log, "numbers")
    parts = [read_pickle(tile_file(tag, "lanes", t))["report"] for t in area.tiles]
    lane_stats, graph_stats = {}, {}
    for p in parts:
        for k, v in p["lanes"].items():
            if isinstance(v, (int, float)):
                lane_stats[k] = round(lane_stats.get(k, 0) + v, 1)
        for k, v in p["lanegraph"].items():
            if isinstance(v, (int, float)) and k not in ("worst_join_deg", "turn_radius_p10"):
                graph_stats[k] = graph_stats.get(k, 0) + v
            elif k == "worst_join_deg":
                graph_stats[k] = max(graph_stats.get(k, 0.0), v)
            elif k == "controls":
                c = graph_stats.setdefault(k, {})
                for name, n in v.items():
                    c[name] = c.get(name, 0) + n
    done("lanegraph", **graph_stats, lanes_stage=lane_stats, unresolved_references=sum(s["unresolved"] for s in numbered),
         elements_owned=sum(s.get("elements", 0) for s in surf))
    report["surface"] = metrics.merge_surface([p["surface"] for p in parts])
    report["classes"] = metrics.merge_profile([p["classes"] for p in parts])
    return area, report


def load_sector(tag, si, sj, reach):
    """(pieces, junction meshes, lane graph elements) within `reach` of a sector, from the tiles that own them."""
    t = (si, sj)
    lo = np.array([-HALF + si * TILE, -HALF + sj * TILE]) - reach
    hi = lo + TILE + 2 * reach
    tiles = {(si + di, sj + dj) for di in (-1, 0, 1) for dj in (-1, 0, 1)}
    net = tile_file(tag, "network", t)
    if net.exists():
        tiles |= set(read_pickle(net)["far"].values())
    pieces, meshes, elements = [], [], []
    for o in sorted(tiles):
        path = tile_file(tag, "lanes", o)
        if not path.exists():
            continue
        d = read_pickle(path)
        pieces += [p for p in d["pieces"] if meets(p.xy.min(axis=0), p.xy.max(axis=0), (*lo, *hi))]
        meshes += [(j, v) for j, v in d["meshes"] if meets(v[:, :2].min(axis=0), v[:, :2].max(axis=0), (*lo, *hi))]
        elements += [e for e in d["elements"] if meets(e.xyz[:, :2].min(axis=0), e.xyz[:, :2].max(axis=0), (*lo, *hi))]
    pieces.sort(key=lambda p: (p.link, float(p.s[0])))
    meshes.sort(key=lambda m: m[0].key)
    elements.sort(key=lambda e: e.id)
    return pieces, meshes, elements
