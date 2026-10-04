"""Map package (docs/map-package.md).

  uv run python -m package build [--list data/big/small_sectors.json] [--out ../package/small] [--jobs N]
  uv run python -m package check [--out ../package/small]          decode every file, measure it, write previews/

The roads must be built first for the same list: `uv run python -m roads --list <list> build`.
"""
import argparse
import functools
import sys
from pathlib import Path

DEFAULT_LIST = "data/big/small_sectors.json"


def main():
    ap = argparse.ArgumentParser(prog="python -m package", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", default=DEFAULT_LIST, help="sector list json")
    ap.add_argument("--out", default="", help="package folder (default ../package/<tag>)")
    commands = ap.add_subparsers(dest="command", required=True)
    b = commands.add_parser("build")
    b.add_argument("--jobs", type=int, default=0, help="worker processes (default and most: half the cores, machine.py)")
    commands.add_parser("check")
    a = ap.parse_args()
    from roads import build as road_build
    out = Path(a.out or f"../package/{road_build.tag_of(a.list)}")
    log = functools.partial(print, flush=True)
    if a.command == "build":
        from . import export
        export.build(a.list, out, a.jobs, log)
    else:
        from . import check
        sys.exit(0 if check.check(out, log) else 1)


if __name__ == "__main__":
    main()
