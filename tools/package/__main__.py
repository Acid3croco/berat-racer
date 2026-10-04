"""Map package (docs/map-package.md).

  uv run python -m package [--list L] fetch-hires [--no-ortho]     LiDAR ground at 0.5 m, orthophoto at 0.2 m (package/hires.py)
  uv run python -m package [--list L] [--out DIR] build [--cell 1] [--jobs N]
  uv run python -m package [--out DIR] check                       decode every file, measure it, write previews/

The roads must be built first for the same list: `uv run python -m roads --list <list> build`. A cell under 2 m needs the 0.5 m
ground (fetch-hires); the 0.2 m orthophoto goes in the package when it has been fetched.
"""
import argparse
import functools
import json
import sys
from pathlib import Path

DEFAULT_LIST = "data/big/small_sectors.json"


def main():
    ap = argparse.ArgumentParser(prog="python -m package", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", default=DEFAULT_LIST, help="sector list json")
    ap.add_argument("--out", default="", help="package folder (default ../package/<tag>)")
    commands = ap.add_subparsers(dest="command", required=True)
    b = commands.add_parser("build")
    b.add_argument("--cell", type=float, default=2.0, help="terrain vertex spacing in metres: 2, 1 or 0.5 (under 2: fetch-hires first)")
    b.add_argument("--jobs", type=int, default=0, help="worker processes (default and most: half the cores, machine.py)")
    f = commands.add_parser("fetch-hires")
    f.add_argument("--no-ortho", action="store_true", help="the 0.5 m ground only")
    commands.add_parser("check")
    a = ap.parse_args()
    from roads import build as road_build
    out = Path(a.out or f"../package/{road_build.tag_of(a.list)}")
    log = functools.partial(print, flush=True)
    if a.command == "build":
        from . import export
        export.build(a.list, out, a.jobs, log, cell=a.cell)
    elif a.command == "fetch-hires":
        from . import hires
        sectors = [tuple(s) for s in json.loads(Path(a.list).read_text())["sectors"]]
        result = hires.fetch(sectors, ortho=not a.no_ortho, log=log)
        log(f"hires: {result['fetched']} fetched, failures: {result['failures'][:5]}")
        sys.exit(1 if result["failures"] else 0)
    else:
        from . import check
        sys.exit(0 if check.check(out, log) else 1)


if __name__ == "__main__":
    main()
