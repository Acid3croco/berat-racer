using System;
using System.IO;
using UnityEngine;

[Serializable] public class RoadData { public float hw; public bool dirt; public bool bridge; public string name; public string imp; public float[] pts; }
[Serializable] public class RoadList { public RoadData[] items; }
[Serializable] public class BuildingData { public float[] p; public float b, h, r; public float[] rc; public int[] c; public int[] w; public string k, n; public int fe; public float[] tw; }
[Serializable] public class BuildingList { public BuildingData[] items; }
[Serializable] public class SpawnData { public float x, y, z, heading; public string road; }

/// <summary>Everything exported by tools/export_world.py, loaded from StreamingAssets/berat.</summary>
public class WorldData
{
    public const float Half = 3200f;
    public int N; public float Cell, Ox, Oz;
    public float[] Heights;          // [iz * N + ix], iz = 0 at the south edge
    public byte[] Colors;            // rgb per vertex, same order
    public RoadData[] Roads;
    public BuildingData[] Buildings;
    public float[] Trees;            // x,y,z,height per tree
    public SpawnData Spawn;

    public static WorldData Load()
    {
        string dir = Path.Combine(Application.streamingAssetsPath, "berat");
        var w = new WorldData();
        using (var br = new BinaryReader(File.OpenRead(Path.Combine(dir, "terrain.bin"))))
        {
            w.N = br.ReadInt32(); w.Cell = br.ReadSingle(); w.Ox = br.ReadSingle(); w.Oz = br.ReadSingle(); br.ReadSingle();
            var bytes = br.ReadBytes(w.N * w.N * 4);
            w.Heights = new float[w.N * w.N];
            Buffer.BlockCopy(bytes, 0, w.Heights, 0, bytes.Length);
        }
        w.Colors = File.ReadAllBytes(Path.Combine(dir, "terrain_colors.bin"));
        w.Roads = JsonUtility.FromJson<RoadList>(File.ReadAllText(Path.Combine(dir, "roads.json"))).items;
        w.Buildings = JsonUtility.FromJson<BuildingList>(File.ReadAllText(Path.Combine(dir, "buildings.json"))).items;
        var tb = File.ReadAllBytes(Path.Combine(dir, "trees.bin"));
        w.Trees = new float[tb.Length / 4]; Buffer.BlockCopy(tb, 0, w.Trees, 0, tb.Length);
        w.Spawn = JsonUtility.FromJson<SpawnData>(File.ReadAllText(Path.Combine(dir, "spawn.json")));
        return w;
    }

    /// <summary>Bilinear terrain height (already carved flat under roads).</summary>
    public float TerrainHeight(float x, float z)
    {
        float fx = Mathf.Clamp((x - Ox) / Cell, 0, N - 1.001f), fz = Mathf.Clamp((z - Oz) / Cell, 0, N - 1.001f);
        int ix = (int)fx, iz = (int)fz; float tx = fx - ix, tz = fz - iz;
        float h00 = Heights[iz * N + ix], h10 = Heights[iz * N + ix + 1];
        float h01 = Heights[(iz + 1) * N + ix], h11 = Heights[(iz + 1) * N + ix + 1];
        return Mathf.Lerp(Mathf.Lerp(h00, h10, tx), Mathf.Lerp(h01, h11, tx), tz);
    }
}
