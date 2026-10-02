"""OpenStreetMap road attributes, from the osm2pgsql database of the massif-extractor server (read-only, over ssh; tiles of sources.py).

`load` reads the ways of an area's tiles; `enrich` matches each BD TOPO edge to the OSM way lying on it and copies
the attributes OSM knows better: surface / track grade, posted speed limit (per direction, French zone codes included), an explicit
one-way tag, lighting, and lanes / width / name where BD TOPO has none. `level` reads the way's bridge / tunnel / layer / cutting /
covered tags for `crossing.py`.
"""
import re

import numpy as np
import shapely
from shapely.geometry import LineString

from fetch import CX, CY
import sources

from . import config
from .source import drawn_width

MATCH_DISTANCE = 9.0              # an OSM way further than this from a BD TOPO edge is another road
MATCH_ANGLE = 30.0                # degrees between the two centrelines
PAVED = {"asphalt", "paved", "concrete", "concrete:plates", "concrete:lanes", "paving_stones", "sett", "cobblestone", "chipseal"}
UNPAVED = {"unpaved", "gravel", "fine_gravel", "compacted", "ground", "dirt", "earth", "grass", "sand", "mud", "pebblestone"}
# French limit codes (`maxspeed=FR:urban`, `zone:maxspeed=FR:30`, `maxspeed:type=FR:zone30`, `source:maxspeed=FR:rural`)
FR_LIMITS = {"urban": 50, "rural": 80, "motorway": 130, "expressway": 110, "living_street": 20, "walk": 6,
             "zone20": 20, "zone30": 30, "zone50": 50, "zone70": 70, "10": 10, "20": 20, "30": 30, "50": 50, "70": 70}
LIMIT_KEYS = ("maxspeed", "zone:maxspeed", "maxspeed:type", "source:maxspeed")      # in order of authority
ONEWAY_YES, ONEWAY_REVERSED = {"yes", "true", "1"}, {"-1", "reverse"}


def load(tiles):
    """The OSM drivable ways of these tiles (sources.py), by id."""
    return sorted(sources.read_tiles("osm_roads", tiles), key=lambda w: w["id"])


def _speed(text):
    m = re.match(r"^\s*(\d{1,3})\s*$", str(text or ""))
    return int(m.group(1)) if m else 0


def _code_limit(text):
    """`FR:urban` -> 50, `FR:30` -> 30; 0 for anything else. Several values (`fr:urban;sign`): the first code that is known."""
    for value in str(text or "").split(";"):
        country, _, code = value.strip().partition(":")
        if country.lower() == "fr" and code.lower() in FR_LIMITS:
            return FR_LIMITS[code.lower()]
    return 0


def limits(tags):
    """(forward, backward) posted limit of a way in km/h, along and against its digitised direction; 0 where the tags say nothing.
    A numeric `maxspeed[:forward|:backward]` wins, then a French code in the keys of LIMIT_KEYS."""
    general = _speed(tags.get("maxspeed")) or next((v for v in (_code_limit(tags.get(k)) for k in LIMIT_KEYS) if v), 0)
    return (_speed(tags.get("maxspeed:forward")) or general, _speed(tags.get("maxspeed:backward")) or general)


def oneway(tags):
    """1 one-way along the way, 2 against it, 0 two-way; None when the way does not say (untagged, `alternating`, `reversible`)."""
    value = str(tags.get("oneway", "")).lower()
    if value in ONEWAY_YES:
        return 1
    if value in ONEWAY_REVERSED:
        return 2
    if value == "no":
        return 0
    return None


def tunnel(tags):
    """Does the OSM way run underground: `tunnel=yes`, or a service way below ground level (a ramp into an underground car park)?
    A `building_passage` or `covered` way stays at ground level."""
    if not tags:
        return False
    if tags.get("tunnel") in ("yes", "avalanche_protector"):
        return True
    try:
        below = float(tags.get("layer", "0")) < 0
    except ValueError:
        below = False
    return below and tags.get("highway") == "service" and tags.get("tunnel") not in ("building_passage",) and tags.get("covered", "no") == "no"


def level(tags):
    """Vertical level of a way after its tags: `layer` when given (bridge alone 1, tunnel or covered alone -1, else 0), and a cutting
    half a level below its layer. None for an edge without an OSM way."""
    if not tags:
        return None
    bridge, tunnel = tags.get("bridge", "no") != "no", tags.get("tunnel", "no") != "no" or tags.get("covered", "no") != "no"
    try:
        layer = float(tags["layer"])
    except (KeyError, ValueError):
        layer = 1.0 if bridge else -1.0 if tunnel else 0.0
    return layer - (0.5 if tags.get("cutting", "no") != "no" else 0.0)


def _metres(text):
    m = re.match(r"^\s*(\d+(?:[.,]\d+)?)\s*m?\s*$", str(text or ""))
    return float(m.group(1).replace(",", ".")) if m else 0.0


def _heading(line, at):
    a, b = line.interpolate(max(at - 3.0, 0.0)), line.interpolate(min(at + 3.0, line.length))
    return np.arctan2(b.y - a.y, b.x - a.x)


def _same_direction(edge, way):
    """Does the way run the same way as the edge where they overlap?"""
    line = LineString(edge.xy)
    return way.project(line.interpolate(0.7, normalized=True)) >= way.project(line.interpolate(0.3, normalized=True))


def _lines(ways):
    return [LineString(np.asarray(w["xy"], float) - np.array([CX, CY], float)) for w in ways]


def match(edges, ways):
    """For each edge, the OSM way that lies on it (most of three probe points agree), or None."""
    lines = _lines(ways)
    tree = shapely.STRtree(lines)
    out = []
    for e in edges:
        line = LineString(e.xy)
        votes = {}
        for frac in (0.25, 0.5, 0.75):
            at = frac * line.length
            probe = line.interpolate(at)
            for k in tree.query(probe.buffer(MATCH_DISTANCE)):
                way = lines[k]
                if way.distance(probe) > MATCH_DISTANCE:
                    continue
                turn = np.degrees(abs(_heading(line, at) - _heading(way, way.project(probe)))) % 180.0
                if min(turn, 180.0 - turn) <= MATCH_ANGLE:
                    votes[k] = votes.get(k, 0) + 1.0 - way.distance(probe) / (10.0 * MATCH_DISTANCE)
        best = max(votes, key=votes.get) if votes else None
        out.append(ways[best] if best is not None and votes[best] >= 1.5 else None)
    return out


def enrich(edges, ways):
    """Copy OSM knowledge onto the edges in place. Returns counts of what changed, for the build report."""
    stats = dict(matched=0, surface=0, limit=0, limit_by_direction=0, oneway=0, oneway_reversed=0, lanes=0, width=0, lit=0, name=0)
    lines = {id(w): line for w, line in zip(ways, _lines(ways))}
    for e, way in zip(edges, match(edges, ways)):
        if way is None:
            continue
        stats["matched"] += 1
        tags = dict(way.get("tags") or {})
        e.osm_id, e.tags = way["id"], {**tags, "highway": way["highway"]}
        same = e.osm_same = _same_direction(e, lines[id(way)])
        surface, grade = way.get("surface") or tags.get("surface"), tags.get("tracktype")
        paved = True if surface in PAVED or grade == "grade1" else False if surface in UNPAVED or grade in ("grade3", "grade4", "grade5") else None
        if paved is not None and surface:
            e.surface = surface
        if paved is not None and paved == e.dirt and not e.bridge:
            e.dirt = not paved
            if e.klass == "track" and paved:
                e.klass = "local"
            e.width = drawn_width(e.width_real, e.dirt, e.oneway)
            stats["surface"] += 1
        forward, backward = limits(tags) if same else limits(tags)[::-1]           # along and against the edge
        if 5 <= forward <= 130 and forward != e.limit:
            e.limit = forward
            stats["limit"] += 1
        if 5 <= backward <= 130 and backward != forward:
            e.limit_back = backward
            stats["limit_by_direction"] += 1
        way_oneway = oneway(tags)
        ring = e.kind == 1 or tags.get("junction") in ("roundabout", "circular")
        if way_oneway is not None and not ring:
            # rings keep BD TOPO's sense: counter-clockwise on all 61 sections that show it (small map). The direction of a closed OSM
            # way is ambiguous where it is projected, and the two OSM verdicts there would have turned rings clockwise.
            along = way_oneway if same or way_oneway == 0 else 3 - way_oneway
            if along != e.oneway:
                # measured on the small map: every disagreement is a single-lane street where the OSM way is coherent along the street
                # and BD TOPO is not (one segment of a loop one-way, the others two-way), so an explicit tag wins
                stats["oneway_reversed" if along and e.oneway else "oneway"] += 1
                e.oneway = along
                e.width = drawn_width(e.width_real, e.dirt, e.oneway)
        if e.lanes == 0 and _speed(tags.get("lanes")):
            e.lanes = int(min(_speed(tags["lanes"]), max(1, e.width_real // config.LANE_MIN_WIDTH)))
            stats["lanes"] += 1
        if tags.get("lit") == "yes":
            e.lit = True
            stats["lit"] += 1
        if not e.name and way.get("name"):
            e.name = way["name"]
            stats["name"] += 1
        width = _metres(tags.get("width"))
        if 2.0 <= width <= 30.0 and not e.width_surveyed:
            e.width_real, e.width = width, drawn_width(width, e.dirt, e.oneway)
            stats["width"] += 1
    return stats
