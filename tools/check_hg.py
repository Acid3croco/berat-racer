"""Verify a world_hg build: parse chunks like WorldData.cs ChunkData.ParseMid, compare original sectors with ../world, check seams.
Usage: uv run python check_hg.py DIR [--full]"""
import gzip, json, struct, sys
from pathlib import Path
import numpy as np

CV = 101
class R:
    def __init__(s, b): s.b, s.o = b, 0
    def take(s, n): v = s.b[s.o:s.o + n]; s.o += n; assert len(v) == n; return v
    def i(s): return struct.unpack("<i", s.take(4))[0]
    def f(s): return struct.unpack("<f", s.take(4))[0]
    def u8(s): return s.take(1)[0]
    def fl(s, n): return np.frombuffer(s.take(4 * n), "<f4")
    def st(s):
        n, sh = 0, 0
        while True:
            c = s.u8(); n |= (c & 0x7F) << sh; sh += 7
            if c < 0x80: break
        return s.take(n).decode()

def parse_mid(raw):
    r = R(raw); assert r.take(4) == b"BM02"
    d = dict(ci=r.i(), cj=r.i()); assert r.i() == CV
    base, step = r.f(), r.f()
    q = np.frombuffer(r.take(CV * CV * 2), "<u2").reshape(CV, CV)
    d["H"] = base + q.astype(np.float64) * np.float32(step); d["step"] = step
    r.take(CV * CV * 3); r.take(26 * 26 * 3)
    for name in ("roads", "ctx"):
        n = r.i(); d[name] = n
        for _ in range(n):
            r.f(); r.u8(); r.u8(); r.u8(); r.u8(); r.i(); r.f(); r.fl(4); r.st(); r.fl(3 * r.i())
    na = r.i(); d["areas"] = na
    for _ in range(na): r.fl(3 * r.i())
    nl = r.i(); d["lines"] = nl
    for _ in range(nl): r.f(); r.u8(); r.u8(); r.fl(3 * r.i())
    nb = r.i(); d["bld"] = nb
    for _ in range(nb):
        r.fl(2 * r.i()); r.fl(3); r.fl(r.i()); r.take(6); r.st(); r.st(); r.i(); r.fl(r.i()); r.fl(2 * r.i()); n = r.i(); r.take(4 * n)
    assert r.o == len(raw), (r.o, len(raw))
    return d

def load(dirp, kind, ci, cj):
    f = Path(dirp) / "chunks" / f"{kind}_{ci}_{cj}.bin.gz"
    return gzip.open(f).read() if f.exists() else None

if __name__ == "__main__":
    dirp = sys.argv[1]; w = json.load(open(Path(dirp) / "world.json")); print(w)
    files = sorted((Path(dirp) / "chunks").glob("m_*"))
    print(len(files), "m chunks")
    H, S, maxstep, nparse = {}, {}, 0, 0
    for f in files:
        _, ci, cj = f.name.split(".")[0].split("_"); ci, cj = int(ci), int(cj)
        d = parse_mid(gzip.open(f).read()); assert (d["ci"], d["cj"]) == (ci, cj); H[ci, cj] = d["H"]; S[ci, cj] = d["step"]; maxstep = max(maxstep, d["step"]); nparse += 1
    print("parsed", nparse, "max step", maxstep)
    # seams: chunk (ci,cj) east column == chunk (ci+1,cj) west column
    worst = 0
    for (ci, cj), h in H.items():
        for n_, e in (((ci + 1, cj), np.abs(h[:, -1] - H.get((ci + 1, cj), h)[:, 0]).max()), ((ci, cj + 1), np.abs(h[-1, :] - H.get((ci, cj + 1), h)[0, :]).max())):
            if n_ in H: worst = max(worst, e / max(S[ci, cj], S[n_]))
    print("max seam mismatch in units of the coarser chunk step (<=1 = pure quantisation)", worst)
    # original sectors vs ../world (relative -> absolute: subtract x0 chunk offset)
    off_i, off_j = round((w["x0"] + 16000) / 400), round((w["z0"] + 16000) / 400)
    same = diff = 0; bad = []
    for (ci, cj) in H:
        ai, aj = ci + off_i, cj + off_j
        if not (8 <= ai < 72 and 8 <= aj < 72): continue                       # interior sectors only: the block edge now sees real neighbours
        old, new = load("../world", "m", ai, aj), load(dirp, "m", ci, cj)
        if old is None: continue
        step = struct.unpack("<f", new[20:24])[0]
        ok_tail = old[20:] == new[24:] and old[16:20] == new[16:20] and step == np.float32(0.005)
        if ok_tail: same += 1
        else:
            diff += 1; bad.append((ai, aj, step))
        assert load("../world", "n", ai, aj) == load(dirp, "n", ci, cj), ("n differs", ai, aj)
    print("original chunks byte-identical (bar header):", same, "different:", diff, bad[:5])
