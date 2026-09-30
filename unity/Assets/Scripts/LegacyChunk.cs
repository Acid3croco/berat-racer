using System.IO;
using UnityEngine;

/// <summary>
/// Reads the road part of chunk files written before the road pipeline (BM02 - BM04: a centreline and one half width per road, no junctions,
/// terrain not yet cut under the roads) and turns it into today's data: road edges, drawn flags, a terrain kept below the roads.
/// Such worlds keep their old look (ribbons overlap at junctions). Rebuild them with tools/roads + tools/build_world.py to get real junctions.
/// </summary>
public static class LegacyChunk
{
    const float WidthScale = 1.15f;                // those worlds were drawn at 115 % of the surveyed width
    const int CV = ChunkData.CV, LV = ChunkData.LV;

    public static void ReadRoads(BinaryReader br, string magic, ChunkData d)
    {
        bool v3 = magic != "BM02", v4 = magic == "BM04";
        d.Roads = Read(br, v3, v4); d.Ctx = Read(br, v3, v4);
        d.Junctions = new JunctionData[0]; d.CtxJunctions = new JunctionData[0];
        CutTerrain(d);
    }

    static RoadData[] Read(BinaryReader br, bool v3, bool v4)
    {
        int count = br.ReadInt32(); var a = new RoadData[count];
        for (int i = 0; i < count; i++)
        {
            var r = new RoadData { hw = br.ReadSingle() * WidthScale }; r.realWidth = 2f * r.hw / WidthScale;
            byte fl = br.ReadByte(); r.dirt = (fl & 1) != 0; r.bridge = (fl & 2) != 0; r.imp = br.ReadByte().ToString();
            int lead = br.ReadByte(), trail = br.ReadByte();
            if (v3) { r.limit = br.ReadByte(); r.avg = br.ReadByte(); r.oneway = br.ReadByte(); if (v4) { r.lanes = br.ReadByte(); r.kind = br.ReadByte(); } }
            r.fid = br.ReadInt32(); br.ReadSingle(); br.ReadSingle(); br.ReadSingle(); br.ReadSingle(); br.ReadSingle();      // priority and joint tangents: no longer used
            r.name = ChunkData.ReadString(br);
            var all = ChunkData.ReadFloats(br, br.ReadInt32() * 3);
            Edges(r, all, lead, trail, i);
            a[i] = r;
        }
        return a;
    }

    /// <summary>Keeps the points the chunk owns (the file carries one extra point at each end for the tangent) and offsets them sideways by the half width.</summary>
    static void Edges(RoadData r, float[] all, int lead, int trail, int index)
    {
        int total = all.Length / 3, n = Mathf.Max(total - lead - trail, 2); lead = Mathf.Min(lead, total - n);
        r.pts = new float[n * 3]; r.left = new float[n * 3]; r.right = new float[n * 3]; r.drawn = new bool[n - 1];
        float bias = (index % 6) * 0.0009f;                                   // overlapping ribbons of different roads: a hair apart, so they do not flicker
        for (int i = 0; i < n; i++)
        {
            int k = lead + i, k0 = Mathf.Max(k - 1, 0), k1 = Mathf.Min(k + 1, total - 1);
            Vector2 dir = new Vector2(all[k1 * 3] - all[k0 * 3], all[k1 * 3 + 2] - all[k0 * 3 + 2]); dir = dir.sqrMagnitude > 1e-8f ? dir.normalized : Vector2.up;
            Vector2 leftward = new Vector2(-dir.y, dir.x) * r.hw;
            float x = all[k * 3], y = all[k * 3 + 1], z = all[k * 3 + 2];
            r.pts[i * 3] = x; r.pts[i * 3 + 1] = y; r.pts[i * 3 + 2] = z;
            r.left[i * 3] = x + leftward.x; r.left[i * 3 + 1] = y + bias; r.left[i * 3 + 2] = z + leftward.y;
            r.right[i * 3] = x - leftward.x; r.right[i * 3 + 1] = y + bias; r.right[i * 3 + 2] = z - leftward.y;
            if (i < n - 1) r.drawn[i] = true;
        }
    }

    /// <summary>
    /// The rule those worlds applied at run time: within 5.7 m of a road edge the ground is cut down to just below the road,
    /// beyond that it may climb back at 50 %. Applied once here, to the 4 m grid and to the 16 m grid derived from it.
    /// </summary>
    static void CutTerrain(ChunkData d)
    {
        var edge = new float[CV * CV]; var roadY = new float[CV * CV];
        for (int i = 0; i < edge.Length; i++) edge[i] = 1e9f;
        foreach (var set in new[] { d.Roads, d.Ctx })
            foreach (var r in set)
            {
                if (r.bridge) continue;
                for (int k = 0; k + 1 < r.Count; k++)
                {
                    Vector2 a = new Vector2(r.pts[k * 3], r.pts[k * 3 + 2]), b = new Vector2(r.pts[k * 3 + 3], r.pts[k * 3 + 5]), ab = b - a;
                    float ya = r.pts[k * 3 + 1], yb = r.pts[k * 3 + 4], reach = r.hw + 12f;
                    int x0 = Mathf.Max(Mathf.CeilToInt((Mathf.Min(a.x, b.x) - reach - d.x0) / WorldData.Cell), 0), x1 = Mathf.Min(Mathf.FloorToInt((Mathf.Max(a.x, b.x) + reach - d.x0) / WorldData.Cell), CV - 1);
                    int z0 = Mathf.Max(Mathf.CeilToInt((Mathf.Min(a.y, b.y) - reach - d.z0) / WorldData.Cell), 0), z1 = Mathf.Min(Mathf.FloorToInt((Mathf.Max(a.y, b.y) + reach - d.z0) / WorldData.Cell), CV - 1);
                    for (int z = z0; z <= z1; z++)
                        for (int x = x0; x <= x1; x++)
                        {
                            Vector2 p = new Vector2(d.x0 + x * WorldData.Cell, d.z0 + z * WorldData.Cell);
                            float t = Mathf.Clamp01(Vector2.Dot(p - a, ab) / Mathf.Max(ab.sqrMagnitude, 1e-4f)), e = (p - (a + ab * t)).magnitude - r.hw;
                            if (e < edge[z * CV + x]) { edge[z * CV + x] = e; roadY[z * CV + x] = Mathf.Lerp(ya, yb, t); }
                        }
                }
            }
        for (int i = 0; i < edge.Length; i++)
            if (edge[i] < 12f) d.H[i] = Mathf.Min(d.H[i], roadY[i] - 0.03f + Mathf.Max(0f, edge[i] - 5.7f) * 0.5f);
        d.LowH = new float[LV * LV];
        for (int z = 0; z < LV; z++)
            for (int x = 0; x < LV; x++)
            {
                float sum = 0; int cnt = 0;
                for (int dz = -2; dz <= 2; dz += 2)
                    for (int dx = -2; dx <= 2; dx += 2) { sum += d.H[Mathf.Clamp(z * 4 + dz, 0, CV - 1) * CV + Mathf.Clamp(x * 4 + dx, 0, CV - 1)]; cnt++; }
                int c = Mathf.Min(z * 4, CV - 1) * CV + Mathf.Min(x * 4, CV - 1);
                d.LowH[z * LV + x] = edge[c] < 12f ? Mathf.Min(sum / cnt, d.H[c]) : sum / cnt;
            }
    }
}
