using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Threading.Tasks;
using UnityEngine;

/// <summary>
/// Streams the ~1000 km2 world. Three tiers:
///   far  (whole map, always resident): 64 m terrain in 1.6 km tiles, hidden where chunks cover it
///   mid  (chunks within 5 km): 16 m terrain, roads, building shells, water
///   near (chunks within 1.6 km): 4 m terrain, road markings, facades, trees, shrubs, street furniture, building colliders
/// Chunks are read, parsed and meshed on worker threads; the main thread only creates Unity objects, a few per frame.
/// </summary>
public class WorldBuilder : MonoBehaviour
{
    public const float MidRadius = 5000f, MidKeep = 5700f, NearRadius = 1600f, NearKeep = 1950f;
    const int MaxInFlight = 4;
    int FarTile = 1600;                                                    // far terrain tile size (m): grows with the world so the tile count stays around a thousand

    public WorldData Data;
    public RoadIndex Roads = new RoadIndex();
    public float Progress;
    public bool Ready;
    public string Error;

    Material terrainMat, roadMat, markMat, buildingMat, treeMat, waterMat, farMat, flatMat;

    enum State { Loading, Mid, LoadingNear, Near }
    class Chunk
    {
        public int key, ci, cj; public Vector2 center; public State state;
        public ChunkData data; public ChunkMeshes mid; public Task<ChunkMeshes> task; public ChunkData pendingData;
        public Transform root;
        public GameObject terrainLow, roads, buildings, water, terrain, marks, facades, trees, street, paved;
        public List<long> obstacleCells = new List<long>();
        public bool cancelled;
    }
    readonly Dictionary<int, Chunk> chunks = new Dictionary<int, Chunk>();
    readonly HashSet<int> missing = new HashSet<int>();          // chunks that failed to load
    HashSet<int> exists;                                          // chunks that have a file (one directory listing at start)
    readonly List<Chunk> inflight = new List<Chunk>();          // chunks with a worker task running; finished ones get their Unity objects on the main thread
    GameObject[] farTiles; int[] farCovered;

    // solid small obstacles (trunks, poles, lamps, hedges), pooled around the car
    struct Ob { public Obstacle o; public int owner; }
    readonly Dictionary<long, List<Ob>> obHash = new Dictionary<long, List<Ob>>();
    CapsuleCollider[] treePool;
    static long HashKey(float x, float z) => ((long)Mathf.FloorToInt(x / 16f) << 32) ^ (uint)Mathf.FloorToInt(z / 16f);

    public int LoadedMid { get { int n = 0; foreach (var c in chunks.Values) if (c.state >= State.Mid) n++; return n; } }
    public int LoadedNear { get { int n = 0; foreach (var c in chunks.Values) if (c.state == State.Near || c.state == State.LoadingNear && c.terrain != null) n++; return n; } }
    public int InFlight => inflight.Count;

    // ------------------------------------------------------------------ startup
    public IEnumerator Build()
    {
        var total = System.Diagnostics.Stopwatch.StartNew();
        var shader = GameAssets.Flat;
        Log.I("world", $"shader={(shader != null ? shader.name : "NULL")} supported={(shader != null && shader.isSupported)}");
        flatMat = new Material(shader);
        roadMat = new Material(shader); roadMat.SetFloat("_OffsetFactor", -2); roadMat.SetFloat("_OffsetUnits", -2);
        markMat = new Material(shader); markMat.SetFloat("_OffsetFactor", -4); markMat.SetFloat("_OffsetUnits", -4);
        terrainMat = new Material(shader); terrainMat.SetFloat("_Noise", 0.16f); terrainMat.SetFloat("_NoiseScale", 0.55f);
        roadMat.SetFloat("_Noise", 0.10f); roadMat.SetFloat("_NoiseScale", 1.6f);
        buildingMat = new Material(shader); buildingMat.SetFloat("_Noise", 0.05f); buildingMat.SetFloat("_NoiseScale", 2.2f);
        treeMat = new Material(shader); treeMat.SetFloat("_Noise", 0.10f); treeMat.SetFloat("_NoiseScale", 1.1f);
        waterMat = new Material(GameAssets.Water);
        farMat = new Material(shader); farMat.SetFloat("_Noise", 0.10f); farMat.SetFloat("_NoiseScale", 0.02f);
        yield return null;

        try
        {
            Data = WorldData.Load();
            Places.Load();
            Log.I("world", $"world {WorldData.NCX}x{WorldData.NCZ} chunks ({(WorldData.Legacy ? "legacy" : "world.json")}), far terrain {Data.FarNx}x{Data.FarNz} @ {Data.FarCell:F0} m, spawn=({Data.Spawn.x:F0},{Data.Spawn.z:F0}) hdg={Data.Spawn.heading:F0}");
            ScanChunks();
            BuildFarTiles();
            treePool = new CapsuleCollider[System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-notcol") >= 0 ? 0 : 110];
            for (int i = 0; i < treePool.Length; i++)
            {
                var go = new GameObject("treeCollider"); go.transform.SetParent(transform, false);
                var cc = go.AddComponent<CapsuleCollider>(); cc.radius = 0.4f; cc.height = 3f; cc.direction = 1; go.SetActive(false);
                treePool[i] = cc;
            }
        }
        catch (System.Exception e) { Error = "load: " + e.Message; Log.I("world", "FAILED: " + e); yield break; }
        Progress = 0.05f;

        // load everything around the spawn point before the game starts: near detail close by, mid tier further out
        var focus = new Vector3(Data.Spawn.x, 0, Data.Spawn.z);
        yield return LoadAround(focus, 1800f, 3500f, p => Progress = 0.05f + 0.95f * p);
        Ready = true;
        Log.I("world", $"READY in {total.ElapsedMilliseconds} ms: {chunks.Count} chunks ({LoadedNear} near), mem={System.GC.GetTotalMemory(false) / 1048576} MB managed");
    }

    /// <summary>Blocks (frame by frame) until every chunk within nearR has its near tier and every chunk within midR its mid tier.</summary>
    public IEnumerator LoadAround(Vector3 focus, float nearR, float midR, System.Action<float> progress = null)
    {
        var sw = System.Diagnostics.Stopwatch.StartNew();
        int guard = 0;
        while (guard++ < 6000)
        {
            Stream(focus, midR, nearR, midR + 600f, nearR + 300f, true);
            FinishReady(1.0e9f, 8);                                    // loading screen: take as much main-thread time as needed
            int wantMid = 0, haveMid = 0, wantNear = 0, haveNear = 0;
            ForEachAround(focus, midR, (ci, cj, d) =>
            {
                int key = cj * WorldData.NCX + ci; if (missing.Contains(key)) return;
                wantMid++; if (chunks.TryGetValue(key, out var c) && c.state >= State.Mid) haveMid++;
                if (d < nearR) { wantNear++; if (chunks.TryGetValue(key, out var c2) && c2.state == State.Near) haveNear++; }
            });
            progress?.Invoke((haveMid + haveNear) / Mathf.Max(1f, wantMid + wantNear));
            if (haveMid >= wantMid && haveNear >= wantNear) break;
            yield return null;
        }
        Log.I("world", $"LoadAround ({focus.x:F0},{focus.z:F0}) near<{nearR:F0} mid<{midR:F0} done in {sw.ElapsedMilliseconds} ms after {guard} frames");
    }

    void ForEachAround(Vector3 focus, float radius, System.Action<int, int, float> f)
    {
        int r = Mathf.CeilToInt(radius / WorldData.ChunkSize) + 1;
        int c0 = WorldData.ChunkIndexX(focus.x), r0 = WorldData.ChunkIndexZ(focus.z);
        for (int cj = r0 - r; cj <= r0 + r; cj++)
            for (int ci = c0 - r; ci <= c0 + r; ci++)
            {
                if (ci < 0 || cj < 0 || ci >= WorldData.NCX || cj >= WorldData.NCZ) continue;
                var center = WorldData.ChunkCenter(ci, cj);
                float d = Vector2.Distance(center, new Vector2(focus.x, focus.z)) - WorldData.ChunkSize * 0.7f;
                if (d < radius) f(ci, cj, d);
            }
    }

    // ------------------------------------------------------------------ far terrain
    void ScanChunks()
    {
        exists = new HashSet<int>();
        foreach (var f in Directory.GetFiles(Path.Combine(WorldData.Dir, "chunks"), "m_*.bin.gz"))
        {   // m_{ci}_{cj}.bin.gz
            var parts = Path.GetFileName(f).Split('_'); if (parts.Length != 3) continue;
            int ci, cj; if (int.TryParse(parts[1], out ci) && int.TryParse(parts[2].Split('.')[0], out cj)) exists.Add(cj * WorldData.NCX + ci);
        }
        Log.I("world", $"{exists.Count} chunk files");
    }

    int farTilesX, farTilesZ; int[] farNeeded;
    void BuildFarTiles()
    {
        FarTile = 1600; while ((WorldData.NCX * (long)WorldData.ChunkSize / FarTile) * (WorldData.NCZ * (long)WorldData.ChunkSize / FarTile) > 1300) FarTile *= 2;
        int cpt = FarTile / WorldData.ChunkSize;                                                            // chunks per tile side
        farTilesX = Mathf.CeilToInt(WorldData.NCX / (float)cpt); farTilesZ = Mathf.CeilToInt(WorldData.NCZ / (float)cpt);
        int per = Mathf.RoundToInt(FarTile / Data.FarCell), n = per + 1, NX = Data.FarNx, NZ = Data.FarNz;
        farMat.SetFloat("_HoleRadius", 0f);
        farTiles = new GameObject[farTilesX * farTilesZ]; farCovered = new int[farTiles.Length]; farNeeded = new int[farTiles.Length];
        foreach (int key in exists) farNeeded[(key / WorldData.NCX / cpt) * farTilesX + (key % WorldData.NCX) / cpt]++;
        var root = new GameObject("far terrain").transform; root.SetParent(transform, false);
        int made = 0;
        for (int tz = 0; tz < farTilesZ; tz++)
            for (int tx = 0; tx < farTilesX; tx++)
            {
                if (farNeeded[tz * farTilesX + tx] == 0) continue;                                          // no chunks there: nothing to show
                var mb = new MeshBuilder();
                for (int z = 0; z < n; z++)
                    for (int x = 0; x < n; x++)
                    {
                        int gx = Mathf.Min(tx * per + x, NX - 1), gz = Mathf.Min(tz * per + z, NZ - 1), gi = gz * NX + gx;
                        mb.Vertex(new Vector3(WorldData.X0 + gx * Data.FarCell, Data.FarH[gi] - 6f, WorldData.Z0 + gz * Data.FarCell), new Color32(Data.FarC[gi * 3], Data.FarC[gi * 3 + 1], Data.FarC[gi * 3 + 2], 255));
                    }
                for (int z = 0; z < per; z++)
                    for (int x = 0; x < per; x++)
                    {
                        int a = z * n + x, b = a + n, c = a + 1, e = b + 1;
                        if (((x + z) & 1) == 0) { mb.Tri(a, b, e); mb.Tri(a, e, c); } else { mb.Tri(a, b, c); mb.Tri(c, b, e); }
                    }
                farTiles[tz * farTilesX + tx] = MakeObject($"far_{tx}_{tz}", root, mb.ToMesh("far"), farMat, false, false); made++;
            }
        Log.I("world", $"far terrain: {made} tiles of {FarTile} m");
    }

    void UpdateFarCoverage(Chunk c, int delta)
    {
        int cpt = FarTile / WorldData.ChunkSize, t = (c.cj / cpt) * farTilesX + (c.ci / cpt);
        if (farTiles == null || t >= farTiles.Length || farTiles[t] == null) return;
        farCovered[t] += delta;
        // (far tiles stay visible; the shader cuts a hole where the detailed chunks are)
    }

    // ------------------------------------------------------------------ streaming
    string PathOf(string kind, int ci, int cj) => Path.Combine(WorldData.Dir, "chunks", $"{kind}_{ci}_{cj}.bin.gz");

    struct Req { public float prio; public int ci, cj; public bool near; }
    readonly List<Req> reqs = new List<Req>();

    /// <summary>Start loads for what is missing, drop what is far away. Cheap enough to call every few tenths of a second.</summary>
    void Stream(Vector3 focus, float midR, float nearR, float midKeep, float nearKeep, bool unlimited)
    {
        reqs.Clear();
        ForEachAround(focus, midR, (ci, cj, d) =>
        {
            int key = cj * WorldData.NCX + ci;
            if (missing.Contains(key)) return;
            if (!chunks.TryGetValue(key, out var c)) { reqs.Add(new Req { prio = d, ci = ci, cj = cj }); return; }
            if (c.state == State.Mid && d < nearR) reqs.Add(new Req { prio = d * 0.5f, ci = ci, cj = cj, near = true });
        });
        reqs.Sort((a, b) => a.prio.CompareTo(b.prio));
        int busy = InFlight;
        foreach (var r in reqs)
        {
            if (busy >= MaxInFlight * (unlimited ? 3 : 1)) break;
            if (r.near) StartNear(chunks[r.cj * WorldData.NCX + r.ci]); else if (!StartMid(r.ci, r.cj)) continue;
            busy++;
        }
        // unload
        List<Chunk> drop = null, downgrade = null;
        var f2 = new Vector2(focus.x, focus.z);
        foreach (var c in chunks.Values)
        {
            if (c.task != null) continue;
            float d = Vector2.Distance(c.center, f2) - WorldData.ChunkSize * 0.7f;
            if (d > midKeep) (drop ??= new List<Chunk>()).Add(c);
            else if (c.state == State.Near && d > nearKeep) (downgrade ??= new List<Chunk>()).Add(c);
        }
        if (drop != null) foreach (var c in drop) Unload(c);
        if (downgrade != null) foreach (var c in downgrade) DropNear(c);
    }

    bool StartMid(int ci, int cj)
    {
        int key = cj * WorldData.NCX + ci; string path = PathOf("m", ci, cj);
        if (!exists.Contains(key)) { missing.Add(key); return false; }
        var c = new Chunk { key = key, ci = ci, cj = cj, state = State.Loading, center = WorldData.ChunkCenter(ci, cj) };
        chunks[key] = c;
        c.task = Task.Run(() => { c.pendingData = ChunkData.ParseMid(path); return ChunkMeshes.BuildMid(c.pendingData); });
        inflight.Add(c);
        return true;
    }

    void StartNear(Chunk c)
    {
        string path = PathOf("n", c.ci, c.cj);
        c.state = State.LoadingNear; var d = c.data;
        c.task = Task.Run(() =>
        {
            if (File.Exists(path)) d.ParseNear(path); else { d.Trees = new float[0]; d.Shrubs = new float[0]; d.HasNear = true; }
            return ChunkMeshes.BuildNear(d, new ChunkMeshes());
        });
        inflight.Add(c);
    }

    /// <summary>Turn finished worker results into Unity objects, within a time budget (ms).</summary>
    void FinishReady(float budgetMs, int maxChunks)
    {
        var sw = System.Diagnostics.Stopwatch.StartNew(); int done = 0;
        for (int i = 0; i < inflight.Count; i++)
        {
            var c = inflight[i];
            if (!c.task.IsCompleted) continue;
            if (done > 0 && (sw.ElapsedMilliseconds > budgetMs || done >= maxChunks)) break;
            inflight.RemoveAt(i--);
            var task = c.task; c.task = null;
            if (task.IsFaulted) { Log.I("stream", $"chunk {c.ci},{c.cj} failed: {task.Exception?.GetBaseException().Message}"); if (c.state == State.Loading) chunks.Remove(c.key); else c.state = State.Mid; missing.Add(c.key); continue; }
            if (c.state == State.Loading) FinishMid(c, task.Result); else FinishNear(c, task.Result);
            done++;
        }
    }

    void FinishMid(Chunk c, ChunkMeshes m)
    {
        c.data = c.pendingData; c.pendingData = null; c.state = State.Mid;
        var go = new GameObject($"chunk_{c.ci}_{c.cj}"); go.transform.SetParent(transform, false); c.root = go.transform;
        c.terrainLow = MakeObject("terrainLow", c.root, m.TerrainLow.ToMesh("terrainLow"), terrainMat, false, true);
        if (!m.Roads.Empty) c.roads = MakeObject("roads", c.root, m.Roads.ToMesh("roads"), roadMat);
        if (!m.Buildings.Empty) c.buildings = MakeObject("buildings", c.root, m.Buildings.ToMesh("buildings"), buildingMat, true, true);
        if (!m.Water.Empty) c.water = MakeObject("water", c.root, m.Water.ToMesh("water"), waterMat, false, false);
        Data.Chunks[c.key] = c.data; Data.Changed();
        Roads.Add(c.key, c.data.Roads, c.data.Junctions);
        UpdateFarCoverage(c, +1);
    }

    void FinishNear(Chunk c, ChunkMeshes m)
    {
        c.state = State.Near;
        c.terrain = MakeObject("terrain", c.root, m.Terrain.ToMesh("terrain"), terrainMat, false, true);
        if (System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-noribbonphysics") < 0) Roads.AddRibbons(c.key, c.data.Roads, c.data.Junctions);
        if (!m.Paved.Empty) c.paved = MakeObject("paved", c.root, m.Paved.ToMesh("paved"), roadMat, false, true);      // car parks: road material, driven as asphalt
        Roads.AddPaved(PavedOwner(c.key), c.data.Parks);
        if (!m.Marks.Empty) c.marks = MakeObject("marks", (c.roads != null ? c.roads : c.terrain).transform, m.Marks.ToMesh("marks"), markMat);
        if (c.buildings != null && !m.Facades.Empty) c.facades = MakeObject("facades", c.buildings.transform, m.Facades.ToMesh("facades"), markMat, false, true);
        if (c.buildings != null && !m.Collision.Empty && System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-nobcol") < 0)
        {
            var mc = c.buildings.AddComponent<MeshCollider>();
            mc.sharedMesh = m.Collision.ToMesh("buildingCollision");
            mc.cookingOptions = MeshColliderCookingOptions.CookForFasterSimulation;
        }
        if (!m.Trees.Empty) c.trees = MakeObject("trees", c.root, m.Trees.ToMesh("trees"), treeMat, true, true);
        if (!m.Street.Empty) c.street = MakeObject("street", c.root, m.Street.ToMesh("street"), flatMat, true, true);
        if (c.trees != null) c.trees.layer = MiniMap.HiddenLayer;                          // the minimap looks straight down: canopies would hide the roads
        if (c.street != null) c.street.layer = MiniMap.HiddenLayer;
        foreach (var o in m.Obstacles)
        {
            long k = HashKey(o.pos.x, o.pos.z);
            if (!obHash.TryGetValue(k, out var l)) obHash[k] = l = new List<Ob>();
            l.Add(new Ob { o = o, owner = c.key }); c.obstacleCells.Add(k);
        }
        Data.Changed();
    }

    static int PavedOwner(int key) => key + (1 << 28);                                   // the near tier's paved areas, apart from the chunk's roads

    void DropNear(Chunk c)
    {
        foreach (var go in new[] { c.terrain, c.marks, c.facades, c.trees, c.street, c.paved }) DestroyObject(go);
        Roads.Remove(PavedOwner(c.key));
        var mc = c.buildings != null ? c.buildings.GetComponent<MeshCollider>() : null;
        if (mc != null) { if (mc.sharedMesh != null) Destroy(mc.sharedMesh); Destroy(mc); }
        c.terrain = c.marks = c.facades = c.trees = c.street = c.paved = null;
        RemoveObstacles(c); c.state = State.Mid; Roads.RemoveRibbons(c.key);
        c.data.Trees = c.data.Shrubs = null; c.data.HasNear = false;
    }

    void Unload(Chunk c)
    {
        RemoveObstacles(c);
        Roads.Remove(c.key); Roads.Remove(PavedOwner(c.key));
        if (c.root != null) { foreach (var mf in c.root.GetComponentsInChildren<MeshFilter>(true)) if (mf.sharedMesh != null) Destroy(mf.sharedMesh); var mc = c.buildings != null ? c.buildings.GetComponent<MeshCollider>() : null; if (mc != null && mc.sharedMesh != null) Destroy(mc.sharedMesh); Destroy(c.root.gameObject); }
        if (c.state >= State.Mid) { Data.Chunks.Remove(c.key); Data.Changed(); UpdateFarCoverage(c, -1); }
        chunks.Remove(c.key);
    }

    void RemoveObstacles(Chunk c)
    {
        foreach (long k in new HashSet<long>(c.obstacleCells))
            if (obHash.TryGetValue(k, out var l)) { l.RemoveAll(o => o.owner == c.key); if (l.Count == 0) obHash.Remove(k); }
        c.obstacleCells.Clear();
    }

    static void DestroyObject(GameObject go)
    {
        if (go == null) return;
        var mf = go.GetComponent<MeshFilter>(); if (mf != null && mf.sharedMesh != null) Destroy(mf.sharedMesh);
        Destroy(go);
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
    float nextCull; Vector3 lastFocus;
    public bool HideTrees; public int LastToggleFrame;
    /// <summary>Debug: comma list of layers to hide (buildings, water, roads, far, terrainlow, trees) to find what sparkles.</summary>
    public string DebugHide = System.Environment.GetEnvironmentVariable("BERAT_HIDE") ?? "";
    /// <summary>Test mode: a perfectly flat asphalt plane at FlatY instead of the real terrain (physics test harness).</summary>
    public bool Flat; public float FlatY;
    public bool TerrainPhysicsOnly = System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-terrainphys") >= 0;
    public void ForceStream() { nextCull = 0; }
    /// <summary>Radius multiplier for the mid tier (the map view can ask for more of the world).</summary>
    public float MidScale = 1f;

    /// <summary>True when the terrain under (x,z) is loaded (mid tier or better): the car may drive there.</summary>
    public bool GroundLoaded(float x, float z) => Data.HasChunk(x, z);
    /// <summary>The car and everything within 30 m of it (all four corners of a 60 m square) stands on loaded terrain, so no wheel or hull point can meet the coarse far terrain.</summary>
    public bool FootprintLoaded(Vector3 p) => Data.HasChunk(p.x - 30f, p.z - 30f) && Data.HasChunk(p.x + 30f, p.z - 30f) && Data.HasChunk(p.x - 30f, p.z + 30f) && Data.HasChunk(p.x + 30f, p.z + 30f);

    public void UpdateStreaming(Vector3 focus)
    {
        FinishReady(3f, 2);                                             // a couple of ms per frame
        if (Time.unscaledTime < nextCull) return;
        nextCull = Time.unscaledTime + 0.35f; lastFocus = focus;
        Stream(focus, MidRadius * MidScale, NearRadius, MidKeep * MidScale, NearKeep, false);
        {   // the far terrain has a hole around the player, as wide as the loaded detail allows: never past a chunk that is still on its way
            float hole = MidRadius * MidScale - 350f;
            foreach (var r in reqs) if (!r.near) hole = Mathf.Min(hole, r.prio - 150f);
            farMat.SetVector("_HoleCenter", new Vector4(focus.x, 0f, focus.z, 0f)); farMat.SetFloat("_HoleRadius", Mathf.Max(hole, 0f));
        }

        if (DebugHide.Contains("far")) { foreach (var t in farTiles) if (t != null) t.SetActive(false); }
        var f2 = new Vector2(focus.x, focus.z);
        foreach (var c in chunks.Values)
        {
            if (c.state < State.Mid) continue;
            float d = Vector2.Distance(c.center, f2) - WorldData.ChunkSize * 0.7f;
            bool full = c.terrain != null && d < 1150f;
            Toggle(c.terrain, full && !DebugHide.Contains("terrainlow")); Toggle(c.terrainLow, !full && !DebugHide.Contains("terrainlow"));
            Toggle(c.buildings, !DebugHide.Contains("buildings")); Toggle(c.water, !DebugHide.Contains("water")); Toggle(c.roads, !DebugHide.Contains("roads"));
            Toggle(c.marks, d < 1000f);
            Toggle(c.facades, d < 1300f);
            Toggle(c.trees, d < 900f && !HideTrees);
            Toggle(c.street, d < 800f);
        }
        // pool obstacle colliders (trunks, poles, lamp posts, hedges) that are at the car's own height, so nothing below a bridge or above a tunnel can touch it
        int used = 0;
        if (focus.y > 1f)
        {
            int cx = Mathf.FloorToInt(focus.x / 16f), cz = Mathf.FloorToInt(focus.z / 16f);
            for (int dx = -2; dx <= 2 && used < treePool.Length; dx++)
                for (int dz = -2; dz <= 2 && used < treePool.Length; dz++)
                    if (obHash.TryGetValue(((long)(cx + dx) << 32) ^ (uint)(cz + dz), out var list))
                        foreach (var ob0 in list)
                        {
                            var ob = ob0.o;
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

    /// <summary>Debug audit: how close to a road edge is any pole / trunk / hedge collider, and what stands near a given spot.</summary>
    public void AuditObstacles(Vector2 spot)
    {
        int bad = 0, n = 0; float worst = 999f; string nearest = "none within 12 m"; float nd = 12f;
        foreach (var list in obHash.Values)
            foreach (var ob in list)
            {
                var o = ob.o; n++;
                float c = Roads.EdgeClearance(o.pos.x, o.pos.z) - o.r;
                if (c < 0.05f) { bad++; Log.I("audit", $"  touching: r={o.r:F2} h={o.h:F1} at ({o.pos.x:F0},{o.pos.z:F0}) cell {Grid.CellLabel(o.pos.x, o.pos.z)}"); }
                worst = Mathf.Min(worst, c);
                float d = Vector2.Distance(new Vector2(o.pos.x, o.pos.z), spot);
                if (d < nd) { nd = d; nearest = $"obstacle r={o.r:F2} h={o.h:F1} at ({o.pos.x:F1},{o.pos.z:F1}), {d:F1} m away, {c:F2} m clear of the road"; }
            }
        Log.I("audit", $"{n} obstacles (trunks, poles, lamps, hedges) loaded: {bad} touching a road edge, tightest clearance {worst:F2} m");
        Log.I("audit", $"near [{spot.x:F0},{spot.y:F0}]: {nearest}");
    }

    /// <summary>Ground height for the car: terrain, or a bridge deck if one is within reach of refY. Water is reported as a surface when the bed is below its level.</summary>
    public const float RoadLift = ChunkMeshes.RoadLift;
    public float GroundHeight(float x, float z, float refY, out Surface surface)
    {
        if (Flat) { surface = Surface.Asphalt; return FlatY; }
        float terrain = Data.TerrainHeight(x, z);                              // already shaped around the roads by the world builder
        surface = Roads.Query(x, z, refY, out float deck, out float roadY, out float wgt);
        if (TerrainPhysicsOnly) { return float.IsNaN(deck) ? terrain : deck + RoadLift; }        // old behaviour (benchmark comparison)
        if (!float.IsNaN(deck)) return deck + RoadLift;
        float ribbon = Roads.RibbonHeight(x, z);                                // BM07: beside the road the ground is the embankment ribbon drawn over the (lowered) terrain
        if (!float.IsNaN(ribbon)) terrain = ribbon;
        if (wgt > 0f) return Mathf.Lerp(terrain, roadY + RoadLift, wgt);      // on the carriageway the physics surface IS the road; it fades into the ground over 0.6 m
        float wl = Data.WaterLevel(x, z);
        if (!float.IsNaN(wl) && terrain < wl - 0.02f) surface = Surface.Water;
        return terrain;
    }

    /// <summary>
    /// Is this point inside a building footprint (at the height of the walls)? If so `exit` is the nearest spot outside, 2.5 m off the wall.
    /// A car that ended up inside (teleport onto a roof, tunnelling at speed) is otherwise trapped: the wall colliders push it back in from every side.
    /// </summary>
    public bool InsideBuilding(Vector3 p, out Vector3 exit)
    {
        exit = p; int ci0 = WorldData.ChunkIndexX(p.x), cj0 = WorldData.ChunkIndexZ(p.z);
        for (int dj = -1; dj <= 1; dj++)
            for (int di = -1; di <= 1; di++)
            {
                int ci = ci0 + di, cj = cj0 + dj; if (ci < 0 || cj < 0 || ci >= WorldData.NCX || cj >= WorldData.NCZ) continue;
                if (!Data.Chunks.TryGetValue(cj * WorldData.NCX + ci, out var c)) continue;
                foreach (var b in c.Buildings)
                {
                    if (p.y > b.b + b.h + Mathf.Max(b.r, 0f) + 1f || p.y < b.b - 3f) continue;
                    int n = b.p.Length / 2; if (n < 3) continue;
                    bool inside = false; float minx = 1e9f, maxx = -1e9f, minz = 1e9f, maxz = -1e9f;
                    for (int i = 0, j = n - 1; i < n; j = i++)
                    {
                        float xi = b.p[i * 2], zi = b.p[i * 2 + 1], xj = b.p[j * 2], zj = b.p[j * 2 + 1];
                        minx = Mathf.Min(minx, xi); maxx = Mathf.Max(maxx, xi); minz = Mathf.Min(minz, zi); maxz = Mathf.Max(maxz, zi);
                        if ((zi > p.z) != (zj > p.z) && p.x < (xj - xi) * (p.z - zi) / (zj - zi) + xi) inside = !inside;
                    }
                    if (!inside) continue;
                    // nearest point on the outline, pushed outward (rings are counter-clockwise: the outward normal of edge d is (d.y, -d.x))
                    float best = 1e9f; Vector2 q = default, nrm = default;
                    for (int i = 0; i < n; i++)
                    {
                        Vector2 a = new Vector2(b.p[i * 2], b.p[i * 2 + 1]), e = new Vector2(b.p[((i + 1) % n) * 2], b.p[((i + 1) % n) * 2 + 1]), ab = e - a;
                        float t = Mathf.Clamp01(Vector2.Dot(new Vector2(p.x, p.z) - a, ab) / Mathf.Max(ab.sqrMagnitude, 1e-4f));
                        Vector2 pt = a + ab * t; float d = (pt - new Vector2(p.x, p.z)).sqrMagnitude;
                        if (d < best) { best = d; q = pt; nrm = new Vector2(ab.y, -ab.x).normalized; }
                    }
                    Vector2 outp = q + nrm * 2.5f;
                    exit = new Vector3(outp.x, p.y, outp.y);
                    return true;
                }
            }
        return false;
    }

    /// <summary>Depth of water above the ground at (x,z) (0 when dry).</summary>
    public float WaterDepth(float x, float z)
    {
        float wl = Data.WaterLevel(x, z); if (float.IsNaN(wl)) return 0f;
        return Mathf.Max(0f, wl - Data.TerrainHeight(x, z));
    }
}
