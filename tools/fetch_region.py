"""Download the rest of former Midi-Pyrenees, one departement at a time (nearest to 31 first).
Before each departement: estimate its size from the sectors not yet on disk; if free space after it would be < 14 GB, stop (whole departements only).
Resumable: re-run the same command; finished departements are skipped quickly."""
import json, shutil, subprocess, sys
from pathlib import Path

ORDER = ["32", "81", "82", "65", "09", "12", "46"]
MIN_FREE = 14 * 2**30
MB_PER_SECTOR = 4.5 * 2**20            # measured 759 MB/1000 km2 raw, ~55 % less with gzip vectors, +margin; 10.24 km2 per sector
BIG = Path("data/big")

def missing(new):
    return [s for s in new if not (BIG / "hg" / f"ortho_{s[0]}_{s[1]}.jpg").exists()]

for code in ORDER:
    subprocess.run(["uv", "run", "python", "hg_sectors.py", code], check=True, stdout=subprocess.DEVNULL)
    S = json.load(open(BIG / f"hg_sectors_{code}.json"))
    todo = len(missing(S["new_sectors"]))
    free = shutil.disk_usage(".").free
    print(f"== dept {code}: {S['new']} sectors, {todo} without ortho yet, est {todo*MB_PER_SECTOR/2**30:.1f} GB, free {free/2**30:.1f} GB", flush=True)
    if free - todo * MB_PER_SECTOR < MIN_FREE:
        print(f"STOP before dept {code}: would drop under 14 GB", flush=True); sys.exit(3)
    r = subprocess.run(["uv", "run", "python", "-u", "fetch_hg.py", f"data/big/hg_sectors_{code}.json"])
    print(f"== dept {code} finished rc={r.returncode} free {shutil.disk_usage('.').free/2**30:.1f} GB", flush=True)
    if r.returncode != 0: sys.exit(r.returncode)
print("REGION ALL DONE", flush=True)
