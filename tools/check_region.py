"""Verify a region build (BM05 - BM07 chunks; roads against terrain: check_roads.py): every m_ chunk parses to the last byte, n_ chunks parse, world.json / far.bin agree, steepest 4 m height step,
seam mismatches between neighbouring chunks. Usage: uv run python check_region.py DIR [--jobs 4]"""
import gzip, json, struct, sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np
from check_hg import R
from check_roads import parse_mid

def parse_near(raw):
    """BN01: trees, shrubs; BN02 adds the tree kinds, the ground class and row direction per vertex, vine rows, bays and hedges."""
    r = R(raw); magic = r.take(4); assert magic in (b"BN01", b"BN02")
    nt = r.i(); r.fl(4 * nt); ns = r.i(); r.fl(4 * ns)
    if magic == b"BN02":
        cv = 101
        r.take(nt); r.take(2 * cv * cv); r.fl(4 * r.i()); r.fl(4 * r.i())
        for _ in range(r.i()): r.fl(1); r.fl(2 * r.i())
    assert r.o == len(raw)
    return nt, ns

def one(f):
    f = Path(f); _, ci, cj = f.name.split(".")[0].split("_"); ci, cj = int(ci), int(cj)
    try:
        d = parse_mid(gzip.open(f).read()); assert (d["ci"], d["cj"]) == (ci, cj)
        n = f.with_name(f"n_{ci}_{cj}.bin.gz"); nt = parse_near(gzip.open(n).read()) if n.exists() else None
        H = d["H"]; gx = np.abs(np.diff(H, axis=1)).max(); gz = np.abs(np.diff(H, axis=0)).max()
        return dict(ci=ci, cj=cj, ok=True, step=d["step"], slope=float(max(gx, gz)), roads=d["roads"], bld=d["bld"], areas=d["areas"], lines=d["lines"],
                    nt=nt[0] if nt else -1, ns=nt[1] if nt else -1, e=H[:, -1].tolist(), n=H[-1, :].tolist(), w=H[:, 0].tolist(), s=H[0, :].tolist(), sz=f.stat().st_size)
    except Exception as e: return dict(ci=ci, cj=cj, ok=False, err=repr(e))

if __name__ == "__main__":
    dirp = Path(sys.argv[1]); jobs = int(sys.argv[sys.argv.index("--jobs") + 1]) if "--jobs" in sys.argv else 4
    w = json.load(open(dirp / "world.json")); print(w)
    fb = (dirp / "far.bin").stat().st_size; nx, nz, cell = struct.unpack("<iif", open(dirp / "far.bin", "rb").read(12))
    print("far.bin", fb, "expected", 24 + nx * nz * 4 + nx * nz * 3, "header", nx, nz, cell, "OK" if fb == 24 + nx * nz * 7 and (nx, nz, cell) == (w["farNx"], w["farNz"], w["farCell"]) else "MISMATCH")
    files = sorted((dirp / "chunks").glob("m_*")); print(len(files), "m chunks,", len(list((dirp / "chunks").glob("n_*"))), "n chunks")
    with ProcessPoolExecutor(jobs) as ex: res = list(ex.map(one, map(str, files), chunksize=64))
    bad = [r for r in res if not r["ok"]]; res = [r for r in res if r["ok"]]
    print("parsed", len(res), "failed", len(bad), bad[:5])
    print("max quantisation step", max(r["step"] for r in res), "| total m bytes", sum(r["sz"] for r in res))
    top = sorted(res, key=lambda r: -r["slope"])[:8]
    print("steepest 4 m steps (m per 4 m, chunk ci,cj):", [(round(r["slope"], 1), r["ci"], r["cj"]) for r in top])
    print("chunks with slope step > 4 m:", sum(r["slope"] > 4 for r in res), "> 8 m:", sum(r["slope"] > 8 for r in res))
    print("empty chunks (no roads, buildings, trees):", sum(r["roads"] == 0 and r["bld"] == 0 and r["nt"] <= 0 for r in res),
          "| totals roads", sum(r["roads"] for r in res), "buildings", sum(r["bld"] for r in res), "trees", sum(max(r["nt"], 0) for r in res))
    M = {(r["ci"], r["cj"]): r for r in res}; worst, cnt = 0.0, 0
    for (ci, cj), r in M.items():
        for k, (n_, a, b) in enumerate((((ci + 1, cj), "e", "w"), ((ci, cj + 1), "n", "s"))):
            if n_ in M:
                m = np.abs(np.array(r[a]) - np.array(M[n_][b])).max() / max(r["step"], M[n_]["step"]); cnt += 1; worst = max(worst, m)
    print("seams compared", cnt, "worst mismatch in coarser steps (<=1 is quantisation)", round(worst, 2))
    # sector completeness: every sector present has 64 chunks
    secs = {}
    for (ci, cj) in M: secs[(ci // 8, cj // 8)] = secs.get((ci // 8, cj // 8), 0) + 1
    print("sectors", len(secs), "incomplete", [(k, v) for k, v in secs.items() if v != 64][:10])
