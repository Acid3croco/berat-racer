using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using UnityEngine;

public class RoadData
{
    public float hw; public bool dirt, bridge; public int lead, trail, fid; public float pri; public Vector2 t0, t1;   // fid: source feature; t0/t1: shared tangent at a joint with the next road (zero = none)
    // owned segments k satisfy lead <= k < n-1-trail (the rest overlap the neighbours for a seamless ribbon)
    public string name = ""; public string imp = "0"; public float[] pts;
    Vector2[] xz; public Vector2 Min, Max;
    /// <summary>Ground-plane polyline (x, z) of the OWNED part of the road (the overlap points that only shape the ribbon at chunk joins are left out), cached; also fills Min / Max.</summary>
    public Vector2[] XZ
    {
        get
        {
            if (xz != null) return xz;
            int n = pts.Length / 3, first = lead, last = Mathf.Max(n - 1 - trail, first + 1);
            xz = new Vector2[last - first + 1]; Min = new Vector2(1e9f, 1e9f); Max = new Vector2(-1e9f, -1e9f);
            for (int i = 0; i < xz.Length; i++) { int k = first + i; xz[i] = new Vector2(pts[k * 3], pts[k * 3 + 2]); Min = Vector2.Min(Min, xz[i]); Max = Vector2.Max(Max, xz[i]); }
            return xz;
        }
    }
}
public class BuildingData { public float[] p; public float b, h, r; public float[] rc; public int[] c; public int[] w; public string k, n; public int fe; public float[] tw; public float[] cp; public int[] cn; }
[Serializable] public class WorldInfo { public float x0, z0; public int ncx, ncz; }
[Serializable] public class SpawnData { public float x, y, z, heading; public string road; }
public class WaterArea { public float level; public float[] ring; public float[] ys; }      // ring = x,z pairs; ys = surface height per vertex; level = their mean
public class WaterLine { public float hw; public int lead, trail; public float[] pts; }

/// <summary>One 400 m chunk as exported by tools/build_world.py: terrain grid, roads, water, buildings (mid data), trees and shrubs (near data).</summary>
public class ChunkData
{
    public int ci, cj, key; public float x0, z0;
    public float[] H;                 // CV x CV heights, row = z (south first)
    public byte[] Col;                // CV x CV rgb, bare-ground colour
    public byte[] LowCol;             // 26 x 26 rgb for the 16 m LOD (keeps forests and villages)
    public RoadData[] Roads, Ctx;
    public WaterArea[] Areas; public WaterLine[] Lines;
    public BuildingData[] Buildings;
    public float[] Trees, Shrubs;
    public bool HasNear;
    public const int CV = WorldData.CV, LV = 26;

    public float Height(float x, float z)
    {
        float fx = Mathf.Clamp((x - x0) / WorldData.Cell, 0, CV - 1.001f), fz = Mathf.Clamp((z - z0) / WorldData.Cell, 0, CV - 1.001f);
        int ix = (int)fx, iz = (int)fz; float tx = fx - ix, tz = fz - iz;
        float h00 = H[iz * CV + ix], h10 = H[iz * CV + ix + 1], h01 = H[(iz + 1) * CV + ix], h11 = H[(iz + 1) * CV + ix + 1];
        return Mathf.Lerp(Mathf.Lerp(h00, h10, tx), Mathf.Lerp(h01, h11, tx), tz);
    }

    static string Str(BinaryReader br)
    {
        int n = 0, shift = 0; byte b;
        do { b = br.ReadByte(); n |= (b & 0x7F) << shift; shift += 7; } while ((b & 0x80) != 0);
        return n == 0 ? "" : System.Text.Encoding.UTF8.GetString(br.ReadBytes(n));
    }
    static float[] Floats(BinaryReader br, int n) { var a = new float[n]; var bytes = br.ReadBytes(n * 4); Buffer.BlockCopy(bytes, 0, a, 0, bytes.Length); return a; }
    static int[] Ints(BinaryReader br, int n) { var a = new int[n]; var bytes = br.ReadBytes(n * 4); Buffer.BlockCopy(bytes, 0, a, 0, bytes.Length); return a; }
    static BinaryReader Open(string path)
    {
        using (var fs = File.OpenRead(path))
        using (var gz = new GZipStream(fs, CompressionMode.Decompress))
        {
            var ms = new MemoryStream(); gz.CopyTo(ms); ms.Position = 0;
            return new BinaryReader(ms);
        }
    }

    public static ChunkData ParseMid(string path)
    {
        using (var br = Open(path))
        {
            if (new string(br.ReadChars(4)) != "BM02") throw new InvalidDataException(path);
            var d = new ChunkData { ci = br.ReadInt32(), cj = br.ReadInt32() };
            d.key = d.cj * WorldData.NCX + d.ci; d.x0 = WorldData.X0 + d.ci * WorldData.ChunkSize; d.z0 = WorldData.Z0 + d.cj * WorldData.ChunkSize;
            int cv = br.ReadInt32(); if (cv != CV) throw new InvalidDataException("terrain size " + cv);
            float baseH = br.ReadSingle(), step = WorldData.Legacy ? 0.005f : br.ReadSingle();      // the height range of a chunk sets the step (mountain chunks span hundreds of metres)
            d.H = new float[CV * CV]; var q = br.ReadBytes(CV * CV * 2);
            for (int i = 0; i < d.H.Length; i++) d.H[i] = baseH + (q[i * 2] | q[i * 2 + 1] << 8) * step;
            d.Col = br.ReadBytes(CV * CV * 3); d.LowCol = br.ReadBytes(LV * LV * 3);
            d.Roads = ReadRoads(br, false); d.Ctx = ReadRoads(br, true);
            int na = br.ReadInt32(); d.Areas = new WaterArea[na];
            for (int i = 0; i < na; i++)
            {
                int n = br.ReadInt32(); var t = Floats(br, n * 3); var a = new WaterArea { ring = new float[n * 2], ys = new float[n] };
                for (int k = 0; k < n; k++) { a.ring[k * 2] = t[k * 3]; a.ys[k] = t[k * 3 + 1]; a.ring[k * 2 + 1] = t[k * 3 + 2]; a.level += t[k * 3 + 1] / n; }
                d.Areas[i] = a;
            }
            int nl = br.ReadInt32(); d.Lines = new WaterLine[nl];
            for (int i = 0; i < nl; i++) { var l = new WaterLine { hw = br.ReadSingle(), lead = br.ReadByte(), trail = br.ReadByte() }; int n = br.ReadInt32(); l.pts = Floats(br, n * 3); d.Lines[i] = l; }
            int nb = br.ReadInt32(); d.Buildings = new BuildingData[nb];
            for (int i = 0; i < nb; i++)
            {
                var b = new BuildingData(); int np = br.ReadInt32(); b.p = Floats(br, np * 2);
                b.b = br.ReadSingle(); b.h = br.ReadSingle(); b.r = br.ReadSingle();
                b.rc = Floats(br, br.ReadInt32());
                b.c = new[] { (int)br.ReadByte(), br.ReadByte(), br.ReadByte() }; b.w = new[] { (int)br.ReadByte(), br.ReadByte(), br.ReadByte() };
                b.k = Str(br); b.n = Str(br); b.fe = br.ReadInt32(); b.tw = Floats(br, br.ReadInt32());
                b.cp = Floats(br, br.ReadInt32() * 2); b.cn = Ints(br, br.ReadInt32());
                d.Buildings[i] = b;
            }
            return d;
        }
    }

    static RoadData[] ReadRoads(BinaryReader br, bool ctx)
    {
        int n = br.ReadInt32(); var a = new RoadData[n];
        for (int i = 0; i < n; i++)
        {
            var r = new RoadData { hw = br.ReadSingle() * WorldData.RoadWidthScale }; byte fl = br.ReadByte(); r.dirt = (fl & 1) != 0; r.bridge = (fl & 2) != 0; r.imp = br.ReadByte().ToString(); r.lead = br.ReadByte(); r.trail = br.ReadByte(); r.fid = br.ReadInt32(); r.pri = br.ReadSingle(); r.t0 = new Vector2(br.ReadSingle(), br.ReadSingle()); r.t1 = new Vector2(br.ReadSingle(), br.ReadSingle());
            r.name = Str(br); r.pts = Floats(br, br.ReadInt32() * 3); a[i] = r;
        }
        return a;
    }

    public void ParseNear(string path)
    {
        using (var br = Open(path))
        {
            if (new string(br.ReadChars(4)) != "BN01") throw new InvalidDataException(path);
            Trees = Floats(br, br.ReadInt32() * 4); Shrubs = Floats(br, br.ReadInt32() * 4);
        }
        HasNear = true;
    }
}

/// <summary>Everything the game knows about the world: the resident whole-map far terrain and the currently loaded chunks.</summary>
public class WorldData
{
    public const float Cell = 4f;
    /// <summary>Roads are drawn, driven and cleared at 115% of their real (BD TOPO) width: easier cruising in a game whose cars are wider than they look.</summary>
    public const float RoadWidthScale = 1.15f;
    public const int ChunkSize = 400, CV = 101;
    // world extents come from world.json; without one it is the original 32 x 32 km block (legacy format)
    public static float X0 = -16000f, Z0 = -16000f; public static int NCX = 80, NCZ = 80; public static bool Legacy = true;
    public static float MaxX => X0 + NCX * ChunkSize;
    public static float MaxZ => Z0 + NCZ * ChunkSize;
    public static bool InBounds(float x, float z, float margin = 0f) => x > X0 + margin && x < MaxX - margin && z > Z0 + margin && z < MaxZ - margin;
    public static Vector2 ChunkCenter(int ci, int cj) => new Vector2(X0 + (ci + 0.5f) * ChunkSize, Z0 + (cj + 0.5f) * ChunkSize);
    static string dir;
    /// <summary>Generated world: StreamingAssets/berat inside a build, or ../world next to the Unity project when running from the editor. BERAT_WORLD overrides both.</summary>
    public static string Dir
    {
        get
        {
            if (dir != null) return dir;
            var env = System.Environment.GetEnvironmentVariable("BERAT_WORLD");
            var built = Path.Combine(Application.streamingAssetsPath, "berat");
            var project = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "world"));
            foreach (var p in new[] { env, built, project }) if (!string.IsNullOrEmpty(p) && File.Exists(Path.Combine(p, "far.bin"))) return dir = p;
            return dir = built;
        }
    }

    public int FarNx, FarNz; public float FarCell;
    public float[] FarH; public byte[] FarC;
    public SpawnData Spawn;
    public readonly Dictionary<int, ChunkData> Chunks = new Dictionary<int, ChunkData>();

    public static WorldData Load()
    {
        var w = new WorldData();
        string wj = Path.Combine(Dir, "world.json");
        if (File.Exists(wj))
        {
            var info = JsonUtility.FromJson<WorldInfo>(File.ReadAllText(wj));
            if (info.ncx > 0) { X0 = info.x0; Z0 = info.z0; NCX = info.ncx; NCZ = info.ncz; Legacy = false; }
        }
        using (var br = new BinaryReader(File.OpenRead(Path.Combine(Dir, "far.bin"))))
        {
            if (Legacy) { w.FarNx = w.FarNz = br.ReadInt32(); w.FarCell = br.ReadSingle(); br.ReadSingle(); }
            else { w.FarNx = br.ReadInt32(); w.FarNz = br.ReadInt32(); w.FarCell = br.ReadSingle(); br.ReadSingle(); br.ReadSingle(); br.ReadSingle(); }
            var bytes = br.ReadBytes(w.FarNx * w.FarNz * 4); w.FarH = new float[w.FarNx * w.FarNz]; Buffer.BlockCopy(bytes, 0, w.FarH, 0, bytes.Length);
            w.FarC = br.ReadBytes(w.FarNx * w.FarNz * 3);
        }
        w.Spawn = JsonUtility.FromJson<SpawnData>(File.ReadAllText(Path.Combine(Dir, "spawn.json")));
        return w;
    }

    public static int ChunkIndexX(float x) => Mathf.FloorToInt((x - X0) / ChunkSize);
    public static int ChunkIndexZ(float z) => Mathf.FloorToInt((z - Z0) / ChunkSize);
    public static int ChunkKey(float x, float z) { int ci = ChunkIndexX(x), cj = ChunkIndexZ(z); return ci < 0 || cj < 0 || ci >= NCX || cj >= NCZ ? -1 : cj * NCX + ci; }

    /// <summary>Terrain height: the loaded chunk's 4 m grid (already carved under roads and water), else the coarse far terrain.</summary>
    public float TerrainHeight(float x, float z)
    {
        int k = ChunkKey(x, z);
        if (k >= 0 && Chunks.TryGetValue(k, out var c)) return c.Height(x, z);
        return FarHeight(x, z);
    }
    public bool HasChunk(float x, float z) { int k = ChunkKey(x, z); return k >= 0 && Chunks.ContainsKey(k); }

    public float FarHeight(float x, float z)
    {
        float fx = Mathf.Clamp((x - X0) / FarCell, 0, FarNx - 1.001f), fz = Mathf.Clamp((z - Z0) / FarCell, 0, FarNz - 1.001f);
        int ix = (int)fx, iz = (int)fz; float tx = fx - ix, tz = fz - iz;
        return Mathf.Lerp(Mathf.Lerp(FarH[iz * FarNx + ix], FarH[iz * FarNx + ix + 1], tx), Mathf.Lerp(FarH[(iz + 1) * FarNx + ix], FarH[(iz + 1) * FarNx + ix + 1], tx), tz);
    }

    // ---- snapshots of what is loaded (rebuilt lazily after chunks come and go)
    RoadData[] roads; BuildingData[] buildings; int version, roadsVersion = -1, buildingsVersion = -1;
    public void Changed() { version++; }
    public RoadData[] Roads
    {
        get
        {
            if (roadsVersion != version) { var l = new List<RoadData>(); foreach (var c in Chunks.Values) l.AddRange(c.Roads); roads = l.ToArray(); roadsVersion = version; }
            return roads;
        }
    }
    public BuildingData[] Buildings
    {
        get
        {
            if (buildingsVersion != version) { var l = new List<BuildingData>(); foreach (var c in Chunks.Values) l.AddRange(c.Buildings); buildings = l.ToArray(); buildingsVersion = version; }
            return buildings;
        }
    }
    public int LoadedRoadVersion => version;

    /// <summary>Level of the water surface at (x,z) if it lies on standing or running water, else NaN.</summary>
    public float WaterLevel(float x, float z)
    {
        int k = ChunkKey(x, z);
        if (k < 0 || !Chunks.TryGetValue(k, out var c)) return float.NaN;
        foreach (var a in c.Areas) if (InRing(a.ring, x, z)) return a.level;
        foreach (var l in c.Lines)
        {
            var p = l.pts; float best = l.hw * l.hw;
            for (int i = 0; i + 5 < p.Length; i += 3)
            {
                Vector2 a = new Vector2(p[i], p[i + 2]), b = new Vector2(p[i + 3], p[i + 5]), ab = b - a, q = new Vector2(x, z);
                float t = Mathf.Clamp01(Vector2.Dot(q - a, ab) / Mathf.Max(ab.sqrMagnitude, 1e-4f));
                if ((q - (a + ab * t)).sqrMagnitude < best) return Mathf.Lerp(p[i + 1], p[i + 4], t);
            }
        }
        return float.NaN;
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
}
