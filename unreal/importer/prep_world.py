"""The whole package as landscape regions: windows of N x N components tiling the world from its north-west corner, each
prepared by prep.py (window mode), several at a time. Regions tile exactly (no overlap) whatever the sector grid.

    uv run python prep_world.py C:/Users/jack/berat70scale-1m --out C:/Users/jack/berat-cache/world --region 25 --jobs 4
"""

import argparse
import json
import math
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("package", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--region", type=int, default=25, help="components per region side")
    ap.add_argument("--component", type=int, default=510, help="quads per component (2 x 255)")
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()
    man = json.loads((a.package / "manifest.json").read_text())
    size, cell = man["sector_size"], man["cell"]
    i0 = min(s[0] for s in man["sectors"]); i1 = max(s[0] for s in man["sectors"])
    j0 = min(s[1] for s in man["sectors"]); j1 = max(s[1] for s in man["sectors"])
    west, north = size * i0 - 16000, size * (j1 + 1) - 16000
    east, south = size * (i1 + 1) - 16000, size * j0 - 16000
    span = a.region * a.component * cell                       # metres per region
    nx, ny = math.ceil((east - west) / span), math.ceil((north - south) / span)
    jobs = []
    for ry in range(ny):
        for rx in range(nx):
            name = f"r{rx}_{ry}"
            out = a.out / name
            if (out / "block.json").exists():
                continue
            cmd = [sys.executable, str(Path(__file__).with_name("prep.py")), str(a.package), "--out", str(out),
                   "--window", str(west + rx * span), str(north - ry * span), str(a.region), str(a.region)]
            jobs.append((name, cmd))
    print(f"world {west}..{east} x {south}..{north} m: {nx} x {ny} regions of {span:.0f} m, {len(jobs)} to prepare")
    t0 = time.time()

    def run(job):
        name, cmd = job
        r = subprocess.run(cmd, capture_output=True, text=True)
        ok = r.returncode == 0
        print(f"{name}: {'ok' if ok else 'FAILED'} at {time.time() - t0:.0f} s {'' if ok else r.stderr[-400:]}", flush=True)
        return ok

    with ThreadPoolExecutor(a.jobs) as ex:
        results = list(ex.map(run, jobs))
    (a.out / "world.json").write_text(json.dumps({"regions": [nx, ny], "region_m": span, "west": west, "north": north,
                                                  "component": a.component, "region_components": a.region}, indent=1))
    print(f"done: {sum(results)} of {len(jobs)} in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
