"""Facade layout of a building, made offline so it can be measured: which walls it has, which are shared with a neighbour, the ground
at each wall, its floors, and where every window and door goes. The game only draws it (Facade.cs), in a style from the building's
kind, era and wall material.

  walls       consecutive edges of the outline that are nearly collinear (WALL_ANGLE, WALL_DEVIATION) are one wall: a side that
              `simplify` left with a jog still gets its windows
  party       an edge lying along another building's (PARTY_DISTANCE) over most of its length is a shared wall: blind (and a
              gable in the roof, so terraced houses keep one ridge)
  ground      the terrain at both ends of every wall; openings sit on the local ground, not on the front's
  floors      BD TOPO `nombre_d_etages`, OSM `building:levels`, else the wall height
  openings    every free wall of MIN_WALL or more gets at least one opening per floor, in columns aligned from floor to floor; the
              front has the door and the most bays, side walls a centred single or a symmetric pair, back walls drop at most one
              bay per floor; walls are blind only where the data says so (shared, shed, barn, light construction)
  era         BD TOPO `date_d_apparition`: before 1950 village (tall windows, shutters), after 1970 pavillon (wide windows, roller
              shutters, a garage door facing the road)
  finish      BD TOPO `materiaux_des_murs`: 1 stone, 2 millstone, 3 concrete, 4 brick, 5 breeze block, 6 wood, 9 other
  seed        from `cleabs`: the same building keeps its look when its outline moves a little
"""
import hashlib

import numpy as np
import shapely
from shapely.geometry import LineString

WALL_ANGLE = np.radians(15.0)
WALL_DEVIATION = 0.25
PARTY_DISTANCE = 0.5
PARTY_SHARE = 0.5
MIN_WALL = 2.0
DOOR_MIN = 1.8          # m: the lowest door drawn
WINDOW, DOOR, GARAGE, SHOPFRONT, BALCONY = 0, 1, 2, 3, 4
FRONT, PARTY, BLIND, BACK = 1, 2, 4, 8
BLIND_KINDS = {"shed", "barn", "silo", "greenhouse"}
SHOP_KINDS = {"pharmacy", "bakery", "grocery", "restaurant", "bar", "post", "shop"}
STRIP_KINDS = {"school", "library", "hall", "clinic", "industrial"}
ARCHED_KINDS = {"church", "chapel"}
FLOOR_HEIGHT = {"industrial": 4.5, "church": 4.6, "chapel": 4.6, "townhall": 3.6, "school": 3.6, "library": 3.5, "hall": 3.5, "clinic": 3.5, "shop": 3.6}


def seed_of(cleabs):
    return int(hashlib.md5(str(cleabs).encode()).hexdigest()[:8], 16) & 0x7FFFFFFF


def era_of(props):
    """0 unknown, 1 before 1950, 2 1950 - 1970, 3 after 1970."""
    text = str(props.get("date_d_apparition") or "")
    if not text[:4].isdigit():
        return 0
    year = int(text[:4])
    return 1 if year < 1950 else 2 if year <= 1970 else 3


def material_of(props):
    """The main wall material, 255 if unknown. The code is two material digits in no fixed order ("40" and "04" both occur, 0 being
    none): the first that is not 0 is taken."""
    code = str(props.get("materiaux_des_murs") or "")
    named = [int(c) for c in code if c.isdigit() and c != "0"]
    return named[0] if named else 255


def walls(ring):
    """[(first edge, edge count)] of an outline (counter-clockwise, without its closing point): runs of nearly collinear edges."""
    n = len(ring)
    d = np.roll(ring, -1, axis=0) - ring
    heading = np.arctan2(d[:, 1], d[:, 0])
    turn = np.abs((np.roll(heading, -1) - heading + np.pi) % (2 * np.pi) - np.pi)       # at the end of each edge
    if (turn > WALL_ANGLE).sum() == 0:
        return [(0, n)]
    start = int(np.argmax(turn > WALL_ANGLE)) + 1                                         # begin after a real corner
    out, first, count = [], start % n, 0
    for k in range(n):
        e = (start + k) % n
        count += 1
        a, b = ring[first], ring[(e + 1) % n]
        chord = b - a
        length = max(np.hypot(*chord), 1e-9)
        inner = ring[[(first + j) % n for j in range(1, count)]] if count > 1 else np.zeros((0, 2))
        deviation = np.abs((inner - a) @ np.array([-chord[1], chord[0]]) / length).max() if len(inner) else 0.0
        if turn[e] > WALL_ANGLE or deviation > WALL_DEVIATION or k == n - 1:
            if deviation > WALL_DEVIATION and count > 1:                                   # the last edge bent it: it starts the next wall
                out.append((first, count - 1))
                first, count = e, 1
                if turn[e] > WALL_ANGLE or k == n - 1:
                    out.append((first, count))
                    first, count = (e + 1) % n, 0
            else:
                out.append((first, count))
                first, count = (e + 1) % n, 0
    return out


def party_edges(ring, neighbours):
    """Per edge: does it lie along another building (within PARTY_DISTANCE over PARTY_SHARE of its length)?"""
    n = len(ring)
    out = np.zeros(n, bool)
    if not neighbours:
        return out
    near = shapely.union_all([q.boundary.buffer(PARTY_DISTANCE) for q in neighbours])
    for i in range(n):
        edge = LineString([ring[i], ring[(i + 1) % n]])
        if edge.length > 0.3 and edge.intersection(near).length >= PARTY_SHARE * edge.length:
            out[i] = True
    return out


def floors_of(props, osm_levels, wall_height, kind):
    floor_h = FLOOR_HEIGHT.get(kind, 2.9)
    for value in (props.get("nombre_d_etages"), osm_levels):
        try:
            f = int(float(value))
        except (TypeError, ValueError):
            continue
        if 1 <= f <= 30 and f * 2.3 <= wall_height + 0.6:
            return f, max(min(floor_h, (wall_height - 0.4) / f), 2.3)
    return max(1, int(round((wall_height - 0.4) / floor_h))), floor_h


def layout(ring, wall_runs, party, ground_at, eave, kind, floors, floor_h, front_edge, era, seed, apartment=False, light=False):
    """Openings of every wall: [dict(first, count, flags, g0, g1, openings=[(t, floor, type, width, height, sill)])] (t: metres along
    the wall from its first point; sill: height of the opening's bottom above the local ground)."""
    rng = np.random.default_rng(seed)
    n = len(ring)
    blind_kind = kind in BLIND_KINDS or light
    centre = ring.mean(axis=0)
    fx = 0.5 * (ring[front_edge] + ring[(front_edge + 1) % n])
    front_dir = (fx - centre) / max(np.hypot(*(fx - centre)), 1e-9)
    out = []
    for first, count in wall_runs:
        pts = ring[[(first + j) % n for j in range(count + 1)]]
        seg = np.hypot(*np.diff(pts, axis=0).T)
        length = float(seg.sum())
        g0, g1 = float(ground_at(pts[:1])[0]), float(ground_at(pts[-1:])[0])
        edges = [(first + j) % n for j in range(count)]
        flags = 0
        if front_edge in edges:
            flags |= FRONT
        mid = 0.5 * (pts[0] + pts[-1])
        if not flags & FRONT and np.dot(mid - centre, front_dir) < -0.3 * np.hypot(*(mid - centre)):
            flags |= BACK
        if seg[np.array([party[e] for e in edges])].sum() >= PARTY_SHARE * length:
            flags |= PARTY
        if blind_kind or flags & PARTY:
            flags |= BLIND
        wall = dict(first=int(first), count=int(count), flags=flags, g0=g0, g1=g1, openings=[])
        out.append(wall)
        if length < 1.4 or kind in ("silo", "greenhouse") or flags & PARTY:
            continue
        if blind_kind:                                                                   # sheds and barns: a door to the road, else a small window
            if flags & FRONT and length > 2.6 and eave - max(g0, g1) - 0.3 >= DOOR_MIN:
                w = min(length - 1.0, 4.2 if kind == "barn" else 2.6)
                wall["openings"].append((length * float(rng.uniform(0.35, 0.65)), 0, GARAGE, w, min(eave - max(g0, g1) - 0.3, 3.6 if kind == "barn" else 2.2), 0.0))
            elif length > 3.2:
                room = eave - max(g0, g1) - 0.25                                        # a small window under the eave
                if room >= 1.2:
                    wall["openings"].append((length * 0.5, 0, WINDOW, 0.7, 0.7, min(1.2, room - 0.7)))
            continue
        arched, strip = kind in ARCHED_KINDS, kind in STRIP_KINDS
        win_w = 0.85 if era == 1 else 1.25 if era == 3 else 1.0
        win_h = 1.45 if era == 1 else 1.25 if era == 3 else 1.35
        if strip:
            win_w, win_h = 2.2, 1.5
        if arched:
            win_w, win_h = 0.95, float(np.clip((eave - min(g0, g1)) * 0.42, 2.6, 5.2))
        bay = 4.2 if arched else 3.6 if strip else 3.0 + 0.8 * rng.random()
        margin = 1.2 if arched else 0.6 if length < 3.0 else 0.85
        if flags & FRONT:
            bays = max(1, int(np.floor((length - 2 * margin) / bay + 0.5)))
        elif flags & BACK:
            bays = max(1, int(np.floor((length - 2 * margin) / bay + 0.5)))
        else:                                                                            # side walls: a centred single or a symmetric pair
            bays = 1 if length < 6.5 else 2
        win_w = min(win_w, max(length - 2 * min(margin, 0.3), 0.5))
        span = max(length - 2 * margin, win_w)
        columns = [margin + (k + 0.5) * span / bays + float(rng.uniform(-0.1, 0.1)) for k in range(bays)]
        columns = [float(np.clip(c, win_w / 2 + 0.15, length - win_w / 2 - 0.15)) for c in columns]
        door_col = len(columns) // 2 if flags & FRONT else -1
        shop = kind in SHOP_KINDS and flags & FRONT
        garage = kind == "house" and era == 3 and flags & FRONT and length >= 7.0 and not shop
        dropped = int(rng.integers(len(columns))) if flags & BACK and len(columns) >= 3 else -1
        for f in range(floors if not arched else 1):
            for k, t in enumerate(columns):
                ground = g0 + (g1 - g0) * t / max(length, 1e-9)
                base = f * floor_h
                if f == 0 and shop:
                    if k == 0:
                        wall["openings"].append((length / 2, 0, SHOPFRONT, max(length - 1.0, 1.5), min(2.35, eave - ground - 0.9), 0.2))
                    continue
                room = eave - ground - 0.25                                              # a door's top stays under the eave; else a window
                if f == 0 and k == door_col and room >= DOOR_MIN:
                    wall["openings"].append((t, 0, DOOR, 1.5 if arched else 1.0, min(2.6 if arched else 2.1, room), 0.0))
                    continue
                if f == 0 and garage and k == len(columns) - 1 and len(columns) >= 2 and room >= DOOR_MIN:
                    wall["openings"].append((t, 0, GARAGE, min(2.6, span / len(columns) - 0.4), min(2.2, room), 0.0))
                    continue
                if k == dropped:
                    continue
                sill = base + (max(1.6, (eave - ground) * 0.25) if arched else 0.95 if f == 0 else 0.9)
                if ground + sill + win_h > eave - 0.35:
                    if f == 0:                                                           # low wall: a smaller window still fits
                        h = eave - ground - 0.35 - 0.6
                        if h >= 0.5:
                            wall["openings"].append((t, 0, WINDOW, win_w, h, 0.6))
                    continue
                kind_ = BALCONY if apartment and f > 0 and flags & FRONT else WINDOW
                wall["openings"].append((t, f, kind_, win_w, win_h, sill))
    return out


LOW_WALL = 1.45      # m of wall above the local ground: under this no window fits (0.6 sill, 0.5 glass, 0.35 to the eave)


def bare_floors(walls_, ring, eave, floors, floor_h, kind, light=False):
    """Upper floors of free walls of MIN_WALL or more that have room for a window (WINDOW_ROOM) yet none: the per-floor half of the metric."""
    if kind in BLIND_KINDS | ARCHED_KINDS or light:
        return 0
    n, out = len(ring), 0
    for w in walls_:
        pts = ring[[(w["first"] + j) % n for j in range(w["count"] + 1)]]
        if w["flags"] & PARTY or np.hypot(*np.diff(pts, axis=0).T).sum() < MIN_WALL:
            continue
        lit = {o[1] for o in w["openings"]}
        out += sum(1 for f in range(1, floors) if max(w["g0"], w["g1"]) + f * floor_h + WINDOW_ROOM <= eave and f not in lit)
    return out


WINDOW_ROOM = 0.9 + 1.45 + 0.35          # sill, the tallest window, lintel to the eave


def unlit(walls_, ring, eave, kind, light=False):
    """(free walls of MIN_WALL or more with room for a window, those with no opening on the ground floor, low walls left out): the
    report's facade metric. Low walls are where the terrain rises almost to the eave (a building dug into a slope, or the eave off)."""
    if kind in BLIND_KINDS or light:
        return 0, 0, 0
    free = bare = low = 0
    n = len(ring)
    for w in walls_:
        if w["flags"] & PARTY:
            continue
        pts = ring[[(w["first"] + j) % n for j in range(w["count"] + 1)]]
        if np.hypot(*np.diff(pts, axis=0).T).sum() < MIN_WALL:
            continue
        if eave - max(w["g0"], w["g1"]) < LOW_WALL and not w["openings"]:
            low += 1
            continue
        free += 1
        bare += 0 not in {o[1] for o in w["openings"]}
    return free, bare, low


def legacy_unlit(ring, party, kind, wall_height, front_edge, seed, ground_at, eave):
    """The same metric for the facades the game drew before this layout (Facade.cs per outline edge, random blank bays): (free edges of
    MIN_WALL or more, those with nothing on the ground floor). Shared and low edges are left out of both counts, as in `unlit`."""
    if kind in BLIND_KINDS:
        return 0, 0
    rng = np.random.default_rng(seed)
    floor_h = FLOOR_HEIGHT.get(kind, 3.0)
    shop, arched, strip = kind in SHOP_KINDS, kind in ARCHED_KINDS, kind in STRIP_KINDS
    free = bare = 0
    n = len(ring)
    for i in range(n):
        length = float(np.hypot(*(ring[(i + 1) % n] - ring[i])))
        if length < MIN_WALL or party[i] or eave - ground_at(ring[[i, (i + 1) % n]]).max() < LOW_WALL:
            continue
        free += 1
        front = i == front_edge
        if shop:
            if front:
                continue                                                                 # the shopfront
            bays = max(0, int(np.floor((length - 1.6) / 3.4 + 0.5))) or (1 if length > 2.6 else 0)
            lit = any(rng.random() >= 0.4 for _ in range(bays)) and 0.95 + 1.35 <= wall_height - 0.3
        else:
            if length < 2.6:
                lit = front and length >= 1.6                                           # a door only
            else:
                bay = 4.2 if arched else 3.6 if strip else 3.4
                margin = 1.2 if arched else 0.85
                bays = max(1, int(np.floor((length - 2 * margin) / bay + 0.5)))
                h = float(np.clip(wall_height * 0.42, 2.6, 5.2)) if arched else 1.5 if strip else 1.35
                sill = max(1.6, wall_height * 0.25) if arched else 0.95
                fits = sill + h <= wall_height - 0.35
                lit = front or any(rng.random() >= 0.22 and rng.random() >= 0.08 and fits for _ in range(bays))
        bare += not lit
    return free, bare
