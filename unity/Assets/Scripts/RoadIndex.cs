using System.Collections.Generic;
using UnityEngine;

public enum Surface { Grass, Asphalt, Dirt }

/// <summary>Spatial hash of road segments: tells the car what it is driving on, and where bridge decks are.</summary>
public class RoadIndex
{
    struct Seg { public Vector2 a, b; public float ya, yb, hw; public Surface s; public bool bridge; }
    const float CellSize = 24f;
    readonly Dictionary<long, List<Seg>> grid = new Dictionary<long, List<Seg>>();

    static long Key(int cx, int cz) => ((long)cx << 32) ^ (uint)cz;

    public RoadIndex(RoadData[] roads)
    {
        foreach (var r in roads)
        {
            for (int i = 0; i + 5 < r.pts.Length; i += 3)
            {
                var s = new Seg
                {
                    a = new Vector2(r.pts[i], r.pts[i + 2]), ya = r.pts[i + 1],
                    b = new Vector2(r.pts[i + 3], r.pts[i + 5]), yb = r.pts[i + 4],
                    hw = r.hw, s = r.dirt ? Surface.Dirt : Surface.Asphalt, bridge = r.bridge
                };
                int x0 = Mathf.FloorToInt(Mathf.Min(s.a.x, s.b.x) / CellSize), x1 = Mathf.FloorToInt(Mathf.Max(s.a.x, s.b.x) / CellSize);
                int z0 = Mathf.FloorToInt(Mathf.Min(s.a.y, s.b.y) / CellSize), z1 = Mathf.FloorToInt(Mathf.Max(s.a.y, s.b.y) / CellSize);
                for (int cx = x0; cx <= x1; cx++)
                    for (int cz = z0; cz <= z1; cz++)
                    {
                        long k = Key(cx, cz);
                        if (!grid.TryGetValue(k, out var list)) grid[k] = list = new List<Seg>();
                        list.Add(s);
                    }
            }
        }
    }

    /// <summary>Surface under (x,z). If a bridge deck is within reach of refY, deckY is set (else NaN).</summary>
    public Surface Query(float x, float z, float refY, out float deckY)
    {
        deckY = float.NaN;
        Surface best = Surface.Grass;
        var p = new Vector2(x, z);
        int cx = Mathf.FloorToInt(x / CellSize), cz = Mathf.FloorToInt(z / CellSize);
        if (!grid.TryGetValue(Key(cx, cz), out var list)) return best;
        foreach (var s in list)
        {
            Vector2 ab = s.b - s.a;
            float t = Mathf.Clamp01(Vector2.Dot(p - s.a, ab) / Mathf.Max(ab.sqrMagnitude, 1e-4f));
            if ((p - (s.a + ab * t)).sqrMagnitude > (s.hw + 0.3f) * (s.hw + 0.3f)) continue;
            float y = Mathf.Lerp(s.ya, s.yb, t);
            if (s.bridge) { if (Mathf.Abs(y - refY) < 2f) { deckY = y; best = s.s; } }
            else if (best == Surface.Grass || s.s == Surface.Asphalt) best = s.s;
        }
        return best;
    }

    /// <summary>Nearest non-bridge road within maxDist: returns its travel direction (unit vector in x,z).</summary>
    public bool NearestRoad(float x, float z, float maxDist, out Vector2 dir)
    {
        dir = Vector2.zero;
        var p = new Vector2(x, z);
        int cx = Mathf.FloorToInt(x / CellSize), cz = Mathf.FloorToInt(z / CellSize);
        float best = maxDist * maxDist; bool found = false;
        for (int dx = -1; dx <= 1; dx++)
            for (int dz = -1; dz <= 1; dz++)
                if (grid.TryGetValue(Key(cx + dx, cz + dz), out var list))
                    foreach (var sg in list)
                    {
                        if (sg.bridge) continue;
                        Vector2 ab = sg.b - sg.a;
                        float t = Mathf.Clamp01(Vector2.Dot(p - sg.a, ab) / Mathf.Max(ab.sqrMagnitude, 1e-4f));
                        float d2 = (p - (sg.a + ab * t)).sqrMagnitude;
                        if (d2 < best) { best = d2; dir = ab.normalized; found = true; }
                    }
        return found;
    }
}
