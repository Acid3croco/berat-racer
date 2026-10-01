"""Run the stages and keep the result. The stages run tile by tile (roads/tiled.py); a `Network` is what they work on, assembled for
the neighbourhood of a tile."""
import functools
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

import machine
import sources
from rasters import BIG, HALF, SECTOR

from . import tiled

MARGIN = 300                      # roads are built this far beyond the sectors, so the world's border sees complete junctions


@dataclass
class Network:
    tag: str
    sectors: list
    edges: list
    nodes: object
    links: list
    junctions: list
    raw: list = field(default_factory=list)       # surveyed polyline of every edge, before smoothing (debug renders)
    lanes: list = field(default_factory=list)     # lane graph elements (lanegraph.py)
    report: dict = field(default_factory=dict)


ROAD_KINDS = ("roads", "osm_roads", "osm_controls", "osm_restrictions", "non_communication", "mnt", "mnh")     # what the stages read (sources.py)
SECTOR_REACH = 260                # a sector file holds the roads within this distance of its sector (the world builder's window margin is 240)


def report_path(tag):
    return BIG / "roads" / tag / "report.json"


def sector_path(tag, si, sj):
    """The tile file the world builder's roads of a sector come from (roads/tiled.py)."""
    return tiled.tile_file(tag, "lanes", (si, sj))


def load_sector(tag, si, sj):
    """(pieces, junction meshes, lane graph elements) around one sector."""
    return tiled.load_sector(tag, si, sj, SECTOR_REACH)


def load_around(tag, sectors, centre, radius=None):
    """The network (links with heights, junctions with planes) around a point, assembled from the tiles: for `inspect` and `gallery`."""
    area = tiled.Area(tag, sectors, MARGIN)
    t = tiled.tile_of(*centre)
    records = tiled.Records(area)
    links, junctions = records.around(t)
    z, planes = tiled.heights(area, links, junctions)
    links = {k: v for k, v in links.items() if k in z}
    return tiled.assemble(area, links, junctions, records.edges(links, junctions), (z, planes))


def tag_of(list_path):
    """`data/big/small_sectors.json` -> `small`."""
    return Path(list_path).stem.removesuffix("_sectors")


def build(list_path, log=functools.partial(print, flush=True), jobs=0, fresh=False):
    """Fetch the source tiles the area needs, run the tiled passes (roads/tiled.py), keep the report. Returns it."""
    sectors = [tuple(s) for s in json.loads(Path(list_path).read_text())["sectors"]]
    tag, jobs = tag_of(list_path), machine.jobs(jobs)
    fetched = sources.ensure(sectors, ROAD_KINDS, log=log)
    if fetched["failures"]:
        raise RuntimeError(f"source tiles could not be fetched: {fetched['failures'][:3]}")
    _, report = tiled.build(tag, sectors, MARGIN, jobs, fresh, log)
    path = report_path(tag)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=lambda v: v.tolist() if hasattr(v, "tolist") else str(v)))
    return report
