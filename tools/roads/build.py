"""Run the stages and keep the result: one `Network` per build area, cached as a pickle next to the downloaded data."""
import functools
import json
import pickle
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from rasters import BIG, HALF, SECTOR

from . import alignment, geometry, graph as graph_stage, junction as junction_stage, metrics, osm, profile, source, surface

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
    report: dict = field(default_factory=dict)


SECTOR_REACH = 260                # a sector file holds the roads within this distance of its sector (the world builder's window margin is 240)


def artefact_path(tag):
    return BIG / "roads" / f"{tag}.network.pkl"


def sector_path(tag, si, sj):
    return BIG / "roads" / tag / f"sector_{si}_{sj}.pkl"


def save(network):
    """Write the network (for `inspect`, `gallery`, `report`) and, per sector, the finished road surface the world builder reads."""
    path = artefact_path(network.tag)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        pickle.dump(network, fh, protocol=pickle.HIGHEST_PROTOCOL)
    path.with_suffix(".report.json").write_text(json.dumps(network.report, indent=2))

    pieces, meshes = surface.pieces(network), surface.junction_meshes(network)
    piece_lo, piece_hi = np.array([p.xy.min(axis=0) for p in pieces]), np.array([p.xy.max(axis=0) for p in pieces])
    mesh_lo, mesh_hi = np.array([v[:, :2].min(axis=0) for _, v in meshes]), np.array([v[:, :2].max(axis=0) for _, v in meshes])
    sector_path(network.tag, 0, 0).parent.mkdir(parents=True, exist_ok=True)
    for si, sj in network.sectors:
        lo = np.array([-HALF + si * SECTOR, -HALF + sj * SECTOR]) - SECTOR_REACH
        hi = lo + SECTOR + 2 * SECTOR_REACH
        keep_p = np.flatnonzero((piece_hi >= lo).all(axis=1) & (piece_lo <= hi).all(axis=1))
        keep_m = np.flatnonzero((mesh_hi >= lo).all(axis=1) & (mesh_lo <= hi).all(axis=1)) if len(meshes) else []
        with open(sector_path(network.tag, si, sj), "wb") as fh:
            pickle.dump(([pieces[i] for i in keep_p], [meshes[i] for i in keep_m]), fh, protocol=pickle.HIGHEST_PROTOCOL)
    return path


def load(tag):
    with open(artefact_path(tag), "rb") as fh:
        return pickle.load(fh)


def load_sector(tag, si, sj):
    """(pieces, junction meshes) around one sector."""
    with open(sector_path(tag, si, sj), "rb") as fh:
        return pickle.load(fh)


def tag_of(list_path):
    """`data/big/small_sectors.json` -> `small`."""
    return Path(list_path).stem.removesuffix("_sectors")


def build(list_path, log=functools.partial(print, flush=True), jobs=6):
    sectors = [tuple(s) for s in json.loads(Path(list_path).read_text())["sectors"]]
    tag, report, clock = tag_of(list_path), {}, time.time()

    def done(stage, **stats):
        nonlocal clock
        report[stage] = dict(stats, seconds=round(time.time() - clock, 1))
        log(f"  {stage:<10} {report[stage]}")
        clock = time.time()

    edges = source.load_edges(sectors, MARGIN)
    ways = osm.load(tag)
    osm_stats = osm.enrich(edges, ways) if ways is not None else dict(skipped="no OSM cache: run `python -m roads fetch-osm`")
    edges = source.apply_overrides(edges, source.load_overrides())
    done("source", edges=len(edges), km=round(sum(e.length for e in edges) / 1000.0, 1), osm=osm_stats)

    graph = graph_stage.build(edges)
    stroke_list = graph_stage.strokes(graph)
    raw = [e.xy.copy() for e in graph.edges]
    done("graph", nodes=len(graph.nodes), strokes=len(stroke_list), junction_nodes=sum(graph.is_junction(n) for n in range(len(graph.nodes))))

    done("alignment", **alignment.align(graph, stroke_list))

    links = []
    for chain in graph_stage.links(graph):
        nodes = graph_stage.chain_nodes(graph, chain)
        links.append(geometry.make_link(graph, chain, (nodes[0], nodes[-1])))
    narrowed = geometry.clamp_widths(links)
    junctions = junction_stage.build_junctions(graph, links, jobs)
    for link in links:
        geometry.sample(link)
    junction_stage.outline_all(junctions, graph, links, jobs)
    for junction in junctions:
        junction.slim()
    for link in links:
        link.slim()
    done("junction", links=len(links), narrowed_side_by_side=narrowed, junctions=len(junctions), swallowed_links=sum(link.internal for link in links),
         invalid=sum(not j.valid for j in junctions))

    network = Network(tag=tag, sectors=sectors, edges=graph.edges, nodes=graph.nodes, links=links, junctions=junctions, raw=raw, report=report)
    done("profile", **profile.solve(network, log, jobs))
    report["surface"] = metrics.surface_report(network)
    report["classes"] = metrics.profile_report(network)
    return network
