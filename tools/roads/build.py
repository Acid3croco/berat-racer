"""Run the stages and keep the result: one `Network` per build area, cached as a pickle next to the downloaded data."""
import json
import pickle
import time
from dataclasses import dataclass, field
from pathlib import Path

from rasters import BIG

from . import alignment, geometry, graph as graph_stage, junction as junction_stage, metrics, osm, profile, source

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


def artefact_path(tag):
    return BIG / "roads" / f"{tag}.network.pkl"


def save(network):
    path = artefact_path(network.tag)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        pickle.dump(network, fh, protocol=pickle.HIGHEST_PROTOCOL)
    path.with_suffix(".report.json").write_text(json.dumps(network.report, indent=2))
    return path


def load(tag):
    with open(artefact_path(tag), "rb") as fh:
        return pickle.load(fh)


def tag_of(list_path):
    """`data/big/small_sectors.json` -> `small`."""
    return Path(list_path).stem.removesuffix("_sectors")


def build(list_path, log=print):
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
    junctions = junction_stage.build_junctions(graph, links)
    for link in links:
        geometry.sample(link)
    for junction in junctions:
        junction_stage.outline(junction, graph, links)
    done("junction", links=len(links), junctions=len(junctions), swallowed_links=sum(link.internal for link in links),
         invalid=sum(not j.valid for j in junctions))

    network = Network(tag=tag, sectors=sectors, edges=graph.edges, nodes=graph.nodes, links=links, junctions=junctions, raw=raw, report=report)
    done("profile", **profile.solve(network, log))
    report["surface"] = metrics.surface_report(network)
    report["classes"] = metrics.profile_report(network)
    return network
