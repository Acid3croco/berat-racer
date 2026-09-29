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
    RoadIndex roadsHere; Joints joints;

    /// <summary>Where road ends meet: how many roads share each end, and their mean half width (to taper two joining roads to one width, and to overlap ends at junctions).</summary>
    class Joints
    {
        struct End { public Vector2 p; public float hw; public RoadData r; }
        readonly List<End> ends = new List<End>();
        public Joints(RoadData[] a, RoadData[] b)
        {
            foreach (var set in new[] { a, b })
                foreach (var r in set)
                {
                    if (r.bridge || r.pts.Length < 6) continue;
                    int n = r.pts.Length / 3;
                    if (r.lead == 0) ends.Add(new End { p = new Vector2(r.pts[0], r.pts[2]), hw = r.hw, r = r });
                    if (r.trail == 0) ends.Add(new End { p = new Vector2(r.pts[(n - 1) * 3], r.pts[(n - 1) * 3 + 2]), hw = r.hw, r = r });
                }
        }
        public void Query(RoadData self, bool start, out int count, out float meanHw)
        {
            int n = self.pts.Length / 3; int k = start ? 0 : n - 1; var p = new Vector2(self.pts[k * 3], self.pts[k * 3 + 2]);
            count = 1; float sum = self.hw; bool seenSelf = false;
            foreach (var e in ends)
            {
                if ((e.p - p).sqrMagnitude > 2.56f) continue;
                if (e.r == self) { if (!seenSelf) { seenSelf = true; continue; } }          // this very end
                count++; sum += e.hw;
            }
            meanHw = sum / count;
        }
    }

    float CutToRoad(float x, float z, float y) => roadsHere != null ? roadsHere.CutTerrain(x, z, y) : y;

    /// <summary>All road centrelines around this chunk (own + neighbours) in a coarse grid: is a point on ANOTHER road's carriageway?</summary>
    class Corridors
    {
        struct Seg { public Vector2 a, b; public float hw, pri; public int fid; }
        readonly Dictionary<long, List<Seg>> grid = new Dictionary<long, List<Seg>>();
        const float Cell = 16f;
        static long Key(int x, int z) => ((long)x << 32) ^ (uint)z;
        public Corridors(RoadData[] a, RoadData[] b)
        {
            foreach (var set in new[] { a, b })
                foreach (var r in set)
                {
                    if (r.bridge) continue;
                    for (int i = 0; i + 5 < r.pts.Length; i += 3)
                    {
                        var s = new Seg { a = new Vector2(r.pts[i], r.pts[i + 2]), b = new Vector2(r.pts[i + 3], r.pts[i + 5]), hw = r.hw, pri = r.pri, fid = r.fid };
                        float m = s.hw + 1f;
                        int x0 = Mathf.FloorToInt((Mathf.Min(s.a.x, s.b.x) - m) / Cell), x1 = Mathf.FloorToInt((Mathf.Max(s.a.x, s.b.x) + m) / Cell);
                        int z0 = Mathf.FloorToInt((Mathf.Min(s.a.y, s.b.y) - m) / Cell), z1 = Mathf.FloorToInt((Mathf.Max(s.a.y, s.b.y) + m) / Cell);
                        for (int cx = x0; cx <= x1; cx++)
                            for (int cz = z0; cz <= z1; cz++)
                            {
                                if (!grid.TryGetValue(Key(cx, cz), out var l)) grid[Key(cx, cz)] = l = new List<Seg>();
                                l.Add(s);
                            }
                    }
                }
        }
        /// <summary>True if (x,z) is on the carriageway of a road that outranks priority `pri` (asphalt over dirt, then wider, then longer).</summary>
        public bool UnderRanking(float x, float z, float pri, int self)
        {
            if (!grid.TryGetValue(Key(Mathf.FloorToInt(x / Cell), Mathf.FloorToInt(z / Cell)), out var list)) return false;
            var p = new Vector2(x, z);
            foreach (var s in list)
            {
                if (s.fid == self || s.pri <= pri) continue;
                Vector2 ab = s.b - s.a; float t = Mathf.Clamp01(Vector2.Dot(p - s.a, ab) / Mathf.Max(ab.sqrMagnitude, 1e-4f));
                if ((p - (s.a + ab * t)).magnitude < s.hw - 0.1f) return true;
            }
            return false;
        }

        /// <summary>True if (x,z) lies within `inset` metres inside the carriageway of a road that is not feature `self`.</summary>
        public bool OnOther(float x, float z, int self, float inset = 0f)
        {
            if (!grid.TryGetValue(Key(Mathf.FloorToInt(x / Cell), Mathf.FloorToInt(z / Cell)), out var list)) return false;
            var p = new Vector2(x, z);
            foreach (var s in list)
            {
                if (s.fid == self) continue;
                Vector2 ab = s.b - s.a; float t = Mathf.Clamp01(Vector2.Dot(p - s.a, ab) / Mathf.Max(ab.sqrMagnitude, 1e-4f));
                if ((p - (s.a + ab * t)).magnitude < s.hw - inset) return true;
            }
            return false;
        }
    }

    static Color32 C(int r, int g, int b) => new Color32((byte)r, (byte)g, (byte)b, 255);
    static Color32 Tint(Color32 c, float f) => new Color32((byte)Mathf.Clamp(c.r * f, 0, 255), (byte)Mathf.Clamp(c.g * f, 0, 255), (byte)Mathf.Clamp(c.b * f, 0, 255), 255);
    static readonly Color32 Asphalt = new Color32(94, 94, 98, 255), Shoulder = new Color32(108, 102, 84, 255), Dirt = new Color32(158, 132, 96, 255),
        Concrete = new Color32(170, 168, 160, 255), Paint = new Color32(236, 232, 214, 255), WaterCol = new Color32(52, 96, 112, 255);
    public const float RoadLift = 0.012f;                                  // the drawn road sits a hair above the road height; the terrain is cut away instead of the road being raised

    // ------------------------------------------------------------------ mid tier
    public static ChunkMeshes BuildMid(ChunkData d)
    {
        var m = new ChunkMeshes();
        m.roadsHere = new RoadIndex(d.Roads); m.roadsHere.Add(-1, d.Ctx);
        m.BuildTerrainLow(d);
        m.joints = new Joints(d.Roads, d.Ctx);
        m.BuildRoads(d, new Corridors(d.Roads, d.Ctx));
        m.BuildBuildingShells(d);
        m.BuildWater(d);
        return m;
    }

    /// <summary>16 m terrain (26 x 26 vertices, each averaging a 3 x 3 patch), colours keep forests and villages, skirts hide cracks against neighbours.</summary>
    void BuildTerrainLow(ChunkData d)
    {
        const int q = 25, n = q + 1, CV = ChunkData.CV;
        var mb = TerrainLow;
        for (int z = 0; z < n; z++)
            for (int x = 0; x < n; x++)
            {
                float hs = 0; int cnt = 0;
                for (int dz = -2; dz <= 2; dz += 2)
                    for (int dx = -2; dx <= 2; dx += 2)
                    {
                        int sx = Mathf.Clamp(x * 4 + dx, 0, CV - 1), sz = Mathf.Clamp(z * 4 + dz, 0, CV - 1);
                        hs += d.H[sz * CV + sx]; cnt++;
                    }
                int ci = (z * n + x) * 3;
                mb.Vertex(new Vector3(d.x0 + x * 16f, CutToRoad(d.x0 + x * 16f, d.z0 + z * 16f, hs / cnt), d.z0 + z * 16f), new Color32(d.LowCol[ci], d.LowCol[ci + 1], d.LowCol[ci + 2], 255));
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

    const float TaperLength = 7f;
    static void Ribbon(RoadData r, int ri, Corridors cor, Joints jn, bool overlapEnds, out Vector3[] left, out Vector3[] right)
    {
        int n = r.pts.Length / 3; left = new Vector3[n]; right = new Vector3[n];
        int c0 = 1, c1 = 1; float m0 = r.hw, m1 = r.hw; var sFrom = new float[n]; float total = 0f;
        if (jn != null && !r.bridge)
        {
            if (r.lead == 0) jn.Query(r, true, out c0, out m0);
            if (r.trail == 0) jn.Query(r, false, out c1, out m1);
            for (int i = 1; i < n; i++) { total += Mathf.Sqrt(Mathf.Pow(r.pts[i * 3] - r.pts[(i - 1) * 3], 2) + Mathf.Pow(r.pts[i * 3 + 2] - r.pts[(i - 1) * 3 + 2], 2)); sFrom[i] = total; }
        }
        for (int i = 0; i < n; i++)
        {
            Vector3 p = new Vector3(r.pts[i * 3], r.pts[i * 3 + 1], r.pts[i * 3 + 2]);
            Vector3 q0 = new Vector3(r.pts[Mathf.Max(i - 1, 0) * 3], 0, r.pts[Mathf.Max(i - 1, 0) * 3 + 2]);
            Vector3 q1 = new Vector3(r.pts[Mathf.Min(i + 1, n - 1) * 3], 0, r.pts[Mathf.Min(i + 1, n - 1) * 3 + 2]);
            Vector3 dir = q1 - q0; dir.y = 0; dir = dir.sqrMagnitude > 1e-6f ? dir.normalized : Vector3.forward;
            Vector2 joint = i == 0 && r.lead == 0 ? r.t0 : (i == n - 1 && r.trail == 0 ? r.t1 : Vector2.zero);
            if (joint.sqrMagnitude > 0.5f) { Vector3 jd = new Vector3(joint.x, 0, joint.y); dir = Vector3.Dot(jd, dir) >= 0 ? jd : -jd; }      // both roads at a joint use one tangent: no wedge between their ribbons
            float hw = r.hw;
            if (c0 == 2) hw = Mathf.Lerp(m0, hw, Mathf.Clamp01(sFrom[i] / TaperLength));                           // two roads of different width meet: both taper to their mean width at the joint
            if (c1 == 2) hw = Mathf.Lerp(m1, hw, Mathf.Clamp01((total - sFrom[i]) / TaperLength));
            Vector3 fwd = q1 - q0; fwd.y = 0; fwd = fwd.sqrMagnitude > 1e-6f ? fwd.normalized : Vector3.forward;
            if (overlapEnds && c0 >= 3 && i == 0) p -= fwd * (r.hw * 0.9f);                                                       // a junction of three or more: overlap the ends into the junction instead of butting them with a straight cut
            if (overlapEnds && c1 >= 3 && i == n - 1) p += fwd * (r.hw * 0.9f);
            Vector3 side = new Vector3(dir.z, 0, -dir.x) * hw;
            p.y += RoadLift + (ri % 6) * 0.0009f;                  // tiny per-road bias so overlapping ribbons never z-fight
            left[i] = p - side; right[i] = p + side;
            if (cor != null && !r.bridge)
            {   // where this ribbon runs under a higher-ranking road, dip it just below that road's surface: the better road stays visually on top
                if (cor.UnderRanking(left[i].x, left[i].z, r.pri, r.fid)) left[i].y -= 0.03f;
                if (cor.UnderRanking(right[i].x, right[i].z, r.pri, r.fid)) right[i].y -= 0.03f;
            }
        }
    }

    void BuildRoads(ChunkData d, Corridors cor)
    {
        var mb = Roads;
        for (int ri = 0; ri < d.Roads.Length; ri++)
        {
            var r = d.Roads[ri]; Ribbon(r, ri, cor, joints, true, out var left, out var right); Ribbon(r, ri, cor, joints, false, out var left0, out var right0);      // asphalt overlaps into junctions; verges follow the plain ribbon
            int n = left.Length, last = n - 1 - r.trail;
            var col = r.dirt ? Dirt : Asphalt;
            for (int k = r.lead; k < last; k++)
            {
                mb.Quad(mb.Vertex(left[k], col), mb.Vertex(right[k], col), mb.Vertex(right[k + 1], col), mb.Vertex(left[k + 1], col));
                if (r.bridge)                                     // concrete deck edges hanging below the road
                {
                    Vector3 dn = Vector3.down * 1.2f;
                    mb.Quad(mb.Vertex(left[k], Concrete), mb.Vertex(left[k + 1], Concrete), mb.Vertex(left[k + 1] + dn, Concrete), mb.Vertex(left[k] + dn, Concrete));
                    mb.Quad(mb.Vertex(right[k], Concrete), mb.Vertex(right[k] + dn, Concrete), mb.Vertex(right[k + 1] + dn, Concrete), mb.Vertex(right[k + 1], Concrete));
                }
                else if (!r.dirt)                                 // gravel verge on both sides
                {
                    Vector3 wl0 = (left0[k] - right0[k]).normalized, wl1 = (left0[k + 1] - right0[k + 1]).normalized, down = Vector3.down * 0.012f;
                    Vector3 lm = (left0[k] + left0[k + 1]) * 0.5f + wl0 * 0.2f, rm = (right0[k] + right0[k + 1]) * 0.5f - wl0 * 0.2f;
                    if (!cor.UnderRanking(lm.x, lm.z, r.pri, r.fid))                              // no verge across the mouth of a side road, or inside the main road it joins
                    mb.Quad(mb.Vertex(left0[k] + down, Shoulder), mb.Vertex(left0[k + 1] + down, Shoulder), mb.Vertex(left0[k + 1] + wl1 * 0.45f + down * 2f, Shoulder), mb.Vertex(left0[k] + wl0 * 0.45f + down * 2f, Shoulder));
                    if (!cor.UnderRanking(rm.x, rm.z, r.pri, r.fid))
                    mb.Quad(mb.Vertex(right0[k] + down, Shoulder), mb.Vertex(right0[k + 1] + down, Shoulder), mb.Vertex(right0[k + 1] - wl1 * 0.45f + down * 2f, Shoulder), mb.Vertex(right0[k] - wl0 * 0.45f + down * 2f, Shoulder));
                }
            }
        }
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
        m.roadsHere = new RoadIndex(d.Roads); m.roadsHere.Add(-1, d.Ctx);
        m.BuildTerrainFull(d);
        m.joints = new Joints(d.Roads, d.Ctx);
        m.BuildMarks(d, new Corridors(d.Roads, d.Ctx));
        var local = new RoadIndex(d.Roads); local.Add(-1, d.Ctx);                       // own + neighbouring roads: clearance for everything solid
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
                mb.Vertex(new Vector3(d.x0 + x * WorldData.Cell, CutToRoad(d.x0 + x * WorldData.Cell, d.z0 + z * WorldData.Cell, d.H[gi]), d.z0 + z * WorldData.Cell), new Color32(d.Col[gi * 3], d.Col[gi * 3 + 1], d.Col[gi * 3 + 2], 255));
            }
        for (int z = 0; z < CV - 1; z++)
            for (int x = 0; x < CV - 1; x++)
            {
                int a = z * CV + x, b = a + CV, c = a + 1, e = b + 1;
                if (((x + z) & 1) == 0) { mb.Tri(a, b, e); mb.Tri(a, e, c); } else { mb.Tri(a, b, c); mb.Tri(c, b, e); }
            }
    }

    void BuildMarks(ChunkData d, Corridors cor)
    {
        var mb = Marks; Vector3 lift = Vector3.up * 0.03f;
        for (int ri = 0; ri < d.Roads.Length; ri++)
        {
            var r = d.Roads[ri]; if (r.dirt || r.bridge) continue;
            Ribbon(r, ri, cor, joints, false, out var left, out var right); int last = left.Length - 1 - r.trail;
            if (r.hw >= 2.3f)
                for (int k = r.lead; k < last; k += 3)                    // dashes: ~2 m painted, ~4 m gap (points are 2 m apart)
                {
                    int k2 = Mathf.Min(k + 1, left.Length - 1);
                    Vector3 c0 = (left[k] + right[k]) * 0.5f, c1 = (left[k2] + right[k2]) * 0.5f, dv = c1 - c0; if (dv.sqrMagnitude < 1e-4f) continue;
                    if (cor.UnderRanking(c0.x, c0.z, r.pri, r.fid) || cor.UnderRanking(c1.x, c1.z, r.pri, r.fid)) continue;              // no centre dash inside a junction
                    Vector3 side = Vector3.Cross(Vector3.up, dv.normalized) * 0.09f;
                    mb.Quad(mb.Vertex(c0 - side + lift, Paint), mb.Vertex(c0 + side + lift, Paint), mb.Vertex(c1 + side + lift, Paint), mb.Vertex(c1 - side + lift, Paint));
                }
            if (r.hw >= 2.6f)                                              // solid white edge lines on wider roads
                for (int k = r.lead; k < last; k++)
                {
                    Vector3 wl0 = (left[k] - right[k]).normalized, wl1 = (left[k + 1] - right[k + 1]).normalized;
                    for (int side = 0; side < 2; side++)
                    {
                        Vector3 e0 = side == 0 ? left[k] : right[k], e1 = side == 0 ? left[k + 1] : right[k + 1];
                        Vector3 in0 = side == 0 ? -wl0 : wl0, in1 = side == 0 ? -wl1 : wl1;
                        Vector3 em = (e0 + e1) * 0.5f; if (cor.UnderRanking(em.x, em.z, r.pri, r.fid)) continue;             // edge lines stop at a side road's mouth and never run across the main road
                        mb.Quad(mb.Vertex(e0 + in0 * 0.14f + lift, Paint), mb.Vertex(e1 + in1 * 0.14f + lift, Paint), mb.Vertex(e1 + in1 * 0.25f + lift, Paint), mb.Vertex(e0 + in0 * 0.25f + lift, Paint));
                    }
                }
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

    static void AddCollisionPrism(BuildingData bd, MeshBuilder mb, float y0, float y1)
    {
        if (bd.cn == null || bd.cp == null) return;
        int off = 0; var ring = new List<Vector2>(); var c = new Color32(255, 255, 255, 255);
        foreach (int cnt in bd.cn)
        {
            ring.Clear(); for (int k = 0; k < cnt; k++) ring.Add(new Vector2(bd.cp[(off + k) * 2], bd.cp[(off + k) * 2 + 1])); off += cnt;
            for (int k = 0; k < cnt; k++)
            {
                Vector2 a = ring[k], b = ring[(k + 1) % cnt];
                mb.Quad(mb.Vertex(new Vector3(a.x, y0, a.y), c), mb.Vertex(new Vector3(b.x, y0, b.y), c), mb.Vertex(new Vector3(b.x, y1, b.y), c), mb.Vertex(new Vector3(a.x, y1, a.y), c));
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
            for (int k = Mathf.Max(r.lead, 1); k <= n - 1 - r.trail; k++)
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

    void BuildShrubs(ChunkData d, RoadIndex local)
    {
        if (d.Shrubs == null) return;
        int count = d.Shrubs.Length / 4; var mb = Trees;
        for (int i = 0; i < count; i++)
        {
            float x = d.Shrubs[i * 4], y = d.Shrubs[i * 4 + 1], z = d.Shrubs[i * 4 + 2], h = d.Shrubs[i * 4 + 3];
            if (Water_(d, new Vector3(x, y, z))) continue;
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
