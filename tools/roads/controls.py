"""Junction control and turn restrictions of an area, cached next to the OSM ways (`python -m roads fetch-osm`):

  nodes          OSM `highway=traffic_signals / stop / give_way / mini_roundabout` points (planet_osm_point of the massif-extractor
                 database, read-only, bbox-bounded), with their `direction` tag
  restrictions   OSM `type=restriction` relations (Overpass API: the database has no relation table), with the via node's position
  no_turn        BD TOPO `non_communication`: the section one may not leave a node by when arriving from another

All positions in local metres. Used by `lanegraph.py`.
"""
import gzip
import json
import subprocess
import time

import requests
from pyproj import Transformer

from fetch import CX, CY
from rasters import BIG

from . import osm

CONTROLS = ("traffic_signals", "stop", "give_way", "mini_roundabout")
NODE_QUERY = """
copy (
  select json_build_object('id', osm_id, 'highway', highway, 'tags', hstore_to_json(tags),
                           'xy', ST_AsGeoJSON(ST_Transform(way, 2154), 2)::json->'coordinates')
  from planet_osm_point
  where highway in ({classes}) and way && ST_Transform(ST_MakeEnvelope({x0}, {y0}, {x1}, {y1}, 2154), 4326)
) to stdout;
"""
OVERPASS = "https://overpass-api.de/api/interpreter"
USER_AGENT = "berat-racer/1.0 (hobby game, non-commercial)"
WFS = "https://data.geopf.fr/wfs/ows"


def cache_path(tag):
    return BIG / "osm" / f"controls_{tag}.json.gz"


def _nodes(area):
    x0, z0, x1, z1 = area
    sql = NODE_QUERY.format(classes=", ".join(f"'{c}'" for c in CONTROLS), x0=x0 + CX, y0=z0 + CY, x1=x1 + CX, y1=z1 + CY)
    done = subprocess.run(["ssh", "-o", "BatchMode=yes", osm.SSH_HOST, osm.PSQL], input=sql, capture_output=True, text=True, timeout=600)
    if done.returncode != 0 or done.stderr.strip():
        raise RuntimeError(f"OSM control nodes fetch failed: {done.stderr.strip()[:400]}")
    out = []
    for line in done.stdout.splitlines():
        if line.strip():
            node = json.loads(line.replace("\\\\", "\\"))
            out.append(dict(id=node["id"], highway=node["highway"], tags=node.get("tags") or {}, x=node["xy"][0] - CX, z=node["xy"][1] - CY))
    return out


def _restrictions(area):
    x0, z0, x1, z1 = area
    to_wgs = Transformer.from_crs(2154, 4326, always_xy=True)
    to_l93 = Transformer.from_crs(4326, 2154, always_xy=True)
    lon0, lat0 = to_wgs.transform(x0 + CX, z0 + CY)
    lon1, lat1 = to_wgs.transform(x1 + CX, z1 + CY)
    query = f'[out:json][timeout:120];rel["type"="restriction"]({lat0},{lon0},{lat1},{lon1});out body;node(r:"via");out skel;'
    for attempt in range(6):
        reply = requests.post(OVERPASS, data={"data": query}, timeout=240, headers={"User-Agent": USER_AGENT})
        if reply.ok and reply.text.lstrip().startswith("{"):
            break
        time.sleep(10 + 10 * attempt)
    else:
        raise RuntimeError(f"Overpass restrictions failed: HTTP {reply.status_code}")
    elements = reply.json()["elements"]
    where = {e["id"]: to_l93.transform(e["lon"], e["lat"]) for e in elements if e["type"] == "node"}
    out = []
    for rel in (e for e in elements if e["type"] == "relation"):
        kind = rel.get("tags", {}).get("restriction")
        role = {r["role"]: r for r in rel["members"]}
        if not kind or "from" not in role or "to" not in role or role.get("via", {}).get("type") != "node":
            continue                                                            # via a way: not handled
        x, y = where.get(role["via"]["ref"], (None, None))
        if x is None:
            continue
        out.append(dict(id=rel["id"], restriction=kind, from_way=role["from"]["ref"], to_way=role["to"]["ref"], x=x - CX, z=y - CY))
    return out


def _no_turn(area):
    x0, z0, x1, z1 = area
    out, start = [], 0
    while True:
        reply = requests.get(WFS, params=dict(SERVICE="WFS", VERSION="2.0.0", REQUEST="GetFeature", TYPENAMES="BDTOPO_V3:non_communication",
                                              OUTPUTFORMAT="application/json", SRSNAME="EPSG:2154", COUNT=5000, STARTINDEX=start,
                                              BBOX=f"{x0 + CX},{z0 + CY},{x1 + CX},{z1 + CY},EPSG:2154"), timeout=(10, 120))
        reply.raise_for_status()
        page = reply.json()["features"]
        for f in page:
            p = f["properties"]
            exits = p.get("liens_vers_troncon_sortie") or ""
            out.append(dict(x=f["geometry"]["coordinates"][0] - CX, z=f["geometry"]["coordinates"][1] - CY, entry=p.get("lien_vers_troncon_entree"),
                            exits=[e for e in exits.replace(",", "/").split("/") if e]))
        if len(page) < 5000:
            return out
        start += 5000


def fetch(area, tag):
    data = dict(nodes=_nodes(area), restrictions=_restrictions(area), no_turn=_no_turn(area))
    path = cache_path(tag)
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(data, fh)
    return path, {k: len(v) for k, v in data.items()}


def load(tag):
    path = cache_path(tag)
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)
