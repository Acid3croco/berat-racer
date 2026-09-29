using System.Collections.Generic;
using UnityEngine;

public enum Surface { Grass, Asphalt, Dirt }

/// <summary>Spatial hash of road segments: tells the car what it is driving on, and where bridge decks are.</summary>
public class RoadIndex
{
    struct Seg { public Vector2 a, b; public float ya, yb, hw; public Surface s; public bool bridge; }
    const float CellSize = 24f, FadeWidth = 0.6f;
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
                float m = s.hw + 0.7f;                                          // road half width + fade margin: a query near a cell border must still find the road
                int x0 = Mathf.FloorToInt((Mathf.Min(s.a.x, s.b.x) - m) / CellSize), x1 = Mathf.FloorToInt((Mathf.Max(s.a.x, s.b.x) + m) / CellSize);
                int z0 = Mathf.FloorToInt((Mathf.Min(s.a.y, s.b.y) - m) / CellSize), z1 = Mathf.FloorToInt((Mathf.Max(s.a.y, s.b.y) + m) / CellSize);
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

    /// <summary>
    /// Surface under (x,z). deckY: bridge deck within reach of refY (else NaN). roadY / roadWeight: height of the drawn road ribbon there
    /// (weight 1 on the carriageway, fading to 0 over 0.5 m beyond the edge), so the wheels follow exactly what is rendered.
    /// </summary>
    public Surface Query(float x, float z, float refY, out float deckY, out float roadY, out float roadWeight)
    {
        deckY = float.NaN; roadY = float.NaN; roadWeight = 0f; float bestD = 1e9f;
        Surface best = Surface.Grass;
        var p = new Vector2(x, z);
        int cx = Mathf.FloorToInt(x / CellSize), cz = Mathf.FloorToInt(z / CellSize);
        if (!grid.TryGetValue(Key(cx, cz), out var list)) return best;
        foreach (var s in list)
        {
            Vector2 ab = s.b - s.a;
            float t = Mathf.Clamp01(Vector2.Dot(p - s.a, ab) / Mathf.Max(ab.sqrMagnitude, 1e-4f));
            float d = (p - (s.a + ab * t)).magnitude;
            if (d > s.hw + FadeWidth) continue;
            float y = Mathf.Lerp(s.ya, s.yb, t);
            if (s.bridge)
            {
                if (d <= s.hw + 0.3f && Mathf.Abs(y - refY) < 2f) { deckY = y; best = s.s; }
                continue;
            }
            float wgt = d <= s.hw ? 1f : Mathf.SmoothStep(1f, 0f, (d - s.hw) / FadeWidth);
            // strongest coverage wins; among equals the NEAREST segment (never the highest: that made the ground jump between polyline vertices)
            if (wgt > roadWeight + 0.001f || (Mathf.Abs(wgt - roadWeight) <= 0.001f && d < bestD)) { roadWeight = wgt; roadY = y; bestD = d; }
            if (d <= s.hw + 0.3f && (best == Surface.Grass || s.s == Surface.Asphalt)) best = s.s;
        }
        return best;
    }

    public Surface Query(float x, float z, float refY, out float deckY) => Query(x, z, refY, out deckY, out _, out _);

    /// <summary>Distance from (x,z) to the nearest EDGE of any drivable surface (decks included); negative when on one. 999 when no road is near.</summary>
    public float EdgeClearance(float x, float z)
    {
        float best = 999f; var p = new Vector2(x, z);
        int cx = Mathf.FloorToInt(x / CellSize), cz = Mathf.FloorToInt(z / CellSize);
        for (int dx = -1; dx <= 1; dx++)
            for (int dz = -1; dz <= 1; dz++)
                if (grid.TryGetValue(Key(cx + dx, cz + dz), out var list))
                    foreach (var s in list)
                    {
                        Vector2 ab = s.b - s.a; float t = Mathf.Clamp01(Vector2.Dot(p - s.a, ab) / Mathf.Max(ab.sqrMagnitude, 1e-4f));
                        best = Mathf.Min(best, (p - (s.a + ab * t)).magnitude - s.hw);
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
