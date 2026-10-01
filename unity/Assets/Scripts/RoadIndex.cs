using System.Collections.Generic;
using UnityEngine;

public enum Surface { Grass, Asphalt, Dirt, Water }

/// <summary>
/// Spatial hash of the road surface: the very triangles that are drawn (road quads between the two shipped edges, junction meshes),
/// so the wheels follow exactly what is rendered, cross slope included. Also keeps the centrelines, for "which way does the road run here".
/// </summary>
public class RoadIndex
{
    struct Tri { public Vector2 a, b, c; public float ya, yb, yc; public Surface s; public bool bridge, tunnel; public int owner; }
    public const float TunnelHeight = 5.0f;                 // inside a tunnel everything under its ceiling stands on the tunnel road
    struct Line { public Vector2 a, b; public bool bridge; public int owner; }
    const float CellSize = 12f, FadeWidth = 0.6f;
    readonly Dictionary<long, List<Tri>> grid = new Dictionary<long, List<Tri>>();
    // BM07 embankment ribbons: the ground beside the roads (not a road). Asked for many times per physics step (wheels, hull, contact
    // patch): their own fine grid, and each triangle's bounding box checked before anything else.
    struct RTri { public Vector2 a, b, c; public float ya, yb, yc, x0, z0, x1, z1; public int owner; }
    const float RibbonCell = 4f;
    readonly Dictionary<long, List<RTri>> ribbons = new Dictionary<long, List<RTri>>();
    readonly Dictionary<long, List<Line>> lines = new Dictionary<long, List<Line>>();
    readonly Dictionary<int, List<long>> ownerCells = new Dictionary<int, List<long>>();

    static long Key(int cx, int cz) => ((long)cx << 32) ^ (uint)cz;
    static int Cell(float v) => Mathf.FloorToInt(v / CellSize);

    /// <summary>Registers the surface of these roads and junctions under an owner id (a chunk), so the whole set can be removed again.</summary>
    public void Add(int owner, RoadData[] roads, JunctionData[] junctions)
    {
        if (!ownerCells.TryGetValue(owner, out var cells)) ownerCells[owner] = cells = new List<long>();
        foreach (var r in roads)
        {
            var s = r.dirt ? Surface.Dirt : Surface.Asphalt;
            for (int k = 0; k + 1 < r.Count; k++)
            {
                Put(lines, cells, new Line { a = new Vector2(r.pts[k * 3], r.pts[k * 3 + 2]), b = new Vector2(r.pts[k * 3 + 3], r.pts[k * 3 + 5]), bridge = r.bridge, owner = owner }, 0f);
                if (!r.drawn[k]) continue;
                Vector3 l0 = r.P(r.left, k), r0 = r.P(r.right, k), l1 = r.P(r.left, k + 1), r1 = r.P(r.right, k + 1);      // the same two triangles as the drawn quad
                AddTri(cells, l0, r0, r1, s, r.bridge, owner, r.tunnel); AddTri(cells, l0, r1, l1, s, r.bridge, owner, r.tunnel);      // a tunnel road, like a deck, holds only what is in it (not the hill above)
            }
        }
        foreach (var j in junctions)
        {
            var s = j.dirt ? Surface.Dirt : Surface.Asphalt;
            for (int t = 0; t + 2 < j.tri.Length; t += 3) AddTri(cells, V(j, j.tri[t]), V(j, j.tri[t + 1]), V(j, j.tri[t + 2]), s, false, owner);
        }
    }

    /// <summary>Registers paved areas (car parks) as asphalt under an owner id, like a road's surface.</summary>
    public void AddPaved(int owner, PavedArea[] areas)
    {
        if (areas.Length == 0) return;
        if (!ownerCells.TryGetValue(owner, out var cells)) ownerCells[owner] = cells = new List<long>();
        foreach (var p in areas)
            for (int k = 0; k + 2 < p.t.Length; k += 3) AddTri(cells, p.V(p.t[k]), p.V(p.t[k + 1]), p.V(p.t[k + 2]), Surface.Asphalt, false, owner);
    }

    readonly Dictionary<int, List<long>> ribbonCells = new Dictionary<int, List<long>>();

    /// <summary>The embankment ribbons of these roads and junctions as ground for the car (BM07; only where the car can be: the near chunks).</summary>
    public void AddRibbons(int owner, RoadData[] roads, JunctionData[] junctions, float[] seam = null)
    {
        if (!ribbonCells.TryGetValue(owner, out var cells)) ribbonCells[owner] = cells = new List<long>();
        void Add(Vector3 a, Vector3 b, Vector3 c)
        {
            var t = new RTri { a = new Vector2(a.x, a.z), b = new Vector2(b.x, b.z), c = new Vector2(c.x, c.z), ya = a.y, yb = b.y, yc = c.y, owner = owner,
                               x0 = Mathf.Min(a.x, Mathf.Min(b.x, c.x)), x1 = Mathf.Max(a.x, Mathf.Max(b.x, c.x)), z0 = Mathf.Min(a.z, Mathf.Min(b.z, c.z)), z1 = Mathf.Max(a.z, Mathf.Max(b.z, c.z)) };
            for (int cx = Mathf.FloorToInt(t.x0 / RibbonCell); cx <= Mathf.FloorToInt(t.x1 / RibbonCell); cx++)
                for (int cz = Mathf.FloorToInt(t.z0 / RibbonCell); cz <= Mathf.FloorToInt(t.z1 / RibbonCell); cz++)
                {
                    long key = Key(cx, cz);
                    if (!ribbons.TryGetValue(key, out var list)) ribbons[key] = list = new List<RTri>();
                    list.Add(t); cells.Add(key);
                }
        }
        Ribbons.Triangles(roads, junctions, Add);
        if (seam != null)                                                                // BM07: the seam to the kept terrain cells is ground too
            for (int k = 0; k + 8 < seam.Length; k += 9)
                Add(new Vector3(seam[k], seam[k + 1], seam[k + 2]), new Vector3(seam[k + 3], seam[k + 4], seam[k + 5]), new Vector3(seam[k + 6], seam[k + 7], seam[k + 8]));
    }

    public void RemoveRibbons(int owner)
    {
        if (!ribbonCells.TryGetValue(owner, out var cells)) return;
        foreach (long key in new HashSet<long>(cells))
            if (ribbons.TryGetValue(key, out var rl)) { rl.RemoveAll(t => t.owner == owner); if (rl.Count == 0) ribbons.Remove(key); }
        ribbonCells.Remove(owner);
    }

    static Vector3 V(JunctionData j, int i) => new Vector3(j.v[i * 3], j.v[i * 3 + 1], j.v[i * 3 + 2]);

    void AddTri(List<long> cells, Vector3 a, Vector3 b, Vector3 c, Surface s, bool bridge, int owner, bool tunnel = false, Dictionary<long, List<Tri>> into = null)
    {
        into = into ?? grid;
        var t = new Tri { a = new Vector2(a.x, a.z), b = new Vector2(b.x, b.z), c = new Vector2(c.x, c.z), ya = a.y, yb = b.y, yc = c.y, s = s, bridge = bridge, tunnel = tunnel, owner = owner };
        float m = FadeWidth + 0.1f;                                         // a query near a cell border must still find the road
        int x0 = Cell(Mathf.Min(t.a.x, Mathf.Min(t.b.x, t.c.x)) - m), x1 = Cell(Mathf.Max(t.a.x, Mathf.Max(t.b.x, t.c.x)) + m);
        int z0 = Cell(Mathf.Min(t.a.y, Mathf.Min(t.b.y, t.c.y)) - m), z1 = Cell(Mathf.Max(t.a.y, Mathf.Max(t.b.y, t.c.y)) + m);
        for (int cx = x0; cx <= x1; cx++)
            for (int cz = z0; cz <= z1; cz++)
            {
                long key = Key(cx, cz);
                if (!into.TryGetValue(key, out var list)) into[key] = list = new List<Tri>();
                list.Add(t); cells.Add(key);
            }
    }

    static void Put(Dictionary<long, List<Line>> map, List<long> cells, Line l, float margin)
    {
        int x0 = Cell(Mathf.Min(l.a.x, l.b.x) - margin), x1 = Cell(Mathf.Max(l.a.x, l.b.x) + margin);
        int z0 = Cell(Mathf.Min(l.a.y, l.b.y) - margin), z1 = Cell(Mathf.Max(l.a.y, l.b.y) + margin);
        for (int cx = x0; cx <= x1; cx++)
            for (int cz = z0; cz <= z1; cz++)
            {
                long key = Key(cx, cz);
                if (!map.TryGetValue(key, out var list)) map[key] = list = new List<Line>();
                list.Add(l); cells.Add(key);
            }
    }

    public void Remove(int owner)
    {
        if (!ownerCells.TryGetValue(owner, out var cells)) return;
        foreach (long key in new HashSet<long>(cells))
        {
            if (grid.TryGetValue(key, out var list)) { list.RemoveAll(t => t.owner == owner); if (list.Count == 0) grid.Remove(key); }
            if (ribbons.TryGetValue(key, out var rl)) { rl.RemoveAll(t => t.owner == owner); if (rl.Count == 0) ribbons.Remove(key); }
            if (lines.TryGetValue(key, out var ll)) { ll.RemoveAll(l => l.owner == owner); if (ll.Count == 0) lines.Remove(key); }
        }
        ownerCells.Remove(owner);
    }

    static float Cross(Vector2 a, Vector2 b) => a.x * b.y - a.y * b.x;

    /// <summary>Distance from p to the edge a-b and the height at the closest point.</summary>
    static float ToEdge(Vector2 p, Vector2 a, Vector2 b, float ya, float yb, out float y)
    {
        Vector2 ab = b - a; float t = Mathf.Clamp01(Vector2.Dot(p - a, ab) / Mathf.Max(ab.sqrMagnitude, 1e-8f));
        y = Mathf.Lerp(ya, yb, t);
        return (p - (a + ab * t)).magnitude;
    }

    /// <summary>Distance from p to the triangle (0 inside) and the surface height at p, or at the closest point of the triangle.</summary>
    static float Distance(in Tri t, Vector2 p, out float y)
    {
        float area = Cross(t.b - t.a, t.c - t.a);
        if (Mathf.Abs(area) > 1e-6f)
        {
            float wb = Cross(p - t.a, t.c - t.a) / area, wc = Cross(t.b - t.a, p - t.a) / area, wa = 1f - wb - wc;
            if (wa >= 0f && wb >= 0f && wc >= 0f) { y = wa * t.ya + wb * t.yb + wc * t.yc; return 0f; }
        }
        float d = ToEdge(p, t.a, t.b, t.ya, t.yb, out y);
        float d2 = ToEdge(p, t.b, t.c, t.yb, t.yc, out float y2); if (d2 < d) { d = d2; y = y2; }
        float d3 = ToEdge(p, t.c, t.a, t.yc, t.ya, out float y3); if (d3 < d) { d = d3; y = y3; }
        return d;
    }

    /// <summary>
    /// Surface under (x,z). deckY: bridge deck within reach of refY (else NaN). roadY / roadWeight: height of the road surface there
    /// (weight 1 on the carriageway, fading to 0 over 0.6 m beyond its edge).
    /// </summary>
    public Surface Query(float x, float z, float refY, out float deckY, out float roadY, out float roadWeight)
    {
        deckY = float.NaN; roadY = float.NaN; roadWeight = 0f; float bestD = 1e9f;
        Surface best = Surface.Grass;
        if (!grid.TryGetValue(Key(Cell(x), Cell(z)), out var list)) return best;
        var p = new Vector2(x, z);
        for (int i = 0; i < list.Count; i++)
        {
            var t = list[i];
            float d = Distance(t, p, out float y);
            if (t.bridge)
            {
                if (d <= 0.3f && Mathf.Abs(y - refY) < 2f) { deckY = y; best = t.s; }
                continue;
            }
            if (t.tunnel)
            {
                if (d <= 0.3f && refY > y - 2f && refY < y + TunnelHeight + 1f) { deckY = y; best = t.s; }
                continue;
            }
            if (d > FadeWidth) continue;
            float wgt = d <= 0f ? 1f : Mathf.SmoothStep(1f, 0f, d / FadeWidth);
            if (wgt > roadWeight + 0.001f || (Mathf.Abs(wgt - roadWeight) <= 0.001f && d < bestD)) { roadWeight = wgt; roadY = y; bestD = d; }
            if (d <= 0.3f && (best == Surface.Grass || t.s == Surface.Asphalt)) best = t.s;
        }
        return best;
    }

    public Surface Query(float x, float z, float refY, out float deckY) => Query(x, z, refY, out deckY, out _, out _);

    /// <summary>Height of the embankment ribbon at (x, z) (the highest where two overlap: the one drawn on top), NaN where there is none.</summary>
    public float RibbonHeight(float x, float z)
    {
        float best = float.NaN;
        if (!ribbons.TryGetValue(Key(Mathf.FloorToInt(x / RibbonCell), Mathf.FloorToInt(z / RibbonCell)), out var list)) return best;
        for (int i = 0; i < list.Count; i++)
        {
            var t = list[i];
            if (x < t.x0 || x > t.x1 || z < t.z0 || z > t.z1) continue;
            float area = (t.b.x - t.a.x) * (t.c.y - t.a.y) - (t.b.y - t.a.y) * (t.c.x - t.a.x); if (Mathf.Abs(area) < 1e-6f) continue;
            float wb = ((x - t.a.x) * (t.c.y - t.a.y) - (z - t.a.y) * (t.c.x - t.a.x)) / area, wc = ((t.b.x - t.a.x) * (z - t.a.y) - (t.b.y - t.a.y) * (x - t.a.x)) / area;
            if (wb < 0f || wc < 0f || wb + wc > 1f) continue;
            float y = (1f - wb - wc) * t.ya + wb * t.yb + wc * t.yc;
            if (!(y <= best)) best = y;
        }
        return best;
    }

    /// <summary>Distance from (x,z) to the nearest drivable surface (decks included); -1 when on one. 999 when no road is within a dozen metres.</summary>
    public float EdgeClearance(float x, float z)
    {
        float best = 999f; var p = new Vector2(x, z);
        int cx = Cell(x), cz = Cell(z);
        for (int dx = -1; dx <= 1; dx++)
            for (int dz = -1; dz <= 1; dz++)
                if (grid.TryGetValue(Key(cx + dx, cz + dz), out var list))
                    for (int i = 0; i < list.Count; i++)
                    {
                        float d = Distance(list[i], p, out _);
                        if (d <= 0f) return -1f;
                        if (d < best) best = d;
                    }
        return best;
    }

    /// <summary>Is there a bridge deck within `radius` metres (horizontally, measured from the deck edge) of (x,z)? Gives the deck height there.</summary>
    public bool BridgeDeckNear(float x, float z, float radius, out float deckY)
    {
        deckY = 0f; var p = new Vector2(x, z);
        int cx = Cell(x), cz = Cell(z);
        for (int dx = -1; dx <= 1; dx++)
            for (int dz = -1; dz <= 1; dz++)
                if (grid.TryGetValue(Key(cx + dx, cz + dz), out var list))
                    for (int i = 0; i < list.Count; i++)
                        if ((list[i].bridge || list[i].tunnel) && Distance(list[i], p, out deckY) < radius) return true;
        return false;
    }

    /// <summary>Nearest non-bridge road centreline within maxDist: returns its direction (unit vector in x,z).</summary>
    public bool NearestRoad(float x, float z, float maxDist, out Vector2 dir)
    {
        dir = Vector2.zero;
        var p = new Vector2(x, z);
        int reach = Mathf.CeilToInt(maxDist / CellSize), cx = Cell(x), cz = Cell(z);
        float best = maxDist * maxDist; bool found = false;
        for (int dx = -reach; dx <= reach; dx++)
            for (int dz = -reach; dz <= reach; dz++)
                if (lines.TryGetValue(Key(cx + dx, cz + dz), out var list))
                    foreach (var l in list)
                    {
                        if (l.bridge) continue;
                        Vector2 ab = l.b - l.a;
                        float t = Mathf.Clamp01(Vector2.Dot(p - l.a, ab) / Mathf.Max(ab.sqrMagnitude, 1e-4f));
                        float d2 = (p - (l.a + ab * t)).sqrMagnitude;
                        if (d2 < best) { best = d2; dir = ab.normalized; found = true; }
                    }
        return found;
    }
}
