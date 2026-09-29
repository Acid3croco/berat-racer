"""Round the corners of a road centreline: a quadratic Bezier per interior vertex, bounded so the road never moves far from its surveyed line."""
import numpy as np

def round_corners(xy, radius=7.0, min_turn_deg=1.5, samples=6):
    """xy: (n, 2) source vertices of a road. Every interior vertex whose turn exceeds min_turn_deg is replaced by a curve that starts and ends
    d = min(radius, 0.45 * adjacent edge lengths) before/after it and uses the vertex as its control point (tangent-continuous with both edges).
    The two end points never move, so joins with neighbouring roads stay exact. Deviation from the original polyline is at most ~0.25 * d."""
    xy = np.asarray(xy, float)
    keep = np.r_[True, np.hypot(*np.diff(xy, axis=0).T) > 1e-6]                 # drop repeated vertices
    xy = xy[keep]
    if len(xy) < 3: return xy
    seg = np.diff(xy, axis=0); ln = np.hypot(seg[:, 0], seg[:, 1]); u = seg / ln[:, None]
    out = [xy[0]]
    t = np.linspace(0.0, 1.0, samples + 1)[:, None]
    for i in range(1, len(xy) - 1):
        cosang = float(np.clip(u[i - 1] @ u[i], -1.0, 1.0))
        if np.degrees(np.arccos(cosang)) < min_turn_deg:
            out.append(xy[i]); continue
        d = min(radius, 0.45 * ln[i - 1], 0.45 * ln[i])
        a, b, v = xy[i] - u[i - 1] * d, xy[i] + u[i] * d, xy[i]
        out.extend(((1 - t) ** 2) * a + 2 * t * (1 - t) * v + (t ** 2) * b)
    out.append(xy[-1])
    return np.array(out)

if __name__ == "__main__":
    import json, sys
    from shapely.geometry import shape
    feats = json.load(open("data/big/vec/roads_5_5.json"))
    worst = 0.0; n = 0; turns = []
    for f in feats:
        g = f["geometry"]; lines = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
        for line in lines:
            a = np.array(line)[:, :2]
            if len(a) < 3: continue
            r = round_corners(a); n += 1
            from shapely.geometry import LineString
            worst = max(worst, LineString(r).hausdorff_distance(LineString(a)))
    print(f"{n} roads rounded, worst deviation from the surveyed line {worst:.2f} m")
