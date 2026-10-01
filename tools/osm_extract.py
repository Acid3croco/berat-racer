"""The Overpass kinds of sources.py (land use, roof tags, turn restrictions, points of interest, place names) from a Geofabrik
extract instead of the public Overpass servers: the extract is read twice with pyosmium, the elements rebuilt in the shape Overpass
gives them (ways with their geometry, multipolygon relations with their member ways, restrictions with the via node, the centre of
ways and relations) and written per tile by the same converters as sources.fetch_overpass, so both paths give the same tiles.

  uv run python osm_extract.py data/big/src/extract/midi-pyrenees-260930.osm.pbf data/big/berat70new_sectors.json

Only the tiles the list needs and does not have are written; tiles outside the extract's box are left to Overpass.
"""
import json
import sys
import time
from pathlib import Path

import osmium
from pyproj import Transformer

import sources
from fetch import CX, CY


def matches_ground(kind, tags):
    """The Overpass ground filters (sources.GROUND_FILTERS): ways and relations with landuse / natural / leisure / a car park; ways of
    parking aisles and barriers."""
    if any(k in tags for k in ("landuse", "natural", "leisure")) or tags.get("amenity") == "parking":
        return True
    return kind == "w" and (tags.get("service") == "parking_aisle" or "barrier" in tags)


def matches_roof(kind, tags):
    return kind == "w" and "building" in tags and any(k in tags for k in ("roof:shape", "building:levels", "roof:orientation"))


def matches_poi(tags):
    amenity = tags.get("amenity")
    return (amenity is not None and amenity in sources.POI_AMENITY.split("|")) or "shop" in tags


PLACES = ("city", "town", "village", "hamlet", "suburb")


class Relations(osmium.SimpleHandler):
    """Pass 1: the relations wanted, and the ways and nodes they need."""

    def __init__(self):
        super().__init__()
        self.ground, self.restrictions, self.pois = [], [], []
        self.ways, self.nodes = set(), set()

    def relation(self, r):
        tags = dict(r.tags)
        members = [(m.type, m.ref, m.role) for m in r.members]
        rec = dict(type="relation", id=r.id, tags=tags, members=members)
        if tags.get("type") == "restriction":
            self.restrictions.append(rec)
            self.nodes |= {ref for t, ref, role in members if t == "n" and role == "via"}
        if tags.get("type") == "multipolygon" and matches_ground("r", tags):
            self.ground.append(rec)
            self.ways |= {ref for t, ref, _ in members if t == "w"}
        if matches_poi(tags):
            self.pois.append(rec)
            self.ways |= {ref for t, ref, _ in members if t == "w"}
            self.nodes |= {ref for t, ref, _ in members if t == "n"}


class Elements(osmium.SimpleHandler):
    """Pass 2 (node locations on): the nodes and ways wanted, with their coordinates."""

    def __init__(self, need_ways, need_nodes):
        super().__init__()
        self.need_ways, self.need_nodes = need_ways, need_nodes
        self.ground, self.roofs, self.pois, self.places = [], [], [], []
        self.way_geometry, self.node_at = {}, {}

    def node(self, n):
        if n.id in self.need_nodes:
            self.node_at[n.id] = (n.location.lon, n.location.lat)
        if not n.tags:
            return
        tags = dict(n.tags)
        if matches_poi(tags):
            self.pois.append(dict(type="node", id=n.id, lat=n.location.lat, lon=n.location.lon, tags=tags))
        if tags.get("place") in PLACES and "name" in tags:
            self.places.append(dict(type="node", id=n.id, lat=n.location.lat, lon=n.location.lon, tags=tags))

    def way(self, w):
        tags = dict(w.tags)
        wanted = w.id in self.need_ways
        ground, roof, poi = matches_ground("w", tags), matches_roof("w", tags), matches_poi(tags)
        if not (wanted or ground or roof or poi):
            return
        try:
            geometry = [dict(lat=n.location.lat, lon=n.location.lon) for n in w.nodes]
        except osmium.InvalidLocationError:
            return
        if wanted:
            self.way_geometry[w.id] = geometry
        rec = dict(type="way", id=w.id, tags=tags, geometry=geometry)
        if ground:
            self.ground.append(rec)
        if roof:
            self.roofs.append(rec)
        if poi:
            self.pois.append(dict(type="way", id=w.id, tags=tags, center=_centre(geometry)))


def _centre(points):
    """Overpass's `center`: the middle of the bounding box."""
    lats, lons = [p["lat"] for p in points], [p["lon"] for p in points]
    return dict(lat=(min(lats) + max(lats)) / 2, lon=(min(lons) + max(lons)) / 2) if points else dict(lat=0.0, lon=0.0)


def read(path):
    """Everything the Overpass kinds need from an extract: (ground, roofs, restrictions, pois, places) Overpass-shaped elements."""
    t0 = time.time()
    rel = Relations()
    rel.apply_file(str(path))
    print(f"pass 1 (relations): {time.time() - t0:.0f} s, {len(rel.ground)} multipolygons, {len(rel.restrictions)} restrictions", flush=True)
    el = Elements(rel.ways, rel.nodes)
    el.apply_file(str(path), locations=True, idx="flex_mem")
    print(f"pass 2 (nodes, ways): {time.time() - t0:.0f} s, {len(el.ground)} ground ways, {len(el.roofs)} roofs, {len(el.pois)} POIs", flush=True)
    ground = list(el.ground)
    for r in rel.ground:                                                       # members with their geometry, as `out geom` gives them
        members = [dict(type="way", ref=ref, role=role, geometry=el.way_geometry.get(ref, [])) for t, ref, role in r["members"] if t == "w"]
        ground.append(dict(type="relation", id=r["id"], tags=r["tags"], members=members))
    restrictions = []
    for r in rel.restrictions:
        restrictions.append(dict(type="relation", id=r["id"], tags=r["tags"],
                                 members=[dict(type={"n": "node", "w": "way", "r": "relation"}[t], ref=ref, role=role) for t, ref, role in r["members"]]))
        for t, ref, role in r["members"]:
            if t == "n" and role == "via" and ref in el.node_at:
                lon, lat = el.node_at[ref]
                restrictions.append(dict(type="node", id=ref, lat=lat, lon=lon))
    pois = list(el.pois)
    for r in rel.pois:
        points = [p for t, ref, _ in r["members"] if t == "w" for p in el.way_geometry.get(ref, [])]
        points += [dict(lon=el.node_at[ref][0], lat=el.node_at[ref][1]) for t, ref, _ in r["members"] if t == "n" and ref in el.node_at]
        if points:
            pois.append(dict(type="relation", id=r["id"], tags=r["tags"], center=_centre(points)))
    order = lambda e: ("nwr".index(e["type"][0]), e["id"])                       # Overpass's output order
    return sorted(ground, key=order), sorted(el.roofs, key=order), restrictions, sorted(pois, key=order), sorted(el.places, key=order)


def bounds(path):
    """The extract's box (lon0, lat0, lon1, lat1) from its header."""
    box = osmium.io.Reader(str(path), osmium.osm.osm_entity_bits.NOTHING).header().box()
    return box.bottom_left.lon, box.bottom_left.lat, box.top_right.lon, box.top_right.lat


def write_tiles(path, tiles):
    """Write the Overpass kinds of `tiles` that lie wholly inside the extract. Returns the tiles written."""
    to_wgs = Transformer.from_crs(2154, 4326, always_xy=True)
    to_l93 = Transformer.from_crs(4326, 2154, always_xy=True)
    lon0, lat0, lon1, lat1 = bounds(path)
    inside = []
    for t in tiles:
        r = sources.tile_rect(*t)
        lons, lats = to_wgs.transform([r[0], r[2], r[0], r[2]], [r[1], r[1], r[3], r[3]])
        if min(lons) >= lon0 and max(lons) <= lon1 and min(lats) >= lat0 and max(lats) <= lat1:
            inside.append(t)
    if not inside:
        return []
    ground, roofs, restrictions, pois, places = read(path)
    t0 = time.time()

    def box_l93(points):
        xs, ys = to_l93.transform([p["lon"] for p in points], [p["lat"] for p in points])
        return min(xs), min(ys), max(xs), max(ys)

    def tiles_of(e):
        """The tiles an element's box meets (lines and areas live in every tile they meet)."""
        pts = e.get("geometry") or [p for m in e.get("members", []) for p in m.get("geometry", [])]
        if not pts:
            return []
        return sources.tiles_of_box(*box_l93(pts))
    wanted = set(inside)
    by_tile = {t: dict(ground=[], roofs=[], restrictions=[], pois=[], places=[]) for t in inside}
    for name, elements in (("ground", ground), ("roofs", roofs)):
        for e in elements:
            for t in tiles_of(e):
                if t in wanted:
                    by_tile[t][name].append(e)
    via = {e["id"]: e for e in restrictions if e["type"] == "node"}
    for e in restrictions:                                                     # a relation goes with the tile of its via node
        if e["type"] != "relation":
            continue
        node = next((via.get(m["ref"]) for m in e["members"] if m["role"] == "via" and m["type"] == "node"), None)
        if node is None:
            continue
        t = sources.tile_of_point(*to_l93.transform(node["lon"], node["lat"]))
        if t in wanted:
            by_tile[t]["restrictions"] += [e, node]
    for name, elements in (("pois", pois), ("places", places)):
        for e in elements:
            t = sources.tile_of_point(*to_l93.transform(*sources._lonlat(e)))
            if t in wanted:
                by_tile[t][name].append(e)
    for t, parts in by_tile.items():
        rect = sources.tile_rect(*t)
        sources.write_tile("osm_ground", *t, sources.osm_ground_features(parts["ground"], to_l93, rect))
        sources.write_tile("osm_roofs", *t, sources.osm_roof_records(parts["roofs"], to_l93, rect))
        sources.write_tile("osm_restrictions", *t, sources.osm_restriction_records(parts["restrictions"], to_l93, t))
        sources.write_tile("osm_pois", *t, parts["pois"])
        sources.write_tile("osm_places", *t, sources.osm_place_records(parts["places"], to_l93))
    print(f"tiles: {len(inside)} written in {time.time() - t0:.0f} s", flush=True)
    return inside


def main(path, list_path):
    sectors = [tuple(s) for s in json.loads(Path(list_path).read_text())["sectors"]]
    gaps = sources.missing(sectors, sources.OVERPASS_KINDS)
    tiles = sorted({t for v in gaps.values() for t in v})
    print(f"{len(tiles)} tiles lack an Overpass kind", flush=True)
    t0 = time.time()
    done = write_tiles(Path(path), tiles)
    print(f"{len(done)} tiles from the extract in {time.time() - t0:.0f} s; {len(tiles) - len(done)} left to Overpass")


if __name__ == "__main__":
    main(*sys.argv[1:3])
