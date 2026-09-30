"""OpenStreetMap road attributes, from the osm2pgsql database of the massif-extractor server (read-only, over ssh).

`fetch` caches the ways of an area as one gzip JSON file; `enrich` matches each BD TOPO edge to the OSM way lying on it and copies
the attributes OSM knows better: surface / track grade, posted speed limit, lighting, and lanes / width / name where BD TOPO has none.
"""
import gzip
import json
import os
import re
import subprocess

import numpy as np
import shapely
from shapely.geometry import LineString

from fetch import CX, CY
from rasters import BIG

from .source import drawn_width

SSH_HOST = os.environ.get("BERAT_OSM_SSH", "mace")
PSQL = ('PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=300000" '
        "psql -w -h localhost -p 5411 -U osm_user osm_db -At")
DRIVABLE = ("motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link",
            "unclassified", "residential", "living_street", "service", "track", "road")
QUERY = """
copy (
  select json_build_object('id', osm_id, 'highway', highway, 'surface', surface, 'name', name, 'ref', ref, 'tags', hstore_to_json(tags),
                           'xy', ST_AsGeoJSON(ST_Transform(way, 2154), 2)::json->'coordinates')
  from planet_osm_line
  where highway in ({classes}) and way && ST_Transform(ST_MakeEnvelope({x0}, {y0}, {x1}, {y1}, 2154), 4326)
) to stdout;
"""

MATCH_DISTANCE = 9.0              # an OSM way further than this from a BD TOPO edge is another road
MATCH_ANGLE = 30.0                # degrees between the two centrelines
PAVED = {"asphalt", "paved", "concrete", "concrete:plates", "concrete:lanes", "paving_stones", "sett", "cobblestone", "chipseal"}
UNPAVED = {"unpaved", "gravel", "fine_gravel", "compacted", "ground", "dirt", "earth", "grass", "sand", "mud", "pebblestone"}


def cache_path(tag):
    return BIG / "osm" / f"roads_{tag}.json.gz"


def fetch(area, tag):
    """Download the drivable OSM ways of `area` (x0, z0, x1, z1 in local metres) into the cache. Returns the path."""
    x0, z0, x1, z1 = area
    sql = QUERY.format(classes=", ".join(f"'{c}'" for c in DRIVABLE), x0=x0 + CX, y0=z0 + CY, x1=x1 + CX, y1=z1 + CY)
    done = subprocess.run(["ssh", "-o", "BatchMode=yes", SSH_HOST, PSQL], input=sql, capture_output=True, text=True, timeout=600)
    if done.returncode != 0 or done.stderr.strip():
        raise RuntimeError(f"OSM fetch failed: {done.stderr.strip()[:400]}")
    ways = [json.loads(line.replace("\\\\", "\\")) for line in done.stdout.splitlines() if line.strip()]
    path = cache_path(tag)
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(ways, fh)
    return path, len(ways)


def load(tag):
    path = cache_path(tag)
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def _speed(text):
    m = re.match(r"^\s*(\d{1,3})\s*$", str(text or ""))
    return int(m.group(1)) if m else 0


def _metres(text):
    m = re.match(r"^\s*(\d+(?:[.,]\d+)?)\s*m?\s*$", str(text or ""))
    return float(m.group(1).replace(",", ".")) if m else 0.0


def _heading(line, at):
    a, b = line.interpolate(max(at - 3.0, 0.0)), line.interpolate(min(at + 3.0, line.length))
    return np.arctan2(b.y - a.y, b.x - a.x)


def match(edges, ways):
    """For each edge, the OSM way that lies on it (most of three probe points agree), or None."""
    lines = [LineString(np.asarray(w["xy"], float) - np.array([CX, CY], float)) for w in ways]
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
    stats = dict(matched=0, surface=0, limit=0, lanes=0, width=0, lit=0, name=0)
    for e, way in zip(edges, match(edges, ways)):
        if way is None:
            continue
        stats["matched"] += 1
        tags = dict(way.get("tags") or {})
        e.osm_id, e.tags = way["id"], {**tags, "highway": way["highway"]}
        surface, grade = way.get("surface") or tags.get("surface"), tags.get("tracktype")
        paved = True if surface in PAVED or grade == "grade1" else False if surface in UNPAVED or grade in ("grade3", "grade4", "grade5") else None
        if paved is not None and surface:
            e.surface = surface
        if paved is not None and paved == e.dirt and not e.bridge:
            e.dirt = not paved
            if e.klass == "track" and paved:
                e.klass = "local"
            e.width = drawn_width(e.width_real, e.dirt)
            stats["surface"] += 1
        limit = _speed(tags.get("maxspeed"))
        if 10 <= limit <= 130 and limit != e.limit:
            e.limit = limit
            stats["limit"] += 1
        if e.lanes == 0 and _speed(tags.get("lanes")):
            e.lanes = min(_speed(tags["lanes"]), 15)
            stats["lanes"] += 1
        if tags.get("lit") == "yes":
            e.lit = True
            stats["lit"] += 1
        if not e.name and way.get("name"):
            e.name = way["name"]
            stats["name"] += 1
        width = _metres(tags.get("width"))
        if 2.0 <= width <= 30.0 and not e.width_surveyed:
            e.width_real, e.width = width, drawn_width(width, e.dirt)
            stats["width"] += 1
    return stats
