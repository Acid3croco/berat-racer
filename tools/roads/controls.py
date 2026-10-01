"""Junction control and turn restrictions of an area (tiles of sources.py):

  nodes          OSM `highway=traffic_signals / stop / give_way / mini_roundabout` points (planet_osm_point of the massif-extractor
                 database), with their `direction` tag
  restrictions   OSM `type=restriction` relations (Overpass: the database has no relation table), with the via node's position
  no_turn        BD TOPO `non_communication`: the section one may not leave a node by when arriving from another

All positions in local metres. Used by `lanegraph.py`.
"""
import sources


def load(tiles):
    """{nodes, restrictions, no_turn} of these tiles, in a fixed order (by id, by position)."""
    return dict(nodes=sorted(sources.read_tiles("osm_controls", tiles), key=lambda n: n["id"]),
                restrictions=sorted(sources.read_tiles("osm_restrictions", tiles), key=lambda r: r["id"]),
                no_turn=sorted(sources.read_tiles("non_communication", tiles), key=lambda r: (r["x"], r["z"], r["entry"] or "")))
