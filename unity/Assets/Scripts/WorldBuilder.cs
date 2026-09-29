using System.Collections;
using System.Collections.Generic;
using UnityEngine;

/// <summary>Builds the Berat world from exported LiDAR data: terrain, roads, buildings, trees. Chunked for culling.</summary>
public class WorldBuilder : MonoBehaviour
{
    public const int ChunkSize = 400;
    public const int NC = 16;                        // 16 x 400 m = 6.4 km

    public WorldData Data;
    public RoadIndex Roads;
    public float Progress;
    public bool Ready;

    Material mat, roadMat, markMat, terrainMat, buildingMat, treeMat;
    class Chunk { public Transform root; public GameObject terrain, terrainLow, roads, buildings, trees, street; public Vector2 center; public bool t, r, b, tr; }
    readonly Chunk[] chunks = new Chunk[NC * NC];
    readonly MeshBuilder[] colMB = new MeshBuilder[NC * NC];
    readonly MeshBuilder[] streetMB = new MeshBuilder[NC * NC];
    readonly MeshBuilder[] facMB = new MeshBuilder[NC * NC], roadMB = new MeshBuilder[NC * NC], markMB = new MeshBuilder[NC * NC], bldMB = new MeshBuilder[NC * NC], treeMB = new MeshBuilder[NC * NC];
    readonly List<BoxSpec>[] bldBoxes = new List<BoxSpec>[NC * NC];
    struct BoxSpec { public Vector3 c, size; public float yaw; }

    // tree colliders (pooled around the car)
    struct Obstacle { public Vector3 pos; public float r, h; }
    readonly List<Obstacle> obstacles = new List<Obstacle>();
    readonly Dictionary<long, List<int>> treeHash = new Dictionary<long, List<int>>();
    CapsuleCollider[] treePool;
    void AddObstacle(Vector3 pos, float r, float h)
    {
        if (Roads.EdgeClearance(pos.x, pos.z) < r + 0.05f) return;                          // nothing solid on or touching a road (trees under bridges need no collider either)
        long key = HashKey(pos.x, pos.z);
        if (!treeHash.TryGetValue(key, out var l)) treeHash[key] = l = new List<int>();
        l.Add(obstacles.Count); obstacles.Add(new Obstacle { pos = pos, r = r, h = h });
    }

    static int CI(float v) => Mathf.Clamp(Mathf.FloorToInt((v + WorldData.Half) / ChunkSize), 0, NC - 1);
    static int CK(float x, float z) => CI(z) * NC + CI(x);
    static Color32 C(int r, int g, int b) => new Color32((byte)r, (byte)g, (byte)b, 255);
    static Color32 Tint(Color32 c, float f) => new Color32((byte)Mathf.Clamp(c.r * f, 0, 255), (byte)Mathf.Clamp(c.g * f, 0, 255), (byte)Mathf.Clamp(c.b * f, 0, 255), 255);

    public string Error;

    public IEnumerator Build()
    {
        var total = System.Diagnostics.Stopwatch.StartNew();
        var shader = GameAssets.Flat;
        Log.I("world", $"shader={(shader != null ? shader.name : "NULL")} supported={(shader != null && shader.isSupported)}");
        mat = new Material(shader);
        roadMat = new Material(shader); roadMat.SetFloat("_OffsetFactor", -2); roadMat.SetFloat("_OffsetUnits", -2);
        markMat = new Material(shader); markMat.SetFloat("_OffsetFactor", -4); markMat.SetFloat("_OffsetUnits", -4);
        // fine procedural grain: fields, asphalt and rendered walls stop looking like flat colour
        terrainMat = new Material(shader); terrainMat.SetFloat("_Noise", 0.16f); terrainMat.SetFloat("_NoiseScale", 0.55f);
        roadMat.SetFloat("_Noise", 0.10f); roadMat.SetFloat("_NoiseScale", 1.6f);
        buildingMat = new Material(shader); buildingMat.SetFloat("_Noise", 0.05f); buildingMat.SetFloat("_NoiseScale", 2.2f);
        treeMat = new Material(shader); treeMat.SetFloat("_Noise", 0.10f); treeMat.SetFloat("_NoiseScale", 1.1f);
        yield return null;

        var stages = new (string name, float progress, System.Action run)[]
        {
            ("load data", 0.10f, () =>
            {
                Data = WorldData.Load();
                Roads = new RoadIndex(Data.Roads);
                Log.I("world", $"data: terrain {Data.N}x{Data.N} cell={Data.Cell}m, roads={Data.Roads.Length}, buildings={Data.Buildings.Length}, trees={Data.Trees.Length / 4}, spawn=({Data.Spawn.x:F0},{Data.Spawn.z:F0}) hdg={Data.Spawn.heading:F0}");
                for (int i = 0; i < chunks.Length; i++)
                {
                    colMB[i] = new MeshBuilder(); streetMB[i] = new MeshBuilder(); facMB[i] = new MeshBuilder(); roadMB[i] = new MeshBuilder(); markMB[i] = new MeshBuilder(); bldMB[i] = new MeshBuilder(); treeMB[i] = new MeshBuilder();
                    bldBoxes[i] = new List<BoxSpec>();
                    var go = new GameObject($"chunk_{i % NC}_{i / NC}"); go.transform.SetParent(transform, false);
                    chunks[i] = new Chunk { root = go.transform, center = new Vector2(-WorldData.Half + (i % NC + 0.5f) * ChunkSize, -WorldData.Half + (i / NC + 0.5f) * ChunkSize) };
                }
            }),
            ("terrain", 0.35f, BuildTerrain),
            ("roads", 0.55f, BuildRoads),
            ("buildings", 0.75f, BuildBuildings),
            ("street furniture", 0.82f, BuildStreetFurniture),
            ("trees", 0.90f, BuildTrees),
            ("shrubs", 0.95f, BuildShrubs),
            ("finish", 1.00f, Finish),
        };
        foreach (var st in stages)
        {
            var sw = System.Diagnostics.Stopwatch.StartNew();
            try { st.run(); }
            catch (System.Exception e) { Error = $"{st.name}: {e.Message}"; Log.I("world", $"FAILED stage '{st.name}': {e}"); yield break; }
            Progress = st.progress;
            Log.I("world", $"stage '{st.name}' done in {sw.ElapsedMilliseconds} ms (progress {Progress * 100:F0}%)");
            yield return null;
        }
        Ready = true;
        int meshes = 0; long verts = 0;
        foreach (var mf in GetComponentsInChildren<MeshFilter>(true)) { meshes++; verts += mf.sharedMesh.vertexCount; }
        Log.I("world", $"READY in {total.ElapsedMilliseconds} ms: {meshes} meshes, {verts / 1000}k vertices, {GetComponentsInChildren<BoxCollider>(true).Length} building colliders, mem={System.GC.GetTotalMemory(false) / 1048576} MB managed");
    }

    // ------------------------------------------------------------------ terrain
    void BuildTerrain()
    {
        int per = ChunkSize / (int)Data.Cell;       // cells per chunk (100)
        for (int cz = 0; cz < NC; cz++)
            for (int cx = 0; cx < NC; cx++)
            {
                int ix0 = cx * per, iz0 = cz * per;
                int nx = Mathf.Min(per + 1, Data.N - ix0), nz = Mathf.Min(per + 1, Data.N - iz0);
                var verts = new Vector3[nx * nz]; var cols = new Color32[nx * nz];
                for (int z = 0; z < nz; z++)
                    for (int x = 0; x < nx; x++)
                    {
                        int gi = (iz0 + z) * Data.N + ix0 + x;
                        verts[z * nx + x] = new Vector3(Data.Ox + (ix0 + x) * Data.Cell, Data.Heights[gi], Data.Oz + (iz0 + z) * Data.Cell);
                        cols[z * nx + x] = new Color32(Data.Colors[gi * 3], Data.Colors[gi * 3 + 1], Data.Colors[gi * 3 + 2], 255);
                    }
                var tris = new int[(nx - 1) * (nz - 1) * 6]; int t = 0;
                for (int z = 0; z < nz - 1; z++)
                    for (int x = 0; x < nx - 1; x++)
                    {
                        int a = z * nx + x, b = a + nx, c = a + 1, d = b + 1;
                        bool flip = ((x + z) & 1) == 0;                 // alternate the diagonal: nicer low-poly facets
                        if (flip) { tris[t++] = a; tris[t++] = b; tris[t++] = d; tris[t++] = a; tris[t++] = d; tris[t++] = c; }
                        else { tris[t++] = a; tris[t++] = b; tris[t++] = c; tris[t++] = c; tris[t++] = b; tris[t++] = d; }
                    }
                var m = new Mesh { name = "terrain", indexFormat = UnityEngine.Rendering.IndexFormat.UInt32 };
                m.vertices = verts; m.colors32 = cols; m.triangles = tris; m.RecalculateBounds();
                var ch = chunks[cz * NC + cx];
                ch.terrain = MakeObject("terrain", ch.root, m, terrainMat, false, true);
                ch.terrainLow = MakeObject("terrainLow", ch.root, BuildLowTerrain(ix0, iz0, per), terrainMat, false, true);
                ch.terrainLow.SetActive(false);
            }
    }

    /// <summary>
    /// Far-distance terrain: 16 m grid (every 4th vertex), colours averaged over each 4 x 4 block, with a skirt to hide cracks against the full-res neighbours.
    /// Sub-pixel 4 m triangles with high-contrast colours were the source of the shimmering dots on the horizon.
    /// </summary>
    Mesh BuildLowTerrain(int ix0, int iz0, int per)
    {
        const int step = 4; int q = per / step;                                   // 25 quads per side
        int n = q + 1; var verts = new List<Vector3>(); var cols = new List<Color32>(); var tris = new List<int>();
        for (int z = 0; z < n; z++)
            for (int x = 0; x < n; x++)
            {
                int gx = Mathf.Min(ix0 + x * step, Data.N - 1), gz = Mathf.Min(iz0 + z * step, Data.N - 1);
                float r = 0, g = 0, b = 0, hsum = 0; int cnt = 0;
                for (int dz = -step / 2; dz < step / 2; dz++)
                    for (int dx = -step / 2; dx < step / 2; dx++)
                    {
                        int sx = Mathf.Clamp(gx + dx, 0, Data.N - 1), sz = Mathf.Clamp(gz + dz, 0, Data.N - 1), gi = sz * Data.N + sx;
                        r += Data.Colors[gi * 3]; g += Data.Colors[gi * 3 + 1]; b += Data.Colors[gi * 3 + 2]; hsum += Data.Heights[gi]; cnt++;
                    }
                verts.Add(new Vector3(Data.Ox + gx * Data.Cell, hsum / cnt, Data.Oz + gz * Data.Cell));
                cols.Add(new Color32((byte)(r / cnt), (byte)(g / cnt), (byte)(b / cnt), 255));
            }
        for (int z = 0; z < q; z++)
            for (int x = 0; x < q; x++)
            {
                int a = z * n + x, b2 = a + n, c = a + 1, d = b2 + 1;
                if (((x + z) & 1) == 0) { tris.Add(a); tris.Add(b2); tris.Add(d); tris.Add(a); tris.Add(d); tris.Add(c); } else { tris.Add(a); tris.Add(b2); tris.Add(c); tris.Add(c); tris.Add(b2); tris.Add(d); }
            }
        // skirts (2 m down) around the four edges
        void Skirt(int i0, int i1)
        {
            int s0 = verts.Count; verts.Add(verts[i0] + Vector3.down * 2f); cols.Add(cols[i0]); verts.Add(verts[i1] + Vector3.down * 2f); cols.Add(cols[i1]);
            tris.Add(i0); tris.Add(i1); tris.Add(s0 + 1); tris.Add(i0); tris.Add(s0 + 1); tris.Add(s0);
        }
        for (int i = 0; i < q; i++) { Skirt(i, i + 1); Skirt(q * n + i, q * n + i + 1); Skirt(i * n, (i + 1) * n); Skirt(i * n + q, (i + 1) * n + q); }
        var m = new Mesh { name = "terrainLow", indexFormat = UnityEngine.Rendering.IndexFormat.UInt32 };
        m.SetVertices(verts); m.SetColors(cols); m.SetTriangles(tris, 0); m.RecalculateBounds();
        return m;
    }

    // ------------------------------------------------------------------ roads
    static readonly Color32 Asphalt = new Color32(94, 94, 98, 255), Shoulder = new Color32(108, 102, 84, 255), Dirt = new Color32(158, 132, 96, 255),
        Concrete = new Color32(170, 168, 160, 255), Paint = new Color32(236, 232, 214, 255);

    void BuildRoads()
    {
        for (int ri = 0; ri < Data.Roads.Length; ri++)
        {
            var r = Data.Roads[ri];
            int n = r.pts.Length / 3;
            var left = new Vector3[n]; var right = new Vector3[n];
            for (int i = 0; i < n; i++)
            {
                Vector3 p = new Vector3(r.pts[i * 3], r.pts[i * 3 + 1], r.pts[i * 3 + 2]);
                Vector3 q0 = new Vector3(r.pts[Mathf.Max(i - 1, 0) * 3], 0, r.pts[Mathf.Max(i - 1, 0) * 3 + 2]);
                Vector3 q1 = new Vector3(r.pts[Mathf.Min(i + 1, n - 1) * 3], 0, r.pts[Mathf.Min(i + 1, n - 1) * 3 + 2]);
                Vector3 dir = (q1 - q0); dir.y = 0; dir = dir.sqrMagnitude > 1e-6f ? dir.normalized : Vector3.forward;
                Vector3 side = new Vector3(dir.z, 0, -dir.x) * r.hw;
                p.y += RoadLift + (ri % 6) * 0.0009f;                  // tiny per-road bias so overlapping ribbons never z-fight
                left[i] = p - side; right[i] = p + side;
            }
            var col = r.dirt ? Dirt : Asphalt;
            for (int i = 0; i + 1 < n; i += 30)                       // ~60 m pieces so chunks cull cleanly
            {
                int end = Mathf.Min(i + 30, n - 1);
                Vector3 mid = (left[i] + left[end]) * 0.5f;
                int ck = CK(mid.x, mid.z);
                var mb = roadMB[ck];
                for (int k = i; k < end; k++)
                {
                    int a = mb.Vertex(left[k], col), b = mb.Vertex(right[k], col), c = mb.Vertex(right[k + 1], col), d = mb.Vertex(left[k + 1], col);
                    mb.Quad(a, b, c, d);
                    if (r.bridge)                                     // concrete deck edges hanging below the road
                    {
                        Vector3 dn = Vector3.down * 1.2f;
                        int l0 = mb.Vertex(left[k], Concrete), l1 = mb.Vertex(left[k + 1], Concrete), l2 = mb.Vertex(left[k + 1] + dn, Concrete), l3 = mb.Vertex(left[k] + dn, Concrete);
                        mb.Quad(l0, l1, l2, l3);
                        int r0 = mb.Vertex(right[k], Concrete), r1 = mb.Vertex(right[k + 1], Concrete), r2 = mb.Vertex(right[k + 1] + dn, Concrete), r3 = mb.Vertex(right[k] + dn, Concrete);
                        mb.Quad(r0, r3, r2, r1);
                    }
                }
                if (!r.dirt && r.hw >= 2.3f && !r.bridge) AddCentreLine(markMB[ck], left, right, i, end);
                if (!r.dirt && !r.bridge) AddShoulderAndEdges(roadMB[ck], markMB[ck], left, right, i, end, r.hw >= 2.6f);
            }
        }
    }

    /// <summary>Gravel verge on both sides of an asphalt road, and (on wider roads) solid white edge lines.</summary>
    void AddShoulderAndEdges(MeshBuilder road, MeshBuilder marks, Vector3[] left, Vector3[] right, int i, int end, bool edgeLines)
    {
        for (int k = i; k < end; k++)
        {
            Vector3 wl0 = (left[k] - right[k]).normalized, wl1 = (left[k + 1] - right[k + 1]).normalized;     // outward on the left / right side
            foreach (int side in new[] { 0, 1 })
            {
                Vector3 e0 = side == 0 ? left[k] : right[k], e1 = side == 0 ? left[k + 1] : right[k + 1];
                Vector3 o0 = side == 0 ? wl0 : -wl0, o1 = side == 0 ? wl1 : -wl1;
                Vector3 down = Vector3.down * 0.012f;
                road.Quad(road.Vertex(e0 + down, Shoulder), road.Vertex(e1 + down, Shoulder), road.Vertex(e1 + o1 * 0.45f + down * 2f, Shoulder), road.Vertex(e0 + o0 * 0.45f + down * 2f, Shoulder));
                if (edgeLines)
                {
                    Vector3 in0 = -o0, in1 = -o1, lift = Vector3.up * 0.03f;
                    marks.Quad(marks.Vertex(e0 + in0 * 0.14f + lift, Paint), marks.Vertex(e1 + in1 * 0.14f + lift, Paint), marks.Vertex(e1 + in1 * 0.25f + lift, Paint), marks.Vertex(e0 + in0 * 0.25f + lift, Paint));
                }
            }
        }
    }

    // ------------------------------------------------------------------ street furniture: lamp posts in the village, wooden poles + wires in the country
    void BuildStreetFurniture()
    {
        // where are the buildings? (30 m cells with a building count) and their oriented boxes, to keep poles out of walls
        var urban = new Dictionary<long, int>();
        foreach (var bd in Data.Buildings) { float cx = bd.p[0], cz = bd.p[1]; long key = ((long)Mathf.FloorToInt(cx / 30f) << 32) ^ (uint)Mathf.FloorToInt(cz / 30f); urban.TryGetValue(key, out int c); urban[key] = c + 1; }
        Color32 steel = new Color32(64, 68, 72, 255), lampHead = new Color32(240, 236, 220, 60), wood = new Color32(112, 86, 62, 255), wire = new Color32(24, 24, 26, 255), insul = new Color32(190, 196, 190, 255);
        int lamps = 0, poles = 0; var rng = new System.Random(7);
        foreach (var r in Data.Roads)
        {
            if (r.dirt || r.bridge || r.pts.Length < 12) continue;
            int n = r.pts.Length / 3; float acc = 0, spacing = 0; int sideSign = rng.Next(2) == 0 ? -1 : 1;
            Vector3 prevPole = default; bool hasPrev = false;
            for (int k = 1; k < n; k++)
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
                Vector3 pos = b + side * (r.hw + (isUrban ? 1.0f : 1.5f)); pos.y = Data.TerrainHeight(pos.x, pos.z);
                if (InsideAnyBuilding(pos) || Roads.EdgeClearance(pos.x, pos.z) < 0.9f) { hasPrev = false; continue; }      // never on or right beside ANY road (crossings included)
                int ck = CK(pos.x, pos.z); var mb = streetMB[ck];
                if (isUrban)
                {
                    AddObstacle(pos, 0.2f, 3f);
                    mb.Box(pos + Vector3.up * 3.1f, new Vector3(0.15f, 6.2f, 0.15f), steel);
                    Vector3 toRoad = -side; var q = Quaternion.LookRotation(toRoad, Vector3.up);
                    mb.BoxQ(pos + Vector3.up * 6.15f + toRoad * 0.65f, new Vector3(0.09f, 0.09f, 1.4f), q, steel);
                    mb.BoxQ(pos + Vector3.up * 6.05f + toRoad * 1.25f, new Vector3(0.34f, 0.10f, 0.62f), q, lampHead);
                    lamps++; hasPrev = false;
                }
                else
                {
                    AddObstacle(pos, 0.3f, 3f);
                    mb.Box(pos + Vector3.up * 4.6f, new Vector3(0.24f, 9.2f, 0.24f), wood);
                    var q = Quaternion.LookRotation(side, Vector3.up);
                    mb.BoxQ(pos + Vector3.up * 8.7f, new Vector3(2.0f, 0.13f, 0.13f), q, wood);
                    for (int w = -1; w <= 1; w++) mb.BoxQ(pos + Vector3.up * 8.85f + Vector3.Cross(Vector3.up, side).normalized * (w * 0.85f), new Vector3(0.07f, 0.16f, 0.07f), q, insul);
                    Vector3 top = pos + Vector3.up * 8.9f;
                    if (hasPrev && Vector3.Distance(prevPole, top) < 90f)
                    {   // three wires with a catenary sag
                        Vector3 perp = Vector3.Cross(Vector3.up, side).normalized;
                        for (int w = -1; w <= 1; w++)
                        {
                            Vector3 p0 = prevPole + perp * (w * 0.85f), p1 = top + perp * (w * 0.85f); Vector3 last = p0;
                            for (int sgm = 1; sgm <= 8; sgm++)
                            {
                                float t = sgm / 8f; Vector3 pt = Vector3.Lerp(p0, p1, t); pt.y -= 4f * 0.9f * t * (1f - t) * (Vector3.Distance(p0, p1) / 45f);
                                Vector3 mid = (last + pt) * 0.5f; Vector3 dv = pt - last;
                                streetMB[CK(mid.x, mid.z)].BoxQ(mid, new Vector3(0.025f, 0.025f, dv.magnitude), Quaternion.LookRotation(dv.normalized, Vector3.up), wire);
                                last = pt;
                            }
                        }
                    }
                    prevPole = top; hasPrev = true; poles++;
                }
            }
        }
        Log.I("world", $"street furniture: {lamps} lamp posts, {poles} utility poles");
    }

    /// <summary>Debug audit: how close to a road edge is any pole / trunk / hedge collider, and what stands near a given spot.</summary>
    public void AuditObstacles(Vector2 spot)
    {
        int bad = 0, n = obstacles.Count; float worst = 999f; string nearest = "none within 12 m"; float nd = 12f;
        foreach (var o in obstacles)
        {
            float c = Roads.EdgeClearance(o.pos.x, o.pos.z) - o.r;
            if (c < 0.05f) { bad++; Roads.Query(o.pos.x, o.pos.z, o.pos.y + 5f, out float dk); Log.I("audit", $"  touching: r={o.r:F2} h={o.h:F1} at ({o.pos.x:F0},{o.pos.z:F0}) cell {Grid.CellLabel(o.pos.x, o.pos.z)} ground y={o.pos.y:F1}, bridge deck above: {(float.IsNaN(dk) ? "none" : dk.ToString("F1") + " (" + (dk - o.pos.y).ToString("F1") + " m above)")}"); }
            worst = Mathf.Min(worst, c);
            float d = Vector2.Distance(new Vector2(o.pos.x, o.pos.z), spot);
            if (d < nd) { nd = d; nearest = $"obstacle r={o.r:F2} h={o.h:F1} at ({o.pos.x:F1},{o.pos.z:F1}), {d:F1} m away, {c:F2} m clear of the road"; }
        }
        Log.I("audit", $"{n} obstacles (trunks, poles, lamps, hedges): {bad} touching a road edge, tightest clearance {worst:F2} m");
        Log.I("audit", $"near cell (207,212) [{spot.x:F0},{spot.y:F0}]: {nearest}");
    }

    bool InsideAnyBuilding(Vector3 p)
    {
        foreach (var bx in bldBoxes[CK(p.x, p.z)])
        {
            float yaw = bx.yaw * Mathf.Deg2Rad; Vector2 d = new Vector2(p.x - bx.c.x, p.z - bx.c.z);
            float lx = d.x * Mathf.Cos(yaw) + d.y * Mathf.Sin(yaw), lz = -d.x * Mathf.Sin(yaw) + d.y * Mathf.Cos(yaw);
            if (Mathf.Abs(lx) < bx.size.x * 0.5f + 1.5f && Mathf.Abs(lz) < bx.size.z * 0.5f + 1.5f) return true;
        }
        return false;
    }

    void AddCentreLine(MeshBuilder mb, Vector3[] left, Vector3[] right, int i, int end)
    {
        for (int k = i; k < end; k += 3)                              // dash: ~2 m painted, ~4 m gap (points are 2 m apart)
        {
            int k2 = Mathf.Min(k + 1, end);
            Vector3 c0 = (left[k] + right[k]) * 0.5f, c1 = (left[k2] + right[k2]) * 0.5f;
            Vector3 d = c1 - c0; if (d.sqrMagnitude < 1e-4f) continue;
            Vector3 side = Vector3.Cross(Vector3.up, d.normalized) * 0.09f;
            Vector3 lift = Vector3.up * 0.03f;
            int a = mb.Vertex(c0 - side + lift, Paint), b = mb.Vertex(c0 + side + lift, Paint), c = mb.Vertex(c1 + side + lift, Paint), e = mb.Vertex(c1 - side + lift, Paint);
            mb.Quad(a, b, c, e);
        }
    }

    // ------------------------------------------------------------------ buildings
    void BuildBuildings()
    {
        var pts = new List<Vector2>();
        foreach (var bd in Data.Buildings)
        {
            int n = bd.p.Length / 2;
            if (n < 3) continue;
            pts.Clear();
            Vector2 cen = Vector2.zero;
            for (int i = 0; i < n; i++) { var v = new Vector2(bd.p[i * 2], bd.p[i * 2 + 1]); pts.Add(v); cen += v; }
            cen /= n;
            int ck = CK(cen.x, cen.y);
            var mb = bldMB[ck];
            float y0 = bd.b, y1 = bd.b + bd.h;
            var rng = new System.Random((int)(bd.p[0] * 100f) * 73856093 ^ (int)(bd.p[1] * 100f) * 19349663);
            var style = Facade.Pick(bd, rng);
            Color32 wall = style.wall;
            Color32 orthoRoof = C(bd.c[0], bd.c[1], bd.c[2]);
            Color32 roof = style.hasRoof ? style.roof : Facade.RoofTile(orthoRoof, rng);

            for (int i = 0; i < n; i++)                               // walls
            {
                Vector2 a = pts[i], b = pts[(i + 1) % n];
                int v0 = mb.Vertex(new Vector3(a.x, y0, a.y), wall), v1 = mb.Vertex(new Vector3(b.x, y0, b.y), wall);
                int v2 = mb.Vertex(new Vector3(b.x, y1, b.y), wall), v3 = mb.Vertex(new Vector3(a.x, y1, a.y), wall);
                mb.Quad(v0, v1, v2, v3);
            }
            if (bd.r > 0 && bd.rc != null && bd.rc.Length == 6)       // gable roof over the oriented bounding rectangle
            {
                Vector2 c = new Vector2(bd.rc[0], bd.rc[1]), u = new Vector2(bd.rc[2], bd.rc[3]), v = new Vector2(-u.y, u.x);
                float hl = bd.rc[4] * 0.5f, hw = bd.rc[5] * 0.5f + 0.3f;      // gable ends flush with the wall, small overhang on the eave sides only
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
            else                                                        // flat roof
            {
                var tri = MeshBuilder.Triangulate(pts);
                int baseIdx = mb.V.Count;
                var rc = new Color32((byte)(roof.r * 0.85f), (byte)(roof.g * 0.85f), (byte)(roof.b * 0.85f), 255);
                foreach (var p in pts) mb.Vertex(new Vector3(p.x, y1, p.y), rc);
                for (int i = 0; i + 2 < tri.Count; i += 3) mb.Tri(baseIdx + tri[i], baseIdx + tri[i + 1], baseIdx + tri[i + 2]);
            }
            int fei = Mathf.Clamp(bd.fe, 0, n - 1); Vector2 fm = (pts[fei] + pts[(fei + 1) % n]) * 0.5f;
            float yg = Mathf.Clamp(Data.TerrainHeight(fm.x, fm.y), y0 + 0.4f, y1 - 2f);      // doors sit on the ground at the road-facing wall
            Facade.Build(bd, pts, style, yg, y1, facMB[ck], rng);
            bldBoxes[ck].Add(FitBox(pts, y0, y1 + Mathf.Max(bd.r, 0)));
            AddCollisionPrism(bd, colMB[ck], y0, y1 + Mathf.Max(bd.r, 0));
        }
    }

    /// <summary>Exact collision volume of a building: its footprint (minus any road corridor that crosses it) extruded to roof height.</summary>
    static void AddCollisionPrism(BuildingData bd, MeshBuilder mb, float y0, float y1)
    {
        if (bd.cn == null || bd.cp == null) return;
        int off = 0; var ring = new List<Vector2>();
        foreach (int cnt in bd.cn)
        {
            ring.Clear(); for (int k = 0; k < cnt; k++) ring.Add(new Vector2(bd.cp[(off + k) * 2], bd.cp[(off + k) * 2 + 1])); off += cnt;
            var c = new Color32(255, 255, 255, 255);
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

    /// <summary>Oriented box collider from the longest footprint edge (only used to keep poles out of walls now).</summary>
    static BoxSpec FitBox(List<Vector2> pts, float y0, float y1)
    {
        int n = pts.Count; float best = 0; Vector2 axis = Vector2.right;
        for (int i = 0; i < n; i++)
        {
            Vector2 e = pts[(i + 1) % n] - pts[i];
            if (e.sqrMagnitude > best) { best = e.sqrMagnitude; axis = e.normalized; }
        }
        Vector2 perp = new Vector2(-axis.y, axis.x);
        float minA = 1e9f, maxA = -1e9f, minP = 1e9f, maxP = -1e9f;
        foreach (var p in pts)
        {
            float a = Vector2.Dot(p, axis), q = Vector2.Dot(p, perp);
            minA = Mathf.Min(minA, a); maxA = Mathf.Max(maxA, a); minP = Mathf.Min(minP, q); maxP = Mathf.Max(maxP, q);
        }
        Vector2 c = axis * ((minA + maxA) / 2) + perp * ((minP + maxP) / 2);
        return new BoxSpec { c = new Vector3(c.x, (y0 + y1) / 2, c.y), size = new Vector3(maxA - minA, y1 - y0, maxP - minP), yaw = Mathf.Atan2(axis.y, axis.x) * Mathf.Rad2Deg };
    }

    // ------------------------------------------------------------------ trees
    void BuildTrees()
    {
        int count = Data.Trees.Length / 4;
        for (int i = 0; i < count; i++)
        {
            float x = Data.Trees[i * 4], y = Data.Trees[i * 4 + 1], z = Data.Trees[i * 4 + 2], h = Data.Trees[i * 4 + 3];
            int ck = CK(x, z); var mb = treeMB[ck];
            uint hash = (uint)(i * 2654435761u); float rnd = (hash >> 8 & 255) / 255f;
            var leaf = rnd < 0.12f ? C(150, 150, 60) : C((int)(58 + 34 * rnd), (int)(112 + 44 * rnd), (int)(56 + 22 * rnd));
            var trunk = C(96, 70, 48);
            float th = Mathf.Max(1.2f, h * 0.3f), rad = Mathf.Clamp(h * 0.28f, 0.9f, 4f);
            float tr = Mathf.Clamp(h * 0.03f, 0.12f, 0.35f);
            int shape0 = (int)(hash >> 16 & 7);
            float trunkH = (shape0 < 5 || h < 5f) ? th + (h - th) * 0.075f + 0.7f : th;   // round canopy: its lowest point is above th, so run the trunk up inside it
            // trunk: 4-sided prism
            int b0 = mb.Vertex(new Vector3(x - tr, y, z - tr), trunk), b1 = mb.Vertex(new Vector3(x + tr, y, z - tr), trunk), b2 = mb.Vertex(new Vector3(x + tr, y, z + tr), trunk), b3 = mb.Vertex(new Vector3(x - tr, y, z + tr), trunk);
            int t0 = mb.Vertex(new Vector3(x - tr, y + trunkH, z - tr), trunk), t1 = mb.Vertex(new Vector3(x + tr, y + trunkH, z - tr), trunk), t2 = mb.Vertex(new Vector3(x + tr, y + trunkH, z + tr), trunk), t3 = mb.Vertex(new Vector3(x - tr, y + trunkH, z + tr), trunk);
            mb.Quad(b0, b1, t1, t0); mb.Quad(b1, b2, t2, t1); mb.Quad(b2, b3, t3, t2); mb.Quad(b3, b0, t0, t3);
            // canopy: three tree shapes like the region -- round deciduous (oak, plane), columnar poplar / cypress, and conifer
            int shape = (int)(hash >> 16 & 7); float squash = 1f;
            int leafBase = mb.V.Count;
            if (shape < 5 || h < 5f)
            {   // round: two rings + apex + base = 6-sided ellipsoid; 2 offset lobes for fullness on big trees
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
            {   // poplar / cypress: tall narrow spindle
                float pr = Mathf.Clamp(h * 0.09f, 0.5f, 1.3f);
                int top = mb.Vertex(new Vector3(x, y + h, z), leaf), bot = mb.Vertex(new Vector3(x, y + th * 0.6f, z), Tint(leaf, 0.7f));
                int r0 = mb.V.Count;
                for (int k = 0; k < 5; k++) { float a2 = k * Mathf.PI * 2 / 5 + rnd; mb.Vertex(new Vector3(x + Mathf.Cos(a2) * pr, y + h * 0.42f, z + Mathf.Sin(a2) * pr), k % 2 == 0 ? Tint(leaf, 1.08f) : leaf); }
                for (int k = 0; k < 5; k++) { mb.Tri(top, r0 + k, r0 + (k + 1) % 5); mb.Tri(bot, r0 + (k + 1) % 5, r0 + k); }
            }
            else
            {   // conifer cone
                int apex = mb.Vertex(new Vector3(x, y + h, z), leaf); int r0 = mb.V.Count;
                for (int k = 0; k < 6; k++) { float a2 = k * Mathf.PI / 3 + rnd * 1.5f; mb.Vertex(new Vector3(x + Mathf.Cos(a2) * rad * 0.8f, y + th * 0.7f, z + Mathf.Sin(a2) * rad * 0.8f), k % 2 == 0 ? leaf : Tint(leaf, 0.85f)); }
                for (int k = 0; k < 6; k++) mb.Tri(apex, r0 + k, r0 + (k + 1) % 6);
            }
            if (h >= 3f) AddObstacle(new Vector3(x, y, z), tr + 0.12f, 3f);                  // trunk only: the crown is not solid
        }
    }
    /// <summary>Hedges and garden shrubs: small squashed blobs, three shades of green, merged into the tree layer of each chunk.</summary>
    void BuildShrubs()
    {
        int count = Data.Shrubs.Length / 4;
        for (int i = 0; i < count; i++)
        {
            float x = Data.Shrubs[i * 4], y = Data.Shrubs[i * 4 + 1], z = Data.Shrubs[i * 4 + 2], h = Data.Shrubs[i * 4 + 3];
            var mb = treeMB[CK(x, z)];
            uint hash = (uint)(i * 2246822519u); float rnd = (hash >> 9 & 255) / 255f;
            var leaf = C((int)(46 + 40 * rnd), (int)(96 + 46 * rnd), (int)(44 + 22 * rnd));
            float rad = Mathf.Clamp(h * 0.62f, 0.55f, 1.4f), hh = Mathf.Clamp(h, 0.9f, 2.4f);
            int top = mb.Vertex(new Vector3(x, y + hh, z), leaf), bot = mb.Vertex(new Vector3(x, y + 0.05f, z), Tint(leaf, 0.7f)); int r0 = mb.V.Count;
            for (int k = 0; k < 6; k++) { float a = k * Mathf.PI / 3 + rnd * 2f; mb.Vertex(new Vector3(x + Mathf.Cos(a) * rad, y + hh * 0.42f, z + Mathf.Sin(a) * rad), k % 2 == 0 ? Tint(leaf, 1.1f) : leaf); }
            for (int k = 0; k < 6; k++) { mb.Tri(top, r0 + k, r0 + (k + 1) % 6); mb.Tri(bot, r0 + (k + 1) % 6, r0 + k); }
            if (hh > 1.3f) AddObstacle(new Vector3(x, y, z), rad * 0.75f, hh * 0.8f);
        }
        Log.I("world", $"shrubs: {count}");
    }

    static long HashKey(float x, float z) => ((long)Mathf.FloorToInt(x / 16f) << 32) ^ (uint)Mathf.FloorToInt(z / 16f);

    // ------------------------------------------------------------------ finish
    void Finish()
    {
        for (int i = 0; i < chunks.Length; i++)
        {
            var ch = chunks[i];
            if (!roadMB[i].Empty) ch.roads = MakeObject("roads", ch.root, roadMB[i].ToMesh("roads"), roadMat);
            if (!markMB[i].Empty && ch.roads != null) MakeObject("marks", ch.roads.transform, markMB[i].ToMesh("marks"), markMat);
            if (!bldMB[i].Empty)
            {
                ch.buildings = MakeObject("buildings", ch.root, bldMB[i].ToMesh("buildings"), buildingMat, true, true);
                if (!facMB[i].Empty) MakeObject("facades", ch.buildings.transform, facMB[i].ToMesh("facades"), markMat, false, true);
                if (System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-nobcol") < 0 && !colMB[i].Empty)
                {
                    var mc = ch.buildings.AddComponent<MeshCollider>();
                    mc.sharedMesh = colMB[i].ToMesh("buildingCollision");
                    mc.cookingOptions = MeshColliderCookingOptions.CookForFasterSimulation;
                }
            }
            if (!streetMB[i].Empty) ch.street = MakeObject("street", ch.root, streetMB[i].ToMesh("street"), mat, true, true);
            if (!treeMB[i].Empty) ch.trees = MakeObject("trees", ch.root, treeMB[i].ToMesh("trees"), treeMat, true, true);
        }
        treePool = new CapsuleCollider[System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-notcol") >= 0 ? 0 : 110];
        for (int i = 0; i < treePool.Length; i++)
        {
            var go = new GameObject("treeCollider"); go.transform.SetParent(transform, false);
            var cc = go.AddComponent<CapsuleCollider>(); cc.radius = 0.4f; cc.height = 3f; cc.direction = 1; go.SetActive(false);
            treePool[i] = cc;
        }
    }

    static GameObject MakeObject(string name, Transform parent, Mesh m, Material mat, bool cast = false, bool receive = true)
    {
        var go = new GameObject(name); go.transform.SetParent(parent, false);
        go.AddComponent<MeshFilter>().sharedMesh = m;
        var mr = go.AddComponent<MeshRenderer>(); mr.sharedMaterial = mat;
        mr.shadowCastingMode = cast ? UnityEngine.Rendering.ShadowCastingMode.On : UnityEngine.Rendering.ShadowCastingMode.Off; mr.receiveShadows = receive;
        return go;
    }

    // ------------------------------------------------------------------ runtime
    float nextCull; int lastT = -1, lastB = -1;
    public bool HideTrees; public int LastToggleFrame;
    /// <summary>Test mode: a perfectly flat asphalt plane at FlatY instead of the real terrain (physics test harness).</summary>
    public bool Flat; public float FlatY;
    public bool TerrainPhysicsOnly = System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-terrainphys") >= 0;
    public void ForceStream() { nextCull = 0; }
    public void UpdateStreaming(Vector3 focus)
    {
        if (Time.unscaledTime < nextCull) return;
        nextCull = Time.unscaledTime + 0.4f;
        int aT = 0, aR = 0, aB = 0, aTr = 0;
        foreach (var ch in chunks)
        {
            float d = Vector2.Distance(ch.center, new Vector2(focus.x, focus.z)) - ChunkSize * 0.7f;
            bool near = d < 850f;
            Toggle(ch.terrain, near); Toggle(ch.terrainLow, !near && d < 3600f);
            Toggle(ch.roads, d < 1600f);
            Toggle(ch.buildings, d < 1300f);
            Toggle(ch.trees, d < 900f && !HideTrees);
            Toggle(ch.street, d < 800f);
            if (ch.terrain != null && ch.terrain.activeSelf) aT++;
            if (ch.roads != null && ch.roads.activeSelf) aR++;
            if (ch.buildings != null && ch.buildings.activeSelf) aB++;
            if (ch.trees != null && ch.trees.activeSelf) aTr++;
        }
        if (aT != lastT || aB != lastB) { lastT = aT; lastB = aB; Log.I("stream", $"active chunks: terrain={aT} roads={aR} buildings={aB} trees={aTr} around ({focus.x:F0},{focus.z:F0})"); }
        // pool obstacle colliders (trunks, poles, lamp posts, hedges) that are at the car's own height, so nothing below a bridge or above a tunnel can touch it
        int used = 0;
        if (focus.y > 1f)
        {
            int cx = Mathf.FloorToInt(focus.x / 16f), cz = Mathf.FloorToInt(focus.z / 16f);
            for (int dx = -2; dx <= 2 && used < treePool.Length; dx++)
                for (int dz = -2; dz <= 2 && used < treePool.Length; dz++)
                    if (treeHash.TryGetValue(((long)(cx + dx) << 32) ^ (uint)(cz + dz), out var list))
                        foreach (int oi in list)
                        {
                            var ob = obstacles[oi];
                            if (Mathf.Abs(ob.pos.y - (focus.y - 0.7f)) > 3.2f) continue;         // car mount is ~0.7 m above the road
                            if (used >= treePool.Length) break;
                            var t = treePool[used++];
                            t.radius = Mathf.Max(ob.r, 0.15f); t.height = ob.h; t.transform.position = ob.pos + Vector3.up * (ob.h * 0.5f);
                            t.gameObject.SetActive(true);
                        }
        }
        for (int i = used; i < treePool.Length; i++) treePool[i].gameObject.SetActive(false);
    }
    void Toggle(GameObject go, bool on) { if (go != null && go.activeSelf != on) { go.SetActive(on); LastToggleFrame = Time.frameCount; } }

    /// <summary>Ground height for the car: terrain, or a bridge deck if one is within reach of refY.</summary>
    public const float RoadLift = 0.05f;
    public float GroundHeight(float x, float z, float refY, out Surface surface)
    {
        if (Flat) { surface = Surface.Asphalt; return FlatY; }
        float terrain = Data.TerrainHeight(x, z);
        surface = Roads.Query(x, z, refY, out float deck, out float roadY, out float wgt);
        if (TerrainPhysicsOnly) { return float.IsNaN(deck) ? terrain : deck + RoadLift; }        // old behaviour (benchmark comparison)
        if (!float.IsNaN(deck)) return deck + RoadLift;
        if (wgt > 0f) return Mathf.Lerp(terrain, roadY + RoadLift, wgt);      // on the carriageway the physics surface IS the ribbon; it fades into the terrain over 0.6 m
        return terrain;
    }

}
