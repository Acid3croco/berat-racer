using System.Collections.Generic;
using UnityEngine;

public enum Surface { Grass, Asphalt, Dirt, Water }

/// <summary>Spatial hash of road segments: tells the car what it is driving on, and where bridge decks are.</summary>
public class RoadIndex
{
    struct Seg { public Vector2 a, b; public float ya, yb, hw; public Surface s; public bool bridge; public int owner; }
    const float CellSize = 24f, FadeWidth = 0.6f;
    readonly Dictionary<long, List<Seg>> grid = new Dictionary<long, List<Seg>>();

    static long Key(int cx, int cz) => ((long)cx << 32) ^ (uint)cz;

    readonly Dictionary<int, List<long>> ownerCells = new Dictionary<int, List<long>>();

    public RoadIndex() { }
    public RoadIndex(RoadData[] roads) { Add(-2, roads); }

    /// <summary>Registers the owned segments of these roads under an owner id (a chunk), so the whole set can be removed again.</summary>
    public void Add(int owner, RoadData[] roads)
    {
        if (!ownerCells.TryGetValue(owner, out var cells)) ownerCells[owner] = cells = new List<long>();
        foreach (var r in roads)
        {
            int n = r.pts.Length / 3;
            for (int k = r.lead; k < n - 1 - r.trail; k++)
            {
                int i = k * 3;
                var s = new Seg
                {
                    a = new Vector2(r.pts[i], r.pts[i + 2]), ya = r.pts[i + 1],
                    b = new Vector2(r.pts[i + 3], r.pts[i + 5]), yb = r.pts[i + 4],
                    hw = r.hw, s = r.dirt ? Surface.Dirt : Surface.Asphalt, bridge = r.bridge, owner = owner
                };
                float m = s.hw + 0.7f;                                          // road half width + fade margin: a query near a cell border must still find the road
                int x0 = Mathf.FloorToInt((Mathf.Min(s.a.x, s.b.x) - m) / CellSize), x1 = Mathf.FloorToInt((Mathf.Max(s.a.x, s.b.x) + m) / CellSize);
                int z0 = Mathf.FloorToInt((Mathf.Min(s.a.y, s.b.y) - m) / CellSize), z1 = Mathf.FloorToInt((Mathf.Max(s.a.y, s.b.y) + m) / CellSize);
                for (int cx = x0; cx <= x1; cx++)
                    for (int cz = z0; cz <= z1; cz++)
                    {
                        long key = Key(cx, cz);
                        if (!grid.TryGetValue(key, out var list)) grid[key] = list = new List<Seg>();
                        list.Add(s); cells.Add(key);
                    }
            }
        }
    }

    public void Remove(int owner)
    {
        if (!ownerCells.TryGetValue(owner, out var cells)) return;
        foreach (long key in new HashSet<long>(cells))
            if (grid.TryGetValue(key, out var list)) { list.RemoveAll(s => s.owner == owner); if (list.Count == 0) grid.Remove(key); }
        ownerCells.Remove(owner);
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
            float tRaw = Vector2.Dot(p - s.a, ab) / Mathf.Max(ab.sqrMagnitude, 1e-4f), t = Mathf.Clamp01(tRaw);
            float d = (p - (s.a + ab * t)).magnitude;
            if (d > s.hw + FadeWidth) continue;
            if (s.bridge)
            {   // a deck ends square at its abutment: no rounded cap sticking out over the approach road (that made a step of up to half a metre)
                float slack = 0.25f / Mathf.Max(ab.magnitude, 0.25f);
                if (tRaw < -slack || tRaw > 1f + slack) continue;
            }
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

    /// <summary>Nearest ground road (bridges excluded) within `reach` metres of its edge: its surface height there and the distance from (x,z) to its edge (negative on the carriageway).</summary>
    public bool NearestEdge(float x, float z, float reach, out float roadY, out float edgeDist)
    {
        roadY = 0f; edgeDist = 1e9f; var p = new Vector2(x, z);
        int cx = Mathf.FloorToInt(x / CellSize), cz = Mathf.FloorToInt(z / CellSize);
        for (int dx = -1; dx <= 1; dx++)
            for (int dz = -1; dz <= 1; dz++)
                if (grid.TryGetValue(Key(cx + dx, cz + dz), out var list))
                    foreach (var s in list)
                    {
                        if (s.bridge) continue;
                        Vector2 ab = s.b - s.a; float t = Mathf.Clamp01(Vector2.Dot(p - s.a, ab) / Mathf.Max(ab.sqrMagnitude, 1e-4f));
                        float e = (p - (s.a + ab * t)).magnitude - s.hw;
                        if (e < edgeDist) { edgeDist = e; roadY = Mathf.Lerp(s.ya, s.yb, t); }
                    }
        return edgeDist < reach;
    }

    /// <summary>
    /// The road is the top level: terrain is never above it. Within 5.7 m of a road edge (any terrain triangle touching the ribbon has all its corners that close)
    /// the ground is cut down to just below the road surface; beyond that it may climb at most 50% back to its natural height. Used for the terrain mesh AND for physics.
    /// </summary>
    public float CutTerrain(float x, float z, float y)
    {
        if (!NearestEdge(x, z, 12f, out float ry, out float e)) return y;
        return Mathf.Min(y, ry - 0.03f + Mathf.Max(0f, e - 5.7f) * 0.5f);
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
