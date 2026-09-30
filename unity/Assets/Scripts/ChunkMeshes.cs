using System.Collections.Generic;
using UnityEngine;

public struct Obstacle { public Vector3 pos; public float r, h; }

/// <summary>
/// CPU-side geometry of one chunk. Pure data (no Unity objects), so it can be built on a worker thread; the streamer turns the
/// builders into Meshes on the main thread. BuildMid makes what is visible out to ~5 km, BuildNear the fine detail close to the car.
/// </summary>
public class ChunkMeshes
{
    // mid tier
    public readonly MeshBuilder TerrainLow = new MeshBuilder(), Roads = new MeshBuilder(), Buildings = new MeshBuilder(), Water = new MeshBuilder();
    // near tier
    public readonly MeshBuilder Terrain = new MeshBuilder(), Marks = new MeshBuilder(), Facades = new MeshBuilder(), Collision = new MeshBuilder(), Trees = new MeshBuilder(), Street = new MeshBuilder();
    public readonly List<Obstacle> Obstacles = new List<Obstacle>();

    struct BoxSpec { public Vector3 c, size; public float yaw; }
    static Color32 C(int r, int g, int b) => new Color32((byte)r, (byte)g, (byte)b, 255);
    static Color32 Tint(Color32 c, float f) => new Color32((byte)Mathf.Clamp(c.r * f, 0, 255), (byte)Mathf.Clamp(c.g * f, 0, 255), (byte)Mathf.Clamp(c.b * f, 0, 255), 255);
    static readonly Color32 Asphalt = new Color32(94, 94, 98, 255), Shoulder = new Color32(108, 102, 84, 255), Dirt = new Color32(158, 132, 96, 255),
        Concrete = new Color32(170, 168, 160, 255), Wall = new Color32(150, 142, 126, 255), Paint = new Color32(236, 232, 214, 255), WaterCol = new Color32(52, 96, 112, 255);
    public const float RoadLift = 0.012f;                                  // the drawn road sits a hair above the road height; the terrain is cut away instead of the road being raised

    // ------------------------------------------------------------------ mid tier
    public static ChunkMeshes BuildMid(ChunkData d)
    {
        var m = new ChunkMeshes();
        m.BuildTerrainLow(d);
        m.BuildRoads(d);
        m.BuildBuildingShells(d);
        m.BuildWater(d);
        return m;
    }

    /// <summary>16 m terrain (26 x 26 vertices, heights from the world builder: already below every road), colours keep forests and villages, skirts hide cracks against neighbours.</summary>
    void BuildTerrainLow(ChunkData d)
    {
        const int q = 25, n = q + 1;
        var mb = TerrainLow;
        for (int z = 0; z < n; z++)
            for (int x = 0; x < n; x++)
            {
                int i = z * n + x;
                mb.Vertex(new Vector3(d.x0 + x * 16f, d.LowH[i], d.z0 + z * 16f), new Color32(d.LowCol[i * 3], d.LowCol[i * 3 + 1], d.LowCol[i * 3 + 2], 255));
            }
        for (int z = 0; z < q; z++)
            for (int x = 0; x < q; x++)
            {
                int a = z * n + x, b = a + n, c = a + 1, e = b + 1;
                if (((x + z) & 1) == 0) { mb.Tri(a, b, e); mb.Tri(a, e, c); } else { mb.Tri(a, b, c); mb.Tri(c, b, e); }
            }
        void Skirt(int i0, int i1)
        {
            int s0 = mb.Vertex(mb.V[i0] + Vector3.down * 2f, mb.C[i0]), s1 = mb.Vertex(mb.V[i1] + Vector3.down * 2f, mb.C[i1]);
            mb.Tri(i0, i1, s1); mb.Tri(i0, s1, s0);
        }
        for (int i = 0; i < q; i++) { Skirt(i, i + 1); Skirt(q * n + i, q * n + i + 1); Skirt(i * n, (i + 1) * n); Skirt(i * n + q, (i + 1) * n + q); }
    }

    const float VergeWidth = 0.45f, VergeDrop = 0.09f, WallFrom = 0.25f;         // a gravel strip along every paved edge, sloping down into the ground so the road never shows a floating rim
    static readonly Vector3 Lift = Vector3.up * RoadLift;

    /// <summary>
    /// The road surface exactly as the world builder computed it: a quad between the two edges of every drawn road segment, and the junction meshes.
    /// Roads end where junctions begin and share their end vertices with them, so nothing overlaps and nothing needs to be layered.
    /// </summary>
    void BuildRoads(ChunkData d)
    {
        var mb = Roads;
        foreach (var r in d.Roads)
        {
            var col = r.dirt ? Dirt : Asphalt;
            for (int k = 0; k + 1 < r.Count; k++)
            {
                if (!r.drawn[k]) continue;
                Vector3 l0 = r.P(r.left, k) + Lift, r0 = r.P(r.right, k) + Lift, l1 = r.P(r.left, k + 1) + Lift, r1 = r.P(r.right, k + 1) + Lift;
                mb.Quad(mb.Vertex(l0, col), mb.Vertex(r0, col), mb.Vertex(r1, col), mb.Vertex(l1, col));
                if (r.bridge)                                     // concrete deck edges hanging below the road
                {
                    Vector3 dn = Vector3.down * 1.2f;
                    mb.Quad(mb.Vertex(l0, Concrete), mb.Vertex(l1, Concrete), mb.Vertex(l1 + dn, Concrete), mb.Vertex(l0 + dn, Concrete));
                    mb.Quad(mb.Vertex(r0, Concrete), mb.Vertex(r0 + dn, Concrete), mb.Vertex(r1 + dn, Concrete), mb.Vertex(r1, Concrete));
                }
                else
                {
                    Vector3 out0 = Flat(l0 - r0).normalized, out1 = Flat(l1 - r1).normalized;
                    Verge(mb, d, r0, r1, -out0, -out1, !r.dirt); Verge(mb, d, l1, l0, out1, out0, !r.dirt);
                }
            }
        }
        foreach (var j in d.Junctions)
        {
            var col = j.dirt ? Dirt : Asphalt; int first = mb.V.Count;
            for (int i = 0; i * 3 < j.v.Length; i++) mb.Vertex(new Vector3(j.v[i * 3], j.v[i * 3 + 1], j.v[i * 3 + 2]) + Lift, col);
            for (int t = 0; t + 2 < j.tri.Length; t += 3) mb.Tri(first + j.tri[t], first + j.tri[t + 1], first + j.tri[t + 2]);
            for (int e = 0; e < j.mouth.Length; e++)              // kerb edges get the verge, road mouths do not
            {
                if (j.mouth[e]) continue;
                Vector3 a = mb.V[first + j.edge[e * 2]], b = mb.V[first + j.edge[e * 2 + 1]], along = Flat(b - a);
                if (along.sqrMagnitude < 1e-6f) continue;
                Vector3 outward = new Vector3(along.z, 0f, -along.x).normalized;      // the surface lies on the left of the edge
                Verge(mb, d, a, b, outward, outward, !j.dirt);
            }
        }
    }

    static Vector3 Flat(Vector3 v) => new Vector3(v.x, 0f, v.z);

    /// <summary>
    /// Along the road edge a -> b (the road on its left), reaching outwards along out0 / out1: a gravel strip on paved roads, and,
    /// where the ground beside the road lies well below it (a road on a terrace above another one), a retaining wall down to the ground.
    /// </summary>
    static void Verge(MeshBuilder mb, ChunkData d, Vector3 a, Vector3 b, Vector3 out0, Vector3 out1, bool gravel)
    {
        float reach = gravel ? VergeWidth : 0.05f;
        Vector3 a1 = a + out0 * reach + Vector3.down * VergeDrop, b1 = b + out1 * reach + Vector3.down * VergeDrop, inner = Vector3.down * 0.004f;
        if (gravel) mb.Quad(mb.Vertex(a + inner, Shoulder), mb.Vertex(a1, Shoulder), mb.Vertex(b1, Shoulder), mb.Vertex(b + inner, Shoulder));
        float ga = d.Height(a1.x, a1.z), gb = d.Height(b1.x, b1.z);
        if (a1.y - ga < WallFrom && b1.y - gb < WallFrom) return;
        Vector3 a2 = new Vector3(a1.x, Mathf.Min(ga, a1.y) - 0.15f, a1.z), b2 = new Vector3(b1.x, Mathf.Min(gb, b1.y) - 0.15f, b1.z);
        mb.Quad(mb.Vertex(a1, Wall), mb.Vertex(b1, Wall), mb.Vertex(b2, Wall), mb.Vertex(a2, Wall));
    }

    void BuildWater(ChunkData d)
    {
        var mb = Water;
        foreach (var a in d.Areas)
        {
            int n = a.ring.Length / 2; if (n < 3) continue;
            var pts = new List<Vector2>(n); for (int i = 0; i < n; i++) pts.Add(new Vector2(a.ring[i * 2], a.ring[i * 2 + 1]));
            var tri = MeshBuilder.Triangulate(pts); int bi = mb.V.Count;
            for (int i = 0; i < n; i++) mb.Vertex(new Vector3(pts[i].x, a.ys[i], pts[i].y), WaterCol);
            for (int i = 0; i + 2 < tri.Count; i += 3) mb.Tri(bi + tri[i], bi + tri[i + 1], bi + tri[i + 2]);
        }
        foreach (var l in d.Lines)
        {
            int n = l.pts.Length / 3; float w = l.hw + 0.25f;
            Vector3 P(int i) => new Vector3(l.pts[i * 3], l.pts[i * 3 + 1], l.pts[i * 3 + 2]);
            var left = new int[n]; var right = new int[n];
            for (int i = 0; i < n; i++)
            {
                Vector3 a = P(Mathf.Max(i - 1, 0)), b = P(Mathf.Min(i + 1, n - 1)); Vector3 dir = b - a; dir.y = 0; dir = dir.sqrMagnitude > 1e-6f ? dir.normalized : Vector3.forward;
                Vector3 side = new Vector3(dir.z, 0, -dir.x) * w, p = P(i);
                left[i] = mb.Vertex(p - side, WaterCol); right[i] = mb.Vertex(p + side, WaterCol);
            }
            for (int k = l.lead; k < n - 1 - l.trail; k++) mb.Quad(left[k], right[k], right[k + 1], left[k + 1]);      // neighbours' points only shape the tangent
        }
    }

    // ------------------------------------------------------------------ buildings
    struct Look { public System.Random rng; public FacadeStyle style; public Color32 wall, roof; }

    /// <summary>Deterministic per-building look: the same on the mid tier (shell) and the near tier (facade).</summary>
    static Look LookOf(BuildingData bd)
    {
        var rng = new System.Random((int)(bd.p[0] * 100f) * 73856093 ^ (int)(bd.p[1] * 100f) * 19349663);
        var style = Facade.Pick(bd, rng);
        Color32 orthoRoof = C(bd.c[0], bd.c[1], bd.c[2]);
        return new Look { rng = rng, style = style, wall = style.wall, roof = style.hasRoof ? style.roof : Facade.RoofTile(orthoRoof, rng) };
    }

    void BuildBuildingShells(ChunkData d)
    {
        var mb = Buildings; var pts = new List<Vector2>();
        foreach (var bd in d.Buildings)
        {
            int n = bd.p.Length / 2; if (n < 3) continue;
            pts.Clear(); for (int i = 0; i < n; i++) pts.Add(new Vector2(bd.p[i * 2], bd.p[i * 2 + 1]));
            float y0 = bd.b, y1 = bd.b + bd.h;
            var look = LookOf(bd); Color32 wall = look.wall, roof = look.roof;
            for (int i = 0; i < n; i++)
            {
                Vector2 a = pts[i], b = pts[(i + 1) % n];
                int v0 = mb.Vertex(new Vector3(a.x, y0, a.y), wall), v1 = mb.Vertex(new Vector3(b.x, y0, b.y), wall);
                int v2 = mb.Vertex(new Vector3(b.x, y1, b.y), wall), v3 = mb.Vertex(new Vector3(a.x, y1, a.y), wall);
                mb.Quad(v0, v1, v2, v3);
            }
            if (bd.r > 0 && bd.rc != null && bd.rc.Length == 6)       // gable roof over the oriented bounding rectangle
            {
                Vector2 c = new Vector2(bd.rc[0], bd.rc[1]), u = new Vector2(bd.rc[2], bd.rc[3]), v = new Vector2(-u.y, u.x);
                float hl = bd.rc[4] * 0.5f, hw = bd.rc[5] * 0.5f + 0.3f;
                Vector2 e0 = c - u * hl - v * hw, e1 = c + u * hl - v * hw, e2 = c + u * hl + v * hw, e3 = c - u * hl + v * hw;
                Vector2 r0 = c - u * hl, r1 = c + u * hl;
                Vector3 P(Vector2 p, float y) => new Vector3(p.x, y, p.y);
                int a0 = mb.Vertex(P(e0, y1), roof), a1 = mb.Vertex(P(e1, y1), roof), b1 = mb.Vertex(P(r1, y1 + bd.r), roof), b0 = mb.Vertex(P(r0, y1 + bd.r), roof);
                mb.Quad(a0, a1, b1, b0);
                int c0 = mb.Vertex(P(e3, y1), roof), c1 = mb.Vertex(P(e2, y1), roof), d1 = mb.Vertex(P(r1, y1 + bd.r), roof), d0 = mb.Vertex(P(r0, y1 + bd.r), roof);
                mb.Quad(c0, d0, d1, c1);
                int g0 = mb.Vertex(P(e0, y1), wall), g1 = mb.Vertex(P(e3, y1), wall), g2 = mb.Vertex(P(r0, y1 + bd.r), wall); mb.Tri(g0, g1, g2);
                int h0 = mb.Vertex(P(e1, y1), wall), h1 = mb.Vertex(P(e2, y1), wall), h2 = mb.Vertex(P(r1, y1 + bd.r), wall); mb.Tri(h0, h2, h1);
            }
            else
            {
                var tri = MeshBuilder.Triangulate(pts); int baseIdx = mb.V.Count;
                var rc = new Color32((byte)(roof.r * 0.85f), (byte)(roof.g * 0.85f), (byte)(roof.b * 0.85f), 255);
                foreach (var p in pts) mb.Vertex(new Vector3(p.x, y1, p.y), rc);
                for (int i = 0; i + 2 < tri.Count; i += 3) mb.Tri(baseIdx + tri[i], baseIdx + tri[i + 1], baseIdx + tri[i + 2]);
            }
        }
    }

    // ------------------------------------------------------------------ near tier
    public static ChunkMeshes BuildNear(ChunkData d, ChunkMeshes m)
    {
        m.BuildTerrainFull(d);
        m.BuildMarks(d);
        var local = new RoadIndex(); local.Add(-2, d.Roads, d.Junctions); local.Add(-1, d.Ctx, d.CtxJunctions);      // own + neighbouring roads: clearance for everything solid
        var boxes = m.BuildBuildingDetail(d, local);
        m.BuildStreetFurniture(d, local, boxes);
        m.BuildTrees(d, local);
        m.BuildShrubs(d, local);
        return m;
    }

    void BuildTerrainFull(ChunkData d)
    {
        const int CV = ChunkData.CV; var mb = Terrain;
        for (int z = 0; z < CV; z++)
            for (int x = 0; x < CV; x++)
            {
                int gi = z * CV + x;
                mb.Vertex(new Vector3(d.x0 + x * WorldData.Cell, d.H[gi], d.z0 + z * WorldData.Cell), new Color32(d.Col[gi * 3], d.Col[gi * 3 + 1], d.Col[gi * 3 + 2], 255));
            }
        for (int z = 0; z < CV - 1; z++)
            for (int x = 0; x < CV - 1; x++)
            {
                int a = z * CV + x, b = a + CV, c = a + 1, e = b + 1;
                if (((x + z) & 1) == 0) { mb.Tri(a, b, e); mb.Tri(a, e, c); } else { mb.Tri(a, b, c); mb.Tri(c, b, e); }
            }
    }

    /// <summary>
    /// Road markings by French practice, from the real data (carriageway width, lane count, one-way, road kind):
    ///   - every paved two-way road: dashed centre line (one lane each way); no line on dirt tracks or one-way roads; 4 lanes or more: dashed centre plus a dashed line per lane
    ///   - one-way roads and dual carriageways: a dashed line between each pair of lanes, no centre line
    ///   - edge lines: thin dashed from ~5.5 m, solid from ~6.5 m and on dual carriageways / motorways
    ///   - roundabouts: lane lines only when the ring has two lanes or more
    /// Lines stop where a junction begins. Dashes are laid out by distance along the whole road, so they stay in step across chunks.
    /// </summary>
    void BuildMarks(ChunkData d)
    {
        var mb = Marks; Vector3 lift = Vector3.up * (RoadLift + 0.018f);
        foreach (var r in d.Roads)
        {
            if (r.dirt || r.bridge || r.kind == 5) continue;
            float width = r.realWidth; int lanes = r.lanes; bool oneWay = r.oneway != 0, dual = r.kind == 2 || r.kind == 3;
            bool centre = false; int dividers = 0;
            if (r.kind == 1) { if (lanes >= 2) dividers = lanes - 1; }                                                 // roundabout ring: lane lines only
            else if (oneWay || dual) { if (lanes >= 2) dividers = lanes - 1; else if (lanes == 0 && width >= 6.5f) dividers = 1; }
            else if (lanes >= 4) { centre = true; dividers = 2; }
            else centre = true;                                                                                       // every paved two-way road carries a centre line
            bool edgeThin = r.kind != 1 && width >= 5.5f, edgeSolid = r.kind != 1 && (width >= 6.5f || dual);
            for (int g = 0; g < r.giveWayAt.Length; g++) GiveWayLine(mb, r, r.giveWayAt[g], r.giveWayAfter[g], centre, lift);
            if (!centre && dividers == 0 && !edgeThin) continue;

            // a stripe along segment k from fraction t0 to t1 of its length, `across` of the way from the left edge to the right one, or `inset` metres inside an edge
            void Stripe(int k, float t0, float t1, float across, float inset, float half)
            {
                Vector3 la = Vector3.Lerp(r.P(r.left, k), r.P(r.left, k + 1), t0), ra = Vector3.Lerp(r.P(r.right, k), r.P(r.right, k + 1), t0);
                Vector3 lb = Vector3.Lerp(r.P(r.left, k), r.P(r.left, k + 1), t1), rb = Vector3.Lerp(r.P(r.right, k), r.P(r.right, k + 1), t1);
                float fa = inset > 0f ? inset / Mathf.Max((ra - la).magnitude, 0.1f) : across, fb = inset > 0f ? inset / Mathf.Max((rb - lb).magnitude, 0.1f) : across;
                if (inset > 0f && across > 0.5f) { fa = 1f - fa; fb = 1f - fb; }
                Vector3 a = Vector3.Lerp(la, ra, fa), b = Vector3.Lerp(lb, rb, fb), side = (ra - la).normalized * half;
                mb.Quad(mb.Vertex(a - side + lift, Paint), mb.Vertex(a + side + lift, Paint), mb.Vertex(b + side + lift, Paint), mb.Vertex(b - side + lift, Paint));
            }
            // dashes `on` metres long every `period` metres (period 0: a solid line)
            void Line(float on, float period, float across, float inset, float half)
            {
                float s = r.s0;
                for (int k = 0; k + 1 < r.Count; k++)
                {
                    float len = Vector3.Distance(r.P(r.pts, k), r.P(r.pts, k + 1)), s1 = s + len;
                    if (r.drawn[k] && len > 1e-3f)
                    {
                        if (period <= 0f) Stripe(k, 0f, 1f, across, inset, half);
                        else
                            for (float start = Mathf.Floor(s / period) * period; start < s1; start += period)
                            {
                                float a = Mathf.Max(start, s), b = Mathf.Min(start + on, s1);
                                if (b - a > 0.05f) Stripe(k, (a - s) / len, (b - s) / len, across, inset, half);
                            }
                    }
                    s = s1;
                }
            }
            if (centre) Line(3f, 9f, 0.5f, 0f, 0.09f);
            for (int dvd = 1; dvd <= dividers; dvd++) Line(3f, dual ? 0f : 6f, dvd / (float)(dividers + 1), 0f, 0.075f);
            if (edgeSolid) { Line(0f, 0f, 0f, 0.22f, 0.055f); Line(0f, 0f, 1f, 0.22f, 0.055f); }
            else if (edgeThin) { Line(3f, 6.5f, 0f, 0.22f, 0.045f); Line(3f, 6.5f, 1f, 0.22f, 0.045f); }
        }
    }

    /// <summary>
    /// "Cédez le passage": a line of 0.5 m blocks across the lane that arrives at the junction (the right-hand half of a two-way road
    /// seen by the driver coming to it, the whole width of a one-way road), half a metre before the junction begins.
    /// </summary>
    static void GiveWayLine(MeshBuilder mb, RoadData r, int i, bool after, bool twoLanes, Vector3 lift)
    {
        if (r.oneway == (after ? 1 : 2)) return;                                        // one-way away from the junction: nobody arrives here
        int j = i + (after ? 1 : -1); if (j < 0 || j >= r.Count) return;
        Vector3 along = Flat(r.P(r.pts, j) - r.P(r.pts, i)); float len = along.magnitude; if (len < 0.2f) return;
        float t = Mathf.Clamp01(0.5f / len);
        Vector3 left = Vector3.Lerp(r.P(r.left, i), r.P(r.left, j), t), right = Vector3.Lerp(r.P(r.right, i), r.P(r.right, j), t), mid = (left + right) * 0.5f;
        bool whole = r.oneway != 0 || !twoLanes;                                        // no centre line: the line spans the road
        Vector3 from = whole ? left : mid, to = whole ? right : (after ? left : right);      // else the arriving driver's right-hand half
        Vector3 across = to - from; float width = across.magnitude; if (width < 1f) return;
        across /= width; Vector3 depth = along / len * 0.25f;
        for (float u = 0.3f; u + 0.5f <= width - 0.2f; u += 1f)
        {
            Vector3 a = from + across * u + lift, b = from + across * (u + 0.5f) + lift;
            mb.Quad(mb.Vertex(a - depth, Paint), mb.Vertex(a + depth, Paint), mb.Vertex(b + depth, Paint), mb.Vertex(b - depth, Paint));
            mb.Quad(mb.Vertex(b - depth, Paint), mb.Vertex(b + depth, Paint), mb.Vertex(a + depth, Paint), mb.Vertex(a - depth, Paint));      // either winding: the block faces up whichever way the road was digitised
        }
    }

    List<BoxSpec> BuildBuildingDetail(ChunkData d, RoadIndex local)
    {
        var boxes = new List<BoxSpec>(); var pts = new List<Vector2>();
        foreach (var bd in d.Buildings)
        {
            int n = bd.p.Length / 2; if (n < 3) continue;
            pts.Clear(); for (int i = 0; i < n; i++) pts.Add(new Vector2(bd.p[i * 2], bd.p[i * 2 + 1]));
            float y0 = bd.b, y1 = bd.b + bd.h;
            var look = LookOf(bd);
            int fei = Mathf.Clamp(bd.fe, 0, n - 1); Vector2 fm = (pts[fei] + pts[(fei + 1) % n]) * 0.5f;
            float yg = Mathf.Clamp(d.Height(fm.x, fm.y), y0 + 0.4f, y1 - 2f);      // doors sit on the ground at the road-facing wall
            Facade.Build(bd, pts, look.style, yg, y1, Facades, look.rng, local.EdgeClearance);
            boxes.Add(FitBox(pts, y0, y1 + Mathf.Max(bd.r, 0)));
            AddCollisionPrism(bd, Collision, y0, y1 + Mathf.Max(bd.r, 0));
        }
        return boxes;
    }

    const float WallDepth = 1.2f;
    static void AddCollisionPrism(BuildingData bd, MeshBuilder mb, float y0, float y1)
    {
        if (bd.cn == null || bd.cp == null) return;
        int off = 0; var ring = new List<Vector2>(); var c = new Color32(255, 255, 255, 255);
        foreach (int cnt in bd.cn)
        {
            ring.Clear(); for (int k = 0; k < cnt; k++) ring.Add(new Vector2(bd.cp[(off + k) * 2], bd.cp[(off + k) * 2 + 1])); off += cnt;
            // signed area: which side of an edge is the inside?
            float area = 0f; for (int k = 0; k < cnt; k++) { Vector2 p0 = ring[k], p1 = ring[(k + 1) % cnt]; area += p0.x * p1.y - p1.x * p0.y; }
            float inSign = area >= 0f ? 1f : -1f;                                                // CCW ring (x east, z north): the inside is on the left of the walking direction
            for (int k = 0; k < cnt; k++)
            {
                // Each wall is a SOLID slab reaching WallDepth metres into the building (not a zero-thickness sheet: a slow car could slip through a sheet and then be pushed
                // further in instead of back out). The outer face is exactly the visual wall.
                Vector2 a = ring[k], b = ring[(k + 1) % cnt], e = b - a; float len = e.magnitude; if (len < 0.05f) continue;
                Vector2 inward = new Vector2(-e.y, e.x) / len * inSign * WallDepth;
                Vector3 a0 = new Vector3(a.x, y0, a.y), b0 = new Vector3(b.x, y0, b.y), a1 = new Vector3(a.x, y1, a.y), b1 = new Vector3(b.x, y1, b.y);
                Vector3 ia0 = a0 + new Vector3(inward.x, 0, inward.y), ib0 = b0 + new Vector3(inward.x, 0, inward.y), ia1 = a1 + new Vector3(inward.x, 0, inward.y), ib1 = b1 + new Vector3(inward.x, 0, inward.y);
                int v_a0 = mb.Vertex(a0, c), v_b0 = mb.Vertex(b0, c), v_b1 = mb.Vertex(b1, c), v_a1 = mb.Vertex(a1, c), v_ia0 = mb.Vertex(ia0, c), v_ib0 = mb.Vertex(ib0, c), v_ib1 = mb.Vertex(ib1, c), v_ia1 = mb.Vertex(ia1, c);
                // Unity draws / collides the CLOCKWISE side of a triangle: for a wall walked with the inside on its left, that means the vertex order below (outer face first, all faces point out of the slab)
                mb.Quad(v_a1, v_b1, v_b0, v_a0); mb.Quad(v_ib0, v_ia0, v_a0, v_b0);                 // outer face, floor
                mb.Quad(v_ib1, v_b1, v_a1, v_ia1); mb.Quad(v_ib1, v_ia1, v_ia0, v_ib0);             // top, inner face
                mb.Quad(v_ia0, v_ia1, v_a1, v_a0); mb.Quad(v_ib0, v_b0, v_b1, v_ib1);               // the two ends
            }
            var tri = MeshBuilder.Triangulate(ring); int bi = mb.V.Count;
            foreach (var q in ring) mb.Vertex(new Vector3(q.x, y1, q.y), c);
            for (int k = 0; k + 2 < tri.Count; k += 3) mb.Tri(bi + tri[k], bi + tri[k + 1], bi + tri[k + 2]);
        }
    }

    static BoxSpec FitBox(List<Vector2> pts, float y0, float y1)
    {
        int n = pts.Count; float best = 0; Vector2 axis = Vector2.right;
        for (int i = 0; i < n; i++) { Vector2 e = pts[(i + 1) % n] - pts[i]; if (e.sqrMagnitude > best) { best = e.sqrMagnitude; axis = e.normalized; } }
        Vector2 perp = new Vector2(-axis.y, axis.x);
        float minA = 1e9f, maxA = -1e9f, minP = 1e9f, maxP = -1e9f;
        foreach (var p in pts) { float a = Vector2.Dot(p, axis), q = Vector2.Dot(p, perp); minA = Mathf.Min(minA, a); maxA = Mathf.Max(maxA, a); minP = Mathf.Min(minP, q); maxP = Mathf.Max(maxP, q); }
        Vector2 c = axis * ((minA + maxA) / 2) + perp * ((minP + maxP) / 2);
        return new BoxSpec { c = new Vector3(c.x, (y0 + y1) / 2, c.y), size = new Vector3(maxA - minA, y1 - y0, maxP - minP), yaw = Mathf.Atan2(axis.y, axis.x) * Mathf.Rad2Deg };
    }

    static bool InsideAnyBuilding(List<BoxSpec> boxes, Vector3 p)
    {
        foreach (var bx in boxes)
        {
            float yaw = bx.yaw * Mathf.Deg2Rad; Vector2 dd = new Vector2(p.x - bx.c.x, p.z - bx.c.z);
            float lx = dd.x * Mathf.Cos(yaw) + dd.y * Mathf.Sin(yaw), lz = -dd.x * Mathf.Sin(yaw) + dd.y * Mathf.Cos(yaw);
            if (Mathf.Abs(lx) < bx.size.x * 0.5f + 1.5f && Mathf.Abs(lz) < bx.size.z * 0.5f + 1.5f) return true;
        }
        return false;
    }

    void AddObstacle(RoadIndex local, Vector3 pos, float r, float h)
    {
        if (local.EdgeClearance(pos.x, pos.z) < r + 0.05f) return;                    // nothing solid on or touching a road
        Obstacles.Add(new Obstacle { pos = pos, r = r, h = h });
    }

    // ------------------------------------------------------------------ street furniture: lamp posts in the village, wooden poles + wires in the country
    void BuildStreetFurniture(ChunkData d, RoadIndex local, List<BoxSpec> boxes)
    {
        var urban = new Dictionary<long, int>();
        foreach (var bd in d.Buildings) { long key = ((long)Mathf.FloorToInt(bd.p[0] / 30f) << 32) ^ (uint)Mathf.FloorToInt(bd.p[1] / 30f); urban.TryGetValue(key, out int c); urban[key] = c + 1; }
        Color32 steel = new Color32(64, 68, 72, 255), lampHead = new Color32(240, 236, 220, 60), wood = new Color32(112, 86, 62, 255), wire = new Color32(24, 24, 26, 255), insul = new Color32(190, 196, 190, 255);
        var rng = new System.Random(7 + d.key); var mb = Street;
        foreach (var r in d.Roads)
        {
            if (r.dirt || r.bridge || r.pts.Length < 12) continue;
            int n = r.pts.Length / 3; float acc = 0, spacing = 0; int sideSign = rng.Next(2) == 0 ? -1 : 1;
            Vector3 prevPole = default; bool hasPrev = false;
            for (int k = 1; k <= n - 1; k++)
            {
                Vector3 a = new Vector3(r.pts[(k - 1) * 3], r.pts[(k - 1) * 3 + 1], r.pts[(k - 1) * 3 + 2]), b = new Vector3(r.pts[k * 3], r.pts[k * 3 + 1], r.pts[k * 3 + 2]);
                acc += Vector3.Distance(a, b);
                Vector3 dir = new Vector3(b.x - a.x, 0, b.z - a.z); if (dir.sqrMagnitude < 1e-4f) continue; dir.Normalize();
                long key = ((long)Mathf.FloorToInt(b.x / 30f) << 32) ^ (uint)Mathf.FloorToInt(b.z / 30f);
                bool isUrban = urban.TryGetValue(key, out int cnt) && cnt >= 3;
                float want = isUrban ? 34f : 52f;
                if (acc < want + spacing) continue;
                acc = 0; spacing = (float)rng.NextDouble() * 6f;
                Vector3 side = new Vector3(dir.z, 0, -dir.x) * sideSign; if (isUrban || rng.Next(3) == 0) sideSign = -sideSign;
                Vector3 pos = b + side * (r.hw + (isUrban ? 1.0f : 1.5f)); pos.y = d.Height(pos.x, pos.z);
                if (InsideAnyBuilding(boxes, pos) || local.EdgeClearance(pos.x, pos.z) < 0.9f) { hasPrev = false; continue; }
                if (!InsideChunk(d, pos)) continue;
                if (Water_(d, pos)) { hasPrev = false; continue; }
                if (isUrban)
                {
                    AddObstacle(local, pos, 0.2f, 3f);
                    mb.Box(pos + Vector3.up * 3.1f, new Vector3(0.15f, 6.2f, 0.15f), steel);
                    Vector3 toRoad = -side; var q = Quaternion.LookRotation(toRoad, Vector3.up);
                    mb.BoxQ(pos + Vector3.up * 6.15f + toRoad * 0.65f, new Vector3(0.09f, 0.09f, 1.4f), q, steel);
                    mb.BoxQ(pos + Vector3.up * 6.05f + toRoad * 1.25f, new Vector3(0.34f, 0.10f, 0.62f), q, lampHead);
                    hasPrev = false;
                }
                else
                {
                    AddObstacle(local, pos, 0.3f, 3f);
                    mb.Box(pos + Vector3.up * 4.6f, new Vector3(0.24f, 9.2f, 0.24f), wood);
                    var q = Quaternion.LookRotation(side, Vector3.up);
                    mb.BoxQ(pos + Vector3.up * 8.7f, new Vector3(2.0f, 0.13f, 0.13f), q, wood);
                    for (int w = -1; w <= 1; w++) mb.BoxQ(pos + Vector3.up * 8.85f + Vector3.Cross(Vector3.up, side).normalized * (w * 0.85f), new Vector3(0.07f, 0.16f, 0.07f), q, insul);
                    Vector3 top = pos + Vector3.up * 8.9f;
                    if (hasPrev && Vector3.Distance(prevPole, top) < 90f)
                    {
                        Vector3 perp = Vector3.Cross(Vector3.up, side).normalized;
                        for (int w = -1; w <= 1; w++)
                        {
                            Vector3 p0 = prevPole + perp * (w * 0.85f), p1 = top + perp * (w * 0.85f); Vector3 last = p0;
                            for (int sgm = 1; sgm <= 8; sgm++)
                            {
                                float t = sgm / 8f; Vector3 pt = Vector3.Lerp(p0, p1, t); pt.y -= 4f * 0.9f * t * (1f - t) * (Vector3.Distance(p0, p1) / 45f);
                                Vector3 mid = (last + pt) * 0.5f; Vector3 dv = pt - last;
                                mb.BoxQ(mid, new Vector3(0.025f, 0.025f, dv.magnitude), Quaternion.LookRotation(dv.normalized, Vector3.up), wire);
                                last = pt;
                            }
                        }
                    }
                    prevPole = top; hasPrev = true;
                }
            }
        }
    }

    static bool InsideChunk(ChunkData d, Vector3 p) => p.x >= d.x0 && p.x < d.x0 + WorldData.ChunkSize && p.z >= d.z0 && p.z < d.z0 + WorldData.ChunkSize;

    /// <summary>True if (x,z) is inside one of the chunk's water polygons or on a stream (nothing is planted in the water).</summary>
    static bool Water_(ChunkData d, Vector3 p)
    {
        foreach (var a in d.Areas) if (InRing(a.ring, p.x, p.z)) return true;
        foreach (var l in d.Lines)
            for (int i = 0; i + 5 < l.pts.Length; i += 3)
            {
                Vector2 a = new Vector2(l.pts[i], l.pts[i + 2]), b = new Vector2(l.pts[i + 3], l.pts[i + 5]), ab = b - a, q = new Vector2(p.x, p.z);
                float t = Mathf.Clamp01(Vector2.Dot(q - a, ab) / Mathf.Max(ab.sqrMagnitude, 1e-4f));
                if ((q - (a + ab * t)).magnitude < l.hw + 0.6f) return true;
            }
        return false;
    }
    static bool InRing(float[] r, float x, float z)
    {
        bool inside = false; int n = r.Length / 2;
        for (int i = 0, j = n - 1; i < n; j = i++)
        {
            float xi = r[i * 2], zi = r[i * 2 + 1], xj = r[j * 2], zj = r[j * 2 + 1];
            if ((zi > z) != (zj > z) && x < (xj - xi) * (z - zi) / (zj - zi) + xi) inside = !inside;
        }
        return inside;
    }

    // ------------------------------------------------------------------ trees and shrubs
    void BuildTrees(ChunkData d, RoadIndex local)
    {
        if (d.Trees == null) return;
        int count = d.Trees.Length / 4; var mb = Trees;
        for (int i = 0; i < count; i++)
        {
            float x = d.Trees[i * 4], y = d.Trees[i * 4 + 1], z = d.Trees[i * 4 + 2], h = d.Trees[i * 4 + 3];
            if (Water_(d, new Vector3(x, y, z))) continue;
            if (TouchesBridge(local, x, y, z, Mathf.Clamp(h * 0.28f, 0.9f, 4f), h)) continue;                  // nothing grows through a bridge deck
            uint hash = (uint)((i + d.key * 7919) * 2654435761u); float rnd = (hash >> 8 & 255) / 255f;
            var leaf = rnd < 0.12f ? C(150, 150, 60) : C((int)(58 + 34 * rnd), (int)(112 + 44 * rnd), (int)(56 + 22 * rnd));
            var trunk = C(96, 70, 48);
            float th = Mathf.Max(1.2f, h * 0.3f), rad = Mathf.Clamp(h * 0.28f, 0.9f, 4f);
            float tr = Mathf.Clamp(h * 0.03f, 0.12f, 0.35f);
            int shape0 = (int)(hash >> 16 & 7);
            float trunkH = (shape0 < 5 || h < 5f) ? th + (h - th) * 0.075f + 0.7f : th;
            float root = y - 2.5f;                                                                // trunks run well below the surface, so on a slope or a coarse terrain facet they still meet the ground
            int b0 = mb.Vertex(new Vector3(x - tr, root, z - tr), trunk), b1 = mb.Vertex(new Vector3(x + tr, root, z - tr), trunk), b2 = mb.Vertex(new Vector3(x + tr, root, z + tr), trunk), b3 = mb.Vertex(new Vector3(x - tr, root, z + tr), trunk);
            int t0 = mb.Vertex(new Vector3(x - tr, y + trunkH, z - tr), trunk), t1 = mb.Vertex(new Vector3(x + tr, y + trunkH, z - tr), trunk), t2 = mb.Vertex(new Vector3(x + tr, y + trunkH, z + tr), trunk), t3 = mb.Vertex(new Vector3(x - tr, y + trunkH, z + tr), trunk);
            mb.Quad(b0, b1, t1, t0); mb.Quad(b1, b2, t2, t1); mb.Quad(b2, b3, t3, t2); mb.Quad(b3, b0, t0, t3);
            int shape = (int)(hash >> 16 & 7);
            if (shape < 5 || h < 5f)
            {
                int lobes = h > 8f ? 3 : (h > 5f ? 2 : 1);
                for (int L = 0; L < lobes; L++)
                {
                    float ang = rnd * 6.28f + L * 2.1f, off = L == 0 ? 0f : rad * 0.45f;
                    float cx = x + Mathf.Cos(ang) * off, cz = z + Mathf.Sin(ang) * off;
                    float lr = rad * (L == 0 ? 1f : 0.72f), cy = y + th + (h - th) * (L == 0 ? 0.5f : 0.4f + 0.12f * L), hy = (h - th) * (L == 0 ? 0.5f : 0.36f);
                    int top = mb.Vertex(new Vector3(cx, cy + hy, cz), leaf), bot = mb.Vertex(new Vector3(cx, cy - hy * 0.85f, cz), Tint(leaf, 0.72f));
                    int r0 = mb.V.Count;
                    for (int k = 0; k < 6; k++) { float a2 = k * Mathf.PI / 3 + rnd; mb.Vertex(new Vector3(cx + Mathf.Cos(a2) * lr, cy + hy * 0.1f, cz + Mathf.Sin(a2) * lr), k % 2 == 0 ? Tint(leaf, 1.08f) : leaf); }
                    for (int k = 0; k < 6; k++) { mb.Tri(top, r0 + k, r0 + (k + 1) % 6); mb.Tri(bot, r0 + (k + 1) % 6, r0 + k); }
                }
            }
            else if (shape == 5)
            {
                float pr = Mathf.Clamp(h * 0.09f, 0.5f, 1.3f);
                int top = mb.Vertex(new Vector3(x, y + h, z), leaf), bot = mb.Vertex(new Vector3(x, y + th * 0.6f, z), Tint(leaf, 0.7f));
                int r0 = mb.V.Count;
                for (int k = 0; k < 5; k++) { float a2 = k * Mathf.PI * 2 / 5 + rnd; mb.Vertex(new Vector3(x + Mathf.Cos(a2) * pr, y + h * 0.42f, z + Mathf.Sin(a2) * pr), k % 2 == 0 ? Tint(leaf, 1.08f) : leaf); }
                for (int k = 0; k < 5; k++) { mb.Tri(top, r0 + k, r0 + (k + 1) % 5); mb.Tri(bot, r0 + (k + 1) % 5, r0 + k); }
            }
            else
            {
                int apex = mb.Vertex(new Vector3(x, y + h, z), leaf); int r0 = mb.V.Count;
                for (int k = 0; k < 6; k++) { float a2 = k * Mathf.PI / 3 + rnd * 1.5f; mb.Vertex(new Vector3(x + Mathf.Cos(a2) * rad * 0.8f, y + th * 0.7f, z + Mathf.Sin(a2) * rad * 0.8f), k % 2 == 0 ? leaf : Tint(leaf, 0.85f)); }
                for (int k = 0; k < 6; k++) mb.Tri(apex, r0 + k, r0 + (k + 1) % 6);
            }
            if (h >= 3f) AddObstacle(local, new Vector3(x, y, z), tr + 0.12f, 3f);           // trunk only: the crown is not solid
        }
    }

    /// <summary>A plant whose crown (radius `rad`, top at y + h) would reach a bridge deck above it, or stand on it, is dropped: the bridge is a road, and nothing is rendered on a road.</summary>
    static bool TouchesBridge(RoadIndex local, float x, float y, float z, float rad, float h)
    {
        if (!local.BridgeDeckNear(x, z, rad + 0.4f, out float deck)) return false;
        return y + h > deck - 2f && y < deck + 3f;
    }

    void BuildShrubs(ChunkData d, RoadIndex local)
    {
        if (d.Shrubs == null) return;
        int count = d.Shrubs.Length / 4; var mb = Trees;
        for (int i = 0; i < count; i++)
        {
            float x = d.Shrubs[i * 4], y = d.Shrubs[i * 4 + 1], z = d.Shrubs[i * 4 + 2], h = d.Shrubs[i * 4 + 3];
            if (Water_(d, new Vector3(x, y, z))) continue;
            if (TouchesBridge(local, x, y, z, 1.4f, h)) continue;
            uint hash = (uint)((i + d.key * 6007) * 2246822519u); float rnd = (hash >> 9 & 255) / 255f;
            var leaf = C((int)(46 + 40 * rnd), (int)(96 + 46 * rnd), (int)(44 + 22 * rnd));
            float rad = Mathf.Clamp(h * 0.62f, 0.55f, 1.4f), hh = Mathf.Clamp(h, 0.9f, 2.4f);
            int top = mb.Vertex(new Vector3(x, y + hh, z), leaf), bot = mb.Vertex(new Vector3(x, y - 0.5f, z), Tint(leaf, 0.7f)); int r0 = mb.V.Count;      // hedges sink a little into the ground too
            for (int k = 0; k < 6; k++) { float a = k * Mathf.PI / 3 + rnd * 2f; mb.Vertex(new Vector3(x + Mathf.Cos(a) * rad, y + hh * 0.42f, z + Mathf.Sin(a) * rad), k % 2 == 0 ? Tint(leaf, 1.1f) : leaf); }
            for (int k = 0; k < 6; k++) { mb.Tri(top, r0 + k, r0 + (k + 1) % 6); mb.Tri(bot, r0 + (k + 1) % 6, r0 + k); }
            if (hh > 1.3f) AddObstacle(local, new Vector3(x, y, z), rad * 0.75f, hh * 0.8f);
        }
    }
}
