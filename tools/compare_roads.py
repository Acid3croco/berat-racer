"""Compare two road builds of the same area: heights, junction planes, outlines.

  uv run python compare_roads.py smallbase smallnew        # tags, as in data/big/roads/<tag>.network.pkl
"""
import dataclasses
import sys

import numpy as np

from roads import build


def difference(a, b):
    """Largest absolute difference between two values of the same structure (arrays, dataclasses, lists, numbers); inf when they differ in kind."""
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        a, b = np.asarray(a), np.asarray(b)
        if a.shape != b.shape:
            return np.inf
        if a.dtype.kind in "fc" or b.dtype.kind in "fc":
            same_nan = np.isnan(a) == np.isnan(b)
            return float(np.nanmax(np.abs(a - b), initial=0.0)) if same_nan.all() else np.inf
        return 0.0 if np.array_equal(a, b) else np.inf
    if dataclasses.is_dataclass(a) and type(a) is type(b):
        return max((difference(getattr(a, f.name), getattr(b, f.name)) for f in dataclasses.fields(a)), default=0.0)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return max((difference(x, y) for x, y in zip(a, b)), default=0.0) if len(a) == len(b) else np.inf
    if isinstance(a, dict) and isinstance(b, dict):
        return max((difference(a[k], b[k]) for k in a), default=0.0) if a.keys() == b.keys() else np.inf
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) if not (np.isnan(a) and np.isnan(b)) else 0.0
    return 0.0 if a == b else np.inf


def main(tag_a, tag_b):
    a, b = build.load(tag_a), build.load(tag_b)
    assert len(a.links) == len(b.links) and len(a.junctions) == len(b.junctions), "the two networks differ in links or junctions"
    same_xy = all(np.array_equal(p.xy, q.xy) for p, q in zip(a.links, b.links))
    dz = np.abs(np.concatenate([p.z - q.z for p, q in zip(a.links, b.links)]))
    planes = np.abs(np.array([p.plane - q.plane for p, q in zip(a.junctions, b.junctions)]))
    print(f"links {len(a.links)}, junctions {len(a.junctions)}, samples {len(dz)}, plan geometry identical: {same_xy}")
    print(f"height difference  mean {dz.mean() * 1000:.2f} mm   p50 {np.median(dz) * 1000:.2f}   p99 {np.percentile(dz, 99) * 1000:.1f}   "
          f"p99.9 {np.percentile(dz, 99.9) * 1000:.1f}   max {dz.max() * 1000:.0f} mm   over 1 cm: {(dz > 0.01).mean():.3%}   over 5 cm: {(dz > 0.05).mean():.4%}")
    print(f"junction planes    height max {planes[:, 0].max() * 1000:.0f} mm, mean {planes[:, 0].mean() * 1000:.2f} mm   slope max {planes[:, 1:].max() * 100:.3f} %")
    for name, net in ((tag_a, a), (tag_b, b)):
        err = np.abs(np.concatenate([link.z - link.ground for link in net.links]))
        print(f"  {name:<12} road to ground: mean {err.mean() * 100:.2f} cm, within 10 cm {np.mean(err < 0.1):.2%}")
    worst = [difference(build.load_sector(tag_a, *sector), build.load_sector(tag_b, *sector)) for sector in a.sectors]
    print(f"sector files (what the world builder reads): {sum(w == 0.0 for w in worst)} of {len(worst)} identical, largest difference {max(worst) * 1000:.2e} mm")


if __name__ == "__main__":
    main(*sys.argv[1:3])
