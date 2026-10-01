"""Command line of the road pipeline. Run from tools/:

  uv run python -m roads fetch                      fetch the source tiles the area needs and does not have yet (sources.py; `build` does it too)
  uv run python -m roads build                      run every stage, write data/big/roads/<tag>.network.pkl + .report.json, print the report
  uv run python -m roads report                     print the report of the last build
  uv run python -m roads inspect --at X,Z           plan view, height profiles and the surveyed sections around a spot (local metres)
  uv run python -m roads gallery                    sheets of junction plan views (all junctions, 48 per sheet)

then `uv run python build_world.py --list <same list>` puts the roads into the world. Default list: data/big/small_sectors.json.
"""
import argparse
import json
from pathlib import Path

import numpy as np

from . import build as build_stage
import sources

from . import debug

DEFAULT_LIST = "data/big/small_sectors.json"


def print_report(report):
    for stage in ("source", "graph", "alignment", "junction", "crossing", "profile", "lanes", "lanegraph", "surface", "save"):
        if stage in report:
            print(f"{stage:<10} {json.dumps(report[stage], ensure_ascii=False)}")
    print(f"{'class':<11}{'km':>7}{'max grade %':>13}{'min crest m':>13}{'min sag m':>11}{'take-off km/h':>15}{'(draped)':>10}{'cut/fill p90':>14}{'p99':>7}{'max':>7}")
    for name, row in report.get("classes", {}).items():
        if name == "junctions":
            continue
        print(f"{name:<11}{row['km']:>7}{row['max_grade_pct']:>13}{row['min_crest_radius']:>13}{row['min_sag_radius']:>11}"
              f"{row['takeoff_kmh']:>15}{row['before_takeoff_kmh']:>10}{row['cut_fill_p90_m']:>14}{row['cut_fill_p99_m']:>7}{row['cut_fill_max_m']:>7}")
    print("junctions ", json.dumps(report.get("classes", {}).get("junctions", {})))


def cmd_fetch(args):
    sectors = [tuple(s) for s in json.loads(Path(args.list).read_text())["sectors"]]
    report = sources.ensure(sectors, build_stage.ROAD_KINDS)
    print(json.dumps(report, indent=1))


def sectors_of(args):
    return [tuple(s) for s in json.loads(Path(args.list).read_text())["sectors"]]


def cmd_build(args):
    report = build_stage.build(args.list, jobs=args.jobs, fresh=args.fresh)
    print_report(report)
    print(f"-> {build_stage.report_path(build_stage.tag_of(args.list))}")


def cmd_report(args):
    print_report(json.loads(build_stage.report_path(build_stage.tag_of(args.list)).read_text()))


def cmd_inspect(args):
    centre = tuple(float(v) for v in args.at.split(","))
    network = build_stage.load_around(build_stage.tag_of(args.list), sectors_of(args), centre)
    out = debug.ensure_dir(args.out)
    debug.save_plan(network, centre, args.radius, out / "plan.png")
    near = [k for k, link in enumerate(network.links) if np.hypot(*(link.xy - np.array(centre)).T).min() < args.radius]
    if near:
        debug.profile(network, near[:12], out / "profiles.png")
    for k in near:
        link = network.links[k]
        print(f"L{link.key}: {link.length:.0f} m, junctions {[network.junctions[j].key if j >= 0 else None for j in link.junction]}, "
              f"trims {np.round(link.trim, 1).tolist()}{' (swallowed)' if link.internal else ''}")
        for e, rev in link.chain:
            edge = network.edges[e]
            print(f"    {edge.cleabs}  {edge.nature}, {edge.klass}, width {edge.width_real:g} m (drawn {edge.width:.1f}), {edge.surface}, "
                  f"{edge.limit} km/h, lanes {edge.lanes}{', bridge' if edge.bridge else ''}  {edge.name or edge.number}")
    print(f"-> {out}/plan.png, profiles.png")


def cmd_gallery(args):
    """Junction plan views, tile by tile: the junctions each tile owns, 48 per sheet, the complicated ones first."""
    from rasters import HALF, SECTOR
    from . import tiled
    tag, sectors, out = build_stage.tag_of(args.list), sectors_of(args), debug.ensure_dir(args.out)
    count = 0
    for t in tiled.Area(tag, sectors, build_stage.MARGIN).tiles:
        network = build_stage.load_around(tag, sectors, (-HALF + (t[0] + 0.5) * SECTOR, -HALF + (t[1] + 0.5) * SECTOR))
        ids = [k for k, j in enumerate(network.junctions) if j.owner == t and j.vertices is not None]
        ids.sort(key=lambda k: -len(network.junctions[k].nodes))
        for page, start in enumerate(range(0, len(ids), 48)):
            debug.gallery(network, ids[start:start + 48], out / f"junctions_{t[0]}_{t[1]}_{page:02d}.png", radius=args.radius, columns=8)
        count += len(ids)
    print(f"{count} junctions -> {out}/junctions_*.png")


def main():
    parser = argparse.ArgumentParser(prog="python -m roads", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", default=DEFAULT_LIST, help="sector list json: the area to build")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("fetch").set_defaults(run=cmd_fetch)
    build = commands.add_parser("build")
    build.add_argument("--jobs", type=int, default=0, help="worker processes (default and most: half the cores, machine.py)")
    build.add_argument("--fresh", action="store_true", help="smooth every stroke and solve every height tile again, even those whose inputs did not change")
    build.set_defaults(run=cmd_build)
    commands.add_parser("report").set_defaults(run=cmd_report)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--at", required=True, help="X,Z in local metres (the game's K key copies them)")
    inspect.add_argument("--radius", type=float, default=60.0)
    inspect.add_argument("--out", default="data/big/roads/inspect")
    inspect.set_defaults(run=cmd_inspect)
    gallery = commands.add_parser("gallery")
    gallery.add_argument("--radius", type=float, default=30.0)
    gallery.add_argument("--out", default="data/big/roads/gallery")
    gallery.set_defaults(run=cmd_gallery)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
