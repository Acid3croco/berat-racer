"""Compare two built worlds file by file (chunk contents are compared uncompressed).

  uv run python compare_worlds.py ../world_small ../world_small_new
"""
import gzip
import sys
from pathlib import Path

import numpy as np


def content(path):
    return gzip.open(path).read() if path.suffix == ".gz" else path.read_bytes()


def terrain_difference(a, b):
    """Largest difference in metres between the 4 m terrain heights of two BM05 / BM06 chunks (None when the layout differs)."""
    if a[:4] not in (b"BM05", b"BM06") or b[:4] not in (b"BM05", b"BM06"):
        return None
    out = []
    for data in (a, b):
        cv = int(np.frombuffer(data, "<i4", 1, 12)[0])
        base, step = np.frombuffer(data, "<f4", 2, 16)
        out.append(base + step * np.frombuffer(data, "<u2", cv * cv, 24).astype(float))
    return float(np.abs(out[0] - out[1]).max()) if len(out[0]) == len(out[1]) else None


def main(dir_a, dir_b):
    a, b = Path(dir_a), Path(dir_b)
    names_a = {p.relative_to(a) for p in a.rglob("*") if p.is_file()}
    names_b = {p.relative_to(b) for p in b.rglob("*") if p.is_file()}
    print(f"{len(names_a)} files in {a}, {len(names_b)} in {b}; only in the first: {len(names_a - names_b)}, only in the second: {len(names_b - names_a)}")
    different, heights = [], []
    for name in sorted(names_a & names_b):
        x, y = content(a / name), content(b / name)
        if x != y:
            different.append(name)
            if name.name.startswith("m_"):
                heights.append(terrain_difference(x, y))
    print(f"identical: {len(names_a & names_b) - len(different)} of {len(names_a & names_b)}")
    if different:
        known = [h for h in heights if h is not None]
        print(f"different: {len(different)} ({sum(n.name.startswith('m_') for n in different)} terrain / road chunks, {sum(n.name.startswith('n_') for n in different)} plant chunks, "
              f"{sum(not n.name.startswith(('m_', 'n_')) for n in different)} other), e.g. {[str(n) for n in different[:4]]}")
        if known:
            print(f"4 m terrain in the differing chunks: largest height difference {max(known):.3f} m, median of the per-chunk largest {float(np.median(known)):.4f} m")


if __name__ == "__main__":
    main(*sys.argv[1:3])
